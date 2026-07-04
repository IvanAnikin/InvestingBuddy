"""add discovery screener tables

Revision ID: 006
Revises: 005
Create Date: 2026-06-30

Phase 14 — Company Discovery / Screener.

Three new tables:
  screening_universes  — reusable universe definitions (region, exchange, sector, theme)
  screening_runs       — individual executions of a universe screen
  screening_candidates — companies discovered by a screening run (internal funnel only)

No investment recommendations, price targets, or fair values are stored.
Candidates carry only internal status values:
  candidate_found | needs_data | needs_primary_sources |
  ready_for_deeper_analysis | rejected_by_screen | error
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── screening_universes ──────────────────────────────────────────────────
    op.create_table(
        "screening_universes",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("exchange", sa.String(50), nullable=True),
        sa.Column("sector_filter", sa.String(100), nullable=True),
        sa.Column("theme", sa.String(100), nullable=True),
        sa.Column("provider_name", sa.String(50), nullable=False, server_default="mock"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_su_theme", "screening_universes", ["theme"])
    op.create_index("ix_su_region", "screening_universes", ["region"])
    op.create_index("ix_su_provider", "screening_universes", ["provider_name"])

    # ── screening_runs ───────────────────────────────────────────────────────
    op.create_table(
        "screening_runs",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "universe_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("screening_universes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("provider_name", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parameters_json", JSONB, nullable=True),
        sa.Column("summary_json", JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_sr_universe_id", "screening_runs", ["universe_id"])
    op.create_index("ix_sr_status", "screening_runs", ["status"])
    op.create_index("ix_sr_provider", "screening_runs", ["provider_name"])
    op.create_index("ix_sr_created_at", "screening_runs", ["created_at"])

    # ── screening_candidates ─────────────────────────────────────────────────
    op.create_table(
        "screening_candidates",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "screening_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("screening_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # identity
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=True),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("sector", sa.String(100), nullable=True),
        sa.Column("provider_symbol", sa.String(50), nullable=True),
        # basic financials
        sa.Column("market_cap", sa.Numeric(20, 2), nullable=True),
        sa.Column("currency", sa.String(10), nullable=True),
        # internal status (never public recommendation)
        sa.Column(
            "candidate_status",
            sa.String(50),
            nullable=False,
            server_default="candidate_found",
        ),
        # discovery metadata (JSONB arrays)
        sa.Column("discovery_reasons_json", JSONB, nullable=True),
        sa.Column("available_data_json", JSONB, nullable=True),
        sa.Column("missing_data_json", JSONB, nullable=True),
        # source quality
        sa.Column("source_tier", sa.String(50), nullable=True),
        sa.Column("data_quality", sa.String(50), nullable=True),
        sa.Column("warnings_json", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_sc_screening_run_id", "screening_candidates", ["screening_run_id"]
    )
    op.create_index("ix_sc_candidate_status", "screening_candidates", ["candidate_status"])
    op.create_index("ix_sc_ticker", "screening_candidates", ["ticker"])
    op.create_index("ix_sc_company_id", "screening_candidates", ["company_id"])


def downgrade() -> None:
    op.drop_table("screening_candidates")
    op.drop_table("screening_runs")
    op.drop_table("screening_universes")
