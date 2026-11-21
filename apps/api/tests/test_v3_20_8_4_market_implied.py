"""V3.20 slice 8.4 — what today's enterprise value implies (reverse arithmetic)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.pipeline import market_implied_step as step
from app.services.safety_terms import scan_value
from app.services.valuation import market_implied as mi


def _ev(value: float = 1.5e9, *, ccy: str = "USD", mult: float | None = 12.0,
        ebit: float = 125e6, net_debt: float = 100e6) -> dict:
    return {"enterprise_value": {"state": "computed", "value": value, "currency": ccy},
            "net_debt": {"state": "computed", "value": net_debt},
            "multiples": [{"key": "ev_to_ebit", "state": "computed" if mult else
                           "not_meaningful", "value": mult, "ebit": ebit}]}


PEERS = {"median_ev_to_ebit": 15.0, "peer_count": 4,
         "peers": [{"market_cap_usd": c} for c in (2e9, 3e9, 4e9, 5e9)]}


@pytest.mark.parametrize("mult, position", [(12.0, mi.BELOW), (15.0, mi.IN_LINE),
                                            (18.0, mi.ABOVE)])
def test_peer_position_bands(mult: float, position: str) -> None:
    out = mi.peer_position(_ev(mult=mult), PEERS)
    assert out["state"] == "computed" and out["position"] == position


def test_no_median_or_no_multiple_is_not_available_with_the_reason() -> None:
    assert mi.peer_position(_ev(), {"median_reason": "fewer than 3 peers"}) == {
        "state": "not_available", "reason": "fewer than 3 peers"}
    assert mi.peer_position(_ev(mult=None), PEERS)["state"] == "not_available"


def test_implied_margin_at_the_peer_median() -> None:
    out = mi.implied_margin(_ev(), PEERS, {"value": 1e9, "currency": "USD",
                                           "period": "FY2025", "source_url": "u"})
    assert out["implied_ebit_margin"] == 0.1  # 1.5e9 / 15 / 1e9
    assert out["filed_ebit_margin"] == 0.125
    assert mi.implied_margin(_ev(), PEERS, {"value": 1e9, "currency": "EUR"})[
        "state"] == "not_available"


def test_implied_growth_grid_and_fail_closed_cases() -> None:
    cf = {"operating_cash_flow": 80e6, "capital_expenditure": -30e6, "currency": "USD",
          "period": "FY2025", "sources": []}
    out = mi.implied_growth(_ev(1e9), cf)
    assert out["state"] == "computed" and out["free_cash_flow"] == 50e6
    by_rate = {g["discount_rate"]: g["implied_perpetual_growth"] for g in out["grid"]}
    assert by_rate[0.10] == pytest.approx(0.0476, abs=1e-4)  # (0.1e9 - 0.05e9) / 1.05e9
    assert out["discount_rate_assumption"]["kind"] == "assumption"
    negative = mi.implied_growth(_ev(1e9), {**cf, "operating_cash_flow": 20e6})
    assert negative["state"] == "not_meaningful" and "grid" not in negative
    assert mi.implied_growth(_ev(1e9), {**cf, "currency": "EUR"})["state"] == "not_available"


def test_discount_characteristics_are_observations_with_their_block() -> None:
    blocks = {
        "valuation_context.layer_peers": PEERS,
        "financial_capacity.financial_depth": {
            "debt_maturity_ladder": {"total": 400e6, "buckets": [
                {"bucket": "next_12_months", "value": 80e6},
                {"bucket": "year_2", "value": 40e6}]},
            "dilution": {"years": [{"change_pct": 1.0}, {"change_pct": 7.5}]}},
        "valuation_context.obscurity": {"score": 72},
        "valuation_context.passive_ownership": {"state": "held",
                                                "pct_of_shares_outstanding": 2.1},
        "business_model.relationships": {"parent_and_group": {
            "direct_parents": [{"counterparty_name": "Holding AG"}]}},
        "financial_capacity.earnings_track_record": {
            "eps_streak": {"direction": "below", "quarters": 3}},
    }
    chars = mi.discount_characteristics(
        blocks, size_fit={"market_cap_usd": 1e9},
        ev_block=_ev(ccy="EUR", ebit=100e6, net_debt=400e6))
    assert [c["key"] for c in chars] == [
        "smaller_than_peers", "leverage", "refinancing_wall", "dilution", "obscurity",
        "low_passive_ownership", "controlling_holder", "non_usd_listing",
        "results_below_expectations"]
    assert all(c["source_block"] and "threshold" in c for c in chars)
    assert mi.discount_characteristics({}, size_fit=None, ev_block=_ev()) == []


def test_characteristics_only_when_below_and_no_block_without_ev() -> None:
    above = mi.market_implied_block(ev_block=_ev(mult=18.0), peers_block=PEERS,
                                    revenue=None, cash_flow=None, blocks={}, size_fit=None)
    assert above is not None and above["discount_characteristics"] is None
    below = mi.market_implied_block(ev_block=_ev(), peers_block=PEERS, revenue=None,
                                    cash_flow=None, blocks={}, size_fit=None)
    assert below is not None and below["discount_characteristics"] == []
    assert scan_value(below) == []
    refused = {"enterprise_value": {"state": "refused", "value": None}}
    assert mi.market_implied_block(ev_block=refused, peers_block=PEERS, revenue=None,
                                   cash_flow=None, blocks={}, size_fit=None) is None


def _row(label: str, value: float, period: str, ccy: str = "USD") -> dict:
    return {"label": label, "value": value, "currency": ccy, "scale": "million",
            "period": period, "source_url": f"https://x/{label}/{period}", "fact_id": "f"}


def test_pick_annual_takes_the_newest_complete_period_in_the_ev_currency() -> None:
    rows = [_row("revenue", 900, "FY2024"), _row("revenue", 1000, "FY2025"),
            _row("revenue", 300, "Q1 FY2026"), _row("revenue", 1100, "FY2025", "EUR"),
            _row("operating_cash_flow", 70, "FY2025"),  # no FY2025 capex
            _row("operating_cash_flow", 60, "FY2024"),
            _row("capital_expenditure", 20, "FY2024")]
    revenue, cash_flow = step.pick_annual(rows, "USD")
    assert revenue is not None and revenue["value"] == 1_000_000_000
    assert revenue["period"] == "FY2025"
    assert cash_flow is not None and cash_flow["period"] == "FY2024"
    assert cash_flow["operating_cash_flow"] == 60_000_000
    assert step.pick_annual(rows, None) == (None, None)


async def test_section_is_off_by_flag_and_reads_facts_when_on(monkeypatch) -> None:  # noqa: ANN001
    async def rows(_s, _c, labels=()):  # noqa: ANN001, ANN202
        assert set(labels) == set(step.LABELS)
        return [_row("revenue", 1000, "FY2025"), _row("operating_cash_flow", 80, "FY2025"),
                _row("capital_expenditure", 30, "FY2025")]

    monkeypatch.setattr("app.services.pipeline.enterprise_value_step.load_fact_rows", rows)
    company = SimpleNamespace(id="c")
    off = await step.market_implied_section(
        None, company, cfg=SimpleNamespace(), ev_block=_ev(), peers_block=PEERS, blocks={},
        size_fit=None)
    assert off is None
    on = await step.market_implied_section(
        None, company, cfg=SimpleNamespace(v3_market_implied_enabled=True), ev_block=_ev(),
        peers_block=PEERS, blocks={}, size_fit=None)
    assert on is not None
    assert on["implied_margin"]["implied_ebit_margin"] == 0.1
    assert on["implied_growth"]["state"] == "computed"


def test_report_input_record_block_and_web_reader_keys_are_pinned() -> None:
    import inspect
    import pathlib

    from app.services.pipeline import professional_research as pr
    from app.services.research_records import RECORD_BLOCKS

    assert "market_implied" in inspect.signature(pr.ReportInputs).parameters
    assert ("valuation_context", "market_implied") in RECORD_BLOCKS
    reader = (pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "components"
              / "research" / "marketImplied.ts").read_text()
    for key in ("peer_position", "implied_margin", "implied_growth",
                "discount_characteristics", "note"):
        assert f"raw.{key}" in reader, key
    for key in ("state", "reason", "position", "subject_ev_to_ebit",
                "peer_median_ev_to_ebit", "ratio", "peer_count"):
        assert f"p.{key}" in reader, key
    for key in ("implied_ebit_margin", "filed_ebit_margin", "revenue_period",
                "revenue_source_url"):
        assert f"m.{key}" in reader, key
    for key in ("free_cash_flow", "period", "currency", "fcf_basis", "grid",
                "discount_rate_assumption"):
        assert f"g.{key}" in reader, key
    for key in ("discount_rate", "implied_perpetual_growth", "key", "label",
                "source_block", "observed", "threshold"):
        assert f"r.{key}" in reader, key


def test_assemble_attaches_the_block_only_when_present() -> None:
    from app.services.pipeline import professional_research as pr

    src = pathlib_text("app/services/pipeline/professional_research.py")
    assert 'implied_section["market_implied"] = dict(inputs.market_implied)' in src
    assert "if inputs.market_implied:" in src
    assert pr.ReportInputs.__dataclass_fields__["market_implied"].default is None


def pathlib_text(rel: str) -> str:
    import pathlib

    return (pathlib.Path(__file__).resolve().parents[1] / rel).read_text()
