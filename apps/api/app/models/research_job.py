"""The durable research-job record — V3.0 Slice 1.

One row per long-running research job, committed BEFORE any expensive work
starts and owned by exactly one worker at a time through a lease. The rules that
govern it live in ``app.services.jobs.job_contract``; this module is only the
shape.

Why a new table rather than more columns on ``agent_runs``: an ``AgentRun`` is a
record of a workflow that ran — it is written by the thing doing the work and
read afterwards for audit. A job is the opposite: it is written before anything
runs, it is contended for by workers, and it is updated on a schedule (heartbeat)
by whoever holds it. Those are different access patterns with different indexes,
and overloading one row with both would put lease churn on the audit trail. The
two are linked by ``agent_run_id`` instead.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchJob(Base):
    __tablename__ = "research_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # What kind of work this is: 'company_research' | 'discovery_run' |
    # 'field_review' | 'candidate_analysis'. A plain string rather than an enum
    # because adding a job type must not require a migration.
    job_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)

    # Dedup identity. A second submit for the same logical work JOINS the
    # existing job instead of starting a second expensive run — generalising the
    # per-company in-flight check the V2 path already does. UNIQUE, so the
    # database enforces it under concurrency rather than a read-then-write race
    # in application code.
    idempotency_key: Mapped[str] = mapped_column(sa.String(200), nullable=False)

    # V2's vocabulary, unchanged, plus 'dead_letter' and 'cancelled'.
    # 'interrupted' is NEVER stored here — it is derived at read time from an
    # expired lease. See job_contract.derive_status.
    status: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="pending", server_default="pending"
    )

    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # What the job produced. Deliberately NOT a typed FK: a job may yield a
    # report, a discovery run or a field review, and one nullable FK per result
    # type would add columns every time a job type is added. The pair
    # (result_type, result_ref) is honest about being a soft reference.
    result_type: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    result_ref: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), nullable=True
    )

    # Lineage. SET NULL preserves research history on deletion (CLAUDE.md #15).
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id",
            ondelete="SET NULL",
            name="fk_research_jobs_company_id_companies",
        ),
        nullable=True,
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "agent_runs.id",
            ondelete="SET NULL",
            name="fk_research_jobs_agent_run_id_agent_runs",
        ),
        nullable=True,
    )

    # Retry accounting. ``attempt`` increments on every claim, including a
    # reclaim after a worker died — an attempt that was started and lost is an
    # attempt spent, or a job that reliably kills its worker would retry forever.
    attempt: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=3, server_default="3"
    )

    # Leasing. ``lease_owner`` identifies the worker instance; only the holder
    # may heartbeat, which is what makes reclaim safe.
    lease_owner: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    # Scheduling. Retry backoff is expressed by pushing this into the future;
    # the claim query filters on it, so a backoff is a real delay rather than a
    # hot loop.
    available_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=sa.func.now(),
    )

    # Reader-facing progress, using research_job's existing stage vocabulary so
    # the UI contract does not fork.
    stage: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)

    # Cooperative cancellation, observed at task boundaries — never a kill.
    cancel_requested: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )

    error_class: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    dead_letter_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        # Idempotency is a DATABASE constraint, not an application check.
        sa.UniqueConstraint(
            "idempotency_key", name="uq_research_jobs_idempotency_key"
        ),
        # The claim query: WHERE status IN (...) AND available_at <= now
        # ORDER BY available_at. This is the hot path of the worker loop.
        sa.Index("ix_research_jobs_status_available_at", "status", "available_at"),
        # The reclaim scan: expired leases on running jobs.
        sa.Index("ix_research_jobs_lease_expires_at", "lease_expires_at"),
        sa.Index("ix_research_jobs_job_type", "job_type"),
        sa.Index("ix_research_jobs_company_id", "company_id"),
    )
