"""Corpus document + version persistence — V3.1 Slice 1.2.

Turns one retrieval into two durable records: the *logical* document, and the
*version* of it that was actually fetched. The V2 ingestion path keeps writing
``ExtractedDocument`` exactly as before; this runs beside it and links the two,
so nothing has to be migrated en masse for the corpus to start being useful.

THREE RULES, AND THEY ARE THE WHOLE MODULE
==========================================
**Never merge on a guess.** Two retrievals are the same document only when the
documents themselves say so — same company, same declared kind, same declared
period. Everything else stays separate. See
:mod:`app.services.corpus.identity` for why the asymmetry between splitting and
merging decides this.

**Version, never overwrite.** A restatement is a new version. The old one keeps
its row, its hash and its citations; only ``is_current`` moves. The database
enforces "exactly one current version" with a partial unique index rather than
trusting this module to maintain it.

**Never claim what the document did not state.** A period that was not declared
stays NULL. A publication date that was not printed stays NULL — it is never
filled from the retrieval timestamp, because "when we fetched it" is not "when it
was published", and that substitution is how a stale document starts looking
fresh.

WHY THE FLUSH ORDER IS EXPLICIT
===============================
Promotion demotes the outgoing current version and promotes the incoming one.
SQLAlchemy orders INSERTs before UPDATEs within a flush, so writing both in one
go would momentarily leave two current versions and trip the partial unique
index. The three flushes below are therefore deliberate, not defensive noise:
insert not-current, demote, promote.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.extracted_document import ExtractedDocument
from app.models.research_artifact import ResearchArtifact
from app.models.research_document import ResearchDocument, ResearchDocumentVersion
from app.services.corpus.identity import (
    annual_period_from,
    document_key_for,
    normalize_document_type,
)
from app.services.corpus.parsed import build_parsed_document, persist_parsed_document
from app.services.corpus.policy import ACCESS_PUBLIC_ISSUER, normalize_access_class
from app.services.sources.document_period import DocumentPeriod, document_period_of
from app.services.sources.redaction import canonicalize_source_url

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.core.config import Settings

# Column guards. Values are code-controlled; clipping keeps a pathological title
# or URL from failing an INSERT for a whole research run.
_URL_MAX = 2000
_TITLE_MAX = 500
_TRANSPORT_MAX = 100
_ORIGIN_MAX = 200
_TIER_MAX = 50
_STATUS_MAX = 50
_FAILURE_MAX = 50
_MEDIA_TYPE_MAX = 100
_LANGUAGE_MAX = 10
_PERIOD_KEY_MAX = 20
_PERIOD_TYPE_MAX = 20
_PERIOD_BASIS_MAX = 40

STATUS_EXTRACTED = "extracted"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


@dataclass(frozen=True)
class DocumentVersionInput:
    """Everything one retrieval contributes to the corpus.

    A value object rather than fifteen keyword arguments, so a caller that
    forgets the transport or the tier fails at construction instead of silently
    defaulting to something plausible.
    """

    content_hash: str
    canonical_url: str
    transport: str
    source_tier: str
    company_id: uuid.UUID | None = None
    document_type: str | None = None
    title: str | None = None
    media_type: str | None = None
    byte_size: int | None = None
    language: str | None = None
    content_origin: str | None = None
    access_class: str = ACCESS_PUBLIC_ISSUER
    period: DocumentPeriod | None = None
    published_at: date | None = None
    retrieved_at: datetime | None = None
    extraction_status: str = STATUS_EXTRACTED
    failure_code: str | None = None
    research_artifact_id: uuid.UUID | None = None
    extracted_document_id: uuid.UUID | None = None


@dataclass
class CorpusIngestResult:
    """Secret-free counts, in the shape the existing ``PersistResult`` uses."""

    documents_created: int = 0
    documents_reused: int = 0
    versions_created: int = 0
    versions_reused: int = 0
    promoted_to_current: int = 0
    skipped: int = 0


def period_fields(period: DocumentPeriod | None) -> tuple[str | None, str | None, str | None]:
    """``(period_key, period_type, period_basis)`` — all NULL when unknown.

    An unknown period is a real answer. Coercing it to a bare year is where the
    ``INTERIM_AS_ANNUAL`` contradiction starts, which is the failure
    ``document_period.py`` was written to stop.
    """
    if period is None or not period.is_known:
        return None, None, None
    return (
        _clip(period.period.key, _PERIOD_KEY_MAX),
        _clip(period.period.period_type, _PERIOD_TYPE_MAX),
        _clip(period.basis, _PERIOD_BASIS_MAX),
    )


async def upsert_document_version(
    session: "Any",
    payload: DocumentVersionInput,
    *,
    cfg: "Settings",
    now: datetime | None = None,
    result: CorpusIngestResult | None = None,
) -> ResearchDocumentVersion | None:
    """Record one retrieval as a corpus version. Flush-only; the caller commits.

    Returns ``None`` — no query, no row — when the corpus is disabled or the
    payload has no content hash to identify it by. Idempotent: re-ingesting the
    same bytes for the same document returns the existing version rather than
    creating a second one.
    """
    if not getattr(cfg, "v3_corpus_enabled", False):
        return None
    counts = result if result is not None else CorpusIngestResult()
    content_hash = (payload.content_hash or "").strip().lower()
    if not content_hash:
        # A retrieval with no content identity cannot be stored honestly: there
        # would be no way to tell it from the next one.
        counts.skipped += 1
        return None

    stamp = _as_aware(now or _utcnow())
    retrieved_at = _as_aware(payload.retrieved_at or stamp)
    canonical = _clip(canonicalize_source_url(payload.canonical_url), _URL_MAX) or ""
    doc_type = normalize_document_type(payload.document_type)
    period_key, period_type, period_basis = period_fields(payload.period)
    if period_key is None:
        # ``document_period`` found nothing, which for an annual report is the
        # normal outcome — it refuses a bare year on purpose. Fall back to the
        # narrow rule that reads a year only when it sits beside the document's own
        # name for itself ("Annual Report 2025"). Corpus-only, never a fact period;
        # see ``app.services.corpus.identity`` for the reasoning and the blast
        # radius. An interim period, when one was detected, always wins outright —
        # this branch is unreachable in that case.
        period_key, period_type, period_basis = annual_period_from(
            title=payload.title, url=canonical
        )
    key = document_key_for(
        document_type=doc_type, period_key=period_key, canonical_url=canonical
    )

    document = await _get_or_create_document(
        session,
        company_id=payload.company_id,
        document_key=key,
        document_type=doc_type,
        title=_clip(payload.title, _TITLE_MAX),
        period_key=period_key,
        period_type=period_type,
        language=_clip(payload.language, _LANGUAGE_MAX),
        now=stamp,
        result=counts,
    )

    existing = (
        await session.execute(
            select(ResearchDocumentVersion)
            .where(
                ResearchDocumentVersion.research_document_id == document.id,
                ResearchDocumentVersion.content_hash == content_hash,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        counts.versions_reused += 1
        # Backfill only — an immutable version is never rewritten, but a link that
        # was NULL because the corresponding row did not exist yet is a pure gain.
        if payload.research_artifact_id and not existing.research_artifact_id:
            existing.research_artifact_id = payload.research_artifact_id
        if payload.extracted_document_id and not existing.extracted_document_id:
            existing.extracted_document_id = payload.extracted_document_id
        await session.flush()
        return existing

    version = ResearchDocumentVersion(
        id=uuid.uuid4(),
        research_document_id=document.id,
        content_hash=content_hash,
        research_artifact_id=payload.research_artifact_id,
        extracted_document_id=payload.extracted_document_id,
        canonical_url=canonical,
        media_type=_clip(payload.media_type, _MEDIA_TYPE_MAX),
        byte_size=payload.byte_size,
        title=_clip(payload.title, _TITLE_MAX),
        language=_clip(payload.language, _LANGUAGE_MAX),
        transport=_clip(payload.transport, _TRANSPORT_MAX) or "unknown",
        content_origin=_clip(payload.content_origin, _ORIGIN_MAX),
        source_tier=_clip(payload.source_tier, _TIER_MAX) or "",
        access_class=normalize_access_class(payload.access_class),
        period_key=period_key,
        period_type=period_type,
        period_basis=period_basis,
        published_at=payload.published_at,
        retrieved_at=retrieved_at,
        extraction_status=_clip(payload.extraction_status, _STATUS_MAX)
        or STATUS_EXTRACTED,
        failure_code=_clip(payload.failure_code, _FAILURE_MAX),
        # Inserted NOT current on purpose — see the module docstring on flush order.
        is_current=False,
    )
    session.add(version)
    counts.versions_created += 1
    await session.flush()

    await _promote_if_newer(session, document=document, version=version, result=counts)
    return version


async def _get_or_create_document(
    session: "Any",
    *,
    company_id: uuid.UUID | None,
    document_key: str,
    document_type: str,
    title: str | None,
    period_key: str | None,
    period_type: str | None,
    language: str | None,
    now: datetime,
    result: CorpusIngestResult,
) -> ResearchDocument:
    existing = (
        await session.execute(
            select(ResearchDocument)
            .where(
                ResearchDocument.company_id == company_id,
                ResearchDocument.document_key == document_key,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        result.documents_reused += 1
        existing.last_seen_at = now
        # Fill in what was previously unknown; never overwrite what is known. A
        # later retrieval that failed to detect a period must not erase a period an
        # earlier one read off the document's own title page.
        if title and not existing.title:
            existing.title = title
        if period_key and not existing.period_key:
            existing.period_key = period_key
            existing.period_type = period_type
        if language and not existing.language:
            existing.language = language
        await session.flush()
        return existing

    document = ResearchDocument(
        id=uuid.uuid4(),
        company_id=company_id,
        document_key=document_key,
        document_type=document_type,
        title=title,
        period_key=period_key,
        period_type=period_type,
        language=language,
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(document)
    result.documents_created += 1
    await session.flush()
    return document


async def _promote_if_newer(
    session: "Any",
    *,
    document: ResearchDocument,
    version: ResearchDocumentVersion,
    result: CorpusIngestResult,
) -> None:
    """Make ``version`` current when it is a more recent retrieval than the incumbent.

    Retrieval time, not publication date, decides. Within one document key the
    kind and period are already identical by construction, so the only thing that
    distinguishes two versions is which retrieval is more recent — and a
    restatement is retrieved later than the statement it restates.

    Re-ingesting an OLDER document never demotes a newer one, which is what keeps
    a cache-reuse path from silently rolling the corpus backwards.
    """
    current = (
        await session.execute(
            select(ResearchDocumentVersion)
            .where(
                ResearchDocumentVersion.research_document_id == document.id,
                ResearchDocumentVersion.is_current.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if current is not None:
        if _as_aware(current.retrieved_at) >= _as_aware(version.retrieved_at):
            return
        current.is_current = False
        current.superseded_at = _as_aware(version.retrieved_at)
        # Flush the demotion BEFORE the promotion: the partial unique index means
        # two current versions cannot coexist for even one statement.
        await session.flush()

    version.is_current = True
    version.superseded_at = None
    result.promoted_to_current += 1
    await session.flush()


# --------------------------------------------------------------------------- #
# The V2 adapter
# --------------------------------------------------------------------------- #


async def ingest_extracted_document(
    session: "Any",
    *,
    artifact: "Any",
    document: ExtractedDocument,
    cfg: "Settings",
    now: datetime | None = None,
    result: CorpusIngestResult | None = None,
) -> ResearchDocumentVersion | None:
    """Record the corpus version for an ``ExtractedDocument`` just persisted.

    The compatibility bridge, and the reason no bulk migration is needed: the V2
    writer keeps doing exactly what it did, and the corpus record is created
    beside it with ``extracted_document_id`` pointing back.

    .. warning::

       This docstring previously claimed a corpus read could "fall back to that row's
       bounded ``excerpts_json``". **It cannot, and never could.**
       ``search_company_corpus`` reads ``research_document_chunks`` and nothing else;
       there is no excerpts fallback in retrieval, indexing or the search backend. A
       version created here carries chunks ONLY when ``artifact.extraction`` captured
       blocks or tables — which a freshly fetched document does and a cache rebuilt
       from excerpts does not. Ask
       :func:`app.services.corpus.filing_evidence.is_corpus_search_ready`; it is the
       single definition of whether the backend can return anything for a document.

    Duck-typed on ``artifact`` deliberately: this module stays free of connector
    imports, exactly as ``document_period`` does, so the corpus does not acquire a
    dependency on the shape of the V2 ingestion pipeline.
    """
    if not getattr(cfg, "v3_corpus_enabled", False):
        return None

    stored = getattr(artifact, "raw_artifact", None)
    artifact_row_id = None
    if stored is not None:
        artifact_row_id = await _artifact_id_for(session, stored.content_hash)

    extraction = getattr(artifact, "extraction", None)
    period = document_period_of(
        title=getattr(artifact, "title", None),
        url=getattr(artifact, "source_url", None),
        extraction=extraction,
    )
    payload = DocumentVersionInput(
        content_hash=document.content_hash,
        canonical_url=document.canonical_url,
        transport=document.provider,
        source_tier=document.source_tier,
        company_id=document.company_id,
        document_type=getattr(artifact, "doc_kind", None) or document.source_type,
        title=document.title,
        media_type=document.mime_type,
        byte_size=getattr(stored, "byte_size", None),
        language=getattr(extraction, "language", None),
        access_class=getattr(stored, "access_class", None) or ACCESS_PUBLIC_ISSUER,
        period=period,
        # `doc_date` is the document's own date when the pipeline knows one. It is
        # NULL far more often than not, and it stays NULL — never `retrieved_at`.
        published_at=document.doc_date,
        retrieved_at=document.retrieved_at,
        extraction_status=document.status,
        failure_code=getattr(artifact, "failure_code", None),
        research_artifact_id=artifact_row_id,
        extracted_document_id=document.id,
    )
    version = await upsert_document_version(
        session, payload, cfg=cfg, now=now, result=result
    )
    if version is None:
        return None

    # V3.1 Slice 1.3 — persist what the parser made of it: the full text of every
    # page it opened, the heading structure, and the tables AS GRIDS. Empty unless
    # the extraction captured blocks, which it only does with the corpus on.
    parsed = build_parsed_document(
        extraction,
        max_pages=int(getattr(cfg, "v3_corpus_max_pages_persisted", 0) or 0),
    )
    derivation = await persist_parsed_document(
        session, version_id=version.id, parsed=parsed, cfg=cfg, now=now
    )

    # V3.1 Slice 1.5 — build the retrieval units. Chunks are corpus DATA, stored
    # beside the pages they came from, and they exist whether or not a search
    # backend has ever been configured: that is what makes the backend decision
    # (OPEN DECISION #1) cheap to defer and cheap to change, because switching
    # backends reindexes from rows that are already there rather than re-parsing
    # every document.
    if derivation is not None and parsed is not None:
        from app.services.corpus.indexing import persist_chunks

        await persist_chunks(
            session, version=version, derivation=derivation, parsed=parsed, cfg=cfg
        )
    return version


async def _artifact_id_for(session: "Any", content_hash: str | None) -> uuid.UUID | None:
    if not content_hash:
        return None
    row = (
        await session.execute(
            select(ResearchArtifact.id)
            .where(ResearchArtifact.content_hash == content_hash)
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


# --------------------------------------------------------------------------- #
# Backfill from the V2 table
# --------------------------------------------------------------------------- #


async def backfill_from_extracted_documents(
    session: "Any",
    *,
    cfg: "Settings",
    company_id: uuid.UUID | None = None,
    limit: int = 200,
    now: datetime | None = None,
) -> CorpusIngestResult:
    """Create corpus documents/versions for existing ``extracted_documents`` rows.

    .. warning::

       **This does NOT make anything searchable.** It calls ``upsert_document_version``
       and stops: no derivation, no pages, no chunks. ``search_company_corpus`` reads
       ``research_document_chunks``, so a completed backfill can report "N versions
       created" and leave every search returning nought — the exact false success V3.16
       was opened to investigate.

       It cannot do better, either: a historical row's bytes were never retained, so
       there is nothing to re-parse into chunks. Use this for document/version LINEAGE.
       To make a filing searchable use
       :func:`app.services.corpus.filing_evidence.ensure_filing_corpus_evidence`, which
       reacquires and re-extracts the official filing.

    **Resumable and idempotent**, per the three-step backfill pattern in the
    migration plan: it selects only rows that have no corpus version yet, so
    running it twice does nothing the second time and running it in batches is
    safe. Nothing calls it automatically — a backfill that starts itself on the
    first request after a deploy is how a migration turns into an outage.

    It deliberately does **not** invent what the V2 row never recorded. A period
    is re-derived from the row's own title and URL through the same pure
    ``document_period`` rules the live path uses; when those say nothing, the
    period stays NULL and the document gets a URL-derived key. No raw bytes exist
    for a historical row — nothing was ever stored — so ``research_artifact_id``
    stays NULL and the version records honestly that its bytes are not held.
    """
    result = CorpusIngestResult()
    if not getattr(cfg, "v3_corpus_enabled", False):
        return result

    stmt = (
        select(ExtractedDocument)
        .outerjoin(
            ResearchDocumentVersion,
            ResearchDocumentVersion.extracted_document_id == ExtractedDocument.id,
        )
        .where(ResearchDocumentVersion.id.is_(None))
        .order_by(ExtractedDocument.retrieved_at)
        .limit(max(1, int(limit)))
    )
    if company_id is not None:
        stmt = stmt.where(ExtractedDocument.company_id == company_id)

    rows: "Sequence[ExtractedDocument]" = (await session.execute(stmt)).scalars().all()
    for row in rows:
        period = document_period_of(title=row.title, url=row.canonical_url, extraction=None)
        payload = DocumentVersionInput(
            content_hash=row.content_hash,
            canonical_url=row.canonical_url,
            transport=row.provider,
            source_tier=row.source_tier,
            company_id=row.company_id,
            document_type=row.source_type,
            title=row.title,
            media_type=row.mime_type,
            language=None,
            period=period,
            published_at=row.doc_date,
            retrieved_at=row.retrieved_at,
            extraction_status=row.status,
            # A historical row predates artifact retention entirely: its bytes were
            # never stored, and the corpus says so rather than implying otherwise.
            research_artifact_id=None,
            extracted_document_id=row.id,
        )
        await upsert_document_version(session, payload, cfg=cfg, now=now, result=result)
    return result


__all__ = [
    "STATUS_EXTRACTED",
    "CorpusIngestResult",
    "DocumentVersionInput",
    "backfill_from_extracted_documents",
    "ingest_extracted_document",
    "period_fields",
    "upsert_document_version",
]
