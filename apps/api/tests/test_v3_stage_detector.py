"""Item 20 — the development-stage detector: positive MINING evidence only.

Mining evidence (a reporting-code term or a mining industry) ∧ P1 (no / immaterial
revenue in the latest annual year AND the prior year, from one annual statement) ∧
(P2 mining exploration/development spend ∨ P3 ≥2 project terms incl. a code term).

Every negative case below is a realistic shape that must NEVER produce a signal: a
loss-making biotech with capitalised development, a technology company with assets
under construction, an energy developer with FID + offtake + DFS, a producer with an
interim loss, a luxury group. The fixtures are synthetic; nothing names an issuer.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.classification.stage import (
    SIGNAL_DEVELOPMENT_STAGE,
    SIGNAL_RESOURCE_EXTRACTION,
    assess,
    assess_revenue,
    assess_spend,
    detect_stage,
    project_terms,
)


def _fact(field, value, period="2025", *, scope="group", confidence="high",
          currency="AUD", scale="thousand", document="ar2025"):
    return {"field": field, "numeric_value": value, "period": period, "scope": scope,
            "confidence": confidence, "currency": currency, "scale": scale,
            "fact_id": f"{document}:{field}:{period}", "document_id": document}


def _loss_two_years(**kw):
    return [_fact("net_income", -3265, "2025", **kw), _fact("net_income", -2410, "2024", **kw),
            _fact("administrative_expenses", 2140, "2025", **kw)]


MINING_TEXT = [
    "Our Mineral Resource estimate, reported in accordance with the JORC Code, underpins "
    "the Definitive Feasibility Study.",
    "The Company's maiden Ore Reserve estimate was released in 2025.",
    "A binding offtake agreement covers 60% of planned output.",
]

# ── Realistic NON-mining corpora ─────────────────────────────────────────────── #
BIOTECH_TEXT = [
    "Capitalised development costs relate to the Phase 3 clinical programme.",
    "Assets under construction comprise the new manufacturing facility.",
    "The final investment decision on the fill-finish plant was taken in 2025.",
]
TECH_TEXT = [
    "Capitalised development of software platforms totalled $12 million.",
    "Assets under construction include the new data centre.",
]
ENERGY_TEXT = [
    "The LNG project reached FID after the DFS was completed; a 20-year offtake agreement "
    "with a utility covers 80% of output.",
    "The battery storage scoping study and the hydrogen pre-feasibility study continue.",
]
LUXURY_TEXT = ["Revenue grew in every region; the Maisons opened 40 boutiques."]


class TestPositiveMiningProof:
    def test_a_loss_making_miner_with_capitalised_exploration(self):
        facts = [*_loss_two_years(), _fact("exploration_capitalised", 5200)]
        out = assess(facts, ["Our updated Mineral Resource estimate (JORC Code).",
                             "The Project's Ore Reserve estimate supports the plan."],
                     has_commodity=False)
        assert out.signals == (SIGNAL_DEVELOPMENT_STAGE,)
        assert out.proofs["mining_evidence"] and out.proofs["no_or_immaterial_revenue"]

    def test_mining_industry_classification_is_mining_evidence(self):
        facts = [*_loss_two_years(), _fact("exploration_capitalised", 5200)]
        assert assess(facts, [], has_commodity=False).signals == ()
        assert assess(facts, [], has_commodity=False, mining_sector=True).signals == (
            SIGNAL_DEVELOPMENT_STAGE,)

    def test_project_vocabulary_with_a_commodity_signals_both(self):
        out = assess(_loss_two_years(), MINING_TEXT, has_commodity=True)
        assert out.signals == (SIGNAL_DEVELOPMENT_STAGE, SIGNAL_RESOURCE_EXTRACTION)

    def test_immaterial_revenue_in_both_years(self):
        facts = [_fact("revenue", 150, "2025"), _fact("administrative_expenses", 2140, "2025"),
                 _fact("revenue", 120, "2024"), _fact("administrative_expenses", 1900, "2024")]
        assert assess(facts, MINING_TEXT, has_commodity=False).is_development_stage


class TestNeverOnNonMining:
    """B1 / H3 — the realistic false positives the review found."""

    @pytest.mark.parametrize("texts", [BIOTECH_TEXT, TECH_TEXT, ENERGY_TEXT, LUXURY_TEXT])
    def test_loss_making_non_miners_get_no_signal(self, texts):
        facts = [*_loss_two_years(), _fact("operating_cash_flow", -2980)]
        out = assess(facts, texts, has_commodity=True)
        assert out.signals == ()

    def test_capitalised_development_and_assets_under_construction_are_not_mining_spend(self):
        assert assess_spend([], BIOTECH_TEXT + TECH_TEXT) == (False, None)
        assert assess_spend([], ["Exploration and evaluation expenditure capitalised."])[0]

    def test_energy_fid_offtake_and_dfs_are_not_resource_extraction(self):
        terms = project_terms(ENERGY_TEXT)
        assert {"final_investment_decision", "offtake", "feasibility_study"} <= set(terms)
        out = assess([], ENERGY_TEXT, has_commodity=True)
        assert SIGNAL_RESOURCE_EXTRACTION not in out.signals

    def test_a_producer_reporting_resources_is_a_resource_company_not_a_developer(self):
        producer = [_fact("revenue", 25000, "2025"), _fact("net_income", 4000, "2025"),
                    _fact("revenue", 22000, "2024"), _fact("net_income", 3500, "2024")]
        out = assess(producer, MINING_TEXT, has_commodity=True)
        assert out.signals == (SIGNAL_RESOURCE_EXTRACTION,)


class TestRevenueIsJudgedOnTheLatestAnnualTwoYears:
    """H2 — the latest ANNUAL statement only, from one document, both years."""

    def test_a_producer_with_annual_revenue_and_an_interim_loss_is_not_pre_revenue(self):
        facts = [_fact("revenue", 50000, "2025"), _fact("net_income", 6000, "2025"),
                 _fact("net_income", -800, "H1 2026", document="h1")]
        assert assess_revenue(facts)[0] is False
        assert assess(facts, MINING_TEXT, has_commodity=True).signals == (
            SIGNAL_RESOURCE_EXTRACTION,)

    def test_revenue_in_the_prior_year_defeats_p1(self):
        facts = [*_loss_two_years(), _fact("revenue", 40000, "2024")]
        assert assess_revenue(facts)[0] is False

    def test_one_year_alone_is_not_enough(self):
        facts = [_fact("net_income", -3265, "2025")]
        assert assess_revenue(facts)[0] is False

    def test_the_two_years_must_come_from_one_document(self):
        facts = [_fact("net_income", -3265, "2025", document="a"),
                 _fact("net_income", -2410, "2024", document="b")]
        assert assess_revenue(facts)[0] is False

    def test_a_lower_confidence_revenue_figure_blocks_the_proof(self):
        facts = [*_loss_two_years(), _fact("revenue", 9000, "2025", confidence="medium")]
        assert assess_revenue(facts)[0] is False

    def test_operating_cash_outflow_alone_is_not_an_income_statement(self):
        facts = [_fact("operating_cash_flow", -2980, "2025"),
                 _fact("operating_cash_flow", -2300, "2024")]
        assert assess_revenue(facts)[0] is False

    def test_a_segment_loss_is_not_the_company(self):
        facts = _loss_two_years(scope="Project Company Pty Ltd")
        assert assess_revenue(facts)[0] is False

    def test_a_profit_is_not_a_loss(self):
        facts = [_fact("net_income", 500, "2025"), _fact("net_income", 400, "2024")]
        assert assess_revenue(facts)[0] is False


def test_acronyms_are_case_sensitive():
    assert project_terms(["the fid and the dfs and jorc"]) == []


def test_the_biotech_signal_is_never_emitted():
    for texts in (MINING_TEXT, BIOTECH_TEXT, []):
        assert "pre_revenue" not in assess(_loss_two_years(), texts,
                                           has_commodity=True).signals


async def test_detect_stage_never_raises():
    class _Broken:
        def begin_nested(self):  # noqa: ANN202
            raise RuntimeError("database gone")

    out = await detect_stage(_Broken(), SimpleNamespace(id="x"), subject_profile=None)
    assert out.signals == () and out.reason.startswith("stage evidence unreadable")
