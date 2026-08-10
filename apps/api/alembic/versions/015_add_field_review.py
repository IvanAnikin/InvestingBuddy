"""add field_review_runs + field_review_candidate_summaries

Revision ID: 015
Revises: 014
Create Date: 2026-08-10

Phase 32A Slice 6D — Deep Field Review.

Creates:
  field_review_runs
  field_review_candidate_summaries

A Deep Field Review is a COMPARATIVE council that runs AFTER two or more
candidates from the SAME discovery run already have a COMPLETED full analysis.
It compares those already-persisted reports and produces an internal
RESEARCH-PRIORITY shortlist. It is distinct from the discovery council (which
triages a candidate LIST before any analysis exists) and from the single-company
council (which analyses ONE company).

Deliberately bounded and honest:
  * every candidate considered gets a row — INCLUDING excluded ones, with a
    closed-vocabulary ``exclusion_reason`` (rejected cases are learning data);
  * ``priority_tier`` is an internal research bucket only — no BUY/SELL/HOLD/
    WATCH label, no price target, fair value, or return projection is stored;
  * ``error`` holds a short, safe reason code — never a raw exception string;
  * ``human_review_required`` defaults TRUE and nothing here is publishable.

Reversible. No data backfill.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers
revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "field_review_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("discovery_run_id", sa.Uuid(as_uuid=True), nullable=False),
        # pending | running | completed | completed_with_warnings | failed |
        # insufficient_candidates (see app/models/field_review.py).
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column(
            "included_candidate_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "missing_candidate_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "llm_used", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("council_version", sa.String(20), nullable=True),
        sa.Column("provider", sa.String(50), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column(
            "agents_completed", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("agents_failed", sa.Integer(), nullable=False, server_default="0"),
        # strong | adequate | thin | failed — an internal field-quality label,
        # never a rating.
        sa.Column("field_quality", sa.String(20), nullable=True),
        sa.Column("safety_valid", sa.Boolean(), nullable=True),
        sa.Column(
            "review_json",
            JSONB(),
            nullable=True,
        ),
        sa.Column(
            "warnings_json",
            JSONB(),
            nullable=True,
        ),
        # Short, safe reason code only.
        sa.Column("error", sa.String(200), nullable=True),
        sa.Column(
            "human_review_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["discovery_run_id"],
            ["discovery_runs.id"],
            name="fk_field_review_runs_discovery_run_id_discovery_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_field_review_runs_discovery_run_id",
        "field_review_runs",
        ["discovery_run_id"],
    )
    op.create_index("ix_field_review_runs_status", "field_review_runs", ["status"])
    op.create_index(
        "ix_field_review_runs_created_at", "field_review_runs", ["created_at"]
    )

    op.create_table(
        "field_review_candidate_summaries",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("field_review_run_id", sa.Uuid(as_uuid=True), nullable=False),
        # SET NULL on both links: research history survives a candidate/report
        # deletion (never CASCADE away the record of what was compared).
        sa.Column("discovery_candidate_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("report_id", sa.Uuid(as_uuid=True), nullable=True),
        # "F1", "F2", … — the citation id the council used for this company.
        sa.Column("citation_ref", sa.String(20), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=True),
        sa.Column("exchange", sa.String(20), nullable=True),
        sa.Column(
            "included", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        # Closed vocabulary (see app/models/field_review.py).
        sa.Column("exclusion_reason", sa.String(50), nullable=True),
        sa.Column("data_provenance", sa.String(20), nullable=True),
        # strongest_candidates | second_tier | blocked_insufficient_evidence.
        sa.Column("priority_tier", sa.String(50), nullable=True),
        sa.Column(
            "summary_json",
            JSONB(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["field_review_run_id"],
            ["field_review_runs.id"],
            name="fk_field_review_candidate_summaries_run_id_field_review_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["discovery_candidate_id"],
            ["discovery_candidates.id"],
            # NOTE: shortened ("candidate_summaries" -> "summaries"). The fully
            # symmetrical name would be 69 chars and PostgreSQL rejects any
            # identifier over 63. The model declares the SAME name.
            name="fk_field_review_summaries_candidate_id_discovery_candidates",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name="fk_field_review_candidate_summaries_report_id_reports",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "field_review_run_id",
            "citation_ref",
            name="uq_field_review_candidate_summary_run_ref",
        ),
    )
    op.create_index(
        "ix_field_review_candidate_summaries_run_id",
        "field_review_candidate_summaries",
        ["field_review_run_id"],
    )
    op.create_index(
        "ix_field_review_candidate_summaries_candidate_id",
        "field_review_candidate_summaries",
        ["discovery_candidate_id"],
    )
    op.create_index(
        "ix_field_review_candidate_summaries_report_id",
        "field_review_candidate_summaries",
        ["report_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_field_review_candidate_summaries_report_id",
        table_name="field_review_candidate_summaries",
    )
    op.drop_index(
        "ix_field_review_candidate_summaries_candidate_id",
        table_name="field_review_candidate_summaries",
    )
    op.drop_index(
        "ix_field_review_candidate_summaries_run_id",
        table_name="field_review_candidate_summaries",
    )
    op.drop_table("field_review_candidate_summaries")

    op.drop_index("ix_field_review_runs_created_at", table_name="field_review_runs")
    op.drop_index("ix_field_review_runs_status", table_name="field_review_runs")
    op.drop_index(
        "ix_field_review_runs_discovery_run_id", table_name="field_review_runs"
    )
    op.drop_table("field_review_runs")
