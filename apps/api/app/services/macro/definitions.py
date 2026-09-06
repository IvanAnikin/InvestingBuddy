"""Macro series vocabulary and period keys — V3.4 Slice 4.7.

A DELIBERATELY SEPARATE PERIOD VOCABULARY
=========================================
``financial_period.ReportingPeriod`` is not reused here, and that is a decision rather
than an oversight. An issuer's fiscal half has no statistical analogue, a statistical
month has no fiscal one, and a *shared* vocabulary would invite exactly the comparison
that must never happen: an issuer's FY2025 against a calendar 2025 macro reading, as
though they covered the same twelve months. Richemont's FY2025 ends in March.

So a macro period is its own type, its keys are visibly different (``2025-07`` is a
month; ``2025-Q2`` is a calendar quarter), and a test asserts neither vocabulary can
parse the other's keys into something that looks valid.

PERIOD KEYS SORT AS TEXT
========================
Zero-padded on purpose. ``2025-07`` before ``2025-10`` is true as a string and as a date,
which means a series can be ordered in SQL without a date column being correct first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

FREQ_ANNUAL = "annual"
FREQ_QUARTERLY = "quarterly"
FREQ_MONTHLY = "monthly"
FREQ_WEEKLY = "weekly"
FREQ_DAILY = "daily"

FREQUENCIES: tuple[str, ...] = (
    FREQ_ANNUAL,
    FREQ_QUARTERLY,
    FREQ_MONTHLY,
    FREQ_WEEKLY,
    FREQ_DAILY,
)

#: ``sa`` = seasonally adjusted, ``nsa`` = not adjusted. NULL/None means the publisher
#: did not say — which is never read as ``nsa``, because reading "not stated" as "not
#: adjusted" turns a seasonal artefact into a trend.
ADJUSTMENT_SEASONAL = "sa"
ADJUSTMENT_NONE = "nsa"
ADJUSTMENTS: tuple[str, ...] = (ADJUSTMENT_SEASONAL, ADJUSTMENT_NONE)

_ANNUAL_RE = re.compile(r"^(\d{4})$")
_QUARTER_RE = re.compile(r"^(\d{4})-Q([1-4])$")
_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_DAY_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


class UnknownMacroPeriodError(ValueError):
    """Raised for a period key no frequency recognises. Never coerced."""


@dataclass(frozen=True)
class MacroPeriod:
    """One statistical period. Unknown is refused, never guessed."""

    key: str
    frequency: str
    start: date
    end: date

    @property
    def year(self) -> int:
        return self.start.year


def _quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    start_month = 3 * (quarter - 1) + 1
    end_month = start_month + 2
    last_day = _last_day_of(year, end_month)
    return date(year, start_month, 1), date(year, end_month, last_day)


def _last_day_of(year: int, month: int) -> int:
    if month == 12:
        return 31
    following = date(year + (month // 12), (month % 12) + 1, 1)
    return (following - date(year, month, 1)).days


def parse_macro_period(key: str | None) -> MacroPeriod:
    """Interpret a macro period key. **Raises** on anything unrecognised.

    Raising rather than returning an unknown sentinel is the opposite of what
    ``financial_period.parse_period`` does, and for a reason: a financial period arrives
    from a *document*, where "the period is not stated" is a real and common state that
    the platform must be able to carry. A macro period key arrives from **this codebase**
    — a connector built it from a publisher's structured response — so an unrecognised
    one is a bug in the connector, and swallowing it would store an observation nothing
    can order.
    """
    text = (key or "").strip()
    if match := _ANNUAL_RE.match(text):
        year = int(match.group(1))
        return MacroPeriod(text, FREQ_ANNUAL, date(year, 1, 1), date(year, 12, 31))
    if match := _QUARTER_RE.match(text):
        year, quarter = int(match.group(1)), int(match.group(2))
        start, end = _quarter_bounds(year, quarter)
        return MacroPeriod(text, FREQ_QUARTERLY, start, end)
    if match := _MONTH_RE.match(text):
        year, month = int(match.group(1)), int(match.group(2))
        return MacroPeriod(
            text,
            FREQ_MONTHLY,
            date(year, month, 1),
            date(year, month, _last_day_of(year, month)),
        )
    if match := _DAY_RE.match(text):
        day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return MacroPeriod(text, FREQ_DAILY, day, day)
    raise UnknownMacroPeriodError(
        f"{key!r} is not a macro period key. A connector built this string from a "
        "publisher's response, so an unrecognised one is a defect in the connector — "
        "not a document that failed to state its period."
    )


@dataclass(frozen=True)
class SeriesSpec:
    """What a source declares about a series before any value is stored.

    Every field here is a property of the *series*, not of an observation, which is what
    makes a stored value interpretable: a series whose unit could change between readings
    is not a series.
    """

    series_key: str
    display_name: str
    unit: str
    frequency: str
    indicator_code: str | None = None
    currency: str | None = None
    scale: str | None = None
    geography: str | None = None
    seasonal_adjustment: str | None = None

    def __post_init__(self) -> None:
        if self.frequency not in FREQUENCIES:
            raise ValueError(
                f"{self.frequency!r} is not a macro frequency. Recognised: "
                f"{', '.join(FREQUENCIES)}."
            )
        if self.seasonal_adjustment is not None and (
            self.seasonal_adjustment not in ADJUSTMENTS
        ):
            raise ValueError(
                f"{self.seasonal_adjustment!r} is not a seasonal-adjustment state. "
                "Use 'sa', 'nsa', or None for 'the publisher did not say' — which is "
                "never the same as 'not adjusted'."
            )
        if not (self.unit or "").strip():
            raise ValueError(
                "a series needs a unit: an ambiguous unit lets arithmetic run and be "
                "wrong, where a missing one makes it refuse."
            )


@dataclass(frozen=True)
class DatasetSpec:
    """What a source declares about the dataset a series belongs to."""

    dataset_key: str
    source_id: str
    display_name: str
    source_url: str | None = None
    licence: str | None = None
    update_cadence: str | None = None
    access_class: str = "public_official"


__all__ = [
    "ADJUSTMENTS",
    "ADJUSTMENT_NONE",
    "ADJUSTMENT_SEASONAL",
    "FREQUENCIES",
    "FREQ_ANNUAL",
    "FREQ_DAILY",
    "FREQ_MONTHLY",
    "FREQ_QUARTERLY",
    "FREQ_WEEKLY",
    "DatasetSpec",
    "MacroPeriod",
    "SeriesSpec",
    "UnknownMacroPeriodError",
    "parse_macro_period",
]
