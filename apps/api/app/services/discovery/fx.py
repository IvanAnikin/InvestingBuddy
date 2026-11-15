"""Official exchange rates, for comparing a market cap with a USD size band — V3.19.3.

WHY
===
"Is Kering small-cap?" needs Kering's market cap in the currency the size bands are
declared in. The platform had no FX at all: ``director.thesis`` refused every non-US
listing ("no FX conversion is made"), so a Europe thesis could never test size, and the
discovery council was free to call LVMH small-cap because nothing said otherwise.

WHAT
====
The Federal Reserve's H.10 daily noon buying rates, fetched as CSV from FRED (T2, free, no
key) through the platform's own guarded fetcher — the same path ``macro.commodity_sources``
already uses for FRED. The rate used is the latest observation ON OR BEFORE the market-cap
date, never one after it; the rate, its date, the series and the URL travel with every
conversion. An unmapped currency, or a series that cannot be read, is an unknown — never
a guess and never a stale default.

V3.20 slice 1.2 — ECB REFERENCE RATES BESIDE H.10
=================================================
H.10 has no PLN, CZK, HUF, RON, ISK, TRY, ILS, IDR or PHP, so a Warsaw, Prague or Jakarta
listing could never be sized. The ECB publishes a daily euro reference rate for about thirty
currencies (``data-api.ecb.europa.eu``, SDMX ``EXR/D.<CCY>.EUR.SP00.A``, T2, free, no key).
A USD rate is the ECB's own cross: ``USD-per-EUR ÷ CCY-per-EUR`` on the SAME ECB date, so
both legs come from one publisher and one day.

Order: H.10 first where it has the currency (unchanged behaviour); the ECB when H.10 has no
series, cannot be fetched, or has only a STALE observation.

STALENESS (owner rule, spec §3.1): a rate more than ``STALE_AFTER_BUSINESS_DAYS`` (5)
business days before the date it must price is no rate. With no stated date the reference
is today, allowing for each publisher's own publication lag (H.10 reaches FRED about a week
late; the ECB publishes the same afternoon) — so a dead series can no longer hand back a
months-old observation as "the latest rate".
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.services.macro.commodity_sources import FRED_HOST, fred_url

#: currency → (FRED series, direction). ``usd_per_unit`` series quote USD per one unit of
#: the currency; ``units_per_usd`` series quote the currency per one USD.
FX_SERIES: dict[str, tuple[str, str]] = {
    "EUR": ("DEXUSEU", "usd_per_unit"),
    "GBP": ("DEXUSUK", "usd_per_unit"),
    "AUD": ("DEXUSAL", "usd_per_unit"),
    "NZD": ("DEXUSNZ", "usd_per_unit"),
    "CHF": ("DEXSZUS", "units_per_usd"),
    "DKK": ("DEXDNUS", "units_per_usd"),
    "SEK": ("DEXSDUS", "units_per_usd"),
    "NOK": ("DEXNOUS", "units_per_usd"),
    "CAD": ("DEXCAUS", "units_per_usd"),
    "JPY": ("DEXJPUS", "units_per_usd"),
    "HKD": ("DEXHKUS", "units_per_usd"),
    "CNY": ("DEXCHUS", "units_per_usd"),
    "INR": ("DEXINUS", "units_per_usd"),
    "KRW": ("DEXKOUS", "units_per_usd"),
    "MXN": ("DEXMXUS", "units_per_usd"),
    "BRL": ("DEXBZUS", "units_per_usd"),
    "ZAR": ("DEXSFUS", "units_per_usd"),
    "SGD": ("DEXSIUS", "units_per_usd"),
    "TWD": ("DEXTAUS", "units_per_usd"),
}

#: Minor units quoted by some venues: London prices in pence (GBX / GBp), Johannesburg in
#: cents (ZAc). The amount is converted to the major unit first.
MINOR_UNITS: dict[str, tuple[str, float]] = {
    "GBX": ("GBP", 100.0),
    "GBP_PENCE": ("GBP", 100.0),
    "ZAC": ("ZAR", 100.0),
}

FX_SOURCE_TIER = "T2_regulator_or_gov"
FX_PUBLISHER = "Board of Governors of the Federal Reserve System (H.10), via FRED"

#: ECB euro foreign exchange reference rates (V3.20 slice 1.2): every currency on the
#: ECB's daily list. USD is also the cross leg of every other conversion.
ECB_HOST = "data-api.ecb.europa.eu"
ECB_PUBLISHER = "European Central Bank euro foreign exchange reference rates"
ECB_CURRENCIES: frozenset[str] = frozenset({
    "USD", "JPY", "CZK", "DKK", "HUF", "PLN", "RON", "SEK", "CHF", "ISK", "NOK", "TRY",
    "AUD", "BRL", "CAD", "CNY", "HKD", "IDR", "ILS", "INR", "KRW", "MXN", "MYR", "NZD",
    "PHP", "SGD", "THB", "ZAR", "GBP", "BGN",
})
#: How far back one ECB request reaches. A market cap dated earlier has no ECB rate
#: (H.10 still serves it where it has the currency: the FRED CSV is the full history).
ECB_LOOKBACK_DAYS = 400

#: A rate more than this many business days before the date it prices is stale = unknown.
STALE_AFTER_BUSINESS_DAYS = 5
#: Business days a publisher's latest observation normally trails today. Used only when
#: no date is stated and the reference is "today".
PUBLICATION_LAG_BUSINESS_DAYS: dict[str, int] = {"fred": 7, "ecb": 1}


@dataclass(frozen=True)
class FxRate:
    currency: str
    usd_per_unit: float
    rate_date: str
    series: str | None
    source_url: str | None
    publisher: str = FX_PUBLISHER
    tier: str = FX_SOURCE_TIER

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "usd_per_unit": self.usd_per_unit,
            "rate_date": self.rate_date,
            "series": self.series,
            "source_url": self.source_url,
            "publisher": self.publisher,
            "tier": self.tier,
        }


USD_IDENTITY = FxRate("USD", 1.0, "n/a", None, None, publisher="identity", tier="n/a")


def normalise_currency(code: str | None) -> tuple[str | None, float]:
    """(major currency, divisor) for a quoted currency code. ``(None, 1)`` if unusable."""
    raw = (code or "").strip()
    if not raw:
        return None, 1.0
    if raw in ("GBp", "GBX", "gbx"):
        return "GBP", 100.0
    upper = raw.upper()
    if upper in MINOR_UNITS:
        major, divisor = MINOR_UNITS[upper]
        return major, divisor
    return upper, 1.0


def fx_currency_supported(currency: str | None) -> bool:
    """True when some official publisher (H.10 or the ECB) quotes this currency."""
    major, _ = normalise_currency(currency)
    return major is not None and (
        major in ("USD", "EUR") or major in FX_SERIES or major in ECB_CURRENCIES
    )


# --- staleness ---------------------------------------------------------------------


def business_days_between(earlier: date, later: date) -> int:
    """Weekdays strictly after ``earlier`` up to and including ``later`` (0 if not later).

    Friday → Monday is 1; Friday → Sunday is 0. Public holidays are not modelled: a holiday
    only ever makes a rate look one day older, which errs towards "unknown".
    """
    if later <= earlier:
        return 0
    full_weeks, extra = divmod((later - earlier).days, 7)
    count = full_weeks * 5
    day = earlier + timedelta(days=full_weeks * 7)
    for _ in range(extra):
        day += timedelta(days=1)
        if day.weekday() < 5:
            count += 1
    return count


def is_stale(
    rate_day: date, reference: date, *, allowance: int = STALE_AFTER_BUSINESS_DAYS
) -> bool:
    return business_days_between(rate_day, reference) > allowance


# --- FRED H.10 ---------------------------------------------------------------------


def parse_fred_csv(text: str) -> list[tuple[date, float]]:
    """``(date, value)`` rows from a FRED graph CSV. Missing values (".") are skipped."""
    out: list[tuple[date, float]] = []
    reader = csv.reader(io.StringIO(text or ""))
    for row in reader:
        if len(row) < 2:
            continue
        try:
            day = date.fromisoformat(row[0].strip())
            value = float(row[1])
        except ValueError:
            continue
        if value > 0:
            out.append((day, value))
    out.sort()
    return out


def rate_on_or_before(
    rows: list[tuple[date, float]],
    as_of: date,
    *,
    max_business_days: int = STALE_AFTER_BUSINESS_DAYS,
) -> tuple[date, float] | None:
    """The latest observation on or before ``as_of``, at most ``max_business_days`` older.

    Never an observation AFTER the date: a market cap stated for 30 June is not converted
    at September's rate. An older observation (a dead series, a long gap) is no rate.
    """
    best: tuple[date, float] | None = None
    for day, value in rows:
        if day <= as_of:
            best = (day, value)
        else:
            break
    if best is None or is_stale(best[0], as_of, allowance=max_business_days):
        return None
    return best


def rate_from_rows(
    currency: str,
    rows: list[tuple[date, float]],
    as_of: date,
    *,
    max_business_days: int = STALE_AFTER_BUSINESS_DAYS,
) -> FxRate | None:
    series, direction = FX_SERIES[currency]
    found = rate_on_or_before(rows, as_of, max_business_days=max_business_days)
    if found is None:
        return None
    day, value = found
    usd_per_unit = value if direction == "usd_per_unit" else 1.0 / value
    return FxRate(currency, usd_per_unit, day.isoformat(), series, fred_url(series))


# --- ECB ---------------------------------------------------------------------------


def ecb_series_key(currency: str) -> str:
    return f"EXR/D.{currency}.EUR.SP00.A"


def ecb_url(currency: str, start: date) -> str:
    """One request for the currency AND its USD cross leg."""
    legs = "USD" if currency in ("USD", "EUR") else f"{currency}+USD"
    return (
        f"https://{ECB_HOST}/service/data/EXR/D.{legs}.EUR.SP00.A"
        f"?format=csvdata&startPeriod={start.isoformat()}"
    )


def parse_ecb_csv(text: str) -> dict[str, list[tuple[date, float]]]:
    """``{currency: [(date, units per EUR)]}`` from an ECB SDMX ``csvdata`` response.

    Columns are read by NAME (``CURRENCY``, ``CURRENCY_DENOM``, ``TIME_PERIOD``,
    ``OBS_VALUE``). A row whose denominator is not EUR, or whose value is not a positive
    number, is skipped.
    """
    out: dict[str, list[tuple[date, float]]] = {}
    for row in csv.DictReader(io.StringIO(text or "")):
        try:
            if (row.get("CURRENCY_DENOM") or "").strip() != "EUR":
                continue
            ccy = (row.get("CURRENCY") or "").strip().upper()
            day = date.fromisoformat((row.get("TIME_PERIOD") or "").strip())
            value = float((row.get("OBS_VALUE") or "").strip())
        except (ValueError, AttributeError):
            continue
        if ccy and value > 0:
            out.setdefault(ccy, []).append((day, value))
    for rows in out.values():
        rows.sort()
    return out


def ecb_cross_rows(
    currency: str, by_ccy: dict[str, list[tuple[date, float]]]
) -> list[tuple[date, float]]:
    """``[(date, USD per one currency)]`` = ``USD/EUR ÷ CCY/EUR`` on each SHARED date.

    A cross is only ever formed from two legs the ECB published on the same day.
    """
    usd_rows = dict(by_ccy.get("USD") or [])
    if currency == "EUR":
        return sorted(usd_rows.items())
    ccy_rows = dict(by_ccy.get(currency) or [])
    return sorted(
        (day, usd_rows[day] / ccy_rows[day]) for day in usd_rows.keys() & ccy_rows.keys()
    )


def ecb_series_label(currency: str) -> str:
    if currency == "EUR":
        return ecb_series_key("USD")
    return f"{ecb_series_key('USD')} / {ecb_series_key(currency)}"


def ecb_rate_from_rows(
    currency: str,
    cross_rows: list[tuple[date, float]],
    as_of: date,
    *,
    max_business_days: int = STALE_AFTER_BUSINESS_DAYS,
    url: str | None = None,
) -> FxRate | None:
    """The ECB cross on or before ``as_of``, at most ``max_business_days`` older."""
    found = rate_on_or_before(cross_rows, as_of, max_business_days=max_business_days)
    if found is None:
        return None
    day, usd_per_unit = found
    return FxRate(
        currency, usd_per_unit, day.isoformat(), ecb_series_label(currency), url,
        publisher=ECB_PUBLISHER, tier=FX_SOURCE_TIER,
    )


# --- fetch + choose ------------------------------------------------------------------

#: Per-process cache of parsed series, keyed by (series, UTC day fetched). An hour-old
#: H.10 series is the same series; a re-fetch per candidate would be 25 identical fetches.
#: ECB crosses share it under the key ``("ECB:<CCY>", day)``; ``[]`` = failed today.
_CACHE: dict[tuple[str, str], list[tuple[date, float]]] = {}


def _today() -> date:
    return datetime.now(timezone.utc).date()


async def _fetch_text(url: str, host: str, *, cfg: Any, fetcher: Any) -> str | None:
    from app.services.sources.document_fetcher import safe_fetch_document

    result = await (fetcher or safe_fetch_document)(
        url,
        allowed_domains=(host,),
        cfg=cfg,
        resolve_ip=True,
        extra_text_content_types=("application/csv", "text/csv"),
    )
    content = getattr(result, "content", None) if getattr(result, "ok", False) else None
    return content.decode("utf-8", "replace") if content else None


async def _fred_rate(
    major: str, as_of: date | None, *, cfg: Any, fetcher: Any
) -> tuple[FxRate | None, str]:
    series, _ = FX_SERIES[major]
    key = (series, _today().isoformat())
    rows = _CACHE.get(key)
    if rows == []:
        return None, f"the official FX series {series} could not be fetched today"
    if rows is None:
        try:
            text = await _fetch_text(fred_url(series), FRED_HOST, cfg=cfg, fetcher=fetcher)
        except Exception as exc:  # noqa: BLE001 - an FX fetch failure is an unknown
            return None, (
                f"the official FX series {series} could not be fetched "
                f"({type(exc).__name__})"
            )
        if not text:
            _CACHE[key] = []  # negative cache: one failed fetch per series per day
            return None, f"the official FX series {series} could not be fetched"
        rows = parse_fred_csv(text)
        _CACHE[key] = rows
    if not rows:
        return None, f"the official FX series {series} has no observations"
    if as_of is None:
        # No stated date: the latest published rate — but never a dead series' last value.
        latest = rows[-1][0]
        allowance = STALE_AFTER_BUSINESS_DAYS + PUBLICATION_LAG_BUSINESS_DAYS["fred"]
        if is_stale(latest, _today(), allowance=allowance):
            return None, f"the latest {series} observation ({latest.isoformat()}) is stale"
        as_of = latest
    rate = rate_from_rows(major, rows, as_of)
    if rate is None:
        return None, (
            f"no {series} observation within {STALE_AFTER_BUSINESS_DAYS} business days "
            f"before {as_of.isoformat()}"
        )
    return rate, ""


async def _ecb_rate(
    major: str, as_of: date | None, *, cfg: Any, fetcher: Any
) -> tuple[FxRate | None, str]:
    today = _today()
    key = (f"ECB:{major}", today.isoformat())
    url = ecb_url(major, today - timedelta(days=ECB_LOOKBACK_DAYS))
    rows = _CACHE.get(key)
    if rows is None:
        try:
            text = await _fetch_text(url, ECB_HOST, cfg=cfg, fetcher=fetcher)
        except Exception as exc:  # noqa: BLE001 - an FX fetch failure is an unknown
            return None, (
                f"the ECB reference rate for {major} could not be fetched "
                f"({type(exc).__name__})"
            )
        rows = ecb_cross_rows(major, parse_ecb_csv(text)) if text else []
        _CACHE[key] = rows
    if not rows:
        return None, f"the ECB reference rate for {major} could not be fetched today"
    if as_of is None:
        reference = today
        allowance = STALE_AFTER_BUSINESS_DAYS + PUBLICATION_LAG_BUSINESS_DAYS["ecb"]
    else:
        reference, allowance = as_of, STALE_AFTER_BUSINESS_DAYS
    rate = ecb_rate_from_rows(major, rows, reference, max_business_days=allowance, url=url)
    if rate is None:
        return None, (
            f"no ECB reference rate for {major} within {allowance} business days "
            f"before {reference.isoformat()}"
        )
    return rate, ""


async def usd_rate(
    currency: str | None,
    as_of: date | None = None,
    *,
    cfg: Any = None,
    fetcher: Any = None,
) -> tuple[FxRate | None, str | None]:
    """``(rate, None)`` or ``(None, reason)``. Never raises.

    H.10 first where it has a series; the ECB when H.10 has none, cannot be fetched, or
    has only a stale observation. Both failing is one unknown naming both reasons.
    """
    major, _divisor = normalise_currency(currency)
    if major is None:
        return None, "no currency is stated for the amount"
    if major == "USD":
        return USD_IDENTITY, None
    in_fred = major in FX_SERIES
    in_ecb = major == "EUR" or major in ECB_CURRENCIES
    if not in_fred and not in_ecb:
        return None, f"no official FX series is configured for {major}"
    reasons: list[str] = []
    if in_fred:
        rate, reason = await _fred_rate(major, as_of, cfg=cfg, fetcher=fetcher)
        if rate is not None:
            return rate, None
        reasons.append(reason)
    if in_ecb:
        rate, reason = await _ecb_rate(major, as_of, cfg=cfg, fetcher=fetcher)
        if rate is not None:
            return rate, None
        reasons.append(reason)
    return None, "; ".join(r for r in reasons if r)


def to_usd(amount: float, currency: str | None, rate: FxRate) -> float:
    """Convert a quoted amount (possibly in a minor unit) to USD with ``rate``."""
    _major, divisor = normalise_currency(currency)
    return amount / divisor * rate.usd_per_unit


__all__ = [
    "ECB_CURRENCIES",
    "ECB_HOST",
    "FX_SERIES",
    "FxRate",
    "STALE_AFTER_BUSINESS_DAYS",
    "USD_IDENTITY",
    "business_days_between",
    "ecb_cross_rows",
    "ecb_rate_from_rows",
    "ecb_url",
    "fx_currency_supported",
    "is_stale",
    "normalise_currency",
    "parse_ecb_csv",
    "parse_fred_csv",
    "rate_from_rows",
    "rate_on_or_before",
    "to_usd",
    "usd_rate",
]
