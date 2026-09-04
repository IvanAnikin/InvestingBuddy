"""Add the durable research-job table — V3.0 Slice 1.

PURPOSE
=======
One row per long-running research job, committed before any expensive work
starts and owned by exactly one worker at a time through a lease.

This is ADDITIVE ONLY. It creates a new table and touches nothing that exists,
so code on ``release/v2-current`` runs unchanged against a database that has had
this migration applied — which is what makes the V3 rollback plan real rather
than theoretical.

Nothing writes to this table yet. Slice 2 adds the worker loop and Slice 3 routes
``/company-research/jobs`` through it behind ``V3_DURABLE_JOBS_ENABLED``.

INDEXES, AND WHY EACH ONE EXISTS
================================
* ``uq_research_jobs_idempotency_key`` — idempotency is enforced by the DATABASE.
  A read-then-write check in application code loses to a concurrent duplicate
  submit, which is precisely the case it exists to prevent.
* ``ix_research_jobs_status_available_at`` — the worker's claim query
  (``WHERE status IN (...) AND available_at <= now ORDER BY available_at``). The
  hot path of the whole worker loop.
* ``ix_research_jobs_lease_expires_at`` — the reclaim scan for lapsed leases.
* ``ix_research_jobs_job_type`` / ``ix_research_jobs_company_id`` — operator and
  per-company lookups.

NOT STORED: ``interrupted``. It is derived at read time from an expired lease
(``job_contract.derive_status``). A stored ``interrupted`` would need a writer
that is running, which is exactly what is absent in the case it describes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision: str = "019"
down_revision: str | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("payload_json", postgresql.JSONB(), nullable=True),
        sa.Column("result_type", sa.String(length=50), nullable=True),
        sa.Column("result_ref", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("stage", sa.String(length=50), nullable=True),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("error_class", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("dead_letter_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # SET NULL, never CASCADE: deleting a company must not delete the record
        # that research was performed for it (CLAUDE.md rule 15).
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_research_jobs_company_id_companies",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name="fk_research_jobs_agent_run_id_agent_runs",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_research_jobs_idempotency_key"
        ),
    )
    op.create_index(
        "ix_research_jobs_status_available_at",
        "research_jobs",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_research_jobs_lease_expires_at", "research_jobs", ["lease_expires_at"]
    )
    op.create_index("ix_research_jobs_job_type", "research_jobs", ["job_type"])
    op.create_index("ix_research_jobs_company_id", "research_jobs", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_research_jobs_company_id", table_name="research_jobs")
    op.drop_index("ix_research_jobs_job_type", table_name="research_jobs")
    op.drop_index("ix_research_jobs_lease_expires_at", table_name="research_jobs")
    op.drop_index(
        "ix_research_jobs_status_available_at", table_name="research_jobs"
    )
    op.drop_table("research_jobs")
