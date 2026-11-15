"""Earnings results — V3.20 slice 4.1 (migration 050, spec §3.4).

See ``alembic/versions/050_add_earnings_results.py``. NULL is "not looked up", never 0.
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

ACTUALS_SEC_XBRL = "sec_xbrl"
#: ``consensus_status`` (4.2 / 4.4). NULL = no provider asked yet.
CONSENSUS_AVAILABLE = "available"
CONSENSUS_THIN = "thin"
CONSENSUS_NONE = "none"
CONSENSUS_SOURCE_UNAVAILABLE = "source_unavailable"
CONSENSUS_STATUSES: frozenset[str] = frozenset(
    {CONSENSUS_AVAILABLE, CONSENSUS_THIN, CONSENSUS_NONE, CONSENSUS_SOURCE_UNAVAILABLE})

_MONEY = sa.Numeric(24, 2)
_PER_SHARE = sa.Numeric(18, 6)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EarningsResult(Base):
    """One company × fiscal quarter: actuals, and later consensus / guidance / surprise."""

    __tablename__ = "earnings_results"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="CASCADE",
                      name="fk_earnings_results_company_id_companies"),
        nullable=False)
    #: ``2026-Q3`` — the filer's own fiscal year and quarter (ADR-091); ``2026`` for the
    #: full fiscal year (what annual guidance is compared with).
    fiscal_period_label: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    #: 1–4, or 0 for the full fiscal year.
    fiscal_quarter: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    period_start: Mapped[date | None] = mapped_column(sa.Date)
    period_end: Mapped[date] = mapped_column(sa.Date, nullable=False)
    report_date: Mapped[date | None] = mapped_column(sa.Date)
    #: ``8-K item 2.02`` or ``periodic report filing date``.
    report_date_source: Mapped[str | None] = mapped_column(sa.String(40))
    report_date_accession: Mapped[str | None] = mapped_column(sa.String(25))

    actuals_source: Mapped[str | None] = mapped_column(sa.String(30))
    #: As first reported in the quarter's own filing.
    actual_revenue: Mapped[Decimal | None] = mapped_column(_MONEY)
    #: The newest filing's value for the same dates (restated / re-presented).
    actual_revenue_latest: Mapped[Decimal | None] = mapped_column(_MONEY)
    revenue_currency: Mapped[str | None] = mapped_column(sa.String(3))
    #: ``reported_quarter`` | ``derived_fy_minus_9m``.
    revenue_basis: Mapped[str | None] = mapped_column(sa.String(30))
    revenue_concept: Mapped[str | None] = mapped_column(sa.String(120))
    revenue_accession: Mapped[str | None] = mapped_column(sa.String(25))
    revenue_source_url: Mapped[str | None] = mapped_column(sa.String(2000))
    actual_eps: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    actual_eps_latest: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    eps_currency: Mapped[str | None] = mapped_column(sa.String(3))
    eps_concept: Mapped[str | None] = mapped_column(sa.String(120))
    eps_accession: Mapped[str | None] = mapped_column(sa.String(25))
    eps_source_url: Mapped[str | None] = mapped_column(sa.String(2000))
    actuals_as_of: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    consensus_eps_avg: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    consensus_eps_high: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    consensus_eps_low: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    consensus_revenue_avg: Mapped[Decimal | None] = mapped_column(_MONEY)
    consensus_revenue_high: Mapped[Decimal | None] = mapped_column(_MONEY)
    consensus_revenue_low: Mapped[Decimal | None] = mapped_column(_MONEY)
    analyst_count_eps: Mapped[int | None] = mapped_column(sa.Integer)
    analyst_count_revenue: Mapped[int | None] = mapped_column(sa.Integer)
    consensus_source: Mapped[str | None] = mapped_column(sa.String(40))
    #: Must be BEFORE ``report_date``, else it is not an expectation (spec §3.4).
    consensus_as_of: Mapped[date | None] = mapped_column(sa.Date)
    consensus_status: Mapped[str | None] = mapped_column(sa.String(20))

    surprise_eps_abs: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    surprise_eps_pct: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4))
    surprise_revenue_abs: Mapped[Decimal | None] = mapped_column(_MONEY)
    surprise_revenue_pct: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4))

    guidance_metric: Mapped[str | None] = mapped_column(sa.String(30))
    guidance_low: Mapped[Decimal | None] = mapped_column(_MONEY)
    guidance_high: Mapped[Decimal | None] = mapped_column(_MONEY)
    guidance_mid: Mapped[Decimal | None] = mapped_column(_MONEY)
    guidance_evidence_ref: Mapped[str | None] = mapped_column(sa.String(80))
    vs_guidance: Mapped[str | None] = mapped_column(sa.String(20))

    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now(), onupdate=_now)

    __table_args__ = (
        sa.UniqueConstraint("company_id", "fiscal_period_label",
                            name="uq_earnings_results_company_period"),
        sa.Index("ix_earnings_results_report_date", "report_date"),
        sa.CheckConstraint("fiscal_quarter BETWEEN 0 AND 4",
                           name="ck_earnings_results_fiscal_quarter"),
    )


__all__ = [
    "ACTUALS_SEC_XBRL",
    "CONSENSUS_AVAILABLE",
    "CONSENSUS_NONE",
    "CONSENSUS_SOURCE_UNAVAILABLE",
    "CONSENSUS_STATUSES",
    "CONSENSUS_THIN",
    "EarningsResult",
]
