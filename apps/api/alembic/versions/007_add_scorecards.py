"""add scorecards table

Revision ID: 007
Revises: 006
Create Date: 2026-07-01

Phase 15 — Scoring + Valuation Framework.

One new table:
  scorecards — multi-dimension internal research attractiveness scorecard
               for screening candidates and company analysis outputs.

No investment recommendations, price targets, fair values, or upside
percentages are stored.

internal_status allowed values (research queue labels — never public):
  not_enough_data
  low_priority_research
  needs_primary_sources
  ready_for_deeper_analysis
  high_priority_for_human_review
  reject_due_to_data_quality
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scorecards",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # source links
        sa.Column(
            "company_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "screening_candidate_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("screening_candidates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "report_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("reports.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # score type
        sa.Column(
            "score_type",
            sa.String(50),
            nullable=False,
            server_default="candidate_scoring",
        ),
        # composite score 0–100
        sa.Column("overall_score", sa.Integer, nullable=False, server_default="0"),
        # internal status (admin-only research queue label — not a public recommendation)
        sa.Column(
            "internal_status",
            sa.String(50),
            nullable=False,
            server_default="not_enough_data",
        ),
        # JSONB payloads
        sa.Column("scores_json", JSONB, nullable=True),
        sa.Column("warnings_json", JSONB, nullable=True),
        sa.Column("missing_data_json", JSONB, nullable=True),
        sa.Column("source_quality_summary_json", JSONB, nullable=True),
        # provider
        sa.Column("provider_name", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_sc_score_company_id", "scorecards", ["company_id"])
    op.create_index(
        "ix_sc_score_candidate_id", "scorecards", ["screening_candidate_id"]
    )
    op.create_index("ix_sc_score_report_id", "scorecards", ["report_id"])
    op.create_index("ix_sc_score_type", "scorecards", ["score_type"])
    op.create_index("ix_sc_overall_score", "scorecards", ["overall_score"])
    op.create_index("ix_sc_internal_status", "scorecards", ["internal_status"])


def downgrade() -> None:
    op.drop_index("ix_sc_internal_status", table_name="scorecards")
    op.drop_index("ix_sc_overall_score", table_name="scorecards")
    op.drop_index("ix_sc_score_type", table_name="scorecards")
    op.drop_index("ix_sc_score_report_id", table_name="scorecards")
    op.drop_index("ix_sc_score_candidate_id", table_name="scorecards")
    op.drop_index("ix_sc_score_company_id", table_name="scorecards")
    op.drop_table("scorecards")
