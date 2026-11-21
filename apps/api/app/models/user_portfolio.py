"""Private, hand-entered portfolios — V3.20 slice 12.1 (ADR-100, migration 055).

PRIVATE USER DATA (CLAUDE.md rule 10)
=====================================
These two tables are the only place a holding is stored. Nothing here is ever copied
into a public research table, and nothing public is keyed on it: the portfolio review
(slice 12.2) READS public research records; it never writes a holding into them.

``owner_key`` is a SHA-256 of the signed-in admin's normalised e-mail address (the
identity the admin proxy forwards after Basic Auth), so the table scopes rows to a person
without storing the address itself. Entered by hand only: no broker import, credential
or account link exists (ADR-100 §1). A position records what was entered — issuer, entry
date, and optionally entry price and weight — never an order or a target.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserPortfolio(Base):
    __tablename__ = "user_portfolios"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    owner_key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    note: Mapped[str | None] = mapped_column(sa.String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 nullable=False)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 onupdate=_now, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("owner_key", "name", name="uq_user_portfolios_owner_name"),
        sa.Index("ix_user_portfolios_owner_key", "owner_key"),
    )


class UserPortfolioPosition(Base):
    __tablename__ = "user_portfolio_positions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("user_portfolios.id", ondelete="CASCADE",
                      name="fk_user_portfolio_positions_portfolio_id"),
        nullable=False)
    #: The issuer when it is a known company; NULL keeps the hand-entered ticker only.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="SET NULL",
                      name="fk_user_portfolio_positions_company_id"),
        nullable=True)
    ticker: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    exchange: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    entry_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    entry_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(20, 6), nullable=True)
    entry_currency: Mapped[str | None] = mapped_column(sa.String(3), nullable=True)
    #: Percent of the portfolio as the user entered it (0 < w ≤ 100); never computed.
    weight_pct: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 3), nullable=True)
    note: Mapped[str | None] = mapped_column(sa.String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("portfolio_id", "ticker", "exchange", "entry_date",
                            name="uq_user_portfolio_positions_entry"),
        sa.Index("ix_user_portfolio_positions_portfolio_id", "portfolio_id"),
        sa.CheckConstraint("entry_price IS NULL OR entry_price > 0",
                           name="ck_user_portfolio_positions_price_positive"),
        sa.CheckConstraint("weight_pct IS NULL OR (weight_pct > 0 AND weight_pct <= 100)",
                           name="ck_user_portfolio_positions_weight_range"),
        sa.CheckConstraint("(entry_price IS NULL) = (entry_currency IS NULL)",
                           name="ck_user_portfolio_positions_price_has_currency"),
    )


__all__ = ["UserPortfolio", "UserPortfolioPosition"]
