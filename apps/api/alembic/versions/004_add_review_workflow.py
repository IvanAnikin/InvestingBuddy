"""add review workflow fields and report_review_events table

Revision ID: 004
Revises: 003
Create Date: 2026-06-25

Phase 11 — Admin Review / Approve-Reject Workflow.

Changes to `reports` table:
  review_status           — current human review status (draft / under_review /
                            approved_internal / rejected_internal / needs_revision / archived)
  reviewed_at             — timestamp of the most recent review action
  reviewer_note           — free-text note left by the reviewer
  review_decision_reason  — structured reason for approval or rejection
  human_review_required   — flag set by the Analysis Council committee chair;
                            defaults true so every report requires explicit review
  approved_by             — label for who approved (placeholder string; no user FK yet)
  rejected_by             — label for who rejected (placeholder string; no user FK yet)

New `report_review_events` table:
  immutable audit log — one row per review action taken by a human admin.
  Never deleted. Soft-deletes not applicable.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Add review workflow columns to `reports`
    # ------------------------------------------------------------------
    op.add_column(
        "reports",
        sa.Column(
            "review_status",
            sa.String(50),
            nullable=False,
            server_default="draft",
        ),
    )
    op.add_column(
        "reports",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "reports",
        sa.Column("reviewer_note", sa.Text, nullable=True),
    )
    op.add_column(
        "reports",
        sa.Column("review_decision_reason", sa.Text, nullable=True),
    )
    op.add_column(
        "reports",
        sa.Column(
            "human_review_required",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "reports",
        sa.Column("approved_by", sa.String(200), nullable=True),
    )
    op.add_column(
        "reports",
        sa.Column("rejected_by", sa.String(200), nullable=True),
    )
    op.create_index("ix_reports_review_status", "reports", ["review_status"])

    # ------------------------------------------------------------------
    # 2. Create `report_review_events` audit table
    # ------------------------------------------------------------------
    op.create_table(
        "report_review_events",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("report_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("from_status", sa.String(50), nullable=True),
        sa.Column("to_status", sa.String(50), nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("actor_label", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_review_events_report_id", "report_review_events", ["report_id"]
    )
    op.create_index(
        "ix_review_events_action", "report_review_events", ["action"]
    )


def downgrade() -> None:
    op.drop_index("ix_review_events_action", table_name="report_review_events")
    op.drop_index("ix_review_events_report_id", table_name="report_review_events")
    op.drop_table("report_review_events")

    op.drop_index("ix_reports_review_status", table_name="reports")
    op.drop_column("reports", "rejected_by")
    op.drop_column("reports", "approved_by")
    op.drop_column("reports", "human_review_required")
    op.drop_column("reports", "review_decision_reason")
    op.drop_column("reports", "reviewer_note")
    op.drop_column("reports", "reviewed_at")
    op.drop_column("reports", "review_status")
