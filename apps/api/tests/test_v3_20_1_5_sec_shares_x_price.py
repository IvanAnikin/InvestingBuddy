"""V3.20 slice 1.5 — a US filer's size from its cover-page shares × a dated close.

Live finding 2026-11-22 (staging, thesis run e7bd3ebf): ASUR, SMSI and EGAN — public floats
of USD 0.25bn, 0.01bn and 0.18bn — were all listed "size unverified" and never shortlisted
under the USD 2bn mandate. The only official figure for a US filer was the 10-K public
float, a LOWER bound, which can never place a company under a ceiling. Without a web page
stating the cap, every US small cap failed the mandate it was built for.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.services.discovery import constraints as cons
from app.services.discovery.directories import (
    MAX_COVER_SHARES_AGE_DAYS,
    MAX_PRICE_AGE_DAYS,
    sec_shares_x_price,
)
from app.services.discovery.fx import USD_IDENTITY, FxRate
from app.services.discovery.intent import Constraint, buckets_below
from app.services.sources.document_fetcher import DocumentFetchResult

TODAY = date(2026, 11, 22)
SHARES_URL = ("https://data.sec.gov/api/xbrl/companyconcept/CIK0000884144/dei/"
              "EntityCommonStockSharesOutstanding.json")
FLOAT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK0000884144/dei/EntityPublicFloat.json"
PRICE_URL = "https://eodhd.com/api/eod/ASUR.US"


def _cover(*rows: dict) -> dict:
    return {"units": {"shares": list(rows)}}


def _row(val: float, end: str, form: str = "10-Q", accn: str = "0000884144-26-000010",
         filed: str = "2026-11-05") -> dict:
    return {"val": val, "end": end, "form": form, "accn": accn, "filed": filed}


def _fetcher(bodies: dict[str, dict]):
    seen: list[dict] = []

    async def _fetch(url, **kw):  # noqa: ANN001, ANN202
        seen.append({"url": url, **kw})
        body = bodies.get(url)
        if body is None:
            return DocumentFetchResult(requested_url=url, final_url=url, status_code=404,
                                       content_type="application/json", document_type="html",
                                       content=b"")
        return DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                                   content_type="application/json", document_type="html",
                                   content=json.dumps(body).encode())

    _fetch.seen = seen  # type: ignore[attr-defined]
    return _fetch


def _price(close: float = 9.10, on: date = TODAY - timedelta(days=2)):
    async def _load(ticker, *, today=None):  # noqa: ANN001, ANN202
        assert ticker == "ASUR"
        return close, on.isoformat(), PRICE_URL, "T5_api_aggregator"

    return _load


async def test_cover_shares_times_a_dated_close_is_the_market_cap():
    fetch = _fetcher({SHARES_URL: _cover(_row(27_100_000, "2026-10-31"),
                                         _row(26_000_000, "2026-07-31"))})
    figure, basis = await sec_shares_x_price("884144", "ASUR", fetcher=fetch,
                                             price_loader=_price(), today=TODAY)
    assert figure is not None
    assert figure.amount == pytest.approx(27_100_000 * 9.10)
    assert (figure.shares_as_of, figure.shares_form, figure.price_date) == (
        "2026-10-31", "10-Q", "2026-11-20")
    assert figure.shares_url == SHARES_URL and figure.price_url == PRICE_URL
    assert "27,100,000" in basis and "9.1 USD on 2026-11-20" in basis
    # The SEC read is pinned to data.sec.gov.
    assert fetch.seen[0]["allowed_domains"] == ("data.sec.gov",)
    parts = figure.components()
    assert parts["shares_concept"] == "dei:EntityCommonStockSharesOutstanding"
    assert parts["price_source_tier"] == "T5_api_aggregator"


@pytest.mark.parametrize(
    ("rows", "price", "reason_part"),
    [
        # A foreign private issuer's cover counts ordinary shares, not its US ADS line.
        ([_row(1e9, "2026-06-30", form="20-F")], _price(), "20-F"),
        # One class's price does not value an unlisted second class.
        ([_row(20e6, "2026-10-31", accn="a"), _row(5e6, "2026-10-31", accn="a")], _price(),
         "2 share classes"),
        # A lapsed filer's share count is not today's.
        ([_row(27e6, (TODAY - timedelta(days=MAX_COVER_SHARES_AGE_DAYS + 1)).isoformat())],
         _price(), "dated"),
        # A stale close is not today's price.
        ([_row(27e6, "2026-10-31")],
         _price(on=TODAY - timedelta(days=MAX_PRICE_AGE_DAYS + 1)), "close price is dated"),
        ([_row(27e6, "2026-10-31")], None, "no recent close"),
        ([], _price(), "no cover-page share count"),
    ],
)
async def test_what_cannot_be_multiplied_is_refused_with_a_reason(rows, price, reason_part):
    async def _no_price(ticker, *, today=None):  # noqa: ANN001, ANN202
        return None

    fetch = _fetcher({SHARES_URL: _cover(*rows)})
    figure, reason = await sec_shares_x_price("884144", "ASUR", fetcher=fetch,
                                              price_loader=price or _no_price, today=TODAY)
    assert figure is None
    assert reason_part in reason


async def test_an_unreadable_cover_or_a_bad_cik_is_a_reason_not_an_exception():
    async def _boom(url, **kw):  # noqa: ANN001, ANN202
        raise RuntimeError("network")

    assert (await sec_shares_x_price("884144", "ASUR", fetcher=_boom,
                                     price_loader=_price(), today=TODAY))[0] is None
    assert (await sec_shares_x_price("not-a-cik", "ASUR", fetcher=_boom,
                                     price_loader=_price(), today=TODAY))[0] is None
    assert (await sec_shares_x_price("884144", None, fetcher=_boom,
                                     price_loader=_price(), today=TODAY))[0] is None


def _usd() -> FxRate:
    return USD_IDENTITY


def _small_cap_under_2bn() -> Constraint:
    return Constraint("size", buckets_below(2e9), cons.HARD, "mandate", "< 2bn", max_usd=2e9)


def test_a_computed_cap_under_the_ceiling_passes_and_cites_both_figures():
    observation = cons.MarketCapObservation(
        amount=27_100_000 * 9.10 * 2, currency="USD", as_of="2026-11-20",
        as_of_basis="stated", source_url=SHARES_URL, source_tier="T1_primary_filing",
        verified=True, method=cons.SHARES_X_PRICE_METHOD,
        components={"shares": 54_200_000, "shares_as_of": "2026-10-31", "price": 9.1,
                    "price_date": "2026-11-20", "price_source_url": PRICE_URL,
                    "price_source_tier": "T5_api_aggregator"},
    )
    result = cons.verify_size(_small_cap_under_2bn(), observation, _usd())
    assert result.status == cons.PASS
    assert "cover-page shares 54,200,000" in result.basis
    assert [s["url"] for s in result.sources] == [SHARES_URL, PRICE_URL]
    assert result.value["components"]["price_date"] == "2026-11-20"
    assert result.value["method"] == cons.SHARES_X_PRICE_METHOD


def test_a_float_floor_alone_still_cannot_pass_a_ceiling():
    floor = cons.MarketCapObservation(
        amount=250e6, currency="USD", as_of="2025-06-30", as_of_basis="stated",
        source_url=FLOAT_URL, source_tier="T1_primary_filing", verified=True,
        method=cons.FLOAT_FLOOR_METHOD,
    )
    assert cons.verify_size(_small_cap_under_2bn(), floor, _usd()).status == cons.UNKNOWN


def test_an_observation_without_components_serialises_as_before():
    obs = cons.MarketCapObservation(amount=1.0, currency="USD", as_of=None,
                                    as_of_basis="fetch_date", source_url=None,
                                    source_tier=None, verified=True)
    assert "components" not in obs.to_dict()


def _sec_identity() -> SimpleNamespace:
    from app.services.discovery.directories import DirectoryListing

    listing = DirectoryListing(
        name="ASURE SOFTWARE INC", ticker="ASUR", exchange="US", isin=None,
        country="United States", market_cap=None, currency=None, price=None, industry=None,
        mic=None, directory="sec", source_url="https://www.sec.gov/files/company_tickers_exchange.json",
        tier="T1_primary_filing", as_of=TODAY.isoformat(), cik="884144",
    )
    return SimpleNamespace(directory_listing=listing.to_dict())


async def test_official_cap_prefers_the_computed_figure_to_the_float_floor(monkeypatch):
    from app.services.discovery import directories, pipeline

    async def _figure(cik, ticker, **kw):  # noqa: ANN001, ANN202
        return directories.SharesTimesPrice(
            amount=246_610_000.0, shares=27_100_000, shares_as_of="2026-10-31",
            shares_form="10-Q", shares_url=SHARES_URL, price=9.1, price_date="2026-11-20",
            price_url=PRICE_URL, price_tier="T5_api_aggregator"), "basis"

    async def _floor(cik, **kw):  # noqa: ANN001, ANN202
        return 250e6, "2025-06-30", FLOAT_URL

    monkeypatch.setattr(directories, "sec_shares_x_price", _figure)
    monkeypatch.setattr(directories, "sec_public_float", _floor)
    cfg = SimpleNamespace(v3_discovery_shares_x_price_enabled=True)
    obs = await pipeline._official_cap(_sec_identity(), cfg=cfg, fetcher=None)
    assert obs.method == cons.SHARES_X_PRICE_METHOD
    assert obs.amount == pytest.approx(246_610_000.0)
    assert obs.as_of == "2026-11-20" and obs.components["price_source_url"] == PRICE_URL
    assert cons.verify_size(_small_cap_under_2bn(), obs, _usd()).status == cons.PASS

    # Flag off: the float floor only, exactly as before.
    off = await pipeline._official_cap(
        _sec_identity(), cfg=SimpleNamespace(v3_discovery_shares_x_price_enabled=False),
        fetcher=None)
    assert off.method == cons.FLOAT_FLOOR_METHOD

    # No computable figure: the float floor still answers.
    async def _none(cik, ticker, **kw):  # noqa: ANN001, ANN202
        return None, "no recent close price for the US listing"

    monkeypatch.setattr(directories, "sec_shares_x_price", _none)
    fallback = await pipeline._official_cap(_sec_identity(), cfg=cfg, fetcher=None)
    assert fallback.method == cons.FLOAT_FLOOR_METHOD


async def test_eodhd_last_close_without_a_key_is_none(monkeypatch):
    from app.services.discovery.directories import eodhd_last_close

    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    assert await eodhd_last_close("ASUR", today=TODAY) is None
