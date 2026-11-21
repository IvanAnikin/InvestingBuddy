"""V3.20 slice 3.5 — issuer (ETF sponsor) holdings files (owner decision D8).

The XLSX fixture is built here with the layout of the real SPDR files read on 2026-11-16
(XLU / XLI / SPSM); the figures are made up.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

import pytest
from sqlalchemy import select

from app.models.passive_ownership import (
    SOURCE_ISSUER_FILE,
    FundHoldingsIngest,
    SecurityPassiveOwnership,
)
from app.services.passive_ownership.issuer_ingest import ingest_files, match
from app.services.sources.issuer_holdings import (
    ADAPTERS,
    CURATED_FUNDS,
    CURATED_FUNDS_VERSION,
    LayoutError,
    fund,
)
from app.services.sources.issuer_holdings.base import IssuerHolding, cusip_ok
from app.services.sources.nport_dataset import Target, Targets
from tests import test_v3_entity_relationships as _base

session = _base.session

POWL_CUSIP, POWL_ISIN = "739128106", "US7391281067"
HEADER = ["Name", "Ticker", "Identifier", "SEDOL", "Weight", "Sector", "Shares Held",
          "Local Currency"]


def _xlsx(rows: list[list[str]]) -> bytes:
    """A workbook with shared strings for text and numbers for numeric-looking cells."""
    strings: list[str] = []
    sheet_rows = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            try:
                float(value)
                numeric = value not in ("",) and not value.isalpha()
            except ValueError:
                numeric = False
            if numeric and c >= 4:
                cells.append(f'<c r="{ref}" t="n"><v>{value}</v></c>')
            else:
                strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    sst = "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<?xml version="1.0"?><sst {ns}>{sst}</sst>')
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<?xml version="1.0"?><worksheet {ns}><sheetData>{"".join(sheet_rows)}'
                   "</sheetData></worksheet>")
    return buf.getvalue()


def _spdr(ticker: str = "SPSM", as_of: str = "As of 13-Nov-2026",
          header: list[str] | None = None, extra: list[list[str]] | None = None) -> bytes:
    rows = [["Fund Name:", f"SPDR {ticker} ETF"], ["Ticker Symbol:", ticker],
            ["Holdings:", as_of], [], header or HEADER,
            ["POWELL INDUSTRIES INC", "POWL", POWL_CUSIP, "2697422", "0.2", "-", "264564.0",
             "USD"],
            ["POWELL INDUSTRIES INC", "POWL", POWL_CUSIP, "2697422", "0.01", "-", "1000.0",
             "USD"],
            ["APPLE INC", "AAPL", "037833100", "2046251", "5.0", "-", "1.2E7", "USD"],
            ["US DOLLAR", "-", "999USDZ92", "-", "0.02", "-", "5362086.71", "USD"],
            *(extra or []),
            [], ["Portfolio holdings, allocations and weightings are as of the date indicated"]]
    return _xlsx(rows)


ISHARES_CSV = (
    "﻿iShares Global Clean Energy ETF\n"
    'Fund Holdings as of,"Nov 13, 2026"\n'
    'Inception Date,"Jun 24, 2008"\n'
    "Shares Outstanding,\"100,000,000.00\"\n"
    "\n"
    "Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,Quantity,Price,"
    "Location,Exchange,Currency,FX Rate,Market Currency,Accrual Date\n"
    'POWL,POWELL INDUSTRIES INC,Industrials,Equity,"1,000,000.00",0.50,"1,000,000.00",'
    '"5,000.00",200.00,United States,NASDAQ,USD,1.00,USD,-\n'
    'VWS,VESTAS WIND SYSTEMS,Industrials,Equity,"2,000,000.00",1.00,"2,000,000.00",'
    '"90,000.00",20.00,Denmark,Nasdaq Copenhagen,DKK,0.15,DKK,-\n'
    "\n"
    '"The content contained herein is owned or licensed by BlackRock"\n'
).encode()


# --------------------------------------------------------------------------- adapters


def test_curated_list_is_versioned_and_complete() -> None:
    assert CURATED_FUNDS_VERSION
    assert {f.sponsor for f in CURATED_FUNDS} <= set(ADAPTERS)
    assert {f.domicile for f in CURATED_FUNDS} == {"US", "UCITS"}
    assert all(f.format_verified for f in CURATED_FUNDS if f.sponsor == "spdr")
    assert not any(f.format_verified for f in CURATED_FUNDS if f.sponsor == "ishares")


def test_spdr_parses_the_real_layout() -> None:
    parsed = ADAPTERS["spdr"].parse(_spdr(), fund("SPSM"))
    assert parsed.as_of == date(2026, 11, 13) and parsed.fund_ticker == "SPSM"
    assert parsed.layout == "spdr_xlsx_v1" and len(parsed.holdings) == 4
    powl = parsed.holdings[0]
    assert powl.cusip == POWL_CUSIP and powl.shares == Decimal("264564.0")
    assert powl.weight_pct == Decimal("0.2") and powl.ticker == "POWL"
    assert ADAPTERS["spdr"].url_for(fund("XLU")) == (
        "https://www.ssga.com/library-content/products/fund-data/etfs/us/"
        "holdings-daily-us-en-xlu.xlsx")


@pytest.mark.parametrize(("body", "reason"), [
    (b"<!DOCTYPE html><html><head><title>Moved</title></head></html>",
     "html_instead_of_file"),
    (b"not a zip", "unreadable_xlsx"),
])
def test_spdr_fails_closed_on_non_files(body: bytes, reason: str) -> None:
    with pytest.raises(LayoutError) as err:
        ADAPTERS["spdr"].parse(body, fund("SPSM"))
    assert err.value.reason == reason


def test_spdr_fails_closed_on_drift() -> None:
    renamed = [c if c != "Shares Held" else "Quantity" for c in HEADER]
    with pytest.raises(LayoutError) as err:
        ADAPTERS["spdr"].parse(_spdr(header=renamed), fund("SPSM"))
    assert err.value.reason == "schema_drift" and "Shares Held" in err.value.detail
    with pytest.raises(LayoutError) as err:
        ADAPTERS["spdr"].parse(_spdr(as_of="13/11/2026"), fund("SPSM"))
    assert err.value.reason == "unknown_layout"
    with pytest.raises(LayoutError) as err:
        ADAPTERS["spdr"].parse(_spdr(ticker="XLI"), fund("SPSM"))
    assert err.value.reason == "fund_mismatch"


def test_ishares_documented_layout_and_html_refusal() -> None:
    parsed = ADAPTERS["ishares"].parse(ISHARES_CSV, fund("ICLN"))
    assert parsed.as_of == date(2026, 11, 13) and len(parsed.holdings) == 2
    powl = parsed.holdings[0]
    assert powl.shares == Decimal("5000.00") and powl.exchange == "NASDAQ" and powl.cusip is None
    # What the real download URL returned on 2026-11-16.
    page = b"<!DOCTYPE html>\n<html lang=\"en-US\"><head><title>iShares ICLN</title>"
    with pytest.raises(LayoutError) as err:
        ADAPTERS["ishares"].parse(page, fund("ICLN"))
    assert err.value.reason == "html_instead_of_file"
    drifted = ISHARES_CSV.replace(b"Weight (%)", b"Weight")
    with pytest.raises(LayoutError) as err:
        ADAPTERS["ishares"].parse(drifted, fund("ICLN"))
    assert err.value.reason == "schema_drift"


def test_cusip_check_digit() -> None:
    assert cusip_ok(POWL_CUSIP) and cusip_ok("037833100") and cusip_ok("65339F101")
    assert not cusip_ok("739128107") and not cusip_ok("73912810") and not cusip_ok(None)


# --------------------------------------------------------------------------- matching


def _targets() -> Targets:
    return Targets([Target(key="security:powl", isin=POWL_ISIN, cusip=POWL_CUSIP)])


def _h(**kw: object) -> IssuerHolding:
    base: dict[str, object] = dict(name="X", ticker=None, cusip=None, isin=None, sedol=None,
                                   exchange=None, shares=None, weight_pct=None,
                                   market_value=None, currency=None, asset_class=None)
    base.update(kw)
    return IssuerHolding(**base)  # type: ignore[arg-type]


def test_match_rules() -> None:
    t, tickers = _targets(), {"POWL": "security:powl"}
    assert match(_h(cusip=POWL_CUSIP), t, tickers) == ("security:powl", "cusip")
    assert match(_h(isin=POWL_ISIN), t, tickers) == ("security:powl", "isin")
    assert match(_h(ticker="POWL", exchange="NASDAQ"), t, tickers) == (
        "security:powl", "ticker_us_exchange")
    # A same-letters ticker on a non-US venue is a different security.
    assert match(_h(ticker="POWL", exchange="London Stock Exchange"), t, tickers) is None
    # A file that gives an identifier is never matched by ticker instead.
    assert match(_h(ticker="POWL", exchange="NASDAQ", cusip="037833100"), t, tickers) is None


# --------------------------------------------------------------------------- ingest


async def test_ingest_writes_issuer_file_aggregate_and_logs_every_file(session) -> None:  # noqa: ANN001
    files = [
        (fund("SPSM"), "https://example.test/spsm.xlsx", _spdr()),
        (fund("ICLN"), "https://example.test/icln.csv", ISHARES_CSV),
        (fund("XLU"), "https://example.test/xlu.xlsx", b"<!DOCTYPE html><html></html>"),
        (fund("INRG"), "https://example.test/inrg.csv", None),
    ]
    result = await ingest_files(session, files, targets=_targets(),
                                tickers={"POWL": "security:powl"},
                                run_date=date(2026, 11, 16))
    assert result.files_ok == 2
    assert result.files_failed == {"XLU": "html_instead_of_file", "INRG": "empty_response"}
    assert result.holdings_matched == 3 and result.securities_written == 1
    assert result.by_basis == {"cusip": 2, "ticker_us_exchange": 1}

    row = (await session.execute(select(SecurityPassiveOwnership).where(
        SecurityPassiveOwnership.source_kind == SOURCE_ISSUER_FILE))).scalar_one()
    assert (row.identifier_kind, row.identifier, row.period) == ("isin", POWL_ISIN, "2026-11-13")
    assert row.fund_count == 2 and row.etf_count == 2
    # SPSM's two lots are one holder: 264,564 + 1,000; ICLN 5,000.
    assert row.shares_held == Decimal("270564")
    funds = {f["fund_ticker"]: f for f in row.top_funds_json}
    assert funds["SPSM"]["shares"] == 265564.0 and funds["SPSM"]["domicile"] == "US"
    assert funds["ICLN"]["match_basis"] == "ticker_us_exchange"
    assert all(f["as_of"] == "2026-11-13" for f in row.top_funds_json)

    logs = {r.source_kind: r for r in (await session.execute(
        select(FundHoldingsIngest))).scalars()}
    assert logs["issuer_file:spdr:SPSM"].status == "complete"
    assert logs["issuer_file:spdr:SPSM"].period == "2026-11-13"
    failed = logs["issuer_file:spdr:XLU"]
    assert failed.status == "failed" and failed.period == "2026-11-16"
    assert failed.error.startswith("html_instead_of_file")
    assert failed.detail_json["use"] == "private_use_own_risk"


async def test_ingest_is_idempotent_per_period(session) -> None:  # noqa: ANN001
    files = [(fund("SPSM"), "https://example.test/spsm.xlsx", _spdr())]
    for _ in range(2):
        await ingest_files(session, files, targets=_targets(), tickers={},
                           run_date=date(2026, 11, 16))
    rows = (await session.execute(select(SecurityPassiveOwnership))).scalars().all()
    logs = (await session.execute(select(FundHoldingsIngest))).scalars().all()
    assert len(rows) == 1 and len(logs) == 1 and rows[0].fund_count == 1


def test_registry_marks_sponsor_files_private_use_own_risk() -> None:
    from app.services.sources.registry import assert_registry_safe, build_registry

    reg = build_registry()
    for source_id in ("spdr_holdings", "ishares_holdings"):
        src = reg.get(source_id)
        assert src is not None and "private_use_own_risk" in (src.reliability_note or "")
    assert_registry_safe(reg)
