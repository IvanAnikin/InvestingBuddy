"""
Primary-document provenance READ VIEW — Phase 32A Slice 5B.3.

Bounded, read-only assembly of "what did primary-document ingestion actually
do for this report's generating run" from the durable Slice 5/5B.1/5B.2
tables (``document_ingestion_attempts``, ``extracted_documents``,
``extracted_facts``). Never writes anything; never fabricates a document,
fact, or count that isn't backed by a real persisted row.

Scoping: ``document_ingestion_attempts`` is the source of truth for WHAT WAS
ATTEMPTED in this report's own run (Slice 5B.1 persists one row per attempt
per run, including reused/failed outcomes — a fresh run always gets its own
attempt rows even when the underlying document is reused, not re-fetched).
``extracted_documents``/``extracted_facts`` are then joined in by raw-bytes
``content_hash`` (the stable dedup identity across reuse) rather than by
their own ``agent_run_id`` — a document's ``agent_run_id`` reflects whichever
run FIRST created it, which would silently miss it for a later run that
correctly reused it without re-persisting.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_ingestion_attempt import (
    STATUS_EXTRACTED,
    STATUS_METADATA_ONLY,
    DocumentIngestionAttempt,
)
from app.models.extracted_document import ExtractedDocument, ExtractedFact
from app.schemas.primary_document import (
    PrimaryDocumentExcerptRead,
    PrimaryDocumentFactRead,
    PrimaryDocumentRead,
    PrimaryDocumentSummary,
    ReportPrimaryDocumentsResponse,
)
from app.services.sources.primary_document_extractor import METHOD_OCR

# A document is considered "reused" by this run if its persisted row was
# created more than this much before the attempt that references it — a
# small tolerance absorbs normal in-request clock skew between the fetch and
# the persistence write within the SAME run, without misclassifying a
# same-run fresh extraction as a reuse.
_REUSE_TOLERANCE = timedelta(seconds=5)

def _as_aware(dt: datetime) -> datetime:
    """Treat a naive datetime (SQLite round-trip) as UTC for safe comparison."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


_FAILED_ATTEMPT_STATUSES = frozenset(
    {
        "extraction_failed",
        "encrypted",
        "password_protected",
        "malformed",
        "rejected_security",
        "timeout",
        "unsupported",
    }
)


async def get_report_primary_documents(
    db: AsyncSession,
    *,
    report_company_id: uuid.UUID | None,
    report_agent_run_id: uuid.UUID | None,
    report_id: uuid.UUID,
) -> ReportPrimaryDocumentsResponse:
    """Assemble the bounded provenance view for one report.

    Scoped to the report's OWN generating run (``report_agent_run_id``) when
    known; falls back to company-wide (``report_company_id``) for legacy
    reports created before lineage stamping (Slice 3) — never cross-company.
    A report with neither known field returns an honest empty response
    (never another report's/company's data).
    """
    attempts: list[DocumentIngestionAttempt] = []
    if report_agent_run_id is not None:
        result = await db.execute(
            select(DocumentIngestionAttempt)
            .where(DocumentIngestionAttempt.agent_run_id == report_agent_run_id)
            .order_by(DocumentIngestionAttempt.attempted_at.asc())
        )
        attempts = list(result.scalars().all())
    elif report_company_id is not None:
        result = await db.execute(
            select(DocumentIngestionAttempt)
            .where(DocumentIngestionAttempt.company_id == report_company_id)
            .order_by(DocumentIngestionAttempt.attempted_at.asc())
        )
        attempts = list(result.scalars().all())

    content_hashes = [a.content_hash for a in attempts if a.content_hash]
    documents_by_hash: dict[str, ExtractedDocument] = {}
    facts_by_document_id: dict[uuid.UUID, list[ExtractedFact]] = {}
    if content_hashes:
        doc_result = await db.execute(
            select(ExtractedDocument).where(
                ExtractedDocument.content_hash.in_(content_hashes)
            )
        )
        docs = list(doc_result.scalars().all())
        documents_by_hash = {d.content_hash: d for d in docs}
        doc_ids = [d.id for d in docs]
        if doc_ids:
            fact_result = await db.execute(
                select(ExtractedFact).where(
                    ExtractedFact.extracted_document_id.in_(doc_ids),
                    # Phase 32A corrective (cache/derivation correctness) — a
                    # revalidation can supersede a document's fact set
                    # (``is_active=False`` on the superseded rows, kept for
                    # audit only). This admin view must show the CURRENT
                    # set, never a stale row mixed in alongside its own
                    # replacement or double-counted in
                    # ``validated_fact_count`` below.
                    ExtractedFact.is_active.is_(True),
                )
            )
            for fact in fact_result.scalars().all():
                facts_by_document_id.setdefault(
                    fact.extracted_document_id, []
                ).append(fact)

    documents: list[PrimaryDocumentRead] = []
    extracted_count = 0
    metadata_only_count = 0
    failed_count = 0
    native_count = 0
    ocr_count = 0
    validated_fact_count = 0
    reused_count = 0
    # A document can be pointed to by more than one attempt in the same run
    # (e.g. discovered via two different candidate URLs/discovery strategies
    # that canonicalize to the same content_hash). Per-attempt DISPLAY
    # (facts/excerpts on each document card) intentionally repeats the same
    # document's data for every attempt that references it, but SUMMARY
    # counts are a property of the document, not the attempt, and must only
    # ever be counted once per unique document — never fabricate a higher
    # count than the number of real persisted rows.
    counted_document_ids: set[uuid.UUID] = set()

    for attempt in attempts:
        doc = documents_by_hash.get(attempt.content_hash) if attempt.content_hash else None
        excerpts: list[PrimaryDocumentExcerptRead] = []
        facts: list[PrimaryDocumentFactRead] = []
        reused = False
        if doc is not None:
            for raw in doc.excerpts_json or []:
                excerpts.append(
                    PrimaryDocumentExcerptRead(
                        text=str(raw.get("text", "")),
                        page_number=raw.get("page_number"),
                        section=raw.get("section"),
                        heading=raw.get("heading"),
                        table_location=raw.get("table_location"),
                        extraction_method=raw.get("extraction_method"),
                        confidence=raw.get("confidence"),
                    )
                )
            first_time_seeing_doc = doc.id not in counted_document_ids
            for fact in facts_by_document_id.get(doc.id, []):
                facts.append(PrimaryDocumentFactRead.model_validate(fact))
                if first_time_seeing_doc and fact.validation_status == "validated":
                    validated_fact_count += 1
            if doc.created_at is not None and attempt.attempted_at is not None:
                reused = (
                    _as_aware(attempt.attempted_at) - _as_aware(doc.created_at)
                ) > _REUSE_TOLERANCE
            if reused and first_time_seeing_doc:
                reused_count += 1
            counted_document_ids.add(doc.id)

        if attempt.status == STATUS_EXTRACTED:
            extracted_count += 1
            if attempt.extraction_method == METHOD_OCR:
                ocr_count += 1
            elif attempt.extraction_method:
                native_count += 1
        elif attempt.status == STATUS_METADATA_ONLY:
            metadata_only_count += 1
        elif attempt.status in _FAILED_ATTEMPT_STATUSES:
            failed_count += 1

        documents.append(
            PrimaryDocumentRead(
                attempt_id=attempt.id,
                canonical_url=attempt.canonical_url,
                title=doc.title if doc is not None else None,
                source_type=attempt.source_type,
                source_tier=attempt.source_tier,
                doc_kind=attempt.doc_kind,
                discovery_strategy=attempt.discovery_strategy,
                attempted_at=attempt.attempted_at,
                status=attempt.status,
                failure_code=attempt.failure_code,
                mime_type=attempt.mime_type,
                extraction_method=attempt.extraction_method,
                page_count=attempt.page_count,
                fetch_ms=attempt.fetch_ms,
                extraction_ms=attempt.extraction_ms,
                total_ms=attempt.total_ms,
                pinned=attempt.pinned,
                content_hash=attempt.content_hash,
                reused=reused,
                excerpts=excerpts,
                facts=facts,
                # Derived, never a second stored number: the count IS the rows
                # returned on this row, so the two can never drift apart.
                persisted_validated_fact_count=len(facts),
            )
        )

    summary = PrimaryDocumentSummary(
        discovered_count=len(attempts),
        attempted_count=len(attempts),
        extracted_count=extracted_count,
        metadata_only_count=metadata_only_count,
        failed_count=failed_count,
        native_count=native_count,
        ocr_count=ocr_count,
        validated_fact_count=validated_fact_count,
        reused_count=reused_count,
        evidence_reference_count=metadata_only_count,
    )

    return ReportPrimaryDocumentsResponse(
        report_id=report_id,
        company_id=report_company_id,
        agent_run_id=report_agent_run_id,
        summary=summary,
        documents=documents,
    )
