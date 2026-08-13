import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExtractedDocument(Base):
    """An issuer primary document (annual report / registration document) that was
    fetched and text-extracted for bounded, citation-bound evidence.

    Phase 32A Slice 5 — FOUNDATION ONLY. This table stores the lineage + status of
    one ingested document; the ingestion behaviour that populates it is added in a
    later slice. ``content_hash`` is the SHA of the RAW fetched bytes and is the
    dedup identity, so re-fetching the same document never accumulates duplicate
    rows. A blocked / JS-gated / scanned document is recorded with an honest
    ``status`` (e.g. ``metadata_only`` / ``extraction_failed``) — a filing is never
    fabricated.
    """

    __tablename__ = "extracted_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # SHA of the RAW fetched bytes — dedup identity (unique index below).
    content_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    canonical_url: Mapped[str] = mapped_column(sa.String(2000), nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    source_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    source_tier: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    mime_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    title: Mapped[str | None] = mapped_column(sa.String(500))
    doc_date: Mapped[date | None] = mapped_column(sa.Date)
    period: Mapped[str | None] = mapped_column(sa.String(50))
    retrieved_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, nullable=False
    )
    # How the text was obtained: 'native_pdf' | 'html' | 'ocr'.
    extraction_method: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    page_count: Mapped[int | None] = mapped_column(sa.Integer)
    # Ingestion outcome: 'extracted' | 'metadata_only' | 'extraction_failed'.
    status: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    # Phase 32A Slice 5 (3c-iii) — the BOUNDED excerpts already produced for this
    # document (each capped by ``primary_document_max_excerpts_per_document`` /
    # ``primary_document_max_excerpt_chars`` at extraction time), stored so a later
    # report regeneration can REBUILD the extraction and REUSE it without a
    # re-fetch / re-extract. A JSON list of
    # ``{text, page_number, section, heading, table_location, extraction_method,
    # confidence, evidence_type, excerpt_id, char_count}``. Bounded on purpose —
    # this is never the full document text and never the raw table grid. NULL for
    # rows written before this column existed (reuse degrades to a re-fetch).
    excerpts_json: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    # Phase 32A corrective (Problem B) — the extraction/parsing/validation
    # pipeline version active when this row's ``excerpts_json`` +
    # ``ExtractedFact`` children were produced (see
    # ``app.services.sources.extraction_pipeline_version``). NULL for rows
    # written before this column existed — treated as stale/legacy, never
    # assumed compatible with the current parser/validator. A future report
    # regeneration reuses ``excerpts_json`` (the content layer) even when this
    # differs from the currently-deployed version, but re-derives facts under
    # current-code semantics rather than trusting the persisted
    # ``ExtractedFact`` rows unchanged.
    pipeline_version: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    # Lineage — SET NULL preserves research history on company / run deletion.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id",
            ondelete="SET NULL",
            name="fk_extracted_documents_company_id_companies",
        ),
        nullable=True,
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "agent_runs.id",
            ondelete="SET NULL",
            name="fk_extracted_documents_agent_run_id_agent_runs",
        ),
        nullable=True,
    )
    # Unused hook for a future blob-store copy of the raw bytes.
    blob_path: Mapped[str | None] = mapped_column(sa.String(1000))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        sa.Index(
            "ix_extracted_documents_content_hash", "content_hash", unique=True
        ),
        sa.Index("ix_extracted_documents_company_id", "company_id"),
        sa.Index("ix_extracted_documents_agent_run_id", "agent_run_id"),
    )


class ExtractedFact(Base):
    """A single primary fact (or excerpt-only text) parsed from an
    ``ExtractedDocument``.

    Phase 32A Slice 5 — FOUNDATION ONLY. Every fact is stored with its raw
    as-found text, provenance (page / table location) and an extraction
    ``confidence``; it is human-review-required by default and never a
    recommendation. Low-confidence text is retained as ``excerpt_only`` rather
    than promoted to a validated figure — numbers are never fabricated.
    """

    __tablename__ = "extracted_facts"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    extracted_document_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "extracted_documents.id",
            ondelete="CASCADE",
            name="fk_extracted_facts_document_id_extracted_documents",
        ),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    value_numeric: Mapped[Decimal | None] = mapped_column(sa.Numeric)
    # Raw value exactly as found in the document (never normalized away).
    value_text: Mapped[str | None] = mapped_column(sa.Text)
    unit: Mapped[str | None] = mapped_column(sa.String(50))
    currency: Mapped[str | None] = mapped_column(sa.String(10))
    scale: Mapped[str | None] = mapped_column(sa.String(50))
    period: Mapped[str | None] = mapped_column(sa.String(50))
    page_number: Mapped[int | None] = mapped_column(sa.Integer)
    # Locator for the source table/cell (e.g. "page=12;table=2;row=4;col=1").
    table_location: Mapped[str | None] = mapped_column(sa.String(200))
    extraction_method: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False)
    # 'validated' | 'excerpt_only' | 'rejected'.
    validation_status: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    needs_human_review: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index(
            "ix_extracted_facts_extracted_document_id", "extracted_document_id"
        ),
    )
