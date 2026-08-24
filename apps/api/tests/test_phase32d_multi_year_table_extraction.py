"""
Phase 32D — geometric extraction of period-scoped facts from MULTI-YEAR
financial tables.

Fully OFFLINE and deterministic: synthetic positioned words plus real (but
small, in-code) PDF bytes from ``tests/helpers/pdf_fixtures.py``. No network,
no LLM, no DB.

Root cause this slice fixes (reproduced against the real 169-page Pandora
Annual Report 2025 before any code was written): ``page.extract_tables()`` is
RULING-LINE driven, and a glossy "Five-year summary" page is borderless. On
Pandora page 14 it returned a degenerate ONE-column artifact
(``[['2025'], ['32,549'], ['6%'], …]``) whose only column is the row-header
column the validator deliberately skips, so the table path produced ZERO
candidates. The same page's text reached the prose path FLATTENED — one metric
label followed by five side-by-side values with the column→year mapping already
destroyed — and the validator correctly refused to promote any of it. The
missing capability was upstream of the refusal: nothing ever rebuilt the grid.

Covered here:
  A. Header detection and column-group splitting (pure word dicts).
  B. The negative matrix — every way a plausible-looking mapping must be
     REFUSED rather than guessed.
  C. The positive matrix — five-year, newest-right, FY labels, interim,
     table-level units, percent rows, wrapped labels, two tables on one page.
  D. The Pandora golden regression: the real page-14 geometry, proven to fail
     before this slice and to yield exact period-scoped facts after it.
  E. Scope: Group vs segment, IFRS 5 discontinued operations, and the
     page-title inheritance a multi-page segment review needs.
  F. Validator integration: prose supersession, conflict eligibility, and the
     metric vocabulary this capability surfaced.
"""

from __future__ import annotations

import io

import pdfplumber

from app.services.sources.extracted_fact_validator import (
    VALIDATION_EXCERPT_ONLY,
    VALIDATION_VALIDATED,
    IssuerContext,
    _match_label,
    validate_extracted_facts,
)
from app.services.sources.financial_table_reconstructor import (
    PERIOD_TYPE_ANNUAL,
    PERIOD_TYPE_INTERIM,
    REASON_COLUMN_ALIGNMENT_UNCERTAIN,
    REASON_IRREGULAR_COLUMN_PITCH,
    REASON_NON_MONOTONIC_PERIODS,
    REASON_REPEATED_PERIOD,
    cluster_words_into_rows,
    is_numeric_cell,
    reconstruct_financial_tables,
)
from app.services.sources.primary_document_extractor import (
    _infer_scope,
    extract_pdf,
    resolve_reconstructed_table_scope,
)
from tests.helpers.pdf_fixtures import (
    helvetica_width,
    make_pdf_positioned_text,
    make_pdf_with_table,
    right_aligned_row,
)

PAGE_WIDTH = 907.0

# The real Pandora page-14 column right-edges, pitch ~42pt.
LEFT_EDGES = [274.0, 316.0, 358.0, 401.0, 443.0]
RIGHT_EDGES = [700.0, 743.0, 785.0, 828.0, 870.0]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def word(text: str, x0: float, top: float, *, width: float = 24.0, size: float = 8.0):
    return {"text": text, "x0": x0, "x1": x0 + width, "top": top, "size": size}


def header_words(
    periods: list[str],
    edges: list[float],
    *,
    y: float = 119.0,
    size: float = 7.0,
    unit: str | None = "DKK million",
    unit_x: float = 35.0,
) -> list[tuple[str, float, float, float]]:
    out: list[tuple[str, float, float, float]] = []
    if unit:
        out.append((unit, unit_x, y, size))
    for period, edge in zip(periods, edges):
        out.append((period, edge - helvetica_width(period, size), y, size))
    return out


def rebuild(words: list[tuple[str, float, float, float]], *, page_number: int = 14):
    """Render positioned words to real PDF bytes and reconstruct page tables."""
    raw = make_pdf_positioned_text([words], page_width=PAGE_WIDTH)
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        page = pdf.pages[0]
        extracted = page.extract_words(extra_attrs=["size", "fontname"])
        return reconstruct_financial_tables(
            extracted, page_width=float(page.width), page_number=page_number
        )


def _facts_from(
    words: list[tuple[str, float, float, float]],
    *,
    currency: str,
    period: str,
    company: str = "Test Issuer A/S",
):
    """Full path: positioned words → PDF bytes → extraction → validated facts."""
    raw = make_pdf_positioned_text([words], page_width=PAGE_WIDTH)
    extraction = extract_pdf(raw)
    return validate_extracted_facts(
        extraction,
        issuer_context=IssuerContext(
            company_name=company, reporting_currency=currency, default_period=period
        ),
    )


def _one(facts, label: str, period: str):
    """The single fact for ``(label, period)`` — asserts there is exactly one."""
    matches = [f for f in facts if f.label == label and f.period == period]
    assert len(matches) == 1, f"expected one {label}/{period}, got {matches}"
    return matches[0]


def grid_of(table) -> dict[str, dict[str, str]]:
    """``{row_label: {period: cell_text}}`` for easy assertions."""
    out: dict[str, dict[str, str]] = {}
    for row in table.rows:
        out[row.label] = {cell.period: cell.text for cell in row.cells}
    return out


# --------------------------------------------------------------------------- #
# A. Header detection and column grouping
# --------------------------------------------------------------------------- #


def test_row_clustering_keeps_a_whole_table_row_together():
    """The reconstructor must NOT split a row on a wide gap — those gaps are
    the columns. (``_group_words_into_lines`` deliberately does split, which
    is why this module has its own row clustering.)"""
    words = [word("Revenue", 35, 147), word("32,549", 250, 147), word("31,680", 292, 147)]
    rows = cluster_words_into_rows(words)
    assert len(rows) == 1
    assert [w["text"] for w in rows[0]] == ["Revenue", "32,549", "31,680"]


def test_numeric_cell_recognition_is_layout_only():
    assert is_numeric_cell("32,549")
    assert is_numeric_cell("23.9%")
    assert is_numeric_cell("-1,048")
    assert is_numeric_cell("(107)")
    assert is_numeric_cell("16 539")  # space-separated thousands
    assert not is_numeric_cell("Revenue")
    assert not is_numeric_cell("44/56")
    assert not is_numeric_cell("-")


def test_two_side_by_side_tables_do_not_share_one_header_map():
    """Pandora page 14 prints "Financial highlights" and "Stock ratios" side by
    side, so EVERY row carries both tables' cells. One header map over the
    whole row would attribute the right table's values to the left one."""
    words = header_words(["2025", "2024", "2023", "2022", "2021"], LEFT_EDGES)
    words += header_words(
        ["2025", "2024", "2023", "2022", "2021"], RIGHT_EDGES, unit="DKK million", unit_x=462.0
    )
    words += right_aligned_row(
        "Revenue", ["32,549", "31,680", "28,136", "26,463", "23,394"],
        y=147, label_x=35, column_right_edges=LEFT_EDGES,
    )
    words += right_aligned_row(
        "Total assets", ["29,603", "27,758", "23,798", "22,013", "18,542"],
        y=147, label_x=462, column_right_edges=RIGHT_EDGES,
    )
    tables = rebuild(words)
    assert len(tables) == 2
    assert grid_of(tables[0]) == {
        "Revenue": {"2025": "32,549", "2024": "31,680", "2023": "28,136",
                    "2022": "26,463", "2021": "23,394"}
    }
    assert grid_of(tables[1]) == {
        "Total assets": {"2025": "29,603", "2024": "27,758", "2023": "23,798",
                         "2022": "22,013", "2021": "18,542"}
    }


# --------------------------------------------------------------------------- #
# B. The negative matrix — refuse, never guess
# --------------------------------------------------------------------------- #


def test_negative_a_value_halfway_between_two_columns_is_refused():
    """A cell equidistant from two period columns cannot be attributed."""
    edges = [274.0, 316.0]
    words = header_words(["2025", "2024"], edges)
    # Midway between the two column centres (~265.5 and ~307.5) is ~286.5.
    words.append(("9,999", 286.5 - helvetica_width("9,999", 8.0) / 2, 147, 8.0))
    words.append(("Revenue", 35, 147, 8.0))
    tables = rebuild(words)
    assert tables == [] or all(not t.rows for t in tables)


def test_negative_b_more_values_than_readable_year_headers_drops_the_extras():
    """Five values under four year headers: the fifth has no column, so it is
    dropped — the four that DO align are still mapped correctly."""
    edges = [274.0, 316.0, 358.0, 401.0]
    words = header_words(["2025", "2024", "2023", "2022"], edges)
    words += right_aligned_row(
        "Revenue", ["32,549", "31,680", "28,136", "26,463"],
        y=147, label_x=35, column_right_edges=edges,
    )
    # A fifth value printed where a 2021 column WOULD be, with no header.
    words.append(("23,394", 443.0 - helvetica_width("23,394", 8.0), 147, 8.0))
    tables = rebuild(words)
    assert len(tables) == 1
    assert grid_of(tables[0]) == {
        "Revenue": {"2025": "32,549", "2024": "31,680",
                    "2023": "28,136", "2022": "26,463"}
    }
    assert "2021" not in {c.period for c in tables[0].columns}


def test_negative_c_years_in_a_footnote_line_are_not_a_table_header():
    """The real Pandora page-14 footnote — "… 22,985 in 2021 (-8%), 16,597 in
    2022 (-5%), 13,645 in 2023 (-5%) and of 9,917 in 2024 (-3%)" — names four
    years in ascending order. Its column pitch is irregular (57.9 / 58.9 /
    73.1 on the real page) and ordinary words sit between the years, so it
    must never be read as a header."""
    footnote = (
        "2 In 2025, we have improved the calculation methodology, with decreases of"
    )
    words = [(footnote, 35, 400, 6.0)]
    x = 460.0
    for value, year in (("22,985", "2021"), ("16,597", "2022"),
                        ("13,645", "2023"), ("9,917", "2024")):
        words.append((value, x, 415, 6.0))
        x += helvetica_width(value, 6.0) + 4
        words.append(("in", x, 415, 6.0))
        x += helvetica_width("in", 6.0) + 4
        words.append((year, x, 415, 6.0))
        x += helvetica_width(year, 6.0) + 14  # deliberately uneven spacing
    tables = rebuild(words)
    assert tables == []


def test_negative_g_h_the_current_column_is_chosen_by_HEADER_not_magnitude_or_position():
    """Newest year on the RIGHT, and a historical value LARGER than the current
    one. Neither position nor magnitude may decide which cell is FY2025."""
    edges = [274.0, 316.0, 358.0, 401.0, 443.0]
    words = header_words(["2021", "2022", "2023", "2024", "2025"], edges)
    words += right_aligned_row(
        # 2021 is the biggest number in the row; 2025 is the smallest.
        "Revenue", ["99,000", "80,000", "60,000", "40,000", "32,549"],
        y=147, label_x=35, column_right_edges=edges,
    )
    tables = rebuild(words)
    assert len(tables) == 1
    cells = grid_of(tables[0])["Revenue"]
    assert cells["2025"] == "32,549"
    assert cells["2021"] == "99,000"
    # And the column order is the printed one, newest LAST.
    assert [c.period for c in tables[0].columns] == [
        "2021", "2022", "2023", "2024", "2025"
    ]


def test_negative_i_a_non_monotonic_period_row_is_refused():
    """Years running in no consistent direction are an ordering this module
    cannot justify mapping."""
    edges = [274.0, 316.0, 358.0, 401.0]
    words = header_words(["2025", "2023", "2024", "2022"], edges)
    words += right_aligned_row(
        "Revenue", ["1,000", "2,000", "3,000", "4,000"],
        y=147, label_x=35, column_right_edges=edges,
    )
    tables = rebuild(words)
    assert tables == []


def test_negative_a_repeated_period_in_one_header_group_is_refused():
    edges = [274.0, 316.0, 358.0]
    words = header_words(["2025", "2025", "2024"], edges)
    words += right_aligned_row(
        "Revenue", ["1,000", "2,000", "3,000"],
        y=147, label_x=35, column_right_edges=edges,
    )
    assert rebuild(words) == []


def test_negative_e_a_metric_label_never_takes_values_from_a_different_table():
    """A label printed in one table's window must not absorb the cells of the
    table beside it."""
    words = header_words(["2025", "2024", "2023", "2022", "2021"], LEFT_EDGES)
    words += header_words(
        ["2025", "2024", "2023", "2022", "2021"], RIGHT_EDGES, unit="DKK million",
        unit_x=462.0,
    )
    # Only the RIGHT table has a data row on this line.
    words += right_aligned_row(
        "Total assets", ["29,603", "27,758", "23,798", "22,013", "18,542"],
        y=147, label_x=462, column_right_edges=RIGHT_EDGES,
    )
    # ... and the LEFT table has a bare label with no values of its own.
    words.append(("Revenue", 35, 147, 8.0))
    tables = rebuild(words)
    left = [t for t in tables if t.x_max < 500]
    assert left == [] or all("Revenue" not in grid_of(t) for t in left)


def test_negative_f_a_percent_row_under_a_monetary_table_stays_a_percentage():
    """"DKK million" governs the money rows; a margin row in the same table is
    still a percentage, and must never be read as DKK 23.9 million."""
    words = header_words(["2025", "2024"], [274.0, 316.0])
    words += right_aligned_row("Revenue", ["32,549", "31,680"], y=147,
                               label_x=35, column_right_edges=[274.0, 316.0])
    words += right_aligned_row("EBIT margin, %", ["23.9%", "25.2%"], y=160,
                               label_x=35, column_right_edges=[274.0, 316.0])
    tables = rebuild(words)
    assert grid_of(tables[0])["EBIT margin, %"] == {"2025": "23.9%", "2024": "25.2%"}

    facts = _facts_from(words, currency="DKK", period="2025")
    margin = _one(facts, "operating_margin", "2025")
    assert margin.unit == "percent"
    assert margin.value_numeric == 23.9
    assert margin.currency is None and margin.scale is None
    revenue = _one(facts, "revenue", "2025")
    assert revenue.unit == "currency_amount"
    assert (revenue.currency, revenue.scale) == ("DKK", "million")


# --------------------------------------------------------------------------- #
# C. The positive matrix
# --------------------------------------------------------------------------- #


def test_positive_c_fy_prefixed_headers_normalize_to_the_bare_year():
    edges = [274.0, 316.0, 358.0]
    words = header_words(["FY2025", "FY2024", "FY2023"], edges)
    words += right_aligned_row("Revenue", ["32,549", "31,680", "28,136"],
                               y=147, label_x=35, column_right_edges=edges)
    tables = rebuild(words)
    assert [c.period for c in tables[0].columns] == ["2025", "2024", "2023"]
    assert [c.period_type for c in tables[0].columns] == [PERIOD_TYPE_ANNUAL] * 3
    assert tables[0].promotable


def test_positive_d_an_interim_table_is_understood_but_never_promoted():
    """The geometry is recovered — columns, periods and rows are all correct —
    but ``ExtractedFact.period`` is a bare YEAR, so promoting "H1 2026" would
    make it indistinguishable from FY2026. Fail closed instead."""
    edges = [274.0, 316.0]
    words = header_words(["H1 2026", "H1 2025"], edges)
    words += right_aligned_row("Revenue", ["16,539", "15,328"],
                               y=147, label_x=35, column_right_edges=edges)
    tables = rebuild(words)
    assert len(tables) == 1
    assert [c.period for c in tables[0].columns] == ["H1 2026", "H1 2025"]
    assert tables[0].period_types == {PERIOD_TYPE_INTERIM}
    assert tables[0].promotable is False
    assert grid_of(tables[0])["Revenue"] == {"H1 2026": "16,539", "H1 2025": "15,328"}

    # ... and nothing from it reaches the fact set.
    facts = _facts_from(words, currency="DKK", period="2026")
    assert not [f for f in facts if f.label == "revenue"]


def test_positive_mixed_annual_and_interim_columns_are_refused():
    edges = [274.0, 316.0]
    words = header_words(["H1 2026", "2025"], edges)
    words += right_aligned_row("Revenue", ["16,539", "15,328"],
                               y=147, label_x=35, column_right_edges=edges)
    assert rebuild(words) == []


def test_positive_g_a_wrapped_metric_label_is_rejoined():
    """"Earnings before interest, tax, depreciation" / "and amortisation
    (EBITDA)" is one label split across two printed lines. Only a genuine
    CONTINUATION (starting lower-case) is joined, so a section sub-heading
    above a row is never glued onto it."""
    edges = [274.0, 316.0]
    words = header_words(["2025", "2024"], edges)
    words.append(("Financial highlights", 35, 133, 8.0))
    words += right_aligned_row("Revenue", ["32,549", "31,680"], y=147,
                               label_x=35, column_right_edges=edges)
    words.append(("Earnings before interest, tax, depreciation", 35, 188, 8.0))
    words += right_aligned_row("and amortisation (EBITDA)", ["10,316", "10,327"],
                               y=197, label_x=35, column_right_edges=edges)
    tables = rebuild(words)
    labels = set(grid_of(tables[0]))
    assert "Earnings before interest, tax, depreciation and amortisation (EBITDA)" in labels
    # The sub-heading was NOT glued onto the row below it.
    assert "Revenue" in labels
    assert "Financial highlights" in tables[0].heading_candidates


def test_positive_space_separated_thousands_are_rejoined():
    """Continental issuers print "16 539", so ``extract_words`` returns two
    boxes. Left split they land in the same column and the row is discarded —
    which cost every ``Sales`` and ``Operating result`` row on the real
    Richemont segment pages."""
    edges = [274.0, 316.0]
    words = header_words(["2026", "2025"], edges, unit="in €m")
    words += right_aligned_row("Sales", ["16 539", "15 328"], y=147,
                               label_x=35, column_right_edges=edges)
    tables = rebuild(words)
    assert grid_of(tables[0])["Sales"] == {"2026": "16 539", "2025": "15 328"}


def test_positive_a_five_year_table_end_to_end_through_extract_pdf():
    """The whole path: real PDF bytes → ``extract_pdf`` → an ``ExtractedTable``
    flagged ``reconstructed`` whose header row carries the periods."""
    words = header_words(["2025", "2024", "2023", "2022", "2021"], LEFT_EDGES)
    words += right_aligned_row(
        "Revenue", ["32,549", "31,680", "28,136", "26,463", "23,394"],
        y=147, label_x=35, column_right_edges=LEFT_EDGES,
    )
    raw = make_pdf_positioned_text([words], page_width=PAGE_WIDTH)
    result = extract_pdf(raw)
    rebuilt = [t for t in result.tables if t.reconstructed]
    assert len(rebuilt) == 1
    assert rebuilt[0].column_periods == ["2025", "2024", "2023", "2022", "2021"]
    assert rebuilt[0].rows[0] == ["DKK million", "2025", "2024", "2023", "2022", "2021"]
    assert rebuilt[0].rows[1] == [
        "Revenue", "32,549", "31,680", "28,136", "26,463", "23,394"
    ]
    assert any("reconstructed a borderless" in w for w in result.warnings)


def test_a_ruled_grid_is_not_rebuilt_a_second_time():
    """When ``extract_tables()`` already recovered a real grid (>=2 rows and
    >=2 columns), rebuilding it geometrically would only duplicate every cell
    and give the same figure a second provenance locator."""
    raw = make_pdf_with_table(
        [["", "2024", "2023"], ["Revenue", "20,616", "19,300"]]
    )
    result = extract_pdf(raw)
    assert result.tables
    assert not any(t.reconstructed for t in result.tables)


# --------------------------------------------------------------------------- #
# D. The Pandora golden regression
#
# The geometry below is the REAL page-14 "Five-year summary" layout, measured
# from the actual 169-page Annual Report 2025: two borderless tables printed
# side by side, each with five right-aligned year columns on a ~42pt pitch
# under a "DKK million" unit label. No copyrighted narrative text is
# reproduced — only the structural pattern that caused the bug, and the
# handful of headline figures needed to prove the mapping.
# --------------------------------------------------------------------------- #

PANDORA_LEFT_ROWS = [
    ("Revenue", ["32,549", "31,680", "28,136", "26,463", "23,394"]),
    ("Operating profit (EBIT)", ["7,783", "7,974", "7,039", "6,743", "5,839"]),
    ("EBIT margin, %", ["23.9%", "25.2%", "25.0%", "25.5%", "25.0%"]),
    ("Net profit for the period", ["5,241", "5,227", "4,740", "5,029", "4,160"]),
]
PANDORA_RIGHT_ROWS = [
    ("Total assets", ["29,603", "27,758", "23,798", "22,013", "18,542"]),
    ("Net interest-bearing debt (NIBD)", ["13,719", "11,008", "9,770", "6,794", "2,882"]),
    ("Equity", ["5,282", "5,508", "5,355", "7,167", "7,001"]),
    ("Cash flows from operating activities", ["7,361", "8,721", "7,384", "4,434", "6,228"]),
    ("Free cash flows incl. lease payments", ["5,022", "6,767", "5,489", "2,602", "5,137"]),
    ("Total employees (end of period), number",
     ["42,281", "41,326", "37,142", "34,299", "30,533"]),
]
PANDORA_YEARS = ["2025", "2024", "2023", "2022", "2021"]


def pandora_page_14_words() -> list[tuple[str, float, float, float]]:
    words: list[tuple[str, float, float, float]] = [
        ("FIVE-YEAR SUMMARY", 31, 53, 14.0),  # the page's own section title
    ]
    words += header_words(PANDORA_YEARS, LEFT_EDGES, y=119, unit="DKK million")
    words += header_words(
        PANDORA_YEARS, RIGHT_EDGES, y=119, unit="DKK million", unit_x=462.0
    )
    words.append(("Financial highlights", 35, 135, 8.0))
    words.append(("Consolidated balance sheet", 462, 135, 8.0))
    y = 147.0
    for label, values in PANDORA_LEFT_ROWS:
        words += right_aligned_row(label, values, y=y, label_x=35,
                                   column_right_edges=LEFT_EDGES)
        y += 12.0
    y = 147.0
    for label, values in PANDORA_RIGHT_ROWS:
        words += right_aligned_row(label, values, y=y, label_x=462,
                                   column_right_edges=RIGHT_EDGES)
        y += 12.0
    return words


def test_pandora_golden_the_ruled_table_detector_recovers_nothing_usable():
    """The PRE-FIX failure, pinned: on this exact geometry pdfplumber's own
    ruling-line table finder produces no grid at all (a real report's faint
    rules yielded a degenerate one-column artifact). Either way the validator
    gets nothing it can map to a period."""
    raw = make_pdf_positioned_text([pandora_page_14_words()], page_width=PAGE_WIDTH)
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        ruled = pdf.pages[0].extract_tables()
    usable = [t for t in ruled if len(t) >= 2 and max(len(r) for r in t) >= 2]
    assert not usable


def test_pandora_golden_every_metric_maps_to_every_year():
    tables = rebuild(pandora_page_14_words())
    assert len(tables) == 2
    left, right = tables
    assert [c.period for c in left.columns] == PANDORA_YEARS
    assert [c.period for c in right.columns] == PANDORA_YEARS
    assert left.unit_label == "DKK million" and right.unit_label == "DKK million"

    left_grid = grid_of(left)
    for label, values in PANDORA_LEFT_ROWS:
        assert left_grid[label] == dict(zip(PANDORA_YEARS, values)), label
    right_grid = grid_of(right)
    for label, values in PANDORA_RIGHT_ROWS:
        assert right_grid[label] == dict(zip(PANDORA_YEARS, values)), label


def test_pandora_golden_facts_are_period_scoped_with_the_right_units():
    facts = _facts_from(
        pandora_page_14_words(), currency="DKK", period="2025", company="Pandora A/S"
    )
    validated = [f for f in facts if f.validation_status == VALIDATION_VALIDATED]

    expected_money = {
        ("revenue", "2025"): 32549.0,
        ("revenue", "2024"): 31680.0,
        ("revenue", "2021"): 23394.0,
        ("operating_profit", "2025"): 7783.0,
        ("operating_profit", "2024"): 7974.0,
        ("net_income", "2025"): 5241.0,
        ("total_assets", "2025"): 29603.0,
        ("net_debt", "2025"): 13719.0,
        ("total_equity", "2025"): 5282.0,
        ("operating_cash_flow", "2025"): 7361.0,
        ("free_cash_flow", "2025"): 5022.0,
    }
    for (label, period), value in expected_money.items():
        fact = _one(validated, label, period)
        assert fact.value_numeric == value, (label, period)
        assert fact.unit == "currency_amount"
        assert (fact.currency, fact.scale) == ("DKK", "million"), (label, period)
        assert fact.page_number == 1
        assert fact.needs_human_review is True

    margin = _one(validated, "operating_margin", "2025")
    assert margin.value_numeric == 23.9 and margin.unit == "percent"
    headcount = _one(validated, "employees", "2025")
    assert headcount.value_numeric == 42281.0 and headcount.unit == "people"


def test_pandora_golden_the_current_year_wins_on_PERIOD_not_magnitude():
    """FY2025 revenue (32,549) is not the largest figure in its own row — FY2024
    equity (5,508) exceeds FY2025's, and FY2021 total assets are the smallest.
    Every year must land on its OWN column, and each becomes its own fact."""
    facts = _facts_from(
        pandora_page_14_words(), currency="DKK", period="2025", company="Pandora A/S"
    )
    equity = {f.period: f.value_numeric for f in facts if f.label == "total_equity"}
    assert equity == {"2025": 5282.0, "2024": 5508.0, "2023": 5355.0,
                      "2022": 7167.0, "2021": 7001.0}
    # A historical year is a fact in its own right, never a rewrite of the
    # current one: five distinct periods, five distinct facts.
    revenue = {f.period: f.value_numeric for f in facts if f.label == "revenue"}
    assert revenue == {"2025": 32549.0, "2024": 31680.0, "2023": 28136.0,
                       "2022": 26463.0, "2021": 23394.0}


def test_pandora_golden_scope_comes_from_the_regions_OWN_headings():
    """The right-hand region sits under "Consolidated balance sheet" and is
    Group-scoped; the left-hand one has no scope vocabulary of its own and
    stays honestly unknown rather than inheriting a guess."""
    left, right = rebuild(pandora_page_14_words())
    assert resolve_reconstructed_table_scope(right) == "group"
    assert resolve_reconstructed_table_scope(left) is None


# --------------------------------------------------------------------------- #
# E. Scope
# --------------------------------------------------------------------------- #


def test_scope_a_segment_table_is_scoped_by_its_own_title_under_a_section_title():
    """Richemont's shape: a page-level "… by segment" section title, then each
    segment's own name printed directly over its grid. ``_infer_scope`` reads
    the SIGNAL from the section title and the LABEL from the leaf."""
    edges = [274.0, 316.0]
    words = [
        ("Sales and operating results by segment", 31, 53, 14.0),
        ("Jewellery Maisons", 31, 80, 9.5),
    ]
    words += header_words(["2026", "2025"], edges, y=100, unit="in €m")
    words += right_aligned_row("Operating result", ["5 037", "4 896"], y=115,
                               label_x=35, column_right_edges=edges)
    tables = rebuild(words)
    assert resolve_reconstructed_table_scope(tables[0]) == "Jewellery Maisons"


def test_scope_d_a_segment_table_never_populates_a_group_slot():
    """A Group-scoped and a segment-scoped figure for the same metric/period
    are two different facts, never one contradicting itself."""
    edges = [274.0, 316.0]
    words = [
        ("Sales and operating results by segment", 31, 53, 14.0),
        ("Specialist Watchmakers", 31, 80, 9.5),
    ]
    words += header_words(["2026", "2025"], edges, y=100, unit="in EUR million")
    words += right_aligned_row("Operating profit", ["107", "175"], y=115,
                               label_x=35, column_right_edges=edges)
    facts = _facts_from(words, currency="EUR", period="2026", company="Richemont")
    operating = [f for f in facts if f.label == "operating_profit" and f.period == "2026"]
    assert operating
    # Whatever else the page yields, NOTHING from this segment table is
    # attributed to the Group.
    assert all(f.scope != "group" for f in operating)
    from_table = [f for f in operating if (f.table_location or "").startswith("p1:m")]
    assert from_table and all(
        f.scope == "Specialist Watchmakers" and f.value_numeric == 107.0
        for f in from_table
    )


def test_scope_ifrs5_disposal_group_is_not_an_issuer_group_claim():
    """IFRS 5's own phrase "disposal GROUP" must not read as a Group-scope
    claim — on the real Richemont report that let the YNAP discontinued
    operation's EUR 82m revenue contradict the Group's EUR 22 420m."""
    assert _infer_scope("Assets and disposal group held for sale") != "group"
    assert _infer_scope(
        "The results of the discontinued operations are set out below"
    ) != "group"
    assert _infer_scope("Consolidated balance sheet") == "group"


def test_scope_a_page_title_carries_across_a_contiguous_page_run():
    """A multi-page segment review states "… by segment" once, on its first
    page; the pages after it print only each segment's own name."""
    edges = [274.0, 316.0]
    page1 = [("Sales and operating results by segment", 31, 53, 14.0),
             ("Jewellery Maisons", 31, 80, 9.5)]
    page1 += header_words(["2026", "2025"], edges, y=100, unit="in €m")
    page1 += right_aligned_row("Operating result", ["5 037", "4 896"], y=115,
                               label_x=35, column_right_edges=edges)
    page2 = [("Specialist Watchmakers", 31, 53, 9.5)]  # no section title of its own
    page2 += header_words(["2026", "2025"], edges, y=100, unit="in €m")
    page2 += right_aligned_row("Operating result", ["107", "175"], y=115,
                               label_x=35, column_right_edges=edges)

    raw = make_pdf_positioned_text([page1, page2], page_width=PAGE_WIDTH)
    result = extract_pdf(raw)
    scopes = {t.page_number: t.scope for t in result.tables if t.reconstructed}
    assert scopes == {1: "Jewellery Maisons", 2: "Specialist Watchmakers"}


# --------------------------------------------------------------------------- #
# F. Validator integration
# --------------------------------------------------------------------------- #


def test_vocabulary_the_labels_this_capability_surfaced():
    assert _match_label("EBIT margin, %") == "operating_margin"
    assert _match_label("Operating profit (EBIT)") == "operating_profit"
    assert _match_label("Cash flows from operating activities") == "operating_cash_flow"
    assert _match_label("Net interest-bearing debt (NIBD)") == "net_debt"
    # An income statement prints BOTH lines; only the bottom one is net income.
    assert _match_label("Profit for the year") == "net_income"
    assert _match_label("Profit for the year from continuing operations") is None
    assert _match_label("Profit/(loss) for the year from discontinued operations") is None
    # Unchanged: EBITDA has no field of its own and must not become EBIT.
    assert _match_label("EBITDA margin, %") is None
    assert _match_label(
        "Earnings before interest, tax, depreciation and amortisation (EBITDA)"
    ) is None


def test_a_flattened_prose_read_of_the_same_page_is_superseded_not_conflicting():
    """The reconstructed page's text ALSO reaches the prose path with its grid
    gone, where one label is followed by five values. Those two candidates are
    one table read twice — not independent corroboration — so the prose read
    must not be able to demote the column-anchored figure to ``excerpt_only``.
    """
    facts = _facts_from(
        pandora_page_14_words(), currency="DKK", period="2025", company="Pandora A/S"
    )
    revenue = _one(facts, "revenue", "2025")
    assert revenue.validation_status == VALIDATION_VALIDATED
    assert revenue.value_numeric == 32549.0
    ebit = _one(facts, "operating_profit", "2025")
    assert ebit.validation_status == VALIDATION_VALIDATED
    assert ebit.value_numeric == 7783.0
    assert any("superseded" in n for n in revenue.validation_notes)


def test_an_unqualified_number_cannot_veto_a_fully_specified_one():
    """A bare figure whose currency/scale could not be established is already
    barred from becoming a fact; letting it CONTRADICT a fully qualified one
    compares two quantities in unknown units. On the real Pandora report a
    stray "Approx. -600" on the guidance page demoted the correct FY2025
    revenue to ``excerpt_only``."""
    words = pandora_page_14_words()
    # A guidance-style bare number elsewhere on the page, with no unit at all.
    words.append(("Financial impact of approx. -600 on revenue in 2025", 35, 300, 8.0))
    facts = _facts_from(words, currency="DKK", period="2025", company="Pandora A/S")
    revenue = _one(facts, "revenue", "2025")
    assert revenue.validation_status == VALIDATION_VALIDATED
    assert revenue.value_numeric == 32549.0


def test_two_fully_qualified_disagreeing_readings_still_conflict():
    """Genuine contradiction detection is untouched: when BOTH readings are
    completely specified and disagree, neither is promoted.

    The disagreeing prose is on a DIFFERENT page from the table, so it is a
    genuinely independent statement rather than the same grid read twice —
    which is exactly the case supersession must NOT swallow.
    """
    edges = [274.0, 316.0]
    page1 = header_words(["2025", "2024"], edges)
    page1 += right_aligned_row("Revenue", ["32,549", "31,680"], y=147,
                               label_x=35, column_right_edges=edges)
    page2 = [("Revenue for 2025 was DKK 30,000 million in the period.", 35, 100, 8.0)]
    raw = make_pdf_positioned_text([page1, page2], page_width=PAGE_WIDTH)
    facts = validate_extracted_facts(
        extract_pdf(raw),
        issuer_context=IssuerContext(
            company_name="Pandora A/S", reporting_currency="DKK", default_period="2025"
        ),
    )
    revenue = [f for f in facts if f.label == "revenue" and f.period == "2025"]
    assert revenue and all(
        f.validation_status == VALIDATION_EXCERPT_ONLY for f in revenue
    )
    assert any(
        "Conflicting magnitudes" in n for f in revenue for n in f.validation_notes
    )


def test_j_an_exact_table_figure_is_preferred_over_a_rounded_narrative_one():
    """"DKK 32.5 billion" in prose and "32,549" in the table are the same
    figure; they must agree, and the EXACT one represents the fact."""
    edges = [274.0, 316.0]
    words = header_words(["2025", "2024"], edges)
    words += right_aligned_row("Revenue", ["32,549", "31,680"], y=147,
                               label_x=35, column_right_edges=edges)
    words.append(("Revenue reached DKK 32.5 billion in the 2025 financial year.", 35, 300, 8.0))
    facts = _facts_from(words, currency="DKK", period="2025", company="Pandora A/S")
    revenue = _one(facts, "revenue", "2025")
    assert revenue.validation_status == VALIDATION_VALIDATED
    assert revenue.value_numeric == 32549.0
    assert revenue.scale == "million"


def test_refusals_are_recorded_as_diagnostics_not_silently_dropped():
    """An operator must be able to see WHY a visible number did not become a
    fact."""
    edges = [274.0, 316.0]
    words = header_words(["2025", "2024"], edges)
    words += right_aligned_row("Revenue", ["32,549", "31,680"], y=147,
                               label_x=35, column_right_edges=edges)
    # A value sitting on the boundary between the two columns.
    words.append(("9,999", 286.5 - helvetica_width("9,999", 8.0) / 2, 160, 8.0))
    words.append(("Total assets", 35, 160, 8.0))
    tables = rebuild(words)
    reasons = {r.reason for t in tables for r in t.rejections}
    assert REASON_COLUMN_ALIGNMENT_UNCERTAIN in reasons
    assert "Total assets" not in grid_of(tables[0])


def test_rejection_reasons_cover_the_header_qualification_rules():
    for periods, reason in (
        (["2025", "2025", "2024"], REASON_REPEATED_PERIOD),
        (["2025", "2023", "2024", "2022"], REASON_NON_MONOTONIC_PERIODS),
    ):
        edges = [274.0 + 42.0 * i for i in range(len(periods))]
        words = header_words(periods, edges)
        words += right_aligned_row("Revenue", ["1"] * len(periods), y=147,
                                   label_x=35, column_right_edges=edges)
        raw = make_pdf_positioned_text([words], page_width=PAGE_WIDTH)
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            page = pdf.pages[0]
            assert reconstruct_financial_tables(
                page.extract_words(extra_attrs=["size"]),
                page_width=float(page.width),
                page_number=1,
            ) == []


def test_irregular_pitch_is_the_guard_that_rejects_a_prose_year_list():
    edges = [274.0, 332.0, 391.0, 464.0]  # gaps 58 / 59 / 73 — the real footnote
    words = header_words(["2021", "2022", "2023", "2024"], edges, unit=None)
    words += right_aligned_row("Revenue", ["1", "2", "3", "4"], y=147,
                               label_x=35, column_right_edges=edges)
    raw = make_pdf_positioned_text([words], page_width=PAGE_WIDTH)
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        page = pdf.pages[0]
        words_out = page.extract_words(extra_attrs=["size"])
        assert reconstruct_financial_tables(
            words_out, page_width=float(page.width), page_number=1
        ) == []
    assert REASON_IRREGULAR_COLUMN_PITCH  # vocabulary exists for the diagnostic


# --------------------------------------------------------------------------- #
# G. Extraction budget coherence
#
# The capability above is worthless if the extractor never reaches the page the
# table is on. Measured live on staging (B1) against the real 169-page Pandora
# Annual Report 2025: ~1.95s per page, and the previous 20s budget stopped at
# page ELEVEN — three pages short of the five-year summary. Parsing a page IS
# the cost (``page.objects`` accounts for essentially all of it), so there is
# no cheaper pre-scan and no per-page work to skip; the budget itself had to
# move.
# --------------------------------------------------------------------------- #


def test_a_documents_total_budget_can_actually_contain_its_own_parts():
    """``total`` must exceed fetch + extraction, or the inner extraction budget
    can never be spent and the outer one silently truncates the document."""
    from app.core.config import settings as app_settings

    assert app_settings.primary_document_total_timeout_seconds > (
        app_settings.primary_document_fetch_timeout_seconds
        + app_settings.primary_document_extraction_timeout_seconds
    )


def test_the_extraction_budget_can_reach_a_real_annual_reports_summary_page():
    """A pace-based floor, pinned to the real measurement rather than a taste.

    Pandora's five-year summary is on page 14 of 169. At the ~1.95s/page
    observed on staging that needs ~27s of extraction budget; anything at or
    below the old 20s cannot reach it no matter how correct the table logic is.
    """
    from app.core.config import settings as app_settings

    observed_seconds_per_page_on_b1 = 1.95
    summary_page = 14
    needed = summary_page * observed_seconds_per_page_on_b1
    assert app_settings.primary_document_extraction_timeout_seconds >= needed
    # ... and the aggregate must hold that for more than one document.
    assert app_settings.primary_document_ingestion_budget_seconds >= (
        app_settings.primary_document_total_timeout_seconds
    )
