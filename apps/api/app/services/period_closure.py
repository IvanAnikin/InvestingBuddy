"""Has the stated period ENDED? A reported actual cannot exist for one that has not (ADR-072).

Cross-sector QA, 2026-11-01: Adobe's deep report carried "Trefis reports Adobe FY2026
total revenue 26.58 Bil and ending ARR growth 10.2%, per its Q4 2026 summary" as a plain
fact. Adobe's FY2026 ends around 2026-11-27, and the report ran on 2026-11-01. No FY2026
actual could exist yet: the figure was an estimate shown as a reported result. No layer
asked whether a stated period had closed.

This module answers that one question for one statement, narrowly. A miss is
acceptable; dropping a true statement is not (review of PR #309):

1. The PERIODS a statement names: a fiscal year (``FY2026``, ``fiscal 2026``), a fiscal
   or platform-keyed quarter (``Q4 FY2026``, ``fiscal 2026 third quarter``, ``Q4 2026``,
   ``2026-Q4``, ``3Q26``), an explicit calendar year or quarter (``CY2026``,
   ``calendar Q4 2026``), or a period ended on a printed date.
2. When each one ENDS. A calendar period and a dated period end on a known date. A fiscal
   period needs a :class:`FiscalYearAnchor` (one observed fiscal year and the date it
   ended, from the issuer's own SEC statements). Without one, a fiscal period has no
   known end and the statement is never judged.
3. The SHAPE of the statement. It is judged only when it reports a period's result
   ("revenue was", "reports … revenue") with a figure, carries no forward, estimate,
   part-period or event wording, names no quarter or half it does not place, and EVERY
   period it names is known and still open. One closed or unknown period keeps it.

A bare ``2026-Q4`` / ``Q4 2026`` is the platform's FISCAL-keyed quarter (ADR-065), so it
is judged only on a known fiscal calendar. For a fiscal year ending January–March no
quarter is judged at all, and a full fiscal year is judged on the earlier of the two
naming readings (Walmart's and Target's conventions differ by a year).

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

    @property
    def ends_early_in_the_year(self) -> bool:
        """A fiscal year ending January–March (a 52/53-week end on day 1–7 counts as the
        month before, so 2 January is a December year-end). Such years are named both
        ways in the wild: Walmart's "fiscal 2027" ends January 2027, Target's "fiscal
        2025" ends January 2026 (ADR-065)."""
        month = self.period_end.month if self.period_end.day > 7 else (
            (self.period_end.month - 2) % 12 + 1
        )
        return month in (1, 2, 3)

    def year_end(self, fiscal_year: int) -> date:
        """FY(N)'s end under the issuer's own label convention (companyfacts ``fy``)."""
        return _add_months(self.period_end, 12 * (fiscal_year - self.fiscal_year))

    def judged_year_end(self, fiscal_year: int) -> date:
        """The end used to JUDGE a statement naming FY(N). For a January–March year-end a
        writer may use either convention, so the EARLIER reading is used: the year that
        ends in calendar year N. A statement is judged open only if it is open under both
        readings."""
        end = self.year_end(fiscal_year)
        if self.ends_early_in_the_year and end.year > fiscal_year:
            return _add_months(end, -12)
        return end

    def quarter_end(self, fiscal_year: int, quarter: int) -> date | None:
        """FY(N) Qn's end, or ``None`` for a January–March year-end: there the issuer's
        label and the platform's ``2026-Q4`` keying (ADR-065) disagree, so no quarter is
        judged."""
        if self.ends_early_in_the_year:
            return None
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
            return anchor.judged_year_end(self.year)
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
            rf"|\b(?P<fqy6>{_Y4})[\s-]q(?P<fq6>[1-4])\b"
            # The year BEFORE the quarter: "fiscal 2026 third quarter", "fiscal-year
            # 2026 third-quarter", "FY2026 third quarter", "2026 third-quarter".
            rf"|\b{_FY}(?P<fqy7>{_Y4}|\d{{2}})[\s-]+(?:q(?P<fq7>[1-4])|(?P<fqw7>{_ORD})"
            rf"[\s-]+quarter)\b"
            rf"|\b(?P<fqy8>{_Y4})[\s-]+(?P<fqw8>{_ORD})[\s-]+quarter\b"
            # "3Q26", "3Q 2026".
            rf"|\b(?P<fq9>[1-4])q\s?'?(?P<fqy9>{_Y4}|\d{{2}})\b)",
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
            word = _first(match, "fqw1", "fqw3", "fqw7", "fqw8")
            quarter = (
                _ORDINALS[word.lower()] if word
                else int(_first(match, "fq1", "fq2", "fq4", "fq5", "fq6", "fq7", "fq9") or 0)
            )
            year = _first(
                match, "fqy1", "fqy2", "fqy3", "fqy4", "fqy5", "fqy6", "fqy7", "fqy8", "fqy9"
            ) or "0"
            out.append(StatedPeriod(KIND_FISCAL_QUARTER, text, _year(year), quarter))
        elif match.group("caly"):
            out.append(StatedPeriod(KIND_CALENDAR_YEAR, text, _year(match.group("cy"))))
        elif match.group("fy"):
            year = _first(match, "fyy1", "fyy2", "fyy3") or "0"
            out.append(StatedPeriod(KIND_FISCAL_YEAR, text, _year(year)))
    return out


#: Words that make a statement about a period that has not ended legitimate, or that
#: make it something other than "the period's result was X". Any one of them keeps the
#: statement: the rule is narrow on purpose (review of PR #309: a miss is acceptable, a
#: false drop of a true statement is not).
_PROTECTED_RE = re.compile(
    # What is expected, guided, estimated or modelled — and whose estimate it is.
    r"\b(?:guidance|guided?|guides|outlook|expect(?:s|ed|ing|ations?)?|anticipat\w*|"
    r"forecast\w*|project(?:s|ed|ing|ions?)|estimat\w*|consensus|analysts?|"
    r"predict\w*|model(?:s|led|ed|ling|ing)?|plan(?:s|ned)?|aims?|intends?|goals?|"
    r"budget(?:s|ed)?|on\s+(?:track|pace|course)|run[\s-]?rate|exit\s+rate|implie[sd]|"
    r"pro\s+forma|scenario|assum\w*|potential(?:ly)?|likely|sees|seeing|"
    r"reaffirm\w*|reiterat\w*|lower(?:s|ed|ing)?|narrow(?:s|ed|ing)?|preliminary|"
    r"will|would|could|should|might|shall|can|price\s+target|"
    r"wall\s+street|street|factset|lseg|refinitiv|visible\s+alpha|mean|median|"
    # Part of a period, or something that happened DURING the period.
    r"year[\s-]to[\s-]date|ytd|so\s+far|to\s+date|through|during|in\s+fiscal|"
    r"trailing|ttm|ltm|last\s+twelve|annuali[sz]ed|first\s+(?:two|three|half|nine|six)|"
    r"(?:three|six|nine)[\s-]months?|[369]m|h[12]|halves|half|interim|"
    # Events, not a period's result.
    r"repurchas\w*|buy[\s-]?backs?|bought|acqui\w*|complet\w*|announc\w*|signed|"
    r"rais(?:e|es|ed|ing)|issu(?:e|es|ed|ing)|fell|fall(?:s|en|ing)?|rose|ris(?:e|es|en|ing)|"
    r"declin\w*|gain\w*|jump\w*|drop\w*|climb\w*|slid|slump\w*|plung\w*|soar\w*|"
    r"surg\w*|tumbl\w*|paid|pays|paying|spent|spend\w*|invest\w*|launch\w*|closed|"
    r"closing|divest\w*|sold|hired|agreed|approved|settled|received|won|awarded|opened)\b",
    re.IGNORECASE,
)
#: Lower-case only, so a company name ("Target Corp", the month "May") never protects.
_PROTECTED_LOWER_RE = re.compile(r"\b(?:targets?|targeted|targeting|may)\b")

#: A quarter or half named WITHOUT a year the reader could place ("through the third
#: quarter", "Q3", "quarterly"): the statement is about part of a period.
_UNPLACED_SUBPERIOD_RE = re.compile(
    r"\b(?:quarters?|quarterly|q[1-4]|[1-4]q|semester)\b", re.IGNORECASE
)

#: "the period's result WAS X": a result metric and a verb that reports it.
_RESULT_METRIC_RE = re.compile(
    r"\b(?:revenues?|sales|turnover|net\s+(?:income|profit|loss|earnings)|earnings|eps|"
    r"arr|annual\s+recurring\s+revenue|operating\s+(?:income|profit|loss|margin|cash\s+flow)|"
    r"gross\s+(?:profit|margin)|free\s+cash\s+flow|cash\s+flow|ebitda|ebit|bookings|"
    r"billings|rpo|profit|margins?)\b",
    re.IGNORECASE,
)
_RESULT_VERB_RE = re.compile(
    r"\b(?:was|were|reported|reports|totall?ed|totals|came\s+in\s+at|amounted\s+to|"
    r"stood\s+at|posted|recorded|generated|delivered|achieved|booked)\b",
    re.IGNORECASE,
)


def is_forward_or_partial(statement: str | None) -> bool:
    """A statement that speaks of what is expected or estimated, of part of a period, or
    of an event in it: never judged."""
    text = statement or ""
    return bool(_PROTECTED_RE.search(text) or _PROTECTED_LOWER_RE.search(text)) or bool(
        set(rf.fields_stated(text)) & rf.SUPERSEDABLE_FIELDS
    )


def _residue(statement: str, periods: list[StatedPeriod]) -> str:
    """The statement with its period labels and dates taken out (an ISO date and "Q4" are
    numbers too)."""
    residue = statement
    for period in periods:
        residue = residue.replace(period.text, " ")
    return _ISO_DATE_RE.sub(" ", residue)


def _judgeable(statement: str | None) -> list[StatedPeriod] | None:
    """The periods of a statement that states a period's RESULT as an actual, or ``None``
    when the statement is not of that shape (and so is never judged)."""
    text = (statement or "").strip()
    if not text or is_forward_or_partial(text):
        return None
    periods = stated_periods(text)
    if not periods:
        return None
    residue = _residue(text, periods)
    if _UNPLACED_SUBPERIOD_RE.search(residue):
        return None
    if not (_RESULT_METRIC_RE.search(residue) and _RESULT_VERB_RE.search(residue)):
        return None
    if not rf.has_figure(residue):
        return None
    if not ("%" in residue or rf.has_money(residue) or rf.fields_mentioned(residue)):
        return None
    return periods


def unended_actual(
    statement: str | None, *, as_of: date, anchor: FiscalYearAnchor | None
) -> UnendedPeriod | None:
    """The period a statement gives a RESULT for as an actual, when that period had not
    ended on ``as_of``; else ``None``.

    Narrow by design. The statement must report a result metric with a reporting verb
    ("revenue was", "reports … revenue") and a figure, carry no forward, estimate,
    part-period or event wording, and EVERY period it names must have a known end that is
    still ahead. One period that has ended, or whose end is unknown (a fiscal period with
    no ``anchor``, a quarter of a January–March year-end), keeps it: "FY2025 revenue was
    $23.8B; Q4 FY2026 results are due in December" is true.
    """
    periods = _judgeable(statement)
    if not periods:
        return None
    open_periods: list[tuple[StatedPeriod, date]] = []
    for period in periods:
        end = period.end_date(anchor)
        if end is None or period.has_ended_by(as_of, anchor) is not False:
            return None
        open_periods.append((period, end))
    # Report the longest period named: the year, when a year and its quarter are both open.
    period, end = max(open_periods, key=lambda pair: pair[1])
    return UnendedPeriod(period, end)


def wants_fiscal_calendar(statement: str | None) -> bool:
    """Would the issuer's fiscal calendar decide this statement? True only for a result
    stated as an actual for a FISCAL period — so the calendar is read when it can matter,
    never for every statement."""
    periods = _judgeable(statement)
    return bool(periods) and any(p.is_fiscal for p in periods or ())


# ── The issuer's fiscal calendar, from its own SEC statements ──────────────── #

_ANCHOR_CACHE: dict[str, tuple[datetime, FiscalYearAnchor | None]] = {}
ANCHOR_CACHE_TTL = timedelta(hours=12)
#: A failed read is retried sooner: an SEC timeout must not switch the check off all day.
ANCHOR_FAILURE_TTL = timedelta(minutes=15)
ANCHOR_CACHE_MAX = 256
#: The whole read — CIK resolution, companyfacts download, normalisation — is bounded, so
#: a slow data.sec.gov costs one write-up a few seconds, never a question's time budget.
ANCHOR_READ_TIMEOUT_SECONDS = 5.0


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
        anchor = await asyncio.wait_for(
            _read_anchor(ticker, exchange), timeout=ANCHOR_READ_TIMEOUT_SECONDS
        )
    except Exception:  # noqa: BLE001 - no calendar means no verdict, never a failure
        anchor = None
    if len(_ANCHOR_CACHE) >= ANCHOR_CACHE_MAX:
        _ANCHOR_CACHE.pop(next(iter(_ANCHOR_CACHE)))
    _ANCHOR_CACHE[key] = (now, anchor)
    return anchor


async def _read_anchor(ticker: str, exchange: str | None) -> FiscalYearAnchor | None:
    from app.integrations import sec_fundamentals_normalizer
    from app.integrations.providers.sec_edgar_fundamentals import SecEdgarFundamentalsProvider
    from app.services.agent_tools import peers

    facts, _reason = await peers._facts_for(  # noqa: SLF001
        ticker, exchange, SecEdgarFundamentalsProvider()
    )
    if facts is None:
        return None
    n = await asyncio.to_thread(
        sec_fundamentals_normalizer.normalize_company_facts, facts, ticker
    )
    if n.period_basis != "annual":
        return None
    return anchor_from_normalised(n.fiscal_year, n.reporting_period_end)


def reset_cache() -> None:
    _ANCHOR_CACHE.clear()
