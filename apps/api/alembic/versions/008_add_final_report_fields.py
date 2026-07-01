"""add final report fields to reports table

Revision ID: 008
Revises: 007
Create Date: 2026-07-01

Phase 16 — Final Report Generator.

Adds Phase 16 columns to the existing `reports` table:
  final_report_version      — version string of the final report generator (e.g. "16.0.0")
  safety_validation_json    — JSONB: safety gate scan result (forbidden terms check)
  schema_validation_json    — JSONB: extended schema validation result for the report
  source_summary_json       — JSONB: source and citation quality summary
  scorecard_id              — FK → scorecards.id (SET NULL); links report to its scorecard

No investment recommendations, price targets, fair values, or upside
percentages are stored.  Human review is always required before any action.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("final_report_version", sa.String(20), nullable=True),
    )
    op.add_column(
        "reports",
        sa.Column("safety_validation_json", JSONB, nullable=True),
    )
    op.add_column(
        "reports",
        sa.Column("schema_validation_json", JSONB, nullable=True),
    )
    op.add_column(
        "reports",
        sa.Column("source_summary_json", JSONB, nullable=True),
    )
    op.add_column(
        "reports",
        sa.Column(
            "scorecard_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("scorecards.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_reports_scorecard_id",
        "reports",
        ["scorecard_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_reports_scorecard_id", table_name="reports")
    op.drop_column("reports", "scorecard_id")
    op.drop_column("reports", "source_summary_json")
    op.drop_column("reports", "schema_validation_json")
    op.drop_column("reports", "safety_validation_json")
    op.drop_column("reports", "final_report_version")
