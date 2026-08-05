"""add document_ingestion_attempts

Revision ID: 014
Revises: 013
Create Date: 2026-08-05

Phase 32A Slice 5B.1 — a durable, honest record of EVERY primary-document
ingestion attempt, including the ones that FAILED.

Creates: document_ingestion_attempts

Slice 5A only ever wrote a row when a document was successfully ``extracted``
(see ``extracted_document_service.persist_primary_document_artifacts``), so a
staging run that tried documents across seven issuers and extracted none left
``extracted_documents`` / ``extracted_facts`` at 0/0 with NO durable record of
what was tried or why it failed. This table is that record: one row per
(company, run, URL) attempt carrying a CLOSED-vocabulary ``status`` and
sanitized ``failure_code``.

Deliberately bounded and secret-free: no raw provider exception text, no
document bodies, no OCR text, no secrets / signed query strings (the URL is
canonicalized + credential-stripped before storage), and only an HTTP status
CLASS ("4xx") rather than the exact code. Nothing here is a financial claim or
a recommendation.

Reversible. No data backfill.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_ingestion_attempts",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("canonical_url", sa.String(2000), nullable=False),
        # sha256 of the canonicalized (credential-stripped) URL — the stable
        # per-attempt identity, so signed-token variants of the same document
        # collapse onto one row.
        sa.Column("url_hash", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_tier", sa.String(50), nullable=False),
        sa.Column("doc_kind", sa.String(50), nullable=True),
        sa.Column("discovery_strategy", sa.String(50), nullable=True),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # CLOSED vocabulary (see app/models/document_ingestion_attempt.py).
        sa.Column("status", sa.String(50), nullable=False),
        # CLOSED, sanitized vocabulary — never raw provider/exception text.
        sa.Column("failure_code", sa.String(50), nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        # Only the CLASS ("2xx"/"3xx"/"4xx"/"5xx") — never the exact code.
        sa.Column("http_status_class", sa.String(10), nullable=True),
        sa.Column("extraction_method", sa.String(50), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("fetch_ms", sa.Integer(), nullable=True),
        sa.Column("extraction_ms", sa.Integer(), nullable=True),
        sa.Column("total_ms", sa.Integer(), nullable=True),
        # Was the connection PINNED to a pre-validated address (ADR-014/015)?
        # TRUE = pinned; FALSE = an honest "not pinned" (kill-switch off, or the
        # httpx build cannot support it); NULL = no fetch was attempted.
        sa.Column("pinned", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_document_ingestion_attempts_company_id_companies",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name="fk_document_ingestion_attempts_agent_run_id_agent_runs",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        # Idempotency key: re-attempting the SAME url in the SAME run updates the
        # existing row instead of appending a duplicate.
        #
        # NOTE: in PostgreSQL NULLs never collide inside a UNIQUE constraint, so
        # a row with a NULL company_id or NULL agent_run_id is NOT protected by
        # this constraint. The writer service therefore ALSO pre-queries for an
        # existing (company_id, agent_run_id, url_hash) row and updates it in
        # place; the constraint is the backstop, the pre-query is the guarantee.
        sa.UniqueConstraint(
            "company_id",
            "agent_run_id",
            "url_hash",
            name="uq_document_ingestion_attempts_run_url",
        ),
    )
    op.create_index(
        "ix_document_ingestion_attempts_company_id",
        "document_ingestion_attempts",
        ["company_id"],
    )
    op.create_index(
        "ix_document_ingestion_attempts_agent_run_id",
        "document_ingestion_attempts",
        ["agent_run_id"],
    )
    op.create_index(
        "ix_document_ingestion_attempts_url_hash",
        "document_ingestion_attempts",
        ["url_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_ingestion_attempts_url_hash",
        table_name="document_ingestion_attempts",
    )
    op.drop_index(
        "ix_document_ingestion_attempts_agent_run_id",
        table_name="document_ingestion_attempts",
    )
    op.drop_index(
        "ix_document_ingestion_attempts_company_id",
        table_name="document_ingestion_attempts",
    )
    op.drop_table("document_ingestion_attempts")
