"""What one research run consumed — V3.0 Slice 5.

One row per completed research run, holding vendor-neutral units and the cost
DERIVED from them. Separate from ``research_jobs`` on purpose: the job row is
contended for by workers and rewritten on every heartbeat, while this is written
exactly once and then only read. Putting an analytical record on a row with lease
churn would make both worse.

It is also deliberately not on ``reports``. A run that produced no report still
consumed a budget, and losing exactly those measurements would bias every
average taken from this table towards the runs that went well.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchRunConsumption(Base):
    __tablename__ = "research_run_consumption"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # What kind of run this was: 'company_research' | 'discovery_run' | …
    run_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)

    # The research mode, once modes exist (V3.5). Nullable because today every
    # run is the one mode there is, and writing a mode name that means nothing
    # yet would make it look like a choice was made.
    research_mode: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)

    # Lineage. SET NULL throughout — research history is preserved, never
    # CASCADEd away (CLAUDE.md rule 15). All nullable: a run that failed before
    # producing a report still consumed a budget, and dropping those rows would
    # bias every average taken from this table.
    research_job_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_jobs.id",
            ondelete="SET NULL",
            name="fk_research_run_consumption_job_id_research_jobs",
        ),
        nullable=True,
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "agent_runs.id",
            ondelete="SET NULL",
            name="fk_research_run_consumption_agent_run_id_agent_runs",
        ),
        nullable=True,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id",
            ondelete="SET NULL",
            name="fk_research_run_consumption_company_id_companies",
        ),
        nullable=True,
    )
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "reports.id",
            ondelete="SET NULL",
            name="fk_research_run_consumption_report_id_reports",
        ),
        nullable=True,
    )

    # The whole record: units, derived cost, budget in force. Kept as one
    # document rather than a column per unit because the unit list is expected
    # to grow with each provider slice, and a migration per unit would make
    # adding one expensive enough to discourage measuring it.
    consumption_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Denormalised for querying, because these are what an operator filters and
    # sorts by and a JSON predicate renders differently per dialect.
    model_calls: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    model_tokens: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    elapsed_seconds: Mapped[float] = mapped_column(
        sa.Float, nullable=False, default=0.0, server_default="0"
    )
    # NULL means "no price book was configured", never "free". The two are
    # different statements and only one of them is ever true.
    estimated_cost_usd: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    # Whether a budget stopped this run, and which limit did it.
    budget_limit_hit: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)

    outcome: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        # "What has this cost lately" and "what did this company's runs cost".
        sa.Index("ix_research_run_consumption_created_at", "created_at"),
        sa.Index("ix_research_run_consumption_company_id", "company_id"),
        sa.Index("ix_research_run_consumption_run_type", "run_type"),
    )
