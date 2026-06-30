"""add company_financial_snapshots table

Revision ID: 005
Revises: 004
Create Date: 2026-06-29

Phase 13 — EODHD Real Financial Data Integration.

New `company_financial_snapshots` table:
  Stores structured provider data snapshots for auditing, re-analysis,
  and deduplication. Every record traces to a company, agent run, provider,
  and retrieval timestamp.

  snapshot_type values:
    "fundamentals"  — full /fundamentals response from EODHD
    "price_history" — OHLCV data from any price provider
    "profile"       — company profile data

  raw_payload_json (JSONB) — the parsed provider response (no API keys)
  raw_payload_hash (VARCHAR 64) — SHA-256 for deduplication
  datapoints_json  (JSONB) — list of FundamentalDataPoint dicts
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_financial_snapshots",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        # Company reference
        sa.Column(
            "company_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=True),
        # Agent run reference
        sa.Column(
            "agent_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Provider metadata
        sa.Column("provider_name", sa.String(50), nullable=False),
        sa.Column("source_tier", sa.String(50), nullable=False),
        sa.Column("snapshot_type", sa.String(50), nullable=False),
        # Data provenance
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "data_quality",
            sa.String(50),
            nullable=False,
            server_default="B_single_credible",
        ),
        # Payload storage
        sa.Column("raw_payload_json", JSONB, nullable=True),
        sa.Column("raw_payload_hash", sa.String(64), nullable=True),
        sa.Column("datapoints_json", JSONB, nullable=True),
        # Record timestamp
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Indexes
    op.create_index(
        "ix_company_financial_snapshots_company_id",
        "company_financial_snapshots",
        ["company_id"],
    )
    op.create_index(
        "ix_company_financial_snapshots_agent_run_id",
        "company_financial_snapshots",
        ["agent_run_id"],
    )
    op.create_index(
        "ix_company_financial_snapshots_ticker",
        "company_financial_snapshots",
        ["ticker"],
    )
    op.create_index(
        "ix_company_financial_snapshots_raw_payload_hash",
        "company_financial_snapshots",
        ["raw_payload_hash"],
    )
    op.create_index(
        "ix_cfs_provider_ticker",
        "company_financial_snapshots",
        ["provider_name", "ticker"],
    )
    op.create_index(
        "ix_cfs_snapshot_type",
        "company_financial_snapshots",
        ["snapshot_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_cfs_snapshot_type", table_name="company_financial_snapshots")
    op.drop_index("ix_cfs_provider_ticker", table_name="company_financial_snapshots")
    op.drop_index(
        "ix_company_financial_snapshots_raw_payload_hash",
        table_name="company_financial_snapshots",
    )
    op.drop_index(
        "ix_company_financial_snapshots_ticker",
        table_name="company_financial_snapshots",
    )
    op.drop_index(
        "ix_company_financial_snapshots_agent_run_id",
        table_name="company_financial_snapshots",
    )
    op.drop_index(
        "ix_company_financial_snapshots_company_id",
        table_name="company_financial_snapshots",
    )
    op.drop_table("company_financial_snapshots")
