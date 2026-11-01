"""QA campaign 2026-11, defect JPM-3 — industrial metrics on a bank (ADR-069).

A bank report led its executive synthesis and chair verdict with "operating cash flow
was negative, indicating cash consumption" and "cash conversion -259%", and recorded
missing EBITDA, EBIT, free cash flow and current ratio as open financial categories and
blocking gaps. The playbook steered the questions; every generic producer and gap
counter was sector-agnostic.

The fixtures are generic and shaped like that report: a deposit-taker whose operating
cash flow is a large negative number beside a large positive net income. No ticker is
read anywhere — the company is a bank because its classification says so.
"""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

from app.agents.analysis_council.valuation_guard_agent import run_valuation_guard_agent
from app.agents.research_team.financial_data_agent import (
    financial_data_agent_output_to_dict,
    run_financial_data_agent,
)
from app.agents.research_team.research_completeness_agent import (
    research_completeness_output_to_dict,
    run_research_completeness_agent,
)
from app.services.agent_tools import peers
from app.services.agent_tools.calculations import (
    _get_calculated_metrics,
    validate_get_calculated_metrics,
)
from app.services.calculations.statement_metrics import metrics_from_sec_summary
from app.services.director.planner import plan_research
from app.services.final_research_state import build_final_research_state
from app.services.llm.evidence_pack import _add_sec_fundamentals, _Builder
from app.services.pipeline.professional_research import peer_rows
from app.services.pipeline.v3_pipeline import _PlaybookAdapter
from app.services.playbooks import select
from app.services.playbooks.applicability import (
    STATUS_NOT_MEANINGFUL,
    for_classification,
    for_snapshot,
    resolve,
)
from app.services.playbooks.industries import BANKS_FINANCIALS
from app.services.playbooks.schema import (
    AppliesTo,
    NotMeaningfulMetrics,
    Playbook,
    PlaybookQuestion,
)

CFG = SimpleNamespace(
    v3_agent_tools_enabled=True,
    v3_filings_tool_enabled=True,
    v3_corpus_enabled=True,
    v3_commodity_sources_enabled=True,
)

#: The industrial calculations the defect computed for a bank.
INDUSTRIAL = {
    "cash_conversion",
    "capex_to_ocf",
    "capex_intensity",
    "net_debt",
    "fcf_conversion",
    "fcf_margin",
    "dividend_cover_by_fcf",
}
#: The categories the defect recorded as open for a bank.
INDUSTRIAL_CATEGORIES = {"ebitda", "ebit", "free_cash_flow", "ev_ebitda", "current_ratio"}

# A deposit-taker's year, in USD millions. Net income positive, operating cash flow a
# large negative swing in trading assets and deposits.
BANK_FS = {
    "fiscal_year": 2025,
    "period_basis": "annual",
    "form_type": "10-K",
    "source_tier": "T2_regulator_or_gov",
    "revenue_usd_m": 182_447.0,
    "net_income_usd_m": 57_048.0,
    "operating_cash_flow_usd_m": -147_782.0,
    "capital_expenditures_usd_m": 0.0,
    "free_cash_flow_usd_m": -147_782.0,
    "total_debt_usd_m": 450_000.0,
    "cash_and_equivalents_usd_m": 300_000.0,
    "shareholders_equity_usd_m": 362_438.0,
    "total_assets_usd_m": 4_424_900.0,
    "eps_diluted": 20.02,
    "debt_to_equity": 1.24,
    "return_on_equity_pct": 15.7,
}


def _snapshot(*, sector: str | None, industry: str | None, fs: dict | None = None) -> dict:
    return {
        "company_identity": {
            "legal_name": "Example Holdings",
            "ticker": "EXB",
            "exchange": "NYSE",
            "country_domicile": "US",
        },
        "profile": {
            "sector": sector,
            "industry": industry,
            "reporting_currency": "USD",
            "fiscal_year_end": "12-31",
        },
        "price_history_summary": {"available": True},
        "provider_metadata": {"provider_name": "sec_edgar", "source_tier": "T2_regulator_or_gov"},
        "fundamentals_summary": dict(fs if fs is not None else BANK_FS),
        "missing_fields": [],
        "is_mock": False,
    }


BANK = _snapshot(sector="Financials", industry="National Commercial Banks")
INDUSTRIAL_CO = _snapshot(sector="Industrials", industry="Aerospace & Defense")


# ── The declaration ─────────────────────────────────────────────────────────── #


def test_the_bank_playbook_declares_the_industrial_metrics_not_meaningful():
    declared = BANKS_FINANCIALS.not_meaningful
    assert declared is not None
    assert INDUSTRIAL <= set(declared.calculations)
    assert INDUSTRIAL_CATEGORIES <= set(declared.financial_categories)
    assert BANKS_FINANCIALS.version == 2
    assert BANKS_FINANCIALS.to_dict()["not_meaningful"]["reason"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            dict(
                applies_to=AppliesTo(sectors=("Financials",)),
                calculations=("net_debt",),
                reason="x",
            ),
            "sector alone",
        ),
        (
            dict(
                applies_to=AppliesTo(industries=("Banks",)), calculations=("no_such",), reason="x"
            ),
            "not calculation definitions",
        ),
        (
            dict(
                applies_to=AppliesTo(industries=("Banks",)),
                financial_categories=("no_such",),
                reason="x",
            ),
            "category vocabulary",
        ),
        (
            dict(
                applies_to=AppliesTo(industries=("Banks",)), calculations=("net_debt",), reason=" "
            ),
            "say why",
        ),
    ],
)
def test_a_declaration_that_could_filter_nothing_or_too_much_is_refused(kwargs, message):
    with pytest.raises(ValueError, match=message):
        NotMeaningfulMetrics(**kwargs)


def test_a_playbook_cannot_require_a_metric_it_declares_meaningless():
    with pytest.raises(ValueError, match="cannot both demand"):
        Playbook(
            playbook_id="contradiction",
            version=1,
            display_name="x",
            applies_to=AppliesTo(industries=("Banks",)),
            questions=(
                PlaybookQuestion(
                    key="q",
                    text="?",
                    required_tools=frozenset({"get_calculated_metrics"}),
                    required_calculations=("net_debt",),
                ),
            ),
            risk_framework=("r",),
            not_meaningful=NotMeaningfulMetrics(
                applies_to=AppliesTo(industries=("Banks",)), calculations=("net_debt",), reason="x"
            ),
        )


# ── Resolution: keyed off classification, never a ticker ────────────────────── #


def test_a_bank_resolves_to_not_meaningful_and_records_it_once():
    found = for_classification(sector=None, industry="Banks")
    assert INDUSTRIAL <= found.calculations
    assert INDUSTRIAL_CATEGORIES <= found.financial_categories
    record = found.to_dict()
    assert record["status"] == STATUS_NOT_MEANINGFUL
    assert record["playbooks"] == ["banks_financials"]


#: Real SIC codes of financial companies that are NOT deposit-taking banks. Each keeps
#: every generic metric (review of PR #308: SIC 6311-6411 maps to "Insurance").
KEEPS_METRICS_BY_SIC = [
    ("UNH", "6324"),  # managed care
    ("MMC", "6411"),  # insurance broker
    ("AON", "6411"),  # insurance broker
    ("BRK", "6331"),  # conglomerate filed as fire/marine/casualty insurer
    ("PYPL", "7389"),  # payments
    ("V", "7389"),  # payments network
    ("ICE", "6200"),  # exchange
    ("CME", "6200"),  # exchange
    ("BLK", "6211"),  # asset manager
    ("SCHW", "6211"),  # broker-dealer
    ("AXP", "6199"),  # card issuer
    ("SOFI", "6199"),  # consumer finance
]


def _resolve_by_sic(sic: str):
    """The pipeline's own path: classify from the SIC code, select, resolve."""
    from app.services.classification.resolver import resolve_classification

    c = resolve_classification(sic_code=sic)
    selection = select(sector=c.matching_sector, industry=c.industry)
    return resolve(selection, sector=c.matching_sector, industry=c.industry)


@pytest.mark.parametrize(("ticker", "sic"), KEEPS_METRICS_BY_SIC)
def test_non_bank_financials_keep_every_generic_metric(ticker, sic):
    assert _resolve_by_sic(sic).is_empty, ticker


def test_a_broker_dealers_vendor_label_does_not_override_its_sic_code():
    """SIC 6211 plus a vendor label "Investment Banking & Brokerage" used to infer Banks
    from the word "banking". When a SIC code is present, the code wins."""
    from app.services.classification.resolver import resolve_classification

    c = resolve_classification(
        sic_code="6211",
        sic_description="Security Brokers, Dealers & Flotation Companies",
        stored_industry="Investment Banking & Brokerage",
    )
    assert c.industry != "Banks"
    selection = select(sector=c.matching_sector, industry=c.industry)
    assert resolve(selection, sector=c.matching_sector, industry=c.industry).is_empty
    # With no code at all, the keyword estimate (T6) still applies, as before.
    assert resolve_classification(stored_industry="Commercial Banking").industry == "Banks"


@pytest.mark.parametrize("sic", ["6021", "6022", "6035"])
def test_a_deposit_taking_bank_by_sic_resolves_to_not_meaningful(sic):
    found = _resolve_by_sic(sic)
    assert INDUSTRIAL <= found.calculations
    assert found.questions == frozenset({"cash_generation_and_funding"})
    assert found.analysed_by == ("book_value_per_share", "return_on_equity")
    assert "deposit-taking bank" in found.reader_note


def test_every_declared_not_meaningful_question_is_a_base_question():
    from app.services.director.base_model import BASE_QUESTIONS
    from app.services.playbooks import all_playbooks

    base = {q.key for q in BASE_QUESTIONS}
    for playbook in all_playbooks():
        if playbook.not_meaningful is not None:
            assert set(playbook.not_meaningful.questions) <= base, playbook.playbook_id


def test_insurance_is_not_declared():
    assert for_classification(industry="Insurance").is_empty
    assert BANKS_FINANCIALS.not_meaningful is not None
    assert BANKS_FINANCIALS.not_meaningful.applies_to.industries == ("Banks",)
    assert not BANKS_FINANCIALS.not_meaningful.applies_to.business_model_signals


@pytest.mark.parametrize(
    ("sector", "industry"),
    [
        ("Financials", None),  # a sector-only match
        ("Financials", "Financial Technology"),  # a payments network
        (None, "Semiconductors"),
        (None, "Aerospace & Defense"),
        (None, None),
    ],
)
def test_everything_else_keeps_every_generic_metric(sector, industry):
    assert for_classification(sector=sector, industry=industry).is_empty


def test_a_conglomerate_keeps_the_metrics_one_of_its_methodologies_uses():
    selection = select(explicit_ids=["banks_financials", "industrial_defense"])
    assert resolve(selection, industry="Banks").is_empty


def test_the_snapshot_path_reads_the_providers_words_through_the_classifier():
    """A US filer's profile carries the SIC description, not the canonical industry."""
    assert "cash_conversion" in for_snapshot(BANK).calculations
    assert for_snapshot(INDUSTRIAL_CO).is_empty
    assert for_snapshot(
        _snapshot(sector="Financials", industry="Accident & Health Insurance")
    ).is_empty
    assert for_snapshot({}).is_empty
    assert for_snapshot({"profile": None}).is_empty


# ── V3: the plan ────────────────────────────────────────────────────────────── #


async def _plan_for(industry: str):
    selection = select(industry=industry)
    found = resolve(selection, industry=industry)
    return await plan_research(
        subject="EXB:NYSE",
        mode="deep",
        playbooks=[_PlaybookAdapter(p) for p in selection.playbooks],
        cfg=CFG,
        not_meaningful_calculations=found.calculations,
        not_meaningful_questions=found.questions,
    )


async def test_no_question_of_a_bank_plan_requires_an_industrial_calculation():
    plan = await _plan_for("Banks")
    required = {c for q in plan.questions for c in q.required_calculations}
    assert not required & INDUSTRIAL
    keys = {q.key for q in plan.questions}
    # The cash-generation question is not asked of a deposit-taker. The leverage
    # question stays: for a bank it is answered with the supplementary leverage ratio,
    # which is a capital fact, and it requires no calculation.
    assert "cash_generation_and_funding" not in keys
    assert {"funding_and_deposits", "capital_adequacy", "balance_sheet_risk"} <= keys
    by_key = {q.key: q for q in plan.questions}
    assert by_key["profitability"].required_calculations == ("net_margin",)
    assert by_key["capital_allocation"].required_calculations == ()
    # Recorded once on the plan — not as a gap.
    record = plan.to_dict()
    assert record["not_meaningful_calculations"]["profitability"] == [
        "operating_margin",
        "gross_margin",
    ]
    assert record["not_meaningful_questions"] == ["cash_generation_and_funding"]
    assert not any("not meaningful" in reason for _, reason in plan.unassignable)


@pytest.mark.parametrize("industry", ["Insurance", "Aerospace & Defense"])
async def test_an_insurer_or_industrial_plan_is_unchanged(industry):
    """The bank playbook still MATCHES an insurer; its declaration does not."""
    plan = await _plan_for(industry)
    by_key = {q.key: q for q in plan.questions}
    assert by_key["cash_generation_and_funding"].required_calculations == (
        "cash_conversion",
        "capex_to_ocf",
        "net_debt",
    )
    assert "not_meaningful_calculations" not in plan.to_dict()
    assert "not_meaningful_questions" not in plan.to_dict()


# ── V3: the statement and calculation tools ─────────────────────────────────── #


def _bank_normalised() -> SimpleNamespace:
    return SimpleNamespace(
        fiscal_year=2025,
        period_basis="annual",
        withheld_fields=[],
        consistency={},
        revenue=182_447.0,
        gross_profit=None,
        operating_income=None,
        net_income=57_048.0,
        ebitda=None,
        operating_cash_flow=-147_782.0,
        capital_expenditures=0.0,
        free_cash_flow=-147_782.0,
        dividends_paid=16_625.0,
        total_assets=4_424_900.0,
        total_liabilities=4_062_462.0,
        shareholders_equity=362_438.0,
        cash_and_equivalents=300_000.0,
        short_term_debt=50_000.0,
        long_term_debt=400_000.0,
        total_debt=450_000.0,
        eps_diluted=20.02,
        field_periods={},
        reporting_period_end="2025-12-31",
        form_type="10-K",
        accession_number="0000000000-26-000001",
        filed_date="2026-02-13",
        source_url="https://www.sec.gov/example",
        current_period_facts=[],
        basis_changes=[],
    )


@pytest.fixture
def bank_facts(monkeypatch):
    import app.integrations.sec_fundamentals_normalizer as normalizer

    monkeypatch.setattr(
        normalizer, "normalize_company_facts", lambda facts, ticker: _bank_normalised()
    )
    return {}


def test_before_the_statement_tool_hands_a_bank_its_cash_conversion(bank_facts):
    items, _gaps = peers.statement_items("EXB", bank_facts)
    by_metric = {i["metric_id"]: i for i in items}
    # The defect, reproduced: -259% "cash conversion" with an id the investigator cites.
    assert round(by_metric["cash_conversion"]["value"], 1) == -259.1
    assert "operating_cash_flow" in by_metric


def test_after_the_statement_tool_does_not_compute_or_hand_them_out(bank_facts):
    found = for_classification(industry="Banks")
    items, gaps = peers.statement_items(
        "EXB", bank_facts, found.calculations, found.financial_categories
    )
    metrics = {i["metric_id"] for i in items}
    assert not metrics & INDUSTRIAL
    assert not metrics & {"operating_cash_flow", "capital_expenditures", "free_cash_flow"}
    assert {"net_margin", "revenue", "net_income", "total_assets"} <= metrics
    # Not computed is not refused: no gap line names a not-meaningful metric.
    assert not any(m in g for g in gaps for m in INDUSTRIAL)


async def test_the_calculation_tool_does_not_compute_a_not_meaningful_metric():
    arguments = validate_get_calculated_metrics(
        {
            "company_id": "00000000-0000-0000-0000-000000000001",
            "metrics": ["cash_conversion", "net_debt"],
            "scope": "group",
        }
    )
    context = SimpleNamespace(
        session=None, not_meaningful_calculations=frozenset({"cash_conversion", "net_debt"})
    )
    payload = await _get_calculated_metrics(context, arguments)
    assert payload["items"] == [] and payload["refused"] == []
    assert payload["not_meaningful_for_business_model"] == ["cash_conversion", "net_debt"]


def test_the_peer_table_has_no_column_of_a_not_meaningful_metric():
    payload = {
        "items": [
            {"ticker": "EXB", "metric_id": "net_margin", "value": 31.3, "period": "FY2025"},
            {"ticker": "EXB", "metric_id": "cash_conversion", "value": -259.1},
        ]
    }
    before = peer_rows([payload], "EXB")
    after = peer_rows([payload], "EXB", not_meaningful=INDUSTRIAL)
    assert before[0]["cash_conversion"] == -259.1
    assert "cash_conversion" not in after[0] and "net_debt_usd_m" not in after[0]
    assert after[0]["net_margin_pct"] == 31.3


# ── V2: the council's evidence pack ─────────────────────────────────────────── #


def _pack_text(snapshot: dict) -> str:
    builder = _Builder(max_items=40)
    _add_sec_fundamentals(builder, snapshot, tier_split=True)
    return " ".join(f"{i.title} {i.excerpt}" for i in builder.items)


def test_the_council_is_not_handed_a_banks_cash_conversion():
    assert "cash_conversion" in _pack_text(
        _snapshot(sector="Financials", industry="Semiconductors")
    )
    text = _pack_text(BANK)
    for absent in (
        "cash_conversion",
        "operating_cash_flow_usd_m",
        "free_cash_flow_usd_m",
        "debt_to_equity",
        "[net_debt]",
    ):
        assert absent not in text
    assert "net_income_usd_m" in text


def test_the_statement_metric_helper_honours_the_declaration():
    readings, refusals = metrics_from_sec_summary(BANK_FS, frozenset(INDUSTRIAL))
    keys = {r.key for r in readings} | {r.key for r in refusals}
    assert not keys & INDUSTRIAL
    assert "net_margin" in {r.key for r in readings}


# ── V2: open categories, blocking gaps, valuation inputs ────────────────────── #


def test_a_bank_has_no_open_industrial_financial_category():
    output = run_financial_data_agent(BANK)
    summary = financial_data_agent_output_to_dict(output)
    assert not {f"financials.{c}" for c in INDUSTRIAL_CATEGORIES} & set(summary["missing_fields"])
    assert "financials.debt_to_equity" not in summary["available_fields"]
    assert set(summary["not_meaningful_fields"]) >= {
        f"financials.{c}" for c in INDUSTRIAL_CATEGORIES
    }

    state = build_final_research_state(
        company_snapshot=BANK,
        fundamentals_data=None,
        primary_facts=None,
        financial_data_summary=summary,
    ).to_payload()
    assert not set(state["open_financial_categories"]) & INDUSTRIAL_CATEGORIES
    assert "free_cash_flow" not in state["resolved_financial_categories"]
    assert set(state["not_meaningful_financial_categories"]) >= INDUSTRIAL_CATEGORIES


def test_an_industrial_still_reports_its_missing_ebitda():
    summary = financial_data_agent_output_to_dict(run_financial_data_agent(INDUSTRIAL_CO))
    assert "financials.ebitda" in summary["missing_fields"]
    assert "not_meaningful_fields" not in summary
    state = build_final_research_state(
        company_snapshot=INDUSTRIAL_CO,
        fundamentals_data=None,
        primary_facts=None,
        financial_data_summary=summary,
    ).to_payload()
    assert "ebitda" in state["open_financial_categories"]
    assert "not_meaningful_financial_categories" not in state


def test_a_banks_missing_ebitda_is_not_a_blocking_gap():
    bank = research_completeness_output_to_dict(run_research_completeness_agent(BANK))
    other = research_completeness_output_to_dict(run_research_completeness_agent(INDUSTRIAL_CO))
    assert not any("snapshot_financials.ebitda" in g for g in bank["blocking_gaps"])
    assert "snapshot_financials.ebitda" in bank["not_meaningful_fields"]
    assert any("snapshot_financials.ebitda" in g for g in other["blocking_gaps"])


def test_a_bank_is_not_told_its_valuation_is_blocked_by_missing_ebitda_or_fcf():
    summary = financial_data_agent_output_to_dict(run_financial_data_agent(BANK))
    out = run_valuation_guard_agent(BANK, summary, {"overall_source_quality": "strong"})
    assert "financials.free_cash_flow" not in out.missing_valuation_inputs
    assert "financials.ebitda" not in out.missing_valuation_inputs
    assert any("not meaningful for this business model" in b for b in out.valuation_blockers)

    other = financial_data_agent_output_to_dict(run_financial_data_agent(INDUSTRIAL_CO))
    out = run_valuation_guard_agent(INDUSTRIAL_CO, other, {"overall_source_quality": "strong"})
    assert "financials.ebitda" in out.missing_valuation_inputs


# ── Review of PR #308: leftover industrial signals and replacements ─────────── #


def test_the_financial_context_narrative_carries_no_industrial_metric_for_a_bank():
    bank = run_financial_data_agent(BANK).financial_context_summary
    for absent in (
        "Operating cash flow -",
        "free cash flow -",
        "debt/equity",
        "EBITDA and EV/EBITDA",
    ):
        assert absent not in bank
    assert "not meaningful for this business model" in bank
    other = run_financial_data_agent(INDUSTRIAL_CO).financial_context_summary
    assert "Operating cash flow" in other and "debt/equity" in other


def test_the_facts_tool_does_not_hand_out_a_not_meaningful_label():
    from app.services.agent_tools.facts import _label_filter

    found = for_classification(industry="Banks")
    keeps = _label_filter(
        SimpleNamespace(
            not_meaningful_calculations=found.calculations,
            not_meaningful_categories=found.financial_categories,
        )
    )
    for label in (
        "operating_cash_flow",
        "capital_expenditure",
        "free_cash_flow",
        "ebitda",
        "net_debt",
    ):
        assert not keeps(label), label
    # A European bank's own operating profit is a bank figure, not an industrial EBIT.
    for label in ("net_income", "revenue", "total_equity", "operating_profit"):
        assert keeps(label), label
    assert _label_filter(SimpleNamespace())("operating_cash_flow")


def _history(metric: str) -> list[dict]:
    return [
        {
            "field": metric,
            "value": str(v),
            "numeric_value": v,
            "period": str(y),
            "scope": "group",
            "currency": "USD",
            "scale": "million",
            "unit": None,
            "confidence": "high",
            "source_url": "https://issuer.test/10k.pdf",
            "page_number": 1,
            "table_location": "page=1;table=1;row=1;col=2",
        }
        for y, v in zip(range(2021, 2026), (-50.0, 20.0, -90.0, 10.0, -147.0))
    ]


def _series_titles(snapshot: dict) -> list[str]:
    from app.services.llm.evidence_pack import build_evidence_pack

    pack = build_evidence_pack(
        report_content={},
        company_snapshot=snapshot,
        historical_facts=_history("operating_cash_flow") + _history("net_income"),
    )
    return [
        i.title or "" for i in pack.evidence_items if i.source_type == "historical_financial_series"
    ]


def test_the_council_gets_no_operating_cash_flow_history_for_a_bank():
    assert any("operating_cash_flow" in t for t in _series_titles(INDUSTRIAL_CO))
    titles = _series_titles(BANK)
    assert not any("operating_cash_flow" in t for t in titles)
    assert any("net_income" in t for t in titles)


def test_a_banks_valuation_inputs_are_book_value_and_roe_not_nothing():
    summary = financial_data_agent_output_to_dict(run_financial_data_agent(BANK))
    out = run_valuation_guard_agent(BANK, summary, {"overall_source_quality": "strong"})
    # No platform source yields book value per share yet: an honest missing input.
    assert "financials.book_value_per_share" in out.missing_valuation_inputs
    assert "financials.return_on_equity" in out.available_valuation_inputs
    assert not any(
        "EBITDA" in b for b in out.valuation_blockers if "Valuation conclusion withheld" in b
    )


def test_a_banks_completeness_schema_asks_for_bank_fields_instead():
    bank = research_completeness_output_to_dict(run_research_completeness_agent(BANK))
    gaps = " ".join(bank["non_blocking_gaps"])
    assert "financials_deep.book_value_per_share" in gaps
    assert "financials_deep.return_on_equity" not in gaps  # the snapshot has ROE
    assert "financials_deep.free_cash_flow" not in gaps


def test_the_report_tells_the_investor_why_the_metrics_are_absent():
    from app.services.pipeline import professional_research as pr

    note = for_classification(industry="Banks").reader_note
    report = pr.assemble(
        pr.ReportInputs(
            subject={"ticker": "EXB"}, questions=[], findings=[], not_meaningful_note=note
        )
    )
    by_key = {s["key"]: s for s in report["sections"]}
    assert by_key["financial_capacity"]["metrics_note"] == note
    assert by_key["competitive_position"]["metrics_note"] == note
    plain = pr.assemble(pr.ReportInputs(subject={"ticker": "X"}, questions=[], findings=[]))
    assert not any("metrics_note" in s for s in plain["sections"])


def test_a_banks_free_cash_flow_is_not_counted_as_a_core_valuation_input():
    """The `core_statement_inputs` gate. A summary written before ADR-069 can still
    list FCF as available. For a bank, FCF is not counted towards "partial" readiness."""
    summary = financial_data_agent_output_to_dict(run_financial_data_agent(BANK))
    summary["available_fields"] = [*summary["available_fields"], "financials.free_cash_flow"]
    out = run_valuation_guard_agent(BANK, summary, {"overall_source_quality": "strong"})
    withheld = [b for b in out.valuation_blockers if "Valuation conclusion withheld" in b]
    assert withheld, out.valuation_blockers
    assert not any("free_cash_flow" in b for b in withheld)


# ── After ADR-067: a bank's total debt is a real figure; its net debt is not ──── #

#: REAL JPMorgan Chase companyfacts rows (SEC EDGAR), cut to the concepts the SEC
#: normaliser reads and to FY2025+ rows. The normalised record is identical to the one
#: from the full 8 MB payload. Values are as filed (USD millions after normalising).
JPM_FACTS = pathlib.Path(__file__).parent / "fixtures" / "sec_companyfacts_jpm_fy2025_trimmed.json"


def _jpm_summary(n) -> dict:  # noqa: ANN001
    from app.workflows.snapshot_builder import _SEC_STATEMENT_SLOTS

    fs = {key: getattr(n, field) for key, field in _SEC_STATEMENT_SLOTS.items()}
    fs.update(
        total_debt_usd_m=n.total_debt,
        fiscal_year=n.fiscal_year,
        period_basis=n.period_basis,
        withheld_fields=[{"field": f} for f in n.withheld_fields],
        statement_consistency=n.consistency or {},
    )
    return fs


def test_jpm_total_debt_is_meaningful_and_its_net_debt_is_not():
    from app.integrations.sec_fundamentals_normalizer import normalize_company_facts

    facts = json.loads(JPM_FACTS.read_text())
    n = normalize_company_facts(facts, "JPM")
    # ADR-067: incl-current long-term debt 435,206 + ShortTermBorrowings 64,776.
    assert n.fiscal_year == 2025
    assert n.total_debt == 499_982.0

    bank = for_classification(industry="Banks")
    assert "total_debt" not in bank.financial_categories
    assert bank.keeps_fact_label("total_debt")

    # The statement tool hands out the total, computes no net debt and refuses nothing.
    items, gaps = peers.statement_items("JPM", facts, bank.calculations, bank.financial_categories)
    by_metric = {i["metric_id"]: i for i in items}
    assert by_metric["total_debt"]["value"] == 499_982.0
    assert not {"net_debt", "net_debt_to_ebitda"} & set(by_metric)
    assert not any("net_debt" in g or "net debt" in g.lower() for g in gaps)

    peer_items, peer_gaps = peers._peer_metrics("JPM", facts, bank.calculations)
    assert "net_debt" not in {i["metric_id"] for i in peer_items}
    assert not any("net_debt" in g for g in peer_gaps)

    fs = _jpm_summary(n)
    # Before: the summary helper alone WOULD compute net debt from the complete total.
    assert "net_debt" in {r.key for r in metrics_from_sec_summary(fs)[0]}
    readings, refusals = metrics_from_sec_summary(fs, bank.calculations)
    assert "net_debt" not in {r.key for r in readings}
    assert "net_debt" not in {r.key for r in refusals}

    snapshot = _snapshot(sector="Financials", industry="National Commercial Banks", fs=fs)
    summary = financial_data_agent_output_to_dict(run_financial_data_agent(snapshot))
    assert "financials.total_debt" in summary["available_fields"]
    state = build_final_research_state(
        company_snapshot=snapshot,
        fundamentals_data=None,
        primary_facts=None,
        financial_data_summary=summary,
    ).to_payload()
    assert "total_debt" not in state["open_financial_categories"]
    assert "total_debt" in state["resolved_financial_categories"]
    out = run_valuation_guard_agent(snapshot, summary, {"overall_source_quality": "strong"})
    assert "financials.total_debt" not in out.missing_valuation_inputs
    assert not any("net debt" in b.lower() for b in out.valuation_blockers)


def test_a_bank_label_never_overrides_a_non_bank_sic() -> None:
    from app.services.classification.resolver import resolve_classification

    broker = resolve_classification(
        sic_code="6211", sic_description="Security Brokers, Dealers & Flotation Companies",
        stored_industry="Investment Banking & Brokerage",
    )
    assert broker.industry != "Banks"
