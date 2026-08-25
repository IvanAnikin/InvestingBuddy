"""Canonical REPORTING-PERIOD semantics for one extracted financial fact.

Private-use production readiness, PR-B (used by PR-D for current-period work).

Until now a fact's ``period`` was a bare 4-digit year string and nothing more.
That was enough while every promoted fact was annual — the multi-year table
reconstructor deliberately DETECTS interim and split-year columns and then
REFUSES to promote them (``REASON_INTERIM_PERIOD_UNSUPPORTED`` /
``REASON_SPLIT_YEAR_PERIOD_UNSUPPORTED``), precisely because
``ExtractedFact.period`` could not represent them without ambiguity.

A private research tool cannot stop at annual data when newer official
reporting exists, so periods need a representation that can hold

    FY2025 revenue   and   H1 2026 revenue

at the same time, keep them apart, and refuse to compare them. This module is
that representation. It is deliberately a PURE VALUE model — it parses and
orders periods, and decides comparability. It does not fetch, extract, promote
or select anything.

Fail-closed throughout: an unparseable period is ``UNKNOWN_PERIOD``, an unknown
period is never comparable with anything (including another unknown), and two
periods of different types are never comparable however close their years are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Period types ─────────────────────────────────────────────────────────── #

PERIOD_TYPE_ANNUAL = "annual"
PERIOD_TYPE_HALF = "half"
PERIOD_TYPE_QUARTER = "quarter"
#: A straddling fiscal year printed as "2025/26". Representable and orderable,
#: but only ever comparable with another split year — never with a bare annual
#: year, because which calendar year it "is" depends on the issuer's own
#: fiscal-year convention, which the document does not always state.
PERIOD_TYPE_SPLIT_YEAR = "split_year"

VALID_PERIOD_TYPES: frozenset[str] = frozenset(
    {
        PERIOD_TYPE_ANNUAL,
        PERIOD_TYPE_HALF,
        PERIOD_TYPE_QUARTER,
        PERIOD_TYPE_SPLIT_YEAR,
    }
)

#: Types that describe part of a year. An interim figure must never fill an
#: annual slot, and must never be annualised (no forecasting in this system).
INTERIM_PERIOD_TYPES: frozenset[str] = frozenset(
    {PERIOD_TYPE_HALF, PERIOD_TYPE_QUARTER}
)

_ANNUAL_RE = re.compile(r"^(?:FY[-\s]?)?((?:19|20)\d{2})$", re.IGNORECASE)
_SPLIT_YEAR_RE = re.compile(r"^(?:FY[-\s]?)?((?:19|20)\d{2})\s*/\s*(\d{2}|\d{4})$")
# "H1 2026", "2026 H1", "H1 FY2026", "1H26" is deliberately NOT accepted — an
# ambiguous two-digit year is a guess, and this module does not guess.
_HALF_RE = re.compile(
    r"^(?:H(?P<h1>[12])[\s-]*(?:FY[-\s]?)?(?P<y1>(?:19|20)\d{2})"
    r"|(?:FY[-\s]?)?(?P<y2>(?:19|20)\d{2})[\s-]*H(?P<h2>[12]))$",
    re.IGNORECASE,
)
_QUARTER_RE = re.compile(
    r"^(?:Q(?P<q1>[1-4])[\s-]*(?:FY[-\s]?)?(?P<y1>(?:19|20)\d{2})"
    r"|(?:FY[-\s]?)?(?P<y2>(?:19|20)\d{2})[\s-]*Q(?P<q2>[1-4]))$",
    re.IGNORECASE,
)

_WS_RE = re.compile(r"\s+")

#: Column width of ``ExtractedFact.period``.
_PERIOD_MAX = 50


@dataclass(frozen=True)
class ReportingPeriod:
    """One reporting period, ordered and comparable by construction.

    ``year`` is the period's own headline year exactly as printed. For a split
    year it is the FIRST year ("2025" of "2025/26"), so ordering stays stable
    without asserting a fiscal-year convention the document never stated.
    ``ordinal`` is the half/quarter number (``None`` for annual/split year).
    """

    period_type: str | None = None
    year: int | None = None
    ordinal: int | None = None
    #: The label exactly as found, kept so a human always sees the document's
    #: own words rather than this module's normalisation.
    raw: str | None = None

    # -- identity / ordering ---------------------------------------------- #

    @property
    def is_unknown(self) -> bool:
        return self.period_type is None or self.year is None

    @property
    def is_annual(self) -> bool:
        return self.period_type == PERIOD_TYPE_ANNUAL

    @property
    def is_interim(self) -> bool:
        return self.period_type in INTERIM_PERIOD_TYPES

    @property
    def key(self) -> str | None:
        """Canonical identity: ``2025`` | ``2026-H1`` | ``2026-Q2`` | ``2025/26``."""
        if self.is_unknown:
            return None
        if self.period_type == PERIOD_TYPE_ANNUAL:
            return str(self.year)
        if self.period_type == PERIOD_TYPE_SPLIT_YEAR:
            return f"{self.year}/{str((self.year or 0) + 1)[-2:]}"
        prefix = "H" if self.period_type == PERIOD_TYPE_HALF else "Q"
        return f"{self.year}-{prefix}{self.ordinal}"

    @property
    def sort_key(self) -> tuple[int, int]:
        """Chronological ordering WITHIN one period type.

        Never used to order across types — ``comparable_with`` refuses that
        case before any ordering question can be asked.
        """
        return (self.year or 0, self.ordinal or 0)

    def label(self) -> str:
        """Short human label. Never emits ``None`` into human-facing text."""
        if self.is_unknown:
            return self.raw or "Period not stated"
        if self.period_type == PERIOD_TYPE_ANNUAL:
            return f"FY{self.year}"
        if self.period_type == PERIOD_TYPE_SPLIT_YEAR:
            return f"FY{self.key}"
        prefix = "H" if self.period_type == PERIOD_TYPE_HALF else "Q"
        return f"{prefix}{self.ordinal} {self.year}"

    def comparable_with(self, other: "ReportingPeriod") -> bool:
        """True only when the two periods measure the same KIND of span.

        Fail-closed: an unknown period is comparable with nothing, not even
        another unknown. Comparing FY2025 with H1 2026 is the
        ``INTERIM_AS_ANNUAL`` contradiction class, so it is refused here rather
        than left to each caller to remember.
        """
        if self.is_unknown or other.is_unknown:
            return False
        if self.period_type != other.period_type:
            return False
        if self.period_type in INTERIM_PERIOD_TYPES:
            # H1 2025 vs H1 2026 compares like-for-like; H1 vs H2 does not.
            return self.ordinal == other.ordinal
        return True


UNKNOWN_PERIOD = ReportingPeriod()


def parse_period(raw: str | None) -> ReportingPeriod:
    """Interpret a period label. Unrecognised input is UNKNOWN, never guessed."""
    if raw is None:
        return UNKNOWN_PERIOD
    text = _WS_RE.sub(" ", str(raw)).strip()[:_PERIOD_MAX]
    if not text:
        return UNKNOWN_PERIOD

    m = _ANNUAL_RE.match(text)
    if m:
        return ReportingPeriod(PERIOD_TYPE_ANNUAL, int(m.group(1)), None, text)

    m = _SPLIT_YEAR_RE.match(text)
    if m:
        return ReportingPeriod(PERIOD_TYPE_SPLIT_YEAR, int(m.group(1)), None, text)

    m = _HALF_RE.match(text)
    if m:
        half = m.group("h1") or m.group("h2")
        year = m.group("y1") or m.group("y2")
        return ReportingPeriod(PERIOD_TYPE_HALF, int(year), int(half), text)

    m = _QUARTER_RE.match(text)
    if m:
        quarter = m.group("q1") or m.group("q2")
        year = m.group("y1") or m.group("y2")
        return ReportingPeriod(PERIOD_TYPE_QUARTER, int(year), int(quarter), text)

    return ReportingPeriod(None, None, None, text)


def format_period(period: ReportingPeriod) -> str | None:
    """The canonical string to persist in ``ExtractedFact.period``.

    Round-trips: ``parse_period(format_period(p)).key == p.key``. An unknown
    period persists its raw text unchanged rather than being normalised away —
    losing the document's own words would make the refusal unauditable.
    """
    if period.is_unknown:
        return period.raw
    return period.key


def is_more_recent(a: ReportingPeriod, b: ReportingPeriod) -> bool:
    """True when ``a`` is strictly later than ``b`` WITHIN the same period type.

    Deliberately returns ``False`` for two periods of different types: "is
    H1 2026 more recent than FY2025?" is not a question this system answers by
    comparing numbers — an interim result never supersedes an annual one, it
    sits beside it. Current-period SELECTION (PR-D) picks the latest of each
    type separately for exactly this reason.
    """
    if not a.comparable_with(b):
        return False
    return a.sort_key > b.sort_key


def latest(periods: "list[ReportingPeriod]") -> ReportingPeriod:
    """The chronologically latest KNOWN period of a single type, else UNKNOWN.

    Refuses (returns UNKNOWN) when the input mixes period types, because
    "the latest" would then silently mean "the latest of whichever type
    happened to sort highest".
    """
    known = [p for p in periods if not p.is_unknown]
    if not known:
        return UNKNOWN_PERIOD
    if len({p.period_type for p in known}) > 1:
        return UNKNOWN_PERIOD
    return max(known, key=lambda p: p.sort_key)


__all__ = [
    "INTERIM_PERIOD_TYPES",
    "PERIOD_TYPE_ANNUAL",
    "PERIOD_TYPE_HALF",
    "PERIOD_TYPE_QUARTER",
    "PERIOD_TYPE_SPLIT_YEAR",
    "UNKNOWN_PERIOD",
    "VALID_PERIOD_TYPES",
    "ReportingPeriod",
    "format_period",
    "is_more_recent",
    "latest",
    "parse_period",
]
