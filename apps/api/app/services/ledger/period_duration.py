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

* **its own words** state a year-to-date duration ("nine months", "nine-month", "9M",
  "year to date", "six months") of exactly the quarter's cumulative length and state no
  quarter or three-month duration; or
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

Never guessed: anything else keeps the inherited key. A balance at a date (an instant)
is never rekeyed — it has no duration.
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


def statement_months(statement: str) -> int | None:
    """The one year-to-date length a statement states, when it states no quarter.
    ``None``: none, two, or a quarter beside it."""
    text = str(statement or "")[:cp.MAX_TEXT_CHARS]
    lengths = _ytd_months(text)
    if not lengths:
        return None
    if _QUARTER_WORDS_RE.search(text):
        return None
    known = {n for n in lengths if n is not None}
    if len(known) > 1:
        return None
    return next(iter(known)) if known else 0  # 0: "year to date", length from the key


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
    period_key: str | None, statement: str, cited_texts: Sequence[str],
) -> DurationRekey | None:
    """ADR-093 — the year-to-date key a quarter-keyed finding takes, or ``None``."""
    m = _QUARTER_KEY_RE.match(str(period_key or "").strip())
    if m is None:
        return None
    year, quarter, fiscal = int(m.group("y")), int(m.group("q")), bool(m.group("fy"))
    months = 3 * quarter
    key = ytd_key(year, months, fiscal=fiscal)
    if key is None:
        return None
    text = str(statement or "")
    own = statement_months(text)
    if own is not None:
        if own not in (0, months):
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
        found_any = True
    if not found_any:
        return None
    label = f"{'9M' if months == 9 else 'H1'} {'FY' if fiscal else ''}{year}"
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
