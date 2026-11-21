"""iShares (BlackRock) holdings CSV — V3.20 slice 3.5.

FORMAT NOT VERIFIED ON A REAL FILE. On 2026-11-16 the product download URL
(``/us/products/<id>/<slug>/1467271812596.ajax?fileType=csv&fileName=<T>_holdings
&dataType=fund``) answered a plain request with the product's HTML page (status 200,
``Content-Type: text/csv``), and the UK UCITS URL did the same. This adapter is written
against the long-published layout and fails closed on anything else — which is exactly
what it does on that HTML today (``html_instead_of_file``):

    line 1   iShares <fund name>
    line 2   "Fund Holdings as of,"Nov 13, 2026""
    ...      further key/value preamble lines
    header   Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,
             Quantity|Shares,Price,Location,Exchange,Currency,...   (UCITS files may add
             ISIN / SEDOL columns, which are used when present)
    rows     until a blank line or the trailing disclaimer

No CUSIP in the US file: a holding matches a researched security by ISIN when the file
has one, else by ticker on a US exchange (``Exchange`` names a US venue).
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

from app.services.sources.issuer_holdings.base import (
    FundSpec,
    HoldingsFile,
    IssuerHolding,
    LayoutError,
    looks_like_html,
    number,
)

LAYOUT = "ishares_csv_v1"
_HOSTS = {"US": "https://www.ishares.com/us/products",
          "UCITS": "https://www.ishares.com/uk/individual/en/products"}
_AJAX = {"US": "1467271812596", "UCITS": "1506575576011"}
REQUIRED = ("Ticker", "Name", "Asset Class", "Weight (%)")
_AS_OF_RE = re.compile(r"^Fund Holdings as of$", re.IGNORECASE)
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")


def _as_of(value: str) -> date:
    for fmt in ("%b %d, %Y", "%d/%b/%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise LayoutError("unknown_layout", "unreadable 'Fund Holdings as of' date")


class ISharesAdapter:
    sponsor = "ishares"
    layout = LAYOUT

    def url_for(self, fund: FundSpec) -> str:
        # locator = "<product id>/<slug>", e.g. "239738/ishares-global-clean-energy-etf"
        region = "UCITS" if fund.domicile == "UCITS" else "US"
        return (f"{_HOSTS[region]}/{fund.locator}/{_AJAX[region]}.ajax?fileType=csv"
                f"&fileName={fund.ticker.upper()}_holdings&dataType=fund")

    def parse(self, body: bytes, fund: FundSpec) -> HoldingsFile:
        if looks_like_html(body):
            raise LayoutError("html_instead_of_file")
        text = body.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
        as_of: date | None = None
        header_at = None
        for i, row in enumerate(rows[:20]):
            cells = [c.strip() for c in row]
            if len(cells) >= 2 and _AS_OF_RE.match(cells[0]):
                as_of = _as_of(cells[1])
            if cells[:2] == ["Ticker", "Name"]:
                header_at = i
                break
        if as_of is None:
            raise LayoutError("unknown_layout", "no 'Fund Holdings as of' line")
        if header_at is None:
            raise LayoutError("unknown_layout", "no header row")
        header = [c.strip() for c in rows[header_at]]
        missing = [c for c in REQUIRED if c not in header]
        shares_col = next((c for c in ("Quantity", "Shares", "Nominal") if c in header), None)
        if missing or shares_col is None:
            raise LayoutError("schema_drift", "missing " + ", ".join(
                missing + ([] if shares_col else ["Quantity/Shares"])))
        idx = {name: i for i, name in enumerate(header)}

        def cell(row: list[str], name: str) -> str:
            i = idx.get(name)
            return row[i].strip() if i is not None and i < len(row) else ""

        out = HoldingsFile(sponsor=self.sponsor, fund_ticker=fund.ticker.upper(),
                           fund_name=(rows[0][0].strip() if rows and rows[0] else None),
                           as_of=as_of, layout=LAYOUT)
        for row in rows[header_at + 1:]:
            if not row or not any(c.strip() for c in row) or len(row) < len(REQUIRED):
                break
            name = cell(row, "Name")
            if not name:
                continue
            isin = cell(row, "ISIN").upper()
            ticker = cell(row, "Ticker")
            out.holdings.append(IssuerHolding(
                name=name, ticker=ticker if ticker not in ("", "-") else None, cusip=None,
                isin=isin if _ISIN_RE.match(isin) else None, sedol=cell(row, "SEDOL") or None,
                exchange=cell(row, "Exchange") or None, shares=number(cell(row, shares_col)),
                weight_pct=number(cell(row, "Weight (%)")),
                market_value=number(cell(row, "Market Value")),
                currency=cell(row, "Currency") or None,
                asset_class=cell(row, "Asset Class") or None))
        if not out.holdings:
            raise LayoutError("unknown_layout", "no holdings rows")
        return out


__all__ = ["LAYOUT", "ISharesAdapter"]
