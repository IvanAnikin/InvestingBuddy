"""The reporting period a DOCUMENT itself covers — current-period acceptance.

``financial_period`` models the period of one extracted VALUE. This module
answers the prior question: *what period is this document about?*

It exists because of a confirmed, dangerous live failure. Richemont's quarterly
sales release for the quarter ended 30 June 2026 states its headline figure as
plain prose — "Group sales at € 6.3 billion" — with no year in the sentence at
all. ``extracted_fact_validator`` then fell back to the document's DOMINANT
explicit year, which in that release is the bare token ``2026``, and stamped the
quarter's sales as **annual 2026** revenue. Beside the FY2026 annual Group
revenue of € 22.4 billion, that is the ``INTERIM_AS_ANNUAL`` contradiction in
its worst form: a three-month figure competing for the full-year slot.

The fallback was never wrong to exist — a real report states its period once and
then omits it from later sentences. It was wrong to be period-TYPE blind. A
document that says, in its own title and its own headings, that it covers one
quarter cannot supply an ANNUAL period to anything.

Deliberately a PURE VALUE module: it reads text somebody else already fetched,
and it decides nothing about facts, selection or promotion.

Fail-closed throughout. Every rule reads the document's OWN words — its title,
its URL as the issuer published it, its leading headings. Nothing is derived
from a fiscal calendar, a publication date or a company registry, because a
document that does not state its period simply has none here (``UNKNOWN``), and
an unknown document period changes no existing behaviour.

ADR-080 (QA campaign CLLS5-1 / CLLS4-2) adds two refusals and one stronger source:

* a mention is read as the document's period only when it is FRAMED as the period the
  document reports (``date_semantics``). "Cash runway into H2 2028" stamped a 2026
  strategic-update 6-K "2028-H2"; a runway, a guidance year, a maturity or a readout is
  now skipped and the scan moves on;
* a period that had not begun by the document's publication date (``as_of``, when the
  caller knows it) is a forecast and is skipped the same way. The date only ever
  REFUSES a period; it never supplies one;
* ``sec_period_of_report`` reads the regulator's structured period of report (EDGAR
  ``reportDate``) consistently with the form. Filing metadata outranks every rule here.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.services.sources.date_semantics import (
    FRAMING_DEFAULT,
    FRAMING_WEAK,
    REPORTING_PERIOD,
    ROLE_BODY,
    ROLE_HEADING,
    ROLE_HEADLINE,
    ROLE_TITLE,
    is_forward_label,
    read_date_mention,
)
from app.services.sources.financial_period import (
    PERIOD_TYPE_ANNUAL,
    PERIOD_TYPE_HALF,
    PERIOD_TYPE_QUARTER,
    UNKNOWN_PERIOD,
    ReportingPeriod,
)

# ── How a period was determined (closed vocabulary, for the audit trail) ──── #

BASIS_UNKNOWN = "unknown"
#: "FY27 Q1" / "Q1 FY2027" — the issuer's own combined fiscal-year label.
BASIS_FISCAL_LABEL = "fiscal_period_label"
#: "Q2 2026" / "H1 2026" — an unambiguous four-digit period label.
BASIS_PERIOD_LABEL = "period_label"
#: "for its first quarter ended 30 June 2026" — a period-end sentence.
BASIS_PERIOD_END_PHRASE = "period_end_phrase"
#: "first quarter ended 30 June 2026" read with its printed END month: the key names the
#: FISCAL year (2027-Q1), so it is fiscal for every rule that asks whether the period
#: had begun when the document was published.
BASIS_FISCAL_PERIOD_END = "fiscal_period_end_phrase"
#: ADR-080 — the regulator's structured "period of report" (EDGAR ``reportDate``), read
#: consistently with the form. Filing METADATA rather than the document's prose, so it
#: outranks every rule below that reads words.
BASIS_SEC_PERIOD_OF_REPORT = "sec_period_of_report"
#: The same, for a quarter whose key names a FISCAL year other than its end year (a June
#: year-end's quarter ended September 2026 is 2027-Q1): fiscal for the began-by rule.
BASIS_SEC_FISCAL_PERIOD_OF_REPORT = "sec_fiscal_period_of_report"

#: Hard bound on how much document text is scanned. The period is stated in the
#: title and the first headings; scanning a whole annual report would only add
#: chances to read a comparative year as the document's own.
_MAX_SCAN_CHARS = 4_000
_MAX_HEADINGS = 12
#: Security review of PR #323: candidates read per rule and source. ADR-080 refuses
#: mentions and reads on, so a page of 500 refused labels cost 500 classifications;
#: a document's own period sits among its first few mentions.
_MAX_CANDIDATES_PER_SOURCE = 48

_MIN_YEAR = 1990
_MAX_YEAR = 2100

_WS_RE = re.compile(r"\s+")
_SEP_RE = re.compile(r"[^a-z0-9]+")

# ── Rule 1 — the issuer's own combined fiscal-period label ────────────────── #
#
# "fy27 q1", "q1 fy27", "fy2027 q1", "h1 fy26". A two-digit fiscal year is
# accepted ONLY when it sits immediately beside a quarter/half token: on its own
# it is the guess ``financial_period.parse_period`` rightly refuses, but "FY27
# Q1" in an issuer's own filename is that issuer stating which fiscal year its
# first quarter belongs to, and no other reading of the pair exists.
_FISCAL_FIRST_RE = re.compile(
    r"\bfy\s*(?P<year>\d{2}|\d{4})\s*(?P<kind>[qh])\s*(?P<ordinal>[1-4])\b"
)
_PERIOD_FIRST_RE = re.compile(
    r"\b(?P<kind>[qh])\s*(?P<ordinal>[1-4])\s*fy\s*(?P<year>\d{2}|\d{4})\b"
)

# ── Rule 2 — an unambiguous four-digit period label ───────────────────────── #
_LABEL_RE = re.compile(
    r"\b(?:(?P<kind1>[qh])(?P<ord1>[1-4])\s*(?:fy\s*)?(?P<y1>(?:19|20)\d{2})"
    r"|(?P<y2>(?:19|20)\d{2})\s*(?P<kind2>[qh])(?P<ord2>[1-4]))\b"
)

# ── Rule 3 — a period-end sentence ────────────────────────────────────────── #
#
# The ordinal words a real release uses. "nine months" is deliberately ABSENT:
# a nine-month cumulative period is neither a quarter nor a half, and this
# module has no representation for it — inventing one would be exactly the kind
# of quiet mismapping it exists to prevent.
_ORDINAL_WORDS: tuple[tuple[str, str, int], ...] = (
    ("first quarter", PERIOD_TYPE_QUARTER, 1),
    ("1st quarter", PERIOD_TYPE_QUARTER, 1),
    ("second quarter", PERIOD_TYPE_QUARTER, 2),
    ("2nd quarter", PERIOD_TYPE_QUARTER, 2),
    ("third quarter", PERIOD_TYPE_QUARTER, 3),
    ("3rd quarter", PERIOD_TYPE_QUARTER, 3),
    ("fourth quarter", PERIOD_TYPE_QUARTER, 4),
    ("4th quarter", PERIOD_TYPE_QUARTER, 4),
    ("first half", PERIOD_TYPE_HALF, 1),
    ("first six months", PERIOD_TYPE_HALF, 1),
    ("six months", PERIOD_TYPE_HALF, 1),
    ("six month", PERIOD_TYPE_HALF, 1),
    ("half year", PERIOD_TYPE_HALF, 1),
    ("half-year", PERIOD_TYPE_HALF, 1),
    ("halfyear", PERIOD_TYPE_HALF, 1),
    ("second half", PERIOD_TYPE_HALF, 2),
)
#: The year must belong to the SAME sentence-sized window as the period phrase,
#: so an unrelated year elsewhere on the page can never date the document.
_PERIOD_END_WINDOW = 120
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_MONTH_IN_WINDOW_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b")


@dataclass(frozen=True)
class DocumentPeriod:
    """The period a document covers, with how that was decided."""

    period: ReportingPeriod = UNKNOWN_PERIOD
    basis: str = BASIS_UNKNOWN
    #: The document's own phrase, kept verbatim so a reader can check the call.
    evidence: str | None = None

    @property
    def is_known(self) -> bool:
        return not self.period.is_unknown

    @property
    def is_interim(self) -> bool:
        """True when the document covers PART of a year.

        This is the property the whole module exists for: it is what forbids an
        annual period being inferred for a value the document never dated.
        """
        return self.period.is_interim

    def label(self) -> str:
        return self.period.label()


UNKNOWN_DOCUMENT_PERIOD = DocumentPeriod()

#: Review round 2, H1 — titles that say a document covers PART of a year even when no
#: period can be read from them ("Interim Results", "Half-year Report", "Appendix 4D
#: and Half Year Financial Report", "Quarterly Activities and Cashflow Report"). An
#: "interim dividend" is not an interim report.
_PART_YEAR_TITLE_RE = re.compile(
    r"\bhalf[- ]?year(?:ly)?\b|\binterim\b(?!\s+dividend)|\bappendix\s+(?:4d|4c|5b)\b"
    r"|\bquarter(?:ly)?\b|\b(?:three|six|nine|3|6|9)[- ]months?\b|\b(?:H[12]|Q[1-4])\b",
    re.IGNORECASE,
)


#: A title that states a FULL year: an annual report or full-year results. Wins over
#: an incidental part-year word ("Annual Report 2025 including fourth quarter review").
_ANNUAL_TITLE_RE = re.compile(
    r"\bannual\s+(?:financial\s+)?(?:report|accounts|results)\b|\bfull[- ]year\s+results\b"
    r"|\b(?:final|preliminary)\s+(?:final\s+)?(?:results|report)\b|\bappendix\s+4e\b"
    r"|\bannual\s+report\s+and\s+accounts\b",
    re.IGNORECASE,
)


def title_states_annual(title: str | None) -> bool:
    """True when a document's own title says it covers a full year."""
    return bool(title and _ANNUAL_TITLE_RE.search(title))


def title_states_part_year(title: str | None) -> bool:
    """True when a document's own title says it covers part of a year (and does not
    also say it is an annual report or full-year results)."""
    return bool(title and _PART_YEAR_TITLE_RE.search(title) and not title_states_annual(title))


def _expand_year(raw: str) -> int | None:
    year = int(raw)
    if len(raw) == 2:
        year += 2000
    return year if _MIN_YEAR <= year <= _MAX_YEAR else None


def _period_of(kind: str, ordinal: int, year: int) -> ReportingPeriod | None:
    if kind == "q":
        return ReportingPeriod(PERIOD_TYPE_QUARTER, year, ordinal, f"Q{ordinal} {year}")
    if kind == "h" and ordinal in (1, 2):
        return ReportingPeriod(PERIOD_TYPE_HALF, year, ordinal, f"H{ordinal} {year}")
    return None


def _normalise(text: str) -> str:
    """Lower-case, separator-flattened text — the form every rule reads.

    A URL slug (``…-fy27-q1-sales-en.pdf``) and a printed heading ("FY27 Q1
    Sales") reduce to the same words, so one keyword table covers both.
    """
    lowered = _SEP_RE.sub(" ", (text or "").lower())
    return _WS_RE.sub(" ", lowered).strip()


#: ADR-080 — where a sentence ends in body text. Used only to bound the window a date
#: mention is classified in, so "…FY27 Q1 guidance. Q3 2026 results" does not read the
#: previous sentence's "guidance" as framing the Q3 label. Matching is unaffected.
#: A sentence ends only where the next one starts with a capital, a quote or a bracket,
#: so "Sept. 30, 2026" and "U.S. $5" are never split.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z\"'(\[“‘])|\n+")
#: Review of PR #323 — where a CLAUSE ends in a title or a heading: "Q3 2026: Revenue up
#: 12%, full-year guidance raised", "Strong H1 2026; outlook upgraded", "Q1 FY27 update –
#: guidance maintained", "Earnings release | Q3 2026". A later clause's outlook is not the
#: headline's period. A colon or comma counts only before a space ("10:30", "1,234" are
#: not clauses), a hyphen only between spaces ("first-half" is one word).
#: Security review of PR #323: at most EIGHT spaces may precede the delimiter. An
#: unbounded leading ``\s*`` backtracked quadratically on a run of whitespace (a title of
#: 10,000 spaces took 16 s on the event loop — the gunicorn SIGKILL class); every
#: attempt now costs O(1) before the delimiter must match.
_CLAUSE_END_RE = re.compile(
    r"[ \t]{0,8}(?:[;|–—]|[:,](?=\s)|(?<=\s)-(?=\s))\s*")
#: Titles and headings are cut to this length before they are read: a real one is far
#: shorter, and nothing upstream bounds them.
_MAX_TITLE_CHARS = 500
#: The first body segment is the document's headline line ("Third Quarter 2026
#: Highlights", "Acme Q3 2026 Earnings") when it is no longer than this
#: (``date_semantics.ROLE_HEADLINE``). A longer first segment is running text (a cover
#: page, a lead paragraph).
_HEADLINE_MAX_CHARS = 200


@dataclass(frozen=True)
class _Source:
    """One normalised source, with where its segments start and how each is framed."""

    text: str
    #: Offsets in ``text`` where a segment (a sentence, a clause, a heading) starts.
    bounds: tuple[int, ...]
    #: The framing role of each segment, aligned with ``bounds``.
    roles: tuple[str, ...]

    def role_at(self, offset: int) -> str:
        index = 0
        for i, bound in enumerate(self.bounds):
            if bound <= offset:
                index = i
        return self.roles[index] if self.roles else ROLE_BODY


def _source(parts: "list[tuple[str, str]]") -> _Source:
    """Normalise each ``(part, role)`` on its own and join them with one space.

    The joined text is what ``_normalise`` makes of the parts joined with whitespace —
    the rules read exactly the text they always read — and the segment starts and roles
    are kept for the classifier.
    """
    pieces: list[str] = []
    bounds: list[int] = []
    roles: list[str] = []
    offset = 0
    for part, role in parts:
        piece = _normalise(part)
        if not piece:
            continue
        if pieces:
            offset += 1
        bounds.append(offset)
        roles.append(role)
        pieces.append(piece)
        offset += len(piece)
    return _Source(" ".join(pieces), tuple(bounds), tuple(roles))


def _clauses(texts: "list[str] | tuple[str, ...]", role: str) -> list[tuple[str, str]]:
    return [
        (clause, role)
        for text in texts
        for clause in _CLAUSE_END_RE.split((text or "").strip()[:_MAX_TITLE_CHARS])
    ]


def _body_parts(text: str) -> list[tuple[str, str]]:
    sentences = _SENTENCE_END_RE.split(text[:_MAX_SCAN_CHARS])
    parts: list[tuple[str, str]] = []
    for index, sentence in enumerate(sentences):
        headline = sentence.strip()
        if index == 0 and len(headline) <= _HEADLINE_MAX_CHARS:
            parts.extend(_clauses([headline], ROLE_HEADLINE))
        else:
            parts.append((sentence, ROLE_BODY))
    return parts


def _segment_index(source: _Source, offset: int) -> int:
    index = 0
    for i, bound in enumerate(source.bounds):
        if bound <= offset:
            index = i
    return index


def _segment_text(source: _Source, index: int) -> str:
    if not 0 <= index < len(source.bounds):
        return ""
    high = source.bounds[index + 1] if index + 1 < len(source.bounds) else len(source.text)
    return source.text[source.bounds[index]:high].strip()


def _framing_of(source: _Source, start: int, end: int) -> tuple[str, bool] | None:
    """How ``source.text[start:end]`` is framed as the period the document reports
    (``date_semantics.FRAMING_*``) and whether a forward or notice cue — or a clause
    that is only a forward label — sits beside it; ``None`` when it is not a reporting
    period.

    The classification window is the segment(s) the mention sits in, read with the
    framing role of the segment it starts in. Re-review of PR #323: an un-cued mention
    in a title, heading or headline clause is NOT a reporting period when a clause next
    to it is only a forward label — "Outlook: Q4 2026", "Q4 2026: guidance", "Trading
    outlook | H2 2026" are split by punctuation, not by meaning."""
    low = max((b for b in source.bounds if b <= start), default=0)
    high = min((b for b in source.bounds if b >= end), default=len(source.text))
    segment = source.text[low:high]
    role = source.role_at(start)
    reading = read_date_mention(segment, start - low, end - low, role=role)
    if reading.role != REPORTING_PERIOD:
        return None
    forward_nearby = reading.forward_nearby
    if role in (ROLE_TITLE, ROLE_HEADING, ROLE_HEADLINE):
        first = _segment_index(source, start)
        last = _segment_index(source, max(start, end - 1))
        if any(is_forward_label(_segment_text(source, neighbour))
               for neighbour in (first - 1, last + 1)):
            if reading.framing == FRAMING_DEFAULT:
                return None
            forward_nearby = True
    return reading.framing or FRAMING_DEFAULT, forward_nearby


#: Re-review of PR #323 — a period framed only WEAKLY (a measure, a lead verb, "for
#: the") or only by a headline line's position must be OVER by the publication date:
#: "Company expects record Q4 2026 sales", "Pre-announcement of Q4 2026 sales" and
#: "Announced Q4 2026 restructuring plan" name a period still running. In body text it
#: must also be RECENT — a results release follows its period closely, while "Revenue
#: for Q3 2025 was $2m" in a 2026 strategic update is a comparative.
_MAX_BODY_REPORTING_LAG_DAYS = 200


def _calendar_end(found: DocumentPeriod) -> date | None:
    period = found.period
    if period.year is None:
        return None
    if period.period_type == PERIOD_TYPE_QUARTER and period.ordinal:
        month = 3 * int(period.ordinal)
    elif period.period_type == PERIOD_TYPE_HALF and period.ordinal:
        month = 6 * int(period.ordinal)
    elif period.period_type == PERIOD_TYPE_ANNUAL:
        month = 12
    else:
        return None
    year = int(period.year)
    return date(year, month, calendar.monthrange(year, month)[1])


def _is_fiscal_basis(basis: str) -> bool:
    return basis in (BASIS_FISCAL_LABEL, BASIS_FISCAL_PERIOD_END,
                     BASIS_SEC_FISCAL_PERIOD_OF_REPORT)


def _ended_by(found: DocumentPeriod, as_of: date) -> bool:
    """Had the period ENDED by ``as_of``? A fiscal key names the year its fiscal year
    ends in, so its quarter may end up to twelve months before the calendar reading —
    the same lenient estimate the began-by rule uses."""
    end = _calendar_end(found)
    if end is None:
        return True
    if _is_fiscal_basis(found.basis):
        year = end.year - 1
        end = date(year, end.month, calendar.monthrange(year, end.month)[1])
    # STRICTLY after the last day: a release dated the day its quarter ends reports a
    # quarter that is not over.
    return end < as_of


def _ended_recently(found: DocumentPeriod, as_of: date) -> bool:
    """Did the period end no more than ``_MAX_BODY_REPORTING_LAG_DAYS`` before
    ``as_of``? A fiscal period is read at its LATEST possible end (the calendar
    reading), so the rule never refuses it for being old when it may not be."""
    end = _calendar_end(found)
    return end is None or (as_of - end).days <= _MAX_BODY_REPORTING_LAG_DAYS


def _unfit_for_its_date(found: DocumentPeriod, framing: str, forward_nearby: bool,
                        role: str, as_of: date | None) -> bool:
    """Whether a reporting-framed candidate is still refused by its publication date.

    * BODY text (final re-review of PR #323, CLLS4-2): the period must have ENDED,
      whatever the framing — "Cellectis will report its fourth quarter … 2026 results in
      March 2027" in a strategic update is not a fourth-quarter report.
    * A title, heading or headline with a forward or NOTICE cue (or a forward-label
      clause) beside the period: it must have ended — "Acme to report Q4 2026 results",
      "Invitation to Q4 2026 results presentation".
    * WEAK framing, or a headline's no-cue default: it must have ended; in body text and
      the headline line also RECENTLY.
    """
    if as_of is None:
        return False
    ended = _ended_by(found, as_of)
    if not ended and role == ROLE_BODY:
        return True
    if not ended and forward_nearby:
        return True
    headline_default = framing == FRAMING_DEFAULT and role == ROLE_HEADLINE
    if framing != FRAMING_WEAK and not headline_default:
        return False
    if not ended:
        return True
    return role in (ROLE_BODY, ROLE_HEADLINE) and not _ended_recently(found, as_of)


#: A candidate: the period, and where in the normalised text it was read.
_Candidate = tuple[DocumentPeriod, int, int]


def _overlapping(regex: re.Pattern[str], text: str) -> Iterator[re.Match[str]]:
    """Every match, overlapping ones included: in "…half of 2031 Q3 2026 results" the
    refused "2031 Q3" must not swallow the "Q3" of "Q3 2026"."""
    pos = 0
    while True:
        match = regex.search(text, pos)
        if match is None:
            return
        yield match
        pos = match.start() + 1


def _fiscal_label_candidates(text: str) -> Iterator[_Candidate]:
    for regex in (_FISCAL_FIRST_RE, _PERIOD_FIRST_RE):
        for match in _overlapping(regex, text):
            year = _expand_year(match.group("year"))
            if year is None:
                break
            period = _period_of(match.group("kind"), int(match.group("ordinal")), year)
            if period is None:
                break
            yield DocumentPeriod(period, BASIS_FISCAL_LABEL, match.group(0)), *match.span()


def _period_label_candidates(text: str) -> Iterator[_Candidate]:
    for match in _overlapping(_LABEL_RE, text):
        kind = match.group("kind1") or match.group("kind2")
        ordinal = match.group("ord1") or match.group("ord2")
        year_text = match.group("y1") or match.group("y2")
        year = _expand_year(year_text)
        if year is None:
            return
        period = _period_of(kind, int(ordinal), year)
        if period is None:
            return
        yield DocumentPeriod(period, BASIS_PERIOD_LABEL, match.group(0)), *match.span()


def _first(candidates: Iterator[_Candidate]) -> DocumentPeriod | None:
    for found, _start, _end in candidates:
        return found
    return None


def _match_fiscal_label(text: str) -> DocumentPeriod | None:
    return _first(_fiscal_label_candidates(text))


def _match_period_label(text: str) -> DocumentPeriod | None:
    return _first(_period_label_candidates(text))


#: The END month of a quarter, only inside an "ended / ending / to / through" clause, so
#: a modal "may" ("first quarter results may vary") is never read as May.
_QUARTER_END_MONTH_RE = re.compile(
    r"\b(?:ended|ending|to|through)\b[a-z0-9 ]{0,16}?"
    r"\b(?:(?P<day_before>\d{1,2}) )?"
    r"(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"
    r"(?: (?P<day_after>\d{1,2})\b)?"
)
#: A 52/53-week quarter can end in the first days of the next month ("first quarter
#: ended April 4, 2026" is a calendar-March quarter): up to this day it counts as the
#: month before.
_WEEK_YEAR_SPILL_DAYS = 7
_MONTH_NUMBER = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _spill_adjusted(month: int, year: int, day: int | None) -> tuple[int, int]:
    """``(month, year)`` a period ending on that day ends in: a 52/53-week period that
    spilled a few days into the next month ends in the month before."""
    if day is not None and day <= _WEEK_YEAR_SPILL_DAYS:
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return month, year


def _fiscal_year_of_quarter(ordinal: int, end_month: str, end_year: int,
                            end_day: int | None = None) -> int | None:
    """The fiscal year a quarter belongs to, named by the year that fiscal year ENDS.

    "First quarter ended 30 June 2026" is Q1 of a fiscal year ending March 2027: the
    issuer's own label for it is "FY27 Q1" (Richemont), which rule 1 reads as
    2027-Q1. Keying the phrase by the quarter's end year (2026-Q1) gave the same
    quarter two keys. QA campaign 2026-11: Richemont's HTML release (slug "…first-
    quarter-ended-30-june-2026") and its PDF ("…fy27-q1-sales…") disagreed, so a
    statement citing both was discarded as a period conflict and another was labelled
    2026-Q1. This is also SEC's ``fy`` convention (Microsoft Q1 ended 30 September
    2026 is fy 2027).

    A fiscal year ending in January or February is named both ways (Walmart names the
    year to January 2027 "fiscal 2027", Home Depot the year to February 2026 "fiscal
    2025"), so no year is stated for it.
    """
    month = _MONTH_NUMBER.get(end_month[:3])
    if month is None:
        return None
    month, end_year = _spill_adjusted(month, end_year, end_day)
    fiscal_end_month = month + 3 * (4 - ordinal)
    fiscal_year = end_year + (fiscal_end_month - 1) // 12
    if (fiscal_end_month - 1) % 12 + 1 in (1, 2):
        return None
    return fiscal_year


def _quarter_end_year(month: str, year: int, day: int | None) -> int:
    """The calendar year a quarter printed as ending on that day ends in."""
    number = _MONTH_NUMBER.get(month[:3])
    return year if number is None else _spill_adjusted(number, year, day)[1]


def _match_period_end_phrase(text: str) -> DocumentPeriod | None:
    """"…for its first quarter ended 30 June 2026" → 2027-Q1 (fiscal year to March 2027).

    A quarter's year is its FISCAL year, worked out from the printed end month (see
    ``_fiscal_year_of_quarter``); without an end month it is the year as printed.
    When the issuer also states a fiscal-year label (rule 1) that label wins,
    because it is the issuer's own answer to which fiscal year the quarter
    belongs to; this rule is the fallback for a document that never states one.
    """
    return _first(_period_end_candidates(text))


#: Review of PR #323 — the issuer STATES the fiscal year between the period and the
#: year: "First Quarter Fiscal Year 2027" (Cisco), "first quarter of fiscal 2027"
#: (Micron), "First-Quarter Fiscal 2027" (Accenture), "Fourth-Quarter and Full-Year
#: Fiscal 2026", "Fourth Quarter and Full Year Earnings for Fiscal 2026". The year is
#: then a fiscal year — the key keeps it, and the began-by rule reads it as fiscal
#: (NVIDIA's "First Quarter Fiscal 2027" is published in May 2026).
_STATED_FISCAL_RE = re.compile(
    r" (?:and full year )?(?:(?:financial )?(?:results|earnings) )?(?:(?:of|for|in) )?"
    r"(?:fiscal(?: year)?|fy) "
)
#: The END date printed AFTER the year: "first quarter of fiscal 2027 ended November 27,
#: 2026". Read only beside a stated fiscal year, to check the two agree.
_TRAILING_END_RE = re.compile(
    r" (?:ended|ending) (?:(?P<day_before>\d{1,2}) )?"
    r"(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"(?: (?P<day_after>\d{1,2}))? (?P<year>(?:19|20)\d{2})\b"
)


def _period_end_candidates(text: str) -> Iterator[_Candidate]:
    """Every period-end phrase, in table order and then text order. A phrase whose
    first reading names no period this model can state is abandoned, exactly as before
    ADR-080; a later occurrence is read only after an earlier one was REFUSED by the
    caller (a forward mention, or a period that had not begun)."""
    for phrase, period_type, ordinal in _ORDINAL_WORDS:
        index = text.find(phrase)
        while index >= 0:
            found = _period_end_at(text, index, period_type, ordinal, len(phrase))
            if found is None:
                break
            yield found
            index = text.find(phrase, index + 1)


def _period_end_at(text: str, index: int, period_type: str, ordinal: int,
                   phrase_length: int = 0) -> _Candidate | None:
    window = text[index : index + _PERIOD_END_WINDOW]
    year_match = _YEAR_RE.search(window)
    if year_match is None:
        return None
    year = _expand_year(year_match.group(1))
    if year is None:
        return None
    end = index + year_match.end()
    if phrase_length and _STATED_FISCAL_RE.fullmatch(
        window[phrase_length:year_match.start()]
    ):
        return _stated_fiscal_period(text, window, index, end, period_type, ordinal, year)
    if period_type == PERIOD_TYPE_HALF:
        # Review round 2, H2 — "half year ended 31 December 2025" is the first half
        # of a JUNE fiscal year, not calendar H1 2025. A half that ends in another
        # month than its calendar half's names no half this model can state.
        end_month = _MONTH_IN_WINDOW_RE.search(window[: year_match.start()])
        if end_month is not None and end_month.group(1) != ("jun" if ordinal == 1 else "dec"):
            return None
    if period_type == PERIOD_TYPE_QUARTER:
        quarter_end = _QUARTER_END_MONTH_RE.search(window[: year_match.start()])
        if quarter_end is not None:
            day_text = quarter_end.group("day_before") or quarter_end.group("day_after")
            day = int(day_text) if day_text else None
            month = quarter_end.group("month")
            fiscal_year = _fiscal_year_of_quarter(ordinal, month, year, day)
            if fiscal_year is None:
                return None
            period = _period_of("q", ordinal, fiscal_year)
            if period is None:
                return None
            # Only a SHIFTED year is fiscal — compared with the year the quarter ENDS
            # in, after a 52/53-week spill ("ended January 3, 2027" ends in December
            # 2026). A calendar quarter keeps the strict began-by rule, so "guidance
            # for the first quarter ending March 31, 2027" published in 2026 is still
            # a forecast (review round 4).
            basis = (BASIS_FISCAL_PERIOD_END
                     if fiscal_year != _quarter_end_year(month, year, day)
                     else BASIS_PERIOD_END_PHRASE)
            return DocumentPeriod(period, basis, window.strip()[:120]), index, end
    kind = "q" if period_type == PERIOD_TYPE_QUARTER else "h"
    period = _period_of(kind, ordinal, year)
    if period is None:
        return None
    return DocumentPeriod(period, BASIS_PERIOD_END_PHRASE, window.strip()[:120]), index, end


def _stated_fiscal_period(text: str, window: str, index: int, end: int,
                          period_type: str, ordinal: int, year: int) -> _Candidate | None:
    """A period whose fiscal year the issuer states ("first quarter fiscal 2027").

    The stated year is the key. An end date printed after it ("… fiscal 2027 ended
    November 27, 2026") must agree with it whenever that date places the quarter in one
    fiscal year; when the two disagree neither is chosen."""
    if period_type == PERIOD_TYPE_QUARTER:
        trailing = _TRAILING_END_RE.match(text, end)
        if trailing is not None:
            day_text = trailing.group("day_before") or trailing.group("day_after")
            implied = _fiscal_year_of_quarter(
                ordinal, trailing.group("month"), int(trailing.group("year")),
                int(day_text) if day_text else None,
            )
            if implied is not None and implied != year:
                return None
    period = _period_of("q" if period_type == PERIOD_TYPE_QUARTER else "h", ordinal, year)
    if period is None:
        return None
    return DocumentPeriod(period, BASIS_FISCAL_PERIOD_END, window.strip()[:120]), index, end


def _began_by(found: DocumentPeriod, as_of: date | None) -> bool:
    from app.services.sources.disclosure_period_policy import period_began_by

    return period_began_by(found.period.key or "", as_of, basis=found.basis)


def detect_document_period(
    *,
    title: str | None = None,
    url: str | None = None,
    headings: "list[str] | tuple[str, ...] | None" = None,
    text: str | None = None,
    as_of: date | None = None,
) -> DocumentPeriod:
    """Resolve the period a document covers from its OWN words. Never raises.

    Sources are read in the order the issuer's own intent is clearest:
    ``title``, then ``url`` (the issuer published that slug), then the leading
    ``headings``, then a bounded slice of ``text``. Within each source the rules
    run strongest-first: a combined fiscal label, then an unambiguous
    four-digit period label, then a period-end sentence.

    ADR-080 — a candidate is SKIPPED, and the scan moves on, when:

    * its mention is not framed as the period the document reports
      (``date_semantics.classify_date_mention``): "cash runway into H2 2028", "FY27 Q1
      guidance", "notes maturing Q1 2031", "readout expected H2 2027". In body text an
      un-framed mention is skipped too — fail closed;
    * ``as_of`` (the document's publication or filing date) is known and the period had
      not begun by it (``disclosure_period_policy.period_began_by``, fiscal-aware).

    Returns ``UNKNOWN_DOCUMENT_PERIOD`` when the document states no period —
    which is the common case for an annual report, and which leaves every
    existing behaviour exactly as it was.
    """
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    try:
        sources = [
            _source(_clauses([title or ""], ROLE_TITLE)),
            _source([((url or "")[:_MAX_TITLE_CHARS], ROLE_TITLE)]),
            _source(_clauses(list((headings or [])[:_MAX_HEADINGS]), ROLE_HEADING)),
            _source(_body_parts(text or "")),
        ]
        for rule in (_fiscal_label_candidates, _period_label_candidates,
                     _period_end_candidates):
            for source in sources:
                if not source.text:
                    continue
                for seen, (found, start, end) in enumerate(rule(source.text)):
                    if seen >= _MAX_CANDIDATES_PER_SOURCE:
                        break
                    if rule is not _period_end_candidates and any(
                        start < b < end for b in source.bounds
                    ):
                        # A label is one token pair: "…of 2031. Q3 2026" is not "2031 Q3".
                        continue
                    framed = _framing_of(source, start, end)
                    if framed is None:
                        continue
                    if as_of is not None and not _began_by(found, as_of):
                        continue
                    if _unfit_for_its_date(found, framed[0], framed[1],
                                           source.role_at(start), as_of):
                        continue
                    return found
    except (AttributeError, TypeError, ValueError):  # pragma: no cover - defensive
        return UNKNOWN_DOCUMENT_PERIOD
    return UNKNOWN_DOCUMENT_PERIOD


#: ADR-086 — a finding statement is read as one short line: at most this long it is
#: framed like a headline (an un-cued period names what the line reports); a longer
#: statement is running text, where an un-cued period is not read at all.
_STATEMENT_HEADLINE_MAX_CHARS = _HEADLINE_MAX_CHARS
#: Candidates read per statement (a statement is short; this bounds a degenerate one).
_MAX_STATEMENT_CANDIDATES = 24


def statement_reporting_periods(text: str | None, *, as_of: date | None) -> list[str]:
    """ADR-086 — the DISTINCT reporting periods one finding statement states, each a
    ``ReportingPeriod.key`` ("2026-Q2", "2026-H1"), read with the ADR-080 rules.

    Every label, fiscal label and period-end phrase in the statement is a candidate. A
    candidate counts only when ``date_semantics.read_date_mention`` frames it as a
    REPORTING period (not a forecast, a target, a deadline or a comparison) and, read
    against ``as_of`` (the statement's source publication date), the period had begun
    and — weakly framed or un-cued — had ENDED and recently (``_unfit_for_its_date``).
    Without ``as_of`` nothing is returned: an undated statement cannot be told apart
    from a forecast. Never raises."""
    if as_of is None or not text:
        return []
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    raw = str(text)[:_MAX_SCAN_CHARS]
    role = ROLE_HEADLINE if len(raw.strip()) <= _STATEMENT_HEADLINE_MAX_CHARS else ROLE_BODY
    try:
        source = _source([(raw, role)])
        if not source.text:
            return []
        spans: list[tuple[int, int]] = []
        keys: list[str] = []
        for rule in (_fiscal_label_candidates, _period_label_candidates,
                     _period_end_candidates):
            for seen, (found, start, end) in enumerate(rule(source.text)):
                if seen >= _MAX_STATEMENT_CANDIDATES:
                    break
                if any(start < e and s < end for s, e in spans):
                    continue  # one mention, read by two rules
                spans.append((start, end))
                framed = _framing_of(source, start, end)
                if framed is None or not _began_by(found, as_of):
                    continue
                if _unfit_for_its_date(found, framed[0], framed[1],
                                       source.role_at(start), as_of):
                    continue
                key = found.period.key
                if key and key not in keys:
                    keys.append(key)
    except (AttributeError, TypeError, ValueError):  # pragma: no cover - defensive
        return []
    return keys


# ── ADR-080 — the regulator's structured period of report ─────────────────── #

_ANNUAL_SEC_FORMS: frozenset[str] = frozenset({"10-K", "20-F", "40-F"})
_QUARTERLY_SEC_FORMS: frozenset[str] = frozenset({"10-Q"})
_INTERIM_SEC_FORMS: frozenset[str] = frozenset({"6-K"})
#: A period of report more than this long before the filing is not trusted to be one.
_MAX_REPORT_LAG_DAYS = 400
#: A period end falls in a month's last week, or (52/53-week years) its first days.
_MONTH_END_FROM_DAY = 24
_MONTH_ABBR = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
               "nov", "dec")


def _day_of(raw: Any) -> date | None:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw or "").strip()[:10]
    try:
        return date.fromisoformat(text) if len(text) == 10 else None
    except ValueError:
        return None


def _period_end_month(day: date) -> tuple[int, int] | None:
    """``(year, month)`` a period ending on ``day`` ends in, or ``None`` for a mid-month
    day. A 52/53-week period ending in a month's first days ends in the month before."""
    if day.day <= _WEEK_YEAR_SPILL_DAYS:
        return (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
    if day.day >= _MONTH_END_FROM_DAY:
        return day.year, day.month
    return None


def fiscal_year_end_month_of(report_date: Any) -> int | None:
    """The month an issuer's fiscal year ends in, read from an ANNUAL filing's period of
    report — or ``None``. Never a default calendar."""
    day = _day_of(report_date)
    found = _period_end_month(day) if day is not None else None
    return found[1] if found is not None else None


def sec_period_of_report(
    *,
    form: str | None,
    report_date: Any,
    filing_date: Any,
    fiscal_year_end_month: int | None = None,
    results_title: str | None = None,
) -> DocumentPeriod:
    """The period an SEC filing reports, from EDGAR's ``reportDate``. Never raises.

    Read consistently with the form, and ``UNKNOWN`` whenever it cannot be:

    * **10-K / 20-F / 40-F** → the fiscal year, named by the year it ends in (SEC's
      ``fy`` convention). A year ending in January or February is named both ways
      (Walmart / Home Depot), so it states none.
    * **10-Q** → the fiscal quarter, which needs the fiscal year end
      (``fiscal_year_end_month``, from the annual filing's own period of report); the
      fiscal year follows ``_fiscal_year_of_quarter``, the rule the prose reader uses.
    * **6-K** → a half or a quarter only for an interim-RESULTS document whose own
      headline (``results_title``) names the period that ends on the period of report.
      A 6-K's period of report is otherwise just its filing date — nothing is invented.

    Requires the filing date: EDGAR's feed reader fills a missing ``reportDate`` with the
    filing date, and a period of report on or after the filing is not one.
    """
    try:
        base = (form or "").strip().upper()
        if base.endswith("/A"):
            base = base[:-2]
        report = _day_of(report_date)
        filed = _day_of(filing_date)
        if report is None or filed is None:
            return UNKNOWN_DOCUMENT_PERIOD
        lag = (filed - report).days
        if lag <= 0 or lag > _MAX_REPORT_LAG_DAYS:
            return UNKNOWN_DOCUMENT_PERIOD
        ends = _period_end_month(report)
        if ends is None:
            return UNKNOWN_DOCUMENT_PERIOD
        end_year, end_month = ends
        evidence = f"{base} period of report {report.isoformat()}"
        if base in _ANNUAL_SEC_FORMS:
            if end_month in (1, 2):
                return UNKNOWN_DOCUMENT_PERIOD
            period = ReportingPeriod(PERIOD_TYPE_ANNUAL, end_year, None, str(end_year))
            return DocumentPeriod(period, BASIS_SEC_PERIOD_OF_REPORT, evidence)
        if base in _QUARTERLY_SEC_FORMS:
            return _quarter_of_report(report, end_month, fiscal_year_end_month, evidence)
        if base in _INTERIM_SEC_FORMS:
            return _interim_of_report(end_year, end_month, results_title, filed, evidence)
    except (AttributeError, TypeError, ValueError):  # pragma: no cover - defensive
        return UNKNOWN_DOCUMENT_PERIOD
    return UNKNOWN_DOCUMENT_PERIOD


def _quarter_of_report(
    report: date, end_month: int, fiscal_year_end_month: int | None, evidence: str
) -> DocumentPeriod:
    if fiscal_year_end_month is None or not 1 <= int(fiscal_year_end_month) <= 12:
        return UNKNOWN_DOCUMENT_PERIOD
    offset = (end_month - int(fiscal_year_end_month)) % 12
    if offset % 3 != 0 or not 1 <= offset // 3 <= 3:
        # Not a quarter end of this fiscal year (a changed year end), or its fourth
        # quarter — which no 10-Q reports.
        return UNKNOWN_DOCUMENT_PERIOD
    ordinal = offset // 3
    fiscal_year = _fiscal_year_of_quarter(
        ordinal, _MONTH_ABBR[report.month - 1], report.year, report.day
    )
    if fiscal_year is None:
        return UNKNOWN_DOCUMENT_PERIOD
    period = _period_of("q", ordinal, fiscal_year)
    if period is None:
        return UNKNOWN_DOCUMENT_PERIOD
    # Compared with the year the quarter ENDS in after a 52/53-week spill: a quarter
    # reported as ending 2 January 2027 ends in December 2026.
    end_year = _period_end_month(report)
    basis = (BASIS_SEC_FISCAL_PERIOD_OF_REPORT
             if end_year is None or fiscal_year != end_year[0]
             else BASIS_SEC_PERIOD_OF_REPORT)
    return DocumentPeriod(period, basis, evidence)


def _interim_of_report(
    end_year: int, end_month: int, results_title: str | None, filed: date, evidence: str
) -> DocumentPeriod:
    if not results_title:
        return UNKNOWN_DOCUMENT_PERIOD
    stated = detect_document_period(title=results_title, as_of=filed)
    if not stated.is_interim or stated.basis not in (BASIS_PERIOD_LABEL,
                                                      BASIS_PERIOD_END_PHRASE):
        return UNKNOWN_DOCUMENT_PERIOD
    period = stated.period
    span = 6 if period.period_type == PERIOD_TYPE_HALF else 3
    if period.year != end_year or (period.ordinal or 0) * span != end_month:
        # The headline names another period than the one that ends on the period of
        # report: two sources disagree, and neither is chosen.
        return UNKNOWN_DOCUMENT_PERIOD
    return DocumentPeriod(period, BASIS_SEC_PERIOD_OF_REPORT, evidence)


def document_period_of(
    *,
    title: str | None,
    url: str | None,
    extraction: object | None = None,
    as_of: date | None = None,
) -> DocumentPeriod:
    """``detect_document_period`` for an already-extracted document.

    Reads the extraction's LEADING headings and excerpt text (lowest page
    numbers first) so a document whose title and URL say nothing — an official
    PDF filed under a numeric identifier, as regulated storage venues publish
    them — can still state its own period on its own front page.

    Duck-typed on purpose: this module stays free of extractor imports so it
    remains a pure value layer. Never raises.

    ``as_of`` (ADR-080) is the document's publication or filing date when the caller
    knows it: a period that had not begun by then is a forecast and is skipped.
    """
    headings: list[str] = []
    texts: list[str] = []
    try:
        excerpts = list(getattr(extraction, "excerpts", None) or [])
        excerpts.sort(key=lambda e: (getattr(e, "page_number", None) or 0))
        for excerpt in excerpts[:_MAX_HEADINGS]:
            for attr in ("heading", "ancestor_heading", "section"):
                value = getattr(excerpt, attr, None)
                if isinstance(value, str) and value.strip():
                    headings.append(value.strip())
            text = getattr(excerpt, "text", None)
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
    except (AttributeError, TypeError):  # pragma: no cover - defensive
        headings, texts = [], []
    return detect_document_period(
        title=title, url=url, headings=headings, text="\n".join(texts), as_of=as_of
    )


__all__ = [
    "title_states_annual",
    "title_states_part_year",
    "BASIS_FISCAL_LABEL",
    "BASIS_FISCAL_PERIOD_END",
    "BASIS_PERIOD_END_PHRASE",
    "BASIS_PERIOD_LABEL",
    "BASIS_SEC_FISCAL_PERIOD_OF_REPORT",
    "BASIS_SEC_PERIOD_OF_REPORT",
    "BASIS_UNKNOWN",
    "UNKNOWN_DOCUMENT_PERIOD",
    "DocumentPeriod",
    "detect_document_period",
    "document_period_of",
    "fiscal_year_end_month_of",
    "sec_period_of_report",
    "statement_reporting_periods",
]
