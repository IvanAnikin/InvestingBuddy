"""V3.20 slice 11.1 — the Taiwan Stock Exchange as an official exchange directory.

TWSE publishes two open-data files (Open Government Data License v1.0, attribution):
``t187ap03_L`` (every listed company, with its ISSUED common shares and an English short
name) and ``STOCK_DAY_ALL`` (the official closing price of every security that day).
A lead's listing on TW is verified against the first; its market cap is shares × close.

Field names below are the producer's own, from the TWSE OpenAPI schema. The figures are
test fixtures, not market data.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import Settings
from app.services.discovery import directories as d
from app.services.discovery.identity import normalise_venue, verify_identity
from app.services.discovery.leads import CompanyLead
from app.services.sources.document_fetcher import DocumentFetchResult


def _company(code: str, name: str, english: str, shares: str, foreign: str = "") -> dict:
    return {"出表日期": "1151116", "公司代號": code, "公司名稱": name, "公司簡稱": name,
            "外國企業註冊地國": foreign, "產業別": "24", "英文簡稱": english,
            "已發行普通股數或TDR原股發行股數": shares}


COMPANIES = json.dumps([
    _company("2330", "台灣積體電路製造股份有限公司", "TSMC", "1000000000"),
    _company("1101", "臺灣水泥股份有限公司", "TCC", "2000000000"),
    _company("9105", "泰金寶-DR", "Cal-Comp DR", "500000000", foreign="泰國"),
    {"出表日期": "1151116", "公司代號": "<script>", "英文簡稱": "x"},
], ensure_ascii=False)
PRICES = json.dumps([
    {"Date": "1151116", "Code": "2330", "Name": "台積電", "ClosingPrice": "1,050.00"},
    {"Date": "1151116", "Code": "1101", "Name": "台泥", "ClosingPrice": ""},
    {"Date": "1151116", "Code": "9105", "Name": "泰金寶-DR", "ClosingPrice": "2.50"},
], ensure_ascii=False)

CALLS: list[str] = []


async def fetcher(url: str, **_kw):  # noqa: ANN201
    CALLS.append(url)
    body = (COMPANIES if url.endswith("/opendata/t187ap03_L")
            else PRICES if url.endswith("/exchangeReport/STOCK_DAY_ALL") else None)
    if body is None:
        return DocumentFetchResult(requested_url=url, error="nope", failure_code="http_404")
    return DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                               content_type="application/json", document_type="text",
                               content=body.encode())


@pytest.fixture(autouse=True)
def _clear():
    d.reset_cache()
    CALLS.clear()
    yield
    d.reset_cache()


def _lead(name: str, ticker: str, venue: str) -> CompanyLead:
    return CompanyLead(name=name, ticker=ticker, exchange_raw=venue, country=None,
                       listing_source_url=None, evidence_url=None, why=None,
                       source="external_search")


class TestParse:
    def test_the_company_file(self) -> None:
        rows = {r.ticker: r for r in d.parse_twse(COMPANIES)}
        assert set(rows) == {"2330", "1101", "9105"}  # an unsafe code is never kept
        tsmc = rows["2330"]
        assert (tsmc.name, tsmc.exchange, tsmc.mic, tsmc.currency) == ("TSMC", "TW", "XTAI",
                                                                       "TWD")
        assert tsmc.shares == 1e9 and tsmc.as_of == "2026-11-16"  # ROC year 115
        assert tsmc.country == "Taiwan" and rows["9105"].country is None  # foreign issuer
        assert tsmc.isin is None  # TWSE open data carries none; never derived

    def test_the_price_file_skips_a_day_without_a_close(self) -> None:
        rows = {r.ticker: r for r in d.parse_twse_prices(PRICES)}
        assert rows["2330"].price == 1050.0 and "1101" not in rows

    @pytest.mark.parametrize(("raw", "iso"), [("1151116", "2026-11-16"),
                                              ("990101", "2010-01-01"),
                                              ("1151332", None), ("", None)])
    def test_roc_dates(self, raw: str, iso: str | None) -> None:
        assert d._roc_date(raw) == iso

    def test_a_malformed_file_is_no_rows(self) -> None:
        assert d.parse_twse("<html>") == [] and d.parse_twse('{"a": 1}') == []


class TestAcronym:
    @pytest.mark.parametrize(("short", "full"), [
        ("TSMC", "Taiwan Semiconductor Manufacturing Company Limited"),
        ("TSMC", "Taiwan Semiconductor Manufacturing Co., Ltd."),
        ("TCC", "Taiwan Cement Corp."),
    ])
    def test_an_acronym_of_the_full_name_agrees(self, short: str, full: str) -> None:
        assert d._acronym_agrees(short, full)

    @pytest.mark.parametrize(("short", "full"), [
        ("ASE", "Taiwan Semiconductor Manufacturing Company Limited"),
        ("TS", "Taiwan Semiconductor"),  # too short to mean anything
        ("Cal-Comp DR", "Cal-Comp Electronics Thailand"),  # not an acronym
        ("TSMC", "Taiwan Semiconductor Manufacturing Company Ventures"),  # word left over
        ("TC", None),
    ])
    def test_anything_else_does_not(self, short: str, full: str | None) -> None:
        assert not d._acronym_agrees(short, full)


class TestVerification:
    async def test_a_lead_is_verified_by_the_twse_file(self) -> None:
        outcome = await verify_identity(
            _lead("Taiwan Semiconductor Manufacturing Company Limited", "2330.TW",
                  "Taiwan Stock Exchange"), fetcher=fetcher)
        assert outcome.status == "verified" and outcome.exchange == "TW"
        assert outcome.listing_source["tier"] == "exchange"
        assert outcome.directory_listing["shares"] == 1e9

    async def test_a_real_code_on_the_wrong_company_is_refused(self) -> None:
        outcome = await verify_identity(_lead("Formosa Plastics Corp", "2330", "TWSE"),
                                        fetcher=fetcher)
        assert outcome.status == "rejected"
        assert outcome.rejection_reason == "not_in_exchange_directory"

    async def test_market_cap_is_issued_shares_times_the_official_close(self) -> None:
        row, _ = await d.find_listing(name="TSMC", ticker="2330", venue="TW", fetcher=fetcher)
        assert row is not None
        amount, currency, url, basis = await d.official_market_cap(row, fetcher=fetcher)
        assert (amount, currency) == (1e9 * 1050.0, "TWD")
        assert url.endswith("STOCK_DAY_ALL")
        assert "1,000,000,000" in basis and "2026-11-16" in basis
        assert "Open Government Data License" in basis  # the licence's attribution

    async def test_no_close_that_day_means_no_market_cap(self) -> None:
        row, _ = await d.find_listing(name="TCC", ticker="1101", venue="TW", fetcher=fetcher)
        assert row is not None
        assert (await d.official_market_cap(row, fetcher=fetcher))[0] is None

    async def test_a_tdr_gets_no_market_cap(self) -> None:
        # Its file states the UNDERLYING shares, not the receipts the price is for.
        row, _ = await d.find_listing(name="Cal-Comp DR", ticker="9105", venue="TW",
                                      fetcher=fetcher)
        assert row is not None
        assert (await d.official_market_cap(row, fetcher=fetcher))[0] is None


class TestFlag:
    async def test_off_means_no_directory_and_no_request(self) -> None:
        cfg = Settings(v3_directory_twse_enabled=False)
        assert d.directory_for_venue("TW", cfg) is None
        assert await d.find_listing(name="TSMC", ticker="2330", venue="TW", cfg=cfg,
                                    fetcher=fetcher) == (None, None)
        assert await d.load(d.TWSE, cfg=cfg, fetcher=fetcher) == []
        assert CALLS == []

    def test_on_by_default_and_other_venues_unaffected(self) -> None:
        cfg = Settings()
        assert cfg.v3_directory_twse_enabled is True
        assert d.directory_for_venue("TW", cfg) is d.TWSE
        assert d.directory_for_venue("PA", Settings(v3_directory_twse_enabled=False)) \
            is d.EURONEXT
        assert d.directory_for_venue("KO", cfg) is None  # no Korean directory (yet)


@pytest.mark.parametrize(("raw", "code"), [
    ("Taiwan Stock Exchange", "TW"), ("TWSE", "TW"), ("KOSDAQ", "KQ"), ("KOSPI", "KO"),
    ("Tokyo Stock Exchange", "TSE"), ("TSE", "TSE"), ("NSE", "NSE"),
])
def test_asian_venue_names(raw: str, code: str) -> None:
    assert normalise_venue(raw) == code
