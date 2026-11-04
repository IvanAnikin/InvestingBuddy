"""Research Integrity Closure E2 (ADR-087) — Appendix 5B / 4C reader, later period
supersedes, segment perimeter.

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

_ADVERSARIAL = (" " * 10000, "\n" * 10000, "A-" * 3000, "a," * 5000, "a." * 3000)


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

    assert CURRENT_EXTRACTION_PIPELINE_VERSION == 23

    class _Session:
        def __init__(self, version: int) -> None:
            self.version = version

        async def get(self, _model, _id):  # noqa: ANN001, ANN202
            return SimpleNamespace(pipeline_version=self.version)

    assert asyncio.run(_read_by_an_older_pipeline(_Session(22), uuid.uuid4())) is True
    assert asyncio.run(_read_by_an_older_pipeline(_Session(23), uuid.uuid4())) is False


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
            "[segment structure as reported for FY2025; the issuer's newer 10-Q of "
            "2026-10-25 is held] The company operates three reportable segments."]

    def test_no_period_is_filled_from_an_event_on_a_date(self) -> None:
        f = _finding("7a2b3c4d-0000-0000-0000-00000000000d", "F1",
                     "On 3 March 2023 the company reorganised its segments for Q1 2023.",
                     domain="business_model")
        out, record = ps.apply_period_supersession(_payload([f]), as_of=AS_OF)
        assert "period_key" not in out["findings"][0] and record["periods_filled"] == []

    def test_a_segment_absent_from_the_newer_perimeter_is_historical(self) -> None:
        def seg(n: int, scope: str, period: str) -> dict:
            return _finding(f"8a2b3c4{n}-0000-0000-0000-00000000000{n}", f"F{n}",
                            f"The {scope.split(':')[1]} segment reported revenue.",
                            domain="business_model", scope_key=scope, period_key=period,
                            source_kinds=["issuer_filing"])

        findings = [seg(1, "segment:digital_media", "FY2024"),
                    seg(2, "segment:publishing", "FY2024"),
                    seg(3, "segment:digital_media", "FY2025"),
                    seg(4, "segment:digital_experience", "FY2025")]
        out, record = ps.apply_period_supersession(_payload(findings), as_of=AS_OF)
        statements = [f["statement"] for f in out["findings"]]
        assert statements[1] == (
            "[historical segment, FY2024: not among the segments the issuer reports for a "
            "later period] The publishing segment reported revenue.")
        assert statements[0] == findings[0]["statement"]  # still reported later
        assert statements[2:] == [findings[2]["statement"], findings[3]["statement"]]
        assert [a["rule"] for a in record["labelled"]] == [ps.RULE_SEGMENT_HISTORICAL]

    def test_a_third_party_segment_claim_never_defines_the_perimeter(self) -> None:
        old = _finding("9a2b3c4d-0000-0000-0000-000000000001", "F1",
                       "The publishing segment reported revenue.", domain="business_model",
                       scope_key="segment:publishing", period_key="FY2024",
                       source_kinds=["issuer_filing"])
        third = _finding("9a2b3c4d-0000-0000-0000-000000000002", "F2",
                         "The media segment grew.", domain="business_model",
                         scope_key="segment:media", period_key="FY2025",
                         source_kinds=["third_party"])
        out, record = ps.apply_period_supersession(_payload([old, third]), as_of=AS_OF)
        assert record["labelled"] == []

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
        # (A day-precise date also reads as its month-year; the set is what counts.)
        assert set(ps.text_dates("at 31 December 2024 and Dec 31, 2025 and 2026-06-30")) == {
            date(2024, 12, 31), date(2025, 12, 31), date(2026, 6, 30)}
        assert set(ps.text_period_keys("Q1 2026, H1 2025, FY24")) == {
            "2026-Q1", "2025-H1", "FY2024"}
        assert not ps.has_own_date("The company holds cash of $4bn.")


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
