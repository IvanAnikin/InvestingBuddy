"""Grid capex plans — V3.20 slice 7.1 (spec §3.7, demand-side triangulation).

``grid_capex_plans``: one row per project (or investment within a project) in a
published grid plan — promoter(s), plan name and edition, project, technology tags,
capex amount + currency when the plan states one, period, commissioning, status — and
the source (URL, retrieval time, the source's use constraint and attribution).

``grid_plan_ingests``: one row per ingest attempt — the file's SHA-256, counts, and the
failure reason when the layout was not the one the parser was written against. A failed
ingest is kept (learning data); rows from it are never written.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

INGEST_RUNNING = "running"
INGEST_COMPLETE = "complete"
INGEST_FAILED = "failed"
INGEST_STATUSES = (INGEST_RUNNING, INGEST_COMPLETE, INGEST_FAILED)

STATUS_CLASSES = ("under_consideration", "planned", "permitting", "committed",
                  "under_construction", "commissioned", "withdrawn", "other")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GridPlanIngest(Base):
    __tablename__ = "grid_plan_ingests"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    source_key: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    plan_edition: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    source_url: Mapped[str] = mapped_column(sa.String(2000), nullable=False)
    file_sha256: Mapped[str | None] = mapped_column(sa.String(64))
    rows_read: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0,
                                           server_default="0")
    rows_written: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0,
                                              server_default="0")
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(sa.String(500))
    retrieved_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True),
                                                   nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now())

    __table_args__ = (
        sa.CheckConstraint("status IN ('running', 'complete', 'failed')",
                           name="ck_grid_plan_ingests_status"),
    )


class GridCapexPlan(Base):
    __tablename__ = "grid_capex_plans"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    ingest_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("grid_plan_ingests.id", ondelete="SET NULL",
                      name="fk_grid_capex_plans_ingest_id"))
    source_key: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    plan_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    plan_edition: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    #: ``project`` | ``investment`` (a component of a project) | ``queue_summary`` (7.2).
    record_kind: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    external_id: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    parent_external_id: Mapped[str | None] = mapped_column(sa.String(80))
    project_name: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    promoters_text: Mapped[str | None] = mapped_column(sa.String(1000))
    promoters_json: Mapped[list[str] | None] = mapped_column(JSONB)
    region: Mapped[str | None] = mapped_column(sa.String(200))
    category: Mapped[str | None] = mapped_column(sa.String(120))
    technology_tags_json: Mapped[list[str] | None] = mapped_column(JSONB)
    voltage_kv: Mapped[int | None] = mapped_column(sa.Integer)
    description: Mapped[str | None] = mapped_column(sa.Text)
    capex_amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(24, 2))
    capex_currency: Mapped[str | None] = mapped_column(sa.String(3))
    capex_period: Mapped[str | None] = mapped_column(sa.String(60))
    capacity_text: Mapped[str | None] = mapped_column(sa.String(500))
    capacity_mw: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 2))
    commissioning_text: Mapped[str | None] = mapped_column(sa.String(120))
    commissioning_year: Mapped[int | None] = mapped_column(sa.Integer)
    status_text: Mapped[str | None] = mapped_column(sa.String(200))
    status_class: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    source_url: Mapped[str] = mapped_column(sa.String(2000), nullable=False)
    source_title: Mapped[str | None] = mapped_column(sa.String(500))
    retrieved_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True),
                                                   nullable=False)
    use_constraint: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    attribution: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now(), onupdate=_now)

    __table_args__ = (
        sa.UniqueConstraint("source_key", "plan_edition", "record_kind", "external_id",
                            name="uq_grid_capex_plans_record"),
        sa.CheckConstraint(
            "(capex_amount IS NULL AND capex_currency IS NULL) OR "
            "(capex_amount > 0 AND capex_currency IS NOT NULL)",
            name="ck_grid_capex_plans_capex_pair"),
        sa.CheckConstraint(
            "status_class IN ('under_consideration', 'planned', 'permitting', 'committed', "
            "'under_construction', 'commissioned', 'withdrawn', 'other')",
            name="ck_grid_capex_plans_status_class"),
        sa.Index("ix_grid_capex_plans_source_edition", "source_key", "plan_edition"),
    )


__all__ = ["INGEST_COMPLETE", "INGEST_FAILED", "INGEST_RUNNING", "INGEST_STATUSES",
           "STATUS_CLASSES", "GridCapexPlan", "GridPlanIngest"]
