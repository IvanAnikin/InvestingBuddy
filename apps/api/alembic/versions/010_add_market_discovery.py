"""add market candidate discovery tables

Revision ID: 010
Revises: 009
Create Date: 2026-07-17

Phase 25 — Real Market Candidate Discovery.

Adds two new tables:
  discovery_runs        — one execution of a bounded, internal-only market scan
  discovery_candidates  — a ranked internal research candidate produced by a run

SAFETY: These records are INTERNAL ADMIN ONLY. No BUY/SELL/HOLD/WATCH labels,
no price targets, no fair values, no upside/downside, and no recommendations are
stored. ``candidate_score`` is an internal prioritization signal only. Every
candidate has ``human_review_required=True`` and ``is_public=False``.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers
revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # discovery_runs
    # ------------------------------------------------------------------
    op.create_table(
        "discovery_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column(
            "provider_name", sa.String(50), nullable=False, server_default="free_real"
        ),
        sa.Column(
            "universe_source",
            sa.String(50),
            nullable=False,
            server_default="curated_seed",
        ),
        sa.Column("universe_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("requested_tickers", JSONB, nullable=True),
        sa.Column("processed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("candidate_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lookback_days", sa.Integer, nullable=False, server_default="90"),
        sa.Column("warnings", JSONB, nullable=True),
        sa.Column("config_json", JSONB, nullable=True),
        sa.Column("safety_notes", JSONB, nullable=True),
        sa.Column("created_by", sa.String(200), nullable=True),
        sa.Column(
            "human_review_required",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_discovery_runs_status", "discovery_runs", ["status"])
    op.create_index("ix_discovery_runs_created_at", "discovery_runs", ["created_at"])
    op.create_index("ix_discovery_runs_provider", "discovery_runs", ["provider_name"])

    # ------------------------------------------------------------------
    # discovery_candidates
    # ------------------------------------------------------------------
    op.create_table(
        "discovery_candidates",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "discovery_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("discovery_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Identity
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=False, server_default="US"),
        sa.Column("company_name", sa.String(200), nullable=True),
        sa.Column("legal_name", sa.String(200), nullable=True),
        sa.Column("sector", sa.String(100), nullable=True),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("lei", sa.String(40), nullable=True),
        sa.Column("website", sa.String(500), nullable=True),
        # Candidate scoring (internal prioritization only)
        sa.Column("candidate_score", sa.Float, nullable=True),
        sa.Column("candidate_score_grade", sa.String(50), nullable=True),
        sa.Column("rank", sa.Integer, nullable=True),
        # Component scores
        sa.Column("momentum_score", sa.Float, nullable=True),
        sa.Column("fundamentals_score", sa.Float, nullable=True),
        sa.Column("catalyst_score", sa.Float, nullable=True),
        sa.Column("source_quality_score", sa.Float, nullable=True),
        sa.Column("data_completeness_score", sa.Float, nullable=True),
        sa.Column("risk_penalty_score", sa.Float, nullable=True),
        sa.Column("labels_json", JSONB, nullable=True),
        sa.Column("score_explanation", sa.Text, nullable=True),
        # Momentum / trend signals
        sa.Column("momentum_label", sa.String(50), nullable=True),
        sa.Column("return_1m", sa.Float, nullable=True),
        sa.Column("return_3m", sa.Float, nullable=True),
        sa.Column("return_6m", sa.Float, nullable=True),
        sa.Column("pct_above_ma50", sa.Float, nullable=True),
        sa.Column("pct_above_ma200", sa.Float, nullable=True),
        # Catalyst signals
        sa.Column("catalyst_coverage_status", sa.String(50), nullable=True),
        sa.Column("latest_catalyst_date", sa.Date, nullable=True),
        sa.Column(
            "positive_catalyst_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "high_strength_catalyst_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "press_release_event_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("news_event_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("filing_event_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "primary_or_regulator_event_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "aggregator_only_event_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        # Financial / market summary
        sa.Column("latest_close", sa.Float, nullable=True),
        sa.Column("market_cap_mln", sa.Float, nullable=True),
        sa.Column("enterprise_value_mln", sa.Float, nullable=True),
        sa.Column("pe_ratio", sa.Float, nullable=True),
        sa.Column("revenue_mln", sa.Float, nullable=True),
        sa.Column("revenue_growth_yoy_pct", sa.Float, nullable=True),
        sa.Column("net_income_mln", sa.Float, nullable=True),
        sa.Column("free_cash_flow_mln", sa.Float, nullable=True),
        sa.Column("total_debt_mln", sa.Float, nullable=True),
        sa.Column("cash_mln", sa.Float, nullable=True),
        sa.Column("latest_annual_fy", sa.String(20), nullable=True),
        # Completeness / source quality
        sa.Column("source_quality", sa.String(50), nullable=True),
        sa.Column("missing_info_count", sa.Integer, nullable=True),
        sa.Column("blocking_gap_count", sa.Integer, nullable=True),
        sa.Column("source_tiers_json", JSONB, nullable=True),
        sa.Column("warnings_json", JSONB, nullable=True),
        sa.Column("missing_sources_json", JSONB, nullable=True),
        sa.Column("missing_fields_json", JSONB, nullable=True),
        sa.Column("raw_signal_json", JSONB, nullable=True),
        sa.Column("snapshot_json", JSONB, nullable=True),
        # Workflow linkage
        sa.Column(
            "analysis_report_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("reports.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "agent_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Safety
        sa.Column(
            "human_review_required",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "is_public", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column("safety_valid", sa.Boolean, nullable=True),
        sa.Column("schema_valid", sa.Boolean, nullable=True),
        sa.Column("safety_notes", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "discovery_run_id",
            "ticker",
            "exchange",
            name="uq_discovery_candidate_run_ticker_exchange",
        ),
    )
    op.create_index(
        "ix_discovery_candidates_run_id", "discovery_candidates", ["discovery_run_id"]
    )
    op.create_index(
        "ix_discovery_candidates_ticker", "discovery_candidates", ["ticker"]
    )
    op.create_index(
        "ix_discovery_candidates_score", "discovery_candidates", ["candidate_score"]
    )
    op.create_index(
        "ix_discovery_candidates_sector", "discovery_candidates", ["sector"]
    )
    op.create_index(
        "ix_discovery_candidates_grade",
        "discovery_candidates",
        ["candidate_score_grade"],
    )


def downgrade() -> None:
    op.drop_table("discovery_candidates")
    op.drop_table("discovery_runs")
