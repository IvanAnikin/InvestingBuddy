"""Has the stated period ENDED? A reported actual cannot exist for one that has not (ADR-072).

Cross-sector QA, 2026-11-01: Adobe's deep report carried "Trefis reports Adobe FY2026
total revenue 26.58 Bil and ending ARR growth 10.2%, per its Q4 2026 summary" as a plain
fact. Adobe's FY2026 ends around 2026-11-27, and the report ran on 2026-11-01. No FY2026
actual could exist yet: the figure was an estimate shown as a reported result. No layer
asked whether a stated period had closed.

This module answers that one question for one statement, and nothing else:

1. The PERIODS a statement names: a fiscal year (``FY2026``, ``fiscal 2026``), a fiscal
   or platform-keyed quarter (``Q4 FY2026``, ``Q4 2026``, ``2026-Q4``), an explicit
   calendar year or quarter (``CY2026``, ``calendar Q4 2026``), or a period ended on a
   printed date ("the quarter ending November 27, 2026").
2. When each one ENDS. A calendar period and a dated period end on a known date. A fiscal
   period ends on a date only the issuer's fiscal calendar gives, so it needs a
   :class:`FiscalYearAnchor` (one observed fiscal year and the date it ended, read from
   the issuer's own SEC statements). Without one, a fiscal period has no known end and is
   never judged: a miss, never a false drop.
3. Whether the statement presents a FIGURE for it as an actual. Forward-looking words
   (guidance, outlook, expects, forecast, estimate, consensus, target, "will", …) and
   part-period words (year-to-date, nine months, so far, trailing) keep it.

A bare ``2026-Q4`` / ``Q4 2026`` is the platform's FISCAL-keyed quarter (ADR-065: a
quarter is keyed by the year its fiscal year ends), not a calendar quarter, so it is
judged only on a known fiscal calendar. For a December year-end the two are the same.

Pure functions apart from :func:`load_fiscal_year_anchor`, which reads the issuer's SEC
companyfacts through the same cached, allowlisted reader as ``get_sec_statements``.
"""

from __future__ import annotations

import asyncio
import calendar as _calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.services import research_fields as rf

#: A 52/53-week fiscal year ends on a weekday near a month end, so a projected end date
#: can be a week out either way. A fiscal period is "not ended" only when the run date is
#: more than this before its projected end.
FISCAL_END_TOLERANCE = timedelta(days=7)

KIND_FISCAL_YEAR = "fiscal_year"
KIND_FISCAL_QUARTER = "fiscal_quarter"
KIND_CALENDAR_YEAR = "calendar_year"
KIND_CALENDAR_QUARTER = "calendar_quarter"
KIND_DATED = "dated"

_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_MONTHS = {
    name: index
    for index, names in enumerate(
        (
            (),
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
            ("december", "dec"),
        )
    )
    for name in names
}


@dataclass(frozen=True)
class FiscalYearAnchor:
    """One observed fiscal year and the date it ended: ``FY2025`` ended ``2025-11-28``.

    Every other fiscal year is projected from it a whole number of years away, so the
    issuer's own naming convention (a January year-end named for the year it ends in, or
    for the year it began in) is read from the data, never assumed.
    """

    fiscal_year: int
    period_end: date

    def year_end(self, fiscal_year: int) -> date:
        return _add_months(self.period_end, 12 * (fiscal_year - self.fiscal_year))

    def quarter_end(self, fiscal_year: int, quarter: int) -> date:
        return _add_months(self.year_end(fiscal_year), -3 * (4 - quarter))


@dataclass(frozen=True)
class StatedPeriod:
    """A period a statement names, as written."""

    kind: str
    text: str
    year: int | None = None
    ordinal: int | None = None
    #: Only for :data:`KIND_DATED`: the printed end date.
    end: date | None = None

    @property
    def is_fiscal(self) -> bool:
        """Its end is projected from the issuer's fiscal calendar, not printed."""
        return self.kind in (KIND_FISCAL_YEAR, KIND_FISCAL_QUARTER)

    def end_date(self, anchor: FiscalYearAnchor | None) -> date | None:
        """When the period ends, or ``None`` when that is not known."""
        if self.kind == KIND_DATED:
            return self.end
        if self.year is None:
            return None
        if self.kind == KIND_CALENDAR_YEAR:
            return date(self.year, 12, 31)
        if self.kind == KIND_CALENDAR_QUARTER and self.ordinal:
            month = 3 * self.ordinal
            return date(self.year, month, _calendar.monthrange(self.year, month)[1])
        if anchor is None:
            return None
        if self.kind == KIND_FISCAL_YEAR:
            return anchor.year_end(self.year)
        if self.kind == KIND_FISCAL_QUARTER and self.ordinal:
            return anchor.quarter_end(self.year, self.ordinal)
        return None

    def has_ended_by(self, as_of: date, anchor: FiscalYearAnchor | None) -> bool | None:
        """``False`` when the period demonstrably had NOT ended on ``as_of``; ``True``
        when it had (or may have, inside the tolerance); ``None`` when unknown."""
        end = self.end_date(anchor)
        if end is None:
            return None
        margin = FISCAL_END_TOLERANCE if self.is_fiscal else timedelta(0)
        return not as_of < end - margin


@dataclass(frozen=True)
class UnendedPeriod:
    """The verdict: the statement states a figure for ``period``, which ends ``ends``."""

    period: StatedPeriod
    ends: date


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    last = _calendar.monthrange(year, month)[1]
    # Clamped to the target month's length; the tolerance absorbs a month-end drift.
    return date(year, month, min(day.day, last))


def _year(value: str) -> int:
    return int(value) if len(value) == 4 else 2000 + int(value)


_Y4 = r"(?:19|20)\d{2}"
_FY = r"(?:fy\s?'?|fiscal(?:[\s-]+year)?\s+)"
_ORD = r"(?:first|second|third|fourth)"
_MONTH = r"(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DATE = (
    rf"(?:(?P<m1>{_MONTH})\s+(?P<d1>\d{{1,2}}),?\s+(?P<y1>{_Y4})"
    rf"|(?P<d2>\d{{1,2}})\s+(?P<m2>{_MONTH}),?\s+(?P<y2>{_Y4})"
    rf"|(?P<iso>{_Y4}-\d{{2}}-\d{{2}}))"
)

#: Alternatives in priority order: at the same start the first one that matches wins, so
#: "Q4 FY2026" is one fiscal quarter and never also the fiscal year 2026.
_PERIOD_RE = re.compile(
    "|".join(
        (
            # A period ended on a printed date.
            rf"(?P<dated>\b(?:year|quarter|half|period|months)\s+(?:ended|ending)\s+"
            rf"(?:on\s+)?{_DATE})",
            # An explicit calendar quarter.
            rf"(?P<calq>\bcalendar(?:[\s-]+year)?\s+(?:q(?P<cq1>[1-4])|(?P<cqw>{_ORD})\s+"
            rf"quarter)\s+(?:of\s+)?(?P<cqy1>{_Y4})\b"
            rf"|\bq(?P<cq2>[1-4])\s*(?:cy\s?|calendar(?:[\s-]+year)?\s+)(?P<cqy2>{_Y4})\b)",
            # A fiscal quarter, the year after it ("Q4 FY2026", "fourth quarter of
            # fiscal 2026", "Q4 2026") or before it ("FY2026 Q4", "2026-Q4").
            rf"(?P<fq>\bfiscal\s+(?:q(?P<fq1>[1-4])|(?P<fqw1>{_ORD})\s+quarter)\s+"
            rf"(?:of\s+)?(?P<fqy1>{_Y4})\b"
            rf"|\bq(?P<fq2>[1-4])\s*(?:of\s+)?{_FY}(?P<fqy2>{_Y4}|\d{{2}})\b"
            rf"|\b(?P<fqw3>{_ORD})[\s-]+quarter\s+(?:of\s+)?(?:{_FY})?(?P<fqy3>{_Y4})\b"
            rf"|\bq(?P<fq4>[1-4])[\s'-]*(?P<fqy4>{_Y4})\b"
            rf"|\bfy\s?'?(?P<fqy5>{_Y4}|\d{{2}})[\s-]?q(?P<fq5>[1-4])\b"
            rf"|\b(?P<fqy6>{_Y4})[\s-]q(?P<fq6>[1-4])\b)",
            # An explicit calendar year.
            rf"(?P<caly>\b(?:cy\s?|calendar(?:[\s-]+year)?\s+)(?P<cy>{_Y4})\b)",
            # A fiscal year.
            rf"(?P<fy>\bfy\s?'?(?P<fyy1>{_Y4}|\d{{2}})\b"
            rf"|\bfiscal(?:[\s-]+year)?\s+(?P<fyy2>{_Y4})\b"
            rf"|\bfull[\s-]+year\s+(?P<fyy3>{_Y4})\b)",
        )
    ),
    re.IGNORECASE,
)

_ISO_DATE_RE = re.compile(rf"\b{_Y4}-\d{{2}}-\d{{2}}\b")


def _first(match: re.Match[str], *names: str) -> str | None:
    return next((match.group(n) for n in names if match.group(n)), None)


def _dated_end(match: re.Match[str]) -> date | None:
    try:
        if match.group("iso"):
            return date.fromisoformat(match.group("iso"))
        month = _first(match, "m1", "m2") or ""
        day = _first(match, "d1", "d2") or "0"
        year = _first(match, "y1", "y2") or "0"
        return date(int(year), _MONTHS[month.lower().rstrip(".")], int(day))
    except (KeyError, ValueError):
        return None


def stated_periods(statement: str | None) -> list[StatedPeriod]:
    """Every period the statement names, in order. Bare years are not periods."""
    out: list[StatedPeriod] = []
    for match in _PERIOD_RE.finditer(statement or ""):
        text = match.group(0)
        if match.group("dated"):
            end = _dated_end(match)
            if end is not None:
                out.append(StatedPeriod(KIND_DATED, text, end=end))
        elif match.group("calq"):
            digit = _first(match, "cq1", "cq2")
            quarter = int(digit) if digit else _ORDINALS[(match.group("cqw") or "").lower()]
            year = _first(match, "cqy1", "cqy2") or "0"
            out.append(StatedPeriod(KIND_CALENDAR_QUARTER, text, _year(year), quarter))
        elif match.group("fq"):
            word = _first(match, "fqw1", "fqw3")
            quarter = (
                _ORDINALS[word.lower()] if word
                else int(_first(match, "fq1", "fq2", "fq4", "fq5", "fq6") or 0)
            )
            year = _first(match, "fqy1", "fqy2", "fqy3", "fqy4", "fqy5", "fqy6") or "0"
            out.append(StatedPeriod(KIND_FISCAL_QUARTER, text, _year(year), quarter))
        elif match.group("caly"):
            out.append(StatedPeriod(KIND_CALENDAR_YEAR, text, _year(match.group("cy"))))
        elif match.group("fy"):
            year = _first(match, "fyy1", "fyy2", "fyy3") or "0"
            out.append(StatedPeriod(KIND_FISCAL_YEAR, text, _year(year)))
    return out


#: Words that make a statement about a period that has not ended legitimate: what is
#: expected, guided, estimated or modelled, and part-period actuals ("year-to-date").
_FORWARD_OR_PARTIAL_RE = re.compile(
    r"\b(?:guidance|guided?|guides|outlook|expect(?:s|ed|ing|ations?)?|anticipat\w*|"
    r"forecast\w*|project(?:s|ed|ing|ions?)|target\w*|estimat\w*|consensus|analysts?|"
    r"predict\w*|model(?:s|led|ed|ling|ing)?|plan(?:s|ned)?|aims?|intends?|goals?|"
    r"budget(?:s|ed)?|on\s+(?:track|pace|course)|run[\s-]?rate|exit\s+rate|implie[sd]|"
    r"pro\s+forma|scenario|assum\w*|potential(?:ly)?|likely|sees|seeing|"
    r"reaffirm\w*|reiterat\w*|rais(?:e|es|ed|ing)|lower(?:s|ed|ing)?|narrow(?:s|ed|ing)?|"
    r"will|would|could|should|may|might|shall|can|"
    r"year[\s-]to[\s-]date|ytd|so\s+far|to\s+date|through\s+(?:the\s+)?(?:first|q[1-3])|"
    r"first\s+(?:two|three|half|nine|six)|(?:three|six|nine)\s+months|"
    r"trailing|ttm|ltm|last\s+twelve|annuali[sz]ed|preliminary)\b",
    re.IGNORECASE,
)


def is_forward_or_partial(statement: str | None) -> bool:
    """A statement that speaks of what is expected, or of part of a period: legitimate
    for a period that has not ended."""
    text = statement or ""
    return bool(_FORWARD_OR_PARTIAL_RE.search(text)) or bool(
        set(rf.fields_stated(text)) & rf.SUPERSEDABLE_FIELDS
    )


def _states_a_figure(statement: str, periods: list[StatedPeriod]) -> bool:
    """A money or metric figure, once the period labels and dates are taken out (an
    ISO date and "Q4" are numbers too)."""
    residue = statement
    for period in periods:
        residue = residue.replace(period.text, " ")
    residue = _ISO_DATE_RE.sub(" ", residue)
    if not rf.has_figure(residue):
        return False
    return bool("%" in residue or rf.has_money(residue) or rf.fields_mentioned(residue))


def unended_actual(
    statement: str | None, *, as_of: date, anchor: FiscalYearAnchor | None
) -> UnendedPeriod | None:
    """The period a statement gives a figure for as an ACTUAL, when that period had not
    ended on ``as_of``; else ``None``.

    ``None`` too whenever the answer is not certain: forward-looking or part-period
    words, no figure, or no known end for any period named (a fiscal period with no
    ``anchor``).
    """
    text = (statement or "").strip()
    if not text or is_forward_or_partial(text):
        return None
    periods = stated_periods(text)
    if not periods or not _states_a_figure(text, periods):
        return None
    for period in periods:
        if period.has_ended_by(as_of, anchor) is False:
            end = period.end_date(anchor)
            if end is not None:
                return UnendedPeriod(period, end)
    return None


def wants_fiscal_calendar(statement: str | None) -> bool:
    """Would the issuer's fiscal calendar decide this statement? True only for a figure
    stated as an actual for a FISCAL period — so the calendar is read when it can matter,
    never for every statement."""
    text = (statement or "").strip()
    if not text or is_forward_or_partial(text):
        return False
    periods = stated_periods(text)
    return any(p.is_fiscal for p in periods) and _states_a_figure(text, periods)


# ── The issuer's fiscal calendar, from its own SEC statements ──────────────── #

_ANCHOR_CACHE: dict[str, tuple[datetime, FiscalYearAnchor | None]] = {}
ANCHOR_CACHE_TTL = timedelta(hours=12)
#: A failed read is retried sooner: an SEC timeout must not switch the check off all day.
ANCHOR_FAILURE_TTL = timedelta(minutes=15)
ANCHOR_CACHE_MAX = 256


def anchor_from_normalised(fiscal_year: object, period_end: object) -> FiscalYearAnchor | None:
    """An anchor from a normaliser's headline ``fiscal_year`` and ``reporting_period_end``,
    or ``None`` when either is missing or unreadable."""
    try:
        year = int(str(fiscal_year))
    except (TypeError, ValueError):
        return None
    end = rf.parse_date(period_end)
    if end is None or not 1990 <= year <= 2100 or abs(end.year - year) > 1:
        return None
    return FiscalYearAnchor(year, end)


async def load_fiscal_year_anchor(
    ticker: str | None, exchange: str | None
) -> FiscalYearAnchor | None:
    """The issuer's latest ANNUAL period from its SEC statements, or ``None``. Never
    raises.

    The same companyfacts read (cached per CIK) and the same normaliser as the
    ``get_sec_statements`` tool, so "FY2025 ended 2025-11-28" here is the figure the
    report states. A subject SEC does not cover, or whose statements do not normalise to
    an annual period, has no anchor.
    """
    if not ticker:
        return None
    key = f"{ticker.upper()}|{(exchange or '').upper()}"
    now = datetime.now(timezone.utc)
    cached = _ANCHOR_CACHE.get(key)
    if cached is not None and now - cached[0] < (
        ANCHOR_CACHE_TTL if cached[1] is not None else ANCHOR_FAILURE_TTL
    ):
        return cached[1]
    anchor: FiscalYearAnchor | None = None
    try:
        from app.integrations.providers.sec_edgar_fundamentals import (
            SecEdgarFundamentalsProvider,
        )
        from app.integrations.sec_fundamentals_normalizer import normalize_company_facts
        from app.services.agent_tools.peers import _facts_for  # noqa: PLC2701

        facts, _reason = await _facts_for(ticker, exchange, SecEdgarFundamentalsProvider())
        if facts is not None:
            n = await asyncio.to_thread(normalize_company_facts, facts, ticker)
            if n.period_basis == "annual":
                anchor = anchor_from_normalised(n.fiscal_year, n.reporting_period_end)
    except Exception:  # noqa: BLE001 - no calendar means no verdict, never a failure
        anchor = None
    if len(_ANCHOR_CACHE) >= ANCHOR_CACHE_MAX:
        _ANCHOR_CACHE.pop(next(iter(_ANCHOR_CACHE)))
    _ANCHOR_CACHE[key] = (now, anchor)
    return anchor


def reset_cache() -> None:
    _ANCHOR_CACHE.clear()
