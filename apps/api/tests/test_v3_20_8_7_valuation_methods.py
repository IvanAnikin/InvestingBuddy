"""V3.20 slice 8.7 — further engine methods: contracted DCF, RAB, SOTP, peer multiples and
the replacement-cost floor. Engine only; nothing in the pipeline calls it."""

from __future__ import annotations

from dataclasses import replace

from app.services.calculations.valuation_engine import (
    INSUFFICIENT,
    NOT_APPLICABLE,
    Assumption,
    Cited,
    PeerMultiple,
    Replacement,
    Segment,
    contracted_dcf,
    rab_multiple,
    replacement_floor,
    run_engine,
    sotp,
)
from app.services.safety_terms import scan_value
from tests.test_v3_20_8_6_valuation_engine import _inputs, _scenario

A = Assumption


def _with_8_7(**over):  # noqa: ANN003, ANN202
    scen = {name: replace(s, contract_spread=A(spread, "test"), rab_multiple=A(rab, "test"),
                          replacement_haircut=A(cut, "test"))
            for (name, s), (spread, rab, cut) in zip(
                _inputs().scenarios.items(),
                ((0.04, 1.0, 0.5), (0.03, 1.15, 0.4), (0.02, 1.3, 0.3)))}
    base = dict(
        scenarios=scen,
        contracted_cash_flows=[Cited(50.0, "https://x/ppa", f"FY{y}", "USD")
                               for y in (2026, 2027, 2028)],
        regulated_asset_base=Cited(1000.0, "https://x/rab", "FY2025", "USD"),
        segments=[Segment("grid", Cited(80.0, "https://x/s1", "FY2025", "USD"), A(10, "t")),
                  Segment("service", Cited(20.0, "https://x/s2", "FY2025", "USD"), A(14, "t"))],
        peer_multiples=[PeerMultiple("ev_per_mw", Cited(300.0, "https://x/mw", "FY2025"),
                                     Cited(5.0, None, "2026-11-01"))],
        replacement=Replacement(Cited(300.0, "https://x/mw", "FY2025"), "MW",
                                A(4.0, "cost per MW, test")),
    )
    base.update(over)
    return _inputs(**base)


def test_contracted_dcf_discounts_each_cited_year_without_a_terminal_value() -> None:
    inp = _with_8_7()
    out = contracted_dcf(inp, inp.scenarios["base"])
    r = 0.04 + 0.03
    ev = sum(50.0 / (1 + r) ** t for t in (1, 2, 3))
    assert out["enterprise_value"] == round(ev, 0) and out["years"] == 3
    assert out["per_share"] == round((ev - 200) / 10, 4)
    no_spread = replace(inp.scenarios["base"], contract_spread=None)
    assert contracted_dcf(inp, no_spread)["state"] == INSUFFICIENT
    assert contracted_dcf(_inputs(), _inputs().scenarios["base"])["state"] == NOT_APPLICABLE


def test_rab_and_sotp_arithmetic() -> None:
    inp = _with_8_7()
    assert rab_multiple(inp, inp.scenarios["base"])["enterprise_value"] == 1150
    parts = sotp(inp, inp.scenarios["bear"])  # multiple adjustment -0.2
    assert [p["value"] for p in parts["parts"]] == [640, 224]
    assert parts["enterprise_value"] == 864
    one = _with_8_7(segments=[Segment("grid", Cited(80.0, "u", "FY2025"), A(10, "t"))])
    assert sotp(one, one.scenarios["base"])["state"] == NOT_APPLICABLE


def test_replacement_cost_is_a_floor_not_a_method() -> None:
    inp = _with_8_7()
    floor = replacement_floor(inp, inp.scenarios["base"])
    assert floor["enterprise_value"] == round(300 * 4.0 * 0.6, 0)
    out = run_engine(inp)
    assert "replacement_cost" in out["floors"]
    assert "replacement_cost" not in out["methods"]
    assert "replacement_cost" not in out["valuation_range"]["methods"]


def test_all_methods_triangulate_and_every_input_is_listed() -> None:
    out = run_engine(_with_8_7())
    rng = out["valuation_range"]
    assert rng["state"] == "computed"
    assert rng["methods"] == ["fcff_dcf", "peer_multiple", "contracted_dcf", "rab_multiple",
                              "sotp", "multiple:ev_per_mw"]
    assert rng["per_share_low"] <= rng["per_share_mid"] <= rng["per_share_high"]
    cited = out["inputs"]["cited"]
    assert {"regulated_asset_base", "replacement_capacity"} <= set(cited)
    assert len(out["inputs"]["contracted_cash_flows"]) == 3
    assert {s["name"] for s in out["inputs"]["segments"]} == {"grid", "service"}
    assumptions = out["inputs"]["assumptions"]
    assert {"base.contract_spread", "base.rab_multiple", "base.replacement_haircut",
            "replacement_cost_per_unit"} <= set(assumptions)
    assert scan_value(out) == []


def test_without_8_7_inputs_the_8_6_output_is_unchanged() -> None:
    out = run_engine(_inputs())
    assert set(out["methods"]) == {"fcff_dcf", "peer_multiple"}
    assert "floors" not in out
    bear = _scenario(0.0, 0.01, 1.3, -0.2)
    assert bear.contract_spread is None and bear.rab_multiple is None
