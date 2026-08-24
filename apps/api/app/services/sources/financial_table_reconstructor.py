"""Geometry-driven reconstruction of MULTI-YEAR financial tables from a PDF
page's positioned text — Phase 32D.

Why this module exists
----------------------
``primary_document_extractor`` recovers structured tables with pdfplumber's
``page.extract_tables()``, which is RULING-LINE driven. A glossy annual
report's "Five-year summary" / "Financial highlights" table is almost always
BORDERLESS: it has no cell rules at all, only whitespace alignment. On such a
page ``extract_tables()`` either returns nothing or — proven live against the
real 169-page Pandora Annual Report 2025, page 14 — a degenerate ONE-column
artifact (``[['2025'], ['32,549'], ['6%'], …]``) whose only "column" is the
row-header column the validator deliberately skips. No usable candidate is
produced.

The page's TEXT still contains every number, but by the time it reaches the
prose parser the grid has been flattened: one metric label followed by five
side-by-side values with nothing left to say which value belongs to which
year. The validator then (CORRECTLY) refuses to promote any of them — see
``extracted_fact_validator._candidates_from_table``'s "multiple unlabelled
magnitudes" branch and the prose parser's own ambiguity refusal. Fail-closed
behaviour is right; the missing capability is upstream of it.

What this module does
---------------------
It works from the ONLY representation that still carries the answer — the
positioned word boxes ``page.extract_words()`` returns — and rebuilds the grid
geometrically:

  1. cluster words into visual ROWS by their ``top`` coordinate;
  2. find HEADER rows: a row carrying >= 2 period tokens (``2025``, ``FY2025``);
  3. split those period tokens into COLUMN GROUPS — one group per physical
     table, so two tables printed side by side on one page (exactly Pandora
     page 14) never share a header map;
  4. accept a group only when its geometry actually looks like a table header
     (uniform column pitch, strictly monotonic distinct periods, a clean
     header band) — this is what rejects a body-text/footnote line that merely
     happens to mention several years;
  5. turn each group's period tokens into x-COLUMN BANDS;
  6. walk down the page assigning each row's numeric cells to bands by their
     own x-centre, refusing any cell that sits too close to a band edge;
  7. stop the region when prose intrudes into the value zone.

The result is handed back as a plain grid whose first row is the header, so
the EXISTING, already-validated ``extracted_fact_validator`` machinery
(``_column_periods`` → column→year map, ``_table_currency_scale`` →
``DKK million``, ``_match_label`` → metric vocabulary, the subtotal /
balance-sheet cross-checks, scope grouping and cross-method reconciliation)
consumes it unchanged. This module deliberately decides LAYOUT ONLY; it never
decides what a number MEANS, never normalizes a value, and never promotes
anything to a fact.

Fail-closed by construction
---------------------------
Every ambiguity is resolved by producing LESS, never by guessing: a value
equidistant between two columns is dropped, a row with two values landing in
one column is dropped, a header whose column pitch is irregular is not a
header, and a period form this codebase cannot represent losslessly (interim
"H1 2026", split-year "2025/26") is detected and then deliberately NOT
promoted rather than being flattened onto a bare fiscal year. Each refusal
records a machine-readable reason on the region so an operator can see WHY a
visible number did not become a fact.

Self-contained on purpose
-------------------------
The tiny geometry helpers below (``_f``, ``_text``, ``cluster_words_into_rows``)
intentionally mirror ``primary_document_extractor``'s ``_layout_f`` /
``_layout_text`` / row-grouping step rather than importing them: that module
imports THIS one, and its ``_group_words_into_lines`` additionally SPLITS a
row on any gap >= a gutter width — which is exactly wrong here, because a
multi-year table's whole point is that its cells ARE separated by such gaps.
Keeping this module dependency-free also keeps it unit-testable from raw word
dicts alone, with no PDF bytes and no extractor state.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Bounds (§ performance: never an unbounded scan of a 169-page document)
# --------------------------------------------------------------------------- #

# Words on one page above which table reconstruction is skipped entirely.
# Mirrors ``primary_document_extractor._MAX_WORDS_PER_PAGE_FOR_LAYOUT``.
MAX_WORDS_PER_PAGE = 6000
# Two words are on the same visual row when their ``top`` differs by less than
# this. Same value as the extractor's ``_LAYOUT_LINE_Y_TOLERANCE``.
ROW_Y_TOLERANCE = 3.0
# A header group must have at least this many period columns to be a table.
MIN_PERIOD_COLUMNS = 2
# ... and at most this many (a wider "table" is not a financial summary).
MAX_PERIOD_COLUMNS = 12
# Distinct tables reconstructed from one page.
MAX_REGIONS_PER_PAGE = 6
# Data rows examined below one header before the region is closed.
MAX_ROWS_PER_REGION = 80
# Header rows examined per page.
MAX_HEADER_ROWS_PER_PAGE = 12
# A row-label longer than this is not a metric label.
MAX_LABEL_CHARS = 200
# Heading-like lines retained per region for the caller's scope resolution.
MAX_HEADING_CANDIDATES = 12
# Heading-like lines collected ABOVE a table's header row. A borderless
# segment table states WHOSE figures it holds in a title printed above the
# header ("Jewellery Maisons" over "in €m 2026 2025"), never inside the grid.
MAX_PRECEDING_HEADINGS = 3
# Rows examined above the header while looking for those titles.
MAX_PRECEDING_ROWS_SCANNED = 8
# A digit group split off by a thousands SPACE is re-joined when the gap to
# the preceding token is no wider than this many points, or than
# ``THOUSANDS_SEPARATOR_GAP_SIZE_RATIO`` times the font size, whichever is
# larger. Measured at 1.9pt on the real Richemont annual report (8.5pt type).
MAX_THOUSANDS_SEPARATOR_GAP_PT = 3.0
THOUSANDS_SEPARATOR_GAP_SIZE_RATIO = 0.4
# Font size assumed when ``extract_words`` was called without the ``size``
# extra attr (the gap rule then falls back to the absolute bound above).
DEFAULT_FONT_SIZE_PT = 10.0
# A page's largest line is a SECTION title (worth carrying to the pages after
# it) only when it is at least this many times the page's own body size.
# Matches ``primary_document_extractor._PDF_HEADING_LARGE_SIZE_RATIO``.
SECTION_TITLE_SIZE_RATIO = 1.5

# --------------------------------------------------------------------------- #
# Geometry thresholds
# --------------------------------------------------------------------------- #

# A gap between two consecutive period tokens larger than this multiple of the
# row's median gap starts a NEW column group (a second table on the same row).
COLUMN_GROUP_SPLIT_FACTOR = 2.0
# Every gap inside one group must be within this fraction of the group's own
# median pitch. A real table header is uniformly pitched; a sentence that
# happens to name several years is not. This single check is what rejects the
# real Pandora page-14 footnote "… 22,985 in 2021 (-8%), 16,597 in 2022 (-5%),
# 13,645 in 2023 (-5%) and of 9,917 in 2024 (-3%)" (gaps 57.9 / 58.9 / 73.1).
MAX_PITCH_DEVIATION = 0.15
# Non-period words tolerated inside a single column band on the HEADER row
# (a footnote marker, a "Change" column caption); more than this and the row
# is body text, not a header.
MAX_HEADER_INTRUDERS_PER_COLUMN = 2
# A value cell whose centre sits closer than this fraction of the pitch to a
# band EDGE is ambiguous between two columns and is dropped.
COLUMN_AMBIGUITY_FRACTION = 0.18
# The region ends when the value zone carries this many prose-like words ...
REGION_END_PROSE_WORDS = 3
# ... totalling at least this many characters ...
REGION_END_PROSE_CHARS = 24
# ... including at least one purely alphabetic word of at least this length.
REGION_END_PROSE_MIN_ALPHA = 4
# A vertical jump larger than this multiple of the region's own median row
# pitch ends the region (a different block has started).
MAX_ROW_GAP_FACTOR = 3.5

# --------------------------------------------------------------------------- #
# Period vocabulary (§ header/year detection)
# --------------------------------------------------------------------------- #

PERIOD_TYPE_ANNUAL = "annual"
PERIOD_TYPE_INTERIM = "interim"
PERIOD_TYPE_SPLIT_YEAR = "split_year"

# A bare or FY-prefixed 4-digit year — the ONLY form this codebase can
# represent losslessly in ``ExtractedFact.period`` (a bare year string).
_ANNUAL_RE = re.compile(r"^(?:FY[-\s]?)?((?:19|20)\d{2})$", re.IGNORECASE)
# "2025/26", "2025/2026" — a straddling fiscal year. Detected so the region is
# not silently misread, never promoted (see ``_normalize_period``).
_SPLIT_YEAR_RE = re.compile(r"^((?:19|20)\d{2})/(\d{2}|\d{4})$")
# "H1", "H2", "Q1".."Q4" — an interim half/quarter marker, which may appear
# before or after its year as a separate word.
_INTERIM_MARKER_RE = re.compile(r"^(H[12]|Q[1-4])$", re.IGNORECASE)
_BARE_YEAR_RE = re.compile(r"^((?:19|20)\d{2})$")
# A trailing digit group produced by a thousands SPACE (the French/Nordic
# convention Richemont, LVMH and Hermès all print): exactly three digits,
# optionally closing a parenthesised negative or carrying a percent sign.
_THOUSANDS_GROUP_RE = re.compile(r"^\d{3}[)%]?$")
_ENDS_WITH_DIGIT_RE = re.compile(r"\d$")

# A numeric value cell: optional sign / parenthesised negative, digit groups
# with , . or thin-space separators, optional trailing % or footnote marker.
# Deliberately a LAYOUT test only — the authoritative parse stays with
# ``primary_fact_parser._norm_number`` once the grid reaches the validator.
_NUMERIC_CELL_RE = re.compile(
    r"^[(\-–−+]?\s*\d[\d\s,.  ]*\s*\)?\s*%?$"
)
# Cells that mean "no value here" — neither a number nor prose. They must not
# end a region (a real table routinely leaves an early year blank).
_PLACEHOLDER_CELLS = frozenset({"-", "–", "—", "‒", "n/a", "n.a.", "na", "*", "†", "‡"})


# --------------------------------------------------------------------------- #
# Diagnostics vocabulary (§ observability — never silently discard a refusal)
# --------------------------------------------------------------------------- #

REASON_AMBIGUOUS_PERIOD_COLUMN = "ambiguous_period_column"
REASON_COLUMN_ALIGNMENT_UNCERTAIN = "column_alignment_uncertain"
REASON_DUPLICATE_COLUMN_ASSIGNMENT = "duplicate_column_assignment"
REASON_IRREGULAR_COLUMN_PITCH = "irregular_column_pitch"
REASON_NON_MONOTONIC_PERIODS = "non_monotonic_periods"
REASON_REPEATED_PERIOD = "repeated_period"
REASON_HEADER_BAND_NOT_CLEAN = "header_band_not_clean"
REASON_MIXED_PERIOD_TYPES = "mixed_period_types"
REASON_INTERIM_PERIOD_UNSUPPORTED = "interim_period_unsupported"
REASON_SPLIT_YEAR_PERIOD_UNSUPPORTED = "split_year_period_unsupported"
REASON_TOO_FEW_COLUMNS = "too_few_period_columns"
REASON_TOO_MANY_COLUMNS = "too_many_period_columns"


# --------------------------------------------------------------------------- #
# Typed result (§ static/contract discipline — no bare dict shapes)
# --------------------------------------------------------------------------- #


class TablePeriodColumn(BaseModel):
    """One period column of a reconstructed table, anchored in page space."""

    column_index: int
    # Normalized period as it will be written into the emitted header cell.
    # For an annual column this is the bare 4-digit year the rest of the
    # pipeline already understands.
    period: str
    period_type: str
    # The header token exactly as printed (e.g. "FY2025").
    header_text: str
    header_x_center: float
    band_x_min: float
    band_x_max: float


class TableCell(BaseModel):
    """One value cell that was deterministically assigned to a period column."""

    column_index: int
    period: str
    text: str
    value_x_center: float


class TableRow(BaseModel):
    """One metric row: a label plus the cells that were safely assigned."""

    label: str
    row_y: float
    cells: list[TableCell] = Field(default_factory=list)


class TableRejection(BaseModel):
    """A machine-readable record of something this module refused to map."""

    reason: str
    detail: str


class ReconstructedFinancialTable(BaseModel):
    """One borderless multi-year financial table recovered from a PDF page.

    ``promotable`` is False when the table's geometry was recovered correctly
    but its PERIOD form cannot be represented losslessly by the downstream
    ``ExtractedFact.period`` contract (interim / split-year). Such a table is
    still returned — so diagnostics can show it was seen and understood — but
    the caller must not turn it into facts.
    """

    page_number: int
    region_index: int
    columns: list[TablePeriodColumn] = Field(default_factory=list)
    rows: list[TableRow] = Field(default_factory=list)
    # The header row's own label-zone text, which is where a borderless
    # financial table states its unit ("DKK million", "€m").
    unit_label: str | None = None
    # The FIRST heading-like line found inside this region's own x-window
    # (display/diagnostics). Never a guess — None when absent.
    region_heading: str | None = None
    # EVERY label-only line inside the region, in document order. A borderless
    # financial table routinely carries sub-headings between its row blocks
    # ("Financial highlights", "Consolidated balance sheet"), and the caller
    # resolves SCOPE from all of them rather than only the first — a segment
    # sub-heading half way down a region governs the rows beneath it just as
    # much as the one at the top.
    heading_candidates: list[str] = Field(default_factory=list)
    # Heading-like lines found directly ABOVE the header row, in the region's
    # own x-window, NEAREST FIRST. A segment table names its entity in a title
    # printed above the grid ("Jewellery Maisons" over "in €m 2026 2025"),
    # under a section title that supplies the segment SIGNAL ("Sales and
    # operating results by segment"). Ordering matters: the caller feeds
    # consecutive pairs to ``_infer_scope(leaf, ancestor)``, which is built for
    # exactly this leaf-under-generic-section shape.
    preceding_headings: list[str] = Field(default_factory=list)
    # The page's own largest-type title line, when the page has one that is
    # genuinely larger than its body text and sits above this table. Real
    # reports state the segment SIGNAL once per page ("Sales and operating
    # results by segment") and then print each segment's own name directly
    # over its grid; only the first table on such a page has that page title
    # as its immediate neighbour, so without this every LATER segment table
    # on the page would lose its scope. Font-size derived, exactly like
    # ``primary_document_extractor._page_heading_sizes``.
    page_title: str | None = None
    # The largest-type title carried in from an EARLIER, CONTIGUOUS page, when
    # this page has none of its own above the table. A segment review runs
    # over several pages under one "… by segment" title stated on the first of
    # them; the later pages print only each segment's own name.
    carried_page_title: str | None = None
    header_y: float
    x_min: float
    x_max: float
    promotable: bool = True
    rejections: list[TableRejection] = Field(default_factory=list)

    @property
    def period_types(self) -> set[str]:
        return {c.period_type for c in self.columns}

    def to_grid(self) -> list[list[str]]:
        """Render as the plain header-first grid ``ExtractedTable.rows`` expects.

        Row 0 is the header: the unit label in column 0 (so
        ``_table_currency_scale`` can read "DKK million" straight off the
        table) followed by one period per column. Every later row is a metric
        label in column 0 plus that row's cells, with an EMPTY string wherever
        no value could be safely assigned — a blank is honest, a shifted value
        would not be.
        """
        width = len(self.columns)
        grid: list[list[str]] = [
            [self.unit_label or ""] + [c.period for c in self.columns]
        ]
        for row in self.rows:
            by_col = {cell.column_index: cell.text for cell in row.cells}
            grid.append([row.label] + [by_col.get(i, "") for i in range(width)])
        return grid


# --------------------------------------------------------------------------- #
# Small geometry helpers (see the module docstring for why they live here)
# --------------------------------------------------------------------------- #


def _f(word: Mapping[str, object], key: str) -> float:
    value = word.get(key, 0.0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _text(word: Mapping[str, object]) -> str:
    value = word.get("text", "")
    return value.strip() if isinstance(value, str) else ""


def _center(word: Mapping[str, object]) -> float:
    return (_f(word, "x0") + _f(word, "x1")) / 2.0


def cluster_words_into_rows(
    words: Sequence[Mapping[str, object]],
) -> list[list[Mapping[str, object]]]:
    """Cluster words into visual ROWS by ``top`` only, left-to-right within a row.

    Unlike ``primary_document_extractor._group_words_into_lines`` this never
    splits a row on a wide horizontal gap: in a multi-year table those gaps
    ARE the columns.
    """
    ordered = sorted(words, key=lambda w: (round(_f(w, "top"), 1), _f(w, "x0")))
    rows: list[list[Mapping[str, object]]] = []
    for word in ordered:
        top = _f(word, "top")
        if rows and abs(top - _f(rows[-1][0], "top")) <= ROW_Y_TOLERANCE:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [
        _join_space_separated_thousands(sorted(row, key=lambda w: _f(w, "x0")))
        for row in rows
    ]


def _join_space_separated_thousands(
    row: list[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    """Re-join a number a thousands SPACE split into several word boxes.

    Continental European issuers print "16 539", not "16,539", so
    ``extract_words()`` hands back "16" and "539" as two separate boxes. Left
    alone, each fragment is assigned to a column on its own: they land in the
    same band, the row is flagged as a duplicate assignment, and a perfectly
    good "Sales 16 539 15 328" row is discarded. Proven against the real
    Richemont annual report, where this cost every ``Sales`` and ``Operating
    result`` row on its segment pages while the single-token ``Operating
    margin 30.5%`` row came through fine.

    Only joins when the next box is EXACTLY three digits, the previous box
    ends in a digit, and the gap between them is no wider than a space at
    that font size — so "31 December", "2026 2025" (four digits) and ordinary
    prose are all untouched.
    """
    if len(row) < 2:
        return row
    out: list[Mapping[str, object]] = []
    for word in row:
        text = _text(word)
        if out and _THOUSANDS_GROUP_RE.match(text):
            previous = out[-1]
            gap = _f(word, "x0") - _f(previous, "x1")
            size = _f(previous, "size") or DEFAULT_FONT_SIZE_PT
            limit = max(
                MAX_THOUSANDS_SEPARATOR_GAP_PT,
                size * THOUSANDS_SEPARATOR_GAP_SIZE_RATIO,
            )
            if 0 <= gap <= limit and _ENDS_WITH_DIGIT_RE.search(_text(previous)):
                merged = dict(previous)
                merged["text"] = f"{_text(previous)} {text}"
                merged["x1"] = _f(word, "x1")
                out[-1] = merged
                continue
        out.append(word)
    return out


def is_numeric_cell(text: str) -> bool:
    """True when ``text`` looks like a table VALUE cell (layout test only)."""
    stripped = text.strip()
    if not stripped or not any(ch.isdigit() for ch in stripped):
        return False
    return bool(_NUMERIC_CELL_RE.match(stripped))


def _is_placeholder(text: str) -> bool:
    return text.strip().lower() in _PLACEHOLDER_CELLS


# --------------------------------------------------------------------------- #
# Period tokens
# --------------------------------------------------------------------------- #


class _PeriodToken:
    """A period header token: its normalized value plus where it sits."""

    __slots__ = ("period", "period_type", "text", "x_center", "sort_key", "words")

    def __init__(
        self,
        *,
        period: str,
        period_type: str,
        text: str,
        x_center: float,
        sort_key: int,
        words: list[Mapping[str, object]],
    ) -> None:
        self.period = period
        self.period_type = period_type
        self.text = text
        self.x_center = x_center
        self.sort_key = sort_key
        self.words = words


def _period_tokens(row: Sequence[Mapping[str, object]]) -> list[_PeriodToken]:
    """Every period token on one row, left to right.

    Recognises a single-word form (``2025``, ``FY2025``, ``2025/26``) and the
    bounded two-word interim forms (``H1 2026`` and ``2026 H1``) — an interim
    marker is only ever merged with a bare year DIRECTLY beside it, so a
    stray "Q1" elsewhere on the line can never capture an unrelated year.
    """
    tokens: list[_PeriodToken] = []
    index = 0
    while index < len(row):
        word = row[index]
        text = _text(word)
        nxt = row[index + 1] if index + 1 < len(row) else None
        nxt_text = _text(nxt) if nxt is not None else ""

        # "H1" + "2026"
        if _INTERIM_MARKER_RE.match(text) and _BARE_YEAR_RE.match(nxt_text):
            assert nxt is not None
            tokens.append(
                _PeriodToken(
                    period=f"{text.upper()} {nxt_text}",
                    period_type=PERIOD_TYPE_INTERIM,
                    text=f"{text} {nxt_text}",
                    x_center=(_f(word, "x0") + _f(nxt, "x1")) / 2.0,
                    sort_key=int(nxt_text) * 10 + int(text[1]),
                    words=[word, nxt],
                )
            )
            index += 2
            continue
        # "2026" + "H1"
        if _BARE_YEAR_RE.match(text) and _INTERIM_MARKER_RE.match(nxt_text):
            assert nxt is not None
            tokens.append(
                _PeriodToken(
                    period=f"{nxt_text.upper()} {text}",
                    period_type=PERIOD_TYPE_INTERIM,
                    text=f"{text} {nxt_text}",
                    x_center=(_f(word, "x0") + _f(nxt, "x1")) / 2.0,
                    sort_key=int(text) * 10 + int(nxt_text[1]),
                    words=[word, nxt],
                )
            )
            index += 2
            continue

        annual = _ANNUAL_RE.match(text)
        if annual:
            year = annual.group(1)
            tokens.append(
                _PeriodToken(
                    period=year,
                    period_type=PERIOD_TYPE_ANNUAL,
                    text=text,
                    x_center=_center(word),
                    sort_key=int(year) * 10,
                    words=[word],
                )
            )
            index += 1
            continue

        split = _SPLIT_YEAR_RE.match(text)
        if split:
            tokens.append(
                _PeriodToken(
                    period=text,
                    period_type=PERIOD_TYPE_SPLIT_YEAR,
                    text=text,
                    x_center=_center(word),
                    sort_key=int(split.group(1)) * 10,
                    words=[word],
                )
            )
            index += 1
            continue

        index += 1
    return tokens


# --------------------------------------------------------------------------- #
# Header groups → column bands
# --------------------------------------------------------------------------- #


class _ColumnGroup:
    """A candidate header for ONE table: its period tokens and their bands."""

    __slots__ = ("tokens", "pitch", "bands", "x_min", "x_max")

    def __init__(
        self,
        tokens: list[_PeriodToken],
        pitch: float,
        bands: list[tuple[float, float]],
    ) -> None:
        self.tokens = tokens
        self.pitch = pitch
        self.bands = bands
        self.x_min = bands[0][0]
        self.x_max = bands[-1][1]


def _split_into_groups(tokens: list[_PeriodToken]) -> list[list[_PeriodToken]]:
    """Split a header row's period tokens into one list per physical table.

    A gap markedly wider than the row's own median token gap means the next
    token belongs to a DIFFERENT table printed alongside this one — the exact
    shape of Pandora's page 14, where "Financial highlights" and "Stock
    ratios" share every header row (gaps 42.0 / 42.5 / 42.5 / 42.5 / **257.0**
    / 42.5 / 42.5 / 42.5 / 42.5).
    """
    if len(tokens) < 2:
        return [tokens] if tokens else []
    gaps = [tokens[i + 1].x_center - tokens[i].x_center for i in range(len(tokens) - 1)]
    median_gap = statistics.median(gaps)
    groups: list[list[_PeriodToken]] = [[tokens[0]]]
    for i, gap in enumerate(gaps):
        if median_gap > 0 and gap > median_gap * COLUMN_GROUP_SPLIT_FACTOR:
            groups.append([tokens[i + 1]])
        else:
            groups[-1].append(tokens[i + 1])
    return groups


def _qualify_group(
    tokens: list[_PeriodToken],
    row: Sequence[Mapping[str, object]],
    rejections: list[TableRejection],
) -> _ColumnGroup | None:
    """Decide whether one candidate group really is a table header.

    Every check here exists to REFUSE a plausible-looking non-header. A row of
    body text that names several years will normally fail the pitch check; a
    header whose columns repeat a period, run in no consistent direction, or
    mix annual with interim columns is not something a value can be assigned
    to unambiguously.
    """
    detail = " ".join(t.text for t in tokens)
    if len(tokens) < MIN_PERIOD_COLUMNS:
        rejections.append(TableRejection(reason=REASON_TOO_FEW_COLUMNS, detail=detail))
        return None
    if len(tokens) > MAX_PERIOD_COLUMNS:
        rejections.append(TableRejection(reason=REASON_TOO_MANY_COLUMNS, detail=detail))
        return None

    if len({t.period_type for t in tokens}) > 1:
        rejections.append(
            TableRejection(reason=REASON_MIXED_PERIOD_TYPES, detail=detail)
        )
        return None

    periods = [t.period for t in tokens]
    if len(set(periods)) != len(periods):
        rejections.append(TableRejection(reason=REASON_REPEATED_PERIOD, detail=detail))
        return None

    keys = [t.sort_key for t in tokens]
    ascending = all(keys[i] < keys[i + 1] for i in range(len(keys) - 1))
    descending = all(keys[i] > keys[i + 1] for i in range(len(keys) - 1))
    if not (ascending or descending):
        # Neither newest-left nor newest-right: an ordering this module cannot
        # justify mapping (§ "unusual ordering if unsupported → fail closed").
        rejections.append(
            TableRejection(reason=REASON_NON_MONOTONIC_PERIODS, detail=detail)
        )
        return None

    gaps = [tokens[i + 1].x_center - tokens[i].x_center for i in range(len(tokens) - 1)]
    pitch = statistics.median(gaps)
    if pitch <= 0 or any(abs(g - pitch) / pitch > MAX_PITCH_DEVIATION for g in gaps):
        rejections.append(
            TableRejection(
                reason=REASON_IRREGULAR_COLUMN_PITCH,
                detail=f"{detail} pitch={[round(g, 1) for g in gaps]}",
            )
        )
        return None

    centers = [t.x_center for t in tokens]
    bands: list[tuple[float, float]] = []
    for i, center in enumerate(centers):
        low = (centers[i - 1] + center) / 2.0 if i > 0 else center - pitch / 2.0
        high = (
            (center + centers[i + 1]) / 2.0
            if i < len(centers) - 1
            else center + pitch / 2.0
        )
        bands.append((low, high))

    # The header band must be CLEAN: a genuine header column contains its
    # period token and little else. A sentence that names years has ordinary
    # words sitting between them.
    own_words = {id(w) for t in tokens for w in t.words}
    for (low, high), token in zip(bands, tokens):
        intruders = [
            w
            for w in row
            if id(w) not in own_words and low <= _center(w) < high and _text(w)
        ]
        if len(intruders) > MAX_HEADER_INTRUDERS_PER_COLUMN:
            rejections.append(
                TableRejection(
                    reason=REASON_HEADER_BAND_NOT_CLEAN,
                    detail=f"{token.text}: " + " ".join(_text(w) for w in intruders[:6]),
                )
            )
            return None

    return _ColumnGroup(tokens, pitch, bands)


# --------------------------------------------------------------------------- #
# Region walking
# --------------------------------------------------------------------------- #


def _assign_cells(
    value_words: Sequence[Mapping[str, object]],
    group: _ColumnGroup,
    rejections: list[TableRejection],
    row_label: str,
) -> tuple[list[TableCell], bool]:
    """Assign a row's value words to period columns by x-centre.

    Returns ``(cells, ambiguous)``. A cell is only produced when its centre
    lands INSIDE one band and is comfortably clear of both band edges; two
    values landing in the same band make the whole row ambiguous, because that
    means the row is not aligned to this header at all.
    """
    cells: list[TableCell] = []
    seen: dict[int, str] = {}
    ambiguous = False
    margin = group.pitch * COLUMN_AMBIGUITY_FRACTION
    for word in value_words:
        text = _text(word)
        if not is_numeric_cell(text):
            continue
        center = _center(word)
        for index, (low, high) in enumerate(group.bands):
            if not (low <= center < high):
                continue
            if (center - low) < margin or (high - center) < margin:
                ambiguous = True
                rejections.append(
                    TableRejection(
                        reason=REASON_COLUMN_ALIGNMENT_UNCERTAIN,
                        detail=f"{row_label[:60]}: '{text}' sits on a column edge",
                    )
                )
                break
            if index in seen:
                ambiguous = True
                rejections.append(
                    TableRejection(
                        reason=REASON_DUPLICATE_COLUMN_ASSIGNMENT,
                        detail=(
                            f"{row_label[:60]}: '{seen[index]}' and '{text}' both "
                            f"map to {group.tokens[index].text}"
                        ),
                    )
                )
                break
            seen[index] = text
            cells.append(
                TableCell(
                    column_index=index,
                    period=group.tokens[index].period,
                    text=text,
                    value_x_center=round(center, 2),
                )
            )
            break
    return cells, ambiguous


def _is_prose_intrusion(words: Sequence[Mapping[str, object]]) -> bool:
    """True when the value zone holds running prose rather than table cells.

    Requires several genuinely word-like tokens, not merely non-numeric ones:
    a table legitimately contains cells like "44/56" or "-" that must not end
    the region.
    """
    prose = [
        _text(w)
        for w in words
        if _text(w) and not is_numeric_cell(_text(w)) and not _is_placeholder(_text(w))
    ]
    if len(prose) < REGION_END_PROSE_WORDS:
        return False
    if sum(len(t) for t in prose) < REGION_END_PROSE_CHARS:
        return False
    return any(t.isalpha() and len(t) >= REGION_END_PROSE_MIN_ALPHA for t in prose)


def _looks_like_continuation(label: str) -> bool:
    """True when a row label is the TAIL of a label wrapped from the row above.

    A wrapped metric name continues in lower case ("and amortisation
    (EBITDA)", "months revenue"); a new row — or a section sub-heading such as
    "Financial highlights" — starts with a capital. Purely orthographic, so it
    can never join two unrelated rows that both begin as sentences do.
    """
    stripped = label.lstrip("([")
    return bool(stripped) and stripped[0].islower()


def _page_title(
    rows: list[list[Mapping[str, object]]],
) -> tuple[str, float, float] | None:
    """The page's largest-type heading line as ``(text, top, size_ratio)``.

    ``size_ratio`` is that line's font size divided by the page's own most
    common (body) size, so a caller can tell a genuine SECTION title (set
    markedly larger than body text) from a mere leaf heading one notch up.
    Returns ``None`` unless the page's biggest font is strictly larger than
    its body font — a page set entirely in one size has no title, and
    guessing one would be worse than having none.
    """
    sizes: list[float] = []
    for row in rows:
        for word in row:
            size = _f(word, "size")
            if size > 0:
                sizes.append(round(size, 1))
    if not sizes:
        return None
    biggest = max(sizes)
    # MEDIAN, not mode: a page whose largest heading happens to contain more
    # words than any single body-size run would otherwise elect that heading
    # itself as the "body" size and conclude the page has no title at all.
    body = statistics.median(sizes)
    if biggest <= body:
        return None
    for row in rows:
        row_size = max((_f(w, "size") for w in row), default=0.0)
        if round(row_size, 1) < biggest:
            continue
        text = " ".join(_text(w) for w in row).strip()
        if text and len(text) <= MAX_LABEL_CHARS:
            return text, _f(row[0], "top"), biggest / body if body > 0 else 1.0
    return None


def _resolve_page_title(
    own: tuple[str, float, float] | None,
    carried: str | None,
    header_top: float,
    preceding: list[str],
) -> str | None:
    """The title line that governs this table: its own page's, else the carried one."""
    if own is not None and own[1] < header_top and own[0] not in preceding:
        return own[0]
    if carried and carried not in preceding:
        return carried
    return None


def _region_x_windows(groups: list[_ColumnGroup], page_width: float) -> list[tuple[float, float]]:
    """Horizontal window owned by each table on the row, left to right.

    A table owns everything from where the previous table's last column band
    ends up to the end of its OWN last band — so a row-label printed to the
    left of a table's first column (Pandora page 14's right-hand table starts
    its labels at x=462, just past the left table's last band edge at ~455.5)
    is attributed to the right table, and never mistaken for part of the left
    one's five-value row.
    """
    windows: list[tuple[float, float]] = []
    for index, group in enumerate(groups):
        low = 0.0 if index == 0 else groups[index - 1].x_max
        high = group.x_max if index < len(groups) - 1 else max(page_width, group.x_max)
        windows.append((low, high))
    return windows


def page_title_of(
    words: Sequence[Mapping[str, object]],
) -> tuple[str, float, float] | None:
    """This page's own largest-type title line, if any.

    Returns ``(text, top, size_ratio)`` — see :func:`_page_title`. Exposed so
    the caller can carry a SECTION title across a CONTIGUOUS page run, the
    same way ``primary_document_extractor`` already carries ``running_scope``
    and its heading stack.
    """
    if not words or len(words) > MAX_WORDS_PER_PAGE:
        return None
    return _page_title(cluster_words_into_rows(words))


def reconstruct_financial_tables(
    words: Sequence[Mapping[str, object]],
    *,
    page_width: float,
    page_number: int,
    carried_page_title: str | None = None,
) -> list[ReconstructedFinancialTable]:
    """Rebuild every borderless multi-year financial table on ONE PDF page.

    ``words`` is pdfplumber's ``page.extract_words()`` output (only
    ``text``/``x0``/``x1``/``top`` are read). Never raises and never guesses:
    a page with no qualifying header simply yields an empty list, and the
    caller's existing extraction path is completely unaffected.
    """
    if not words or len(words) > MAX_WORDS_PER_PAGE or page_width <= 0:
        return []

    rows = cluster_words_into_rows(words)
    page_title = _page_title(rows)
    tables: list[ReconstructedFinancialTable] = []
    header_rows_seen = 0
    # Rows already consumed as the body of an earlier region on this page, so
    # a second header lower down never re-reads the first table's rows.
    consumed: set[int] = set()

    for row_index, row in enumerate(rows):
        if len(tables) >= MAX_REGIONS_PER_PAGE:
            break
        if header_rows_seen >= MAX_HEADER_ROWS_PER_PAGE:
            break
        if row_index in consumed:
            continue
        tokens = _period_tokens(row)
        if len(tokens) < MIN_PERIOD_COLUMNS:
            continue
        header_rows_seen += 1

        rejections: list[TableRejection] = []
        groups = [
            qualified
            for candidate in _split_into_groups(tokens)
            if (qualified := _qualify_group(candidate, row, rejections)) is not None
        ]
        if not groups:
            continue

        windows = _region_x_windows(groups, page_width)
        for group, (win_low, win_high) in zip(groups, windows):
            table = _walk_region(
                rows=rows,
                start_index=row_index,
                group=group,
                window=(win_low, win_high),
                page_number=page_number,
                region_index=len(tables),
                header_row=row,
                rejections=list(rejections),
                consumed=consumed,
                page_title=page_title,
                carried_page_title=carried_page_title,
            )
            if table is not None and table.rows:
                tables.append(table)
            if len(tables) >= MAX_REGIONS_PER_PAGE:
                break
    return tables


def _walk_region(
    *,
    rows: list[list[Mapping[str, object]]],
    start_index: int,
    group: _ColumnGroup,
    window: tuple[float, float],
    page_number: int,
    region_index: int,
    header_row: Sequence[Mapping[str, object]],
    rejections: list[TableRejection],
    consumed: set[int],
    page_title: tuple[str, float, float] | None,
    carried_page_title: str | None = None,
) -> ReconstructedFinancialTable | None:
    """Walk down the page from one qualified header, collecting metric rows."""
    win_low, win_high = window
    header_own = {id(w) for t in group.tokens for w in t.words}

    def in_window(word: Mapping[str, object]) -> bool:
        return win_low <= _center(word) < win_high

    # The header's own label zone is where a borderless financial table states
    # its unit ("DKK million").
    unit_label = " ".join(
        _text(w)
        for w in header_row
        if in_window(w) and id(w) not in header_own and _center(w) < group.x_min
    ).strip() or None

    period_types = {t.period_type for t in group.tokens}
    promotable = period_types == {PERIOD_TYPE_ANNUAL}
    if PERIOD_TYPE_INTERIM in period_types:
        rejections.append(
            TableRejection(
                reason=REASON_INTERIM_PERIOD_UNSUPPORTED,
                detail=" ".join(t.text for t in group.tokens),
            )
        )
    if PERIOD_TYPE_SPLIT_YEAR in period_types:
        rejections.append(
            TableRejection(
                reason=REASON_SPLIT_YEAR_PERIOD_UNSUPPORTED,
                detail=" ".join(t.text for t in group.tokens),
            )
        )

    preceding_headings: list[str] = []
    for back in range(1, MAX_PRECEDING_ROWS_SCANNED + 1):
        above_index = start_index - back
        if above_index < 0 or len(preceding_headings) >= MAX_PRECEDING_HEADINGS:
            break
        above = [w for w in rows[above_index] if in_window(w) and _text(w)]
        if not above:
            continue  # nothing of this table's own above that row
        if any(is_numeric_cell(_text(w)) for w in above):
            break  # a data/other-table row, not a title
        title = " ".join(_text(w) for w in above).strip()
        if title and len(title) <= MAX_LABEL_CHARS:
            preceding_headings.append(title)

    out_rows: list[TableRow] = []
    heading_candidates: list[str] = []
    pending_label: str | None = None
    row_tops: list[float] = []
    previous_top = _f(header_row[0], "top")

    for offset in range(1, MAX_ROWS_PER_REGION + 1):
        index = start_index + offset
        if index >= len(rows):
            break
        row = rows[index]
        scoped = [w for w in row if in_window(w) and _text(w)]
        if not scoped:
            continue

        top = _f(scoped[0], "top")
        if row_tops:
            median_pitch = statistics.median(row_tops)
            if median_pitch > 0 and (top - previous_top) > median_pitch * MAX_ROW_GAP_FACTOR:
                break  # a large vertical jump: a different block has begun
        label_words = [w for w in scoped if _center(w) < group.x_min]
        value_words = [w for w in scoped if _center(w) >= group.x_min]

        if _is_prose_intrusion(value_words):
            break

        # A second period-header row inside this window ends the region: a new
        # table has started underneath this one (§ multiple tables on one
        # page). Deliberately a plain token count rather than a full header
        # re-qualification — ending a region early only ever costs rows,
        # while running one header's column map over a DIFFERENT table's rows
        # is precisely the mis-association this module exists to prevent.
        if len(_period_tokens(scoped)) >= MIN_PERIOD_COLUMNS:
            break

        label = " ".join(_text(w) for w in label_words).strip()
        cells, ambiguous = _assign_cells(value_words, group, rejections, label or "?")

        if not cells:
            # A label-only line is either this region's heading (the first one
            # seen) or the first half of a wrapped metric name.
            if label and len(label) <= MAX_LABEL_CHARS:
                pending_label = label
                if len(heading_candidates) < MAX_HEADING_CANDIDATES:
                    heading_candidates.append(label)
            continue

        if ambiguous:
            pending_label = None
            continue
        if not label:
            pending_label = None
            continue
        if _looks_like_continuation(label) and pending_label:
            label = f"{pending_label} {label}"
        pending_label = None
        if len(label) > MAX_LABEL_CHARS:
            continue

        out_rows.append(
            TableRow(label=label, row_y=round(top, 2), cells=cells)
        )
        row_tops.append(top - previous_top)
        previous_top = top

    if not out_rows:
        return None

    return ReconstructedFinancialTable(
        page_number=page_number,
        region_index=region_index,
        columns=[
            TablePeriodColumn(
                column_index=i,
                period=token.period,
                period_type=token.period_type,
                header_text=token.text,
                header_x_center=round(token.x_center, 2),
                band_x_min=round(group.bands[i][0], 2),
                band_x_max=round(group.bands[i][1], 2),
            )
            for i, token in enumerate(group.tokens)
        ],
        rows=out_rows,
        unit_label=unit_label,
        region_heading=heading_candidates[0] if heading_candidates else None,
        heading_candidates=heading_candidates,
        preceding_headings=preceding_headings,
        page_title=_resolve_page_title(
            page_title, carried_page_title, _f(header_row[0], "top"), preceding_headings
        ),
        carried_page_title=carried_page_title,
        header_y=round(_f(header_row[0], "top"), 2),
        x_min=round(group.x_min, 2),
        x_max=round(group.x_max, 2),
        promotable=promotable,
        rejections=rejections,
    )


__all__ = [
    "MAX_WORDS_PER_PAGE",
    "page_title_of",
    "PERIOD_TYPE_ANNUAL",
    "PERIOD_TYPE_INTERIM",
    "PERIOD_TYPE_SPLIT_YEAR",
    "REASON_AMBIGUOUS_PERIOD_COLUMN",
    "REASON_COLUMN_ALIGNMENT_UNCERTAIN",
    "REASON_DUPLICATE_COLUMN_ASSIGNMENT",
    "REASON_HEADER_BAND_NOT_CLEAN",
    "REASON_INTERIM_PERIOD_UNSUPPORTED",
    "REASON_IRREGULAR_COLUMN_PITCH",
    "REASON_MIXED_PERIOD_TYPES",
    "REASON_NON_MONOTONIC_PERIODS",
    "REASON_REPEATED_PERIOD",
    "REASON_SPLIT_YEAR_PERIOD_UNSUPPORTED",
    "ReconstructedFinancialTable",
    "TableCell",
    "TablePeriodColumn",
    "TableRejection",
    "TableRow",
    "cluster_words_into_rows",
    "is_numeric_cell",
    "reconstruct_financial_tables",
]
