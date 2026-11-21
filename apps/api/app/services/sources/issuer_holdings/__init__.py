"""Issuer (ETF sponsor) holdings files — V3.20 slices 3.5–3.6 (owner decision D8).

Private use, own risk: switch off or license before any public launch.
"""

from __future__ import annotations

from app.services.sources.issuer_holdings.base import (
    DOMICILE_UCITS,
    DOMICILE_US,
    Adapter,
    FundSpec,
    HoldingsFile,
    IssuerHolding,
    LayoutError,
)
from app.services.sources.issuer_holdings.ishares import ISharesAdapter
from app.services.sources.issuer_holdings.spdr import SpdrAdapter

#: Version of the curated list below; bump on every change.
CURATED_FUNDS_VERSION = "2026-11-16.1"

#: The electricity / grid / clean-energy / small-cap funds read first (spec §3.3, D8).
#: ``format_verified`` = a real file of this sponsor was fetched and parsed.
CURATED_FUNDS: tuple[FundSpec, ...] = (
    FundSpec("spdr", "XLU", "Utilities Select Sector SPDR", DOMICILE_US,
             ("utilities", "grid_and_electrification"), format_verified=True),
    FundSpec("spdr", "XLI", "Industrial Select Sector SPDR", DOMICILE_US,
             ("industrials", "grid_and_electrification"), format_verified=True),
    FundSpec("spdr", "SPSM", "SPDR Portfolio S&P 600 Small Cap", DOMICILE_US, ("small_cap",),
             format_verified=True),
    FundSpec("ishares", "ICLN", "iShares Global Clean Energy ETF", DOMICILE_US,
             ("clean_energy",), locator="239738/ishares-global-clean-energy-etf"),
    FundSpec("ishares", "INRG", "iShares Global Clean Energy Transition UCITS ETF",
             DOMICILE_UCITS, ("clean_energy",),
             locator="251911/ishares-global-clean-energy-ucits-etf"),
)

ADAPTERS: dict[str, Adapter] = {"spdr": SpdrAdapter(), "ishares": ISharesAdapter()}


def fund(ticker: str) -> FundSpec | None:
    return next((f for f in CURATED_FUNDS if f.ticker == ticker.upper()), None)


__all__ = [
    "ADAPTERS",
    "CURATED_FUNDS",
    "CURATED_FUNDS_VERSION",
    "DOMICILE_UCITS",
    "DOMICILE_US",
    "FundSpec",
    "HoldingsFile",
    "IssuerHolding",
    "LayoutError",
    "fund",
]
