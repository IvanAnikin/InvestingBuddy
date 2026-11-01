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
INDUSTRIAL = {"cash_conversion", "capex_to_ocf", "capex_intensity", "net_debt",
              "fcf_conversion", "fcf_margin", "dividend_cover_by_fcf"}
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
        "company_identity": {"legal_name": "Example Holdings", "ticker": "EXB",
                             "exchange": "NYSE", "country_domicile": "US"},
        "profile": {"sector": sector, "industry": industry,
                    "reporting_currency": "USD", "fiscal_year_end": "12-31"},
        "price_history_summary": {"available": True},
        "provider_metadata": {"provider_name": "sec_edgar", "source_tier":
                              "T2_regulator_or_gov"},
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
        (dict(applies_to=AppliesTo(sectors=("Financials",)), calculations=("net_debt",),
              reason="x"), "sector alone"),
        (dict(applies_to=AppliesTo(industries=("Banks",)), calculations=("no_such",),
              reason="x"), "not calculation definitions"),
        (dict(applies_to=AppliesTo(industries=("Banks",)),
              financial_categories=("no_such",), reason="x"), "category vocabulary"),
        (dict(applies_to=AppliesTo(industries=("Banks",)), calculations=("net_debt",),
              reason=" "), "say why"),
    ],
)
def test_a_declaration_that_could_filter_nothing_or_too_much_is_refused(kwargs, message):
    with pytest.raises(ValueError, match=message):
        NotMeaningfulMetrics(**kwargs)


def test_a_playbook_cannot_require_a_metric_it_declares_meaningless():
    with pytest.raises(ValueError, match="cannot both demand"):
        Playbook(
            playbook_id="contradiction", version=1, display_name="x",
            applies_to=AppliesTo(industries=("Banks",)),
            questions=(PlaybookQuestion(
                key="q", text="?", required_tools=frozenset({"get_calculated_metrics"}),
                required_calculations=("net_debt",)),),
            risk_framework=("r",),
            not_meaningful=NotMeaningfulMetrics(
                applies_to=AppliesTo(industries=("Banks",)),
                calculations=("net_debt",), reason="x"),
        )


# ── Resolution: keyed off classification, never a ticker ────────────────────── #


def test_a_bank_resolves_to_not_meaningful_and_records_it_once():
    found = for_classification(sector=None, industry="Banks")
    assert INDUSTRIAL <= found.calculations
    assert INDUSTRIAL_CATEGORIES <= found.financial_categories
    record = found.to_dict()
    assert record["status"] == STATUS_NOT_MEANINGFUL
    assert record["playbooks"] == ["banks_financials"]


def test_an_insurer_resolves_the_same_way():
    assert "cash_conversion" in for_classification(industry="Insurance").calculations


@pytest.mark.parametrize(
    ("sector", "industry"),
    [
        ("Financials", None),                    # a sector-only match
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
    assert for_snapshot({}).is_empty
    assert for_snapshot({"profile": None}).is_empty


# ── V3: the plan ────────────────────────────────────────────────────────────── #


async def _bank_plan(*, with_declaration: bool):
    selection = select(industry="Banks")
    found = resolve(selection, industry="Banks")
    return await plan_research(
        subject="EXB:NYSE", mode="deep",
        playbooks=[_PlaybookAdapter(p) for p in selection.playbooks], cfg=CFG,
        not_meaningful_calculations=found.calculations if with_declaration else (),
    )


async def test_no_question_of_a_bank_plan_requires_an_industrial_calculation():
    plan = await _bank_plan(with_declaration=True)
    required = {c for q in plan.questions for c in q.required_calculations}
    assert not required & INDUSTRIAL
    keys = {q.key for q in plan.questions}
    # The bank's funding question replaces the industrial cash-generation one. The
    # leverage question stays: for a bank it is answered with the supplementary leverage
    # ratio, which is a capital fact, and it requires no calculation.
    assert "cash_generation_and_funding" not in keys
    assert {"funding_and_deposits", "capital_adequacy", "balance_sheet_risk"} <= keys
    by_key = {q.key: q for q in plan.questions}
    assert by_key["profitability"].required_calculations == ("net_margin",)
    assert by_key["capital_allocation"].required_calculations == ()
    # Recorded once on the plan, by question — not as a gap.
    record = plan.to_dict()["not_meaningful_calculations"]
    assert record["profitability"] == ["operating_margin", "gross_margin"]
    assert record["capital_allocation"] == ["dividend_cover_by_fcf"]
    assert not any("not meaningful" in reason for _, reason in plan.unassignable)


async def test_an_industrial_plan_is_unchanged():
    selection = select(industry="Aerospace & Defense")
    plan = await plan_research(
        subject="ANY:US", mode="deep",
        playbooks=[_PlaybookAdapter(p) for p in selection.playbooks], cfg=CFG,
        not_meaningful_calculations=resolve(
            selection, industry="Aerospace & Defense").calculations,
    )
    by_key = {q.key: q for q in plan.questions}
    assert by_key["cash_generation_and_funding"].required_calculations == (
        "cash_conversion", "capex_to_ocf", "net_debt")
    assert "not_meaningful_calculations" not in plan.to_dict()


# ── V3: the statement and calculation tools ─────────────────────────────────── #


def _bank_normalised() -> SimpleNamespace:
    return SimpleNamespace(
        fiscal_year=2025, period_basis="annual", withheld_fields=[], consistency={},
        revenue=182_447.0, gross_profit=None, operating_income=None, net_income=57_048.0,
        ebitda=None, operating_cash_flow=-147_782.0, capital_expenditures=0.0,
        free_cash_flow=-147_782.0, dividends_paid=16_625.0, total_assets=4_424_900.0,
        total_liabilities=4_062_462.0, shareholders_equity=362_438.0,
        cash_and_equivalents=300_000.0, short_term_debt=50_000.0,
        long_term_debt=400_000.0, total_debt=450_000.0, eps_diluted=20.02,
        field_periods={}, reporting_period_end="2025-12-31", form_type="10-K",
        accession_number="0000000000-26-000001", filed_date="2026-02-13",
        source_url="https://www.sec.gov/example", current_period_facts=[],
        basis_changes=[],
    )


@pytest.fixture
def bank_facts(monkeypatch):
    import app.integrations.sec_fundamentals_normalizer as normalizer

    monkeypatch.setattr(normalizer, "normalize_company_facts",
                        lambda facts, ticker: _bank_normalised())
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
        "EXB", bank_facts, found.calculations, found.financial_categories)
    metrics = {i["metric_id"] for i in items}
    assert not metrics & INDUSTRIAL
    assert not metrics & {"operating_cash_flow", "capital_expenditures", "free_cash_flow"}
    assert {"net_margin", "revenue", "net_income", "total_assets"} <= metrics
    # Not computed is not refused: no gap line names a not-meaningful metric.
    assert not any(m in g for g in gaps for m in INDUSTRIAL)


async def test_the_calculation_tool_does_not_compute_a_not_meaningful_metric():
    arguments = validate_get_calculated_metrics({
        "company_id": "00000000-0000-0000-0000-000000000001",
        "metrics": ["cash_conversion", "net_debt"], "scope": "group",
    })
    context = SimpleNamespace(
        session=None, not_meaningful_calculations=frozenset({"cash_conversion", "net_debt"}))
    payload = await _get_calculated_metrics(context, arguments)
    assert payload["items"] == [] and payload["refused"] == []
    assert payload["not_meaningful_for_business_model"] == ["cash_conversion", "net_debt"]


def test_the_peer_table_has_no_column_of_a_not_meaningful_metric():
    payload = {"items": [
        {"ticker": "EXB", "metric_id": "net_margin", "value": 31.3, "period": "FY2025"},
        {"ticker": "EXB", "metric_id": "cash_conversion", "value": -259.1},
    ]}
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
    assert "cash_conversion" in _pack_text(_snapshot(sector="Financials",
                                                     industry="Semiconductors"))
    text = _pack_text(BANK)
    for absent in ("cash_conversion", "operating_cash_flow_usd_m",
                   "free_cash_flow_usd_m", "debt_to_equity", "[net_debt]"):
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
    assert not {f"financials.{c}" for c in INDUSTRIAL_CATEGORIES} & set(
        summary["missing_fields"])
    assert "financials.debt_to_equity" not in summary["available_fields"]
    assert set(summary["not_meaningful_fields"]) >= {
        f"financials.{c}" for c in INDUSTRIAL_CATEGORIES}

    state = build_final_research_state(
        company_snapshot=BANK, fundamentals_data=None, primary_facts=None,
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
        company_snapshot=INDUSTRIAL_CO, fundamentals_data=None, primary_facts=None,
        financial_data_summary=summary,
    ).to_payload()
    assert "ebitda" in state["open_financial_categories"]
    assert "not_meaningful_financial_categories" not in state


def test_a_banks_missing_ebitda_is_not_a_blocking_gap():
    bank = research_completeness_output_to_dict(run_research_completeness_agent(BANK))
    other = research_completeness_output_to_dict(
        run_research_completeness_agent(INDUSTRIAL_CO))
    assert not any("snapshot_financials.ebitda" in g for g in bank["blocking_gaps"])
    assert "snapshot_financials.ebitda" in bank["not_meaningful_fields"]
    assert any("snapshot_financials.ebitda" in g for g in other["blocking_gaps"])


def test_a_bank_is_not_told_its_valuation_is_blocked_by_missing_ebitda_or_fcf():
    summary = financial_data_agent_output_to_dict(run_financial_data_agent(BANK))
    out = run_valuation_guard_agent(BANK, summary, {"overall_source_quality": "strong"})
    assert "financials.free_cash_flow" not in out.missing_valuation_inputs
    assert "financials.ebitda" not in out.missing_valuation_inputs
    assert any("not meaningful for this business model" in b
               for b in out.valuation_blockers)

    other = financial_data_agent_output_to_dict(run_financial_data_agent(INDUSTRIAL_CO))
    out = run_valuation_guard_agent(INDUSTRIAL_CO, other,
                                    {"overall_source_quality": "strong"})
    assert "financials.ebitda" in out.missing_valuation_inputs
