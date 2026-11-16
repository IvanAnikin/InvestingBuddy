"""V3.20 slice 5.3 — the electricity playbook (8th) and how it is selected."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.services.pipeline.electricity_step import (
    SELECTION_SIGNAL,
    electricity_selection_signals,
)
from app.services.playbooks import select
from app.services.playbooks.industries import ELECTRICITY, PLAYBOOKS
from app.services.safety_terms import scan_value
from tests import test_v3_entity_relationships as _base

session = _base.session

ON = SimpleNamespace(v3_electricity_classifier_enabled=True)
OFF = SimpleNamespace(v3_electricity_classifier_enabled=False)


def test_eighth_playbook_covers_the_spec_questions() -> None:
    assert len(PLAYBOOKS) == 8 and ELECTRICITY in PLAYBOOKS
    keys = {q.key for q in ELECTRICITY.questions}
    assert keys == {
        "elec_revenue_visibility", "elec_backlog_quality", "elec_contracted_vs_merchant",
        "elec_qualification_evidence", "elec_capacity_natural_unit",
        "elec_customer_concentration", "elec_input_cost_pass_through",
        "elec_capex_cycle_stage", "elec_competitor_capacity",
    }
    # At most two block (the platform rule); the rest are mandatory priority 1–2.
    assert {q.key for q in ELECTRICITY.blocking_questions} == {
        "elec_revenue_visibility", "elec_capacity_natural_unit"}
    assert all(q.priority <= 2 for q in ELECTRICITY.questions)
    capacity = next(q for q in ELECTRICITY.questions if q.key == "elec_capacity_natural_unit")
    for unit in ("MVA", "GVA", "km", "tonnes", "units per year"):
        assert unit in capacity.text
    assert scan_value([q.text for q in ELECTRICITY.questions]) == []


def test_selected_by_industry_by_signal_and_not_otherwise() -> None:
    def ids(**kw: object) -> set[str]:
        return {p.playbook_id for p in select(**kw).playbooks}  # type: ignore[arg-type]

    assert "electricity_value_chain" in ids(sector="Industrials",
                                            industry="Electrical Equipment")
    assert "electricity_value_chain" in ids(sector="Utilities")
    # A copper-rod producer classified "Materials" reaches it only through the signal.
    assert "electricity_value_chain" not in ids(sector="Materials", industry="Chemicals")
    assert "electricity_value_chain" in ids(sector="Materials", industry="Chemicals",
                                            signals=[SELECTION_SIGNAL])
    assert "electricity_value_chain" not in ids(sector="Consumer Discretionary",
                                                industry="Luxury Goods")


async def _company(session, description: str):  # noqa: ANN001, ANN202
    from app.models.company import Company

    company = Company(id=uuid.uuid4(), ticker=f"T{uuid.uuid4().hex[:4]}", exchange="NYSE",
                      name="Example", description=description)
    session.add(company)
    await session.flush()
    return company


async def test_signal_only_for_a_classified_company(session) -> None:  # noqa: ANN001
    grid = await _company(session, "We make power transformers and shunt reactors.")
    assert await electricity_selection_signals(session, grid, cfg=ON) == (SELECTION_SIGNAL,)
    assert await electricity_selection_signals(session, grid, cfg=OFF) == ()
    miner = await _company(session, "A gold mining company; we also sell copper wire and "
                                    "magnet wire.")
    assert await electricity_selection_signals(session, miner, cfg=ON) == ()
    bags = await _company(session, "We design handbags.")
    assert await electricity_selection_signals(session, bags, cfg=ON) == ()
