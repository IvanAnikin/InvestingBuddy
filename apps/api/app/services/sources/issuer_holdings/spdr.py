"""SPDR (State Street) daily holdings XLSX — V3.20 slice 3.5.

Layout pinned from the real XLU, XLI and SPSM files fetched on 2026-11-16 (as of
13-Nov-2026; 34, 86 and 611 holdings):

    A1 "Fund Name:"      B1 <fund name>
    A2 "Ticker Symbol:"  B2 <ticker>
    A3 "Holdings:"       B3 "As of 13-Nov-2026"
    A5.. header: Name | Ticker | Identifier | SEDOL | Weight | Sector | Shares Held |
                 Local Currency
    rows until the first blank row; disclaimers follow.

``Identifier`` is a CUSIP for US securities. Cash, money-market and futures lines carry
SSGA-internal CUSIP-format codes (``999USDZ92``, ``ADI393HZ6``) that even pass the check
digit; they never equal a researched security's CUSIP, so they never match. ``Weight`` is
in percent.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from app.services.sources.issuer_holdings.base import (
    FundSpec,
    HoldingsFile,
    IssuerHolding,
    LayoutError,
    cusip_ok,
    looks_like_html,
    number,
)
from app.services.sources.issuer_holdings.xlsx import xlsx_rows

LAYOUT = "spdr_xlsx_v1"
#: The canonical location (the older ``/us/en/intermediary/library-content/...`` path
#: answers 301 to this one; seen 2026-11-16).
BASE = "https://www.ssga.com/library-content/products/fund-data/etfs/us"
REQUIRED = ("Name", "Ticker", "Identifier", "SEDOL", "Weight", "Shares Held", "Local Currency")
_PREAMBLE = (("Fund Name:", 0), ("Ticker Symbol:", 1), ("Holdings:", 2))
_AS_OF_RE = re.compile(r"^As of (\d{1,2}-[A-Za-z]{3}-\d{4})$")


class SpdrAdapter:
    sponsor = "spdr"
    layout = LAYOUT

    def url_for(self, fund: FundSpec) -> str:
        return f"{BASE}/holdings-daily-us-en-{fund.ticker.lower()}.xlsx"

    def parse(self, body: bytes, fund: FundSpec) -> HoldingsFile:
        if looks_like_html(body):
            raise LayoutError("html_instead_of_file")
        rows = xlsx_rows(body)
        if len(rows) < 6:
            raise LayoutError("unknown_layout", "too few rows")
        for label, i in _PREAMBLE:
            if not rows[i] or rows[i][0] != label:
                raise LayoutError("unknown_layout", f"row {i + 1} is not {label!r}")
        ticker = rows[1][1].strip().upper() if len(rows[1]) > 1 else ""
        if ticker != fund.ticker.upper():
            raise LayoutError("fund_mismatch", f"file is for {ticker or '?'}")
        match = _AS_OF_RE.match(rows[2][1].strip() if len(rows[2]) > 1 else "")
        if not match:
            raise LayoutError("unknown_layout", "no 'As of' date")
        as_of: date = datetime.strptime(match.group(1), "%d-%b-%Y").date()
        header_at = next((i for i, r in enumerate(rows[:12]) if r[:1] == ["Name"]), None)
        if header_at is None:
            raise LayoutError("unknown_layout", "no header row")
        header = rows[header_at]
        missing = [c for c in REQUIRED if c not in header]
        if missing:
            raise LayoutError("schema_drift", "missing " + ", ".join(missing))
        col = {name: header.index(name) for name in REQUIRED}
        out = HoldingsFile(sponsor=self.sponsor, fund_ticker=ticker,
                           fund_name=rows[0][1] if len(rows[0]) > 1 else None,
                           as_of=as_of, layout=LAYOUT)
        for row in rows[header_at + 1:]:
            if not any(cell for cell in row):
                break
            def cell(name: str, row: list[str] = row) -> str:
                return row[col[name]] if col[name] < len(row) else ""

            name = cell("Name")
            if not name:
                continue
            ident = cell("Identifier").upper()
            out.holdings.append(IssuerHolding(
                name=name, ticker=cell("Ticker") if cell("Ticker") not in ("", "-") else None,
                cusip=ident if cusip_ok(ident) else None, isin=None,
                sedol=cell("SEDOL") if cell("SEDOL") not in ("", "-") else None,
                exchange=None, shares=number(cell("Shares Held")),
                weight_pct=number(cell("Weight")), market_value=None,
                currency=cell("Local Currency") or None, asset_class=None))
        if not out.holdings:
            raise LayoutError("unknown_layout", "no holdings rows")
        return out


__all__ = ["LAYOUT", "SpdrAdapter"]
