"""Persist deep primary-document artifacts as ExtractedDocument / ExtractedFact
rows — Phase 32A Slice 5 (part 3c-i).

This is the WRITER for the deep-ingestion artifacts collected by the company IR
connector (``PrimaryDocumentArtifact``). It turns a bounded, secret-stripped
artifact into durable, citation-bound lineage rows so a LATER slice can feed the
council real T1 primary evidence — without re-fetching or re-extracting.

Design invariants (product safety):
  * Gated behind BOTH ``primary_document_ingestion_enabled`` and
    ``report_citation_persistence_enabled``. With either flag OFF this function
    issues NO query and writes NO row (byte-identical dark path).
  * Only ``status == 'extracted'`` artifacts become an ``ExtractedDocument``.
    ``metadata_only`` / ``extraction_failed`` artifacts are SKIPPED — a filing is
    never recorded as if it had been read.
  * Documents dedup on ``content_hash`` (SHA of the raw bytes): re-fetching the
    same document REUSES the existing row rather than duplicating it. The stored
    ``canonical_url`` is run through ``canonicalize_source_url`` so signed-token /
    redirected URL variants of the same content never leave a credential residue
    and map to the same logical document.
  * Only ``validation_status == 'validated'`` facts are persisted as structured
    ``ExtractedFact`` rows; ``excerpt_only`` / ``rejected`` are NOT structured
    facts. Facts dedup on ``(document, normalized label, period, value_numeric)``
    so a re-run is idempotent.
  * Lineage is attached via ``company_id`` / ``agent_run_id`` only — never in a
    way that lets a generated final become from-company-selectable (the Slice-3
    "B1" invariant). No cross-company linkage. Errors are sanitized to the
    exception type name by the caller; this module logs nothing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extracted_document import ExtractedDocument, ExtractedFact
from app.services.sources.redaction import canonicalize_source_url

if TYPE_CHECKING:  # avoid a runtime import cycle — attributes are read by name
    from collections.abc import Sequence

    from app.core.config import Settings
    from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact

# Ingestion outcome that means "the document was actually read".
_STATUS_EXTRACTED = "extracted"
# Fact verdict that means "a validated structured figure" (vs excerpt_only /
# rejected, which are NOT structured facts and are never persisted as such).
_VALIDATION_VALIDATED = "validated"
# Every deep artifact here originates from the company IR connector's deep
# extractor; recording that provenance is honest (the transport is issuer-IR).
_ARTIFACT_PROVIDER = "company_ir"
_DEFAULT_SOURCE_TYPE = "company_ir_primary_document"

# Defensive column-length guards (values are Slice-5-controlled, but truncating
# keeps a pathological title/URL from failing the INSERT).
_CANONICAL_URL_MAX = 2000
_TITLE_MAX = 500
_SOURCE_TYPE_MAX = 50
_SOURCE_TIER_MAX = 50
_PROVIDER_MAX = 100
_MIME_TYPE_MAX = 100
_EXTRACTION_METHOD_MAX = 50
_LABEL_MAX = 200
_TABLE_LOCATION_MAX = 200
_STATUS_MAX = 50


@dataclass
class PersistResult:
    """Secret-free counts of what the writer did (for telemetry only)."""

    documents_created: int = 0
    documents_reused: int = 0
    facts_created: int = 0
    facts_deduped: int = 0
    skipped: int = 0


def _norm_label(label: str | None) -> str:
    """Whitespace-collapsed, lower-cased label for fact dedup."""
    return " ".join((label or "").split()).lower()


def _numeric_key(value: float | Decimal | None) -> float | None:
    """A stable numeric dedup key (rounds away float/Decimal round-trip noise)."""
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _to_decimal(value: float | None) -> Decimal | None:
    """Convert a float fact value to a clean, finite Decimal (else None)."""
    if value is None:
        return None
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return dec if dec.is_finite() else None


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit]


def _excerpts_to_json(extraction: Any) -> list[dict[str, Any]]:
    """Serialize an extraction's already-BOUNDED excerpts for durable reuse.

    Copies only each excerpt's citeable text + provenance (page / section /
    heading / table location / method / confidence / evidence type). The excerpts
    are already length- and count-capped by the extractor, so this never stores
    the full document text or a raw table grid.
    """
    out: list[dict[str, Any]] = []
    for ex in getattr(extraction, "excerpts", None) or []:
        text = getattr(ex, "text", "") or ""
        out.append(
            {
                "excerpt_id": getattr(ex, "excerpt_id", None),
                "text": text,
                "page_number": getattr(ex, "page_number", None),
                "section": getattr(ex, "section", None),
                "heading": getattr(ex, "heading", None),
                "table_location": getattr(ex, "table_location", None),
                "extraction_method": getattr(ex, "extraction_method", None),
                "confidence": getattr(ex, "confidence", None),
                "evidence_type": getattr(ex, "evidence_type", None),
                "char_count": getattr(ex, "char_count", None) or len(text),
            }
        )
    return out


def _as_aware(dt: datetime | None) -> datetime:
    """Treat a naive datetime (SQLite round-trip) as UTC for safe comparison."""
    if dt is None:
        return _utcnow()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def persist_primary_document_artifacts(
    session: AsyncSession,
    *,
    artifacts: "Sequence[PrimaryDocumentArtifact] | None",
    company_id: uuid.UUID | None,
    agent_run_id: uuid.UUID | None,
    cfg: "Settings",
) -> PersistResult:
    """Persist ``extracted`` artifacts + their validated facts idempotently.

    Flush-only: the caller owns the single commit (mirrors the Slice-3 citation
    writer). Returns secret-free counts. With either gate flag OFF, or with no
    artifacts, returns an empty result WITHOUT issuing any query.
    """
    result = PersistResult()

    # Gate: BOTH flags must be on. Either OFF ⇒ no query, no row (dark path).
    if not (
        getattr(cfg, "primary_document_ingestion_enabled", False)
        and getattr(cfg, "report_citation_persistence_enabled", False)
    ):
        return result
    if not artifacts:
        return result

    wrote_row = False
    for artifact in artifacts:
        if getattr(artifact, "status", None) != _STATUS_EXTRACTED:
            # metadata_only / extraction_failed — never recorded as extracted.
            result.skipped += 1
            continue

        extraction = getattr(artifact, "extraction", None)
        content_hash = getattr(extraction, "content_hash", None) if extraction else None
        if not content_hash:
            # 'extracted' with no dedup identity — cannot store honestly.
            result.skipped += 1
            continue

        document, reused = await _get_or_create_document(
            session,
            artifact=artifact,
            extraction=extraction,
            content_hash=content_hash,
            company_id=company_id,
            agent_run_id=agent_run_id,
        )
        if reused:
            result.documents_reused += 1
        else:
            result.documents_created += 1
            wrote_row = True

        wrote_fact = await _persist_validated_facts(
            session, artifact=artifact, document=document, reused=reused, result=result
        )
        wrote_row = wrote_row or wrote_fact

    if wrote_row:
        await session.flush()
    return result


async def _get_or_create_document(
    session: AsyncSession,
    *,
    artifact: "PrimaryDocumentArtifact",
    extraction: Any,
    content_hash: str,
    company_id: uuid.UUID | None,
    agent_run_id: uuid.UUID | None,
) -> tuple[ExtractedDocument, bool]:
    """Return ``(document, reused)`` — reuse the content_hash row if it exists."""
    existing = (
        await session.execute(
            select(ExtractedDocument)
            .where(ExtractedDocument.content_hash == content_hash)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, True

    source_url = getattr(artifact, "source_url", "") or ""
    canonical = canonicalize_source_url(source_url) or source_url
    document = ExtractedDocument(
        id=uuid.uuid4(),
        content_hash=content_hash,
        canonical_url=_clip(canonical, _CANONICAL_URL_MAX) or "",
        provider=_clip(_ARTIFACT_PROVIDER, _PROVIDER_MAX) or _ARTIFACT_PROVIDER,
        source_type=_clip(
            getattr(artifact, "document_type", None) or _DEFAULT_SOURCE_TYPE,
            _SOURCE_TYPE_MAX,
        )
        or _DEFAULT_SOURCE_TYPE,
        source_tier=_clip(getattr(artifact, "content_tier", "") or "", _SOURCE_TIER_MAX)
        or "",
        mime_type=_clip(getattr(extraction, "mime_type", "") or "", _MIME_TYPE_MAX)
        or "",
        title=_clip(getattr(artifact, "title", None), _TITLE_MAX),
        # doc_date / period are NOT reliably carried on the artifact; leaving them
        # NULL is honest (a fact carries its own period). Never fabricated.
        doc_date=None,
        period=None,
        retrieved_at=getattr(artifact, "retrieved_at", None) or _utcnow(),
        extraction_method=_clip(
            getattr(extraction, "extraction_method", "") or "", _EXTRACTION_METHOD_MAX
        )
        or "",
        page_count=getattr(extraction, "page_count", None),
        status=_clip(_STATUS_EXTRACTED, _STATUS_MAX) or _STATUS_EXTRACTED,
        # Phase 32A Slice 5 (3c-iii) — persist the BOUNDED excerpts so a later
        # report regeneration can rebuild + reuse this extraction (see
        # ``load_reusable_documents``) without a re-fetch / re-extract. Already
        # capped at extraction time; never the full document text.
        excerpts_json=_excerpts_to_json(extraction) or None,
        company_id=company_id,
        agent_run_id=agent_run_id,
        blob_path=None,
    )
    session.add(document)
    return document, False


async def _persist_validated_facts(
    session: AsyncSession,
    *,
    artifact: "PrimaryDocumentArtifact",
    document: ExtractedDocument,
    reused: bool,
    result: PersistResult,
) -> bool:
    """Persist this artifact's ``validated`` facts, deduped. Returns wrote-any."""
    validated = [
        f
        for f in getattr(artifact, "validated_facts", []) or []
        if getattr(f, "validation_status", None) == _VALIDATION_VALIDATED
    ]
    if not validated:
        return False

    existing_keys: set[tuple[str, str | None, float | None]] = set()
    if reused:
        rows = (
            await session.execute(
                select(
                    ExtractedFact.label,
                    ExtractedFact.period,
                    ExtractedFact.value_numeric,
                ).where(ExtractedFact.extracted_document_id == document.id)
            )
        ).all()
        for label, period, value_numeric in rows:
            existing_keys.add((_norm_label(label), period, _numeric_key(value_numeric)))

    wrote = False
    for fact in validated:
        key = (
            _norm_label(getattr(fact, "label", None)),
            getattr(fact, "period", None),
            _numeric_key(getattr(fact, "value_numeric", None)),
        )
        if key in existing_keys:
            result.facts_deduped += 1
            continue
        existing_keys.add(key)
        session.add(
            ExtractedFact(
                id=uuid.uuid4(),
                extracted_document_id=document.id,
                label=_clip(getattr(fact, "label", "") or "", _LABEL_MAX) or "",
                value_numeric=_to_decimal(getattr(fact, "value_numeric", None)),
                value_text=getattr(fact, "value_text", None),
                unit=getattr(fact, "unit", None),
                currency=getattr(fact, "currency", None),
                scale=getattr(fact, "scale", None),
                period=getattr(fact, "period", None),
                page_number=getattr(fact, "page_number", None),
                table_location=_clip(
                    getattr(fact, "table_location", None), _TABLE_LOCATION_MAX
                ),
                extraction_method=_clip(
                    getattr(fact, "extraction_method", "") or "",
                    _EXTRACTION_METHOD_MAX,
                )
                or "",
                confidence=float(getattr(fact, "confidence", 0.0) or 0.0),
                validation_status=_VALIDATION_VALIDATED,
                needs_human_review=bool(getattr(fact, "needs_human_review", True)),
            )
        )
        result.facts_created += 1
        wrote = True
    return wrote


# --------------------------------------------------------------------------- #
# Reuse across report regeneration — Phase 32A Slice 5 (3c-iii)
# --------------------------------------------------------------------------- #


@dataclass
class ReusedDocument:
    """A persisted ``extracted`` document rebuilt into an artifact-equivalent.

    Carries the fully-rebuilt ``PrimaryDocumentArtifact`` (bounded excerpts from
    ``excerpts_json`` + validated facts from ``extracted_facts``) so the connector
    deep path can SKIP the network fetch/extract for this document and flow it into
    evidence + facts + (idempotent) persistence exactly as a freshly-fetched
    artifact would. ``content_hash`` is preserved so re-persisting a reused
    document dedups onto the SAME row (never a duplicate).
    """

    canonical_url: str
    content_hash: str
    retrieved_at: datetime
    artifact: "PrimaryDocumentArtifact"


async def load_reusable_documents(
    session: AsyncSession,
    *,
    company_id: uuid.UUID | None,
    cfg: "Settings",
    now: datetime | None = None,
) -> dict[str, "ReusedDocument"]:
    """Load THIS company's fresh, reusable extracted documents, keyed by URL.

    Returns a dict keyed by ``canonicalize_source_url(canonical_url)`` mapping to a
    rebuilt :class:`ReusedDocument` for every ``status == 'extracted'`` document of
    this company whose ``retrieved_at`` is within ``primary_document_reuse_ttl_
    hours``. STRICTLY ``company_id``-scoped — a company never sees another
    company's documents. Gated behind BOTH the ingestion + citation-persistence
    flags: with either off, or with no ``company_id``, returns an EMPTY dict
    WITHOUT issuing any query (byte-identical dark path). The freshest document
    wins when two share a canonical URL.
    """
    # Gate: BOTH flags must be on. Either OFF ⇒ no query, no reuse (dark path).
    if not (
        getattr(cfg, "primary_document_ingestion_enabled", False)
        and getattr(cfg, "report_citation_persistence_enabled", False)
    ):
        return {}
    if company_id is None:
        return {}

    ttl_hours = max(0, int(getattr(cfg, "primary_document_reuse_ttl_hours", 24)))
    cutoff = _as_aware(now or _utcnow()) - timedelta(hours=ttl_hours)

    rows = (
        (
            await session.execute(
                select(ExtractedDocument)
                .where(
                    ExtractedDocument.company_id == company_id,
                    ExtractedDocument.status == _STATUS_EXTRACTED,
                )
                .order_by(ExtractedDocument.retrieved_at.desc())
            )
        )
        .scalars()
        .all()
    )
    fresh = [r for r in rows if _as_aware(r.retrieved_at) >= cutoff]
    if not fresh:
        return {}

    # Load all validated facts for the fresh docs in ONE query, grouped by doc.
    doc_ids = [r.id for r in fresh]
    facts_by_doc: dict[uuid.UUID, list[ExtractedFact]] = {}
    fact_rows = (
        (
            await session.execute(
                select(ExtractedFact).where(
                    ExtractedFact.extracted_document_id.in_(doc_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    for fact in fact_rows:
        facts_by_doc.setdefault(fact.extracted_document_id, []).append(fact)

    out: dict[str, ReusedDocument] = {}
    for doc in fresh:
        key = canonicalize_source_url(doc.canonical_url) or doc.canonical_url
        if not key or key in out:
            # A fresher document for this URL already won (rows ordered desc).
            continue
        out[key] = ReusedDocument(
            canonical_url=doc.canonical_url,
            content_hash=doc.content_hash,
            retrieved_at=_as_aware(doc.retrieved_at),
            artifact=_rebuild_artifact(doc, facts_by_doc.get(doc.id, [])),
        )
    return out


def _rebuild_artifact(
    doc: ExtractedDocument, facts: "Sequence[ExtractedFact]"
) -> "PrimaryDocumentArtifact":
    """Rebuild a fully-usable ``PrimaryDocumentArtifact`` from persisted rows.

    Local imports keep this WRITER module free of a runtime import cycle and off
    the (default) dark path's import cost. Only validated facts are persisted, so
    every rebuilt fact is ``validated`` + ``needs_human_review`` — never fabricated.
    """
    from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
    from app.services.sources.document_text_extractor import EVIDENCE_TYPE_GENERAL
    from app.services.sources.extracted_fact_validator import (
        VALIDATION_VALIDATED,
        ValidatedFact,
    )
    from app.services.sources.primary_document_extractor import (
        STATUS_EXTRACTED,
        PrimaryDocumentExcerpt,
        PrimaryDocumentExtraction,
    )
    from app.services.sources.taxonomy import T1_PRIMARY_FILING

    excerpts: list[PrimaryDocumentExcerpt] = []
    for i, raw in enumerate(doc.excerpts_json or [], start=1):
        if not isinstance(raw, dict):
            continue
        text = raw.get("text") or ""
        excerpts.append(
            PrimaryDocumentExcerpt(
                excerpt_id=raw.get("excerpt_id") or f"X{i}",
                text=text,
                page_number=raw.get("page_number"),
                section=raw.get("section"),
                heading=raw.get("heading"),
                table_location=raw.get("table_location"),
                extraction_method=raw.get("extraction_method")
                or doc.extraction_method,
                confidence=float(raw.get("confidence") or 0.0),
                char_count=int(raw.get("char_count") or len(text)),
                evidence_type=raw.get("evidence_type") or EVIDENCE_TYPE_GENERAL,
            )
        )

    extraction = PrimaryDocumentExtraction(
        content_hash=doc.content_hash,
        mime_type=doc.mime_type or "",
        extraction_method=doc.extraction_method or "",
        status=STATUS_EXTRACTED,
        page_count=doc.page_count,
        excerpts=excerpts,
    )

    validated_facts: list[ValidatedFact] = [
        ValidatedFact(
            label=fact.label,
            value_numeric=(
                float(fact.value_numeric) if fact.value_numeric is not None else None
            ),
            value_text=fact.value_text,
            unit=fact.unit,
            currency=fact.currency,
            scale=fact.scale,
            period=fact.period,
            page_number=fact.page_number,
            table_location=fact.table_location,
            extraction_method=fact.extraction_method or "",
            confidence=float(fact.confidence or 0.0),
            validation_status=VALIDATION_VALIDATED,
            needs_human_review=bool(fact.needs_human_review),
        )
        for fact in facts
    ]

    return PrimaryDocumentArtifact(
        source_url=doc.canonical_url,
        document_type=doc.source_type,
        content_tier=doc.source_tier or T1_PRIMARY_FILING,
        title=doc.title,
        retrieved_at=_as_aware(doc.retrieved_at),
        status=STATUS_EXTRACTED,
        extraction=extraction,
        validated_facts=validated_facts,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
