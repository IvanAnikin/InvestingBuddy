"""
Primary-document provenance schemas — Phase 32A Slice 5B.3.

Bounded, authenticated admin-facing view of what the primary-document
ingestion pipeline (Slice 5/5A/5B.1/5B.2) actually did for one report's
generating run: what was discovered, attempted, extracted (natively or via
OCR), what facts were validated, and honest gap/failure states. Never exposes
raw document bodies, raw OCR output, raw HTML, provider exceptions, signed
URLs, or credentials — every field here already went through the existing
bounded/sanitized persistence layer (``extracted_document_service.py``,
``document_ingestion_attempt`` model) before reaching this schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class PrimaryDocumentFactRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    label: str
    value_numeric: float | None
    value_text: str | None
    unit: str | None
    currency: str | None
    period: str | None
    page_number: int | None
    table_location: str | None
    extraction_method: str
    confidence: float
    validation_status: str
    needs_human_review: bool


class PrimaryDocumentExcerptRead(BaseModel):
    """One already-bounded excerpt (capped at write time — never a raw dump)."""

    text: str
    page_number: int | None = None
    section: str | None = None
    heading: str | None = None
    table_location: str | None = None
    extraction_method: str | None = None
    confidence: float | None = None


class PrimaryDocumentRead(BaseModel):
    """One ingestion attempt for this report's run, with any persisted
    document/fact detail joined in by raw-bytes ``content_hash``."""

    attempt_id: uuid.UUID
    canonical_url: str
    title: str | None
    source_type: str
    source_tier: str
    doc_kind: str | None
    discovery_strategy: str | None
    attempted_at: datetime
    # Attempt-level status — the closed ingestion_status vocabulary
    # ('extracted' | 'metadata_only' | 'extraction_failed' | 'encrypted' | ...).
    status: str
    failure_code: str | None
    mime_type: str | None
    extraction_method: str | None
    page_count: int | None
    fetch_ms: int | None
    extraction_ms: int | None
    total_ms: int | None
    pinned: bool | None
    content_hash: str | None
    # True when a persisted ExtractedDocument with this content_hash already
    # existed before this attempt (a real, honestly-derived reuse signal —
    # not a separately-persisted flag) — i.e. this run REUSED a prior
    # extraction rather than re-fetching/re-extracting it.
    reused: bool
    excerpts: list[PrimaryDocumentExcerptRead]
    facts: list[PrimaryDocumentFactRead]


class PrimaryDocumentSummary(BaseModel):
    discovered_count: int
    attempted_count: int
    extracted_count: int
    metadata_only_count: int
    failed_count: int
    native_count: int
    ocr_count: int
    validated_fact_count: int
    reused_count: int
    evidence_reference_count: int


class ReportPrimaryDocumentsResponse(BaseModel):
    report_id: uuid.UUID
    company_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    summary: PrimaryDocumentSummary
    documents: list[PrimaryDocumentRead]
