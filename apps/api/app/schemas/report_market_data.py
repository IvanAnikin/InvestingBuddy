"""Chart data for a report's company: end-of-day prices and annual filed financials.

V3.21 investor PDF export. Read-only and descriptive: every number is a figure
as returned by its source (EODHD end-of-day closes, the issuer's own SEC XBRL
10-K facts), with its source, currency and retrieval time. Anything missing is
``null`` with a reason — never estimated.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

BlockState = Literal["ok", "unavailable"]
PriceReason = Literal["no_api_key", "unknown_symbol", "fetch_failed", "no_ticker", "no_prices"]
FinancialsReason = Literal[
    "no_cik", "not_a_us_filer", "fetch_failed", "no_annual_facts", "no_ticker"
]

MARKET_DATA_DISCLAIMER = (
    "Market data for charting only; prices are end-of-day from an aggregator, "
    "financials as filed. Not investment advice."
)


class MarketDataSubject(BaseModel):
    name: str | None = None
    ticker: str | None = None
    exchange: str | None = None
    cik: str | None = None


class PricePointOut(BaseModel):
    d: str = Field(description="Trading date, YYYY-MM-DD")
    c: float = Field(description="End-of-day close as returned by the source")


class PriceBlock(BaseModel):
    state: BlockState
    reason: PriceReason | None = None
    currency: str | None = None
    source: str = "EODHD end-of-day prices"
    source_url: str | None = None
    source_tier: str = "T5_api_aggregator"
    points: list[PricePointOut] = Field(default_factory=list)
    first_date: str | None = None
    last_date: str | None = None
    last_close: float | None = None
    change_pct: float | None = None
    #: True when the series (and change_pct) is split- and dividend-adjusted closes;
    #: last_close is always the traded close of the last day.
    adjusted: bool = False


class FinancialRow(BaseModel):
    fiscal_year_end: str
    revenue: float
    net_income: float | None = None
    operating_income: float | None = None
    form: str | None = None
    filed: str | None = None
    source_urls: list[str] = Field(default_factory=list)


class FinancialsBlock(BaseModel):
    state: BlockState
    reason: FinancialsReason | None = None
    currency: str = "USD"
    source: str = "SEC XBRL (issuer 10-K filings)"
    source_tier: str = "T1_primary_filing"
    rows: list[FinancialRow] = Field(default_factory=list)


class ReportMarketDataResponse(BaseModel):
    report_id: uuid.UUID
    subject: MarketDataSubject
    retrieved_at: datetime
    price: PriceBlock
    financials: FinancialsBlock
    disclaimer: str = MARKET_DATA_DISCLAIMER
