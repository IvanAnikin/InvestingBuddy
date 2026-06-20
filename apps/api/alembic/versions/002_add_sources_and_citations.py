"""add sources and citations

Revision ID: 002
Revises: 001
Create Date: 2026-06-20

Creates: sources, citations
sources stores research documents with dedup support via content_hash.
citations links report claims to source records.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("url", sa.String(2000), nullable=True),
        sa.Column("publisher", sa.String(200), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "retrieved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("credibility_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("blob_path", sa.String(1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sources_source_type", "sources", ["source_type"])
    op.create_index("ix_sources_retrieved_at", "sources", ["retrieved_at"])
    op.create_index("ix_sources_content_hash", "sources", ["content_hash"])

    op.create_table(
        "citations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("report_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("claim_text", sa.Text, nullable=True),
        sa.Column("source_quote", sa.Text, nullable=True),
        sa.Column("url", sa.String(2000), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_citations_source_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name="fk_citations_report_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name="fk_citations_agent_run_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_citations_report_id", "citations", ["report_id"])
    op.create_index("ix_citations_source_id", "citations", ["source_id"])
    op.create_index("ix_citations_agent_run_id", "citations", ["agent_run_id"])


def downgrade() -> None:
    op.drop_table("citations")
    op.drop_index("ix_sources_content_hash", table_name="sources")
    op.drop_index("ix_sources_retrieved_at", table_name="sources")
    op.drop_index("ix_sources_source_type", table_name="sources")
    op.drop_table("sources")
