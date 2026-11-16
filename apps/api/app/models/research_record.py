"""Comparable-set store — V3.20 slice 9.1 (migration 053, spec §3.10).

See ``alembic/versions/053_add_company_research_records.py`` and
``services/research_records.py``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CompanyResearchRecord(Base):
    """One structured record per completed research run: the comparable set.

    Read from the run's own report blocks (never re-derived), so the memo, the report and
    this record cannot disagree. Rows are never updated after the run and never deleted.
    """

    __tablename__ = "company_research_records"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="CASCADE",
                      name="fk_company_research_records_company_id_companies"),
        nullable=False)
    research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("research_runs.id", ondelete="SET NULL",
                      name="fk_company_research_records_research_run_id_research_runs"),
        nullable=True)
    as_of: Mapped[date] = mapped_column(sa.Date, nullable=False)
    record_version: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    ticker: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    exchange: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    sector: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    industry: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    #: Electricity taxonomy primary layer (slice 5.2); NULL outside the vertical.
    layer: Mapped[str | None] = mapped_column(sa.String(60), nullable=True)
    market_cap_usd: Mapped[Decimal | None] = mapped_column(sa.Numeric(24, 2), nullable=True)
    market_cap_as_of: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    enterprise_value: Mapped[Decimal | None] = mapped_column(sa.Numeric(24, 2), nullable=True)
    ev_currency: Mapped[str | None] = mapped_column(sa.String(3), nullable=True)
    ev_to_ebit: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2), nullable=True)
    obscurity_score: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    #: Slice 9.2 fills it; NULL until then.
    rubric_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    #: The record itself: every block it was read from, verbatim.
    record_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("research_run_id",
                            name="uq_company_research_records_research_run_id"),
        sa.Index("ix_company_research_records_company_as_of", "company_id", "as_of"),
        sa.Index("ix_company_research_records_layer", "layer"),
    )
