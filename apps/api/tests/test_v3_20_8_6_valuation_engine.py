"""V3.20 slice 8.6 — the deterministic valuation engine (ADR-099), partial."""

from __future__ import annotations

from app.services.calculations.valuation_engine import (
    INSUFFICIENT,
    Assumption,
    Cited,
    Inputs,
    Scenario,
    dcf,
    run_engine,
    wacc,
)
from app.services.safety_terms import scan_value

A = Assumption


def _scenario(g: float, tg: float, beta: float, adj: float) -> Scenario:
    return Scenario(growth=[A(g, "test")] * 5, terminal_growth=A(tg, "test"),
                    beta=A(beta, "test"), multiple_adjustment=A(adj, "test"))


def _inputs(**over) -> Inputs:  # noqa: ANN003
    base = dict(
        currency="USD",
        base_fcff=Cited(100.0, "https://www.sec.gov/fcff", "FY2025", "USD"),
        ebit=Cited(150.0, "https://www.sec.gov/ebit", "FY2025", "USD"),
        net_debt=Cited(200.0, "https://www.sec.gov/nd", "FY2025", "USD"),
        diluted_shares=Cited(10.0, "https://www.sec.gov/sh", "FY2025"),
        risk_free=Cited(0.04, "https://fred.stlouisfed.org/series/DGS10", "2026-11-13"),
        equity_risk_premium=Cited(0.05, "https://example.org/erp", "2026"),
        size_premium=A(0.01, "small cap"),
        cost_of_debt_pre_tax=A(0.06, "test"),
        tax_rate=A(0.25, "test"),
        debt_weight=A(0.2, "test"),
        peer_median_ev_to_ebit=Cited(12.0, None, "2026-11-01"),
        scenarios={"bear": _scenario(0.0, 0.01, 1.3, -0.2),
                   "base": _scenario(0.05, 0.02, 1.1, 0.0),
                   "bull": _scenario(0.10, 0.025, 0.9, 0.2)},
    )
    base.update(over)
    return Inputs(**base)  # type: ignore[arg-type]


def test_wacc_from_components() -> None:
    inp = _inputs()
    # ke = 0.04 + 1.1*0.05 + 0.01 = 0.105; kd = 0.06*0.75 = 0.045; 0.8*ke + 0.2*kd
    assert round(wacc(inp, 1.1), 6) == round(0.8 * 0.105 + 0.2 * 0.045, 6)


def test_dcf_matches_a_hand_calculation() -> None:
    inp = _inputs()
    s = inp.scenarios["base"]
    r, g = wacc(inp, 1.1), 0.02
    cash, pv = 100.0, 0.0
    for t in range(1, 6):
        cash *= 1.05
        pv += cash / (1 + r) ** t
    ev = pv + cash * (1 + g) / (r - g) / (1 + r) ** 5
    out = dcf(inp, s)
    assert out["enterprise_value"] == round(ev, 0)
    assert out["per_share"] == round((ev - 200) / 10, 4)


def test_range_triangulates_two_methods_and_orders_scenarios() -> None:
    out = run_engine(_inputs())
    rng = out["valuation_range"]
    assert rng["state"] == "computed" and rng["methods"] == ["fcff_dcf", "peer_multiple"]
    assert rng["per_share_low"] < rng["per_share_mid"] < rng["per_share_high"]
    pm = out["methods"]["peer_multiple"]["scenarios"]
    assert pm["base"]["per_share"] == round((12.0 * 150 - 200) / 10, 4)
    sens = out["sensitivity_fcff_dcf"]
    mid_col = [row[1] for row in sens["per_share"]]
    assert mid_col[0] > mid_col[1] > mid_col[2]  # higher discount rate, lower value
    assert {"kind"} <= set(out["inputs"]["assumptions"]["base.beta"])
    assert scan_value(out) == []


def test_fails_closed() -> None:
    one = run_engine(_inputs(peer_median_ev_to_ebit=None))
    assert one["valuation_range"]["state"] == INSUFFICIENT
    assert "at least 2" in one["valuation_range"]["reason"]
    assert one["methods"]["fcff_dcf"]["state"] == "computed"
    no_shares = run_engine(_inputs(diluted_shares=None))
    assert no_shares["methods"]["fcff_dcf"]["state"] == INSUFFICIENT
    assert no_shares["valuation_range"]["state"] == INSUFFICIENT
    assert "per_share_low" not in no_shares["valuation_range"]
    inverted = _inputs(scenarios={**_inputs().scenarios,
                                  "base": _scenario(0.05, 0.2, 1.1, 0.0)})
    assert dcf(inverted, inverted.scenarios["base"])["state"] == INSUFFICIENT
    uncited = run_engine(_inputs(risk_free=None))
    assert uncited["methods"]["fcff_dcf"]["state"] == INSUFFICIENT
    loss = run_engine(_inputs(ebit=Cited(-5.0, None, "FY2025")))
    assert loss["methods"]["peer_multiple"]["state"] == INSUFFICIENT
