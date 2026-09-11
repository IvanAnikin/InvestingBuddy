"""Acquiring one SEC filing body, entirely through primitives that already exist.

WHY THIS FILE IS SO SHORT
=========================
Everything hard was already built. ``sec_filing_documents`` resolves an accession to the
canonical filing-body URL on ``www.sec.gov/Archives`` behind an allowlist, an SSRF-safe
fetcher, a path-traversal guard, a byte cap, a timeout and the SEC fair-access rate
limiter. ``live_sec_primary_document_extractor`` fetches and extracts through exactly
that path. ``persist_primary_document_artifacts`` writes the V2 row and — because the
freshly extracted artifact carries **blocks** — ingests the corpus version, derivation,
pages and chunks beside it.

The missing piece was never a downloader. It was a caller: nothing ever asked for *one
specific filing the research needed*. This module is that caller and nothing more.

A second SEC downloader would have been the wrong answer twice over — it would duplicate
four safety guards that already work, and it would be the second place a URL could be
constructed, which is the place a mistake eventually gets made.

BOUNDED BY CONSTRUCTION
=======================
One accession in, at most one document fetched: ``max_documents=1``. The wall-budget and
the enabling flags are the existing ones, so this adds no new budget framework and
inherits every limit already agreed. With ``primary_document_sec_body_enabled`` off it
returns without touching the network, exactly as the V2 path does.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from app.services.corpus.filing_evidence import AcquireOutcome

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

logger = logging.getLogger(__name__)

#: Wall-budget for acquiring ONE filing body. Deliberately small: this runs inside a
#: research run that already has its own limits, and a filing that cannot be fetched
#: promptly is better recorded as a gap than allowed to consume the run.
DEFAULT_ACQUIRE_BUDGET_SECONDS = 45.0

_STATUS_EXTRACTED = "extracted"


async def acquire_sec_filing(
    session: Any,
    *,
    company_id: uuid.UUID,
    cik: str,
    accession: str,
    form: str | None,
    cfg: "Settings",
) -> AcquireOutcome:
    """Fetch, extract, persist and index one SEC filing. Never raises.

    The artifact produced here is a *fresh extraction*, which is the whole point: it
    carries blocks, so ``persist_primary_document_artifacts`` creates the derivation and
    chunks that a rebuilt-from-excerpts cache hit never could.
    """
    from app.services.extracted_document_service import (
        persist_primary_document_artifacts,
    )
    from app.services.sources.live_fetchers import live_sec_primary_document_extractor

    if not getattr(cfg, "primary_document_sec_body_enabled", False):
        return AcquireOutcome(
            acquired=False,
            reason="sec_body_fetch_disabled",
            detail=(
                "PRIMARY_DOCUMENT_SEC_BODY_ENABLED is off, so no filing body was "
                "requested. This is a statement about configuration, not about the "
                "filing."
            ),
        )

    # The only input the SEC resolver needs. `accession_number` and `form_type` come
    # from the regulator's own metadata; no URL is constructed here and none is accepted.
    filings = [{"accession_number": accession, "form_type": form or "10-K"}]

    try:
        artifacts = await live_sec_primary_document_extractor(
            cik,
            filings,
            cfg=cfg,
            max_documents=1,
            budget_seconds=DEFAULT_ACQUIRE_BUDGET_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - acquisition never ends a run
        logger.exception("SEC filing acquisition failed for %s", accession)
        return AcquireOutcome(
            acquired=False, reason="fetch_error", detail=type(exc).__name__
        )

    if not artifacts:
        return AcquireOutcome(
            acquired=False,
            reason="no_document_resolved",
            detail=(
                "the filing index named no selectable primary document for this "
                "accession"
            ),
        )

    extracted = [
        a for a in artifacts if getattr(a, "status", None) == _STATUS_EXTRACTED
    ]
    if not extracted:
        # A fetch was attempted and produced nothing usable. Recorded honestly: the
        # failure codes on the artifacts are the closed ingestion vocabulary, never
        # provider text or a URL.
        codes = sorted(
            {
                str(getattr(a, "failure_code", "") or "unknown")
                for a in artifacts
            }
        )
        return AcquireOutcome(
            acquired=False,
            fetched=True,
            reason="extraction_failed",
            detail=f"no extracted document; failure codes: {', '.join(codes)}",
        )

    result = await persist_primary_document_artifacts(
        session,
        artifacts=extracted,
        company_id=company_id,
        agent_run_id=None,
        cfg=cfg,
    )

    # AND THEN INDEX IT — *it*, and nothing else.
    #
    # `persist_chunks` creates chunk ROWS; it does not create index ENTRIES, and the
    # lexical query filters on `indexed_at IS NOT NULL`. Until V3.16 the only caller of
    # `index_version` was the reprocessing path, so every chunk the live ingestion ever
    # created sat un-indexed and unfindable — chunks in the table, nothing in search.
    # That is the second half of why a specialist searching Moderna's filings got
    # nothing, and it is invisible on SQLite because the backend is PostgreSQL-only.
    #
    # Scoped to THIS accession. Indexing every current version the company happens to
    # own would make one requested filing do unrelated work: re-indexing documents
    # nobody asked about, on a path whose cost and failure modes belong to a different
    # request. A filing left un-indexed by some other path is that path's bug to fix.
    indexed = await _index_acquired_version(
        session, company_id=company_id, accession=accession, cfg=cfg
    )

    return AcquireOutcome(
        acquired=True,
        fetched=True,
        detail=(
            f"documents created={result.documents_created} "
            f"reused={result.documents_reused} "
            f"corpus versions={result.corpus_versions_created} "
            f"chunks indexed={indexed}"
        ),
    )


async def _index_acquired_version(
    session: Any, *, company_id: uuid.UUID, accession: str, cfg: "Settings"
) -> int:
    """Index the current version of the filing just acquired. Never raises.

    Resolves the version through ``current_version_for_filing`` — the same lookup the
    readiness predicate uses, so the acquirer and the predicate can never disagree about
    which version a filing means.

    With no backend configured the chunks are still stored; they simply are not
    retrievable, and the readiness predicate says so rather than letting a caller believe
    otherwise.
    """
    from app.services.corpus.filing_evidence import current_version_for_filing
    from app.services.corpus.indexing import index_version

    version = await current_version_for_filing(
        session, company_id=company_id, accession=accession
    )
    if version is None:
        # Persisting produced no current version for this company — the cross-company
        # `content_hash` dedup does this. Saying nothing was indexed is the truth, and
        # the bridge's own re-read will report the filing unavailable.
        return 0

    try:
        from app.services.corpus.search.factory import get_search_backend

        backend = get_search_backend(cfg, session=session)
    except Exception:  # noqa: BLE001 - a missing backend is not an acquisition failure
        logger.warning("No corpus search backend configured; chunks stored un-indexed.")
        return 0
    if backend is None:
        return 0

    try:
        result = await index_version(
            session,
            research_document_version_id=version.id,
            backend=backend,
            cfg=cfg,
        )
    except Exception:  # noqa: BLE001 - indexing failure is reported, never raised
        logger.exception("Indexing failed for version %s", version.id)
        return 0
    return int(result.indexed) + int(result.updated)


__all__ = ["DEFAULT_ACQUIRE_BUDGET_SECONDS", "acquire_sec_filing"]
