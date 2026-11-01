"""Cross-sector QA campaign 2026-11 — a table's unit is never an inline amount's scale.

Live Richemont deep report ``f722d620``:
- Its "Key financial data" table prints the unit beside each figure (``€ 8 496 m``).
- Its cash-flow statement prints ``€m €m`` under the years.

The layout reconstructor dropped both units. The page fallback then took the first scale
token in the page's prose: the "billion" of "sales reached € 22.4 billion". The report
showed net cash "8,496 bn EUR" and operating cash flow "4,880 bn EUR", a thousand times
the truth.

Two generic rules, no issuer special-cased:
1. A unit printed among the VALUES (beside the figures, or in a units row under the
   periods) is the table's unit, when every row that prints one agrees.
2. Page prose may only DECLARE a unit ("in € millions", "€m", "$'000"). The scale of one
   printed amount ("€ 22.4 billion") says nothing about a table.
"""

from __future__ import annotations

import io

import pdfplumber

from app.core.config import Settings
from app.services.sources.document_period import UNKNOWN_DOCUMENT_PERIOD
from app.services.sources.extracted_fact_validator import (
    IssuerContext,
    _declared_scale,
    validate_extracted_facts,
)
from app.services.sources.financial_table_reconstructor import reconstruct_financial_tables
from app.services.sources.primary_document_extractor import (
    ExtractedTable,
    PrimaryDocumentExcerpt,
    PrimaryDocumentExtraction,
)
from tests.helpers.pdf_fixtures import helvetica_width, make_pdf_positioned_text

CFG = Settings()
PAGE_WIDTH = 907.0
EDGES = [274.0, 340.0]

PROSE = (
    "Group sales reached € 22.4 billion, up 5%, and operating profit came in at "
    "€ 4.5 billion. Profit for the year was up by 27% to € 3.5 billion."
)


# ── 2. Page prose: declared units only ───────────────────────────────────── #


def _validated(rows: list[list[str]], prose: str) -> dict[str, list]:
    extraction = PrimaryDocumentExtraction(
        content_hash="y" * 64, mime_type="application/pdf", extraction_method="pdf_text",
        status="extracted",
        tables=[ExtractedTable(table_location="p1t0", table_index=0, page_number=1,
                               rows=rows, row_count=len(rows), col_count=len(rows[0]),
                               extraction_method="pdf_text")],
        excerpts=[PrimaryDocumentExcerpt(
            excerpt_id="P1", page_number=1, extraction_method="pdf_text",
            confidence=0.9, text=prose)],
    )
    out: dict[str, list] = {}
    for f in validate_extracted_facts(
        extraction, issuer_context=IssuerContext(company_name="TEST LUXURY SA",
                                                 reporting_currency="EUR"),
        cfg=CFG, document_period=UNKNOWN_DOCUMENT_PERIOD,
    ):
        out.setdefault(f.label, []).append(f)
    return out


STATEMENT = [["", "2026", "2025"],
             ["Revenue", "22 420", "21 399"],
             ["Net cash generated from operating activities", "4 880", "4 443"]]


def test_an_inline_prose_amount_never_scales_a_table() -> None:
    facts = _validated(STATEMENT, PROSE)
    # The prose sentence's own fact keeps its own scale; the TABLE's figures get none.
    table = [f for f in facts["revenue"] + facts.get("operating_cash_flow", [])
             if f.value_numeric in (22420.0, 21399.0, 4880.0, 4443.0)]
    assert table
    for f in table:
        assert f.scale is None
        assert f.validation_status != "validated"


def test_a_declared_unit_on_the_page_still_scales_a_table() -> None:
    facts = _validated(STATEMENT, "Consolidated statement of cash flows (in € millions).")
    assert facts["revenue"] and all(f.scale == "million" for f in facts["revenue"])


def test_declared_and_inline_scales_are_told_apart() -> None:
    assert _declared_scale("sales reached € 22.4 billion") is None
    assert _declared_scale("rose by 3% to € 8 496 million at 31 March") is None
    assert _declared_scale("Net cash € 8 496 m") is None
    assert _declared_scale("€m   €m") == "million"
    assert _declared_scale("(in millions of US dollars)") == "million"
    assert _declared_scale("US$'000") == "thousand"
    assert _declared_scale("Amounts in EUR million unless otherwise stated") == "million"


# ── 1. Units printed among the values ────────────────────────────────────── #


def _right(text: str, edge: float, y: float, size: float = 8.0):
    return (text, edge - helvetica_width(text, size), y, size)


def _header(y: float = 119.0) -> list[tuple[str, float, float, float]]:
    return [_right("2026", EDGES[0], y, 7.0), _right("2025", EDGES[1], y, 7.0)]


def _row(label: str, values: list[str], y: float, *, before: str | None = None,
         after: str | None = None) -> list[tuple[str, float, float, float]]:
    out = [(label, 35.0, y, 8.0)]
    for value, edge in zip(values, EDGES):
        out.append(_right(value, edge, y))
        if before:
            out.append((before, edge - helvetica_width(value, 8.0) - 22.0, y, 8.0))
        if after:
            out.append((after, edge + 3.0, y, 8.0))
    return out


def _rebuild(words: list[tuple[str, float, float, float]]):
    raw = make_pdf_positioned_text([words], page_width=PAGE_WIDTH)
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        page = pdf.pages[0]
        extracted = page.extract_words(extra_attrs=["size", "fontname"])
        return reconstruct_financial_tables(
            extracted, page_width=float(page.width), page_number=1
        )


def test_a_currency_and_scale_printed_beside_each_figure_is_the_unit() -> None:
    words = _header()
    words += _row("Revenue", ["22 420", "21 399"], 135, before="CHF", after="m")
    words += _row("Net cash position", ["8 496", "8 257"], 149, before="CHF", after="m")
    tables = _rebuild(words)
    assert tables and tables[0].unit_label == "CHF million"
    assert tables[0].to_grid()[0][0] == "CHF million"


def test_a_units_row_under_the_periods_is_the_unit() -> None:
    words = _header()
    words += [_right("$m", EDGES[0], 129, 7.0), _right("$m", EDGES[1], 129, 7.0)]
    words += _row("Revenue", ["22 420", "21 399"], 143)
    words += _row("Operating profit", ["4 492", "4 467"], 157)
    tables = _rebuild(words)
    assert tables and tables[0].unit_label == "$ million"


def test_a_lone_m_without_a_currency_is_not_a_unit() -> None:
    # "m" may be metres: a drilling table is not in millions.
    words = _header()
    words += _row("Drilled depth", ["1 240", "980"], 135, after="m")
    words += _row("Revenue", ["22 420", "21 399"], 149)
    tables = _rebuild(words)
    assert tables and tables[0].unit_label is None


def test_disagreeing_value_units_give_no_unit() -> None:
    words = _header()
    words += _row("Revenue", ["22 420", "21 399"], 135, before="CHF", after="m")
    words += _row("Net cash position", ["8.5", "8.3"], 149, before="CHF", after="bn")
    tables = _rebuild(words)
    assert tables and tables[0].unit_label is None


def test_a_header_unit_still_wins() -> None:
    words = [("DKK million", 35.0, 119.0, 7.0)] + _header()
    words += _row("Revenue", ["22 420", "21 399"], 135, before="CHF", after="bn")
    tables = _rebuild(words)
    assert tables and tables[0].unit_label == "DKK million"


# ── A justified two-column page with a narrow gutter ────────────────────── #

from app.services.sources.primary_document_extractor import (  # noqa: E402
    _reconstruct_two_column_text,
)

A4 = 595.32


def _line(words: list[str], x0: float, x1: float, top: float) -> list[dict]:
    """Words spread evenly (justified) between x0 and x1 on one line."""
    step = (x1 - x0) / len(words)
    return [
        {"text": w, "x0": x0 + i * step, "x1": x0 + (i + 1) * step - (0 if i == len(words) - 1 else 2.0),
         "top": top}
        for i, w in enumerate(words)
    ]


def _two_columns(n: int, *, left_x1: float, long_left_x1: float, right_x0: float) -> list[dict]:
    """``n`` rows of two justified columns. The last left line is a paragraph line that
    runs further right (to ``long_left_x1``) and has no right-hand neighbour."""
    words: list[dict] = []
    for i in range(n):
        top = 100.0 + i * 11.0
        words += _line([f"L{i}a", f"L{i}b", f"L{i}c", f"L{i}d"], 57.0, left_x1, top)
        words += _line([f"R{i}a", f"R{i}b", f"R{i}c", f"R{i}d"], right_x0, 539.0, top)
    words += _line(["Lzz", "Lzy", "Lzx", "Lzw"], 57.0, long_left_x1, 100.0 + n * 11.0)
    return words


def test_a_narrow_gutter_between_two_text_columns_is_still_a_gutter() -> None:
    # Richemont, page 2: left lines end at ~284pt, the longest at 293.4pt; the right
    # column starts at 297.6pt — a 4.3pt gutter.
    words = _two_columns(12, left_x1=284.0, long_left_x1=293.4, right_x0=297.7)
    text = _reconstruct_two_column_text(words, A4)
    assert text is not None
    assert text.index("L11d") < text.index("R0a")  # the whole left column first
    assert "L0d R0a" not in text  # never one line across both columns


def test_a_narrow_gap_with_few_lines_is_not_trusted() -> None:
    words = _two_columns(4, left_x1=284.0, long_left_x1=293.4, right_x0=297.7)
    assert _reconstruct_two_column_text(words, A4) is None


def test_a_gap_below_the_narrow_floor_is_not_a_gutter() -> None:
    words = _two_columns(12, left_x1=284.0, long_left_x1=296.0, right_x0=297.7)
    assert _reconstruct_two_column_text(words, A4) is None
