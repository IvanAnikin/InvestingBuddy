"""Issuer holdings files — the adapter interface (V3.20 slice 3.5, owner decision D8).

An ETF sponsor publishes each fund's holdings daily as a CSV or XLSX. Each sponsor gets
one adapter: ``url_for(fund)`` and ``parse(body) -> HoldingsFile``. ``parse`` checks the
layout first (the labels and column names it was written against) and raises
``LayoutError`` on anything else — an HTML page, a renamed column, a missing "as of"
date. It never guesses: a drifted file is a recorded failure, not wrong numbers.

Private use, own risk (D8): the sponsors' terms do not license redistribution. These
files must be switched off or licensed before any public launch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Protocol

#: ``domicile`` of a fund: a US-registered fund (also in N-PORT) or a UCITS fund (not).
DOMICILE_US = "US"
DOMICILE_UCITS = "UCITS"


class LayoutError(ValueError):
    """The file is not the layout the adapter was written against. Fail closed."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class FundSpec:
    """One curated fund: who publishes it and where its file is."""

    sponsor: str          # adapter key: "spdr" | "ishares"
    ticker: str
    name: str
    domicile: str         # DOMICILE_US | DOMICILE_UCITS
    themes: tuple[str, ...]
    #: Sponsor-specific locator (iShares product id + slug; SPDR needs none).
    locator: str = ""
    #: True once a real file was fetched and parsed with this adapter.
    format_verified: bool = False


@dataclass(frozen=True)
class IssuerHolding:
    name: str
    ticker: str | None
    cusip: str | None
    isin: str | None
    sedol: str | None
    exchange: str | None
    shares: Decimal | None
    weight_pct: Decimal | None
    market_value: Decimal | None
    currency: str | None
    asset_class: str | None


@dataclass
class HoldingsFile:
    sponsor: str
    fund_ticker: str
    fund_name: str | None
    as_of: date
    layout: str
    holdings: list[IssuerHolding] = field(default_factory=list)


class Adapter(Protocol):
    sponsor: str
    layout: str

    def url_for(self, fund: FundSpec) -> str: ...

    def parse(self, body: bytes, fund: FundSpec) -> HoldingsFile: ...


def number(text: str | None) -> Decimal | None:
    """"3.5753686E7" / "1,234.5" / "-" → Decimal or None."""
    raw = (text or "").strip().replace(",", "")
    if not raw or raw in {"-", "--", "N/A", "n/a"}:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def cusip_ok(value: str | None) -> bool:
    """Nine characters with a valid CUSIP check digit (rejects sponsors' internal codes)."""
    v = (value or "").strip().upper()
    if len(v) != 9 or not v[8].isdigit() or not v[:8].isalnum():
        return False
    total = 0
    for i, ch in enumerate(v[:8]):
        n = int(ch) if ch.isdigit() else ord(ch) - 55
        if i % 2:
            n *= 2
        total += n // 10 + n % 10
    return (10 - total % 10) % 10 == int(v[8])


def looks_like_html(body: bytes) -> bool:
    head = body[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


__all__ = [
    "DOMICILE_UCITS",
    "DOMICILE_US",
    "Adapter",
    "FundSpec",
    "HoldingsFile",
    "IssuerHolding",
    "LayoutError",
    "cusip_ok",
    "looks_like_html",
    "number",
]
