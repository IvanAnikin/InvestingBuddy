"""add extracted_documents and extracted_facts

Revision ID: 013
Revises: 012
Create Date: 2026-08-04

Phase 32A Slice 5 — FOUNDATION ONLY for bounded primary-document ingestion.

Creates: extracted_documents, extracted_facts

``extracted_documents`` records the lineage + status of one ingested issuer
primary document (annual report / registration document), deduped by a
``content_hash`` of the RAW fetched bytes (unique index). ``extracted_facts``
stores the bounded primary facts / excerpts parsed from a document, each with
provenance (page / table location), an extraction ``confidence``, a validation
status, and a human-review flag.

Nothing is wired to populate these tables in this slice; ingestion behaviour
arrives in a later slice. No investment recommendations, price targets, fair
values, or upside percentages are stored — extracted facts are research
evidence that always requires human review.

Reversible. No data backfill.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "extracted_documents",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("canonical_url", sa.String(2000), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_tier", sa.String(50), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("doc_date", sa.Date(), nullable=True),
        sa.Column("period", sa.String(50), nullable=True),
        sa.Column(
            "retrieved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("extraction_method", sa.String(50), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("blob_path", sa.String(1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_extracted_documents_company_id_companies",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name="fk_extracted_documents_agent_run_id_agent_runs",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_extracted_documents_content_hash",
        "extracted_documents",
        ["content_hash"],
        unique=True,
    )
    op.create_index(
        "ix_extracted_documents_company_id",
        "extracted_documents",
        ["company_id"],
    )
    op.create_index(
        "ix_extracted_documents_agent_run_id",
        "extracted_documents",
        ["agent_run_id"],
    )

    op.create_table(
        "extracted_facts",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("extracted_document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("value_numeric", sa.Numeric(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("scale", sa.String(50), nullable=True),
        sa.Column("period", sa.String(50), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("table_location", sa.String(200), nullable=True),
        sa.Column("extraction_method", sa.String(50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("validation_status", sa.String(50), nullable=False),
        sa.Column("needs_human_review", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["extracted_document_id"],
            ["extracted_documents.id"],
            name="fk_extracted_facts_document_id_extracted_documents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_extracted_facts_extracted_document_id",
        "extracted_facts",
        ["extracted_document_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extracted_facts_extracted_document_id",
        table_name="extracted_facts",
    )
    op.drop_table("extracted_facts")
    op.drop_index(
        "ix_extracted_documents_agent_run_id",
        table_name="extracted_documents",
    )
    op.drop_index(
        "ix_extracted_documents_company_id",
        table_name="extracted_documents",
    )
    op.drop_index(
        "ix_extracted_documents_content_hash",
        table_name="extracted_documents",
    )
    op.drop_table("extracted_documents")
