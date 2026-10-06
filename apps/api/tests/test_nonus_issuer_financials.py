"""Item 21 — non-US structured financials and an honest "not reported".

Three situations are never merged: C the issuer's statements were extracted, B the
report was acquired but no validated Group figure came out of it, A nothing was
acquired (and, only with the official listing as evidence, the issuer filed none).

The statement fixtures are SYNTHETIC tables shaped like real UK (£'000, "Total equity and
liabilities", liabilities in brackets) and ASX ($'000 / A$'000, "Loss for the year",
"Net cash used in operating activities", "Interest revenue") statements. No issuer is
named in production code; the issuer names below are placeholders.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.config import settings as CFG
from app.services.calculations.definitions import (
    CASH_RUNWAY_QUARTERS,
    REFUSED_NOT_A_CASH_BURN,
)
from app.services.calculations.engine import (
    REFUSED_CURRENCY_MISMATCH,
    REFUSED_PERIOD_MISMATCH,
    REFUSED_SCOPE_UNKNOWN,
    calculate,
)
from app.services.calculations.quantities import Quantity
from app.services.pipeline.issuer_financials import (
    STATE_ACQUIRED_NOT_EXTRACTED,
    STATE_FACTS_EXTRACTED,
    STATE_NOT_ACQUIRED,
    STATE_NOT_REPORTED_BY_ISSUER,
    build_financial_statements_state,
)
from app.services.report_consistency import audit_report_consistency
from app.services.sources.document_text_extractor import DocumentExcerpt
from app.services.sources.extracted_fact_validator import (
    IssuerContext,
    _match_label,
    validate_extracted_facts,
)
from app.services.sources.extraction_pipeline_version import (
    CURRENT_EXTRACTION_PIPELINE_VERSION,
)
from app.services.sources.fact_scope import GROUP_SCOPE, UNKNOWN_SCOPE
from app.services.sources.financial_period import parse_period
from app.services.sources.primary_document_extractor import (
    ExtractedTable,
    PrimaryDocumentExtraction,
)
from app.services.sources.primary_fact_parser import (
    FINANCIAL_STATEMENT_FIELDS,
    STATEMENT_DETAIL_FIELDS,
    _parse_excerpt,
)

NOW = datetime(2026, 9, 30, tzinfo=UTC)

# --------------------------------------------------------------------------- #
# Statement fixtures
# --------------------------------------------------------------------------- #

ASX_PROFIT_OR_LOSS_AND_CASH_FLOWS = [
    ["", "Note", "30 June 2025 A$'000", "30 June 2024 A$'000"],
    ["Interest revenue", "", "412", "380"],
    ["Administration expenses", "", "(2,140)", "(1,905)"],
    ["Exploration expenditure expensed", "", "(1,020)", "(880)"],
    ["Loss for the year", "", "(3,265)", "(2,410)"],
    ["Net cash used in operating activities", "", "(2,980)", "(2,300)"],
    ["Payments for property, plant and equipment", "", "(450)", "(120)"],
    ["Payments for capitalised exploration and evaluation", "", "(5,200)", "(4,100)"],
    ["Payments for exploration and evaluation", "", "(700)", "(500)"],
    ["Payments for mine development", "", "(900)", "-"],
    ["Net cash used in investing activities", "", "(7,250)", "(4,720)"],
    ["Net cash from financing activities", "", "12,000", "3,000"],
    ["Cash and cash equivalents at the beginning of the financial year", "", "6,100", "9,620"],
    ["Cash and cash equivalents at the end of the financial year", "", "9,470", "6,100"],
]

UK_BALANCE_SHEET = [
    ["", "Note", "2025 £'000", "2024 £'000"],
    ["Cash and cash equivalents", "15", "9,470", "6,100"],
    ["Total current assets", "", "10,200", "7,000"],
    ["Total non-current assets", "", "30,000", "25,000"],
    ["Total assets", "", "40,200", "32,000"],
    ["Total current liabilities", "", "(1,200)", "(900)"],
    ["Borrowings", "18", "(5,000)", "-"],
    ["Total liabilities", "", "(6,200)", "(900)"],
    ["Share capital", "", "12,500", "10,000"],
    ["Issue of share capital", "", "2,500", "1,000"],
    ["Total equity", "", "34,000", "31,100"],
    ["Total equity and liabilities", "", "40,200", "32,000"],
]


def _validate(rows, *, name="EXAMPLE RESOURCES LTD", title_only=True, units_note=None):
    excerpts = []
    if units_note:
        from app.services.sources.primary_document_extractor import PrimaryDocumentExcerpt

        excerpts = [PrimaryDocumentExcerpt(
            excerpt_id="P1", page_number=3, extraction_method="native_pdf",
            confidence=0.9, text=units_note)]
    extraction = PrimaryDocumentExtraction(
        content_hash="x" * 64, mime_type="application/pdf",
        extraction_method="native_pdf", status="extracted",
        tables=[ExtractedTable(table_location="p3:t0", table_index=0, page_number=3,
                               rows=rows, row_count=len(rows), col_count=len(rows[0]))],
        excerpts=excerpts,
    )
    return validate_extracted_facts(
        extraction,
        issuer_context=IssuerContext(company_name=name, bare_dollar_is_usd=False),
        cfg=CFG,
        title_only_period=title_only,
    )


def _by(facts, label, period):
    found = [f for f in facts if f.label == label and f.period == period]
    assert len(found) == 1, (label, period, found)
    return found[0]


# --------------------------------------------------------------------------- #
# Labels, scale, currency and sign
# --------------------------------------------------------------------------- #


class TestAsxStatementShapes:
    def test_loss_outflow_and_spend_lines_are_read_with_the_platform_sign(self):
        facts = _validate(ASX_PROFIT_OR_LOSS_AND_CASH_FLOWS)
        loss = _by(facts, "net_income", "2025")
        assert loss.value_numeric == -3265 and loss.validation_status == "validated"
        assert (loss.currency, loss.scale) == ("AUD", "thousand")
        assert _by(facts, "operating_cash_flow", "2025").value_numeric == -2980
        assert _by(facts, "investing_cash_flow", "2025").value_numeric == -7250
        assert _by(facts, "financing_cash_flow", "2025").value_numeric == 12000
        # Spend lines are the amount spent.
        assert _by(facts, "capital_expenditure", "2025").value_numeric == 450
        assert _by(facts, "administrative_expenses", "2025").value_numeric == 2140
        assert _by(facts, "exploration_expensed", "2025").value_numeric == 1020
        assert _by(facts, "development_expenditure", "2025").value_numeric == 900
        assert _by(facts, "cash_and_equivalents", "2025").value_numeric == 9470

    def test_expensed_capitalised_and_paid_exploration_stay_three_statements(self):
        facts = _validate(ASX_PROFIT_OR_LOSS_AND_CASH_FLOWS)
        assert _by(facts, "exploration_capitalised", "2025").value_numeric == 5200
        assert _by(facts, "exploration_payments", "2025").value_numeric == 700
        assert _by(facts, "exploration_expensed", "2025").value_numeric == 1020
        # A bare caption is none of them: on an ASX balance sheet it is the asset.
        assert _match_label("Exploration and evaluation expenditure") is None
        assert _match_label("Exploration and evaluation assets") is None

    def test_interest_revenue_is_not_revenue(self):
        facts = _validate(ASX_PROFIT_OR_LOSS_AND_CASH_FLOWS)
        assert not [f for f in facts if f.label == "revenue"]
        for caption in ("Interest revenue", "Other revenue", "Finance revenue",
                        "Deferred revenue", "Unearned revenue"):
            assert _match_label(caption) is None, caption

    def test_the_opening_cash_balance_does_not_contradict_the_closing_one(self):
        facts = _validate(ASX_PROFIT_OR_LOSS_AND_CASH_FLOWS)
        cash = [f for f in facts if f.label == "cash_and_equivalents"]
        assert {f.validation_status for f in cash} == {"validated"}
        assert _by(facts, "cash_and_equivalents", "2024").value_numeric == 6100

    def test_bare_dollar_thousands_is_no_currency_for_a_non_us_issuer(self):
        rows = [["", "2025 $'000", "2024 $'000"], ["Loss for the year", "(3,265)", "(2,410)"]]
        loss = _by(_validate(rows), "net_income", "2025")
        assert loss.scale == "thousand" and loss.currency is None
        assert loss.validation_status == "excerpt_only"

    def test_table_units_never_come_from_page_prose_for_an_announcement(self):
        rows = [["", "2025 A$", "2024 A$"], ["Loss for the year", "(3,265,409)", "(2,410,000)"]]
        facts = _validate(rows, units_note="The Group raised A$25 million during the year.")
        loss = _by(facts, "net_income", "2025")
        assert loss.scale is None and loss.validation_status == "excerpt_only"
        # A whole-currency figure with two thousands separators is still a number.
        assert loss.value_numeric == -3265409

    @pytest.mark.parametrize(
        "caption,printed,stored",
        [
            ("(Loss)/profit for the year", "(1,234)", -1234),
            ("(Loss)/profit for the year", "567", 567),
            ("Loss for the year", "1,234", -1234),
            ("Loss after income tax expense for the year", "(1,234)", -1234),
            ("Profit for the year", "(1,234)", -1234),
            ("Net cash used in operating activities", "2,980", -2980),
            ("Net cash (used in)/from operating activities", "2,980", 2980),
            ("Net cash outflow from investing activities", "500", -500),
            ("Payments for property, plant and equipment", "450", 450),
        ],
    )
    def test_the_caption_decides_the_sign(self, caption, printed, stored):
        rows = [["", "2025 A$'000"], [caption, printed]]
        facts = [f for f in _validate(rows) if f.period == "2025"]
        assert len(facts) == 1 and facts[0].value_numeric == stored

    @pytest.mark.parametrize(
        "caption",
        [
            "Total comprehensive loss for the year",
            "Loss before income tax",
            "Operating loss",
            "Loss for the year from discontinued operations",
            "Loss for the year attributable to non-controlling interests",
            "Net loss on disposal of assets",
            "Proceeds from borrowings",
            "Proceeds from sale of property, plant and equipment",
            "Selling, general and administrative expenses",
        ],
    )
    def test_captions_that_are_other_lines_are_not_read(self, caption):
        assert _match_label(caption) not in {
            "net_income", "borrowings", "capital_expenditure", "administrative_expenses"
        }


class TestUkBalanceSheetShapes:
    def test_the_balance_sheet_reconciles_and_every_line_is_read(self):
        facts = _validate(UK_BALANCE_SHEET, name="EXAMPLE PLC")
        assert _by(facts, "total_assets", "2025").validation_status == "validated"
        assert _by(facts, "total_equity", "2025").value_numeric == 34000
        assert _by(facts, "total_equity", "2025").validation_status == "validated"
        # Liabilities printed in brackets are the amount owed.
        assert _by(facts, "total_liabilities", "2025").value_numeric == 6200
        assert _by(facts, "total_current_liabilities", "2025").value_numeric == 1200
        assert _by(facts, "borrowings", "2025").value_numeric == 5000
        assert _by(facts, "total_current_assets", "2025").value_numeric == 10200
        assert _by(facts, "total_non_current_assets", "2025").value_numeric == 30000
        assert _by(facts, "issued_capital", "2025").value_numeric == 12500
        assert _by(facts, "cash_and_equivalents", "2025").currency == "GBP"

    def test_the_balance_sheet_total_is_not_equity(self):
        assert _match_label("Total equity and liabilities") is None
        assert _match_label("Total liabilities and equity") is None

    def test_a_movement_in_share_capital_is_not_the_balance(self):
        assert _match_label("Issue of share capital") is None
        assert _match_label("Contributed equity") == "issued_capital"

    def test_non_current_is_not_current(self):
        assert _match_label("Total non-current assets") == "total_non_current_assets"
        assert _match_label("Total non-current liabilities") is None


class TestProseLoss:
    def _facts(self, text):
        excerpt = DocumentExcerpt(excerpt_id="x", heading=None, text=text, page_number=1,
                                  char_count=len(text), confidence="high")
        return {f.field: f for f in _parse_excerpt(excerpt, None)}

    def test_a_net_loss_is_a_negative_net_income(self):
        f = self._facts("The Group recorded a net loss of A$3.2 million in FY2025.")
        assert f["net_income"].numeric_value == -3.2
        assert (f["net_income"].currency, f["net_income"].scale) == ("AUD", "million")

    def test_a_comparative_aside_does_not_lend_its_year(self):
        f = self._facts(
            "Results for 2025. Loss after tax for the year was £2.5 million "
            "(2024: £1.9 million)."
        )
        assert f["net_income"].numeric_value == -2.5
        assert f["net_income"].period == "2025"

    def test_a_disposal_loss_is_not_net_income(self):
        assert "net_income" not in self._facts("Net loss on disposal of $5 million in 2025.")


def test_the_pipeline_version_advanced_so_reuse_restamps():
    assert CURRENT_EXTRACTION_PIPELINE_VERSION == 19


def test_the_v2_snapshot_vocabulary_is_unchanged():
    """The V2 generator's canonical slots stay byte-identical; the new lines are V3-only."""
    assert not (STATEMENT_DETAIL_FIELDS & FINANCIAL_STATEMENT_FIELDS)
    assert "ebitda" not in STATEMENT_DETAIL_FIELDS | FINANCIAL_STATEMENT_FIELDS


# --------------------------------------------------------------------------- #
# Cash runway
# --------------------------------------------------------------------------- #


def _q(value, period="2025", currency="AUD", scale="thousand", scope=GROUP_SCOPE):
    return Quantity(value=Decimal(str(value)), unit="currency_amount", currency=currency,
                    scale=scale, period=parse_period(period), scope=scope)


class TestCashRunway:
    def test_quarter_equivalents_of_an_annual_burn(self):
        out = calculate(CASH_RUNWAY_QUARTERS, {
            "cash": _q(9470), "operating_cash_flow": _q(-2980), "capital_expenditure": _q(450),
        })
        assert out.computed
        assert float(out.value) == pytest.approx(9470 / ((2980 + 450) / 4))
        assert out.result_unit == "quarters"

    def test_capex_printed_as_an_outflow_gives_the_same_burn(self):
        a = calculate(CASH_RUNWAY_QUARTERS, {
            "cash": _q(9470), "operating_cash_flow": _q(-2980), "capital_expenditure": _q(450)})
        b = calculate(CASH_RUNWAY_QUARTERS, {
            "cash": _q(9470), "operating_cash_flow": _q(-2980), "capital_expenditure": _q(-450)})
        assert a.value == b.value

    def test_a_half_year_burn_is_two_quarters(self):
        out = calculate(CASH_RUNWAY_QUARTERS, {
            "cash": _q(9470, "H1 2026"), "operating_cash_flow": _q(-1490, "H1 2026"),
            "capital_expenditure": _q(0, "H1 2026")})
        assert float(out.value) == pytest.approx(9470 / (1490 / 2))

    def test_refused_on_mixed_periods(self):
        out = calculate(CASH_RUNWAY_QUARTERS, {
            "cash": _q(9470, "H1 2026"), "operating_cash_flow": _q(-2980),
            "capital_expenditure": _q(450)})
        assert out.refused and out.refusal_reason == REFUSED_PERIOD_MISMATCH

    def test_refused_when_nothing_is_burned(self):
        out = calculate(CASH_RUNWAY_QUARTERS, {
            "cash": _q(9470), "operating_cash_flow": _q(3000), "capital_expenditure": _q(450)})
        assert out.refused and out.refusal_reason == REFUSED_NOT_A_CASH_BURN

    def test_refused_on_mixed_currencies_and_unknown_scope(self):
        out = calculate(CASH_RUNWAY_QUARTERS, {
            "cash": _q(9470, currency="GBP"), "operating_cash_flow": _q(-2980),
            "capital_expenditure": _q(450)})
        assert out.refusal_reason == REFUSED_CURRENCY_MISMATCH
        out = calculate(CASH_RUNWAY_QUARTERS, {
            "cash": _q(9470, scope=UNKNOWN_SCOPE), "operating_cash_flow": _q(-2980),
            "capital_expenditure": _q(450)})
        assert out.refusal_reason == REFUSED_SCOPE_UNKNOWN

    def test_it_is_labelled_as_derived(self):
        assert "derived" in CASH_RUNWAY_QUARTERS.label.lower()
        assert "derived" in CASH_RUNWAY_QUARTERS.tags


# --------------------------------------------------------------------------- #
# The A / B / C states
# --------------------------------------------------------------------------- #


def _fact(field, value, period="2025", *, scope="group", confidence="high",
          currency="AUD", scale="thousand", document="d1", title="Annual Report 2025"):
    return {
        "field": field, "value": str(value), "numeric_value": value,
        "unit": "currency_amount", "currency": currency, "scale": scale,
        "period": period, "scope": scope, "confidence": confidence,
        "source_url": f"https://official.example/{document}.pdf", "page_number": 3,
        "excerpt_id": "p3:t0", "fact_id": f"{document}:{field}:{period}:{scope}",
        "document_id": document, "document_title": title, "document_date": "2025-09-30",
    }


def _disclosures(*documents, **extra):
    return {"source_id": "asx_announcements", "documents": list(documents), **extra}


ANNUAL_READY = {"document_kind": "annual_report", "state": "ready",
                "headline": "Annual Report 2025", "filing_date": "2025-09-30",
                "source_url": "https://official.example/ar.pdf", "source_id": "asx_announcements"}


class TestStatementStates:
    def test_c_group_facts_name_the_period(self):
        state = build_financial_statements_state(
            [_fact("cash_and_equivalents", 9470), _fact("net_income", -3265)],
            core_disclosures=_disclosures(ANNUAL_READY), now=NOW)
        assert state["annual"]["state"] == STATE_FACTS_EXTRACTED
        assert state["annual"]["label"] == "FY2025"
        assert state["reporting_periods"]["latest_annual"] == "FY2025"
        assert state["slots"]["net_income_primary_filing"]["numeric_value"] == -3265
        assert state["slots"]["net_income_primary_filing"]["source_tier"] == "T1_primary_filing"

    def test_b_an_acquired_report_with_no_extracted_figure_is_never_not_reported(self):
        state = build_financial_statements_state(
            [], core_disclosures=_disclosures(ANNUAL_READY), now=NOW)
        annual = state["annual"]
        assert annual["state"] == STATE_ACQUIRED_NOT_EXTRACTED
        assert annual["label"] == (
            "FY2025 annual report acquired (2025-09-30) — figures not yet extracted"
        )
        assert "not reported" not in annual["label"].lower()
        assert annual["document"]["source_url"] == "https://official.example/ar.pdf"

    def test_a_full_year_results_announcement_counts_as_the_annual_document(self):
        release = {**ANNUAL_READY, "document_kind": "results_release",
                   "headline": "Preliminary results for the year ended 31 December 2025"}
        state = build_financial_statements_state(
            [], core_disclosures=_disclosures(release), now=NOW)
        assert state["annual"]["state"] == STATE_ACQUIRED_NOT_EXTRACTED

    def test_a_segment_or_subsidiary_fact_never_makes_c(self):
        facts = [_fact("net_income", -3265, scope="Longonjo Project"),
                 _fact("cash_and_equivalents", 9470, scope="Subsidiary Pty Ltd")]
        state = build_financial_statements_state(
            facts, core_disclosures=_disclosures(ANNUAL_READY), now=NOW)
        assert state["annual"]["state"] == STATE_ACQUIRED_NOT_EXTRACTED
        assert state["slots"] == {}
        assert state["reporting_periods"]["latest_annual"] is None

    def test_a_medium_confidence_fact_never_makes_c(self):
        state = build_financial_statements_state(
            [_fact("net_income", -3265, confidence="medium")], now=NOW)
        assert state["annual"]["state"] == STATE_NOT_ACQUIRED

    def test_a_not_acquired_says_nothing_about_the_issuer(self):
        state = build_financial_statements_state([], core_disclosures={}, now=NOW)
        assert state["annual"]["state"] == STATE_NOT_ACQUIRED
        assert state["annual"]["knowledge_state"] == "not_acquired_by_platform"
        assert state["annual"]["label"] == "No annual report acquired"

    def test_not_reported_by_the_issuer_needs_an_18_month_listing(self):
        covered = _disclosures(listing_oldest="2024-12-01", annual_documents_listed=0,
                               listing_complete=True)
        state = build_financial_statements_state([], core_disclosures=covered, now=NOW)
        assert state["annual"]["state"] == STATE_NOT_REPORTED_BY_ISSUER
        assert state["annual"]["knowledge_state"] == "not_disclosed_by_issuer"
        # Six months of listing is not evidence of anything.
        short = _disclosures(listing_oldest="2026-04-01", annual_documents_listed=0)
        assert build_financial_statements_state(
            [], core_disclosures=short, now=NOW)["annual"]["state"] == STATE_NOT_ACQUIRED
        # A listed annual report that was not acquired is not "not reported" either.
        listed = _disclosures(listing_oldest="2024-01-01", annual_documents_listed=1)
        assert build_financial_statements_state(
            [], core_disclosures=listed, now=NOW)["annual"]["state"] == STATE_NOT_ACQUIRED
        # A skipped listing proves nothing.
        skipped = {**covered, "skipped": "source_unavailable"}
        assert build_financial_statements_state(
            [], core_disclosures=skipped, now=NOW)["annual"]["state"] == STATE_NOT_ACQUIRED

    def test_interim_figures_fill_the_current_period_not_the_annual(self):
        state = build_financial_statements_state(
            [_fact("cash_and_equivalents", 7000, "H1 2026")],
            core_disclosures=_disclosures(ANNUAL_READY), now=NOW)
        assert state["current_period"]["state"] == STATE_FACTS_EXTRACTED
        assert state["current_period"]["label"] == "H1 2026"
        assert state["annual"]["state"] == STATE_ACQUIRED_NOT_EXTRACTED
        assert "cash_and_equivalents_current_period" in state["slots"]
        assert "cash_and_equivalents_primary_filing" not in state["slots"]

    def test_two_documents_that_disagree_fill_no_slot(self):
        facts = [_fact("net_income", -3265, document="d1"),
                 _fact("net_income", -4100, document="d2", title="Results")]
        state = build_financial_statements_state(facts, now=NOW)
        assert "net_income_primary_filing" not in state["slots"]
        assert state["conflicts"][0]["field"] == "net_income"
        assert state["annual"]["state"] != STATE_FACTS_EXTRACTED

    def test_documents_that_agree_on_a_rounded_figure_fill_the_slot(self):
        facts = [_fact("net_income", -3265, document="d1"),
                 _fact("net_income", -3.265, scale="million", document="d2")]
        state = build_financial_statements_state(facts, now=NOW)
        assert state["slots"]["net_income_primary_filing"]["numeric_value"] == -3265
        assert state["conflicts"] == []

    def test_no_ebitda_is_ever_derived_for_an_issuer_that_does_not_report_one(self):
        facts = [_fact("net_income", -3265), _fact("operating_cash_flow", -2980),
                 _fact("administrative_expenses", 2140), _fact("ebitda", -3000)]
        state = build_financial_statements_state(facts, now=NOW)
        assert not [key for key in state["slots"] if "ebitda" in key]
        assert "ebitda" not in str(state["derived"]).lower()

    def test_runway_is_derived_from_the_slots_and_labelled_so(self):
        facts = [_fact("cash_and_equivalents", 9470), _fact("operating_cash_flow", -2980),
                 _fact("capital_expenditure", 450)]
        [runway] = build_financial_statements_state(facts, now=NOW)["derived"]
        assert runway["status"] == "computed" and runway["provenance"] == "derived"
        assert runway["value"] == pytest.approx(9470 / ((2980 + 450) / 4))

    def test_runway_over_mixed_periods_is_not_computed(self):
        facts = [_fact("cash_and_equivalents", 9470, "2025"),
                 _fact("operating_cash_flow", -2980, "2024"),
                 _fact("capital_expenditure", 450, "2025")]
        derived = build_financial_statements_state(facts, now=NOW)["derived"]
        # The annual slot set holds each field's LATEST annual fact: FY2025 cash beside
        # FY2024 operating cash flow is refused, never blended.
        assert derived and derived[0]["status"] == "refused"
        assert derived[0]["refusal_reason"] == REFUSED_PERIOD_MISMATCH

    def test_report_consistency_agrees_with_the_states(self):
        facts = [_fact("cash_and_equivalents", 9470), _fact("net_income", -3265),
                 _fact("cash_and_equivalents", 7000, "H1 2026")]
        state = build_financial_statements_state(facts, now=NOW)
        content = {"financial_snapshot": {**state["slots"],
                                          "reporting_periods": state["reporting_periods"]}}
        audit = audit_report_consistency(content)
        period_findings = [f for f in audit.findings
                           if f.invariant in ("interim_as_annual",
                                              "current_period_contradiction")]
        assert period_findings == []


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #


def test_the_outcome_carries_the_statements_state():
    from app.services.pipeline.v3_pipeline import V3ResearchOutcome

    outcome = V3ResearchOutcome(financial_statements={"annual": {"state": "not_acquired"}})
    assert outcome.to_dict()["financial_statements_state"]["annual"]["state"] == "not_acquired"


async def test_a_failed_read_is_recorded_not_raised():
    from app.services.pipeline.issuer_financials import financial_statements_for

    class _Broken:
        async def execute(self, *_a, **_k):  # noqa: ANN202
            raise RuntimeError("database gone")

    class _Company:
        id = "00000000-0000-0000-0000-000000000001"

    state = await financial_statements_for(
        _Broken(), _Company(), core_disclosures=_disclosures(ANNUAL_READY),
        core_filings={}, now=NOW)
    assert state["error"].startswith("statement facts could not be read")
    assert state["annual"]["state"] == STATE_ACQUIRED_NOT_EXTRACTED


def test_listing_coverage_is_recorded_by_the_acquisition():
    from app.services.sources.disclosures.acquisition import _listing_coverage
    from app.services.sources.disclosures.model import DisclosureListing, OfficialDocument

    def doc(kind, when, headline="Quarterly Activities Report"):
        return OfficialDocument(
            source_id="asx_announcements", document_ref=f"r{when}", headline=headline,
            published_at=datetime.fromisoformat(when).replace(tzinfo=UTC),
            venue_category="", category="periodic", doc_kind=kind, research_rank=2,
            price_sensitive=None, media="pdf", official_url="https://official.example/x")

    listing = DisclosureListing(issuer=None, documents=[
        doc("other", "2024-11-02"), doc("results_release", "2026-01-30"),
        doc("results_release", "2025-08-20", "Full Year Results 2025"),
    ])
    coverage = _listing_coverage(listing, 560)
    assert coverage == {"listing_oldest": "2024-11-02", "listing_newest": "2026-01-30",
                        "listing_window_days": 560, "listing_documents": 3,
                        "listing_complete": True, "annual_documents_listed": 1}
    # Review H4 — an ASX "Annual Financial Report" headline and an NSM "Annual Financial
    # Report" filing of any format are evidence the issuer reported a year.
    listing = DisclosureListing(issuer=None, documents=[
        doc("other", "2024-11-02", "Annual Financial Report"),
    ])
    assert _listing_coverage(listing, 560)["annual_documents_listed"] == 1
    nsm = doc("other", "2025-03-02", "Publication of report")
    nsm.venue_category = "Annual Financial Report"
    assert _listing_coverage(DisclosureListing(issuer=None, documents=[nsm]), 560)[
        "annual_documents_listed"] == 1


def test_not_reported_by_issuer_is_reachable_with_the_real_lookback():
    """Review H4 — with the shipped lookback, a listing read to its window start can
    prove "not reported"; a lookback below 18 months never can."""
    from datetime import timedelta

    from app.core.config import settings
    from app.services.pipeline.issuer_financials import NOT_REPORTED_MIN_COVERAGE_DAYS

    lookback = int(settings.v3_disclosure_lookback_days)
    assert lookback >= NOT_REPORTED_MIN_COVERAGE_DAYS
    oldest = (NOW - timedelta(days=lookback - 3)).date().isoformat()
    covered = _disclosures(listing_oldest=oldest, listing_window_days=lookback,
                           listing_documents=12, annual_documents_listed=0,
                           listing_complete=True)
    state = build_financial_statements_state([], core_disclosures=covered, now=NOW)
    assert state["annual"]["state"] == STATE_NOT_REPORTED_BY_ISSUER
    assert "12 documents listed" in state["annual"]["reason"]
    short_window = {**covered, "listing_window_days": 540}
    assert build_financial_statements_state(
        [], core_disclosures=short_window, now=NOW)["annual"]["state"] == STATE_NOT_ACQUIRED


def test_asx_annual_financial_report_is_an_annual_report():
    from app.services.sources.disclosures.relevance import classify_asx

    assert classify_asx(headline="Annual Financial Report", price_sensitive=False)[1] == (
        "annual_report")


class TestReviewRound1Readings:
    """H1, M3, L1, L3, B1 — each against the reading that was wrong."""

    @pytest.mark.parametrize("caption", [
        "Net income (loss)", "Net income/(loss) for the year", "Profit/(loss) for the year",
        "(Loss)/profit for the year",
    ])
    def test_a_profit_under_a_combined_caption_stays_a_profit(self, caption):
        facts = [f for f in _validate([["", "2025 A$'000"], [caption, "1,500"]])
                 if f.label == "net_income"]
        assert len(facts) == 1 and facts[0].value_numeric == 1500

    def test_a_pure_loss_caption_is_still_a_loss(self):
        [loss] = [f for f in _validate([["", "2025 A$'000"], ["Loss for the year", "1,500"]])
                  if f.label == "net_income"]
        assert loss.value_numeric == -1500

    @pytest.mark.parametrize("caption", ["Net current assets", "Net current liabilities",
                                         "Net current assets/(liabilities)"])
    def test_net_current_position_is_not_current_assets_or_liabilities(self, caption):
        assert _match_label(caption) is None

    def test_an_opening_balance_dated_1_july_is_not_cash(self):
        assert _match_label("Cash and cash equivalents at 1 July 2024") is None
        assert _match_label("Cash and cash equivalents at 30 June 2025") == (
            "cash_and_equivalents")

    @pytest.mark.parametrize("caption", [
        "Payments for development of intangible assets", "Payments for development costs",
        "Payments for capitalised development", "Payments for project development",
    ])
    def test_rnd_and_software_development_is_not_mine_development(self, caption):
        assert _match_label(caption) is None

    def test_mine_development_is_read(self):
        assert _match_label("Payments for mine development") == "development_expenditure"

    def test_current_and_non_current_borrowings_are_summed_explicitly(self):
        rows = [["", "2025 £'000", "2024 £'000"],
                ["Borrowings", "(1,000)", "(500)"],
                ["Total current liabilities", "(3,000)", "(2,000)"],
                ["Borrowings", "(4,000)", "-"]]
        facts = {(f.label, f.period): f for f in _validate(rows, name="EXAMPLE PLC")}
        total = facts[("borrowings", "2025")]
        assert total.value_numeric == 5000 and total.validation_status == "validated"
        assert "Sum of the current and the non-current" in total.validation_notes[0]
        # Only one line states 2024: a part, never presented as the total.
        assert facts[("borrowings", "2024")].validation_status == "excerpt_only"

    def test_two_borrowings_lines_without_the_section_marker_stay_unresolved(self):
        rows = [["", "2025 £'000"], ["Borrowings", "(1,000)"], ["Borrowings", "(4,000)"]]
        [b] = [f for f in _validate(rows, name="EXAMPLE PLC") if f.label == "borrowings"]
        assert b.validation_status == "excerpt_only"


class TestRunwaySpend:
    """M2 — a developer's capital spend includes capitalised exploration and mine
    development; exploration payments of unstated treatment make the burn unstatable."""

    BASE = [_fact("cash_and_equivalents", 9470), _fact("operating_cash_flow", -2980),
            _fact("capital_expenditure", 450)]

    def test_capitalised_exploration_and_development_are_in_the_burn(self):
        facts = [*self.BASE, _fact("exploration_capitalised", 5200),
                 _fact("development_expenditure", 900)]
        [runway] = build_financial_statements_state(facts, now=NOW)["derived"]
        assert runway["status"] == "computed"
        assert runway["value"] == pytest.approx(9470 / ((2980 + 450 + 5200 + 900) / 4))
        assert runway["capital_spend_includes"] == [
            "capital_expenditure", "exploration_capitalised", "development_expenditure"]

    def test_exploration_payments_of_unstated_treatment_refuse(self):
        facts = [*self.BASE, _fact("exploration_payments", 700)]
        [runway] = build_financial_statements_state(facts, now=NOW)["derived"]
        assert runway["status"] == "refused" and runway["value"] is None

    def test_a_nine_month_period_is_refused(self):
        from app.services.calculations.definitions import REFUSED_RUNWAY_PERIOD

        out = calculate(CASH_RUNWAY_QUARTERS, {
            "cash": _q(9470, "9M 2025"), "operating_cash_flow": _q(-2980, "9M 2025"),
            "capital_expenditure": _q(450, "9M 2025")})
        # The period model has no nine-month type: refused as an unknown period.
        assert out.refused and out.refusal_reason == "period_unknown"
        # A period it does know but a runway cannot be stated over (a split year).
        out = calculate(CASH_RUNWAY_QUARTERS, {
            "cash": _q(9470, "2025/26"), "operating_cash_flow": _q(-2980, "2025/26"),
            "capital_expenditure": _q(450, "2025/26")})
        assert out.refused and out.refusal_reason == REFUSED_RUNWAY_PERIOD

    def test_the_interpretation_states_what_is_excluded(self):
        assert "exploration" in CASH_RUNWAY_QUARTERS.interpretation


def test_state_b_is_labelled_acquired_not_extracted():
    state = build_financial_statements_state(
        [], core_disclosures=_disclosures(ANNUAL_READY), now=NOW)
    assert state["annual"]["knowledge_state"] == STATE_ACQUIRED_NOT_EXTRACTED
