"""V3.21 investor PDF export — GET /api/v1/reports/{report_id}/market-data.

Chart data for a report's company: EODHD end-of-day closes and the issuer's own SEC XBRL
10-K facts. No live calls: the EODHD HTTP leg, the SEC fetcher and the CIK lookup are
replaced. The real provider builds its request (so the key is really in the request
params) and the tests prove it never reaches the response.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.integrations.providers.eodhd_price_only_provider import EodhdPriceOnlyProvider
from app.integrations.providers.eodhd_provider import EodhdNotFoundError
from app.models.report import Report
from app.services import report_market_data_service as svc
from app.services.discovery.directories import annual_revenue_pair, annual_usd_facts_by_end
from app.services.sources.document_fetcher import DocumentFetchResult

_GET = "app.api.v1.reports.report_service.get_report"
_COMPANY = "app.api.v1.reports.company_service.get_company"
FIXTURE = Path(__file__).parent / "fixtures" / "sec_growth" / "ospn_revenue.json"
KEY = "SECRET-eodhd-key-123"
REVENUE = "RevenueFromContractWithCustomerExcludingAssessedTax"


def _fact(start: str, end: str, val: float, form: str = "10-K", filed: str = "2026-02-20"):
    return {"start": start, "end": end, "val": val, "form": form, "fp": "FY", "filed": filed}


def _annual(year: int, val: float, **kw):  # noqa: ANN003, ANN202
    return _fact(f"{year}-01-01", f"{year}-12-31", val, **kw)


def _price_rows(n: int, start: date = date(2024, 1, 1), first: float = 20.0, step: float = 0.01):
    return [{"date": (start + timedelta(days=i)).isoformat(), "close": round(first + i * step, 4)}
            for i in range(n)]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EODHD_API_KEY", KEY)
    svc.reset_cache()
    yield
    svc.reset_cache()


@pytest.fixture
def eod(monkeypatch: pytest.MonkeyPatch):
    """Replace the EODHD HTTP leg; ``eod.rows`` is what it returns, ``eod.calls`` what it saw."""
    state = MagicMock()
    state.rows = _price_rows(30)
    state.calls = []
    state.raise_exc = None

    async def fake_get_json(self, url, params=None):  # noqa: ANN001, ANN202
        state.calls.append((url, dict(params or {})))
        if state.raise_exc is not None:
            raise state.raise_exc
        return state.rows

    monkeypatch.setattr(EodhdPriceOnlyProvider, "_get_json", fake_get_json)
    return state


@pytest.fixture
def sec(monkeypatch: pytest.MonkeyPatch):
    """Replace the SEC fetcher and CIK lookup; ``sec.by_concept`` maps concept -> payload."""
    state = MagicMock()
    state.by_concept = {
        REVENUE: {"units": {"USD": [_annual(y, 100e6 + y) for y in range(2019, 2026)]}},
        "NetIncomeLoss": {"units": {"USD": [_annual(2024, -12e6), _annual(2025, 5e6)]}},
    }
    state.seen = []
    state.cik = "1560327"

    async def fetch(url, **kw):  # noqa: ANN001, ANN202
        assert kw["allowed_domains"] == ("data.sec.gov",)
        assert kw["resolve_ip"] is True
        state.seen.append(url)
        body = state.by_concept.get(url.rsplit("/", 1)[-1].removesuffix(".json"))
        return DocumentFetchResult(
            requested_url=url, final_url=url, status_code=200 if body else 404,
            content_type="application/json", document_type="html",
            content=json.dumps(body).encode() if body else b"")

    async def resolve(ticker, exchange):  # noqa: ANN001, ANN202
        if isinstance(state.cik, Exception):
            raise state.cik
        return state.cik

    monkeypatch.setattr(svc, "safe_fetch_document", fetch)
    monkeypatch.setattr(svc, "_resolve_cik", resolve)
    return state


def _report(report_id: uuid.UUID, subject: dict | None, company_id: uuid.UUID | None = None):
    report = MagicMock(spec=Report)
    report.id = report_id
    report.company_id = company_id
    report.source_summary_json = (
        {"v3_research": {"professional_research": {"subject": subject}}} if subject else {})
    return report


RPD = {"name": "Rapid7, Inc.", "ticker": "RPD", "exchange": "US"}


async def _get(client: AsyncClient, report_id: uuid.UUID, report):  # noqa: ANN001, ANN202
    with patch(_GET, new_callable=AsyncMock, return_value=report):
        return await client.get(f"/api/v1/reports/{report_id}/market-data")


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


async def test_ok_path_shape(client: AsyncClient, report_id: uuid.UUID, eod, sec) -> None:
    response = await _get(client, report_id, _report(report_id, RPD))
    assert response.status_code == 200
    body = response.json()
    assert body["report_id"] == str(report_id)
    assert body["subject"] == {**RPD, "cik": "1560327"}
    assert body["retrieved_at"]
    assert "Not investment advice" in body["disclaimer"]

    price = body["price"]
    assert price["state"] == "ok" and price["reason"] is None
    assert price["currency"] == "USD"
    assert price["source_tier"] == "T5_api_aggregator"
    assert price["source_url"].endswith("/eod/RPD.US")
    assert len(price["points"]) == 30
    assert price["points"][0] == {"d": "2024-01-01", "c": 20.0}
    assert price["first_date"] == "2024-01-01" and price["last_date"] == "2024-01-30"
    assert price["last_close"] == 20.29

    fin = body["financials"]
    assert fin["state"] == "ok" and fin["currency"] == "USD"
    assert fin["source_tier"] == "T1_primary_filing"
    assert [r["fiscal_year_end"] for r in fin["rows"]] == [
        f"{y}-12-31" for y in range(2021, 2026)]  # newest five, oldest first
    last = fin["rows"][-1]
    assert last["revenue"] == 100e6 + 2025
    assert last["net_income"] == 5e6
    assert last["operating_income"] is None
    assert last["form"] == "10-K" and last["filed"] == "2026-02-20"
    assert last["source_urls"] == [
        f"https://data.sec.gov/api/xbrl/companyconcept/CIK0001560327/us-gaap/{REVENUE}.json",
        "https://data.sec.gov/api/xbrl/companyconcept/CIK0001560327/us-gaap/NetIncomeLoss.json",
    ]
    assert fin["rows"][0]["net_income"] is None  # never invented


async def test_key_never_reaches_the_response(
    client: AsyncClient, report_id: uuid.UUID, eod, sec, caplog
) -> None:
    caplog.set_level("INFO")
    response = await _get(client, report_id, _report(report_id, RPD))
    assert eod.calls and eod.calls[0][1]["api_token"] == KEY  # it really was sent
    assert KEY not in response.text
    assert "?" not in response.json()["price"]["source_url"]
    assert KEY not in caplog.text


async def test_unknown_report_is_404(client: AsyncClient, report_id: uuid.UUID) -> None:
    response = await _get(client, report_id, None)
    assert response.status_code == 404


async def test_no_ticker_is_200_with_both_blocks_unavailable(
    client: AsyncClient, report_id: uuid.UUID, eod, sec
) -> None:
    response = await _get(client, report_id, _report(report_id, {"name": "Nameless"}))
    assert response.status_code == 200
    body = response.json()
    assert (body["price"]["state"], body["price"]["reason"]) == ("unavailable", "no_ticker")
    assert body["financials"]["state"] == "unavailable"
    assert body["financials"]["reason"] == "no_ticker"
    assert eod.calls == [] and sec.seen == []


async def test_subject_falls_back_to_the_company(
    client: AsyncClient, report_id: uuid.UUID, company_id: uuid.UUID, eod, sec
) -> None:
    company = MagicMock(ticker="RPD", exchange="US", name="Rapid7, Inc.")
    with patch(_COMPANY, new_callable=AsyncMock, return_value=company):
        response = await _get(client, report_id, _report(report_id, None, company_id))
    assert response.json()["subject"]["ticker"] == "RPD"
    assert response.json()["price"]["state"] == "ok"


async def test_no_api_key_makes_price_unavailable(
    client: AsyncClient, report_id: uuid.UUID, eod, sec, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EODHD_API_KEY")
    body = (await _get(client, report_id, _report(report_id, RPD))).json()
    assert body["price"]["state"] == "unavailable"
    assert body["price"]["reason"] == "no_api_key"
    assert body["price"]["points"] == [] and body["price"]["last_close"] is None
    assert body["financials"]["state"] == "ok"
    assert eod.calls == []


# ---------------------------------------------------------------------------
# Price block
# ---------------------------------------------------------------------------


async def test_downsampling_keeps_at_most_260_and_the_last_point(eod, sec) -> None:
    eod.rows = _price_rows(1000)
    price = (await svc.market_data_for("RPD", "US"))["price"]
    assert len(price.points) <= svc.MAX_PRICE_POINTS
    assert price.points[-1].d == eod.rows[-1]["date"]
    assert price.points[-1].c == eod.rows[-1]["close"]
    assert price.points[0].d == eod.rows[0]["date"]
    assert [p.d for p in price.points] == sorted(p.d for p in price.points)


async def test_change_pct_is_last_over_first_and_drops_non_positive_closes(eod, sec) -> None:
    eod.rows = [{"date": "2025-11-25", "close": 0}, {"date": "2026-11-24", "close": 15.6},
                {"date": "2025-11-24", "close": 25.28}, {"date": "2026-01-02", "close": -1}]
    price = (await svc.market_data_for("RPD", "US"))["price"]
    assert [p.d for p in price.points] == ["2025-11-24", "2026-11-24"]
    assert price.change_pct == round((15.6 / 25.28 - 1) * 100, 2) == -38.29
    assert price.last_close == 15.6


async def test_unknown_symbol_and_fetch_failure(eod, sec) -> None:
    eod.raise_exc = EodhdNotFoundError("404")
    assert (await svc.market_data_for("ZZZZ", "US"))["price"].reason == "unknown_symbol"
    eod.raise_exc = RuntimeError("boom")
    assert (await svc.market_data_for("YYYY", "US"))["price"].reason == "fetch_failed"


# ---------------------------------------------------------------------------
# Financials block
# ---------------------------------------------------------------------------


async def test_no_cik_makes_financials_unavailable(eod, sec) -> None:
    sec.cik = ValueError("not in the SEC ticker index")
    data = await svc.market_data_for("NOPE", "US")
    assert data["financials"].state == "unavailable"
    assert data["financials"].reason == "no_cik"
    assert data["cik"] is None and sec.seen == []


async def test_non_us_listing_is_not_a_us_filer_without_any_sec_call(
    eod, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(*a, **kw):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("no SEC fetch for a non-US listing")

    monkeypatch.setattr(svc, "safe_fetch_document", never)
    data = await svc.market_data_for("OXIG", "LSE")  # the real resolver refuses it offline
    assert data["financials"].reason == "not_a_us_filer"
    assert data["price"].state == "ok"


async def test_no_annual_facts(eod, sec) -> None:
    sec.by_concept = {REVENUE: {"units": {"USD": [
        _fact("2025-07-01", "2025-09-30", 50.0, form="10-Q")]}}}
    fin = (await svc.market_data_for("RPD", "US"))["financials"]
    assert fin.state == "unavailable" and fin.reason == "no_annual_facts"


async def test_annual_filtering_quarters_ignored_latest_filed_wins_negatives_kept(
    eod, sec
) -> None:
    sec.by_concept = {
        REVENUE: {"units": {"USD": [
            _annual(2024, 800.0, filed="2025-02-20"),
            _annual(2024, 810.0, form="10-K/A", filed="2025-06-01"),  # amendment wins
            _annual(2025, 860.0, filed="2026-02-19"),
            _fact("2025-07-01", "2025-09-30", 220.0, form="10-Q"),   # a quarter
            _fact("2025-10-01", "2025-12-31", 230.0),                # a 10-K quarter
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            _annual(2024, -12.0), _annual(2025, 0.0),
            _fact("2025-07-01", "2025-09-30", 99.0, form="10-Q")]}},
        "OperatingIncomeLoss": {"units": {"USD": [_annual(2025, -3.5)]}},
    }
    rows = (await svc.market_data_for("RPD", "US"))["financials"].rows
    assert [(r.fiscal_year_end, r.revenue) for r in rows] == [
        ("2024-12-31", 810.0), ("2025-12-31", 860.0)]
    assert rows[0].form == "10-K/A" and rows[0].filed == "2025-06-01"
    assert [r.net_income for r in rows] == [-12.0, 0.0]
    assert [r.operating_income for r in rows] == [None, -3.5]


async def test_revenue_falls_through_to_the_next_concept(eod, sec) -> None:
    sec.by_concept = {"Revenues": {"units": {"USD": [_annual(2025, 42.0)]}}}
    fin = (await svc.market_data_for("RPD", "US"))["financials"]
    assert fin.rows[0].revenue == 42.0
    assert fin.rows[0].source_urls[0].endswith("/us-gaap/Revenues.json")


async def test_the_real_onespan_payload_gives_annual_rows(eod, sec) -> None:
    sec.by_concept = {REVENUE: json.loads(FIXTURE.read_text())}
    rows = (await svc.market_data_for("OSPN", "US"))["financials"].rows
    assert 2 <= len(rows) <= svc.MAX_FISCAL_YEARS
    assert [r.fiscal_year_end for r in rows] == sorted(r.fiscal_year_end for r in rows)
    assert all(r.form.startswith("10-K") and r.revenue > 0 for r in rows)


def test_shared_helper_keeps_the_revenue_pair_unchanged() -> None:
    payload = json.loads(FIXTURE.read_text())
    newer, older = annual_revenue_pair(payload)
    by_end = annual_usd_facts_by_end(payload)
    ends = sorted(by_end)
    assert (newer["_end"].isoformat(), older["_end"].isoformat()) == (ends[-1], ends[-2])
    signed = annual_usd_facts_by_end(
        {"units": {"USD": [_annual(2025, -5.0)]}}, positive_only=False)
    assert signed["2025-12-31"]["_val"] == -5.0
    assert annual_usd_facts_by_end({"units": {"USD": [_annual(2025, -5.0)]}}) == {}


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


async def test_cache_hit_avoids_a_second_fetch(eod, sec) -> None:
    first = await svc.market_data_for("RPD", "US")
    calls, seen = len(eod.calls), len(sec.seen)
    second = await svc.market_data_for("rpd", "NASDAQ")  # same (ticker, exchange) key
    assert len(eod.calls) == calls and len(sec.seen) == seen
    assert second is first


async def test_a_transient_failure_is_not_cached(eod, sec) -> None:
    eod.raise_exc = RuntimeError("boom")
    await svc.market_data_for("RPD", "US")
    eod.raise_exc = None
    again = await svc.market_data_for("RPD", "US")
    assert again["price"].state == "ok" and len(eod.calls) == 2


async def test_cache_expires_after_the_ttl(eod, sec, monkeypatch: pytest.MonkeyPatch) -> None:
    await svc.market_data_for("RPD", "US")
    key = svc._cache_key("RPD", "US")
    stored, payload = svc._CACHE[key]
    svc._CACHE[key] = (stored - svc.CACHE_TTL_SECONDS - 1, payload)
    await svc.market_data_for("RPD", "US")
    assert len(eod.calls) == 2


async def test_a_split_is_charted_on_adjusted_closes(eod, sec) -> None:
    """POWL split 3:1 in March 2026: the raw closes read as a two-thirds crash."""
    eod.rows = [
        {"date": "2025-11-24", "close": 300.0, "adjusted_close": 100.0},
        {"date": "2026-03-02", "close": 540.0, "adjusted_close": 180.0},
        {"date": "2026-03-03", "close": 182.0, "adjusted_close": 182.0},
        {"date": "2026-11-24", "close": 153.88, "adjusted_close": 153.88},
    ]
    price = (await svc.market_data_for("POWL", "US"))["price"]
    assert price.adjusted is True
    assert [p.c for p in price.points] == [100.0, 180.0, 182.0, 153.88]
    assert price.change_pct == 53.88
    assert price.last_close == 153.88


async def test_a_series_without_adjusted_closes_stays_raw(eod, sec) -> None:
    eod.rows = [
        {"date": "2025-11-24", "close": 20.0, "adjusted_close": 10.0},
        {"date": "2026-11-24", "close": 22.0},
    ]
    price = (await svc.market_data_for("RPD", "US"))["price"]
    assert price.adjusted is False
    assert [p.c for p in price.points] == [20.0, 22.0]
    assert price.change_pct == 10.0
