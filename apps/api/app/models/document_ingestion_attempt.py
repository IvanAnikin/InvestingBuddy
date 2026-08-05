import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.services.sources import ingestion_status as _vocab


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# CLOSED vocabularies — re-exported, not redefined.
#
# These are DEFINED ONCE in ``app.services.sources.ingestion_status`` (the layer
# that PRODUCES these values) and re-exported here so the persistence layer and
# the fetch/extraction layer can never drift apart. That module is deliberately
# dependency-free — pure constants, no imports — so a model importing it does not
# invert the model/service layering.
#
# ``failure_code`` may only ever hold a member of ``ALL_FAILURE_CODES``: raw
# provider or exception text is never persisted, and anything outside the
# vocabulary is coerced to ``unknown`` by ``sanitize_failure_code``.
# --------------------------------------------------------------------------- #

ALL_STATUSES: tuple[str, ...] = _vocab.ALL_ATTEMPT_STATUSES
ALL_FAILURE_CODES: tuple[str, ...] = _vocab.ALL_FAILURE_CODES

STATUS_DISCOVERED = _vocab.ATTEMPT_DISCOVERED
STATUS_FETCHED = _vocab.ATTEMPT_FETCHED
STATUS_EXTRACTED = _vocab.ATTEMPT_EXTRACTED
STATUS_METADATA_ONLY = _vocab.ATTEMPT_METADATA_ONLY
STATUS_UNSUPPORTED = _vocab.ATTEMPT_UNSUPPORTED
STATUS_ENCRYPTED = _vocab.ATTEMPT_ENCRYPTED
STATUS_PASSWORD_PROTECTED = _vocab.ATTEMPT_PASSWORD_PROTECTED
STATUS_MALFORMED = _vocab.ATTEMPT_MALFORMED
STATUS_REJECTED_SECURITY = _vocab.ATTEMPT_REJECTED_SECURITY
STATUS_TIMEOUT = _vocab.ATTEMPT_TIMEOUT
STATUS_EXTRACTION_FAILED = _vocab.ATTEMPT_EXTRACTION_FAILED

FAILURE_BLOCKED_HOST = _vocab.FAILURE_BLOCKED_HOST
FAILURE_BLOCKED_SCHEME = _vocab.FAILURE_BLOCKED_SCHEME
FAILURE_BLOCKED_PRIVATE_IP = _vocab.FAILURE_BLOCKED_PRIVATE_IP
FAILURE_BLOCKED_REDIRECT = _vocab.FAILURE_BLOCKED_REDIRECT
FAILURE_REDIRECT_LIMIT = _vocab.FAILURE_REDIRECT_LIMIT
FAILURE_UNSUPPORTED_CONTENT_TYPE = _vocab.FAILURE_UNSUPPORTED_CONTENT_TYPE
FAILURE_HTTP_CLIENT_ERROR = _vocab.FAILURE_HTTP_CLIENT_ERROR
FAILURE_HTTP_SERVER_ERROR = _vocab.FAILURE_HTTP_SERVER_ERROR
FAILURE_FETCH_TIMEOUT = _vocab.FAILURE_FETCH_TIMEOUT
FAILURE_EXTRACTION_TIMEOUT = _vocab.FAILURE_EXTRACTION_TIMEOUT
FAILURE_NOT_A_PDF = _vocab.FAILURE_NOT_A_PDF
FAILURE_ENCRYPTED_PDF = _vocab.FAILURE_ENCRYPTED_PDF
FAILURE_PASSWORD_PROTECTED_PDF = _vocab.FAILURE_PASSWORD_PROTECTED_PDF
FAILURE_MALFORMED_PDF = _vocab.FAILURE_MALFORMED_PDF
FAILURE_SCANNED_NO_TEXT = _vocab.FAILURE_SCANNED_NO_TEXT
FAILURE_EMPTY_EXTRACTION = _vocab.FAILURE_EMPTY_EXTRACTION
FAILURE_BUDGET_EXHAUSTED = _vocab.FAILURE_BUDGET_EXHAUSTED
FAILURE_CLIENT_UNAVAILABLE = _vocab.FAILURE_CLIENT_UNAVAILABLE
FAILURE_UNKNOWN = _vocab.FAILURE_UNKNOWN


class DocumentIngestionAttempt(Base):
    """One attempt to ingest an issuer primary document — successful or NOT.

    Phase 32A Slice 5B.1. Slice 5A persisted a row only when a document was
    actually ``extracted``, so every failed attempt vanished: a staging run that
    tried documents across seven issuers left ``extracted_documents`` at 0 with no
    durable record of what was tried or why it failed. This table is that honest
    record — one row per ``(company_id, agent_run_id, url_hash)`` attempt, updated
    in place when the same URL is re-attempted in the same run.

    Bounded and secret-free by construction: ``canonical_url`` is credential-
    stripped, ``failure_code`` comes from a CLOSED sanitized vocabulary (never raw
    provider or exception text), and only an HTTP status CLASS is kept rather than
    the exact code. No document bodies, no OCR text, no financial claims, no
    recommendations are stored here.
    """

    __tablename__ = "document_ingestion_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Lineage — SET NULL preserves attempt history on company / run deletion.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id",
            ondelete="SET NULL",
            name="fk_document_ingestion_attempts_company_id_companies",
        ),
        nullable=True,
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "agent_runs.id",
            ondelete="SET NULL",
            name="fk_document_ingestion_attempts_agent_run_id_agent_runs",
        ),
        nullable=True,
    )
    # Credential-stripped canonical URL (never a signed / secret-bearing URL).
    canonical_url: Mapped[str] = mapped_column(sa.String(2000), nullable=False)
    # sha256 of ``canonical_url`` — the per-run attempt identity.
    url_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    source_tier: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    # e.g. 'annual_report' | 'registration_document' | 'ir_page'.
    doc_kind: Mapped[str | None] = mapped_column(sa.String(50))
    # How the candidate URL was found (e.g. 'static_link' | 'sec_index').
    discovery_strategy: Mapped[str | None] = mapped_column(sa.String(50))
    attempted_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        nullable=False,
    )
    # CLOSED vocabulary — see ``ALL_STATUSES`` above.
    status: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    # CLOSED, sanitized vocabulary — see ``ALL_FAILURE_CODES`` above.
    failure_code: Mapped[str | None] = mapped_column(sa.String(50))
    mime_type: Mapped[str | None] = mapped_column(sa.String(100))
    # Only '2xx' / '3xx' / '4xx' / '5xx' — never the exact status code.
    http_status_class: Mapped[str | None] = mapped_column(sa.String(10))
    # 'native_pdf' | 'html' | 'ocr' (NULL when nothing was extracted).
    extraction_method: Mapped[str | None] = mapped_column(sa.String(50))
    page_count: Mapped[int | None] = mapped_column(sa.Integer)
    # sha256 of the raw fetched bytes when a body was obtained — lets an attempt
    # be tied back to its ``extracted_documents`` row without duplicating it.
    content_hash: Mapped[str | None] = mapped_column(sa.String(64))
    fetch_ms: Mapped[int | None] = mapped_column(sa.Integer)
    extraction_ms: Mapped[int | None] = mapped_column(sa.Integer)
    total_ms: Mapped[int | None] = mapped_column(sa.Integer)
    # Was the connection PINNED to a pre-validated address (ADR-014/015)?
    # True = pinned; False = an honest "not pinned" (kill-switch off, or this
    # httpx build cannot support it); NULL = no fetch was attempted. Recording
    # False is the point: the degradation is never silent.
    pinned: Mapped[bool | None] = mapped_column(sa.Boolean)
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
        sa.Index("ix_document_ingestion_attempts_company_id", "company_id"),
        sa.Index("ix_document_ingestion_attempts_agent_run_id", "agent_run_id"),
        sa.Index("ix_document_ingestion_attempts_url_hash", "url_hash"),
        # Idempotency key. NOTE: PostgreSQL NULLs never collide in a UNIQUE
        # constraint, so a NULL company_id / agent_run_id is NOT protected here —
        # the writer service pre-queries for the existing row as well.
        sa.UniqueConstraint(
            "company_id",
            "agent_run_id",
            "url_hash",
            name="uq_document_ingestion_attempts_run_url",
        ),
    )
