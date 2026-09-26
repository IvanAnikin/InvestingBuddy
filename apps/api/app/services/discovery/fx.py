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
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timezone
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
    rows: list[tuple[date, float]], as_of: date, *, max_gap_days: int = 10
) -> tuple[date, float] | None:
    """The latest observation on or before ``as_of``, within ``max_gap_days``.

    Never an observation AFTER the date: a market cap stated for 30 June is not converted
    at September's rate. A gap longer than ``max_gap_days`` (a dead series) is no rate.
    """
    best: tuple[date, float] | None = None
    for day, value in rows:
        if day <= as_of:
            best = (day, value)
        else:
            break
    if best is None or (as_of - best[0]).days > max_gap_days:
        return None
    return best


def rate_from_rows(
    currency: str, rows: list[tuple[date, float]], as_of: date
) -> FxRate | None:
    series, direction = FX_SERIES[currency]
    found = rate_on_or_before(rows, as_of)
    if found is None:
        return None
    day, value = found
    usd_per_unit = value if direction == "usd_per_unit" else 1.0 / value
    return FxRate(currency, usd_per_unit, day.isoformat(), series, fred_url(series))


#: Per-process cache of parsed series, keyed by (series, UTC day fetched). An hour-old
#: H.10 series is the same series; a re-fetch per candidate would be 25 identical fetches.
_CACHE: dict[tuple[str, str], list[tuple[date, float]]] = {}


async def usd_rate(
    currency: str | None,
    as_of: date | None = None,
    *,
    cfg: Any = None,
    fetcher: Any = None,
) -> tuple[FxRate | None, str | None]:
    """``(rate, None)`` or ``(None, reason)``. Never raises."""
    major, _divisor = normalise_currency(currency)
    if major is None:
        return None, "no currency is stated for the amount"
    if major == "USD":
        return USD_IDENTITY, None
    if major not in FX_SERIES:
        return None, f"no official FX series is configured for {major}"
    as_of = as_of or datetime.now(timezone.utc).date()
    series, _ = FX_SERIES[major]
    key = (series, datetime.now(timezone.utc).date().isoformat())
    rows = _CACHE.get(key)
    if rows is None:
        from app.services.sources.document_fetcher import safe_fetch_document

        try:
            result = await (fetcher or safe_fetch_document)(
                fred_url(series),
                allowed_domains=(FRED_HOST,),
                cfg=cfg,
                resolve_ip=True,
                extra_text_content_types=("application/csv", "text/csv"),
            )
        except Exception as exc:  # noqa: BLE001 - an FX fetch failure is an unknown
            return None, (
                f"the official FX series {series} could not be fetched "
                f"({type(exc).__name__})"
            )
        content = getattr(result, "content", None) if getattr(result, "ok", False) else None
        if not content:
            return None, f"the official FX series {series} could not be fetched"
        rows = parse_fred_csv(content.decode("utf-8", "replace"))
        _CACHE[key] = rows
    rate = rate_from_rows(major, rows, as_of)
    if rate is None:
        return None, f"no {series} observation within 10 days before {as_of.isoformat()}"
    return rate, None


def to_usd(amount: float, currency: str | None, rate: FxRate) -> float:
    """Convert a quoted amount (possibly in a minor unit) to USD with ``rate``."""
    _major, divisor = normalise_currency(currency)
    return amount / divisor * rate.usd_per_unit


__all__ = [
    "FX_SERIES",
    "FxRate",
    "USD_IDENTITY",
    "normalise_currency",
    "parse_fred_csv",
    "rate_from_rows",
    "rate_on_or_before",
    "to_usd",
    "usd_rate",
]
