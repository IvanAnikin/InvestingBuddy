"""Research Integrity Closure H3a — the statement-line selector (ADR-092).

Live Richemont report ``584ba2e9`` (CFR3) showed ``total_current_assets = 1,907`` and
``total_current_liabilities = 3,086`` from the FY26 results release, page 14. Those are
the rows "Trade receivables and other current assets" and "Trade payables and other
current liabilities". The balance sheet prints its totals UNLABELLED under the section
headings "Current assets" (28 934) and "Current liabilities" (9 341).

Root causes, all generic:
1. Row labels were matched by SUBSTRING: any label containing "current assets" was
   current assets, any label containing "cash and cash equivalents" was cash
   ("Exchange gains/(losses) on cash and cash equivalents"), any "operating income" was
   operating profit ("Other operating income").
2. The table reconstructor dropped a section heading and the unlabelled subtotal under
   it, so the real total could never be read.
3. A prose read in an unknown scope (a two-column page spliced a segment's "€ 107
   million" into a sentence about the operating result) contradicted the income
   statement's own row and withheld the Group figure.

Fixtures are IFRS-style (European, section heading + unlabelled subtotal) and US-style
("Total current assets"), two columns (current / prior year). No network.
"""

from __future__ import annotations

import io
import time
from datetime import datetime, timezone

import pdfplumber

from app.core.config import Settings
from app.services.pipeline.issuer_financials import (
    WITHHELD_PROSE_DISAGREES_WITH_STATEMENT,
    build_financial_statements_state,
)
from app.services.sources import primary_fact_parser as pfp
from app.services.sources import statement_line_selector as sel
from app.services.sources.document_period import UNKNOWN_DOCUMENT_PERIOD
from app.services.sources.extracted_fact_validator import (
    FIELD_NON_CURRENT_ASSETS,
    VALIDATION_EXCERPT_ONLY,
    VALIDATION_VALIDATED,
    IssuerContext,
    _Candidate,
    _match_label,
    _resolve_group,
    validate_extracted_facts,
)
from app.services.sources.financial_table_reconstructor import reconstruct_financial_tables
from app.services.sources.primary_document_extractor import (
    ExtractedTable,
    PrimaryDocumentExtraction,
)
from tests.helpers.pdf_fixtures import (
    helvetica_width,
    make_pdf_positioned_text,
    right_aligned_row,
)

CFG = Settings()
PAGE_WIDTH = 907.0
EDGES = [600.0, 680.0]


# ── Fixtures ─────────────────────────────────────────────────────────────── #

#: IFRS / European balance sheet: section headings, component rows that CONTAIN the
#: concept's words, and unlabelled subtotals.
IFRS_BALANCE_SHEET = [
    ["€ million", "2026", "2025"],
    ["Assets", "", ""],
    ["Non-current assets", "", ""],
    ["Property, plant and equipment", "4 538", "4 049"],
    ["Other non-current assets", "646", "620"],
    ["", "5 184", "4 669"],
    ["Current assets", "", ""],
    ["Inventories", "9 715", "9 013"],
    ["Trade receivables and other current assets", "1 907", "1 897"],
    ["Assets of disposal group held for sale", "–", "616"],
    ["Cash at bank and on hand", "8 522", "7 606"],
    ["", "20 144", "19 132"],
    ["Total assets", "25 328", "23 801"],
    ["Equity and liabilities", "", ""],
    ["Share capital", "352", "352"],
    ["Retained earnings", "12 380", "11 260"],
    ["Total equity", "12 732", "11 612"],
    ["Non-current liabilities", "", ""],
    ["Borrowings", "4 487", "4 487"],
    ["Lease liabilities", "3 975", "3 836"],
    ["", "8 462", "8 323"],
    ["Current liabilities", "", ""],
    ["Trade payables and other current liabilities", "3 086", "3 079"],
    ["Borrowings", "3", "1 502"],
    ["Bank overdraft", "1 045", "(715)"],
    ["", "4 134", "3 866"],
    ["Total liabilities", "12 596", "12 189"],
    ["Total equity and liabilities", "25 328", "23 801"],
]

#: US-style balance sheet: explicit "Total …" rows, component rows with the same words.
US_BALANCE_SHEET = [
    ["$ in millions", "2025", "2024"],
    ["Cash and cash equivalents", "1,200", "1,100"],
    ["Restricted cash and cash equivalents", "35", "30"],
    ["Prepaid expenses and other current assets", "410", "380"],
    ["Total current assets", "1,645", "1,510"],
    ["Other non-current assets", "300", "290"],
    ["Total assets", "1,945", "1,800"],
    ["Short-term borrowings", "100", "90"],
    ["Accrued expenses and other current liabilities", "520", "500"],
    ["Total current liabilities", "620", "590"],
    ["Liabilities of disposal group held for sale", "15", "0"],
    ["Total liabilities of discontinued operations", "15", "0"],
    ["Total liabilities", "900", "860"],
    ["Total stockholders' equity", "1,045", "940"],
]

#: An income statement with operating-profit look-alikes.
INCOME_STATEMENT = [
    ["€ million", "2026", "2025"],
    ["Revenue", "22 420", "21 399"],
    ["Cost of sales", "(7 982)", "(7 080)"],
    ["Other operating income", "120", "95"],
    ["Share of operating profit of joint ventures", "40", "38"],
    ["Operating profit", "4 492", "4 467"],
    ["Non-operating income", "12", "9"],
    ["Net financial (costs)/income", "(144)", "(53)"],
    ["Profit before taxation", "4 350", "4 489"],
    ["Taxation", "(886)", "(727)"],
    ["Profit for the year", "3 484", "2 750"],
]

#: A cash-flow statement tail: only the CLOSING balance is the cash line.
CASH_FLOW_TAIL = [
    ["€ million", "2026", "2025"],
    ["Net change in cash and cash equivalents", "(1 015)", "345"],
    ["Cash and cash equivalents at the beginning of the year", "5 293", "4 906"],
    ["Exchange gains/(losses) on cash and cash equivalents", "(5)", "42"],
    ["Cash and cash equivalents at the end of the year", "4 273", "5 293"],
]


def _table(rows: list[list[str]], location: str = "p14:t0") -> ExtractedTable:
    return ExtractedTable(table_location=location, table_index=0, page_number=14,
                          rows=rows, row_count=len(rows), col_count=3,
                          extraction_method="native_pdf")


def _facts(*tables: ExtractedTable, currency: str = "EUR") -> dict[tuple[str, str], object]:
    extraction = PrimaryDocumentExtraction(
        content_hash="h" * 64, mime_type="application/pdf", extraction_method="native_pdf",
        status="extracted", tables=list(tables))
    out = {}
    for fact in validate_extracted_facts(
        extraction,
        issuer_context=IssuerContext(company_name="Test Issuer SA", ticker="TST",
                                     reporting_currency=currency),
        cfg=CFG, document_period=UNKNOWN_DOCUMENT_PERIOD,
    ):
        out[(fact.label, fact.period)] = fact
    return out


def _validated_value(facts, label, period):
    fact = facts.get((label, period))
    assert fact is not None, f"no {label}/{period}"
    assert fact.validation_status == VALIDATION_VALIDATED, fact.validation_notes
    return fact.value_numeric


# ── 1. A component is not its total ──────────────────────────────────────── #


class TestLineQualifies:
    def test_current_assets_and_liabilities_are_whole_labels(self):
        for label in ("Current assets", "Total current assets", "Total current assets 12",
                      "Current assets (note 5)", "Total current assets:"):
            assert _match_label(label) == pfp.FIELD_CURRENT_ASSETS, label
        for label in ("Current liabilities", "Total current liabilities",
                      "Total current liabilities (restated)"):
            assert _match_label(label) == pfp.FIELD_CURRENT_LIABILITIES, label
        for label in ("Trade receivables and other current assets",
                      "Prepaid expenses and other current assets", "Other current assets",
                      "Current assets held for sale", "Net current assets",
                      "Trade payables and other current liabilities",
                      "Accrued expenses and other current liabilities",
                      "Other current liabilities", "Total assets less current liabilities"):
            assert _match_label(label) is None, label

    def test_total_assets_and_total_liabilities_are_whole_labels(self):
        assert _match_label("Total assets") == pfp.FIELD_TOTAL_ASSETS
        assert _match_label("Total liabilities") == pfp.FIELD_TOTAL_LIABILITIES
        for label in ("Total assets of disposal group held for sale",
                      "Total liabilities of discontinued operations",
                      "Total liabilities and equity", "Total equity and liabilities"):
            assert _match_label(label) not in (pfp.FIELD_TOTAL_ASSETS,
                                               pfp.FIELD_TOTAL_LIABILITIES), label

    def test_non_current_assets_total_is_not_its_other_line(self):
        assert _match_label("Non-current assets") == FIELD_NON_CURRENT_ASSETS
        assert _match_label("Total non-current assets") == FIELD_NON_CURRENT_ASSETS
        assert _match_label("Other non-current assets") is None

    def test_cash_is_the_balance_line_only(self):
        for label in ("Cash and cash equivalents", "Total cash and cash equivalents",
                      "Cash and cash equivalents at the end of the year",
                      "Cash and cash equivalents, end of period",
                      "Cash and cash equivalents at 31 December 2025",
                      "Cash at bank and in hand"):
            assert _match_label(label) == pfp.FIELD_CASH, label
        for label in ("Restricted cash and cash equivalents",
                      "Exchange gains/(losses) on cash and cash equivalents",
                      "Net change in cash and cash equivalents",
                      "Cash and cash equivalents at the beginning of the year",
                      "Cash and cash equivalents held for sale"):
            assert _match_label(label) is None, label

    def test_borrowings_are_a_whole_line(self):
        assert _match_label("Borrowings") == pfp.FIELD_BORROWINGS
        assert _match_label("Loans and borrowings 14") == pfp.FIELD_BORROWINGS
        for label in ("Proceeds from borrowings", "Repayment of borrowings",
                      "Borrowing costs capitalised"):
            assert _match_label(label) != pfp.FIELD_BORROWINGS, label

    def test_operating_profit_refuses_its_components(self):
        for label in ("Operating profit", "Operating profit/(loss)", "Operating income",
                      "Operating result", "EBIT"):
            assert _match_label(label) == pfp.FIELD_OPERATING_PROFIT, label
        for label in ("Other operating income", "Net other operating income",
                      "Share of operating profit of joint ventures",
                      "Non-operating income", "Other operating income and expenses",
                      "Segment operating profit"):
            assert _match_label(label) != pfp.FIELD_OPERATING_PROFIT, label

    def test_field_names_match_the_parser_vocabulary(self):
        assert sel.FIELD_CURRENT_ASSETS == pfp.FIELD_CURRENT_ASSETS
        assert sel.FIELD_CURRENT_LIABILITIES == pfp.FIELD_CURRENT_LIABILITIES
        assert sel.FIELD_TOTAL_ASSETS == pfp.FIELD_TOTAL_ASSETS
        assert sel.FIELD_TOTAL_LIABILITIES == pfp.FIELD_TOTAL_LIABILITIES
        assert sel.FIELD_CASH == pfp.FIELD_CASH
        assert sel.FIELD_OPERATING_PROFIT == pfp.FIELD_OPERATING_PROFIT
        assert sel.FIELD_NON_CURRENT_ASSETS == FIELD_NON_CURRENT_ASSETS


# ── 2. A section's unlabelled subtotal ───────────────────────────────────── #


class TestSectionTotal:
    def test_lines_that_sum_verify_with_nil_cells(self):
        assert sel.verified_section_total(
            [["9 715", "9 013"], ["1 907", "1 897"], ["–", "616"], ["8 522", "7 606"]],
            ["20 144", "19 132"])

    def test_brackets_and_rounding(self):
        assert sel.verified_section_total([["3 086", "3 079"], ["(1 045)", "715"]],
                                          ["2 041", "3 794"])
        # Off by one printed unit across two rounded lines reconciles; off by 50 never.
        assert sel.verified_section_total([["10.4", "1"], ["20.4", "2"]], ["30.9", "3"])
        assert not sel.verified_section_total([["100", "1"], ["200", "2"]], ["350", "3"])

    def test_a_missing_line_or_a_single_line_never_verifies(self):
        assert not sel.verified_section_total([["9 715"], ["8 522"]], ["20 144"])
        assert not sel.verified_section_total([["20 144"]], ["20 144"])
        assert not sel.verified_section_total([["1", "2"], ["n/a", "3"]], ["4", "5"])
        assert not sel.verified_section_total([["1"], ["2"]], [""])

    def test_grid_heading_takes_the_verified_subtotal(self):
        grid, filled = sel.section_totals_in_grid(IFRS_BALANCE_SHEET)
        by_label = {row[0]: row for row in grid}
        assert by_label["Current assets"][1:] == ["20 144", "19 132"]
        assert by_label["Current liabilities"][1:] == ["4 134", "3 866"]
        assert by_label["Non-current assets"][1:] == ["5 184", "4 669"]
        assert by_label["Assets"][1:] == ["", ""]  # a heading over headings: nothing
        # Not a balance-sheet section this rule reads: left as printed.
        assert by_label["Non-current liabilities"][1:] == ["", ""]
        assert len(filled) == 3

    def test_a_segment_heading_is_never_given_the_segments_sum(self):
        """The segment note's "Operating result" heading over the segments sums to the
        segments' result (5 048), not the Group operating profit (4 492)."""
        rows = [["€ million", "2026", "2025"],
                ["Operating result", "", ""],
                ["Jewellery Maisons", "5 037", "4 896"],
                ["Specialist Watchmakers", "107", "175"],
                ["Other", "(96)", "(102)"],
                ["", "5 048", "4 969"],
                ["Corporate costs", "(517)", "(453)"],
                ["Operating profit", "4 492", "4 467"]]
        grid, filled = sel.section_totals_in_grid(rows)
        assert filled == [] and grid == rows
        facts = _facts(_table(rows))
        assert _validated_value(facts, pfp.FIELD_OPERATING_PROFIT, "2026") == 4492

    def test_a_subtotal_that_does_not_reconcile_fills_nothing(self):
        rows = [list(r) for r in IFRS_BALANCE_SHEET]
        rows[11] = ["", "21 000", "19 132"]  # the current-assets subtotal, 2026 wrong
        grid, filled = sel.section_totals_in_grid(rows)
        assert {row[0]: row for row in grid}["Current assets"][1:] == ["", ""]


# ── Validator: IFRS and US balance sheets, income statement, cash flow ───── #


class TestIfrsBalanceSheet:
    def test_totals_are_the_section_subtotals_never_a_component(self):
        facts = _facts(_table(IFRS_BALANCE_SHEET))
        assert _validated_value(facts, pfp.FIELD_CURRENT_ASSETS, "2026") == 20144
        assert _validated_value(facts, pfp.FIELD_CURRENT_ASSETS, "2025") == 19132
        assert _validated_value(facts, pfp.FIELD_CURRENT_LIABILITIES, "2026") == 4134
        assert _validated_value(facts, pfp.FIELD_CURRENT_LIABILITIES, "2025") == 3866
        assert _validated_value(facts, pfp.FIELD_TOTAL_ASSETS, "2026") == 25328
        assert _validated_value(facts, pfp.FIELD_TOTAL_LIABILITIES, "2026") == 12596
        values = {f.value_numeric for f in facts.values()}
        assert 1907 not in values and 3086 not in values
        note = " ".join(facts[(pfp.FIELD_CURRENT_ASSETS, "2026")].validation_notes)
        assert "unlabelled subtotal" in note and "Current assets" in note

    def test_current_and_non_current_borrowings_still_combine(self):
        facts = _facts(_table(IFRS_BALANCE_SHEET))
        borrowings = facts[(pfp.FIELD_BORROWINGS, "2026")]
        assert borrowings.value_numeric == 4490
        assert borrowings.value_text == "4 487 + 3"

    def test_an_unreconciled_subtotal_is_withheld_not_replaced_by_a_component(self):
        rows = [list(r) for r in IFRS_BALANCE_SHEET]
        rows[11] = ["", "21 000", "20 000"]
        facts = _facts(_table(rows))
        assert (pfp.FIELD_CURRENT_ASSETS, "2026") not in facts
        assert (pfp.FIELD_CURRENT_ASSETS, "2025") not in facts
        # The liabilities section still reconciles and is still read.
        assert _validated_value(facts, pfp.FIELD_CURRENT_LIABILITIES, "2026") == 4134


class TestUsBalanceSheet:
    def test_the_total_rows_are_read_and_components_are_not(self):
        facts = _facts(_table(US_BALANCE_SHEET), currency="USD")
        assert _validated_value(facts, pfp.FIELD_CURRENT_ASSETS, "2025") == 1645
        assert _validated_value(facts, pfp.FIELD_CURRENT_ASSETS, "2024") == 1510
        assert _validated_value(facts, pfp.FIELD_CURRENT_LIABILITIES, "2025") == 620
        assert _validated_value(facts, pfp.FIELD_TOTAL_ASSETS, "2025") == 1945
        assert _validated_value(facts, pfp.FIELD_TOTAL_LIABILITIES, "2025") == 900
        assert _validated_value(facts, pfp.FIELD_CASH, "2025") == 1200
        assert _validated_value(facts, pfp.FIELD_CASH, "2024") == 1100


class TestIncomeStatementAndCashFlow:
    def test_operating_profit_is_the_statement_line(self):
        facts = _facts(_table(INCOME_STATEMENT))
        assert _validated_value(facts, pfp.FIELD_OPERATING_PROFIT, "2026") == 4492
        assert _validated_value(facts, pfp.FIELD_OPERATING_PROFIT, "2025") == 4467

    def test_cash_is_the_closing_balance(self):
        facts = _facts(_table(CASH_FLOW_TAIL))
        assert _validated_value(facts, pfp.FIELD_CASH, "2026") == 4273
        assert _validated_value(facts, pfp.FIELD_CASH, "2025") == 5293


# ── The reconstructor keeps a verified section total ─────────────────────── #


def _rebuild(rows: list[tuple[str, list[str]]]):
    words: list[tuple[str, float, float, float]] = [("€ million", 35.0, 100.0, 8.0)]
    for period, edge in zip(["2026", "2025"], EDGES):
        words.append((period, edge - helvetica_width(period, 8.0), 100.0, 8.0))
    y = 114.0
    for label, values in rows:
        row = right_aligned_row(label or "x", values, y=y, label_x=35,
                                column_right_edges=EDGES)
        words += row if label else row[1:]
        y += 12.0
    raw = make_pdf_positioned_text([words], page_width=PAGE_WIDTH)
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        page = pdf.pages[0]
        return reconstruct_financial_tables(
            page.extract_words(extra_attrs=["size", "fontname"]),
            page_width=float(page.width), page_number=14)


BS_ROWS = [
    ("Current assets", []),
    ("Inventories", ["9 715", "9 013"]),
    ("Trade receivables and other current assets", ["1 907", "1 897"]),
    ("Cash at bank and on hand", ["8 522", "7 606"]),
    ("", ["20 144", "18 516"]),
    ("Total assets", ["42 623", "41 006"]),
    ("Current liabilities", []),
    ("Trade payables and other current liabilities", ["3 086", "3 079"]),
    ("Bank overdraft", ["4 249", "2 522"]),
    ("", ["7 335", "5 601"]),
]


class TestReconstructor:
    def test_the_heading_carries_the_verified_subtotal(self):
        tables = _rebuild(BS_ROWS)
        assert len(tables) == 1
        rows = {r.label: r for r in tables[0].rows}
        current = rows["Current assets"]
        assert current.section_total is True
        assert [c.text for c in current.cells] == ["20 144", "18 516"]
        assert [c.text for c in rows["Current liabilities"].cells] == ["7 335", "5 601"]
        # Placed where the heading is printed, before its lines.
        labels = [r.label for r in tables[0].rows]
        assert labels.index("Current assets") < labels.index("Inventories")
        assert not rows["Inventories"].section_total

    def test_a_segment_heading_is_not_given_the_segments_sum(self):
        tables = _rebuild([
            ("Operating result", []),
            ("Jewellery Maisons", ["5 037", "4 896"]),
            ("Specialist Watchmakers", ["107", "175"]),
            ("Other", ["(96)", "(102)"]),
            ("", ["5 048", "4 969"]),
            ("Operating profit", ["4 492", "4 467"]),
        ])
        labels = [r.label for r in tables[0].rows]
        assert "Operating result" not in labels
        assert not any(r.section_total for r in tables[0].rows)

    def test_a_subtotal_that_does_not_add_up_is_not_attached(self):
        bad = list(BS_ROWS)
        bad[4] = ("", ["20 000", "18 516"])
        tables = _rebuild(bad)
        labels = [r.label for r in tables[0].rows]
        assert "Current assets" not in labels
        assert "Current liabilities" in labels

    def test_end_to_end_the_extractor_reads_the_section_total(self):
        words: list[tuple[str, float, float, float]] = [("€ million", 35.0, 100.0, 8.0)]
        for period, edge in zip(["2026", "2025"], EDGES):
            words.append((period, edge - helvetica_width(period, 8.0), 100.0, 8.0))
        y = 114.0
        for label, values in BS_ROWS:
            row = right_aligned_row(label or "x", values, y=y, label_x=35,
                                    column_right_edges=EDGES)
            words += row if label else row[1:]
            y += 12.0
        from app.services.sources.primary_document_extractor import extract_pdf

        extraction = extract_pdf(make_pdf_positioned_text([words], page_width=PAGE_WIDTH))
        tables = [t for t in extraction.tables if t.reconstructed]
        assert tables and tables[0].section_total_rows
        facts = {}
        for fact in validate_extracted_facts(
            extraction, issuer_context=IssuerContext(company_name="Test Issuer SA",
                                                     ticker="TST", reporting_currency="EUR"),
            cfg=CFG,
        ):
            facts[(fact.label, fact.period)] = fact
        assert _validated_value(facts, pfp.FIELD_CURRENT_ASSETS, "2026") == 20144
        assert _validated_value(facts, pfp.FIELD_CURRENT_LIABILITIES, "2026") == 7335
        assert all(f.value_numeric not in (1907, 3086) for f in facts.values()
                   if f.label in (pfp.FIELD_CURRENT_ASSETS, pfp.FIELD_CURRENT_LIABILITIES))


# ── 3. A primary statement table outranks prose ──────────────────────────── #


def _cand(value, *, rank, scale="million", location="p36:m0"):
    cand = _Candidate(label="operating_profit", period="2026", value_numeric=value,
                      value_text=str(value), unit="currency_amount", currency="EUR",
                      scale=scale, page_number=36, table_location=location,
                      method="native_pdf", base_confidence=0.7, fully_qualified=True,
                      status=VALIDATION_VALIDATED)
    cand.line_rank = rank
    return cand


class TestStatementOutranksProse:
    def test_a_disagreeing_prose_read_is_set_aside(self):
        fact = _resolve_group(("operating_profit", "2026", None), [
            _cand(4492.0, rank=sel.LINE_RANK_STATEMENT),
            _cand(107.0, rank=sel.LINE_RANK_PROSE, location="X4"),
            _cand(4492.0, rank=sel.LINE_RANK_PROSE, location="X5"),
        ], CFG)
        assert fact.validation_status == VALIDATION_VALIDATED
        assert fact.value_numeric == 4492.0
        assert fact.table_location == "p36:m0"  # the statement row is cited
        assert any("set aside" in n for n in fact.validation_notes)

    def test_without_a_statement_table_disagreement_stays_ambiguous(self):
        fact = _resolve_group(("operating_profit", "2026", None), [
            _cand(4492.0, rank=sel.LINE_RANK_TABLE),
            _cand(107.0, rank=sel.LINE_RANK_PROSE, location="X4"),
        ], CFG)
        assert fact.validation_status == VALIDATION_EXCERPT_ONLY

    def test_two_statement_rows_that_disagree_stay_a_conflict(self):
        fact = _resolve_group(("operating_profit", "2026", None), [
            _cand(4492.0, rank=sel.LINE_RANK_STATEMENT),
            _cand(4100.0, rank=sel.LINE_RANK_STATEMENT, location="p12:m0"),
            _cand(107.0, rank=sel.LINE_RANK_PROSE, location="X4"),
        ], CFG)
        assert fact.validation_status == VALIDATION_EXCERPT_ONLY

    def test_a_counterparty_table_never_outranks_a_unitless_statement_row(self):
        """A 20-F related-party table prints "Total current assets 6,053" ($ thousands);
        the balance sheet's own row (287,069) has no unit in its grid. The counterparty
        balance must not become Group current assets: nothing is validated."""
        statement = _cand(287069.0, rank=sel.LINE_RANK_STATEMENT, scale=None, location="t38")
        statement.fully_qualified = False
        statement.status = VALIDATION_EXCERPT_ONLY
        other = _cand(6053.0, rank=sel.LINE_RANK_TABLE, scale="thousand", location="t105")
        fact = _resolve_group(("total_current_assets", "2025", None), [statement, other], CFG)
        assert fact.validation_status != VALIDATION_VALIDATED
        assert fact.value_numeric != 6053.0

    def test_a_table_that_agrees_at_some_scale_is_kept(self):
        statement = _cand(22420.0, rank=sel.LINE_RANK_STATEMENT, scale=None, location="t3")
        statement.fully_qualified = False
        statement.status = VALIDATION_EXCERPT_ONLY
        other = _cand(22.42, rank=sel.LINE_RANK_TABLE, scale="billion", location="t9")
        fact = _resolve_group(("revenue", "2025", None), [statement, other], CFG)
        assert fact.validation_status == VALIDATION_VALIDATED
        assert fact.value_numeric == 22.42

    def test_a_long_income_statement_table_is_statement_shaped(self):
        assert sel.is_statement_shaped(10, 2)
        assert not sel.is_statement_shaped(3, 3)  # a segment box


NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


def _sfact(value, *, excerpt, scale="million", scope=None, document="adhoc", rank=None):
    return {
        "line_rank": rank,
        "field": "operating_profit", "value": str(value), "numeric_value": value,
        "unit": "currency_amount", "currency": "EUR", "scale": scale, "period": "2025",
        "scope": scope, "confidence": "high",
        "source_url": f"https://issuer.example/{document}.pdf", "page_number": 1,
        "excerpt_id": excerpt, "fact_id": f"{document}:{excerpt}:{value}",
        "document_id": document, "document_title": "Annual results",
        "document_date": "2026-03-02", "document_source_tier": "T1_primary_filing",
    }


class TestSlotConflicts:
    def test_unknown_scope_prose_does_not_withhold_the_statement_figure(self):
        state = build_financial_statements_state([
            _sfact(4492.0, excerpt="p1:m0", rank=sel.LINE_RANK_STATEMENT),
            _sfact(4.5, excerpt="X2", scale="billion", scope="group", document="ar"),
            _sfact(107.0, excerpt="X8", document="ar2"),
        ], now=NOW)
        assert state["conflicts"] == []
        slot = state["slots"]["operating_profit_primary_filing"]
        assert slot["numeric_value"] == 4492.0 and slot["period"] == "2025"
        reasons = [w["reason"] for w in state["withheld"]]
        assert WITHHELD_PROSE_DISAGREES_WITH_STATEMENT in reasons

    def test_two_tables_that_disagree_remain_a_conflict(self):
        state = build_financial_statements_state([
            _sfact(4492.0, excerpt="p1:m0", rank=sel.LINE_RANK_STATEMENT),
            _sfact(4100.0, excerpt="p36:m0", document="ar", rank=sel.LINE_RANK_STATEMENT),
            _sfact(107.0, excerpt="X8", document="ar2"),
        ], now=NOW)
        assert [c["field"] for c in state["conflicts"]] == ["operating_profit"]
        assert "operating_profit_primary_filing" not in state["slots"] or (
            state["slots"]["operating_profit_primary_filing"]["period"] != "2025")

    def test_group_scoped_prose_that_disagrees_remains_a_conflict(self):
        state = build_financial_statements_state([
            _sfact(4492.0, excerpt="p1:m0", rank=sel.LINE_RANK_STATEMENT),
            _sfact(3.9, excerpt="X2", scale="billion", scope="group", document="ar"),
        ], now=NOW)
        assert [c["field"] for c in state["conflicts"]] == ["operating_profit"]

    def test_line_rank_of_stored_locators(self):
        assert sel.line_rank("p14:m0") == sel.LINE_RANK_TABLE
        assert sel.line_rank("p12:t2") == sel.LINE_RANK_TABLE
        assert sel.line_rank("t3") == sel.LINE_RANK_TABLE
        assert sel.line_rank("ixbrl:us-gaap:OperatingIncomeLoss@c1") == sel.LINE_RANK_TABLE
        assert sel.line_rank("X12") == sel.LINE_RANK_PROSE
        assert sel.line_rank(None) == sel.LINE_RANK_PROSE


# ── ADR-092 review ───────────────────────────────────────────────────────── #

#: IFRS 5: the disposal group is printed AFTER the first subtotal, then a second
#: unlabelled subtotal includes it.
HFS_BALANCE_SHEET = [
    ["€ million", "2026", "2025"],
    ["Non-current assets", "", ""],
    ["Property, plant and equipment", "1 000", "900"],
    ["Other non-current assets", "700", "600"],
    ["", "1 700", "1 500"],
    ["Current assets", "", ""],
    ["Inventories", "900", "800"],
    ["Trade receivables", "300", "250"],
    ["Cash", "100", "90"],
    ["", "1 300", "1 140"],
    ["Assets of disposal group held for sale", "2 000", "–"],
    ["", "3 300", "1 140"],
    ["Total assets", "5 000", "2 640"],
    ["Equity and liabilities", "", ""],
    ["Share capital", "500", "500"],
    ["Retained earnings", "1 000", "800"],
    ["Total equity", "1 500", "1 300"],
    ["Current liabilities", "", ""],
    ["Trade payables", "600", "540"],
    ["Borrowings", "400", "300"],
    ["", "1 000", "840"],
    ["Liabilities directly associated with assets held for sale", "2 500", "–"],
    ["", "3 500", "840"],
    ["Total liabilities", "3 500", "1 340"],
    ["Total equity and liabilities", "5 000", "2 640"],
]


def _row(grid, label):
    return {row[0]: row for row in grid}[label]


class TestReviewHeldForSale:
    def test_the_later_subtotal_including_held_for_sale_is_the_total(self):
        grid, _filled = sel.section_totals_in_grid(HFS_BALANCE_SHEET)
        assert _row(grid, "Current assets")[1:] == ["3 300", "1 140"]
        assert _row(grid, "Current liabilities")[1:] == ["3 500", "840"]
        assert _row(grid, "Non-current assets")[1:] == ["1 700", "1 500"]

    def test_the_validator_reads_the_including_total_and_total_assets_stands(self):
        facts = _facts(_table(HFS_BALANCE_SHEET))
        assert _validated_value(facts, pfp.FIELD_CURRENT_ASSETS, "2026") == 3300
        assert _validated_value(facts, pfp.FIELD_CURRENT_LIABILITIES, "2026") == 3500
        # 1 700 + 3 300 = 5 000: the subtotal check no longer demotes total assets.
        assert _validated_value(facts, pfp.FIELD_TOTAL_ASSETS, "2026") == 5000
        assert all(f.value_numeric not in (1300, 1000) for f in facts.values()
                   if f.label in (pfp.FIELD_CURRENT_ASSETS, pfp.FIELD_CURRENT_LIABILITIES))

    def test_a_later_subtotal_that_does_not_add_up_refuses_the_section(self):
        rows = [list(r) for r in HFS_BALANCE_SHEET]
        rows[11] = ["", "3 200", "1 140"]
        grid, _filled = sel.section_totals_in_grid(rows)
        assert _row(grid, "Current assets")[1:] == ["", ""]
        facts = _facts(_table(rows))
        assert (pfp.FIELD_CURRENT_ASSETS, "2026") not in facts

    def test_a_held_for_sale_line_with_no_later_subtotal_refuses_the_section(self):
        rows = [list(r) for r in HFS_BALANCE_SHEET]
        del rows[11]  # no subtotal including the disposal group
        grid, _filled = sel.section_totals_in_grid(rows)
        assert _row(grid, "Current assets")[1:] == ["", ""]

    def test_the_reconstructor_takes_the_later_subtotal(self):
        tables = _rebuild([
            ("Current assets", []),
            ("Inventories", ["900", "800"]),
            ("Trade receivables", ["300", "250"]),
            ("Cash", ["100", "90"]),
            ("", ["1 300", "1 140"]),
            ("Assets of disposal group held for sale", ["2 000", "–"]),
            ("", ["3 300", "1 140"]),
            ("Total assets", ["5 000", "2 640"]),
        ])
        rows = {r.label: r for r in tables[0].rows}
        assert [c.text for c in rows["Current assets"].cells] == ["3 300", "1 140"]
        labels = [r.label for r in tables[0].rows]
        assert labels.index("Current assets") < labels.index("Inventories")

    def test_the_reconstructor_refuses_a_later_subtotal_that_does_not_add_up(self):
        tables = _rebuild([
            ("Current assets", []),
            ("Inventories", ["900", "800"]),
            ("Trade receivables", ["300", "250"]),
            ("", ["1 200", "1 050"]),
            ("Assets of disposal group held for sale", ["2 000", "–"]),
            ("", ["9 999", "1 050"]),
            ("Total assets", ["5 000", "2 640"]),
        ])
        assert "Current assets" not in [r.label for r in tables[0].rows]


class TestReviewLabelForms:
    ACCEPTED = [
        (sel.FIELD_CASH, "Cash and cash equivalents at the end of the reporting period"),
        (sel.FIELD_CASH, "Cash and cash equivalents at year end"),
        (sel.FIELD_CASH, "Cash and cash equivalents at year-end"),
        (sel.FIELD_CASH, "Closing cash and cash equivalents"),
        (sel.FIELD_CASH, "Cash and cash equivalents – end of year"),
        (sel.FIELD_CASH, "Cash and cash equivalents - end of period"),
        (sel.FIELD_CASH, "Cash and cash equivalents at 31.12.2025"),
        (sel.FIELD_CASH, "Cash and cash equivalents at the close of the period"),
        (sel.FIELD_CASH, "4.6 Cash and cash equivalents at end of period"),
        (sel.FIELD_CASH, "4.6  Cash and cash equivalents at end of period"),
        (sel.FIELD_CASH, "Cash and cash equivalents (item 4.6)"),
        (sel.FIELD_CASH, "Cash and cash equivalents 16 (iii)"),
        (sel.FIELD_CURRENT_LIABILITIES, "Total current liabilities 21,22"),
        (sel.FIELD_CURRENT_LIABILITIES, "Total current liabilities 21-22"),
        (sel.FIELD_CURRENT_ASSETS, "Total current assets (note 5(a))"),
        (sel.FIELD_CURRENT_ASSETS, "Total current assets (Notes 5, 6)"),
        (sel.FIELD_CURRENT_ASSETS, "Current assets, total"),
        (sel.FIELD_CURRENT_ASSETS, "Total current assets (restated)"),
        (sel.FIELD_CURRENT_ASSETS, "Total current assets (re-presented)"),
        (sel.FIELD_CURRENT_ASSETS, "Total current assets (unaudited)"),
        (sel.FIELD_TOTAL_ASSETS, "Total assets (audited)"),
    ]
    REFUSED = [
        (sel.FIELD_CURRENT_ASSETS, "Total current assets (restricted)"),
        (sel.FIELD_CURRENT_ASSETS, "Total current assets (parent company)"),
        (sel.FIELD_CURRENT_ASSETS, "Total current assets (segment)"),
        (sel.FIELD_CURRENT_ASSETS, "Total current assets (excl. HFS)"),
        (sel.FIELD_CURRENT_ASSETS, "Total current assets (pro forma)"),
        (sel.FIELD_CASH, "Cash and cash equivalents (restricted)"),
        (sel.FIELD_OPERATING_PROFIT, "Adjusted operating profit"),
        (sel.FIELD_OPERATING_PROFIT, "Underlying operating profit"),
        (sel.FIELD_OPERATING_PROFIT, "Operating profit (non-GAAP)"),
        (sel.FIELD_OPERATING_PROFIT, "Pro forma operating profit"),
        (sel.FIELD_OPERATING_PROFIT, "Operating profit before exceptional items"),
        (sel.FIELD_OPERATING_PROFIT, "Operating profit after share of associates"),
        (sel.FIELD_OPERATING_PROFIT, "Operating profit before share of associates"),
    ]

    def test_accepted_forms(self):
        refused = [label for field, label in self.ACCEPTED
                   if not sel.line_qualifies(field, label)]
        assert refused == []

    def test_refused_forms(self):
        accepted = [label for field, label in self.REFUSED
                    if sel.line_qualifies(field, label)]
        assert accepted == []

    def test_the_validator_matches_the_accepted_cash_forms(self):
        for _field, label in self.ACCEPTED[:12]:
            assert _match_label(label) == pfp.FIELD_CASH, label


def _wide_table(rows, *, location="p20:t0", scope=None, context=()):
    return ExtractedTable(table_location=location, table_index=0, page_number=20,
                          rows=rows, row_count=len(rows),
                          col_count=max(len(r) for r in rows),
                          extraction_method="native_pdf", scope=scope,
                          context_headings=list(context))


def _ranks(facts, label):
    return {f.line_rank for (lab, _p), f in facts.items() if lab == label}


FIVE_YEAR_RECORD = [
    ["€ million", "2026", "2025", "2024", "2023", "2022"],
    ["Revenue", "22 420", "21 399", "20 616", "19 953", "19 181"],
    ["Cost of sales", "(7 982)", "(7 080)", "(6 900)", "(6 600)", "(6 400)"],
    ["Operating profit", "4 492", "4 467", "4 800", "5 000", "3 400"],
    ["Profit before taxation", "4 350", "4 489", "4 700", "4 900", "3 300"],
    ["Profit for the year", "3 484", "2 750", "3 000", "3 100", "2 000"],
    ["Total assets", "42 623", "41 006", "40 000", "39 000", "38 000"],
    ["Total equity", "22 000", "21 000", "20 000", "19 000", "18 000"],
    ["Employees", "38 000", "37 000", "36 000", "35 000", "34 000"],
]


class TestReviewPrimaryStatementRank:
    def test_marker_and_period_rules(self):
        assert sel.may_be_primary_statement(["€ million", "2026", "2025"],
                                            ["Consolidated balance sheet"])
        assert not sel.may_be_primary_statement(FIVE_YEAR_RECORD[0])
        for heading in ("Parent company balance sheet", "Company-only statements",
                        "Five-year record", "Financial highlights", "Key figures",
                        "Segment information", "Adjusted results", "10-year summary"):
            assert not sel.may_be_primary_statement(["", "2026", "2025"], [heading]), heading

    def test_the_group_balance_sheet_is_the_statement(self):
        facts = _facts(_table(IFRS_BALANCE_SHEET))
        assert _ranks(facts, pfp.FIELD_TOTAL_ASSETS) == {sel.LINE_RANK_STATEMENT}

    def test_a_five_year_record_is_not_the_statement(self):
        facts = _facts(_wide_table(FIVE_YEAR_RECORD))
        assert _ranks(facts, pfp.FIELD_OPERATING_PROFIT) == {sel.LINE_RANK_TABLE}

    def test_a_parent_company_balance_sheet_is_not_the_statement(self):
        facts = _facts(_wide_table(IFRS_BALANCE_SHEET,
                                   context=["Parent company balance sheet"]))
        assert _ranks(facts, pfp.FIELD_TOTAL_ASSETS) == {sel.LINE_RANK_TABLE}

    def test_a_segment_table_is_not_the_statement(self):
        facts = _facts(_wide_table(IFRS_BALANCE_SHEET, scope="Jewellery Maisons"))
        assert sel.LINE_RANK_STATEMENT not in _ranks(facts, pfp.FIELD_TOTAL_ASSETS)

    def test_a_table_whose_identity_fails_is_not_the_statement(self):
        rows = [list(r) for r in IFRS_BALANCE_SHEET]
        rows[16] = ["Total equity", "99 999", "11 612"]
        facts = _facts(_table(rows))
        ranks = {f.line_rank for f in facts.values()}
        assert sel.LINE_RANK_STATEMENT not in ranks

    def test_a_shaped_table_without_statement_rank_sets_nothing_aside(self):
        other = _cand(5100.0, rank=sel.LINE_RANK_TABLE, location="p3:m0")
        prose = _cand(4492.0, rank=sel.LINE_RANK_PROSE, location="X4")
        fact = _resolve_group(("operating_profit", "2026", None), [other, prose], CFG)
        assert not any("set aside" in n for n in fact.validation_notes)


class TestReviewSlotRank:
    def test_a_highlights_table_does_not_silence_prose(self):
        state = build_financial_statements_state([
            _sfact(5100.0, excerpt="p2:m0", rank=sel.LINE_RANK_TABLE),
            _sfact(4492.0, excerpt="X8", document="ar2"),
        ], now=NOW)
        assert [c["field"] for c in state["conflicts"]] == ["operating_profit"]
        slot = state["slots"].get("operating_profit_primary_filing")
        assert slot is None or slot["numeric_value"] != 5100.0
        assert WITHHELD_PROSE_DISAGREES_WITH_STATEMENT not in [
            w["reason"] for w in state["withheld"]]

    def test_a_parent_company_table_does_not_silence_prose(self):
        state = build_financial_statements_state([
            _sfact(310.0, excerpt="p88:m0", rank=sel.LINE_RANK_TABLE, document="ar"),
            _sfact(4492.0, excerpt="X8", document="ar2"),
        ], now=NOW)
        assert [c["field"] for c in state["conflicts"]] == ["operating_profit"]

    def test_a_table_persisted_before_the_rank_sets_nothing_aside(self):
        state = build_financial_statements_state([
            _sfact(4492.0, excerpt="p1:m0"),
            _sfact(107.0, excerpt="X8", document="ar2"),
        ], now=NOW)
        assert [c["field"] for c in state["conflicts"]] == ["operating_profit"]

    def test_an_inline_xbrl_tag_sets_disagreeing_prose_aside(self):
        state = build_financial_statements_state([
            _sfact(4492.0, excerpt="ixbrl:ifrs-full:ProfitLossFromOperatingActivities@c1"),
            _sfact(107.0, excerpt="X8", document="ar2"),
        ], now=NOW)
        assert state["conflicts"] == []
        assert state["slots"]["operating_profit_primary_filing"]["numeric_value"] == 4492.0

    def test_a_period_only_blocker_is_never_the_statement_side(self):
        from app.services.pipeline.issuer_financials import _prose_set_aside

        statement = _sfact(4492.0, excerpt="p1:m0", rank=sel.LINE_RANK_STATEMENT)
        prose = _sfact(107.0, excerpt="X8", document="ar2")
        assert _prose_set_aside([statement, prose]) == [prose]
        assert _prose_set_aside([statement, prose], {id(statement)}) == []


class TestReviewPersistedRank:
    def test_the_rank_is_carried_on_the_validated_fact(self):
        facts = _facts(_table(INCOME_STATEMENT))
        assert facts[(pfp.FIELD_OPERATING_PROFIT, "2026")].line_rank == (
            sel.LINE_RANK_STATEMENT)

    def test_only_a_known_rank_is_persisted(self):
        from app.services.extracted_document_service import _line_rank

        assert [_line_rank(v) for v in (0, 1, 2, 3, -1, None, "0")] == [
            0, 1, 2, None, None, None, None]

    def test_the_model_and_migration_carry_the_column(self):
        import importlib.util
        from pathlib import Path

        from app.models.extracted_document import ExtractedFact

        assert "line_rank" in ExtractedFact.__table__.columns
        path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
                / "046_add_extracted_fact_line_rank.py")
        spec = importlib.util.spec_from_file_location("m046", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert (module.revision, module.down_revision) == ("046", "045")


# ── Every new pattern is linear ──────────────────────────────────────────── #


def test_new_patterns_are_linear_on_their_worst_cases():
    cases = [
        (sel._TRAILING_QUALIFIER_RE, "current assets" + " " * 5000 + "x"),
        (sel._TRAILING_QUALIFIER_RE, "(" + "1 " * 3000),
        (sel._TRAILING_QUALIFIER_RE, " 1" * 3000 + "x"),
        (sel._CLOSING_BALANCE_RE, " ,at end of" * 1500 + "x"),
        (sel._CLOSING_BALANCE_RE, " at 1 " * 2000),
        (sel._OPERATING_PROFIT_COMPONENT_RE, "other " * 3000),
        (sel._OPERATING_PROFIT_COMPONENT_RE, "operating income and " * 1000),
        (sel._PLAIN_NUMBER_RE, "1 000" * 2000 + "x"),
        (sel._PLAIN_NUMBER_RE, "1" * 6000 + "."),
        (sel._TABLE_LOCATOR_RE, "ixbrl:" + "x" * 6000),
        (sel._TRAILING_QUALIFIER_RE, " 1," * 3000 + "x"),
        (sel._TRAILING_QUALIFIER_RE, "(note " + "1-" * 3000),
        (sel._TRAILING_QUALIFIER_RE, ", total" * 2000 + "x"),
        (sel._LEADING_ITEM_RE, "1." * 3000),
        (sel._CLOSING_BALANCE_RE, " - at the close of" * 1000 + "x"),
        (sel._CLOSING_BALANCE_RE, " at 1.1." * 2000),
        (sel._CLOSING_PREFIX_RE, "closing " * 2000),
        (sel._OPERATING_PROFIT_COMPONENT_RE, "before the " * 2000),
        (sel._HELD_FOR_SALE_RE, "held for " * 2000),
        (sel._NOT_PRIMARY_STATEMENT_RE, "five year " * 2000),
        (sel._NOT_PRIMARY_STATEMENT_RE, "parent " * 2000),
        (sel._YEAR_RE, "2" * 6000),
    ]
    for pattern, text in cases:
        started = time.perf_counter()
        list(pattern.finditer(text))
        pattern.fullmatch(text)
        assert time.perf_counter() - started < 0.2, pattern.pattern[:40]
    started = time.perf_counter()
    sel.normalise_line_label("Total current assets" + " (1)" * 2000)
    sel.line_qualifies(sel.FIELD_CASH, "cash and cash equivalents" + " at end of" * 1000)
    assert time.perf_counter() - started < 0.5
