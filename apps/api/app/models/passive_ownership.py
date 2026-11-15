"""Passive / ETF ownership — V3.20 slice 3.1 (migration 049, spec §3.3).

See ``alembic/versions/049_add_passive_ownership.py`` for why three tables and why
``Fund.is_etf`` is three-valued. Source facts: ``docs/v3.20-passive-ownership-spike.md``.
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

#: ``security_passive_ownership.source_kind`` — SEC Form N-PORT data sets (US-registered
#: funds), or an issuer's own holdings file (D8, slice 3.5).
SOURCE_NPORT = "nport"
SOURCE_ISSUER_FILE = "issuer_file"
SOURCE_KINDS: frozenset[str] = frozenset({SOURCE_NPORT, SOURCE_ISSUER_FILE})
#: ``identifier_kind``.
IDENTIFIER_KINDS: frozenset[str] = frozenset({"isin", "cusip", "lei"})
#: ``fund_holdings_ingests.status``.
INGEST_RUNNING = "running"
INGEST_COMPLETE = "complete"
INGEST_FAILED = "failed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Fund(Base):
    """One EDGAR series. ``is_etf`` / ``is_index``: True, False, or None = not known."""

    __tablename__ = "funds"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    series_id: Mapped[str] = mapped_column(sa.String(12), nullable=False)
    series_lei: Mapped[str | None] = mapped_column(sa.String(20))
    name: Mapped[str | None] = mapped_column(sa.String(300))
    registrant_cik: Mapped[str | None] = mapped_column(sa.String(10))
    registrant_name: Mapped[str | None] = mapped_column(sa.String(300))
    is_etf: Mapped[bool | None] = mapped_column(sa.Boolean)
    is_index: Mapped[bool | None] = mapped_column(sa.Boolean)
    #: Where the type came from (``ncen``) and the filing date it is as of.
    fund_type_source: Mapped[str | None] = mapped_column(sa.String(40))
    fund_type_as_of: Mapped[date | None] = mapped_column(sa.Date)
    net_assets: Mapped[Decimal | None] = mapped_column(sa.Numeric(24, 2))
    net_assets_as_of: Mapped[date | None] = mapped_column(sa.Date)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now(), onupdate=_now)

    __table_args__ = (sa.UniqueConstraint("series_id", name="uq_funds_series_id"),)


class SecurityPassiveOwnership(Base):
    """What US-registered funds (or one issuer file family) held of one security, in one
    data-set period. Aggregates only; raw holdings are not kept."""

    __tablename__ = "security_passive_ownership"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    identifier_kind: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    identifier: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    #: The data set's period, e.g. ``2026Q3`` (an N-PORT FILING quarter).
    period: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    source_kind: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    issuer_name: Mapped[str | None] = mapped_column(sa.String(300))
    issuer_lei: Mapped[str | None] = mapped_column(sa.String(20))
    cusip: Mapped[str | None] = mapped_column(sa.String(9))
    isin: Mapped[str | None] = mapped_column(sa.String(12))
    fund_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0,
                                            server_default="0")
    etf_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0,
                                           server_default="0")
    etf_unknown_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0,
                                                   server_default="0")
    index_fund_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0,
                                                  server_default="0")
    #: Sum of ``BALANCE`` where ``UNIT = 'NS'`` (number of shares) only.
    shares_held: Mapped[Decimal | None] = mapped_column(sa.Numeric(28, 4))
    etf_shares_held: Mapped[Decimal | None] = mapped_column(sa.Numeric(28, 4))
    value_usd: Mapped[Decimal | None] = mapped_column(sa.Numeric(24, 2))
    #: Holdings in another unit (principal amount, contracts): counted, never added.
    non_share_holdings: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0,
                                                    server_default="0")
    report_date_min: Mapped[date | None] = mapped_column(sa.Date)
    report_date_max: Mapped[date | None] = mapped_column(sa.Date)
    #: ``[{series_id, name, is_etf, shares, value_usd, pct_of_fund, report_date}]`` ≤ 20.
    top_funds_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    source_url: Mapped[str | None] = mapped_column(sa.String(2000))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now(), onupdate=_now)

    __table_args__ = (
        sa.UniqueConstraint("identifier_kind", "identifier", "period", "source_kind",
                            name="uq_security_passive_ownership_key"),
        sa.Index("ix_security_passive_ownership_isin", "isin"),
        sa.Index("ix_security_passive_ownership_cusip", "cusip"),
        sa.Index("ix_security_passive_ownership_issuer_lei", "issuer_lei"),
    )


class FundHoldingsIngest(Base):
    """One ingest of one period from one source: idempotency and the audit trail."""

    __tablename__ = "fund_holdings_ingests"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    source_kind: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    period: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    source_url: Mapped[str | None] = mapped_column(sa.String(2000))
    funds_written: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0,
                                               server_default="0")
    securities_written: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0,
                                                    server_default="0")
    holdings_read: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0,
                                               server_default="0")
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(sa.String(500))
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 server_default=sa.func.now(), onupdate=_now)

    __table_args__ = (
        sa.UniqueConstraint("source_kind", "period", name="uq_fund_holdings_ingests_period"),
    )


__all__ = [
    "Fund",
    "FundHoldingsIngest",
    "IDENTIFIER_KINDS",
    "INGEST_COMPLETE",
    "INGEST_FAILED",
    "INGEST_RUNNING",
    "SOURCE_ISSUER_FILE",
    "SOURCE_KINDS",
    "SOURCE_NPORT",
    "SecurityPassiveOwnership",
]
