"""A figure's DURATION: three months, year to date, or a balance at a date — ADR-093.

QA campaign 2026-11 (JPM3 F4): "Q3 FY2026 segment revenue: Payments $16.0bn, Lending
$6.4bn, Securities Services $4.8bn, Markets $34.0bn" — every figure is the NINE-MONTH
year-to-date figure of the cited 10-Q (Q3 alone: $5.6bn / $2.2bn / $1.6bn / $10.3bn).
The finding inherited "2026-Q3" from its chunk: a filing filed for the third quarter does
not make every figure in it a third-quarter figure. A 10-Q states most lines twice, for
the three months and for the nine months ended on the same day.

THE RULE (fail closed)
=====================
A finding whose inherited key is a quarter (``2026-Q3``) is rekeyed to the year-to-date
period of the same fiscal year (``2026-9M``; six months → ``2026-H1``) only when:

* **its own words** state a year-to-date REPORTING duration in an explicit form ("nine
  months ended / to …", "first nine months of", "nine-month 2026", "9M 2026", "six
  months ended", "first half", "year-to-date revenue", "YTD sales") of exactly the
  quarter's cumulative length, and state no quarter or three-month duration. A bare
  "nine months" / "year to date" / "six months" is not a reporting period ("cash to
  fund nine months", "up 30% year to date", "six months of supply"); or
* **its sources** state every one of its amounts ONLY in a year-to-date context of that
  length. The context of one occurrence is read from, in order:
  - the table column: a row with an even number of numeric cells under a header naming
    two durations ("Three months ended … Nine months ended …"); the first half of the
    cells is the first duration;
  - the sentence stating the figure, when it names exactly one duration;
  - the nearest section marker line before it ("Quarterly results", "Year-to-date
    results", "Three months ended", "Nine months ended").

  An amount stated in a three-month context anywhere in the cited text, or in no context
  at all, leaves the key as it is.

Then a quarter label the statement itself writes for that quarter ("Q3 FY2026") is
rewritten to the year-to-date label ("9M FY2026 (year to date)").

Never guessed: anything else keeps the inherited key.

* A balance at a date (an instant) is never rekeyed — it has no duration. A statement
  naming a balance (cash, debt, inventory, backlog, deposits, assets …) or a date it is
  stated "at" / "as of" keeps its key (``_INSTANT_RE``).
* Only a calendar key is rekeyed (``2026-Q3``; never ``FY2026-Q3``), and a year to date
  of unstated length ("year-to-date revenue") only when the issuer's fiscal year is
  known to be the calendar year.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.services.ledger import claim_checks as cc
from app.services.ledger import claim_period as cp

#: Cited text read per item, and items read.
MAX_TEXT_CHARS = cc.MAX_FIGURE_TEXT_CHARS
MAX_ITEMS = 8
#: Characters looked back for a table header or a section marker.
LOOKBACK_CHARS = 3000

BASIS_STATEMENT = "statement_duration"
BASIS_SOURCE = "source_duration"

_QUARTER_KEY_RE = re.compile(r"^(?P<fy>FY)?(?P<y>\d{4})-Q(?P<q>[1-4])$")
#: A cumulative duration a text names, with its length when stated.
_YTD_WORDS_RE = re.compile(
    r"\b(?:(?P<n>six|nine|6|9)[- \t]{1,2}months?|(?P<m>6|9)M|year[- \t]to[- \t]date|ytd|"
    r"first[ \t]{1,3}(?P<f>six|nine)[ \t]{1,3}months)\b",
    re.IGNORECASE,
)
#: ADR-093 review: the explicit forms of a year-to-date REPORTING period a statement may
#: state. ``n``/``f``/``h``: the length (six / nine; ``h``: a half); ``t``: length unstated.
_YTD_METRIC = (
    r"(?:revenues?|sales|net[ \t]{1,3}sales|results|net[ \t]{1,3}income|net[ \t]{1,3}interest"
    r"[ \t]{1,3}income|earnings|income|profit|ebitda|operating[ \t]{1,3}(?:income|profit|"
    r"cash[ \t]{1,3}flows?)|free[ \t]{1,3}cash[ \t]{1,3}flows?|cash[ \t]{1,3}flows?|orders|"
    r"bookings|expenses|costs|capex|capital[ \t]{1,3}expenditures?)"
)
_MONTH_AFTER = (
    r"(?:\d{1,2}[ \t]{1,3})?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]{0,6}\.?\b"
)
_STATEMENT_YTD_RE = re.compile(
    # Re-review R1: "to" / "through" only before a month or date ("nine months to 30
    # September"); never "of" ("six months of production").
    r"\b(?:(?P<n>six|nine|6|9)[- \t]{1,2}months?[ \t]{1,3}(?:ended|ending|"
    r"(?:to|through)[ \t]{1,3}" + _MONTH_AFTER + r")"
    r"|(?P<n2>six|nine|6|9)[- \t]{1,2}month[ \t]{1,3}(?:period|(?:19|20)\d{2})\b"
    r"|first[ \t]{1,3}(?P<f>six|nine)[ \t]{1,3}months\b"
    r"|(?P<m>6|9)M[ \t]{0,1}(?:FY[ \t]{0,1})?(?:19|20)?\d{2}\b"
    r"|(?P<h>first[ \t]{1,3}half|H1[ \t]{0,1}(?:FY[ \t]{0,1})?(?:19|20)?\d{2}\b|half[- \t]year"
    r"[ \t]{1,3}(?:ended|results|to))"
    r"|(?P<t>year[- \t]to[- \t]date|ytd)[ \t]{1,3}" + _YTD_METRIC + r"\b)",
    re.IGNORECASE,
)
#: Re-review R1: a forward duration ("over the next nine months", "the coming six
#: months") is not a reporting period. Read on the 30 characters before the form.
_FORWARD_BEFORE_RE = re.compile(
    r"\b(?:next|coming|following|upcoming|remaining|another|over[ \t]{1,3}the)"
    r"(?:[ \t]{1,3}(?:next|coming|following))?[ \t]{1,3}$",
    re.IGNORECASE,
)
#: A year written in a form or its date: "2026", and the two-digit "9M 25".
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_SHORT_YEAR_RE = re.compile(r"(?:[69]M|H1)[ \t]{0,1}(?:FY[ \t]{0,1})?(?P<y>\d{2})\b", re.IGNORECASE)
#: Characters after a form read for its date's year ("ended September 30, 2025").
FORM_DATE_CHARS = 40
#: ADR-093 review: a balance (an instant) — never rekeyed to a duration.
_INSTANT_RE = re.compile(
    r"\b(?:as[ \t]{1,3}(?:of|at)\b|at[ \t]{1,3}(?:the[ \t]{1,3}end[ \t]{1,3}of\b|"
    r"(?:quarter|period|year|half)[- \t]end\b|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|"
    r"nov|dec)[a-z]{0,6}\.?[ \t]{1,3}\d|\d{1,2}[ \t]{1,3}(?:jan|feb|mar|apr|may|jun|jul|aug|"
    r"sep|oct|nov|dec)|(?:19|20)\d{2}-\d{2})"
    r"|cash(?:[ \t]{1,3}and[ \t]{1,3}(?:cash[ \t]{1,3}equivalents|investments|"
    r"marketable[ \t]{1,3}securities))?(?:[ \t]{1,3}balances?)?[ \t]{1,3}(?:of|was|were|"
    r"stood|totall?ed|held|is|are|on[ \t]{1,3}hand)\b|held[ \t]{1,3}cash"
    r"|(?:total|net|long-term|short-term)[ \t]{1,3}(?:debt|borrowings)|inventor(?:y|ies)"
    r"|backlog|total[ \t]{1,3}(?:assets|liabilities|equity|deposits|loans)|deposits[ \t]{1,3}"
    r"(?:of|were|was)|book[ \t]{1,3}value|shares[ \t]{1,3}outstanding|nonperforming|"
    r"nonaccrual|headcount|employees|liquidity[ \t]{1,3}(?:of|was)|runway)",
    re.IGNORECASE,
)
#: A quarter (three-month) duration a text names.
_QUARTER_WORDS_RE = re.compile(
    r"\b(?:three[- \t]{1,2}months?|3[- \t]{1,2}months?|quarterly|quarter|q[1-4])\b",
    re.IGNORECASE,
)
#: A section marker LINE opening a duration's results ("Quarterly results",
#: "Year-to-date results", "Three months ended September 30,").
_MARKER_LINE_RE = re.compile(
    r"^[ \t•\-–|]{0,6}(?:(?P<q>quarterly[ \t]{1,3}results|three[ \t]{1,3}months[ \t]{1,3}ended|"
    r"for[ \t]{1,3}the[ \t]{1,3}three[ \t]{1,3}months)|(?P<y>year[- \t]to[- \t]date[ \t]{1,3}"
    r"results|(?P<n>six|nine)[ \t]{1,3}months[ \t]{1,3}ended|for[ \t]{1,3}the[ \t]{1,3}"
    r"(?P<n2>six|nine)[ \t]{1,3}months))",
    re.IGNORECASE | re.MULTILINE,
)
#: A two-duration table header: both durations named close together.
_HEADER_DURATION_RE = re.compile(
    r"\b(?P<d>three|six|nine|twelve)[ \t]{1,3}months[ \t]{1,3}ended\b", re.IGNORECASE
)
#: One numeric table cell: "5,620", "(4)", "14", "$ 22,734", "20 %".
_CELL_RE = re.compile(r"(?<![\w.,])\(?-?\d{1,3}(?:,\d{3})*(?:\.\d+)?\)?(?![\w.,])")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])[ \t]{1,3}(?=[A-Z•])|\n")
_LENGTHS = {"six": 6, "6": 6, "nine": 9, "9": 9, "three": 3, "twelve": 12}


@dataclass(frozen=True)
class DurationRekey:
    """A finding's year-to-date key, its basis, and its statement with the quarter label
    rewritten (the same statement when it wrote none)."""

    period_key: str
    basis: str
    statement: str


def ytd_key(year: int, months: int, *, fiscal: bool = False) -> str | None:
    """``2026-9M`` (nine months), ``2026-H1`` (six months): the year-to-date key of a
    fiscal year. ``None`` for any other length."""
    prefix = "FY" if fiscal else ""
    if months == 9:
        return f"{prefix}{year}-9M"
    if months == 6:
        return f"{prefix}{year}-H1"
    return None


def _ytd_months(text: str) -> set[int | None]:
    """Year-to-date lengths a text names (``None``: "year to date" with no length)."""
    out: set[int | None] = set()
    for m in _YTD_WORDS_RE.finditer(text[:cp.MAX_TEXT_CHARS]):
        raw = (m.group("n") or m.group("m") or m.group("f") or "").lower()
        out.add(_LENGTHS.get(raw))
    return out


def _form_year(text: str, m: re.Match[str]) -> int | None:
    """The year a form names, in itself or in the date right after it, else ``None``."""
    short = _SHORT_YEAR_RE.match(text, m.start())
    if short is not None and short.end() <= m.end():
        return 2000 + int(short.group("y"))
    end = m.end() + FORM_DATE_CHARS
    for stop in (".", ";", "\n"):
        at = text.find(stop, m.end(), end)
        if at >= 0:
            end = at
    year = _YEAR_RE.search(text, m.start(), end)
    return int(year.group(0)) if year else None


def statement_months(statement: str, year: int | None = None) -> int | None:
    """The one year-to-date length a statement states in an explicit reporting form,
    when it states no quarter. ``None``: none, two, or a quarter beside it; ``0``:
    "year-to-date revenue" (length from the key).

    Re-review R1/R2: a form after "next / coming / over the" is a forward duration, not
    a period. With ``year`` (the key's): the FIRST form's own year (in it, or in the
    date right after it) must be that year — a later form is the comparison ("…
    versus nine-month 2025"); a form with no year needs the statement's only year, if
    it states one, to be that year."""
    text = str(statement or "")[:cp.MAX_TEXT_CHARS]
    lengths: set[int] = set()
    forms = [m for m in _STATEMENT_YTD_RE.finditer(text)
             if not _FORWARD_BEFORE_RE.search(text[max(0, m.start() - 30):m.start()])]
    if forms and year is not None:
        stated = _form_year(text, forms[0])
        if stated is None:
            years = {int(y) for y in _YEAR_RE.findall(text)}
            stated = next(iter(years)) if len(years) == 1 else None
        if stated is not None and stated != year:
            return None
    for m in forms:
        if m.group("h"):
            lengths.add(6)
        elif m.group("t"):
            lengths.add(0)
        else:
            raw = (m.group("n") or m.group("n2") or m.group("f") or m.group("m") or "").lower()
            lengths.add(_LENGTHS.get(raw, -1))
    if not lengths or -1 in lengths:
        return None
    if _QUARTER_WORDS_RE.search(text):
        return None
    known = {n for n in lengths if n}
    if len(known) > 1:
        return None
    return next(iter(known)) if known else 0


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return start, (len(text) if end < 0 else end)


def _table_months(text: str, pos: int, fig: cc._Figure) -> int | None:
    """The duration of a table cell: its row's numeric cells split in two under a
    two-duration header."""
    start, end = _line_bounds(text, pos)
    row = text[start:end]
    cells = [m for m in _CELL_RE.finditer(row)]
    if len(cells) < 2 or len(cells) % 2:
        return None
    index = next((i for i, m in enumerate(cells) if start + m.start() <= pos < start + m.end()
                  or start + m.start() == pos), None)
    if index is None:
        return None
    window = text[max(0, start - LOOKBACK_CHARS):start]
    headers = list(_HEADER_DURATION_RE.finditer(window))
    if len(headers) < 2:
        return None
    first, second = headers[-2], headers[-1]
    if second.start() - first.end() > 300:
        return None
    a = _LENGTHS[first.group("d").lower()]
    b = _LENGTHS[second.group("d").lower()]
    if a == b:
        return None
    return a if index < len(cells) // 2 else b


def _sentence_months(text: str, pos: int) -> int | None:
    start = 0
    for m in _SENTENCE_END_RE.finditer(text, 0, pos):
        start = m.end()
    end_match = _SENTENCE_END_RE.search(text, pos)
    sentence = text[start:end_match.start() if end_match else len(text)]
    ytd = {n for n in _ytd_months(sentence) if n is not None}
    quarter = bool(_QUARTER_WORDS_RE.search(sentence))
    if ytd and not quarter and len(ytd) == 1:
        return next(iter(ytd))
    if quarter and not _ytd_months(sentence):
        return 3
    return None


def _marker_months(text: str, pos: int) -> int | None:
    window_start = max(0, pos - LOOKBACK_CHARS)
    last: re.Match[str] | None = None
    for m in _MARKER_LINE_RE.finditer(text, window_start, pos):
        last = m
    if last is None:
        return None
    if last.group("q"):
        return 3
    raw = (last.group("n") or last.group("n2") or "").lower()
    return _LENGTHS.get(raw, 0)  # 0: "Year-to-date results", length from the key


def occurrence_months(text: str, fig: cc._Figure) -> list[int | None]:
    """The duration of every occurrence of ``fig`` in ``text`` (``None``: unknown;
    ``0``: year to date, length unstated)."""
    out: list[int | None] = []
    source = str(text or "")[:MAX_TEXT_CHARS]
    seen: set[int] = set()
    for found in [*cc.figures(source), *_table_cells(source)]:
        if not cc.same_figure(fig, found):
            continue
        digit = next((i for i, c in enumerate(found.text) if c.isdigit()), 0)
        pos = found.start + digit
        if pos in seen:
            continue
        seen.add(pos)
        months = _table_months(source, pos, found)
        if months is None:
            months = _sentence_months(source, pos)
        if months is None:
            months = _marker_months(source, pos)
        out.append(months)
    return out


#: A table cell is read as an amount only from this value up: a statement's "$6.4bn"
#: must not match a cell "6" (a percent change) at a billion scale.
MIN_CELL_VALUE = 1000
#: Rows read for cells, per text.
MAX_TABLE_ROWS = 200


def _table_cells(text: str) -> list[cc._Figure]:
    """Bare numeric cells of table rows (``figures`` never reads a bare integer: a year,
    a count). Only rows with at least four numeric cells, and values of at least
    ``MIN_CELL_VALUE`` (a table in millions)."""
    out: list[cc._Figure] = []
    rows = 0
    start = 0
    while start < len(text) and rows < MAX_TABLE_ROWS:
        end = text.find("\n", start)
        end = len(text) if end < 0 else end
        cells = list(_CELL_RE.finditer(text, start, end))
        if len(cells) >= 4:
            rows += 1
            for m in cells:
                raw = m.group(0).strip("()").lstrip("-")
                try:
                    value = float(raw.replace(",", ""))
                except ValueError:
                    continue
                if value < MIN_CELL_VALUE:
                    continue
                decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
                out.append(cc._Figure("amt", value, decimals, None, False, m.group(0),
                                      m.start()))
        start = end + 1
    return out


def _amounts(statement: str) -> list[cc._Figure]:
    return [f for f in cc.figures(statement, 12)
            if f.kind == "amt" and (f.currency or f.scale is not None or f.decimals)]


def _relabel(statement: str, year: int, quarter: int, label: str) -> str:
    """The statement with every label of quarter ``quarter`` rewritten to ``label``."""
    out = statement
    for p in sorted(cp.mentions(statement, classify=False), key=lambda p: -p.start):
        if p.label is None or p.label[0] != "q" or p.label[2] != quarter:
            continue
        year_label = label.replace(str(year), str(p.label[1]))
        out = out[:p.start] + year_label + out[p.end:]
    return out


def rekey_for_duration(
    period_key: str | None, statement: str, cited_texts: Sequence[str], *,
    calendar_fiscal_year: bool | None = None,
) -> DurationRekey | None:
    """ADR-093 — the year-to-date key a quarter-keyed finding takes, or ``None``.

    ``calendar_fiscal_year``: whether the issuer's fiscal year is the calendar year
    (``None``: unknown). A year to date of unstated length is read only when it is."""
    m = _QUARTER_KEY_RE.match(str(period_key or "").strip())
    if m is None or m.group("fy"):
        return None  # review P2: a fiscal-prefixed quarter is never rekeyed
    year, quarter = int(m.group("y")), int(m.group("q"))
    months = 3 * quarter
    key = ytd_key(year, months)
    if key is None:
        return None
    text = str(statement or "")
    if _INSTANT_RE.search(text[:cp.MAX_TEXT_CHARS]):
        return None  # a balance at a date has no duration
    unstated_ok = calendar_fiscal_year is True
    own = statement_months(text, year)
    if own is not None:
        if own not in (0, months) or (own == 0 and not unstated_ok):
            return None
        return DurationRekey(key, BASIS_STATEMENT, text)
    if _ytd_months(text):
        return None  # a year-to-date word beside a quarter: two durations, not one
    amounts = _amounts(text)
    if not amounts:
        return None
    found_any = False
    for fig in amounts:
        durations: list[int | None] = []
        for cited in list(cited_texts)[:MAX_ITEMS]:
            durations += occurrence_months(cited, fig)
        if not durations:
            continue  # a figure no cited text states: it votes for nothing
        if any(d is None or d not in (0, months) for d in durations):
            return None
        if 0 in durations and not unstated_ok:
            return None  # review P3: "Year-to-date results" off a calendar year
        found_any = True
    if not found_any:
        return None
    label = f"{'9M' if months == 9 else 'H1'} {year}"
    label = label + (" (year to date)" if months == 9 else "")
    return DurationRekey(key, BASIS_SOURCE, _relabel(text, year, quarter, label))


__all__ = [
    "BASIS_SOURCE",
    "BASIS_STATEMENT",
    "DurationRekey",
    "occurrence_months",
    "rekey_for_duration",
    "statement_months",
    "ytd_key",
]
