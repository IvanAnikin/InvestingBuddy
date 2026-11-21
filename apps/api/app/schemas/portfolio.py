"""Request/response shapes for private, hand-entered portfolios — V3.20 slice 12.1."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TICKER = r"^[A-Za-z0-9.\-]{1,20}$"


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class PositionCreate(BaseModel):
    """What the user entered — never an order, a size to trade or a target."""

    ticker: str = Field(pattern=_TICKER)
    exchange: str | None = Field(default=None, pattern=r"^[A-Za-z0-9.\-]{1,20}$")
    entry_date: date
    entry_price: Decimal | None = Field(default=None, gt=0, max_digits=20, decimal_places=6)
    entry_currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    weight_pct: Decimal | None = Field(default=None, gt=0, le=100, max_digits=6,
                                       decimal_places=3)
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _consistent(self) -> PositionCreate:
        self.ticker = self.ticker.upper()
        self.exchange = self.exchange.upper() if self.exchange else None
        self.entry_currency = self.entry_currency.upper() if self.entry_currency else None
        if (self.entry_price is None) != (self.entry_currency is None):
            raise ValueError("an entry price needs its currency, and a currency its price")
        if self.entry_date > date.today():
            raise ValueError("the entry date is in the future")
        return self


class PositionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID | None
    ticker: str
    exchange: str | None
    entry_date: date
    entry_price: Decimal | None
    entry_currency: str | None
    weight_pct: Decimal | None
    note: str | None
    created_at: datetime


class PortfolioRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    note: str | None
    created_at: datetime
    position_count: int = 0
    weight_pct_total: Decimal | None = None


class PortfolioDetail(PortfolioRead):
    positions: list[PositionRead] = []


__all__ = ["PortfolioCreate", "PortfolioDetail", "PortfolioRead", "PositionCreate",
           "PositionRead"]
