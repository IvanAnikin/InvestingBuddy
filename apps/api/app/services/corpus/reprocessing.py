"""Re-extraction under a new parser or a larger budget — V3.1 Slice 1.7.

This is what the raw bytes were retained FOR.

    raw artifact
        → parser version N, live budget   → derivation (active)
        → parser version N+1, or a deep budget
        → deterministic reprocessing, from the STORED bytes
        → prior derivation retained and auditable
        → new active representation, reindexed

WHY THIS IS NOT A RE-FETCH
==========================
The repository's history is the argument. ``CURRENT_EXTRACTION_PIPELINE_VERSION``
is at 15, and versions 9-15 each changed how already-fetched text is read: a
two-column layout fix, a geometric table rebuilder, a gutter-threshold
recalibration, a larger extraction budget. Applying each of them meant re-fetching
documents from issuer sites — sites that reorganise, that put annual reports on a
CDN behind extension-less URLs, and that eventually stop serving the 2019 edition
at all.

With the bytes retained, a parser improvement is a local, deterministic operation
over documents the platform already holds.

FOUR RULES, AND EACH ONE IS A REFUSAL
=====================================
**Never fetch.** If the bytes are not retained, the outcome is
``skipped_no_bytes``. Reprocessing must not quietly become a crawler — a
maintenance job that reaches the network is a maintenance job that can be
rate-limited, blocked, or noticed by an issuer.

**Never trust the store.** The re-read bytes are re-hashed and must match the
version's ``content_hash``. A store that returned the wrong object would otherwise
attach one document's citations to another document's text — silently, and to
every fact derived from it.

**Never destroy a prior derivation.** The old pages, sections, tables and chunks
stay exactly where they are. Only ``is_active`` moves. "What did this document say
under version 13" remains answerable, which is the difference between a corrective
and a rewrite.

**Never roll backwards.** A derivation is promoted only if it is a better reading:
a newer parser, or the same parser having read more of the document. A deep run
that somehow opened fewer pages cannot take over from the live run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.research_derivation import (
    PROFILE_DEEP,
    PROFILE_LIVE,
    ResearchDocumentDerivation,
)
from app.models.research_document import ResearchDocumentVersion
from app.services.corpus.artifacts.service import load_artifact_bytes
from app.services.corpus.indexing import index_version, persist_chunks
from app.services.corpus.parsed import build_parsed_document, persist_parsed_document
from app.services.sources.extraction_pipeline_version import (
    CURRENT_EXTRACTION_PIPELINE_VERSION,
)
from app.services.sources.primary_document_extractor import (
    content_hash_of,
    extract_primary_document,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.core.config import Settings
    from app.services.corpus.artifacts.store import ArtifactStore
    from app.services.corpus.search.types import SearchBackend

# Closed outcome vocabulary. Every one is safe to persist and to show an operator,
# and none of them can carry provider text or a path — the same discipline
# ``ingestion_status`` applies to a fetch.
OUTCOME_REPROCESSED = "reprocessed"
OUTCOME_UP_TO_DATE = "skipped_up_to_date"
OUTCOME_NO_BYTES = "skipped_no_bytes"
OUTCOME_HASH_MISMATCH = "content_hash_mismatch"
OUTCOME_EXTRACTION_FAILED = "extraction_failed"
OUTCOME_NOT_FOUND = "version_not_found"
OUTCOME_DISABLED = "corpus_disabled"

REPROCESS_OUTCOMES: frozenset[str] = frozenset(
    {
        OUTCOME_REPROCESSED,
        OUTCOME_UP_TO_DATE,
        OUTCOME_NO_BYTES,
        OUTCOME_HASH_MISMATCH,
        OUTCOME_EXTRACTION_FAILED,
        OUTCOME_NOT_FOUND,
        OUTCOME_DISABLED,
    }
)


@dataclass(frozen=True)
class ReprocessResult:
    """What one reprocessing attempt did, and why. Secret-free."""

    outcome: str
    research_document_version_id: uuid.UUID | None = None
    derivation_id: uuid.UUID | None = None
    superseded_derivation_id: uuid.UUID | None = None
    pipeline_version: int | None = None
    extraction_profile: str | None = None
    pages_persisted: int = 0
    chunks_written: int = 0
    chunks_indexed: int = 0

    @property
    def changed_anything(self) -> bool:
        return self.outcome == OUTCOME_REPROCESSED


@dataclass
class ReprocessBatchResult:
    """Counts for a batch run. One entry per outcome, so nothing is averaged away."""

    attempted: int = 0
    by_outcome: dict[str, int] = field(default_factory=dict)
    results: list[ReprocessResult] = field(default_factory=list)

    def record(self, result: ReprocessResult) -> None:
        self.attempted += 1
        self.by_outcome[result.outcome] = self.by_outcome.get(result.outcome, 0) + 1
        self.results.append(result)


def deep_profile_configured(cfg: "Settings") -> bool:
    """Whether a larger reprocessing budget has been configured at all."""
    return int(getattr(cfg, "v3_corpus_reprocess_max_pdf_pages", 0) or 0) > 0


def settings_for_profile(cfg: "Settings", profile: str) -> "Settings":
    """The extraction settings one profile runs under.

    ``deep`` overrides only the two caps that decide how much of a document is
    opened. Everything else — the byte cap, the content-type gate, the
    decompression-bomb guard, the per-excerpt bounds — is untouched, because none
    of those is what stopped the extractor at page 40 and every one of them exists
    for a reason that reprocessing does not change.
    """
    if profile != PROFILE_DEEP or not deep_profile_configured(cfg):
        return cfg
    return cfg.model_copy(
        update={
            "primary_document_max_pdf_pages": int(cfg.v3_corpus_reprocess_max_pdf_pages),
            "primary_document_extraction_timeout_seconds": int(
                getattr(cfg, "v3_corpus_reprocess_timeout_seconds", 600) or 600
            ),
        }
    )


async def active_derivation_for(
    session: "Any", *, research_document_version_id: uuid.UUID
) -> ResearchDocumentDerivation | None:
    return (
        await session.execute(
            select(ResearchDocumentDerivation)
            .where(
                ResearchDocumentDerivation.research_document_version_id
                == research_document_version_id,
                ResearchDocumentDerivation.is_active.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


def target_profile(cfg: "Settings") -> str:
    """The profile a reprocessing run should aim for, given the configuration."""
    return PROFILE_DEEP if deep_profile_configured(cfg) else PROFILE_LIVE


def needs_reprocessing(
    derivation: ResearchDocumentDerivation | None, *, cfg: "Settings"
) -> bool:
    """Whether the active derivation is behind the best reading now available.

    Two reasons, and they are independent:

    * the parser has moved on — the active derivation was produced under an older
      ``pipeline_version``, so it contains readings this codebase has since decided
      were wrong;
    * a deep budget is configured and this document has never been read under one,
      so the corpus holds a fraction of it.

    A derivation that is current on both counts needs nothing, and saying so is
    what keeps a batch run from re-parsing the whole corpus every time it is
    invoked.
    """
    if derivation is None:
        return True
    if int(derivation.pipeline_version) < CURRENT_EXTRACTION_PIPELINE_VERSION:
        return True
    return (
        deep_profile_configured(cfg)
        and derivation.extraction_profile != PROFILE_DEEP
    )


async def versions_needing_reprocessing(
    session: "Any",
    *,
    cfg: "Settings",
    company_id: uuid.UUID | None = None,
    limit: int = 50,
) -> "list[ResearchDocumentVersion]":
    """Versions whose active derivation is behind, newest retrieval first.

    Only versions that HAVE retained bytes are returned: reprocessing never
    fetches, so a version whose artifact was never stored or has since expired is
    not a candidate — listing it would only produce a ``skipped_no_bytes`` on every
    run.
    """
    if not getattr(cfg, "v3_corpus_enabled", False):
        return []

    from app.models.research_document import ResearchDocument

    stmt = (
        select(ResearchDocumentVersion)
        .where(ResearchDocumentVersion.research_artifact_id.is_not(None))
        .order_by(ResearchDocumentVersion.retrieved_at.desc())
    )
    if company_id is not None:
        stmt = stmt.join(
            ResearchDocument,
            ResearchDocument.id == ResearchDocumentVersion.research_document_id,
        ).where(ResearchDocument.company_id == company_id)
    # Scanned rather than filtered in SQL: "is the active derivation behind?" is a
    # comparison against the CURRENT pipeline version and the configured profile,
    # both of which are application constants rather than columns. Bounded by
    # ``limit`` below, and this is an operator-invoked maintenance query.
    rows = (await session.execute(stmt.limit(max(1, int(limit)) * 20))).scalars().all()

    out: list[ResearchDocumentVersion] = []
    for version in rows:
        active = await active_derivation_for(
            session, research_document_version_id=version.id
        )
        if needs_reprocessing(active, cfg=cfg):
            out.append(version)
        if len(out) >= max(1, int(limit)):
            break
    return out


async def reprocess_version(
    session: "Any",
    *,
    research_document_version_id: uuid.UUID,
    cfg: "Settings",
    store: "ArtifactStore | None" = None,
    backend: "SearchBackend | None" = None,
    profile: str | None = None,
    force: bool = False,
) -> ReprocessResult:
    """Re-extract ONE document version from its retained bytes. Never raises.

    ``force`` re-parses even when the active derivation is already current, which
    is what proves determinism: the same bytes and the same profile must produce
    the same derivation and the same chunk ids.

    ``backend`` is optional. Reprocessing a document that is not indexed anywhere
    is a legitimate operation, and the index is reconciled only when one is given.
    """
    if not getattr(cfg, "v3_corpus_enabled", False):
        return ReprocessResult(outcome=OUTCOME_DISABLED)

    version = await session.get(ResearchDocumentVersion, research_document_version_id)
    if version is None:
        return ReprocessResult(outcome=OUTCOME_NOT_FOUND)

    resolved_profile = profile or target_profile(cfg)
    active = await active_derivation_for(
        session, research_document_version_id=version.id
    )
    if not force and not needs_reprocessing(active, cfg=cfg):
        return ReprocessResult(
            outcome=OUTCOME_UP_TO_DATE,
            research_document_version_id=version.id,
            derivation_id=active.id if active else None,
            pipeline_version=active.pipeline_version if active else None,
            extraction_profile=active.extraction_profile if active else None,
        )

    # NEVER a fetch. The bytes are either held or they are not.
    raw = await load_artifact_bytes(
        session, content_hash=version.content_hash, cfg=cfg, store=store
    )
    if raw is None:
        return ReprocessResult(
            outcome=OUTCOME_NO_BYTES, research_document_version_id=version.id
        )
    if content_hash_of(raw) != version.content_hash:
        # Unreachable through ``load_artifact_bytes``, which re-hashes as well.
        # Checked again because the cost of being wrong is one document's citations
        # pointing at another document's text.
        return ReprocessResult(
            outcome=OUTCOME_HASH_MISMATCH, research_document_version_id=version.id
        )

    document_type = "pdf" if (version.media_type or "").endswith("pdf") else "html"
    try:
        extraction = extract_primary_document(
            raw,
            document_type=document_type,
            cfg=settings_for_profile(cfg, resolved_profile),
            original_language=version.language,
            capture_blocks=True,
        )
    except Exception:  # noqa: BLE001 - a maintenance job must not crash a process
        return ReprocessResult(
            outcome=OUTCOME_EXTRACTION_FAILED, research_document_version_id=version.id
        )

    parsed = build_parsed_document(extraction, extraction_profile=resolved_profile)
    if parsed is None or not parsed.has_content:
        # An empty re-parse never supersedes a derivation that has content. A
        # regression in a new parser must not silently empty the corpus.
        return ReprocessResult(
            outcome=OUTCOME_EXTRACTION_FAILED, research_document_version_id=version.id
        )

    previous_active_id = active.id if active is not None else None
    derivation = await persist_parsed_document(
        session, version_id=version.id, parsed=parsed, cfg=cfg
    )
    if derivation is None:
        return ReprocessResult(
            outcome=OUTCOME_EXTRACTION_FAILED, research_document_version_id=version.id
        )

    chunks = await persist_chunks(
        session, version=version, derivation=derivation, parsed=parsed, cfg=cfg
    )

    indexed = 0
    if backend is not None:
        # ``replace=True`` clears the version's previous entries: the superseded
        # derivation's chunks have different ids and would otherwise linger in the
        # index as a second, stale copy of the same document. They stay in the
        # DATABASE — only the index is reconciled.
        index_result = await index_version(
            session,
            research_document_version_id=version.id,
            backend=backend,
            cfg=cfg,
            replace=True,
        )
        indexed = index_result.indexed

    await session.refresh(derivation)
    return ReprocessResult(
        outcome=OUTCOME_REPROCESSED,
        research_document_version_id=version.id,
        derivation_id=derivation.id,
        superseded_derivation_id=(
            previous_active_id if previous_active_id != derivation.id else None
        ),
        pipeline_version=derivation.pipeline_version,
        extraction_profile=derivation.extraction_profile,
        pages_persisted=derivation.pages_persisted,
        chunks_written=len(chunks),
        chunks_indexed=indexed,
    )


async def reprocess_batch(
    session: "Any",
    *,
    cfg: "Settings",
    store: "ArtifactStore | None" = None,
    backend: "SearchBackend | None" = None,
    company_id: uuid.UUID | None = None,
    limit: int = 10,
) -> ReprocessBatchResult:
    """Reprocess up to ``limit`` stale versions. Operator-invoked, never scheduled.

    Nothing in the application calls this. Re-extracting a corpus is minutes of
    CPU per document and it belongs to whoever decided a parser improved — not to
    the first request after a deploy.
    """
    batch = ReprocessBatchResult()
    versions: "Sequence[ResearchDocumentVersion]" = await versions_needing_reprocessing(
        session, cfg=cfg, company_id=company_id, limit=limit
    )
    for version in versions:
        batch.record(
            await reprocess_version(
                session,
                research_document_version_id=version.id,
                cfg=cfg,
                store=store,
                backend=backend,
            )
        )
    return batch


__all__ = [
    "OUTCOME_DISABLED",
    "OUTCOME_EXTRACTION_FAILED",
    "OUTCOME_HASH_MISMATCH",
    "OUTCOME_NOT_FOUND",
    "OUTCOME_NO_BYTES",
    "OUTCOME_REPROCESSED",
    "OUTCOME_UP_TO_DATE",
    "REPROCESS_OUTCOMES",
    "ReprocessBatchResult",
    "ReprocessResult",
    "active_derivation_for",
    "deep_profile_configured",
    "needs_reprocessing",
    "reprocess_batch",
    "reprocess_version",
    "settings_for_profile",
    "target_profile",
    "versions_needing_reprocessing",
]
