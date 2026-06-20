"""add initial tables

Revision ID: 001
Revises:
Create Date: 2026-06-16

Creates: companies, agent_runs, agent_steps, reports
Order: agent_runs first (reports has FK to agent_runs)
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("sector", sa.String(100), nullable=True),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column("market_cap", sa.Numeric(20, 2), nullable=True),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("website", sa.String(500), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="new"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", "exchange", name="uq_companies_ticker_exchange"),
    )
    op.create_index("ix_companies_ticker", "companies", ["ticker"])
    op.create_index("ix_companies_exchange", "companies", ["exchange"])
    op.create_index("ix_companies_status", "companies", ["status"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_name", sa.String(100), nullable=False),
        sa.Column(
            "workflow_version", sa.String(50), nullable=False, server_default="1.0.0"
        ),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trigger_type", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("total_tokens", sa.Integer, nullable=True),
        sa.Column("total_cost", sa.Numeric(10, 6), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_workflow_name", "agent_runs", ["workflow_name"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])

    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("step_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column("input_json", sa.JSON, nullable=True),
        sa.Column("output_json", sa.JSON, nullable=True),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("tokens_used", sa.Integer, nullable=True),
        sa.Column("cost", sa.Numeric(10, 6), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name="fk_agent_steps_agent_run_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_steps_agent_run_id", "agent_steps", ["agent_run_id"])

    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("slug", sa.String(500), nullable=False),
        sa.Column("report_type", sa.String(50), nullable=False),
        sa.Column("period_start", sa.Date, nullable=True),
        sa.Column("period_end", sa.Date, nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("content_markdown", sa.Text, nullable=True),
        sa.Column("content_html", sa.Text, nullable=True),
        sa.Column("created_by_agent_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_agent_run_id"],
            ["agent_runs.id"],
            name="fk_reports_created_by_agent_run_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_reports_slug"),
    )
    op.create_index("ix_reports_slug", "reports", ["slug"])
    op.create_index("ix_reports_status", "reports", ["status"])
    op.create_index("ix_reports_report_type", "reports", ["report_type"])
    op.create_index("ix_reports_published_at", "reports", ["published_at"])


def downgrade() -> None:
    op.drop_table("reports")
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
    op.drop_table("companies")
