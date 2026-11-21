"""Consensus snapshots + vendor call budget — V3.20 slice 4.2 (migration 051, ADR-097).

See ``alembic/versions/051_add_consensus_snapshots.py``.
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

SOURCE_ALPHA_VANTAGE = "alpha_vantage"
HORIZON_QUARTER = "fiscal_quarter"
HORIZON_YEAR = "fiscal_year"
#: The provider's own record of a reported quarter (EARNINGS): its reported EPS, its
#: estimate and its surprise, on the provider's EPS basis.
HORIZON_REPORTED = "reported_quarter"

_MONEY = sa.Numeric(24, 2)
_PER_SHARE = sa.Numeric(18, 6)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConsensusSnapshot(Base):
    """A third party's expectation for one company period, on one date."""

    __tablename__ = "consensus_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="CASCADE",
                      name="fk_consensus_snapshots_company_id_companies"),
        nullable=False)
    source: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    horizon: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    period_end: Mapped[date] = mapped_column(sa.Date, nullable=False)
    as_of: Mapped[date] = mapped_column(sa.Date, nullable=False)
    #: True only when ``as_of`` precedes the period's announcement (ADR-097 §2). None =
    #: the announcement date is not known yet.
    is_pre_report: Mapped[bool | None] = mapped_column(sa.Boolean)
    eps_avg: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    eps_high: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    eps_low: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    eps_analyst_count: Mapped[int | None] = mapped_column(sa.Integer)
    revenue_avg: Mapped[Decimal | None] = mapped_column(_MONEY)
    revenue_high: Mapped[Decimal | None] = mapped_column(_MONEY)
    revenue_low: Mapped[Decimal | None] = mapped_column(_MONEY)
    revenue_analyst_count: Mapped[int | None] = mapped_column(sa.Integer)
    eps_avg_7d_ago: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    eps_avg_30d_ago: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    eps_avg_60d_ago: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    eps_avg_90d_ago: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    eps_revisions_up_30d: Mapped[int | None] = mapped_column(sa.Integer)
    eps_revisions_down_30d: Mapped[int | None] = mapped_column(sa.Integer)
    vendor_reported_date: Mapped[date | None] = mapped_column(sa.Date)
    vendor_reported_eps: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    vendor_surprise: Mapped[Decimal | None] = mapped_column(_PER_SHARE)
    vendor_surprise_pct: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4))
    vendor_report_time: Mapped[str | None] = mapped_column(sa.String(20))
    #: The request URL WITHOUT the key.
    source_url: Mapped[str | None] = mapped_column(sa.String(500))
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now(), onupdate=_now)

    __table_args__ = (
        sa.UniqueConstraint("company_id", "source", "horizon", "period_end", "as_of",
                            name="uq_consensus_snapshots_key"),
        sa.Index("ix_consensus_snapshots_company_period", "company_id", "period_end"),
    )


class VendorApiCall(Base):
    """Calls to one vendor on one UTC day — the free-tier budget."""

    __tablename__ = "vendor_api_calls"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    vendor: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    day: Mapped[date] = mapped_column(sa.Date, nullable=False)
    calls: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0,
                                       server_default="0")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now(), onupdate=_now)

    __table_args__ = (sa.UniqueConstraint("vendor", "day", name="uq_vendor_api_calls_vendor_day"),)


__all__ = [
    "HORIZON_QUARTER",
    "HORIZON_REPORTED",
    "HORIZON_YEAR",
    "SOURCE_ALPHA_VANTAGE",
    "ConsensusSnapshot",
    "VendorApiCall",
]
