import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticker: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    exchange: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    country: Mapped[str | None] = mapped_column(sa.String(100))
    region: Mapped[str | None] = mapped_column(sa.String(100))
    sector: Mapped[str | None] = mapped_column(sa.String(100))
    industry: Mapped[str | None] = mapped_column(sa.String(100))
    market_cap: Mapped[float | None] = mapped_column(sa.Numeric(20, 2))
    currency: Mapped[str | None] = mapped_column(sa.String(10))
    website: Mapped[str | None] = mapped_column(sa.String(500))
    description: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(50), nullable=False, default="new")
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
        sa.Index("ix_companies_ticker", "ticker"),
        sa.Index("ix_companies_exchange", "exchange"),
        sa.Index("ix_companies_status", "status"),
        sa.UniqueConstraint("ticker", "exchange", name="uq_companies_ticker_exchange"),
    )
