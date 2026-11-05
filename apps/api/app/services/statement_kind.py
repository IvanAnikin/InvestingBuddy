"""What KIND of statement a finding is — actual, guidance, forecast, target, estimate — ADR-088.

THE DEFECT
==========
QA campaign 2026-11, Adobe report ``7b401826`` (ADBE-3): finding F14 said "Trefis reports
Adobe FY2026 total revenue 26.58 Bil … per its Q4 2026 summary". Adobe's fiscal 2026 ends
about 27 November 2026; the report ran on 1 November. No FY2026 actual could exist: the
figure was a forecast written as a result, and nothing in the platform recorded whether a
statement was a result, guidance or somebody's estimate.

A first attempt (PR #309, ADR-072, shelved) tried to decide "result or not" from the
WORDS of the finding after it was written. Four review rounds found 13, 19, 21 and 38
false drops: "lifted FY2026 revenue to", "the base for FY2026", "full year 2026 results
are due" all read as actuals or forecasts the wrong way round. Wording rules after the
fact cannot decide it.

THE DESIGN (ADR-088)
====================
1. **The kind is declared at extraction.** The investigator's structured output carries
   ``statement_kind`` (``actual | guidance | forecast | target | estimate | unknown``)
   and ``period`` (the period the stated figures are FOR) for every finding. The model
   that read the evidence says what the statement is; the platform never re-derives it
   from prose.
2. **One structural invariant, fail closed.** A statement declared ``actual`` (or
   ``unknown``) whose DECLARED period had not ended when its newest cited source was
   published (and at the run date) cannot be shown as a result. It is RECLASSIFIED to
   the forward kind its own words carry (an ADR-080 ``date_semantics`` forward role on
   an unended period, or an analyst-estimate cue), and otherwise WITHHELD: kept in the
   ledger with its reason (``verification_status='withdrawn'``,
   ``statement_kind_basis``), never shown as a result. The period judged is the
   declared one only — never one read from the statement's words (ADR-072), and never
   the period tag INHERITED from the evidence: that is the document's reporting period,
   which by ADR-080 cannot be unended at the document's own publication date, so an
   unended inherited tag is a tagging error of the document (the replay found four,
   "6-K … tagged period 2028-H2"), not a claim of the statement.
3. **The fallback only ever labels forward.** When the model declares no kind (or
   declares ``actual`` with no period), the statement is left as declared unless a
   period it names is FORWARD-framed (ADR-080 cue table) and has still not ended at the
   run date — then it is ``guidance`` / ``forecast`` / ``target`` / ``estimate``. A
   statement that ALSO names an ended period ("$246m cash at 31 March 2025, runway into
   H2 2027") is mixed and stays as declared; a year inside a full date ("June 30, 2026")
   is a date, not a period. Prose never makes anything ``actual`` and never withholds
   anything.
4. **Fiscal calendars.** A period key is fiscal (ADR-065). With the issuer's fiscal
   year anchor (one observed fiscal year and the date it ended, read from its SEC
   statements as in ADR-072) FY(N) and its quarters are projected, ±7 days for 52/53-week
   years; without one, a fiscal period is judged at its EARLIEST possible end (twelve
   months before its calendar reading — ADR-080's lenient reading), so a period is
   called unended only when it cannot have ended under any naming convention.

Pure value module except :func:`load_fiscal_year_anchor`, which reads the issuer's SEC
companyfacts through the same cached, allowlisted reader the statement tools use, bounded
at 5 s. Every regular expression here is linear: no nested or adjacent unbounded
quantifiers over overlapping classes.
"""

from __future__ import annotations

import asyncio
import calendar as _calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

# ── The closed vocabulary ──────────────────────────────────────────────────── #

KIND_ACTUAL = "actual"
KIND_GUIDANCE = "guidance"
KIND_FORECAST = "forecast"
KIND_TARGET = "target"
KIND_ESTIMATE = "estimate"
KIND_UNKNOWN = "unknown"

STATEMENT_KINDS: frozenset[str] = frozenset(
    {KIND_ACTUAL, KIND_GUIDANCE, KIND_FORECAST, KIND_TARGET, KIND_ESTIMATE, KIND_UNKNOWN}
)
#: Kinds that are never a reported result. An actual slot, a contradiction with a filed
#: figure and a gap asking for a reported value never take one of these.
NON_ACTUAL_KINDS: frozenset[str] = frozenset(
    {KIND_GUIDANCE, KIND_FORECAST, KIND_TARGET, KIND_ESTIMATE}
)

#: How the kind was set (``research_findings.statement_kind_basis``).
BASIS_MODEL = "model"
BASIS_NOT_DECLARED = "not_declared"
BASIS_FORWARD_CUE = "forward_cue"
BASIS_RECLASSIFIED = "reclassified_period_not_ended"
BASIS_WITHHELD = "withheld_period_not_ended"

#: The reader-facing word for each non-actual kind.
KIND_LABELS: dict[str, str] = {
    KIND_GUIDANCE: "guidance",
    KIND_FORECAST: "forecast",
    KIND_TARGET: "target",
    KIND_ESTIMATE: "estimate",
}

#: A 52/53-week fiscal year ends on a weekday near a month end, so a projected end can
#: be a week out either way. A fiscal period counts as NOT ended only when the reference
#: date is more than this before its projected end.
FISCAL_END_TOLERANCE = timedelta(days=7)

_SYNONYMS: dict[str, str] = {
    "actual": KIND_ACTUAL,
    "reported": KIND_ACTUAL,
    "result": KIND_ACTUAL,
    "historical": KIND_ACTUAL,
    "guidance": KIND_GUIDANCE,
    "outlook": KIND_GUIDANCE,
    "forecast": KIND_FORECAST,
    "projection": KIND_FORECAST,
    "target": KIND_TARGET,
    "estimate": KIND_ESTIMATE,
    "consensus": KIND_ESTIMATE,
    "unknown": KIND_UNKNOWN,
}


def normalise_kind(raw: object) -> str | None:
    """The kind a model declared, in the closed vocabulary, or ``None``."""
    text = str(raw or "").strip().lower()[:24]
    return _SYNONYMS.get(text)


def is_non_actual(kind: str | None) -> bool:
    return kind in NON_ACTUAL_KINDS


# ── Periods ──────────────────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class Period:
    """A period a statement is FOR: ``FY2026``, ``Q4 FY2026``, ``H1 2026``, ``CY2026``."""

    period_type: str  # "annual" | "quarter" | "half"
    year: int
    ordinal: int | None = None
    #: Explicitly calendar (``CY2026``, "calendar 2026"); otherwise fiscal (ADR-065).
    calendar: bool = False

    @property
    def key(self) -> str:
        if self.period_type == "annual":
            return f"{'CY' if self.calendar else ''}{self.year}"
        prefix = "Q" if self.period_type == "quarter" else "H"
        return f"{self.year}-{prefix}{self.ordinal}" + (" (calendar)" if self.calendar else "")

    def calendar_end(self) -> date:
        if self.period_type == "quarter" and self.ordinal:
            month = 3 * self.ordinal
        elif self.period_type == "half" and self.ordinal:
            month = 6 * self.ordinal
        else:
            month = 12
        return date(self.year, month, _calendar.monthrange(self.year, month)[1])


_ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_MIN_YEAR, _MAX_YEAR = 1990, 2100


def _year(raw: str) -> int | None:
    value = int(raw) + (2000 if len(raw) == 2 else 0)
    return value if _MIN_YEAR <= value <= _MAX_YEAR else None


#: A declared period, normalised (lower case, every non-alphanumeric run a space).
#: Each alternative is anchored at both ends and has no unbounded repetition.
_DECLARED_RES: tuple[re.Pattern[str], ...] = (
    # "cy2026", "calendar 2026", "calendar year 2026"
    re.compile(r"^(?P<cal>cy ?|calendar (?:year )?)(?P<y>\d{4})$"),
    # "2026", "fiscal 2026", "fiscal year 2026", "financial year 2026", "full year 2026"
    re.compile(r"^(?:(?:fiscal|financial) (?:year )?|full year )?(?P<y>\d{4})$"),
    # "2026 fiscal year", "2026 financial year"
    re.compile(r"^(?P<y>\d{4}) (?:fiscal|financial) year$"),
    # "fy2026", "fy 2026", "fy26"
    re.compile(r"^fy ?(?P<y>\d{4}|\d{2})$"),
    # "q4 2026", "q4 fy2026", "q4 fy26", "2026 q4", "fy2026 q4", "fy26 q4"
    re.compile(r"^q(?P<q>[1-4]) (?:fy ?|fiscal (?:year )?)?(?P<y>\d{4}|\d{2})$"),
    re.compile(r"^(?:fy ?|fiscal (?:year )?)?(?P<y>\d{4}|\d{2}) q(?P<q>[1-4])$"),
    re.compile(r"^q(?P<q>[1-4])(?:fy ?)?(?P<y>\d{4}|\d{2})$"),
    # "fourth quarter 2026", "fourth quarter of fiscal 2026"
    re.compile(
        r"^(?P<qw>first|second|third|fourth) quarter (?:of )?(?:fiscal (?:year )?|fy ?)?"
        r"(?P<y>\d{4})$"
    ),
    # "h1 2026", "2026 h1", "h1 fy2026", "first half 2026"
    re.compile(r"^h(?P<h>[12]) (?:fy ?|fiscal (?:year )?)?(?P<y>\d{4}|\d{2})$"),
    re.compile(r"^(?:fy ?)?(?P<y>\d{4}|\d{2}) h(?P<h>[12])$"),
    re.compile(r"^h(?P<h>[12])(?:fy ?)?(?P<y>\d{4}|\d{2})$"),
    re.compile(r"^(?P<hw>first|second) half (?:of )?(?:fiscal (?:year )?|fy ?)?(?P<y>\d{4})$"),
)
_SEP_RE = re.compile(r"[^a-z0-9]+")
_ESTIMATE_SUFFIX_RE = re.compile(r"(?<=\d)e$")


def parse_declared_period(raw: object) -> Period | None:
    """The period a model declared (``"FY2026"``, ``"Q4 FY2026"``, ``"2026-Q4"``,
    ``"H1 2026"``, ``"CY2026"``), or ``None`` when it is absent or not one period. A
    range, a part period ("9M 2026", "YTD") or a date is not a period here."""
    text = _SEP_RE.sub(" ", str(raw or "").lower()[:40]).strip()
    # "FY2026E", "2026e": an analyst's estimate suffix names the same period.
    text = _ESTIMATE_SUFFIX_RE.sub("", text)
    if not text:
        return None
    calendar = text.startswith(("cy", "calendar"))
    for pattern in _DECLARED_RES:
        match = pattern.match(text)
        if match is None:
            continue
        groups = match.groupdict()
        year = _year(groups["y"]) if groups.get("y") else None
        if year is None:
            return None
        if groups.get("q") or groups.get("qw"):
            ordinal = int(groups["q"]) if groups.get("q") else _ORDINAL_WORDS[groups["qw"]]
            return Period("quarter", year, ordinal, calendar)
        if groups.get("h") or groups.get("hw"):
            ordinal = int(groups["h"]) if groups.get("h") else _ORDINAL_WORDS[groups["hw"]]
            return Period("half", year, ordinal, calendar)
        return Period("annual", year, None, calendar or bool(groups.get("cal")))
    return None


# ── The issuer's fiscal calendar ────────────────────────────────────────────── #


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, _calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class FiscalYearAnchor:
    """One observed fiscal year and the date it ended (``FY2025`` ended ``2025-11-28``),
    under the issuer's own label (SEC companyfacts ``fy``). Every other fiscal period is
    projected from it — ADR-072's anchor, reused."""

    fiscal_year: int
    period_end: date

    @property
    def ends_early_in_the_year(self) -> bool:
        """A January–March year end (a 52/53-week end on day 1–7 counts as the month
        before). Such years are named both ways in the wild (ADR-065), so quarters are
        not projected and a full year is judged on its EARLIER reading."""
        day = self.period_end
        month = day.month if day.day > 7 else (day.month - 2) % 12 + 1
        return month in (1, 2, 3)

    def year_end(self, fiscal_year: int) -> date:
        end = _add_months(self.period_end, 12 * (fiscal_year - self.fiscal_year))
        if self.ends_early_in_the_year and end.year > fiscal_year:
            return _add_months(end, -12)
        return end

    def end_of(self, period: Period) -> date | None:
        if period.period_type == "annual":
            return self.year_end(period.year)
        if self.ends_early_in_the_year or not period.ordinal:
            return None
        if period.period_type == "quarter":
            return _add_months(self.year_end(period.year), -3 * (4 - period.ordinal))
        if period.period_type == "half":
            return _add_months(self.year_end(period.year), -6 * (2 - period.ordinal))
        return None


def anchor_from_normalised(fiscal_year: object, period_end: object) -> FiscalYearAnchor | None:
    """An anchor from a normaliser's headline ``fiscal_year`` and ``reporting_period_end``."""
    from app.services.research_fields import parse_date

    try:
        year = int(str(fiscal_year))
    except (TypeError, ValueError):
        return None
    end = parse_date(period_end)
    if end is None or not _MIN_YEAR <= year <= _MAX_YEAR or abs(end.year - year) > 1:
        return None
    return FiscalYearAnchor(year, end)


def period_end(period: Period, anchor: FiscalYearAnchor | None) -> tuple[date | None, bool]:
    """``(end, exact)``. A calendar period ends on its calendar end (exact). A fiscal one
    ends where the anchor projects it (± tolerance), else at its EARLIEST possible end:
    twelve months before its calendar reading (ADR-080), so "not ended" is never claimed
    for a period that may have ended under some naming convention."""
    if period.calendar:
        return period.calendar_end(), True
    if anchor is not None:
        projected = anchor.end_of(period)
        if projected is not None:
            return projected, False
    calendar_end = period.calendar_end()
    year = calendar_end.year - 1
    return (
        date(year, calendar_end.month, _calendar.monthrange(year, calendar_end.month)[1]),
        True,
    )


def not_ended_by(
    period: Period, reference: date, anchor: FiscalYearAnchor | None = None
) -> date | None:
    """The period's end when it had demonstrably NOT ended by ``reference``; else None.
    A period ends at the end of its last day: a document dated that day does not report
    it."""
    end, exact = period_end(period, anchor)
    if end is None:
        return None
    margin = timedelta(0) if exact else FISCAL_END_TOLERANCE
    return end if reference <= end - margin else None


def wants_anchor(period: Period, reference: date) -> bool:
    """Would the issuer's calendar change the verdict? Only for a fiscal period the
    lenient reading calls ended while its calendar reading has not ended yet."""
    if period.calendar or not_ended_by(period, reference, None) is not None:
        return False
    # A start-year naming convention can put a fiscal year's end a quarter after its
    # calendar reading; past that nothing an anchor projects is still running.
    return reference <= period.calendar_end() + timedelta(days=120)


# ── The fallback: forward cues on periods the statement names ──────────────── #

#: Period mentions in NORMALISED text (``document_period._normalise``). Each alternative
#: is a short literal shape; no repetition, so a scan is linear.
_MENTION_RE = re.compile(
    r"\b(?:"
    r"fy ?(?P<fy>\d{4}|\d{2})(?: q(?P<fyq>[1-4]))?"
    r"|fiscal (?:year )?(?P<fiscal>\d{4})"
    r"|full year (?P<full>\d{4})"
    r"|q(?P<q>[1-4]) (?:fy ?)?(?P<qy>\d{4})"
    r"|(?P<yq>\d{4}) q(?P<yqq>[1-4])"
    r"|h(?P<h>[12]) (?:fy ?)?(?P<hy>\d{4})"
    r"|(?P<yh>\d{4}) h(?P<yhh>[12])"
    r"|(?P<qw>first|second|third|fourth) quarter (?:of )?(?:fiscal )?(?P<qwy>\d{4})"
    r"|(?P<year>(?:19|20)\d{2})"
    r")\b"
)
#: An analyst or consensus estimate, in normalised text.
_ESTIMATE_RE = re.compile(
    r"\b(?:consensus|analysts? (?:expect|estimate|forecast|project|see)s?"
    r"|(?:street|analyst|consensus) estimates?|estimates? (?:by|from) analysts?)\b"
)
_TARGET_WORD_RE = re.compile(r"\btarget(?:s|ed|ing)?\b")
#: Review P2: "the year ended June 30, 2026", "so far", "year to date" — a statement that
#: names an ENDED period in words (not as a period label) states a result, so the
#: fallback never relabels it forward. Normalised text: single spaces, no hyphens.
_ENDED_PERIOD_WORDS_RE = re.compile(
    r"\b(?:(?:fiscal |financial )?year|quarter|period|half(?: year)?"
    r"|(?:three|six|nine|twelve) months|weeks?) (?:then )?ended\b"
    r"|\bso far\b|\byear to date\b|\bytd\b"
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z\"'(\[])|\n+")


def _mention_period(match: re.Match[str]) -> Period | None:
    g = match.groupdict()
    if g.get("fy"):
        year = _year(g["fy"])
        if year is None:
            return None
        return Period("quarter", year, int(g["fyq"])) if g.get("fyq") else Period("annual", year)
    for key in ("fiscal", "full"):
        if g.get(key):
            year = _year(g[key])
            return Period("annual", year) if year else None
    if g.get("q") and g.get("qy"):
        year = _year(g["qy"])
        return Period("quarter", year, int(g["q"])) if year else None
    if g.get("yq") and g.get("yqq"):
        year = _year(g["yq"])
        return Period("quarter", year, int(g["yqq"])) if year else None
    if g.get("h") and g.get("hy"):
        year = _year(g["hy"])
        return Period("half", year, int(g["h"])) if year else None
    if g.get("yh") and g.get("yhh"):
        year = _year(g["yh"])
        return Period("half", year, int(g["yhh"])) if year else None
    if g.get("qw") and g.get("qwy"):
        year = _year(g["qwy"])
        return Period("quarter", year, _ORDINAL_WORDS[g["qw"]]) if year else None
    if g.get("year"):
        year = _year(g["year"])
        return Period("annual", year) if year else None
    return None


#: A year that closes a full or month date ("june 30 2026", "30 june 2026", "april
#: 2026", "6/30/2026") in normalised text: a DATE, not a period. Bounded repetitions.
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]{0,6}"
_DATE_PREFIX_RE = re.compile(
    rf"(?:\b{_MONTH}\.? ?(?:\d{{1,2}}(?:st|nd|rd|th)?,? ?)?"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)? {_MONTH}\.?,? ?"
    r"|\b\d{1,2}[/.\-]\d{1,2}[/.\-])$"
)
_DATE_PREFIX_CHARS = 20


def _is_date_year(norm: str, match: re.Match[str]) -> bool:
    """Is a bare-year mention the year of a date rather than a period?"""
    if not match.groupdict().get("year"):
        return False
    return bool(_DATE_PREFIX_RE.search(norm[max(0, match.start() - _DATE_PREFIX_CHARS):
                                            match.start()]))


def forward_kind(
    statement: str | None, reference: date, anchor: FiscalYearAnchor | None = None,
    *, mixed_is_none: bool = False,
) -> str | None:
    """The forward kind the statement's OWN words carry for a period that had not ended
    by ``reference`` — ``guidance``, ``target``, ``forecast`` or ``estimate`` — else
    ``None``. A forward cue about an ENDED period ("FY2025 revenue beat guidance") is
    history and gives nothing; a year inside a date is no period. With
    ``mixed_is_none`` a statement that also names an ENDED period gives nothing (it
    states a result beside the expectation). Never raises."""
    from app.services.sources import date_semantics as ds
    from app.services.sources.document_period import _normalise

    text = str(statement or "")[:2000]
    if mixed_is_none and _ENDED_PERIOD_WORDS_RE.search(_normalise(text)):
        # "Revenue for the year ended June 30, 2026 was $70bn; FY2027 outlook is $78bn":
        # a result beside an expectation is mixed, and the fallback never relabels it.
        return None
    found: str | None = None
    estimate = False
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        norm = _normalise(sentence)
        if not norm:
            continue
        estimate_words = bool(_ESTIMATE_RE.search(norm))
        for match in _MENTION_RE.finditer(norm):
            period = _mention_period(match)
            if period is None or _is_date_year(norm, match):
                continue
            if not_ended_by(period, reference, anchor) is None:
                if mixed_is_none:
                    return None
                continue
            if estimate_words:
                estimate = True
                if not mixed_is_none:
                    return KIND_ESTIMATE
                continue
            role = ds.read_date_mention(norm, match.start(), match.end()).role
            if role == ds.GUIDANCE_TARGET_DATE:
                window = norm[max(0, match.start() - ds.PREFIX_WINDOW): match.end()
                              + ds.SUFFIX_WINDOW]
                found = found or (
                    KIND_TARGET
                    if _TARGET_WORD_RE.search(window)
                    # Case-sensitive on the ORIGINAL sentence too: a capitalised
                    # "Target" may be a company's name (re-review P1-b).
                    and _TARGET_WORD_RE.search(sentence)
                    else KIND_GUIDANCE
                )
            elif role == ds.FORECAST_HORIZON:
                found = found or KIND_FORECAST
    return KIND_ESTIMATE if estimate else found


# ── The decision ────────────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class KindDecision:
    """What one finding is recorded as."""

    kind: str
    basis: str
    #: Kept in the ledger, never shown as a result (``verification_status='withdrawn'``).
    withheld: bool = False
    #: The judged period and its end, when the invariant fired.
    period: Period | None = None
    period_end: date | None = None

    @property
    def basis_detail(self) -> str:
        """``statement_kind_basis`` as stored: the basis, and for the invariant the
        period and its end (no statement text)."""
        if self.period is not None and self.period_end is not None:
            return f"{self.basis}:{self.period.key}@{self.period_end.isoformat()}"[:120]
        return self.basis


def reference_date(published_at: date | None, as_of: date) -> date:
    """The date a statement's period must have ended by: its newest cited source's
    publication date when known (no earlier document can report a later period), never
    later than the run date."""
    return min(published_at, as_of) if published_at is not None else as_of


def decide(
    *,
    declared_kind: object,
    declared_period: object,
    statement: str | None,
    published_at: date | None,
    as_of: date,
    anchor: FiscalYearAnchor | None = None,
) -> KindDecision:
    """Record a statement's kind, then hold it to the invariant. Pure; never raises.

    The period judged is the DECLARED one only — never one read from the statement's
    words (what ADR-072 tried), never the evidence's own period tag (module docstring §2).
    """
    reference = reference_date(published_at, as_of)
    kind = normalise_kind(declared_kind)
    basis = BASIS_MODEL
    period = parse_declared_period(declared_period)
    if kind is None or (kind == KIND_ACTUAL and period is None):
        # The fallback: labels forward only, at the RUN date, never on a mixed statement.
        forward = forward_kind(statement, as_of, anchor, mixed_is_none=True)
        if forward is not None:
            return KindDecision(forward, BASIS_FORWARD_CUE)
        if kind is None:
            kind, basis = KIND_UNKNOWN, BASIS_NOT_DECLARED
    # A DECLARED non-actual kind is kept, whatever its period (re-review P1-a): guidance
    # or an estimate for a period that has since ended ("had called for FY2025 revenue of
    # $10.2bn", "Street FY2025 EPS ahead of the print") is still not a result, and must
    # never move into the actual-eligible group.
    if kind not in (KIND_ACTUAL, KIND_UNKNOWN) or period is None:
        return KindDecision(kind, basis)
    end = not_ended_by(period, reference, anchor)
    if end is None:
        return KindDecision(kind, basis)
    forward = forward_kind(statement, reference, anchor)
    if forward is not None:
        return KindDecision(forward, BASIS_RECLASSIFIED, period=period, period_end=end)
    return KindDecision(KIND_UNKNOWN, BASIS_WITHHELD, withheld=True, period=period, period_end=end)


def judged_period(declared_period: object) -> Period | None:
    """The period :func:`decide` would judge, for the caller deciding whether to read
    the issuer's calendar."""
    return parse_declared_period(declared_period)


# ── The issuer's calendar, from its own SEC statements (ADR-072's loader) ───── #

_ANCHOR_CACHE: dict[str, tuple[datetime, FiscalYearAnchor | None]] = {}
ANCHOR_CACHE_TTL = timedelta(hours=12)
#: A failed read is retried sooner: an SEC timeout must not switch the calendar off all day.
ANCHOR_FAILURE_TTL = timedelta(minutes=15)
ANCHOR_CACHE_MAX = 256
#: The whole read is bounded, so a slow data.sec.gov costs one write-up a few seconds.
ANCHOR_READ_TIMEOUT_SECONDS = 5.0


async def load_fiscal_year_anchor(
    ticker: str | None, exchange: str | None
) -> FiscalYearAnchor | None:
    """The issuer's latest ANNUAL period from its SEC statements, or ``None``. Never
    raises. The same cached reader and normaliser as ``get_sec_statements``; a subject
    SEC does not cover has no anchor, and then fiscal periods are judged leniently."""
    if not ticker or not str(exchange or "").strip():
        # Review P3: without the listing's exchange a bare ticker can resolve to a
        # different SEC registrant ("MC" is Moelis, not LVMH), and that registrant's
        # calendar would then judge the subject's periods. No exchange, no anchor.
        return None
    key = f"{ticker.upper()}|{str(exchange).upper()}"
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
    except Exception:  # noqa: BLE001 - no calendar means the lenient reading, never a failure
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
    normalised = await asyncio.to_thread(
        sec_fundamentals_normalizer.normalize_company_facts, facts, ticker
    )
    if normalised.period_basis != "annual":
        return None
    return anchor_from_normalised(normalised.fiscal_year, normalised.reporting_period_end)


def reset_cache() -> None:
    _ANCHOR_CACHE.clear()


__all__ = [
    "BASIS_FORWARD_CUE",
    "BASIS_MODEL",
    "BASIS_NOT_DECLARED",
    "BASIS_RECLASSIFIED",
    "BASIS_WITHHELD",
    "FISCAL_END_TOLERANCE",
    "FiscalYearAnchor",
    "KIND_ACTUAL",
    "KIND_ESTIMATE",
    "KIND_FORECAST",
    "KIND_GUIDANCE",
    "KIND_LABELS",
    "KIND_TARGET",
    "KIND_UNKNOWN",
    "KindDecision",
    "NON_ACTUAL_KINDS",
    "Period",
    "STATEMENT_KINDS",
    "anchor_from_normalised",
    "decide",
    "forward_kind",
    "is_non_actual",
    "judged_period",
    "load_fiscal_year_anchor",
    "normalise_kind",
    "not_ended_by",
    "parse_declared_period",
    "period_end",
    "reference_date",
    "reset_cache",
    "wants_anchor",
]
