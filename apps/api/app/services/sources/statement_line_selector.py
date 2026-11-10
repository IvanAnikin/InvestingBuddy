"""ADR-092 — the statement-line selector: which printed line IS a statement concept.

One deterministic module decides, wherever a statement fact or slot is chosen:

1. **A component is not its total.** "Trade receivables and other current assets" is a
   line INSIDE current assets, not current assets; "Restricted cash and cash
   equivalents" is not the cash line; "Other operating income" is not operating profit.
   A total concept (current assets, current liabilities, total assets, total
   liabilities, cash and cash equivalents) is read only from a row whose WHOLE label is
   one of the concept's aliases (:data:`TOTAL_LINE_ALIASES`), after removing a note
   reference, a trailing colon or parenthetical, and — for cash — a closing-balance
   date. Operating profit keeps its open wording but refuses the component markers
   (:data:`_OPERATING_PROFIT_COMPONENT_RE`). :func:`line_qualifies` is the test.
2. **A section's unlabelled subtotal is the section's total — when it adds up.** A
   European balance sheet prints a section heading ("Current assets"), its lines, and
   then an UNLABELLED subtotal. The subtotal is read as the heading's line only when the
   section's own lines sum to it in every column it prints
   (:func:`verified_section_total`); otherwise nothing is read, never a component.
3. **A primary statement table outranks prose.** A figure read from a table that has
   the shape of a primary statement (:func:`is_statement_shaped`) is not contradicted
   by a prose sentence of UNKNOWN scope that states a different number for the same
   line and period: the prose read is set aside, with a note, instead of manufacturing
   a conflict that withholds the statement's own figure (:func:`line_rank`).

Pure: no I/O, no project imports (the table reconstructor imports this module, and
``primary_fact_parser`` imports the reconstructor's caller). Field names are plain
strings equal to ``primary_fact_parser``'s constants (pinned by a test).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

FIELD_CURRENT_ASSETS = "total_current_assets"
FIELD_CURRENT_LIABILITIES = "total_current_liabilities"
FIELD_NON_CURRENT_ASSETS = "total_non_current_assets"
FIELD_TOTAL_ASSETS = "total_assets"
FIELD_TOTAL_LIABILITIES = "total_liabilities"
FIELD_CASH = "cash_and_equivalents"
FIELD_OPERATING_PROFIT = "operating_profit"

#: The whole-label aliases of each TOTAL concept (normalised: lower case, single
#: spaces, no note reference). Anything else that merely CONTAINS the words is a
#: component or a different line and is not this concept.
TOTAL_LINE_ALIASES: dict[str, frozenset[str]] = {
    FIELD_CURRENT_ASSETS: frozenset({"current assets", "total current assets"}),
    FIELD_CURRENT_LIABILITIES: frozenset(
        {"current liabilities", "total current liabilities"}),
    # Only the subtotal check reads it, but "Other non-current assets" read as the
    # non-current total made total assets fail its own subtotal check.
    FIELD_NON_CURRENT_ASSETS: frozenset({
        "non-current assets", "non current assets", "total non-current assets",
        "total non current assets",
    }),
    FIELD_TOTAL_ASSETS: frozenset({"total assets"}),
    FIELD_TOTAL_LIABILITIES: frozenset({"total liabilities"}),
    FIELD_CASH: frozenset({
        "cash and cash equivalents",
        "total cash and cash equivalents",
        "cash at bank and in hand",
        "cash at bank and on deposit",
    }),
}

#: One note number: "12", "5.2", "14(a)", "3(b)", "5a".
_NOTE_NUMBER = r"\d{1,2}(?:\.\d{1,2})?(?: ?\([a-z]{1,4}\)|[a-z])?"
#: A note reference or a trailing mark a statement prints beside a line's name — and
#: nothing else. "Current assets 12", "Total current liabilities 21,22" / "21-22",
#: "Total assets (note 5)", "(Notes 5, 6)", "(note 5(a))", "(item 4.6)", "14(a)", "(a)",
#: "(iii)", a footnote mark, a colon, and the restatement words an issuer prints beside a
#: comparative ("(restated)", "(re-presented)", "(unaudited)", "(audited)").
#: A parenthetical that NAMES something ("(restricted)", "(parent company)", "(segment)",
#: "(excl. held for sale)", "(pro forma)") is NOT stripped: that line is not the total.
#: Applied to a stripped, single-spaced label; every alternative is bounded and anchored
#: at the end, so a search is linear.
_TRAILING_QUALIFIER_RE = re.compile(
    r"(?:\((?:(?:notes?|item) ?)?" + _NOTE_NUMBER
    + r"(?: ?[,&–-] ?" + _NOTE_NUMBER + r"){0,3}\)"
    r"|\((?:[a-z]|i{1,3}|iv|vi{0,3}|ix|x)\)"
    r"|\((?:restated|re-presented|represented|unaudited|audited)\)"
    r"|(?<=\s)(?:notes? )?" + _NOTE_NUMBER + r"(?: ?[,&–-] ?" + _NOTE_NUMBER + r"){0,3}"
    r"|[¹²³⁴⁵⁶⁷⁸⁹⁰]{1,3}"
    r"|, ?total"
    r"|[:*†‡])$",
    re.I,
)
#: A leading item or note number ("4.6 Cash and cash equivalents at end of period").
_LEADING_ITEM_RE = re.compile(r"^\d{1,2}(?:\.\d{1,2}){0,2}\.? (?=[a-z])", re.I)
#: "… at end of the year", ", end of period", "– end of year", "at the close of the
#: period", "at year-end", "at 31 December 2025", "at 31.12.2025", "at 30 June": the
#: closing balance of the cash line is the cash line (an OPENING balance is refused
#: separately by the validator). Applied to a stripped, single-spaced label.
_CLOSING_BALANCE_RE = re.compile(
    r"(?:,| ?[-–—])? (?:at (?:the )?)?(?:end|close) of (?:the )?"
    r"(?:financial |reporting )?(?:year|period|half[- ]year|quarter)$"
    r"| at (?:the )?year[- ]end$"
    r"| at \d{1,2} [a-z]{3,9}(?: \d{4})?$"
    r"| at \d{1,2}[./]\d{1,2}[./]\d{2,4}$",
    re.I,
)
#: "Closing cash and cash equivalents" names the same closing balance.
_CLOSING_PREFIX_RE = re.compile(r"^closing ", re.I)
#: Operating-profit lines that are NOT the reported operating profit: an income line, a
#: share of an investee's, a non-operating item, a cost line, a segment's, and an
#: issuer's own adjusted measure ("Adjusted operating profit", "Underlying operating
#: profit", "Operating profit before exceptional items", "non-GAAP", "pro forma",
#: "before/after share of associates").
_OPERATING_PROFIT_COMPONENT_RE = re.compile(
    r"^ ?(?:net )?other operating"
    r"|\bshare of\b"
    r"|\bnon[- ]?operating\b"
    r"|\boperating (?:income|profit) and expenses?\b"
    r"|\bsegment(?:al)?\b"
    r"|\badjusted\b|\bunderlying\b|\bnon[- ]?gaap\b|\bpro[- ]?forma\b"
    r"|\b(?:before|after|excluding|excl\.?) (?:the )?(?:exceptional|special|"
    r"non[- ]?recurring|one[- ]off|separately disclosed)\b"
    r"|\b(?:before|after) (?:the )?(?:share|results?) of\b"
    r"|\b(?:associates?|joint ventures?)\b",
    re.I,
)
_SPACE_RE = re.compile(r"\s+")


def normalise_line_label(label: str) -> str:
    """Lower case, single spaces, without a leading item number or the trailing note
    references / restatement marks (never a word that names a different line)."""
    text = _SPACE_RE.sub(" ", (label or "").strip().lower())
    text = _LEADING_ITEM_RE.sub("", text)
    for _ in range(3):  # "Total assets 12 (restated):" — at most three trailing marks
        stripped = _TRAILING_QUALIFIER_RE.sub("", text).rstrip()
        if stripped == text:
            break
        text = stripped
    return text


def line_qualifies(field: str, label: str) -> bool:
    """Whether a printed row label may be read as ``field``'s line (rule 1).

    Fields with no rule here qualify as before; the validator's own label patterns
    still decided the field.
    """
    aliases = TOTAL_LINE_ALIASES.get(field)
    if aliases is not None:
        text = normalise_line_label(label)
        if text in aliases:
            return True
        if field == FIELD_CASH:
            closing = _CLOSING_PREFIX_RE.sub("", text)
            closing = _CLOSING_BALANCE_RE.sub("", closing).rstrip()
            return closing != text and normalise_line_label(closing) in aliases
        return False
    if field == FIELD_OPERATING_PROFIT:
        text = _SPACE_RE.sub(" ", (label or "").strip())
        return _OPERATING_PROFIT_COMPONENT_RE.search(text) is None
    return True


# ── Rule 2: a section's unlabelled subtotal ──────────────────────────────── #

_MINUS_SIGNS = str.maketrans({"−": "-", "–": "-", "—": "-"})
_NIL_CELLS = frozenset({"", "-", "–", "—", "‒", "nil"})
_PLAIN_NUMBER_RE = re.compile(
    r"-?\d{1,3}(?:[ ,  ]\d{3}){1,8}(?:\.\d{1,4})?|-?\d{1,15}(?:\.\d{1,4})?"
)
#: Minimum lines a section must print before its unlabelled subtotal is read: one line
#: "summing" to itself proves nothing.
MIN_SECTION_LINES = 2
#: The balance-sheet sections whose heading may carry their unlabelled subtotal. Only
#: these: an "Operating result" heading over segment lines sums to the SEGMENTS'
#: result, not the Group's operating profit (a segment note: 5 048 against
#: an operating profit of 4 492), so a heading outside this list is never filled.
SECTION_TOTAL_FIELDS: frozenset[str] = frozenset(
    {FIELD_CURRENT_ASSETS, FIELD_CURRENT_LIABILITIES, FIELD_NON_CURRENT_ASSETS})


def is_section_total_heading(label: str) -> bool:
    """Whether a section heading names a balance-sheet section that may carry its
    unlabelled subtotal (:data:`SECTION_TOTAL_FIELDS`)."""
    text = normalise_line_label(label)
    return any(text in TOTAL_LINE_ALIASES[field] for field in SECTION_TOTAL_FIELDS)


def _cell_value(cell: str) -> tuple[float, int] | None:
    """``(value, decimals)`` of a plain statement cell, ``(0, 0)`` for a nil cell,
    ``None`` when it is not a plain number (a percent, a ratio, a word)."""
    text = (cell or "").strip()
    if text.lower() in _NIL_CELLS:
        return 0.0, 0
    text = text.translate(_MINUS_SIGNS)
    negative = False
    if len(text) > 2 and text.startswith("(") and text.endswith(")"):
        text, negative = text[1:-1].strip(), True
    if len(text) > 40 or not _PLAIN_NUMBER_RE.fullmatch(text):
        return None
    digits = text.replace(" ", "").replace(",", "").replace(" ", "").replace(" ", "")
    try:
        value = float(digits)
    except ValueError:
        return None
    decimals = len(digits.split(".", 1)[1]) if "." in digits else 0
    return (-abs(value) if negative else value), decimals


def verified_section_total(
    lines: Sequence[Sequence[str]], subtotal: Sequence[str]
) -> bool:
    """True when ``subtotal`` (one cell per value column) is the sum of ``lines``.

    Every column the subtotal prints a number in must reconcile, and at least one must.
    A nil cell ("–", blank) in a line counts as zero; any other unreadable cell refuses
    the whole section. The tolerance is half a printed unit per line (each printed line
    was rounded once), so a rounding difference reconciles and a missing line never does.
    """
    if len(lines) < MIN_SECTION_LINES or not subtotal:
        return False
    checked = 0
    for col, total_cell in enumerate(subtotal):
        printed = (total_cell or "").strip()
        if not printed:
            continue
        if printed.lower() in _NIL_CELLS:
            return False
        total = _cell_value(printed)
        if total is None:
            return False
        parts: list[float] = []
        decimals = total[1]
        for line in lines:
            read = _cell_value(line[col] if col < len(line) else "")
            if read is None:
                return False
            parts.append(read[0])
            decimals = max(decimals, read[1])
        unit = 10.0 ** (-decimals)
        if abs(sum(parts) - total[0]) > max(unit * 0.5 * len(lines), unit) + 1e-9:
            return False
        checked += 1
    return checked > 0


#: A line that is part of current assets / liabilities but is printed AFTER the first
#: unlabelled subtotal (IFRS 5): "Assets of disposal group held for sale", "Liabilities
#: directly associated with assets held for sale".
_HELD_FOR_SALE_RE = re.compile(r"\bheld[- ]for[- ]sale\b|\bdisposal groups?\b", re.I)


class _ClosedSection:
    __slots__ = ("key", "total", "payload", "after", "held_for_sale", "refused")

    def __init__(self, key: object, total: list[str], payload: object) -> None:
        self.key = key
        self.total = total
        self.payload = payload
        self.after: list[list[str]] = []
        self.held_for_sale = False
        self.refused = False


class SectionTotalScanner:
    """Rule 2 as a top-to-bottom scan of one table's rows.

    * :meth:`open` — a section heading ("Current assets"); call :meth:`settle` first.
    * :meth:`line` — a labelled value row.
    * :meth:`unlabelled` — an unlabelled value row: it closes the open section when the
      section's lines add up to it.
    * :meth:`abandon` — a row that cannot be read (ambiguous cells, an over-long label):
      the section in hand is refused.
    * :meth:`settle` — at the next heading and at the end of the table: returns the
      ``(key, payload)`` of the heading to fill, or nothing.

    A closed section is not final until the next heading. Labelled rows printed after
    the first subtotal and followed by ANOTHER unlabelled row ("Assets of disposal group
    held for sale" 2 000, then 3 300) mean the first subtotal was not the section's
    total: the later row is taken when it equals the first subtotal plus those rows, and
    otherwise the section is refused. A held-for-sale line after the subtotal with no
    such second subtotal also refuses it: the first subtotal excludes it. Missing,
    never a part.
    """

    def __init__(self) -> None:
        self._open: tuple[object, list[list[str]]] | None = None
        self._closed: _ClosedSection | None = None

    def open(self, key: object, label: str) -> None:
        self._open = (key, []) if is_section_total_heading(label) else None

    def line(self, label: str, values: Sequence[str]) -> None:
        if self._closed is not None:
            self._closed.after.append(list(values))
            if _HELD_FOR_SALE_RE.search(label or ""):
                self._closed.held_for_sale = True
        elif self._open is not None:
            self._open[1].append(list(values))

    def unlabelled(self, values: Sequence[str], payload: object) -> None:
        closed = self._closed
        if closed is not None:
            if closed.refused:
                return
            if closed.after and verified_section_total([closed.total, *closed.after],
                                                       values):
                closed.total, closed.payload = list(values), payload
                closed.after, closed.held_for_sale = [], False
            else:
                closed.refused = True
            return
        if self._open is not None:
            key, lines = self._open
            self._open = None
            if verified_section_total(lines, values):
                self._closed = _ClosedSection(key, list(values), payload)

    def abandon(self) -> None:
        self._open = None
        if self._closed is not None:
            self._closed.refused = True

    def settle(self) -> list[tuple[object, object]]:
        closed, self._closed, self._open = self._closed, None, None
        if closed is None or closed.refused or closed.held_for_sale:
            return []
        return [(closed.key, closed.payload)]


def section_totals_in_grid(
    rows: Sequence[Sequence[str]],
) -> tuple[list[list[str]], list[int]]:
    """Rule 2 over a plain grid (row 0 is the header; column 0 the label).

    A heading row (a label, no value) opens a section; labelled value rows are its
    lines; the next UNLABELLED value row closes it (:class:`SectionTotalScanner`). When
    the lines sum to that row, the heading row is given the subtotal's cells — the
    heading's own words now name the figure the issuer printed for it. Returns the new
    grid and the indices of the heading rows that were filled. Anything else is left
    exactly as it was.
    """
    grid = [list(r) for r in rows]
    filled: list[int] = []
    scanner = SectionTotalScanner()

    def fill(settled: list[tuple[object, object]]) -> None:
        for key, payload in settled:
            if not isinstance(key, int) or not isinstance(payload, list):
                continue
            heading, values = key, [str(v) for v in payload]
            width = len(grid[heading])
            grid[heading] = [grid[heading][0]] + [
                values[i] if i < len(values) else "" for i in range(width - 1)
            ]
            filled.append(heading)

    for index in range(1, len(grid)):
        row = grid[index]
        label = (row[0] if row else "").strip()
        values = row[1:]
        has_value = any((c or "").strip() for c in values)
        if label and not has_value:
            fill(scanner.settle())
            scanner.open(index, label)
        elif not has_value:
            continue
        elif label:
            scanner.line(label, values)
        else:
            scanner.unlabelled(values, list(values))
    fill(scanner.settle())
    return grid, filled


# ── Rule 3: a primary statement table outranks prose ─────────────────────── #

LINE_RANK_STATEMENT = 0
LINE_RANK_TABLE = 1
LINE_RANK_PROSE = 2

#: A table is statement-shaped when it prints at least this many value rows and reads
#: at least this many distinct statement lines: a three-row segment box ("Sales",
#: "Operating result", "Operating margin") is not a primary statement, and an income
#: statement whose top line is a bare "Sales" still reads operating profit and the
#: profit for the year.
STATEMENT_MIN_VALUE_ROWS = 8
STATEMENT_MIN_DISTINCT_LINES = 2

_TABLE_LOCATOR_RE = re.compile(r"^(?:p\d{1,4}:[a-z]\d{1,3}|t\d{1,4}|ixbrl:.{1,300})$", re.I)


def is_statement_shaped(value_rows: int, distinct_lines: int) -> bool:
    return (value_rows >= STATEMENT_MIN_VALUE_ROWS
            and distinct_lines >= STATEMENT_MIN_DISTINCT_LINES)


#: A primary statement prints the current period and its comparatives: at most three
#: period columns. A five- or ten-year record has the shape of a statement and is not.
MAX_STATEMENT_PERIOD_COLUMNS = 3
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
#: Words in a table's own header row, its headings or its page title that say it is NOT
#: the Group's primary statement: the parent company's own accounts, a segment note, a
#: multi-year record, a highlights / key-figures box, an issuer's adjusted measures.
_NOT_PRIMARY_STATEMENT_RE = re.compile(
    r"\bparent[- ](?:company|entity)\b|\bcompany[- ]only\b|\bholding company\b"
    r"|\bseparate (?:financial )?statements?\b|\bcompany (?:balance sheet|statement)"
    r"|\b(?:five|ten|\d{1,2})[- ]year (?:record|summary|review|overview|history)\b"
    r"|\b(?:five|ten)-year\b|\bmulti[- ]year\b"
    r"|\bsegment(?:al|s)?\b|\bdivision(?:al|s)?\b"
    r"|\bkey (?:figures|data|metrics|financials)\b|\bhighlights\b|\bat a glance\b"
    r"|\badjusted\b|\bunderlying\b|\bnon[- ]?gaap\b|\bpro[- ]?forma\b"
    r"|\balternative performance\b",
    re.I,
)


def may_be_primary_statement(header_cells: Sequence[str],
                             context: Sequence[str] = ()) -> bool:
    """ADR-092 review — the shape of a statement is not enough to outrank another read.

    False when the table prints more than :data:`MAX_STATEMENT_PERIOD_COLUMNS` distinct
    years in its header (a multi-year record), or when its header row, its headings or
    its page title (``context``) name a parent-company, segment, multi-year, highlights
    or adjusted table. Bounded: at most 40 cells and 8 headings of 300 characters."""
    header = " ".join(str(c or "")[:300] for c in list(header_cells)[:40])
    if len(set(_YEAR_RE.findall(header))) > MAX_STATEMENT_PERIOD_COLUMNS:
        return False
    texts = [header, *(str(t or "")[:300] for t in list(context)[:8])]
    return not any(_NOT_PRIMARY_STATEMENT_RE.search(t) for t in texts)


def line_rank(excerpt_id: str | None) -> int:
    """The rank of a stored fact by where it was read: a table cell (``p14:m0``,
    ``p12:t2``, ``t3``) or a tagged XBRL figure ranks above a prose excerpt (``X12``)."""
    locator = str(excerpt_id or "")
    return LINE_RANK_TABLE if _TABLE_LOCATOR_RE.match(locator) else LINE_RANK_PROSE


__all__ = [
    "LINE_RANK_PROSE",
    "LINE_RANK_STATEMENT",
    "LINE_RANK_TABLE",
    "MIN_SECTION_LINES",
    "SECTION_TOTAL_FIELDS",
    "TOTAL_LINE_ALIASES",
    "is_section_total_heading",
    "is_statement_shaped",
    "line_qualifies",
    "line_rank",
    "normalise_line_label",
    "section_totals_in_grid",
    "verified_section_total",
]
