"""Item 20 — the development-stage detector: positive proof only.

P1 no / immaterial revenue in the latest extracted statements; P2 exploration or
development spend; P3 at least two project-disclosure terms. ``development_stage_resource``
= P1 and (P2 or P3). The fixtures are synthetic; nothing here names an issuer.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.classification.stage import (
    SIGNAL_DEVELOPMENT_STAGE,
    SIGNAL_RESOURCE_EXTRACTION,
    assess,
    assess_revenue,
    detect_stage,
    project_terms,
)


def _fact(field, value, period="2025", *, scope="group", confidence="high",
          currency="AUD", scale="thousand"):
    return {"field": field, "numeric_value": value, "period": period, "scope": scope,
            "confidence": confidence, "currency": currency, "scale": scale,
            "fact_id": f"{field}:{period}"}


PROJECT_TEXT = [
    "The JORC-compliant Mineral Resource underpins the Definitive Feasibility Study.",
    "A binding offtake agreement covers 60% of planned output.",
]
DEVELOPER_FACTS = [
    _fact("net_income", -3265),
    _fact("administrative_expenses", 2140),
    _fact("exploration_capitalised", 5200),
]


class TestPositiveProof:
    def test_a_loss_making_developer_with_capitalised_exploration(self):
        out = assess(DEVELOPER_FACTS, [], has_commodity=False)
        assert out.signals == (SIGNAL_DEVELOPMENT_STAGE,)
        assert out.proofs["no_or_immaterial_revenue"]
        assert out.proofs["exploration_or_development_spend"]

    def test_project_vocabulary_with_a_commodity_also_signals_resource_extraction(self):
        facts = [_fact("net_income", -3265), _fact("operating_cash_flow", -2980)]
        out = assess(facts, PROJECT_TEXT, has_commodity=True)
        assert out.signals == (SIGNAL_DEVELOPMENT_STAGE, SIGNAL_RESOURCE_EXTRACTION)
        assert any(b.startswith("P3") for b in out.basis)

    def test_immaterial_revenue_counts_but_material_revenue_does_not(self):
        immaterial = [_fact("revenue", 150), _fact("administrative_expenses", 2140)]
        assert assess(immaterial, PROJECT_TEXT, has_commodity=False).is_development_stage
        producer = [_fact("revenue", 25000), _fact("administrative_expenses", 2140),
                    _fact("net_income", 4000)]
        out = assess(producer, PROJECT_TEXT, has_commodity=True)
        assert not out.is_development_stage
        # A producer reporting JORC resources is still a resource company.
        assert out.signals == (SIGNAL_RESOURCE_EXTRACTION,)

    def test_no_extraction_is_no_proof(self):
        out = assess([], PROJECT_TEXT, has_commodity=False)
        assert out.signals == ()
        assert "no statement figures" in (out.reason or "")

    def test_a_profitable_issuer_with_no_revenue_line_is_not_proved(self):
        facts = [_fact("net_income", 500), _fact("exploration_capitalised", 5200)]
        assert assess(facts, [], has_commodity=False).signals == ()

    def test_a_segment_loss_is_not_the_company(self):
        facts = [_fact("net_income", -3265, scope="Project Company Pty Ltd"),
                 _fact("exploration_capitalised", 5200)]
        assert assess(facts, [], has_commodity=False).signals == ()

    def test_the_latest_period_decides(self):
        facts = [_fact("net_income", -3265, "2023"), _fact("revenue", 50000, "2025"),
                 _fact("administrative_expenses", 2000, "2025")]
        proved, _basis, _refs = assess_revenue(facts)
        assert not proved

    def test_one_stray_project_term_is_not_enough(self):
        assert project_terms(["An offtake agreement was discussed."]) == ["offtake"]
        facts = [_fact("net_income", -3265)]
        assert assess(facts, ["An offtake agreement was discussed."],
                      has_commodity=True).signals == ()

    def test_acronyms_are_case_sensitive(self):
        assert project_terms(["the fid and the dfs and jorc"]) == []

    def test_the_biotech_signal_is_never_emitted(self):
        for facts, texts in ((DEVELOPER_FACTS, PROJECT_TEXT), ([], [])):
            assert "pre_revenue" not in assess(facts, texts, has_commodity=True).signals


async def test_detect_stage_never_raises():
    class _Broken:
        async def execute(self, *_a, **_k):  # noqa: ANN202
            raise RuntimeError("database gone")

    out = await detect_stage(_Broken(), SimpleNamespace(id="x"), subject_profile=None)
    assert out.signals == () and out.reason.startswith("stage evidence unreadable")
