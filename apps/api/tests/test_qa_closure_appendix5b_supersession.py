"""Research Integrity Closure E2 (ADR-087) — Appendix 5B / 4C reader, later period
supersedes, segment periods; and the regressions of the PR #326 review (H1–L4).

Fixtures only: every report text below is synthetic but laid out the way pdfplumber
prints the real ASX forms (rows in reading order, wrapped labels, a split column header,
a rotated watermark, footers, a footnote marker glued to a value). No network.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.ledger import period_supersession as ps
from app.services.sources import asx_appendix_5b as m5b
from tests.helpers.pdf_fixtures import make_pdf

# ── Fixtures: an Appendix 5B as pdfplumber prints it ──────────────────────── #

_FOOTER = ("ASX Listing Rules Appendix 5B (17/07/20) Page {n}\n"
           "+ See chapter 19 of the ASX Listing Rules for defined terms.\n"
           "ylno\nesu\nlanosrep\nroF")
_HEADER = ("Consolidated statement of cash flows Current quarter Year to date\n"
           "$A'000 (3 months)\n$A'000")

PAGE_1 = f"""Rule 5.5
Appendix 5B
Mining exploration entity or oil and gas exploration entity
quarterly cash flow report
Name of entity
EXAMPLE MINERALS LIMITED
ABN Quarter ended ("current quarter")
12 345 678 901 30 September 2026
{_HEADER}
1. Cash flows from operating activities
1.1 Receipts from customers
1.2 Payments for
(a) exploration & evaluation - -
(b) development
(c) production
(d) staff costs (1,100) (1,100)
(e) administration and corporate costs (900) (900)
1.3 Dividends received (see note 3)
1.4 Interest received 310 310
1.5 Interest and other costs of finance paid
1.6 Income taxes paid
1.7 Government grants and tax incentives 0 0
1.8 Other (provide details if material)
(a) GST/VAT/ paid/received (3,520) (3,520)
1.9 Net cash from / (used in) operating (5,210) (5,210)
activities
2. Cash flows from investing activities
2.1 Payments to acquire or for:
(a) entities
(b) tenements
(c) property, plant and equipment (11,360) (11,360)
(d) exploration & evaluation (1,040) (1,040)
{_FOOTER.format(n=1)}"""

PAGE_2 = f"""Appendix 5B
Mining exploration entity or oil and gas exploration entity quarterly cash flow report
{_HEADER}
(e) investments
(f) other non-current assets
2.2 Proceeds from the disposal of:
2.3 Cash flows from loans to other entities
2.4 Dividends received (see note 3)
2.5 Other (provide details if material)
2.6 Net cash from / (used in) investing
activities (12,400) (12,400)
3. Cash flows from financing activities
3.1 Proceeds from issues of equity securities 20,000 20,000
(excluding convertible debt securities)
3.2 Proceeds from issue of convertible debt
securities
3.5 Proceeds from borrowings
3.6 Repayment of borrowings
3.8 Dividends paid
3.9 Other (provide details if material)
3.10 Net cash from / (used in) financing 20,000 20,000
activities
4. Net increase / (decrease) in cash and
cash equivalents for the period
4.1 Cash and cash equivalents at beginning of 28,8601 28,860
period
4.2 Net cash from / (used in) operating (5,210) (5,210)
activities (item 1.9 above)
1 The opening balance is based on the audited annual report for the year ended 30 June 2026.
{_FOOTER.format(n=2)}"""

PAGE_3 = f"""Appendix 5B
Mining exploration entity or oil and gas exploration entity quarterly cash flow report
{_HEADER}
4.3 Net cash from / (used in) investing activities (12,400) (12,400)
(item 2.6 above)
4.4 Net cash from / (used in) financing activities 20,000 20,000
(item 3.10 above)
4.5 Effect of movement in exchange rates on - -
cash held
4.6 Cash and cash equivalents at end of 31,250 31,250
period
5. Reconciliation of cash and cash Current quarter Previous quarter
equivalents $A'000 $A'000
at the end of the quarter (as shown in the
consolidated statement of cash flows) to the
related items in the accounts
5.1 Bank balances 31,250 28,860
5.5 Cash and cash equivalents at end of 31,250 28,860
quarter (should equal item 4.6 above)
6. Payments to related parties of the entity and their Current quarter
associates $A'000
6.1 Aggregate amount of payments to related parties and their 262
associates included in item 1
Note: if any amounts are shown in items 6.1 or 6.2, your quarterly activity report must
{_FOOTER.format(n=3)}"""

PAGE_4 = f"""Appendix 5B
Mining exploration entity or oil and gas exploration entity quarterly cash flow report
7. Financing facilities Total facility Amount drawn at
7.1 Loan facilities
7.5 Unused financing facilities available at quarter end
8. Estimated cash available for future operating activities $A'000
8.1 Net cash from / (used in) operating activities (item 1.9) (5,210)
8.2 Payments for exploration & evaluation classified as investing (1,040)
activities) (item 2.1(d))
8.3 Total relevant outgoings (item 8.1 + item 8.2) (6,250)
8.4 Cash and cash equivalents at quarter end (item 4.6) 31,250
8.5 Unused finance facilities available at quarter end (item 7.5) -
8.6 Total available funding (item 8.4 + item 8.5) 31,250
8.7 Estimated quarters of funding available (item 8.6 divided by 5.00
item 8.3)
Note: if the entity has reported positive relevant outgoings, answer item 8.7 as "N/A".
8.8 If item 8.7 is less than 2 quarters, please provide answers to the following questions:
8.8.1 Does the entity expect that it will continue to have the current level of net operating
Answer: N/A
{_FOOTER.format(n=4)}"""

PAGES_5B = [PAGE_1, PAGE_2, PAGE_3, PAGE_4]


def _variant(pages: list[str], old: str, new: str) -> list[str]:
    assert any(old in p for p in pages), old
    return [p.replace(old, new) for p in pages]


#: The header split over lines ("Current quarter" / "Year to date"), as some PDFs print it.
PAGES_5B_SPLIT_HEADER = [
    p.replace("Consolidated statement of cash flows Current quarter Year to date",
              "Consolidated statement of cash flows Current quarter\nYear to date")
    for p in PAGES_5B
]

#: An Appendix 4C (a non-mining entity's form): half-year to date, US dollars, runway 8.5.
PAGES_4C = [
    """Appendix 4C
Quarterly cash flow report for entities subject to Listing Rule 4.7B
Name of entity
EXAMPLE TECHNOLOGIES LIMITED
ABN Quarter ended ("current quarter")
98 765 432 109 31 December 2026
Consolidated statement of cash flows Current quarter Year to date
$US'000 (6 months)
$US'000
1. Cash flows from operating activities
1.1 Receipts from customers 4,100 7,900
1.9 Net cash from / (used in) operating (1,200) (2,300)
activities
2. Cash flows from investing activities
2.6 Net cash from / (used in) investing (300) (450)
activities
3. Cash flows from financing activities
3.10 Net cash from / (used in) financing 5,000 5,000
activities
4. Net increase / (decrease) in cash and
cash equivalents for the period
4.1 Cash and cash equivalents at beginning of 5,500 6,750
period
4.2 Net cash from / (used in) operating (1,200) (2,300)
activities (item 1.9 above)
4.6 Cash and cash equivalents at end of 9,000 9,000
period
ASX Listing Rules Appendix 4C (17/07/20) Page 1""",
    """Appendix 4C
Quarterly cash flow report for entities subject to Listing Rule 4.7B
8. Estimated cash available for future operating activities $US'000
8.1 Net cash from / (used in) operating activities (item 1.9) (1,200)
8.2 Cash and cash equivalents at quarter end (item 4.6) 9,000
8.3 Unused finance facilities available at quarter end (item 7.5) -
8.4 Total available funding (item 8.2 + item 8.3) 9,000
8.5 Estimated quarters of funding available (item 8.4 divided by 7.50
item 8.1)
ASX Listing Rules Appendix 4C (17/07/20) Page 2""",
]


def _read(pages):  # noqa: ANN001, ANN202
    return m5b.read_quarterly_cash_flow_report(pages)


# ── The reader ────────────────────────────────────────────────────────────── #


class TestAppendix5BReader:
    def test_the_faithful_layout_reads_every_item(self) -> None:
        r = _read(PAGES_5B)
        assert r.ok, (r.refusal, r.notes)
        assert r.form == m5b.FORM_APPENDIX_5B
        assert r.quarter_end == date(2026, 9, 30)
        assert r.ytd_months == 3 and r.currency == "AUD"
        values = {k: v.values for k, v in r.fields.items()}
        assert values == {
            "operating_cash_flow": (-5210, -5210),
            "investing_cash_flow": (-12400, -12400),
            "financing_cash_flow": (20000, 20000),
            "cash_and_equivalents": (31250, 31250),
            "issuer_estimated_quarters_of_funding": (5,),
        }
        assert {k: v.item for k, v in r.fields.items()} == {
            "operating_cash_flow": "1.9", "investing_cash_flow": "2.6",
            "financing_cash_flow": "3.10", "cash_and_equivalents": "4.6",
            "issuer_estimated_quarters_of_funding": "8.7"}
        assert r.fields["cash_and_equivalents"].page == 3
        assert r.fields["investing_cash_flow"].page == 2

    def test_period_comes_from_the_header_and_is_keyed_by_fiscal_year(self) -> None:
        r = _read(PAGES_5B)
        # Quarter ended 30 Sep 2026, 3 months of the fiscal year: FY ends June 2027.
        assert m5b.period_keys(r) == ("2027-Q1", None)

    def test_a_prose_quarter_ended_does_not_override_the_header(self) -> None:
        pages = [PAGES_5B[0] + "\nDuring the quarter ended 31 March 2026 the company drilled",
                 *PAGES_5B[1:]]
        r = _read(pages)
        assert r.ok and r.quarter_end == date(2026, 9, 30)

    def test_facts_carry_scale_currency_period_and_locator(self) -> None:
        facts = m5b.statement_facts(_read(PAGES_5B))
        by_label = {f.label: f for f in facts}
        assert len(facts) == 5  # 3 months to date: no separate year-to-date fact
        cash = by_label["cash_and_equivalents"]
        assert cash.value_numeric == 31250.0 and cash.value_text == "31,250"
        assert (cash.currency, cash.scale, cash.unit) == ("AUD", "thousand", "currency_amount")
        assert cash.period == "2027-Q1" and cash.scope == "Group"
        assert cash.table_location == "appendix_5b@2026-09-30#4.6"
        assert cash.page_number == 3 and cash.confidence >= 0.75
        assert cash.extraction_method == m5b.EXTRACTION_METHOD
        assert len(cash.extraction_method) <= 50 and len(cash.table_location) <= 200
        runway = by_label["issuer_estimated_quarters_of_funding"]
        assert runway.value_numeric == 5.0 and runway.unit == "quarters"
        assert runway.currency is None and runway.scale is None
        ocf = by_label["operating_cash_flow"]
        assert ocf.value_numeric == -5210.0 and ocf.value_text == "(5,210)"

    def test_the_magnitude_is_thousands_never_units(self) -> None:
        from app.services.pipeline.issuer_financials import _magnitude

        cash = next(f for f in m5b.statement_facts(_read(PAGES_5B))
                    if f.label == "cash_and_equivalents")
        assert _magnitude({"numeric_value": cash.value_numeric, "scale": cash.scale}) == (
            31_250_000.0)

    def test_a_split_column_header_reads_the_same(self) -> None:
        r = _read(PAGES_5B_SPLIT_HEADER)
        assert r.ok and r.ytd_months == 3
        assert set(r.fields) == set(_read(PAGES_5B).fields)

    def test_the_glued_footnote_line_is_not_read(self) -> None:
        r = _read(PAGES_5B)
        # "28,8601" is the value 28,860 with footnote marker 1: not a well-formed amount.
        assert r.items["4.1"].values == (28860,)

    @pytest.mark.parametrize("token,expected", [
        ("(24001)", (False, None)),   # "(2,400)" + footnote 1: never an amount (review L3)
        ("1000", (False, None)),
        ("-12345", (False, None)),
        ("(2,400)", (True, -2400)),
        ("999", (True, 999)),
        ("4.50", (True, 4.5)),
        ("-", (True, 0)),
    ])
    def test_a_separator_less_run_of_four_digits_is_not_an_amount(
            self, token: str, expected: tuple[bool, float | None]) -> None:
        assert m5b._amount(token) == expected

    def test_a_footnote_glued_to_an_unformatted_value_gives_no_fact(self) -> None:
        # Review L3 repro: a 6-month year to date whose 2.6 column reads "(24001)".
        pages = [p.replace("(3 months)", "(6 months)")
                 .replace("activities (12,400) (12,400)", "activities (12,400) (24001)")
                 .replace("(5,210) (5,210)", "(5,210) (9,000)")
                 .replace("(1,100) (1,100)", "(1,100) (2,000)") for p in PAGES_5B]
        facts = m5b.statement_facts(_read(pages))
        assert facts  # the rest of the form still reads
        assert all(abs(f.value_numeric) != 24001 for f in facts)
        assert ("investing_cash_flow", "2027-H1") not in {(f.label, f.period) for f in facts}

    def test_the_appendix_4c_reads_its_own_runway_item(self) -> None:
        r = _read(PAGES_4C)
        assert r.ok, (r.refusal, r.notes)
        assert r.form == m5b.FORM_APPENDIX_4C and r.currency == "USD"
        assert r.quarter_end == date(2026, 12, 31) and r.ytd_months == 6
        assert r.fields["issuer_estimated_quarters_of_funding"].item == "8.5"
        assert m5b.period_keys(r) == ("2027-Q2", "2027-H1")
        facts = {(f.label, f.period): f.value_numeric for f in m5b.statement_facts(r)}
        assert facts[("operating_cash_flow", "2027-Q2")] == -1200.0
        assert facts[("operating_cash_flow", "2027-H1")] == -2300.0
        assert facts[("cash_and_equivalents", "2027-Q2")] == 9000.0
        assert ("cash_and_equivalents", "2027-H1") not in facts  # a balance has no YTD
        assert facts[("issuer_estimated_quarters_of_funding", "2027-Q2")] == 7.5

    def test_a_calendar_year_issuer_keys_its_third_quarter(self) -> None:
        pages = [p.replace("(3 months)", "(9 months)") for p in PAGES_5B]
        # A 9-month year to date: the columns legitimately differ, so give them their
        # own values.
        pages = [p.replace("1.9 Net cash from / (used in) operating (5,210) (5,210)",
                           "1.9 Net cash from / (used in) operating (5,210) (15,900)")
                 .replace("4.2 Net cash from / (used in) operating (5,210) (5,210)",
                          "4.2 Net cash from / (used in) operating (5,210) (15,900)")
                 for p in pages]
        r = _read(pages)
        assert r.ok and m5b.period_keys(r) == ("2026-Q3", None)
        facts = {(f.label, f.period): f.value_numeric for f in m5b.statement_facts(r)}
        assert facts[("operating_cash_flow", "2026-Q3")] == -5210.0
        assert not any(p and p.endswith(("H1", "H2")) for _l, p in facts)


class TestReaderRefusals:
    def test_not_a_report(self) -> None:
        r = _read(["Quarterly Activities Report for the quarter ended 30 September 2026. "
                   "Cash at the end of the quarter was A$31.3 million."])
        assert r.refusal == m5b.REFUSE_NOT_A_REPORT and not m5b.statement_facts(r)

    def test_both_forms_named(self) -> None:
        pages = [PAGES_5B[0] + "\nAppendix 4C", *PAGES_5B[1:]]
        assert _read(pages).refusal == m5b.REFUSE_AMBIGUOUS_FORM

    def test_no_quarter_end(self) -> None:
        pages = _variant(PAGES_5B, "30 September 2026", "the September quarter")
        assert _read(pages).refusal == m5b.REFUSE_NO_PERIOD

    def test_a_date_that_is_not_a_month_end(self) -> None:
        pages = _variant(PAGES_5B, "30 September 2026", "15 September 2026")
        assert _read(pages).refusal == m5b.REFUSE_NO_PERIOD

    def test_two_currency_headers(self) -> None:
        pages = [PAGES_5B[0], PAGES_5B[1].replace("$A'000 (3 months)", "$US'000 (3 months)"),
                 *PAGES_5B[2:]]
        assert _read(pages).refusal == m5b.REFUSE_CURRENCY

    def test_reversed_columns_are_not_read(self) -> None:
        pages = [p.replace("Current quarter Year to date", "Year to date Current quarter")
                 for p in PAGES_5B]
        r = _read(pages)
        assert not r.ok and not m5b.statement_facts(r)

    def test_missing_months_header(self) -> None:
        pages = [p.replace("(3 months)", "") for p in PAGES_5B]
        assert _read(pages).refusal == m5b.REFUSE_NO_COLUMNS

    def test_a_restatement_that_disagrees_refuses_the_reading(self) -> None:
        pages = _variant(PAGES_5B,
                         "4.2 Net cash from / (used in) operating (5,210) (5,210)",
                         "4.2 Net cash from / (used in) operating (5,290) (5,290)")
        r = _read(pages)
        assert r.refusal == m5b.REFUSE_SELF_CHECK and not m5b.statement_facts(r)

    def test_a_three_month_ytd_that_differs_from_the_quarter_is_misaligned(self) -> None:
        pages = [p.replace("(12,400) (12,400)", "(12,400) (14,400)") for p in PAGES_5B]
        assert _read(pages).refusal == m5b.REFUSE_SELF_CHECK

    def test_cash_columns_must_agree(self) -> None:
        pages = _variant(PAGES_5B, "4.6 Cash and cash equivalents at end of 31,250 31,250",
                         "4.6 Cash and cash equivalents at end of 31,250 28,860")
        assert _read(pages).refusal == m5b.REFUSE_SELF_CHECK

    def test_a_runway_that_is_not_the_stated_ratio_drops_only_the_runway(self) -> None:
        pages = _variant(PAGES_5B, "divided by 5.00", "divided by 9.00")
        r = _read(pages)
        assert r.ok
        assert "issuer_estimated_quarters_of_funding" not in r.fields
        assert "cash_and_equivalents" in r.fields
        assert any("not the stated ratio" in n for n in r.notes)

    def test_an_item_with_the_wrong_number_is_not_read(self) -> None:
        pages = _variant(PAGES_5B, "4.6 Cash and cash equivalents at end of",
                         "4.7 Cash and cash equivalents at end of")
        r = _read(pages)
        assert "cash_and_equivalents" not in r.fields
        assert any("numbers it 4.6" in n for n in r.notes)

    def test_a_4c_runway_printed_as_item_8_7_is_not_read(self) -> None:
        pages = _variant(PAGES_4C, "8.5 Estimated quarters", "8.7 Estimated quarters")
        r = _read(pages)
        assert r.ok and "issuer_estimated_quarters_of_funding" not in r.fields

    def test_labels_and_values_in_separate_blocks_give_no_fact(self) -> None:
        # The platform's own layout blocks print every label first and every value after
        # (the reason this reader uses the PDF's row text): nothing may be paired up.
        labels = [ln for ln in "\n".join(PAGES_5B).split("\n")]
        stripped = [re.sub(r"(\s(\([\d,]+\)|[\d,.]+|-))+$", "", ln) for ln in labels]
        values = ["(5,210)", "(5,210)", "(12,400)", "(12,400)", "31,250", "31,250"]
        r = _read(["\n".join(stripped + values)])
        assert not m5b.statement_facts(r)

    def test_a_negative_or_na_runway_is_not_read(self) -> None:
        pages = _variant(PAGES_5B, "divided by 5.00", "divided by N/A")
        r = _read(pages)
        assert r.ok and "issuer_estimated_quarters_of_funding" not in r.fields

    def test_garbage_never_raises(self) -> None:
        for text in ("", "\x00" * 50, "Appendix 5B quarterly cash flow report", ["", None]):
            r = m5b.read_quarterly_cash_flow_report(text)  # type: ignore[arg-type]
            assert not r.ok


class TestLocator:
    def test_round_trip(self) -> None:
        r = _read(PAGES_5B)
        loc = m5b.locator(r, "4.6")
        assert m5b.parse_locator(loc) == ("appendix_5b", date(2026, 9, 30), "4.6")

    @pytest.mark.parametrize("value", [None, "", "p3:t1", "appendix_9z@2026-09-30#4.6",
                                       "appendix_5b@not-a-date#4.6"])
    def test_anything_else_is_none(self, value) -> None:  # noqa: ANN001
        assert m5b.parse_locator(value) is None


# ── Linear patterns ───────────────────────────────────────────────────────── #

_ADVERSARIAL = (" " * 10000, "\n" * 10000, "A-" * 3000, "a," * 5000, "a." * 3000,
                # the review's patterns: own labels, amounts, balance-date leads, U.S.
                "[for " * 2000, "1," * 5000, "at " * 3000, "U.S. " * 2000,
                "[as of x " * 1000 + "is held", "$1" * 4000)


def _patterns(module) -> list[re.Pattern[str]]:  # noqa: ANN001
    return [v for v in vars(module).values() if isinstance(v, re.Pattern)]


@pytest.mark.parametrize("module", [m5b, ps], ids=["asx_appendix_5b", "period_supersession"])
def test_every_pattern_is_linear(module) -> None:  # noqa: ANN001
    patterns = _patterns(module)
    assert len(patterns) >= 10
    for pattern in patterns:
        for text in _ADVERSARIAL:
            started = time.perf_counter()
            pattern.search(text)
            list(pattern.finditer(text))
            assert time.perf_counter() - started < 0.2, pattern.pattern


@pytest.mark.parametrize("text", _ADVERSARIAL)
def test_the_entry_points_are_fast_on_adversarial_text(text: str) -> None:
    started = time.perf_counter()
    m5b.read_quarterly_cash_flow_report(text)
    m5b.read_quarterly_cash_flow_report("Appendix 5B quarterly cash flow report\n" + text)
    ps.families(text)
    ps.has_own_date(text)
    ps.apply_period_supersession({"findings": [{"finding_id": "f1", "statement": text}],
                                  "chair": {"synthesis": text}}, as_of="2026-11-01")
    assert time.perf_counter() - started < 1.0


# ── The hook ──────────────────────────────────────────────────────────────── #


def _fact(label: str, period: str, value: float, scope: str = "Group"):  # noqa: ANN202
    from app.services.sources.extracted_fact_validator import (
        VALIDATION_VALIDATED,
        ValidatedFact,
    )

    return ValidatedFact(
        label=label, value_numeric=value, value_text=str(value), unit="currency_amount",
        currency="AUD", scale="thousand", period=period, page_number=1,
        table_location="p1:t0", extraction_method="table", confidence=0.6,
        validation_status=VALIDATION_VALIDATED, needs_human_review=True, scope=scope,
    )


class TestHook:
    def test_a_pdf_5b_adds_its_facts_and_drops_the_lesser_reading(self) -> None:
        content = make_pdf(PAGES_5B)
        text_facts = [_fact("cash_and_equivalents", "2027-Q1", 99999.0),
                      _fact("revenue", "FY2026", 10.0),
                      _fact("cash_and_equivalents", "2027-Q1", 1.0, scope="Segment: Mine A")]
        out = m5b.with_quarterly_cash_flow_facts(
            text_facts, content, extraction=SimpleNamespace(blocks=[], excerpts=[]),
            title="Appendix 5B Quarterly Cash Flow Report")
        cash = [f for f in out if f.label == "cash_and_equivalents"]
        assert sorted(f.value_numeric for f in cash) == [1.0, 31250.0]  # segment kept
        assert any(f.label == "revenue" for f in out)
        assert any(f.label == "issuer_estimated_quarters_of_funding" for f in out)

    def test_the_form_pages_come_from_the_extraction(self) -> None:
        content = make_pdf(["Quarterly activities report. Drilling continued.", *PAGES_5B])
        extraction = SimpleNamespace(
            blocks=[SimpleNamespace(text="Appendix 5B", page_number=2)], excerpts=[])
        out = m5b.with_quarterly_cash_flow_facts([], content, extraction=extraction,
                                                 title="Quarterly Activities Report")
        cash = next(f for f in out if f.label == "cash_and_equivalents")
        assert cash.page_number == 4  # page 3 of the form is page 4 of the document

    def test_anything_else_is_returned_unchanged(self) -> None:
        facts = [_fact("revenue", "FY2026", 10.0)]
        extraction = SimpleNamespace(blocks=[], excerpts=[])
        for content, title in ((make_pdf(["Annual report 2026"]), "Annual Report"),
                               (b"not a pdf", "Appendix 5B"),
                               (b"%PDF-1.4 broken", "Appendix 5B"),
                               (None, "Appendix 5B")):
            assert m5b.with_quarterly_cash_flow_facts(
                facts, content, extraction=extraction, title=title) == facts

    def test_the_live_extractor_applies_the_hook(self, monkeypatch) -> None:  # noqa: ANN001
        from app.core.config import Settings
        from app.services.sources import live_fetchers
        from app.services.sources.document_fetcher import DocumentFetchResult

        url = "https://announcements.asx.com.au/asxpdf/20261026/pdf/example.pdf"
        fetched = DocumentFetchResult(
            requested_url=url, final_url=url, status_code=200,
            content_type="application/pdf", document_type="pdf",
            content=make_pdf(PAGES_5B))

        async def fake_fetch(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
            return fetched

        monkeypatch.setattr(live_fetchers, "safe_fetch_document", fake_fetch)
        artifact = asyncio.run(live_fetchers.live_primary_document_extractor(
            url, allowed_domains=("asx.com.au",), title_hint="Appendix 5B",
            cfg=Settings(primary_document_ingestion_enabled=True)))
        assert artifact.status == live_fetchers.STATUS_EXTRACTED
        facts = {(f.label, f.period): f for f in artifact.validated_facts}
        assert facts[("cash_and_equivalents", "2027-Q1")].value_numeric == 31250.0
        assert facts[("issuer_estimated_quarters_of_funding", "2027-Q1")].value_numeric == 5.0


def test_an_older_pipeline_reading_is_re_read() -> None:
    from app.services.sources.disclosures.acquisition import _read_by_an_older_pipeline
    from app.services.sources.extraction_pipeline_version import (
        CURRENT_EXTRACTION_PIPELINE_VERSION,
    )

    assert CURRENT_EXTRACTION_PIPELINE_VERSION >= 24

    class _Session:
        def __init__(self, version: int) -> None:
            self.version = version

        async def get(self, _model, _id):  # noqa: ANN001, ANN202
            return SimpleNamespace(pipeline_version=self.version)

    assert asyncio.run(_read_by_an_older_pipeline(_Session(22), uuid.uuid4())) is True
    # ADR-092 (version 24) re-reads a version-23 row once more; ADR-094 (25) a 24 row.
    assert asyncio.run(_read_by_an_older_pipeline(_Session(23), uuid.uuid4())) is True
    assert asyncio.run(_read_by_an_older_pipeline(_Session(24), uuid.uuid4())) is True
    assert asyncio.run(_read_by_an_older_pipeline(
        _Session(CURRENT_EXTRACTION_PIPELINE_VERSION), uuid.uuid4())) is False


def test_the_runway_field_is_a_statement_field() -> None:
    from app.services.sources.primary_fact_parser import (
        FIELD_ISSUER_ESTIMATED_QUARTERS_OF_FUNDING,
        ISSUER_STATEMENT_FIELDS,
    )

    assert FIELD_ISSUER_ESTIMATED_QUARTERS_OF_FUNDING == m5b.FIELD_RUNWAY
    assert m5b.FIELD_RUNWAY in ISSUER_STATEMENT_FIELDS


# ── The statements view ───────────────────────────────────────────────────── #


def _fact_dicts(pages) -> list[dict]:  # noqa: ANN001
    from app.services.sources.primary_document_extractor import _confidence_bucket

    out = []
    for i, f in enumerate(m5b.statement_facts(_read(pages))):
        out.append({
            "field": f.label, "value": f.value_text, "numeric_value": f.value_numeric,
            "unit": f.unit, "currency": f.currency, "scale": f.scale, "period": f.period,
            "scope": "Group", "source_url": "https://announcements.asx.com.au/x.pdf",
            "page_number": f.page_number, "excerpt_id": f.table_location,
            "confidence": _confidence_bucket(f.confidence), "fact_id": f"fact-{i}",
            "document_id": "doc-1", "document_title": "Appendix 5B",
            "document_date": "2026-10-26", "document_source_type": "official_disclosure",
        })
    return out


class TestStatementsView:
    def test_a_held_5b_yields_facts_read_a_current_period_and_the_issuers_runway(self) -> None:
        from app.services.pipeline.issuer_financials import build_financial_statements_state

        state = build_financial_statements_state(
            _fact_dicts(PAGES_5B), now=datetime(2026, 11, 1, tzinfo=timezone.utc))
        assert state["facts_read"] == 5
        assert state["current_period"]["state"] == "facts_extracted"
        assert state["current_period"]["period"] == "Q1 2027"
        slot = state["slots"]["cash_and_equivalents_current_period"]
        assert slot["value"] == "31,250"
        assert "excerpt=appendix_5b@2026-09-30#4.6" in slot["fact_provenance"]
        (runway,) = state["issuer_stated"]
        assert runway["value"] == 5.0 and runway["as_at"] == "2026-09-30"
        assert runway["provenance"] == "issuer_stated" and runway["period_key"] == "2027-Q1"
        assert "item 8.7" in runway["issuer_label"] and "8.6" in runway["formula"]
        assert "not computed by InvestingBuddy" in runway["note"]
        # The issuer's figure is never one of the platform's own derivations.
        assert all(d.get("definition_key") != m5b.FIELD_RUNWAY
                   for d in state["derived"] or [])

    def test_no_runway_fact_means_no_issuer_stated_record(self) -> None:
        from app.services.pipeline.issuer_financials import build_financial_statements_state

        facts = [f for f in _fact_dicts(PAGES_5B) if f["field"] != m5b.FIELD_RUNWAY]
        assert build_financial_statements_state(facts)["issuer_stated"] == []

    def test_a_runway_not_from_the_form_is_not_issuer_stated(self) -> None:
        assert m5b.issuer_stated_runway(
            {m5b.FIELD_RUNWAY: {"numeric_value": 5.0, "excerpt_id": "p3:t1"}}) == []


# ── Later period supersedes ───────────────────────────────────────────────── #

AS_OF = "2026-11-02"


def _finding(fid: str, label: str, statement: str, **extra) -> dict:  # noqa: ANN003
    return {"finding_id": fid, "label": label, "statement": statement,
            "domain": extra.pop("domain", "financial_capacity"), **extra}


def _payload(findings: list[dict], *, sentences: list[tuple[str, list[str]]] = (),
             synthesis: str = "", **extra) -> dict:  # noqa: ANN003
    return {
        "findings": findings,
        "professional_research": {
            "finding_labels": {f["finding_id"]: f["label"] for f in findings},
            "sections": [{
                "lead": {"text": "", "labels": []},
                "sentences": [{"text": t, "labels": labs} for t, labs in sentences],
            }],
        },
        "chair": {"synthesis": synthesis, "key_points": []},
        **extra,
    }


SEC_FILINGS = {"core_filings": {
    "annual": {"state": "ready", "filing_date": "2026-02-10", "form": "10-K"},
    "quarterly": {"state": "ready", "filing_date": "2026-10-25", "form": "10-Q"},
}}


def _sentences(out: dict) -> list[str]:
    return [s["text"] for s in out["professional_research"]["sections"][0]["sentences"]]


class TestSupersession:
    def test_an_undated_cash_figure_older_than_the_newest_held_interim_is_labelled(self) -> None:
        old = _finding("a1b2c3d4-0000-0000-0000-000000000001", "F1",
                       "The company held cash and equivalents of $4.2bn.",
                       source_published_at="2026-02-10")
        new = _finding("b1b2c3d4-0000-0000-0000-000000000002", "F2",
                       "Total debt stood at $9.8bn.", source_published_at="2026-10-25")
        out, record = ps.apply_period_supersession(_payload([old, new], **SEC_FILINGS),
                                                   as_of=AS_OF)
        assert out["findings"][0]["statement"] == (
            "[as of a source dated 2026-02-10; the issuer's newer 10-Q of 2026-10-25 is "
            "held] The company held cash and equivalents of $4.2bn.")
        assert out["findings"][0]["superseded_period_label"].startswith("[as of ")
        assert out["findings"][1]["statement"] == "Total debt stood at $9.8bn."
        assert record["newest_issuer_periods"]["cash"]["document"] == "10-Q"
        assert [a["rule"] for a in record["labelled"]] == [ps.RULE_BALANCE_AS_OF]

    def test_a_sentence_on_a_dated_net_debt_finding_is_labelled_with_that_date(self) -> None:
        f = _finding("c1b2c3d4-0000-0000-0000-000000000003", "F1",
                     "Net debt was $1.6bn at 31 December 2025.",
                     source_published_at="2026-02-10")
        payload = _payload([f], sentences=[
            ("Leverage is moderate with net debt of $1.6bn.", ["F1"]),
            ("Net debt of $1.6bn at 31 December 2025 is moderate.", ["F1"]),
        ], **SEC_FILINGS)
        out, _ = ps.apply_period_supersession(payload, as_of=AS_OF)
        assert _sentences(out) == [
            "[figures at 31 Dec 2025; the issuer's newer 10-Q of 2026-10-25 is held] "
            "Leverage is moderate with net debt of $1.6bn.",
            # A sentence that prints its own date already says what it is.
            "Net debt of $1.6bn at 31 December 2025 is moderate.",
        ]
        assert out["findings"][0]["statement"] == f["statement"]  # dated: no label

    def test_the_5b_slot_dates_the_newest_cash_exactly(self) -> None:
        f = _finding("d1b2c3d4-0000-0000-0000-000000000004", "F1",
                     "Cash of A$107.9m funds the next phase.",
                     source_published_at="2026-07-28")
        statements = {"financial_statements_state": {"slots": {
            "cash_and_equivalents_current_period": {
                "fact_provenance": ["page=3", "excerpt=appendix_5b@2026-09-30#4.6"],
                "document_title": "Appendix 5B", "document_date": "2026-10-26"}}}}
        out, record = ps.apply_period_supersession(_payload([f], **statements), as_of=AS_OF)
        assert record["newest_issuer_periods"]["cash"]["lower_bound"] == "2026-09-30"
        assert out["findings"][0]["statement"].startswith(
            "[as of a source dated 2026-07-28; the issuer's newer Appendix 5B of 2026-10-26 "
            "is held] ")

    def test_a_third_party_most_recent_fiscal_year_older_than_the_held_annual(self) -> None:
        disclosures = {"core_disclosures": {"documents": [
            {"state": "ready", "filing_date": "2026-09-24", "document_kind": "annual_report",
             "headline": "Annual Report 2026"}]}}
        old = _finding("e1b2c3d4-0000-0000-0000-000000000005", "F1",
                       "In its most recent fiscal year the company reported revenue of A$12m.",
                       source_published_at="2026-02-20", source_kinds=["third_party"])
        fresh = _finding("e2b2c3d4-0000-0000-0000-000000000006", "F2",
                         "In its most recent fiscal year the company reported revenue of A$15m.",
                         source_published_at="2026-10-01", source_kinds=["third_party"])
        out, record = ps.apply_period_supersession(_payload([old, fresh], **disclosures),
                                                   as_of=AS_OF)
        assert out["findings"][0]["statement"].startswith(
            "[as of a source dated 2026-02-20; not the latest fiscal year: the issuer's "
            "newer Annual Report 2026 of 2026-09-24 is held] ")
        assert out["findings"][1]["statement"] == fresh["statement"]
        assert record["labelled"][0]["rule"] == ps.RULE_LATEST_FISCAL_YEAR

    def test_flows_policies_and_figure_less_claims_are_not_labelled(self) -> None:
        texts = [
            "Operating cash flow was $1.1bn.",
            "The company targets net debt below two times EBITDA.",
            "Free cash flow of $800m funds the dividend.",
            "The balance sheet carries no debt.",
            "Cash taxes paid were $90m.",
        ]
        findings = [_finding(f"f{i}b2c3d4-0000-0000-0000-00000000000{i}", f"F{i}", t,
                             source_published_at="2026-02-10")
                    for i, t in enumerate(texts, start=1)]
        out, record = ps.apply_period_supersession(_payload(findings, **SEC_FILINGS),
                                                   as_of=AS_OF)
        assert [f["statement"] for f in out["findings"]] == texts
        assert record["labelled"] == []

    def test_a_figure_over_a_year_old_is_labelled_without_a_newer_document(self) -> None:
        f = _finding("1a2b3c4d-0000-0000-0000-000000000007", "F1",
                     "Net debt was $1.6bn at 31 December 2024.",
                     source_published_at="2025-02-20")
        payload = _payload([f], synthesis=(
            "The copper outlook is constructive. Liquidity appears sufficient with net "
            "debt around $1.6bn (finding_ids: 1a2b3c4d)."))
        out, record = ps.apply_period_supersession(payload, as_of=AS_OF)
        assert out["chair"]["synthesis"] == (
            "The copper outlook is constructive. [figures at 31 Dec 2024; over a year "
            "before this report] Liquidity appears sufficient with net debt around $1.6bn "
            "(finding_ids: 1a2b3c4d).")
        assert record["labelled"][0]["path"] == "chair.synthesis"

    def test_a_recent_figure_is_not_labelled(self) -> None:
        f = _finding("2a2b3c4d-0000-0000-0000-000000000008", "F1",
                     "Net debt was $1.6bn at 30 September 2026.",
                     source_published_at="2026-10-25")
        payload = _payload([f], sentences=[("Net debt of $1.6bn is moderate.", ["F1"])],
                           **SEC_FILINGS)
        out, record = ps.apply_period_supersession(payload, as_of=AS_OF)
        assert _sentences(out) == ["Net debt of $1.6bn is moderate."]
        assert record["labelled"] == []

    def test_a_digit_only_id_is_not_a_finding_id(self) -> None:
        assert ps._strip_ids("Revenue of 12345678 units") == "Revenue of 12345678 units"
        assert "1a2b3c4d" not in ps._strip_ids("(finding_ids: 1a2b3c4d)")

    def test_the_rule_is_idempotent_and_never_raises_on_partial_payloads(self) -> None:
        f = _finding("3a2b3c4d-0000-0000-0000-000000000009", "F1",
                     "The company held cash of $4.2bn.", source_published_at="2026-02-10")
        once, _ = ps.apply_period_supersession(_payload([f], **SEC_FILINGS), as_of=AS_OF)
        twice, record = ps.apply_period_supersession(once, as_of=AS_OF)
        assert twice["findings"] == once["findings"] and record["labelled"] == []
        for partial in ({}, {"findings": None}, {"findings": ["x", 3]},
                        {"professional_research": "x", "chair": None},
                        {"financial_statements_state": {"slots": {"cash_x": "y"}}}):
            ps.apply_period_supersession(partial, as_of=AS_OF)

    def test_the_input_payload_is_not_mutated(self) -> None:
        f = _finding("4a2b3c4d-0000-0000-0000-00000000000a", "F1",
                     "The company held cash of $4.2bn.", source_published_at="2026-02-10")
        payload = _payload([f], **SEC_FILINGS)
        ps.apply_period_supersession(payload, as_of=AS_OF)
        assert payload["findings"][0]["statement"] == "The company held cash of $4.2bn."

    def test_the_label_follows_the_platforms_own_source_labels(self) -> None:
        f = _finding("5a2b3c4d-0000-0000-0000-00000000000b", "F1",
                     "[single source] The company held cash of $4.2bn.",
                     source_published_at="2026-02-10")
        out, _ = ps.apply_period_supersession(_payload([f], **SEC_FILINGS), as_of=AS_OF)
        assert out["findings"][0]["statement"].startswith("[single source] [as of a source ")


class TestSegments:
    def test_an_undated_segment_finding_gets_the_period_its_text_names(self) -> None:
        f = _finding("6a2b3c4d-0000-0000-0000-00000000000c", "F1",
                     "In FY2025 the company reported three segments: Digital Media, Digital "
                     "Experience and Publishing and Advertising.", domain="business_model")
        payload = _payload([f], sentences=[
            ("The company operates three reportable segments.", ["F1"])], **SEC_FILINGS)
        out, record = ps.apply_period_supersession(payload, as_of=AS_OF)
        assert out["findings"][0]["period_key"] == "FY2025"
        assert out["findings"][0]["period_key_source"] == "statement_text"
        assert record["periods_filled"] == [
            {"finding_id": f["finding_id"], "period_key": "FY2025"}]
        assert _sentences(out) == [
            "[segment structure for FY2025; the issuer's newer 10-Q of "
            "2026-10-25 is held] The company operates three reportable segments."]

    def test_no_period_is_filled_from_an_event_on_a_date(self) -> None:
        f = _finding("7a2b3c4d-0000-0000-0000-00000000000d", "F1",
                     "On 3 March 2023 the company reorganised its segments for Q1 2023.",
                     domain="business_model")
        out, record = ps.apply_period_supersession(_payload([f]), as_of=AS_OF)
        assert "period_key" not in out["findings"][0] and record["periods_filled"] == []

    # Review H2 (repros P4, P5, P5b, P6): the run's segment scope keys are no complete,
    # normalised perimeter, so no segment is ever called historical.
    @staticmethod
    def _seg(n: int, scope: str, period: str | None,
             kinds: tuple[str, ...] = ("issuer_filing",), text: str | None = None,
             domain: str = "business_model") -> dict:
        return _finding(f"8a2b3c4{n}-0000-0000-0000-00000000000{n}", f"F{n}",
                        text or f"The {scope.split(':')[1]} segment reported revenue of "
                        "$1.0bn.", domain=domain, scope_key=scope, period_key=period,
                        source_kinds=list(kinds))

    @pytest.mark.parametrize("name", ["P4", "P5", "P5b", "P6"])
    def test_no_segment_is_ever_labelled_historical(self, name: str) -> None:
        seg = self._seg
        findings = {
            # A newer 10-Q finding covers one segment only.
            "P4": [seg(1, "segment:jewellery maisons", "FY2025"),
                   seg(2, "segment:specialist watchmakers", "FY2025"),
                   seg(3, "segment:specialist watchmakers", "2026-Q2")],
            # The same segment under a different scope key.
            "P5": [seg(1, "segment:digital media", "FY2025"),
                   seg(2, "segment:digital media segment", "2026-Q2")],
            # A table header read as a segment.
            "P5b": [seg(1, "segment:jewellery maisons", "FY2025"),
                    seg(2, "segment:(a) information on reportable segments", "FY2026",
                        kinds=("issuer_ir",))],
            # A forecast for one segment.
            "P6": [seg(1, "segment:mining", "FY2026"), seg(2, "segment:processing", "FY2026"),
                   seg(9, "segment:mining", None, kinds=("issuer_ir",),
                       domain="growth_pipeline",
                       text="The mining segment targets output of 50kt in FY2028.")],
        }[name]
        out, record = ps.apply_period_supersession(_payload(findings), as_of=AS_OF)
        assert [f["statement"] for f in out["findings"]] == [f["statement"] for f in findings]
        assert record["labelled"] == []
        assert not hasattr(ps, "RULE_SEGMENT_HISTORICAL")

    def test_a_forward_period_is_never_filled(self) -> None:
        # Review M3 (P6): "targets … in FY2028" is a target, not the finding's period.
        f = self._seg(9, "segment:mining", None, kinds=("issuer_ir",),
                      domain="growth_pipeline",
                      text="The mining segment targets output of 50kt in FY2028.")
        out, record = ps.apply_period_supersession(_payload([f]), as_of=AS_OF)
        assert "period_key" not in out["findings"][0] or out["findings"][0]["period_key"] is None
        assert "period_key_source" not in out["findings"][0]
        assert record["periods_filled"] == []

    def test_a_period_not_ended_by_publication_is_never_filled(self) -> None:
        # Calendar FY2025 ends 31 Dec 2025: a source of 28 Nov 2025 cannot report it whole.
        f = _finding("8a2b3c4a-0000-0000-0000-00000000000a", "F1",
                     "In FY2025 the company reported three segments.",
                     domain="business_model", source_published_at="2025-11-28")
        g = _finding("8a2b3c4b-0000-0000-0000-00000000000b", "F2",
                     "In FY2025 the company reported three segments.",
                     domain="business_model", source_published_at="2026-01-15")
        out, record = ps.apply_period_supersession(_payload([f, g]), as_of=AS_OF)
        assert out["findings"][0].get("period_key") is None
        assert out["findings"][1]["period_key"] == "FY2025"
        assert record["periods_filled"] == [{"finding_id": g["finding_id"],
                                             "period_key": "FY2025"}]

    def test_segments_as_a_verb_is_not_a_segment_claim(self) -> None:
        assert not ps._is_segment_claim("The bank segments its business into four lines.")
        assert ps._is_segment_claim("The bank reports four segments.")


class TestFamilies:
    @pytest.mark.parametrize("text,expected", [
        ("Cash and equivalents of $4.2bn", {"cash"}),
        ("Net cash position of $300m", {"debt"}),
        ("Net cash from operating activities was $300m", set()),
        ("Operating cash flow of $1bn", set()),
        ("Total debt of $9bn", {"debt"}),
        ("Debt financing was arranged", set()),
        ("Total equity of $12bn", {"equity"}),
        ("An equity raise of $50m", set()),
        ("Liquidity of $6bn", {"cash"}),
    ])
    def test_balance_families(self, text: str, expected: set[str]) -> None:
        assert ps.families(text) == expected

    def test_dates_and_periods_in_text(self) -> None:
        assert sorted(ps.text_dates("at 31 December 2024 and Dec 31, 2025 and 2026-06-30")) == [
            date(2024, 12, 31), date(2025, 12, 31), date(2026, 6, 30)]
        # Review M1 (P14): the month-year inside a full date is that date, never the
        # month's end ("15 March 2025" is not 31 Mar 2025).
        assert ps.text_dates("Cash was $500m at 15 March 2025.") == [date(2025, 3, 15)]
        assert ps.text_dates("as of June 2025") == [date(2025, 6, 30)]
        assert set(ps.text_period_keys("Q1 2026, H1 2025, FY24")) == {
            "2026-Q1", "2025-H1", "FY2024"}
        assert not ps.has_own_date("The company holds cash of $4bn.")


# ── The review of PR #326: one test per repro ─────────────────────────────── #

_F1 = "a1b2c3d4-0000-0000-0000-000000000001"
_F2 = "b1b2c3d4-0000-0000-0000-000000000002"
_ANNUAL_2026 = {"core_disclosures": {"documents": [
    {"state": "ready", "filing_date": "2026-09-24", "document_kind": "annual_report",
     "headline": "Annual Report 2026"}]}}


class TestReviewH1LabelsAreNotText:
    def test_a_sentence_is_dated_from_the_original_statement(self) -> None:
        # P12: the cited finding is itself labelled first; its label (which prints the
        # 10-Q's date) must never become the sentence's date.
        f = _finding(_F1, "F1", "The company held cash and equivalents of $4.2bn.",
                     source_published_at="2026-02-10")
        out, _ = ps.apply_period_supersession(_payload(
            [f], sentences=[("Liquidity is ample with cash of $4.2bn.", ["F1"])],
            **SEC_FILINGS), as_of=AS_OF)
        assert _sentences(out) == [
            "[figures as of a source dated 2026-02-10; the issuer's newer 10-Q of 2026-10-25 "
            "is held] Liquidity is ample with cash of $4.2bn."]

    def test_the_lin2_most_recent_fiscal_year_cash_keeps_its_source_date(self) -> None:
        # Real LIN2: the koalagains "cash AUD 3.49m" page of 2026-02-20 was labelled
        # "[figures at 24 Sep 2026…]" (the annual report's date, read from its own label).
        f = _finding(_F1, "F42", "[reported in the press; not from a filing] Lindian "
                     "Resources, most recent fiscal year: cash AUD 3.49m, total debt AUD "
                     "0.2m, operating cash flow AUD -6.07m.",
                     source_published_at="2026-02-20", source_kinds=["secondary_web"])
        out, _ = ps.apply_period_supersession(_payload(
            [f], sentences=[("Cash stood at AUD 3.49m with minimal debt.", ["F42"])],
            **_ANNUAL_2026), as_of=AS_OF)
        assert out["findings"][0]["statement"].startswith(
            "[reported in the press; not from a filing] [as of a source dated 2026-02-20; "
            "not the latest fiscal year: ")
        assert _sentences(out) == [
            "[figures as of a source dated 2026-02-20; the issuer's newer Annual Report 2026 "
            "of 2026-09-24 is held] Cash stood at AUD 3.49m with minimal debt."]

    def test_the_as_of_ignores_this_modules_own_labels(self) -> None:
        labelled = {"statement": "[as of a source dated 2026-02-10; the issuer's newer 10-Q "
                                 "of 2026-10-25 is held] Cash was $4.2bn.",
                    "source_published_at": "2026-02-10"}
        assert ps._finding_as_of(labelled) == (
            date(2026, 2, 10), "as of a source dated 2026-02-10")

    def test_a_finding_labelled_for_its_period_is_never_labelled_twice(self) -> None:
        f = _finding(_F1, "F1", "Cash and equivalents were EUR 150m.", period_key="FY2025",
                     source_published_at="2026-03-30")
        once, _ = ps.apply_period_supersession(_payload([f], **SEC_FILINGS), as_of=AS_OF)
        assert once["findings"][0]["statement"].startswith("[for FY2025; ")
        twice, record = ps.apply_period_supersession(once, as_of=AS_OF)
        assert twice["findings"] == once["findings"] and record["labelled"] == []


class TestReviewM1TheDateOfTheFigures:
    @pytest.mark.parametrize("statement,extra,label", [
        # P1: a forecast year is not the figures' date.
        ("Cash of A$45m funds operations into 2028.",
         {"source_published_at": "2025-03-01"}, "as of a source dated 2025-03-01"),
        # P1b: nor is a maturity date.
        ("Net debt was $2.1bn; the bonds mature on 15 March 2030.",
         {"source_published_at": "2025-03-01"}, "as of a source dated 2025-03-01"),
        # P2: nor a filing date; the period key comes first.
        ("At FY2025 (group, 10-K filed 2026-02-03) shareholders' equity was USD 20,256 "
         "million.", {"period_key": "FY2025"}, "for FY2025"),
        # P14: a day-precise balance date stays that day.
        ("Cash was $500m at 15 March 2025.", {"source_published_at": "2025-04-01"},
         "at 15 Mar 2025"),
        # A balance date after publication is never the figures' date.
        ("Cash was $500m as of 30 June 2026.", {"source_published_at": "2025-04-01"},
         "as of a source dated 2025-04-01"),
        # A forward period key falls back to the source date.
        ("Net debt was $1.0bn.", {"period_key": "2028-H2", "source_published_at":
                                   "2025-04-01"}, "as of a source dated 2025-04-01"),
    ])
    def test_the_label_date(self, statement: str, extra: dict, label: str) -> None:
        assert ps._finding_as_of({"statement": statement, **extra},
                                 date(2026, 11, 2))[1] == label

    @pytest.mark.parametrize("statement,published,sentence,expected", [
        ("Cash of A$45m funds operations into 2028.", "2025-03-01",
         "The company holds a cash position of A$45m.", "as of a source dated 2025-03-01"),
        ("Net debt was $2.1bn; the bonds mature on 15 March 2030.", "2025-03-01",
         "Net debt of $2.1bn is manageable.", "as of a source dated 2025-03-01"),
        ("Cash was $500m at 15 March 2025.", "2025-04-01", "Cash of $500m is ample.",
         "at 15 Mar 2025"),
    ])
    def test_repro_sentences(self, statement: str, published: str, sentence: str,
                             expected: str) -> None:
        f = _finding(_F1, "F1", statement, source_published_at=published)
        out, _ = ps.apply_period_supersession(
            _payload([f], sentences=[(sentence, ["F1"])], **SEC_FILINGS), as_of=AS_OF)
        assert _sentences(out) == [
            f"[figures {expected}; the issuer's newer 10-Q of 2026-10-25 is held] {sentence}"]

    def test_p2_a_filing_date_is_not_the_figures_date(self) -> None:
        f = _finding(_F1, "F1", "At FY2025 (group, 10-K filed 2026-02-03) shareholders' "
                     "equity was USD 20,256 million.", period_key="FY2025")
        out, _ = ps.apply_period_supersession(_payload(
            [f], sentences=[("Shareholders' equity of USD 20.3bn is solid.", ["F1"])],
            **SEC_FILINGS), as_of=AS_OF)
        assert _sentences(out)[0].startswith("[figures for FY2025; ")


class TestReviewM2CurrentFiscalYear:
    def test_the_current_fiscal_year_is_never_superseded(self) -> None:
        # P3: guidance for the year in progress.
        f = _finding(_F1, "F1", "The company expects revenue to grow 10% in the current "
                     "fiscal year (FY2026).", source_published_at="2026-03-01")
        out, record = ps.apply_period_supersession(_payload([f], **_ANNUAL_2026), as_of=AS_OF)
        assert out["findings"][0]["statement"] == f["statement"] and record["labelled"] == []

    def test_the_last_fiscal_year_still_is(self) -> None:
        # P11 (correct before, kept).
        f = _finding(_F1, "F1", "Revenue for the last fiscal year was A$12m.",
                     source_published_at="2026-03-20")
        out, _ = ps.apply_period_supersession(_payload([f], **_ANNUAL_2026), as_of=AS_OF)
        assert out["findings"][0]["statement"].startswith(
            "[as of a source dated 2026-03-20; not the latest fiscal year: the issuer's "
            "newer Annual Report 2026 of 2026-09-24 is held] ")


class TestReviewM4EveryCitedFigure:
    def test_a_newer_cited_figure_means_no_label(self) -> None:
        # PYPL3 sections[7].lead: FY2025 debt with a 2026-Q3 revenue figure; the 2026-Q3
        # figure is not older than the held 10-Q.
        old = _finding(_F1, "F1", "FY2025: long-term debt USD 9,987m vs cash USD 8,049m.",
                       period_key="FY2025")
        new = _finding(_F2, "F2", "Group revenue in 2026-Q3 was $8,584 million.",
                       period_key="2026-Q3")
        out, record = ps.apply_period_supersession(_payload([old, new], sentences=[
            ("The company has manageable debt levels and profitability.", ["F1", "F2"])],
            **SEC_FILINGS), as_of=AS_OF)
        assert _sentences(out) == [
            "The company has manageable debt levels and profitability."]
        assert record["labelled"] == []

    def test_older_figures_of_several_dates_read_up_to_the_latest(self) -> None:
        # HON2 sections[7].lead: FY2025 net debt and a 2026-Q2 backlog, both older than a
        # Q3 10-Q — never "figures for FY2025".
        debt = _finding(_F1, "F1", "FY2025 Group total debt was $34,939m against $12,487m "
                        "cash, net debt $22,452m.", period_key="FY2025")
        backlog = _finding(_F2, "F2", "Backlog increased 12% to $38.0 billion as of June 30, "
                           "2026.", period_key="2026-Q2")
        out, _ = ps.apply_period_supersession(_payload([debt, backlog], sentences=[
            ("Backlog grew, supported by manageable net debt.", ["F1", "F2"])],
            **SEC_FILINGS), as_of=AS_OF)
        assert _sentences(out) == [
            "[figures up to 2026-Q2; the issuer's newer 10-Q of 2026-10-25 is held] Backlog "
            "grew, supported by manageable net debt."]

    def test_a_new_figure_in_the_sentence_means_no_label(self) -> None:
        # P7: "rose to A$31.3m" is a figure no cited finding gives.
        f = _finding(_F1, "F1", "The company held cash of A$3.49m.",
                     source_published_at="2026-02-20")
        text = "Cash rose to A$31.3m at quarter end from A$3.49m (finding_ids: a1b2c3d4)."
        out, record = ps.apply_period_supersession(
            _payload([f], synthesis=text, **SEC_FILINGS), as_of=AS_OF)
        assert out["chair"]["synthesis"] == text
        assert [a["path"] for a in record["labelled"]] == [f"finding:{_F1}"]

    def test_a_peers_figure_never_dates_the_subject(self) -> None:
        # P9: a peer's 30 Jun 2025 became the subject's label.
        own = _finding(_F1, "F1", "Net debt was $1.6bn at 31 December 2024.",
                       source_published_at="2025-02-01")
        peer = _finding(_F2, "F2", "Peer South32 net debt was US$762m at 30 June 2025.",
                        source_published_at="2025-08-28")
        text = ("Net debt of $1.6bn compares with a peer at $0.8bn (finding_ids: a1b2c3d4, "
                "b1b2c3d4).")
        out, _ = ps.apply_period_supersession(
            _payload([own, peer], synthesis=text, **SEC_FILINGS), as_of=AS_OF)
        assert out["chair"]["synthesis"] == text  # the peer's $0.8bn has no subject date
        # Cited beside the subject's own figure only, the peer is ignored.
        text2 = "Net debt of $1.6bn is moderate (finding_ids: a1b2c3d4, b1b2c3d4)."
        out2, _ = ps.apply_period_supersession(
            _payload([own, peer], synthesis=text2, **SEC_FILINGS), as_of=AS_OF)
        assert out2["chair"]["synthesis"].startswith(
            "[figures at 31 Dec 2024; the issuer's newer 10-Q of 2026-10-25 is held] ")

    def test_a_peer_finding_is_never_labelled_with_the_issuers_report(self) -> None:
        peer = _finding(_F2, "F2", "Peer net debt was US$762m.",
                        source_published_at="2025-08-28")
        competitor = _finding(_F1, "F1", "Its largest competitor holds cash of $2bn.",
                              source_published_at="2025-08-28")
        out, record = ps.apply_period_supersession(
            _payload([peer, competitor], **SEC_FILINGS), as_of=AS_OF)
        assert record["labelled"] == []
        assert not ps._about_other_entity({"statement": "Peer-to-peer volume was $1bn."})


class TestReReviewB4PrintedPrecision:
    def test_a_figure_only_near_the_cited_one_is_not_it(self) -> None:
        # B4: A$45.1m is not A$44.0m (it was, within the old flat 5%).
        f = _finding(_F1, "F1", "The company held cash of A$44.0m.",
                     source_published_at="2026-02-10")
        out, record = ps.apply_period_supersession(_payload(
            [f], sentences=[("Cash of A$45.1m funds the next phase.", ["F1"])],
            **SEC_FILINGS), as_of=AS_OF)
        assert _sentences(out) == ["Cash of A$45.1m funds the next phase."]
        assert [a["path"] for a in record["labelled"]] == [f"finding:{_F1}"]

    @pytest.mark.parametrize("stated,figure,same", [
        ("$1.6bn", 1_629.1e6, True),      # real rounding (ANTO)
        ("$4.3bn", 4_316.3e6, True),      # real rounding (ANTO)
        ("USD 20.3bn", 20_256e6, True),
        ("A$45.1m", 44.0e6, False),       # B4
        ("$4.1bn", 4.2e9, False),
        ("$2bn", 1.6e9, True),            # printed to the billion
        ("A$1,629m", 1_629.1e6, True),
        ("A$1,629m", 1_600e6, False),
    ])
    def test_the_sentences_printed_precision(self, stated: str, figure: float,
                                             same: bool) -> None:
        (amount,) = ps._printed_amounts(stated)
        assert ps._same_amount(amount, figure) is same


class TestReReviewB2B3NoLabelOverAnUncitedSentence:
    @pytest.mark.parametrize("before,after", [
        # B2
        ("Revenue grew 8% in the U.S.", "Cash of $4.2bn is ample (finding_ids: a1b2c3d4)."),
        # B3
        ("The company acquired Example Pty Ltd.",
         "Liquidity is ample with cash of $4.2bn (finding_ids: a1b2c3d4)."),
    ])
    def test_the_label_starts_at_the_cited_sentence(self, before: str, after: str) -> None:
        f = _finding(_F1, "F1", "The company held cash of $4.2bn.",
                     source_published_at="2026-02-10")
        out, _ = ps.apply_period_supersession(
            _payload([f], synthesis=f"{before} {after}", **SEC_FILINGS), as_of=AS_OF)
        assert out["chair"]["synthesis"] == (
            f"{before} [figures as of a source dated 2026-02-10; the issuer's newer 10-Q of "
            f"2026-10-25 is held] {after}")

    def test_a_cited_part_before_the_join_is_unplaceable(self) -> None:
        f = _finding(_F1, "F1", "The company held cash of $4.2bn.",
                     source_published_at="2026-02-10")
        text = ("Demand held (finding_ids: b1b2c3d4) in the U.S. Cash of $4.2bn is ample "
                "(finding_ids: a1b2c3d4).")
        out, _ = ps.apply_period_supersession(
            _payload([f], synthesis=text, **SEC_FILINGS), as_of=AS_OF)
        assert out["chair"]["synthesis"] == text


class TestReviewL1SureSentenceStarts:
    @pytest.mark.parametrize("synthesis", [
        # P13
        "Net debt of $1.6bn is below that of U.S. Steel, but debt remains a concern "
        "(finding_ids: a1b2c3d4).",
        # P8
        "Net debt sits near that of U.S. Steel at $1.6bn (finding_ids: a1b2c3d4).",
    ])
    def test_a_label_never_lands_mid_sentence(self, synthesis: str) -> None:
        f = _finding(_F1, "F1", "Net debt was $1.6bn.", source_published_at="2025-02-01")
        out, _ = ps.apply_period_supersession(
            _payload([f], synthesis=synthesis, **SEC_FILINGS), as_of=AS_OF)
        assert out["chair"]["synthesis"] == (
            "[figures as of a source dated 2025-02-01; the issuer's newer 10-Q of "
            f"2026-10-25 is held] {synthesis}")

    @pytest.mark.parametrize("text,end,sure", [
        ("Steel is up. Then", 12, True),
        ("from the U.S. Steel", 13, False),
        ("e.g. Some", 4, False),
        ("Acme Inc. Its", 9, False),
        ("Plan B. It", 7, False),
        ("at $1.6bn. Then", 10, True),
        ("(finding_ids: a1b2c3d4). The", 24, True),
        ("Really? Yes", 7, True),
    ])
    def test_sure_breaks(self, text: str, end: int, sure: bool) -> None:
        assert ps._sure_break(text, end) is sure


class TestReviewL2WhatALabelNames:
    def test_a_6k_is_not_named(self) -> None:
        # P10
        f = _finding(_F1, "F1", "Cash and equivalents were EUR 150m.", period_key="FY2025",
                     source_published_at="2026-03-30")
        out, record = ps.apply_period_supersession(_payload([f], core_filings={
            "annual": {"state": "ready", "filing_date": "2026-03-30", "form": "20-F"},
            "quarterly": {"state": "ready", "filing_date": "2026-10-20", "form": "6-K"}}),
            as_of=AS_OF)
        assert out["findings"][0]["statement"] == (
            "[for FY2025; a newer issuer disclosure of 2026-10-20 is held] Cash and "
            "equivalents were EUR 150m.")
        assert record["newest_issuer_periods"]["cash"]["named"] is False

    def test_an_activities_report_is_not_named_but_the_5b_beside_it_is(self) -> None:
        f = _finding(_F1, "F1", "The company held cash of A$3.49m.",
                     source_published_at="2026-02-20")
        activities = {"state": "ready", "filing_date": "2026-10-25",
                      "document_kind": "results_release", "headline": "September Quarterly "
                      "Report"}
        five_b = {**activities, "headline": "Appendix 5B"}
        alone, _ = ps.apply_period_supersession(_payload(
            [f], core_disclosures={"documents": [activities]}), as_of=AS_OF)
        assert alone["findings"][0]["statement"].startswith(
            "[as of a source dated 2026-02-20; a newer issuer disclosure of 2026-10-25 is "
            "held] ")
        both, _ = ps.apply_period_supersession(_payload(
            [f], core_disclosures={"documents": [activities, five_b]}), as_of=AS_OF)
        assert both["findings"][0]["statement"].startswith(
            "[as of a source dated 2026-02-20; the issuer's newer Appendix 5B of 2026-10-25 "
            "is held] ")

    def test_the_records_document_names_are_neutralised(self) -> None:
        # Review L4: a headline is issuer text; the record passes the same neutralisation
        # as the labels.
        disclosures = {"core_disclosures": {"documents": [
            {"state": "ready", "filing_date": "2026-09-24", "document_kind": "annual_report",
             "headline": "Annual Report 2026 and price target review"}]}}
        _, record = ps.apply_period_supersession(_payload([], **disclosures), as_of=AS_OF)
        documents = [e["document"] for e in record["newest_issuer_periods"].values()]
        assert documents and all("price target" not in d for d in documents)


# ── Wiring into report assembly ───────────────────────────────────────────── #


def test_report_assembly_attaches_the_supersession_record() -> None:
    from app.services.pipeline.v3_pipeline import _enforce_research_state

    f = _finding("aa2b3c4d-0000-0000-0000-000000000001", "F1",
                 "The company held cash of $4.2bn.", source_published_at="2026-02-10")
    report = SimpleNamespace(created_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
                             content_markdown=None, source_summary_json={})
    out = _enforce_research_state(report, {}, _payload([f], **SEC_FILINGS), None)
    assert out["period_supersession"]["version"] == ps.SUPERSESSION_VERSION
    assert out["period_supersession"]["as_of"] == "2026-11-02"
    assert out["findings"][0]["statement"].startswith("[as of a source dated 2026-02-10")
    assert "canonical_state" in out


def test_the_replay_script_lists_every_changed_text() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "qa_replay_period_supersession.py"
    spec = importlib.util.spec_from_file_location("_replay", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    f = _finding("bb2b3c4d-0000-0000-0000-000000000001", "F1",
                 "The company held cash of $4.2bn.", source_published_at="2026-02-10")
    result = module.replay_report({"created_at": AS_OF, "source_summary_json": {
        "v3_research": _payload([f], sentences=[("Cash of $4.2bn is ample.", ["F1"])],
                                **SEC_FILINGS)}})
    assert [c["path"] for c in result["changed"]] == [
        "findings[0]", "sections[0].sentences[0]"]
    assert result["changed"][1]["before"] == "Cash of $4.2bn is ample."
