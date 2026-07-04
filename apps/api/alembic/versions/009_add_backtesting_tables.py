"""add backtesting tables

Revision ID: 009
Revises: 008
Create Date: 2026-07-04

Phase 22 — Judge + Backtesting Framework.

Adds three new tables:
  backtest_runs           — named evaluation runs
  backtest_results        — per-report outcome + judge evaluation
  thesis_tracking_events  — optional lightweight thesis event log

No investment recommendations, price targets, fair values, or upside
percentages are stored.  All evaluations are internal historical quality
assessments only.  Human review is required before any action.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers
revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # backtest_runs
    # ------------------------------------------------------------------
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("horizon_days", sa.Integer, nullable=True),
        sa.Column("benchmark_symbol", sa.String(50), nullable=True),
        sa.Column("provider_name", sa.String(100), nullable=False, server_default="mock"),
        sa.Column("parameters_json", JSONB, nullable=True),
        sa.Column("summary_json", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
    )

    # ------------------------------------------------------------------
    # backtest_results
    # ------------------------------------------------------------------
    op.create_table(
        "backtest_results",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "backtest_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("backtest_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "report_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("reports.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "company_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "scorecard_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("scorecards.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ticker", sa.String(50), nullable=True),
        sa.Column("exchange", sa.String(50), nullable=True),
        sa.Column("evaluation_start_date", sa.Date, nullable=True),
        sa.Column("evaluation_end_date", sa.Date, nullable=True),
        sa.Column("horizon_days", sa.Integer, nullable=True),
        sa.Column("benchmark_symbol", sa.String(50), nullable=True),
        sa.Column("outcome_json", JSONB, nullable=True),
        sa.Column("judge_evaluation_json", JSONB, nullable=True),
        sa.Column("warnings_json", JSONB, nullable=True),
        sa.Column("missing_data_json", JSONB, nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_backtest_results_backtest_run_id",
        "backtest_results",
        ["backtest_run_id"],
    )
    op.create_index(
        "ix_backtest_results_report_id",
        "backtest_results",
        ["report_id"],
    )
    op.create_index(
        "ix_backtest_results_company_id",
        "backtest_results",
        ["company_id"],
    )

    # ------------------------------------------------------------------
    # thesis_tracking_events
    # ------------------------------------------------------------------
    op.create_table(
        "thesis_tracking_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "report_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("reports.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "company_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("event_date", sa.Date, nullable=True),
        sa.Column("payload_json", JSONB, nullable=True),
        sa.Column("source_tier", sa.String(50), nullable=True),
        sa.Column("provider_name", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_thesis_tracking_events_report_id",
        "thesis_tracking_events",
        ["report_id"],
    )
    op.create_index(
        "ix_thesis_tracking_events_company_id",
        "thesis_tracking_events",
        ["company_id"],
    )


def downgrade() -> None:
    op.drop_table("thesis_tracking_events")
    op.drop_table("backtest_results")
    op.drop_table("backtest_runs")
