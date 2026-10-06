"""Track C review round 2 — the adversarial extraction probe, as regression tests.

Principle: when a period, scale, sign or statement type is ambiguous, NOTHING is
validated (excerpt-only), never a plausible-looking fact. Each test pins a misreading
the probe produced on 6889c27. Synthetic fixtures; no issuer is named.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.config import settings as CFG
from app.services.classification.stage import (
    SIGNAL_DEVELOPMENT_STAGE,
    SIGNAL_RESOURCE_EXTRACTION,
    assess,
    project_terms,
)
from app.services.pipeline.issuer_financials import (
    STATE_NOT_ACQUIRED,
    STATE_NOT_REPORTED_BY_ISSUER,
    build_financial_statements_state,
)
from app.services.sources.document_period import (
    document_period_of,
    title_states_part_year,
)
from app.services.sources.document_text_extractor import DocumentExcerpt
from app.services.sources.extracted_fact_validator import (
    IssuerContext,
    _find_scale,
    _match_label,
    validate_extracted_facts,
)
from app.services.sources.primary_document_extractor import (
    ExtractedTable,
    PrimaryDocumentExcerpt,
    PrimaryDocumentExtraction,
)
from app.services.sources.primary_fact_parser import _find_currency, _parse_excerpt

NOW = datetime(2026, 3, 30, tzinfo=UTC)


def _validate(rows, *, title=None, excerpt=None, bare_usd=False):
    extraction = PrimaryDocumentExtraction(
        content_hash="x" * 64, mime_type="application/pdf", extraction_method="native_pdf",
        status="extracted",
        tables=[ExtractedTable(table_location="p3:t0", table_index=0, page_number=3,
                               rows=rows, row_count=len(rows), col_count=len(rows[0]))],
        excerpts=[PrimaryDocumentExcerpt(excerpt_id="P1", page_number=3,
                                         extraction_method="native_pdf", confidence=0.9,
                                         text=excerpt)] if excerpt else [],
    )
    return validate_extracted_facts(
        extraction,
        issuer_context=IssuerContext(company_name="EXAMPLE LTD", bare_dollar_is_usd=bare_usd),
        cfg=CFG, title_only_period=True, document_title=title)


def _validated(facts, label=None):
    return [f for f in facts if f.validation_status == "validated"
            and (label is None or f.label == label)]


def _prose(text):
    excerpt = DocumentExcerpt(excerpt_id="e1", text=text, page_number=1, confidence="high")
    return {f.field: f for f in _parse_excerpt(excerpt, None)}


# ── H1 — interim statements must not become annual facts ─────────────────────── #


INTERIM_ROWS = [["Loss for the period", "(1,500)", "(1,100)"],
                ["Net cash used in operating activities", "(1,400)", "(900)"]]


class TestInterimIsNeverAnnual:
    @pytest.mark.parametrize("title", [
        "Interim Results", "Half-year Report", "Half Yearly Report", "Half Year Accounts",
        "Appendix 4D and Half Year Financial Report", "Interim Report",
        "Quarterly Activities and Cashflow Report",
    ])
    def test_a_part_year_title_is_recognised(self, title):
        assert title_states_part_year(title)

    @pytest.mark.parametrize("title", ["Annual Report 2025", "Final Results and Interim Dividend",
                                       "Annual Financial Report"])
    def test_an_annual_title_is_not_part_year(self, title):
        assert not title_states_part_year(title)

    @pytest.mark.parametrize("header", ["31 Dec 2025 A$'000", "30 June 2025 £'000"])
    def test_bare_dated_columns_in_a_part_year_document_are_not_annual(self, header):
        rows = [["", header, header.replace("2025", "2024")], *INTERIM_ROWS]
        facts = _validate(rows, title="Half-year Report")
        assert _validated(facts) == []
        # Control: the same table in an annual document is annual.
        assert _validated(_validate(rows, title="Annual Report 2025"), "net_income")

    @pytest.mark.parametrize("header", ["Unaudited 30 June 2025 £'000",
                                        "Nine months to 30 September 2025 £'000",
                                        "Year to date 2025 A$'000"])
    def test_a_part_year_column_header_names_no_annual_period(self, header):
        rows = [["", header, header.replace("2025", "2024")], *INTERIM_ROWS]
        assert not [f for f in _validated(_validate(rows)) if f.period in ("2025", "2024")]

    @pytest.mark.parametrize("header", ["Six months to 30 June 2025 £'000",
                                        "6 months ended 30 June 2025 £'000"])
    def test_a_june_half_is_h1(self, header):
        rows = [["", header, header.replace("2025", "2024")], *INTERIM_ROWS]
        periods = {f.period for f in _validated(_validate(rows), "net_income")}
        assert periods == {"H1 2025", "H1 2024"}

    def test_a_balance_sheet_comparing_two_dates_is_not_annual(self):
        rows = [["", "31 December 2025 A$'000", "30 June 2025 A$'000"],
                ["Cash and cash equivalents", "8,000", "12,000"]]
        assert _validated(_validate(rows)) == []

    def test_a_full_year_comparative_in_an_interim_is_not_promoted(self):
        rows = [["", "6 months to 30 June 2025 £'000", "Year ended 31 December 2024 £'000"],
                ["Loss for the period", "(1,500)", "(3,100)"]]
        facts = _validate(rows, title="Interim Results")
        assert {f.period for f in _validated(facts)} == {"H1 2025"}

    def test_the_runway_is_not_annualised_from_a_half(self):
        """The probe: H1 statements read as FY gave 13.3 quarters instead of 6.7."""
        rows = [["", "31 Dec 2025 A$'000", "31 Dec 2024 A$'000"],
                ["Cash and cash equivalents", "5,000", "6,000"],
                ["Net cash used in operating activities", "(1,400)", "(900)"],
                ["Payments for property, plant and equipment", "(100)", "(50)"]]
        facts = _validate(rows, title="Half Year Accounts")
        assert not any(f.period == "2025" and f.validation_status == "validated" for f in facts)


# ── H2 — a half ending in December is not calendar H1 ────────────────────────── #


class TestJuneYearEndHalves:
    def test_a_december_half_column_gets_no_h1_label(self):
        rows = [["", "Half-year ended 31 December 2025 A$'000",
                 "Half-year ended 31 December 2024 A$'000"], *INTERIM_ROWS]
        assert not [f for f in _validated(_validate(rows)) if f.period == "H1 2025"]

    def test_a_december_half_title_is_not_h1(self):
        assert not document_period_of(title="Half Year Report 31 December 2025", url=None,
                                      extraction=None).is_known
        assert document_period_of(title="Interim Results for the six months ended 30 June 2025",
                                  url=None, extraction=None).label() == "H1 2025"


# ── H3 — a word ending in "m" is not "million" ───────────────────────────────── #


class TestScale:
    @pytest.mark.parametrize("text", ["Net cash from operating activities", "Platinum",
                                      "Long term borrowings", "Item 4", "2025 US$"])
    def test_no_scale_from_a_word(self, text):
        assert _find_scale(text) is None

    @pytest.mark.parametrize("text,scale", [("US$m", "million"), ("£bn", "billion"),
                                            ("in millions of euros", "million"),
                                            ("$A'000", "thousand"), ("5.2m", "million")])
    def test_real_scales(self, text, scale):
        assert _find_scale(text) == scale

    def test_a_whole_dollar_table_is_not_millions(self):
        rows = [["", "2025 US$", "2024 US$"],
                ["Loss for the year", "(3,265,409)", "(2,104,000)"],
                ["Net cash from / (used in) operating activities", "(2,980,100)", "(2,300,000)"]]
        facts = _validate(rows)
        assert all(f.scale is None and f.validation_status != "validated" for f in facts)


# ── H4 — a balance-sheet E&E asset is not spend ──────────────────────────────── #


class TestExplorationAssetIsNotSpend:
    def test_the_asset_line_is_not_exploration_capitalised(self):
        assert _match_label("Capitalised exploration and evaluation expenditure") is None
        assert _match_label("Payments for capitalised exploration and evaluation") == (
            "exploration_capitalised")

    def test_runway_adds_capitalised_exploration_only_from_the_capex_table(self):
        def fact(field, value, table):
            return {"field": field, "numeric_value": value, "unit": "currency_amount",
                    "currency": "AUD", "scale": "thousand", "period": "2025",
                    "scope": "group", "confidence": "high", "document_id": "ar",
                    "excerpt_id": table, "fact_id": field}

        facts = [fact("cash_and_equivalents", 9470, "bs"),
                 fact("operating_cash_flow", -2980, "cf"),
                 fact("capital_expenditure", 450, "cf"),
                 fact("exploration_capitalised", 52300, "bs")]
        [runway] = build_financial_statements_state(facts, now=NOW)["derived"]
        assert runway["status"] == "refused"
        facts[-1]["excerpt_id"] = "cf"
        facts[-1]["numeric_value"] = 5200
        [runway] = build_financial_statements_state(facts, now=NOW)["derived"]
        assert runway["status"] == "computed"


# ── H5 / H7 — prose net income ───────────────────────────────────────────────── #


class TestProseNetIncome:
    def test_a_loss_and_a_profit_in_one_sentence_emit_neither(self):
        f = _prose("Net loss for 2025 was US$3.3m, compared with a net profit of "
                   "US$1.2m in 2024.")
        assert "net_income" not in f

    def test_a_trailing_year_is_the_values_period(self):
        f = _prose("The Group delivered net profit of €1.2 billion in 2024.")
        assert f["net_income"].period == "2024"

    @pytest.mark.parametrize("text", [
        "The Group recorded a net loss before tax of £3.0 million for the year ended "
        "31 December 2025.",
        "Net profit before tax was £4.1 million in 2025.",
    ])
    def test_a_pre_tax_result_is_not_net_income(self, text):
        assert "net_income" not in _prose(text)

    @pytest.mark.parametrize("caption", ["Net loss before tax", "Net profit before taxation",
                                         "Loss before income tax"])
    def test_a_pre_tax_row_is_not_net_income(self, caption):
        assert _match_label(caption) is None


# ── H6 — a listing with a hole proves no absence ─────────────────────────────── #


class TestNotReportedNeedsACompleteListing:
    def _covered(self, **extra):
        oldest = (NOW - timedelta(days=555)).date().isoformat()
        return {"source_id": "asx_announcements", "documents": [], "listing_oldest": oldest,
                "listing_window_days": 560, "listing_documents": 20,
                "annual_documents_listed": 0, **extra}

    def test_complete_listing_reaches_d(self):
        state = build_financial_statements_state(
            [], core_disclosures=self._covered(listing_complete=True), now=NOW)
        assert state["annual"]["state"] == STATE_NOT_REPORTED_BY_ISSUER

    @pytest.mark.parametrize("extra", [{"listing_complete": False}, {}])
    def test_a_failed_page_or_a_refused_row_never_reaches_d(self, extra):
        state = build_financial_statements_state(
            [], core_disclosures=self._covered(**extra), now=NOW)
        assert state["annual"]["state"] == STATE_NOT_ACQUIRED

    def test_coverage_records_failed_pages_and_refusals(self):
        from app.services.sources.disclosures.acquisition import _listing_coverage
        from app.services.sources.disclosures.model import DisclosureListing

        assert _listing_coverage(DisclosureListing(issuer=None, pages_failed=1), 560)[
            "listing_complete"] is False
        assert _listing_coverage(DisclosureListing(issuer=None, refused=2), 560)[
            "listing_complete"] is False
        assert _listing_coverage(DisclosureListing(issuer=None), 560)[
            "listing_complete"] is True

    async def test_a_failed_asx_year_page_is_counted(self):
        from types import SimpleNamespace

        from app.services.sources.disclosures import asx

        pages = iter([SimpleNamespace(ok=True, content=b"<html></html>"),
                      SimpleNamespace(ok=False, content=None),
                      SimpleNamespace(ok=True, content=b"<html></html>")])

        async def fetcher(*_a, **_k):  # noqa: ANN202
            return next(pages)

        issuer = SimpleNamespace(ticker="EXR")

        async def resolve(*_a, **_k):  # noqa: ANN202
            return issuer, None, 0

        orig = asx.resolve_asx_issuer
        asx.resolve_asx_issuer = resolve  # type: ignore[assignment]
        try:
            listing = await asx.list_asx_announcements(
                None, SimpleNamespace(), cfg=SimpleNamespace(v3_disclosure_lookback_days=560),
                fetcher=fetcher, now=datetime(2026, 2, 1, tzinfo=UTC))
        finally:
            asx.resolve_asx_issuer = orig  # type: ignore[assignment]
        assert listing.pages_failed == 1


# ── H8 — the stage needs mining REPORTING, not a name ────────────────────────── #


def _loss_two_years():
    return [{"field": "net_income", "numeric_value": v, "period": p, "scope": "group",
             "confidence": "high", "currency": "AUD", "scale": "thousand",
             "document_id": "ar", "fact_id": p} for p, v in (("2025", -3265), ("2024", -2410))]


class TestMiningEvidenceIsReporting:
    REFINERY = [
        "The lithium hydroxide refinery reached FID after the DFS; a binding offtake "
        "agreement was signed with Mineral Resources Limited.",
        "JORC does not apply to the refinery.",
    ]

    def test_a_company_name_and_a_negated_mention_are_not_mining_evidence(self):
        assert not {"jorc", "mineral_resource"} & set(project_terms(self.REFINERY))
        out = assess(_loss_two_years(), self.REFINERY, has_commodity=True)
        assert SIGNAL_DEVELOPMENT_STAGE not in out.signals
        assert SIGNAL_RESOURCE_EXTRACTION not in out.signals

    @pytest.mark.parametrize("text,term", [
        ("Indicated and Inferred Mineral Resources of 40 Mt, reported in accordance with "
         "the JORC Code.", "mineral_resource"),
        ("The 2025 Ore Reserve statement.", "ore_reserve"),
        ("An NI 43-101 technical report was filed.", "ni_43_101"),
        ("Reported in accordance with the JORC Code.", "jorc"),
    ])
    def test_reporting_context_counts(self, text, term):
        assert term in project_terms([text])


# ── Mediums ──────────────────────────────────────────────────────────────────── #


class TestMediums:
    def test_a_comparative_profit_under_a_loss_caption_keeps_its_sign(self):
        facts = _validate([["", "2025 £'000", "2024 £'000"],
                           ["Loss for the year", "(3,265)", "1,200"]])
        by_period = {f.period: f.value_numeric for f in facts if f.label == "net_income"}
        assert by_period == {"2025": -3265, "2024": 1200}

    def test_an_unbracketed_loss_row_is_still_a_loss(self):
        facts = _validate([["", "2025 £'000"], ["Loss for the financial year", "3,265"]])
        assert [f.value_numeric for f in facts if f.label == "net_income"] == [-3265]

    def test_revenue_and_other_income_yields_to_a_revenue_line(self):
        rows = [["", "2025 A$'000"], ["Revenue from ordinary activities", "412"],
                ["Revenue and other income", "450"]]
        assert [f.value_numeric for f in _validate(rows) if f.label == "revenue"] == [412]

    def test_revenue_and_other_income_alone_is_labelled(self):
        rows = [["", "2025 A$'000"], ["Revenue and other income", "45"]]
        [rev] = [f for f in _validate(rows) if f.label == "revenue"]
        assert "secondary top-line caption" in " ".join(rev.validation_notes)

    @pytest.mark.parametrize("caption", ["Gold sales", "Sale of concentrate", "Sales of nickel"])
    def test_a_producers_sales_caption_is_revenue(self, caption):
        rows = [["", "2025 A$'000"], [caption, "45,000"]]
        assert [f.value_numeric for f in _validate(rows) if f.label == "revenue"] == [45000]

    def test_dollar_a_is_australian_dollars_even_where_bare_dollar_is_usd(self):
        assert _find_currency("Current quarter $A'000") == "AUD"
        rows = [["", "2025 $A'000"], ["Cash and cash equivalents", "5,500"]]
        [cash] = _validate(rows, bare_usd=True)
        assert cash.currency == "AUD" and cash.scale == "thousand"


def test_title_period_unaffected_for_annual_documents():
    assert not document_period_of(title="Annual Report 2025", url=None, extraction=None).is_known
    assert date(2026, 1, 1)  # keep the import honest


# ── Round 3 re-probe ─────────────────────────────────────────────────────────── #


def _validate_ctx(rows, *, title, **context):
    extraction = PrimaryDocumentExtraction(
        content_hash="x" * 64, mime_type="application/pdf", extraction_method="native_pdf",
        status="extracted",
        tables=[ExtractedTable(table_location="p3:t0", table_index=0, page_number=3,
                               rows=rows, row_count=len(rows), col_count=len(rows[0]))])
    return validate_extracted_facts(
        extraction,
        issuer_context=IssuerContext(company_name="EXAMPLE LTD", bare_dollar_is_usd=False,
                                     **context),
        cfg=CFG, title_only_period=True, document_title=title)


DEC_ROWS = [["", "31 Dec 2025 A$'000", "31 Dec 2024 A$'000"],
            ["Loss for the half-year", "(1,500)", "(1,100)"],
            ["Net cash used in operating activities", "(1,400)", "(900)"]]


class TestRound3HalfYearWithAnAtypicalTitle:
    @pytest.mark.parametrize("title", ["Financial Report 31 December 2025", "Accounts"])
    def test_the_listings_part_year_classification_reaches_validation(self, title):
        assert _validated(_validate_ctx(DEC_ROWS, title=title)), "control: no signal"
        assert _validated(_validate_ctx(DEC_ROWS, title=title, part_year_document=True)) == []

    @pytest.mark.parametrize("title", ["Financial Report 31 December 2025", "Accounts"])
    def test_an_asx_december_column_needs_an_annual_signal(self, title):
        assert _validated(_validate_ctx(DEC_ROWS, title=title, venue="AU")) == []

    def test_an_asx_december_year_end_annual_report_still_reads(self):
        facts = _validate_ctx(DEC_ROWS, title="Annual Report 2025", venue="AU")
        assert {f.period for f in _validated(facts)} == {"2025", "2024"}
        rows = [["", "Year ended 31 Dec 2025 A$'000"], ["Loss for the year", "(1,500)"]]
        assert _validated(_validate_ctx(rows, title="Accounts", venue="AU"))

    def test_the_listing_classification_helper(self):
        from app.services.sources.disclosures.acquisition import is_part_year_listing

        assert is_part_year_listing("interim_report", "Accounts")
        assert is_part_year_listing("results_release", "Quarterly Activities Report")
        assert not is_part_year_listing("results_release", "Full Year Results 2025")
        assert not is_part_year_listing("annual_report", "Annual Report")

    async def test_the_cached_path_reads_the_listing_classification(self):
        from types import SimpleNamespace

        from app.services.extracted_document_service import (
            _context_for_document,
            _part_year_by_listing,
        )

        class _Session:
            async def execute(self, *_a, **_k):  # noqa: ANN202
                return SimpleNamespace(scalars=lambda: SimpleNamespace(
                    all=lambda: ["interim_report"]))

        doc = SimpleNamespace(content_hash="h", title="Accounts",
                              source_type="asx_announcement")
        assert await _part_year_by_listing(_Session(), doc)
        assert _context_for_document(doc, None).venue == "AU"

    @pytest.mark.parametrize("title", ["Annual Report 2025 including fourth quarter review",
                                       "Full Year Results compared with the half year"])
    def test_an_annual_title_with_a_part_year_word_is_annual(self, title):
        assert not title_states_part_year(title)


class TestRound3ProseBinding:
    def test_a_trailing_year_after_another_value_is_not_this_values(self):
        f = _prose("Net loss narrowed to £3.2m from £4.0m in 2024.")
        assert f["net_income"].period is None

    @pytest.mark.parametrize("text,period", [
        ("Net loss of £3.2m for the year ended 31 December 2025 compared with £2.1m in 2024.",
         "2025"),
        ("Revenue for 2025 increased to £4.2m (2024: £3.1m), while the loss for the year "
         "was £1.0m in 2025.", "2025"),
        ("Revenue of £4.2m in FY2025 compared with £3.1m in FY2024.", "2025"),
    ])
    def test_the_values_own_year_is_kept(self, text, period):
        facts = _prose(text)
        assert all(f.period == period for k, f in facts.items() if k in ("revenue",
                                                                          "net_income"))
        assert {"revenue", "net_income"} & set(facts)


class TestRound3ThirdPartyMiningText:
    REFINERY_TEXT = [
        "Our lithium hydroxide refinery reached FID after the DFS; we signed a binding "
        "offtake agreement.",
        "The refinery takes feedstock from a supplier whose Ore Reserve was reported in "
        "accordance with the JORC Code.",
        "MinRes reported a Mineral Resource estimate for its mine; we are its offtake partner.",
    ]

    def test_third_party_reporting_is_not_the_issuers_mining_evidence(self):
        out = assess(_loss_two_years(), self.REFINERY_TEXT, has_commodity=True,
                     issuer_name="Example Lithium Limited")
        assert out.signals == ()

    def test_one_attributed_sentence_needs_a_mining_classification(self):
        from app.services.classification.stage import attributed_code_sentences

        texts = ["Example Lithium's Mineral Resource estimate was updated in 2025, "
                 "ahead of the DFS."]
        assert attributed_code_sentences(texts, "Example Lithium Limited") == 1
        facts = [*_loss_two_years(), {"field": "exploration_capitalised",
                                      "numeric_value": 500, "period": "2025"}]
        assert assess(facts, texts, has_commodity=True,
                      issuer_name="Example Lithium Limited").signals == ()
        assert SIGNAL_DEVELOPMENT_STAGE in assess(
            facts, texts, has_commodity=True, issuer_name="Example Lithium Limited",
            mining_sector=True).signals


class TestRound3HeaderScale:
    def test_a_data_cell_does_not_scale_the_table(self):
        rows = [["", "2025 US$", "2024 US$"],
                ["Loss for the year", "(3,265,409)", "(2,104,000)"],
                ["Loans repayable within 12m", "500,000", "-"],
                ["Shares (millions)", "120", "100"]]
        facts = _validate(rows)
        assert all(f.scale is None for f in facts)

    def test_a_header_units_cell_still_scales(self):
        rows = [["(in millions of euros)", "2025", "2024"], ["Revenue", "12,000", "10,000"]]
        assert {f.scale for f in _validate(rows) if f.label == "revenue"} == {"million"}
