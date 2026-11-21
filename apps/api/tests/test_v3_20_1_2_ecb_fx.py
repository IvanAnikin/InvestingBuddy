"""V3.20 slice 1.2 — ECB reference rates beside FRED H.10, and the staleness rule.

* the ECB cross is USD/EUR ÷ CCY/EUR on ONE shared date, never two different days;
* H.10 stays first where it has the currency; the ECB covers what H.10 lacks
  (PLN, CZK, HUF, …) and steps in when H.10 is unreachable or stale;
* a rate more than 5 business days before the date it prices is an unknown;
* with no stated date, a dead series' last observation is no longer "the latest rate".
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.services.discovery import fx
from app.services.discovery.fx import (
    ECB_HOST,
    STALE_AFTER_BUSINESS_DAYS,
    business_days_between,
    ecb_cross_rows,
    ecb_rate_from_rows,
    ecb_url,
    fx_currency_supported,
    is_stale,
    parse_ecb_csv,
    rate_on_or_before,
    usd_rate,
)

HEADER = (
    "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS,"
    "OBS_CONF,TITLE\n"
)


def _row(ccy: str, day: str, value: str, denom: str = "EUR") -> str:
    return f"EXR.D.{ccy}.{denom}.SP00.A,D,{ccy},{denom},SP00,A,{day},{value},A,F,\"{ccy}/Euro\"\n"


#: USD 1.16 per EUR on the 12th and 13th; PLN 4.32 / 4.35 per EUR. The 11th has PLN only.
ECB_CSV = HEADER + "".join([
    _row("PLN", "2026-11-11", "4.30"),
    _row("PLN", "2026-11-12", "4.32"),
    _row("PLN", "2026-11-13", "4.35"),
    _row("USD", "2026-11-12", "1.16"),
    _row("USD", "2026-11-13", "1.1745"),
])


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    fx._CACHE.clear()
    monkeypatch.setattr(fx, "_today", lambda: date(2026, 11, 15))  # a Sunday
    yield
    fx._CACHE.clear()


def _fetcher(pages: dict[str, str], calls: list[str] | None = None):
    async def fetch(url, **kw):
        if calls is not None:
            calls.append(url)
        for prefix, body in pages.items():
            if url.startswith(prefix):
                assert kw["allowed_domains"] == ((ECB_HOST,) if ECB_HOST in url else (fx.FRED_HOST,))
                return SimpleNamespace(ok=True, content=body.encode())
        return SimpleNamespace(ok=False, content=None)

    return fetch


ECB_PREFIX = f"https://{ECB_HOST}/"


# --- business days -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("earlier", "later", "expected"),
    [
        ("2026-11-13", "2026-11-13", 0),  # same day
        ("2026-11-13", "2026-11-15", 0),  # Fri → Sun
        ("2026-11-13", "2026-11-16", 1),  # Fri → Mon
        ("2026-11-09", "2026-11-13", 4),  # Mon → Fri
        ("2026-11-06", "2026-11-13", 5),  # Fri → Fri
        ("2026-11-05", "2026-11-13", 6),  # Thu → next Fri
        ("2026-10-30", "2026-11-13", 10),
        ("2026-11-13", "2026-11-12", 0),  # later is earlier
    ],
)
def test_business_days_between(earlier, later, expected):
    assert business_days_between(date.fromisoformat(earlier), date.fromisoformat(later)) == expected


def test_five_business_days_is_fresh_six_is_stale():
    assert not is_stale(date(2026, 11, 6), date(2026, 11, 13))
    assert is_stale(date(2026, 11, 5), date(2026, 11, 13))
    assert STALE_AFTER_BUSINESS_DAYS == 5


def test_rate_on_or_before_uses_business_days():
    rows = [(date(2026, 11, 5), 1.0)]
    assert rate_on_or_before(rows, date(2026, 11, 12)) is not None  # 5 bd
    assert rate_on_or_before(rows, date(2026, 11, 13)) is None  # 6 bd


# --- ECB parsing and the cross -------------------------------------------------------


def test_parse_ecb_csv_by_column_name():
    by_ccy = parse_ecb_csv(ECB_CSV)
    assert by_ccy["USD"] == [(date(2026, 11, 12), 1.16), (date(2026, 11, 13), 1.1745)]
    assert len(by_ccy["PLN"]) == 3


def test_parse_ecb_csv_skips_bad_rows():
    text = HEADER + _row("PLN", "2026-11-12", "NaN") + _row("PLN", "2026-11-13", "") \
        + _row("PLN", "2026-11-13", "4.3", denom="USD") + _row("PLN", "bad", "4.3") \
        + _row("PLN", "2026-11-12", "-1")
    assert parse_ecb_csv(text) == {}
    assert parse_ecb_csv("") == {}
    assert parse_ecb_csv("<html>error</html>") == {}


def test_cross_is_formed_only_on_shared_dates():
    rows = ecb_cross_rows("PLN", parse_ecb_csv(ECB_CSV))
    # 11 Nov has no USD leg — no cross for it.
    assert [d for d, _ in rows] == [date(2026, 11, 12), date(2026, 11, 13)]
    assert rows[-1][1] == pytest.approx(1.1745 / 4.35)


def test_eur_cross_is_the_usd_leg():
    rows = ecb_cross_rows("EUR", parse_ecb_csv(ECB_CSV))
    assert rows[-1] == (date(2026, 11, 13), 1.1745)


def test_ecb_rate_names_its_publisher_and_both_series():
    rows = ecb_cross_rows("PLN", parse_ecb_csv(ECB_CSV))
    rate = ecb_rate_from_rows("PLN", rows, date(2026, 11, 13), url="u")
    assert rate is not None
    assert rate.rate_date == "2026-11-13"
    assert rate.publisher == fx.ECB_PUBLISHER
    assert rate.tier == fx.FX_SOURCE_TIER
    assert "EXR/D.USD.EUR.SP00.A" in rate.series and "EXR/D.PLN.EUR.SP00.A" in rate.series
    # never a rate after the date
    assert ecb_rate_from_rows("PLN", rows, date(2026, 11, 12)).rate_date == "2026-11-12"
    assert ecb_rate_from_rows("PLN", rows, date(2026, 11, 11)) is None


def test_ecb_url_asks_for_both_legs():
    url = ecb_url("PLN", date(2025, 10, 11))
    assert url.startswith(f"https://{ECB_HOST}/service/data/EXR/D.PLN+USD.EUR.SP00.A?")
    assert "format=csvdata" in url and "startPeriod=2025-10-11" in url
    assert "D.USD.EUR" in ecb_url("EUR", date(2025, 10, 11))


@pytest.mark.parametrize("ccy", ["PLN", "CZK", "HUF", "RON", "ISK", "TRY", "ILS", "IDR", "PHP",
                                 "MYR", "THB", "GBP", "AUD", "SEK", "CHF", "NOK", "EUR", "USD"])
def test_supported_currencies(ccy):
    assert fx_currency_supported(ccy)


def test_unsupported_currency():
    assert not fx_currency_supported("XYZ")
    assert not fx_currency_supported(None)


# --- usd_rate: source order ----------------------------------------------------------


async def test_pln_is_served_by_the_ecb():
    calls: list[str] = []
    rate, reason = await usd_rate("PLN", date(2026, 11, 13),
                                  fetcher=_fetcher({ECB_PREFIX: ECB_CSV}, calls))
    assert reason is None and rate is not None
    assert rate.usd_per_unit == pytest.approx(1.1745 / 4.35)
    assert rate.publisher == fx.ECB_PUBLISHER
    assert len(calls) == 1 and calls[0].startswith(ECB_PREFIX)
    # cached for the day
    await usd_rate("PLN", date(2026, 11, 12), fetcher=_fetcher({ECB_PREFIX: ECB_CSV}, calls))
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("ccy", "series", "fred_value", "ecb_value", "expected"),
    [
        ("GBP", "DEXUSUK", "1.31", "0.8800", 1.31),
        ("AUD", "DEXUSAL", "0.66", "1.7700", 0.66),
        ("SEK", "DEXSDUS", "10.50", "11.10", 1 / 10.50),
        ("CHF", "DEXSZUS", "0.80", "0.9300", 1 / 0.80),
        ("NOK", "DEXNOUS", "11.00", "11.70", 1 / 11.00),
    ],
)
async def test_h10_stays_first_where_it_has_the_currency(ccy, series, fred_value, ecb_value, expected):
    fred = f"observation_date,{series}\n2026-11-13,{fred_value}\n"
    ecb = HEADER + _row(ccy, "2026-11-13", ecb_value) + _row("USD", "2026-11-13", "1.1745")
    calls: list[str] = []
    rate, _ = await usd_rate(ccy, date(2026, 11, 13), fetcher=_fetcher(
        {"https://fred.stlouisfed.org/": fred, ECB_PREFIX: ecb}, calls))
    assert rate.usd_per_unit == pytest.approx(expected)
    assert rate.series == series
    assert not any(c.startswith(ECB_PREFIX) for c in calls)


@pytest.mark.parametrize(
    ("ccy", "ecb_value"),
    [("GBP", "0.8800"), ("AUD", "1.7700"), ("SEK", "11.10"), ("CHF", "0.9300"), ("NOK", "11.70")],
)
async def test_ecb_steps_in_when_h10_is_unreachable(ccy, ecb_value):
    ecb = HEADER + _row(ccy, "2026-11-13", ecb_value) + _row("USD", "2026-11-13", "1.1745")
    rate, reason = await usd_rate(ccy, date(2026, 11, 13), fetcher=_fetcher({ECB_PREFIX: ecb}))
    assert reason is None
    assert rate.usd_per_unit == pytest.approx(1.1745 / float(ecb_value))
    assert rate.publisher == fx.ECB_PUBLISHER


async def test_ecb_steps_in_when_h10_is_stale():
    fred = "observation_date,DEXUSUK\n2026-10-30,1.29\n"  # 10 business days before
    ecb = HEADER + _row("GBP", "2026-11-13", "0.88") + _row("USD", "2026-11-13", "1.1745")
    rate, _ = await usd_rate("GBP", date(2026, 11, 13), fetcher=_fetcher(
        {"https://fred.stlouisfed.org/": fred, ECB_PREFIX: ecb}))
    assert rate.publisher == fx.ECB_PUBLISHER
    assert rate.rate_date == "2026-11-13"


async def test_both_stale_is_unknown_naming_both_reasons():
    fred = "observation_date,DEXUSUK\n2026-10-30,1.29\n"
    ecb = HEADER + _row("GBP", "2026-10-30", "0.88") + _row("USD", "2026-10-30", "1.17")
    rate, reason = await usd_rate("GBP", date(2026, 11, 13), fetcher=_fetcher(
        {"https://fred.stlouisfed.org/": fred, ECB_PREFIX: ecb}))
    assert rate is None
    assert "DEXUSUK" in reason and "ECB" in reason


async def test_ecb_outage_for_an_ecb_only_currency_is_unknown():
    rate, reason = await usd_rate("CZK", date(2026, 11, 13), fetcher=_fetcher({}))
    assert rate is None and "ECB" in reason
    # the failure is cached for the day: no second request
    calls: list[str] = []
    await usd_rate("CZK", date(2026, 11, 13), fetcher=_fetcher({}, calls))
    assert calls == []


async def test_fetcher_exception_is_unknown_not_raised():
    async def boom(url, **kw):
        raise TimeoutError

    rate, reason = await usd_rate("HUF", date(2026, 11, 13), fetcher=boom)
    assert rate is None and "TimeoutError" in reason


# --- usd_rate: no stated date --------------------------------------------------------


async def test_latest_rate_is_used_when_no_date_is_stated():
    rate, _ = await usd_rate("PLN", None, fetcher=_fetcher({ECB_PREFIX: ECB_CSV}))
    assert rate.rate_date == "2026-11-13"


async def test_dead_series_last_value_is_not_the_latest_rate():
    """Before 1.2 a series that stopped months ago still answered "latest"."""
    fred = "observation_date,DEXUSUK\n2026-06-30,1.27\n"
    rate, reason = await usd_rate("GBP", None, fetcher=_fetcher({"https://fred.stlouisfed.org/": fred}))
    assert rate is None and "stale" in reason


async def test_h10_publication_lag_is_allowed_with_no_stated_date():
    # H.10 a week behind (5 bd + 7 bd lag allowance) is still the latest H.10 rate.
    fred = "observation_date,DEXUSUK\n2026-11-04,1.30\n"
    rate, _ = await usd_rate("GBP", None, fetcher=_fetcher({"https://fred.stlouisfed.org/": fred}))
    assert rate is not None and rate.series == "DEXUSUK"


async def test_ecb_latest_rate_must_be_recent():
    old = HEADER + _row("PLN", "2026-10-30", "4.3") + _row("USD", "2026-10-30", "1.17")
    rate, reason = await usd_rate("PLN", None, fetcher=_fetcher({ECB_PREFIX: old}))
    assert rate is None and "ECB" in reason


# --- size verification end to end ------------------------------------------------------


async def test_a_warsaw_listing_can_now_be_sized():
    from app.services.discovery import constraints as cons
    from app.services.discovery.intent import HARD, Constraint

    rate, _ = await usd_rate("PLN", date(2026, 11, 13), fetcher=_fetcher({ECB_PREFIX: ECB_CSV}))
    obs = cons.MarketCapObservation(
        amount=6.0e9, currency="PLN", as_of="2026-11-13", as_of_basis="stated",
        source_url="https://www.gpw.pl/x", source_tier="T1", verified=True,
        method="exchange_directory",
    )
    size = Constraint("size", ("small_cap",), HARD, "mandate", "< 2bn", max_usd=2e9)
    result = cons.verify_size(size, obs, rate)
    assert result.status == cons.PASS  # PLN 6.0bn ≈ USD 1.62bn
    assert result.value["usd"] == pytest.approx(6.0e9 * 1.1745 / 4.35)


def test_lookback_reaches_a_year():
    assert fx.ECB_LOOKBACK_DAYS >= 366
    assert date(2026, 11, 15) - timedelta(days=fx.ECB_LOOKBACK_DAYS) < date(2025, 11, 15)
