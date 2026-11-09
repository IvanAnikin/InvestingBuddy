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

#: A note reference or a trailing qualifier a statement prints beside a line's name:
#: "Current assets 12", "Total assets (note 5)", "Cash and cash equivalents 14(a)",
#: "Total liabilities (restated)", "Current liabilities:". Applied to a stripped,
#: single-spaced label; every alternative is bounded, so a search is linear.
_TRAILING_QUALIFIER_RE = re.compile(
    r"(?:\((?:notes?\s?)?[0-9a-z.,\s]{0,20}\)"
    r"|(?<=\s)(?:notes?\s)?\d{1,2}(?:\.\d{1,2})?(?:\([a-z]\))?"
    r"|[¹²³⁴⁵⁶⁷⁸⁹⁰]{1,3}"
    r"|[:*†‡])$",
    re.I,
)
#: "… at end of the year", ", end of period", "at 31 December 2025", "at 30 June": the
#: closing balance of the cash line is the cash line (an OPENING balance is refused
#: separately by the validator). Applied to a stripped, single-spaced label.
_CLOSING_BALANCE_RE = re.compile(
    r",? (?:at (?:the )?)?end of (?:the )?(?:financial )?"
    r"(?:year|period|half[- ]year|quarter)$"
    r"| at \d{1,2} [a-z]{3,9}(?: \d{4})?$",
    re.I,
)
#: Operating-profit lines that are NOT the operating profit: an income line, a share
#: of an investee's, a non-operating item, a cost line, a segment's.
_OPERATING_PROFIT_COMPONENT_RE = re.compile(
    r"^ ?(?:net )?other operating"
    r"|\bshare of\b"
    r"|\bnon[- ]?operating\b"
    r"|\boperating (?:income|profit) and expenses?\b"
    r"|\bsegment(?:al)?\b",
    re.I,
)
_SPACE_RE = re.compile(r"\s+")


def normalise_line_label(label: str) -> str:
    """Lower case, single spaces, without the trailing note references / qualifiers."""
    text = _SPACE_RE.sub(" ", (label or "").strip().lower())
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
            closing = _CLOSING_BALANCE_RE.sub("", text).rstrip()
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


def section_totals_in_grid(
    rows: Sequence[Sequence[str]],
) -> tuple[list[list[str]], list[int]]:
    """Rule 2 over a plain grid (row 0 is the header; column 0 the label).

    A heading row (a label, no value) opens a section; labelled value rows are its
    lines; the next UNLABELLED value row closes it. When the lines sum to that row, the
    heading row is given the subtotal's cells — the heading's own words now name the
    figure the issuer printed for it. Returns the new grid and the indices of the
    heading rows that were filled. Anything else is left exactly as it was.
    """
    grid = [list(r) for r in rows]
    filled: list[int] = []
    heading: int | None = None
    lines: list[list[str]] = []
    for index in range(1, len(grid)):
        row = grid[index]
        label = (row[0] if row else "").strip()
        values = row[1:]
        has_value = any((c or "").strip() for c in values)
        if label and not has_value:
            heading, lines = index, []
            continue
        if not has_value:
            continue
        if label:
            if heading is not None:
                lines.append(values)
            continue
        # An unlabelled value row closes the open section, verified or not.
        if (heading is not None and is_section_total_heading(grid[heading][0])
                and verified_section_total(lines, values)):
            width = len(grid[heading])
            grid[heading] = [grid[heading][0]] + [
                values[i] if i < len(values) else "" for i in range(width - 1)
            ]
            filled.append(heading)
        heading, lines = None, []
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
