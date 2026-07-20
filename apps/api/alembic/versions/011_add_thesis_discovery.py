"""add thesis-to-universe discovery columns

Revision ID: 011
Revises: 010
Create Date: 2026-07-19

Phase 27 — Market Segment Discovery / Thesis-to-Universe Candidate Search.

Extends the Phase 25 discovery tables (no new tables) so a discovery run can be
driven by a natural-language market thesis instead of a ticker list:

  discovery_runs
    + mode                (ticker | thesis)          NOT NULL, default 'ticker'
    + thesis_text         raw admin thesis           NULL for ticker runs
    + parsed_thesis_json  structured parse           NULL for ticker runs
    + universe_json       generated universe/excl.   NULL for ticker runs

  discovery_candidates
    + thesis_relevance_score   pre-scan thesis match  (internal signal only)
    + combined_internal_score  blended internal score (internal signal only)
    + thesis_match_json        matched keywords/label/source

SAFETY: All added fields are INTERNAL PRIORITIZATION signals only — never a
recommendation, price target, fair value, or BUY/SELL/HOLD/WATCH label. Fully
reversible.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers
revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # discovery_runs — thesis inputs + generated universe
    # ------------------------------------------------------------------
    op.add_column(
        "discovery_runs",
        sa.Column(
            "mode", sa.String(20), nullable=False, server_default="ticker"
        ),
    )
    op.add_column(
        "discovery_runs", sa.Column("thesis_text", sa.Text(), nullable=True)
    )
    op.add_column(
        "discovery_runs", sa.Column("parsed_thesis_json", JSONB, nullable=True)
    )
    op.add_column(
        "discovery_runs", sa.Column("universe_json", JSONB, nullable=True)
    )
    op.create_index("ix_discovery_runs_mode", "discovery_runs", ["mode"])

    # ------------------------------------------------------------------
    # discovery_candidates — thesis relevance + combined internal score
    # ------------------------------------------------------------------
    op.add_column(
        "discovery_candidates",
        sa.Column("thesis_relevance_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "discovery_candidates",
        sa.Column("combined_internal_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "discovery_candidates",
        sa.Column("thesis_match_json", JSONB, nullable=True),
    )
    op.create_index(
        "ix_discovery_candidates_combined_score",
        "discovery_candidates",
        ["combined_internal_score"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_discovery_candidates_combined_score",
        table_name="discovery_candidates",
    )
    op.drop_column("discovery_candidates", "thesis_match_json")
    op.drop_column("discovery_candidates", "combined_internal_score")
    op.drop_column("discovery_candidates", "thesis_relevance_score")

    op.drop_index("ix_discovery_runs_mode", table_name="discovery_runs")
    op.drop_column("discovery_runs", "universe_json")
    op.drop_column("discovery_runs", "parsed_thesis_json")
    op.drop_column("discovery_runs", "thesis_text")
    op.drop_column("discovery_runs", "mode")
