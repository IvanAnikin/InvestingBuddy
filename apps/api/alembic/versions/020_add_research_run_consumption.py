"""Add the run-consumption table — V3.0 Slice 5.

PURPOSE
=======
One row per research run recording what it consumed, in vendor-neutral units,
with cost derived rather than stored as a vendor's price.

Two open decisions depend on these measurements existing. OPEN DECISION #14
(research-mode budgets) says the numbers should be "derived from measured live
runs rather than guessing" — nothing measures them today. OPEN DECISION #13
(cost thresholds) needs the same input. And the provider benchmark's central
metric, ``cost_per_verified_finding``, cannot be computed until the numerator is
recorded per run.

ADDITIVE ONLY. Creates one table and touches nothing that exists, so code on
``release/v2-current`` runs unchanged against a database with this applied.

Nothing writes to it unless ``V3_RUN_CONSUMPTION_ENABLED`` is turned on, which is
off by default — so this migration and the flag are safe to land separately, in
either order.

INDEXES, AND WHY EACH ONE EXISTS
================================
* ``ix_research_run_consumption_created_at`` — "what has research cost lately",
  which is the only question anyone asks of this table before a benchmark exists.
* ``ix_research_run_consumption_company_id`` — per-issuer cost, needed to compare
  a cheap run against an expensive one on the SAME company rather than across a
  mixed set.
* ``ix_research_run_consumption_run_type`` — company research and a discovery run
  have different cost shapes and averaging them together is meaningless.

NULLABILITY IS A STATEMENT
==========================
``estimated_cost_usd`` is nullable and NULL means "no price book was configured",
never "free". Every FK is nullable and ``SET NULL``: a run that failed before
producing a report still consumed a budget, and dropping exactly those rows would
bias every average taken from this table towards the runs that went well.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision: str = "020"
down_revision: str | None = "019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_run_consumption",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("run_type", sa.String(length=50), nullable=False),
        sa.Column("research_mode", sa.String(length=20), nullable=True),
        sa.Column("research_job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("report_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("consumption_json", postgresql.JSONB(), nullable=True),
        sa.Column("model_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "elapsed_seconds", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("budget_limit_hit", sa.String(length=50), nullable=True),
        sa.Column("outcome", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["research_job_id"],
            ["research_jobs.id"],
            name="fk_research_run_consumption_job_id_research_jobs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name="fk_research_run_consumption_agent_run_id_agent_runs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_research_run_consumption_company_id_companies",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name="fk_research_run_consumption_report_id_reports",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_research_run_consumption_created_at",
        "research_run_consumption",
        ["created_at"],
    )
    op.create_index(
        "ix_research_run_consumption_company_id",
        "research_run_consumption",
        ["company_id"],
    )
    op.create_index(
        "ix_research_run_consumption_run_type",
        "research_run_consumption",
        ["run_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_run_consumption_run_type", table_name="research_run_consumption"
    )
    op.drop_index(
        "ix_research_run_consumption_company_id", table_name="research_run_consumption"
    )
    op.drop_index(
        "ix_research_run_consumption_created_at", table_name="research_run_consumption"
    )
    op.drop_table("research_run_consumption")
