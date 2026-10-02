"""Item 20 — the development-stage resource overlay playbook.

A pre-revenue developer is asked the project questions — resource, grade, recovery,
offtake, permits, construction, capex spent and remaining, funding, runway, the issuer's
study economics — and NOT the producer questions (revenue trajectory, margins, revenue
share by commodity, unit costs). The overlay supersedes those in the mining playbook
too, and the supersession is recorded. Producer, biotech and luxury plans are
byte-identical to the plans before the overlay existed
(``fixtures/playbook_plans_before_overlay.json``, generated from the parent commit).
"""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

from app.services.director.planner import plan_research
from app.services.macro.commodities import BY_SLUG
from app.services.pipeline.v3_pipeline import V3ResearchOutcome, _mark_pre_revenue, _PlaybookAdapter
from app.services.playbooks import get, select
from app.services.playbooks.industries import DEVELOPMENT_STAGE_RESOURCE
from app.services.research_fields import FIELD_KEYS, fields_mentioned
from app.services.safety_terms import scan_text

CFG = SimpleNamespace(v3_agent_tools_enabled=True, v3_deepseek_search_enabled=True,
                      v3_filings_tool_enabled=True, v3_corpus_enabled=True,
                      v3_commodity_sources_enabled=True)
DEV = "development_stage_resource"
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "playbook_plans_before_overlay.json"


async def _plan(industry, signals=(), commodities=("copper",)):
    selection = select(sector=None, industry=industry, signals=signals)
    plan = await plan_research(
        subject="ANY:AU", mode="deep",
        playbooks=[_PlaybookAdapter(p) for p in selection.playbooks], cfg=CFG,
        commodities=[BY_SLUG[c] for c in commodities])
    return selection, plan


class TestSelection:
    def test_only_the_signal_selects_it(self):
        assert DEV not in select(sector="Materials", industry="Metals & Mining").versions
        assert DEV in select(sector=None, industry=None, signals=[DEV]).versions
        assert get(DEV).overlay is True

    def test_it_is_registered_with_two_blocking_questions(self):
        assert {q.key for q in DEVELOPMENT_STAGE_RESOURCE.blocking_questions} == {
            "project_portfolio", "capex_and_funding"}


class TestOverlaySupersession:
    async def test_the_mining_producer_questions_are_superseded_and_recorded(self):
        selection, plan = await _plan("Metals & Mining", signals=[DEV])
        keys = {q.key for q in plan.questions}
        for producer in ("commodity_exposure", "assets_and_production",
                         "reserves_and_mine_life", "unit_costs", "customers_and_offtake"):
            assert producer not in keys and not any(k.startswith(producer) for k in keys)
            assert plan.superseded[producer] == DEV
        assert plan.to_dict()["superseded_by"]["unit_costs"] == DEV
        assert selection.superseded["commodity_exposure"] == DEV
        # The developer questions are asked, and the mining market questions still are.
        assert {"project_portfolio", "capex_and_funding", "study_economics",
                "cash_runway_and_dilution", "revenue_status"} <= keys
        assert any(k.startswith("commodity_market") for k in keys)

    async def test_the_base_producer_questions_are_not_asked(self):
        _selection, plan = await _plan("Nothing Declared", signals=[DEV])
        keys = {q.key for q in plan.questions}
        for base in ("revenue_trajectory", "profitability", "cash_generation_and_funding",
                     "products_and_revenue_mix", "operations_and_assets"):
            assert base not in keys

    async def test_the_runway_question_routes_to_the_runway_calculation(self):
        _selection, plan = await _plan("Nothing Declared", signals=[DEV])
        [runway] = [q for q in plan.questions if q.key == "cash_runway_and_dilution"]
        assert runway.required_calculations == ("cash_runway_quarters",)


class TestPlansWithoutTheOverlayAreUnchanged:
    @pytest.mark.parametrize("case", ["mining_producer", "biotech", "luxury", "no_playbook"])
    async def test_byte_identical_to_the_plan_before_the_overlay(self, case):
        from importlib import util

        spec = util.spec_from_file_location(
            "plan_snapshot", pathlib.Path(__file__).parent / "support_plan_snapshot.py")
        module = util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        now = json.loads(json.dumps(await module.snapshot(), sort_keys=True, default=str))
        before = json.loads(FIXTURE.read_text())
        assert now[case] == before[case]


class TestFieldVocabulary:
    """Thin integration with ``research_fields``: the question wording names the fields
    gap reconciliation joins on, so a finding stating the capex closes the capex gap."""

    @pytest.mark.parametrize(
        "key,expected",
        [
            ("capex_and_funding", {"metric:capex"}),
            ("construction_and_schedule",
             {"milestone:first_production", "milestone:commissioning",
              "milestone:final_investment_decision"}),
            ("offtake", {"commercial:offtake"}),
            ("study_economics", {"metric:npv", "metric:irr"}),
            ("cash_runway_and_dilution", {"metric:cash", "metric:cash_runway"}),
            ("resource_reserve", {"metric:mineral_resource"}),
            ("project_portfolio", {"metric:production_capacity"}),
        ],
    )
    def test_questions_name_their_fields(self, key, expected):
        [question] = [q for q in DEVELOPMENT_STAGE_RESOURCE.questions if q.key == key]
        found = set(fields_mentioned(question.text))
        assert expected <= found and found <= FIELD_KEYS


class TestSafety:
    def test_no_rating_or_valuation_wording(self):
        for q in DEVELOPMENT_STAGE_RESOURCE.questions:
            for text in (q.text, q.why_it_matters, *q.search_intents):
                assert scan_text(text) == [], (q.key, text)

    def test_study_economics_are_the_issuers_stated_figures(self):
        [q] = [q for q in DEVELOPMENT_STAGE_RESOURCE.questions if q.key == "study_economics"]
        assert "issuer's stated study figures" in q.text
        assert "does not value the project" in q.text


class TestPreRevenue:
    def test_revenue_is_a_stage_not_a_gap(self):
        outcome = V3ResearchOutcome(financial_statements={"slots": {}})
        stage = SimpleNamespace(basis=["P1: no revenue line", "P2: spend"])
        _mark_pre_revenue(outcome, stage)
        status = outcome.financial_statements["revenue_status"]
        assert status["label"] == "Revenue: pre-revenue / not applicable yet"
        assert status["basis"] == ["P1: no revenue line"]

    def test_a_revenue_figure_is_never_overwritten(self):
        outcome = V3ResearchOutcome(
            financial_statements={"slots": {"revenue_primary_filing": {"numeric_value": 5}}})
        _mark_pre_revenue(outcome, SimpleNamespace(basis=[]))
        assert "revenue_status" not in outcome.financial_statements

    def test_the_stage_is_on_the_payload(self):
        outcome = V3ResearchOutcome(stage={"stage": DEV, "signals": [DEV]})
        assert outcome.to_dict()["stage"]["stage"] == DEV


class TestReviewRound1:
    async def test_stacked_on_another_blocking_playbook_the_overlay_adds_no_blocking(self):
        """B1 — whatever selects both, a plan never carries two methodologies' blocking
        questions; the overlay's are planned non-blocking and that is recorded."""
        _selection, plan = await _plan("Biotechnology", signals=[DEV], commodities=())
        assert {q.key for q in plan.blocking_questions} == {"cash_runway", "pipeline_state"}
        assert plan.blocking_demoted == {"project_portfolio": DEV, "capex_and_funding": DEV}
        assert plan.to_dict()["blocking_demoted"]["capex_and_funding"] == DEV
        assert len(plan.blocking_questions) <= 2

    async def test_with_the_mining_playbook_the_overlay_keeps_its_two(self):
        _selection, plan = await _plan("Metals & Mining", signals=[DEV])
        assert {q.key for q in plan.blocking_questions} == {
            "project_portfolio", "capex_and_funding"}
        assert plan.blocking_demoted == {}

    @pytest.mark.parametrize(
        "case,industry,texts",
        [
            ("biotech", "Biotechnology", [
                "Capitalised development costs relate to the Phase 3 programme.",
                "Assets under construction comprise the new manufacturing facility."]),
            ("luxury", "Luxury Goods", [
                "The Maisons opened 40 boutiques; final investment decision on a new "
                "workshop was taken."]),
            ("mining_producer", "Metals & Mining", [
                "Copper sales rose; an offtake agreement with a smelter was renewed."]),
        ],
    )
    async def test_realistic_documents_still_give_the_unchanged_plan(self, case, industry,
                                                                     texts):
        """The golden plans are only meaningful if realistic documents give NO signal.
        A loss-making company, a feasibility/FID/offtake vocabulary that is not mining
        reporting-code evidence — and the plan is still byte-identical."""
        from app.services.classification.stage import assess

        facts = [
            {"field": "net_income", "numeric_value": -500.0, "period": p, "scope": "group",
             "confidence": "high", "currency": "EUR", "scale": "million", "document_id": "d"}
            for p in ("2025", "2024")
        ]
        stage = assess(facts, texts, has_commodity=case == "mining_producer")
        assert DEV not in stage.signals
        selection = select(sector=None, industry=industry, signals=stage.signals)
        plan = await plan_research(
            subject="ANY:US", mode="deep",
            playbooks=[_PlaybookAdapter(p) for p in selection.playbooks], cfg=CFG,
            commodities=[BY_SLUG["copper"]] if case == "mining_producer" else [])
        before = json.loads(FIXTURE.read_text())[case]
        assert [q.key for q in plan.questions] == [q["key"] for q in before["questions"]]
        assert plan.to_dict() == before["plan"]


class TestStudyEconomicsScreen:
    """L4 — an NPV / IRR is the issuer's named, dated study figure or it is withheld."""

    def _finding(self, statement, question_key=None):
        from app.services.pipeline.professional_research import FindingView

        return FindingView(finding_id="f", statement=statement, domain=None,
                           question_key=question_key)

    def test_an_attributed_study_figure_is_kept(self):
        from app.services.pipeline.professional_research import screen

        kept, dropped = screen([self._finding(
            "The 2025 Definitive Feasibility Study states a post-tax NPV8 of US$1.2 billion "
            "and an IRR of 24%.")])
        assert len(kept) == 1 and dropped == 0

    @pytest.mark.parametrize("statement", [
        "The project has an NPV of US$1.2 billion.",
        "The feasibility study shows an IRR of 24%.",  # no date
        "An IRR of 24% was reported in 2025.",  # no study
    ])
    def test_an_unattributed_npv_or_irr_is_withheld(self, statement):
        from app.services.pipeline.professional_research import screen

        kept, dropped = screen([self._finding(statement)])
        assert kept == [] and dropped == 1

    def test_valuation_wording_under_study_economics_is_withheld(self):
        from app.services.pipeline.professional_research import screen

        kept, _ = screen([self._finding(
            "The 2024 DFS operating cost makes the project attractive.", "study_economics")])
        assert kept == []
        kept, _ = screen([self._finding(
            "The 2024 DFS states an operating cost of US$40/t.", "study_economics")])
        assert len(kept) == 1
