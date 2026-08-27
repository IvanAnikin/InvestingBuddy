"""The FOUR reporting states an issuer can be in — current-period acceptance.

``financial_period`` decides what one period IS and whether two are comparable.
This module answers the question a reader actually asks of a research report:
*what is the latest thing this issuer has reported, and of which kind?*

Four states, deliberately separate and simultaneously true:

    latest_annual          FY2025   — the last completed financial year
    latest_interim         H1 2026  — the last half-year reported
    latest_quarter         Q2 2026  — the last quarter reported
    latest_current_period  Q2 2026  — the newest of the two part-year states

They are separate because collapsing them loses the distinction the whole
campaign exists to protect. "Revenue" has no single answer: FY2025 revenue and
H1 2026 revenue are both true and answer different questions, and an interim
figure never supersedes an annual one — it sits beside it.

``latest_current_period`` orders half-years and quarters against each other,
which ``financial_period.is_more_recent`` deliberately refuses to do. That is
not a contradiction: ``is_more_recent`` answers *comparability* ("may I compare
these two figures?" — no, a half and a quarter measure different spans), while
this answers *recency* ("which did the issuer report most recently?"). Ordering
is by the period's END, so H1 and Q2 of one year tie and the quarter — the more
specific claim, following the same precedent as the interim-marker parser —
wins. Nothing here ever compares two VALUES.

Pure and total: no fetching, no promotion, no arithmetic on figures. Every
selector returns ``UNKNOWN_PERIOD`` rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.sources.financial_period import (
    PERIOD_TYPE_ANNUAL,
    PERIOD_TYPE_HALF,
    PERIOD_TYPE_QUARTER,
    UNKNOWN_PERIOD,
    ReportingPeriod,
    parse_period,
)

#: Quarters per half — how a half-year is placed on the quarter scale so the two
#: interim kinds can be ordered by when their period ENDS.
_QUARTERS_PER_HALF = 2


def period_end_quarter(period: ReportingPeriod) -> int | None:
    """Which quarter of its year this period ENDS in, or ``None``.

    ``Q3 2026`` ends in quarter 3; ``H1 2026`` ends in quarter 2. Annual and
    split-year periods return ``None`` — they are not part-year states and are
    never ordered on this scale.
    """
    if period.period_type == PERIOD_TYPE_QUARTER:
        return period.ordinal
    if period.period_type == PERIOD_TYPE_HALF and period.ordinal is not None:
        return period.ordinal * _QUARTERS_PER_HALF
    return None


def _recency_key(period: ReportingPeriod) -> tuple[int, int, int]:
    """Order interim periods by (year, end quarter, specificity).

    Specificity breaks the H1-vs-Q2 tie in favour of the quarter: both end
    30 June, and the quarter is the narrower, more recent statement.
    """
    return (
        period.year or 0,
        period_end_quarter(period) or 0,
        1 if period.period_type == PERIOD_TYPE_QUARTER else 0,
    )


def _latest_of_type(
    periods: "list[ReportingPeriod]", period_type: str
) -> ReportingPeriod:
    matching = [
        p for p in periods if not p.is_unknown and p.period_type == period_type
    ]
    if not matching:
        return UNKNOWN_PERIOD
    return max(matching, key=_recency_key)


def select_latest_annual(periods: "list[ReportingPeriod]") -> ReportingPeriod:
    """The latest FULL-YEAR period. Interim periods are invisible here.

    A canonical annual slot may only ever hold a full year, so this selector
    does not fall back to an interim period when no annual one exists: the
    honest answer is then ``UNKNOWN_PERIOD``.
    """
    return _latest_of_type(periods, PERIOD_TYPE_ANNUAL)


def select_latest_interim(periods: "list[ReportingPeriod]") -> ReportingPeriod:
    """The latest HALF-YEAR period."""
    return _latest_of_type(periods, PERIOD_TYPE_HALF)


def select_latest_quarter(periods: "list[ReportingPeriod]") -> ReportingPeriod:
    """The latest QUARTER."""
    return _latest_of_type(periods, PERIOD_TYPE_QUARTER)


def select_latest_current_period(
    periods: "list[ReportingPeriod]",
) -> ReportingPeriod:
    """The newest PART-YEAR period of any kind. Never an annual period."""
    interim = [p for p in periods if not p.is_unknown and p.is_interim]
    if not interim:
        return UNKNOWN_PERIOD
    return max(interim, key=_recency_key)


@dataclass(frozen=True)
class ReportingPeriodState:
    """All four states at once, each independently allowed to be unknown."""

    latest_annual: ReportingPeriod = UNKNOWN_PERIOD
    latest_interim: ReportingPeriod = UNKNOWN_PERIOD
    latest_quarter: ReportingPeriod = UNKNOWN_PERIOD
    latest_current_period: ReportingPeriod = UNKNOWN_PERIOD

    @property
    def has_current_period(self) -> bool:
        return not self.latest_current_period.is_unknown

    def as_labels(self) -> dict[str, str | None]:
        """Human labels, ``None`` where a state is genuinely absent.

        ``None`` is rendered as an explicit "not available" by the caller — the
        absence of current-period reporting is itself a finding, not a blank.
        """
        return {
            "latest_annual": (
                None if self.latest_annual.is_unknown else self.latest_annual.label()
            ),
            "latest_interim": (
                None if self.latest_interim.is_unknown else self.latest_interim.label()
            ),
            "latest_quarter": (
                None if self.latest_quarter.is_unknown else self.latest_quarter.label()
            ),
            "latest_current_period": (
                None
                if self.latest_current_period.is_unknown
                else self.latest_current_period.label()
            ),
        }


def build_reporting_period_state(
    periods: "list[ReportingPeriod]",
) -> ReportingPeriodState:
    """Resolve all four states from the periods a report actually holds."""
    known = [p for p in periods if not p.is_unknown]
    return ReportingPeriodState(
        latest_annual=select_latest_annual(known),
        latest_interim=select_latest_interim(known),
        latest_quarter=select_latest_quarter(known),
        latest_current_period=select_latest_current_period(known),
    )


def periods_of(facts: "list[Any] | None") -> list[ReportingPeriod]:
    """Parse the period of every fact, in either fact shape. Never raises."""
    out: list[ReportingPeriod] = []
    for fact in facts or []:
        raw = (
            fact.get("period")
            if isinstance(fact, dict)
            else getattr(fact, "period", None)
        )
        period = parse_period(raw)
        if not period.is_unknown:
            out.append(period)
    return out


__all__ = [
    "ReportingPeriodState",
    "build_reporting_period_state",
    "period_end_quarter",
    "periods_of",
    "select_latest_annual",
    "select_latest_current_period",
    "select_latest_interim",
    "select_latest_quarter",
]
