"""Deterministic valuation engine — V3.20 slice 8.6 (ADR-099), partial.

ADR-099: the engine produces the number; a model may only PROPOSE assumptions. Every
input is either **cited** (a figure with source, period, currency) or a **named
assumption** (value + rationale). The output is a RANGE (bear / base / bull per method,
triangulated across methods), never a point, and it fails closed.

Methods in this slice
=====================
* ``fcff_dcf`` — five explicit years from a cited base free cash flow to the firm, grown
  at the scenario's named growth path, a Gordon terminal value at the named terminal
  growth, discounted at a WACC built from its components (CAPM cost of equity = risk-free
  + beta × equity risk premium + size premium; after-tax cost of debt; capital weights).
  Equity = EV − cited net debt; per share = equity ÷ cited diluted shares.
* ``peer_multiple`` — the layer-peer median EV/EBIT (slice 8.2) × the subject's cited
  EBIT, with the scenario applying the named discount/premium to the median.

Fail-closed (``insufficient_evidence``, no number shown)
========================================================
* no cited base period (FCFF for the DCF, EBIT for the multiple);
* unknown share count or unknown net debt;
* WACC not above terminal growth;
* fewer than ``MIN_METHODS`` methods computable — then no triangulated range.

Output vocabulary deliberately avoids the safety gate's phrase tier: the range lives
under ``valuation_range``. The engine is NOT wired into the research pipeline yet: the
field-scoped allow-list (slice 0.3, ADR-098 §3) must be in the stack, and the cited
inputs it needs (base FCFF, a cited risk-free rate and equity risk premium) and the
assumption proposer are not built. No flag exists until something consumes one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

ENGINE_VERSION = "2026-11-16.1"
EXPLICIT_YEARS = 5
MIN_METHODS = 2
SCENARIOS = ("bear", "base", "bull")
INSUFFICIENT = "insufficient_evidence"
COMPUTED = "computed"


@dataclass(frozen=True)
class Cited:
    """A figure with its provenance. ``value`` in absolute units of ``currency``."""

    value: float
    source_url: str | None
    period: str | None
    currency: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "source_url": self.source_url, "period": self.period,
                "currency": self.currency, "kind": "cited"}


@dataclass(frozen=True)
class Assumption:
    """A named assumption: a value with the rationale for it (a model may propose one)."""

    value: float
    rationale: str
    proposed_by: str = "engine_default"

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "rationale": self.rationale,
                "proposed_by": self.proposed_by, "kind": "assumption"}


@dataclass(frozen=True)
class Scenario:
    growth: Sequence[Assumption]  # one per explicit year
    terminal_growth: Assumption
    beta: Assumption
    multiple_adjustment: Assumption  # fraction applied to the peer median (e.g. -0.1)


@dataclass
class Inputs:
    currency: str
    base_fcff: Cited | None
    ebit: Cited | None
    net_debt: Cited | None
    diluted_shares: Cited | None
    risk_free: Cited | None
    equity_risk_premium: Cited | None
    size_premium: Assumption
    cost_of_debt_pre_tax: Assumption
    tax_rate: Assumption
    debt_weight: Assumption
    peer_median_ev_to_ebit: Cited | None
    scenarios: Mapping[str, Scenario] = field(default_factory=dict)


def wacc(inputs: Inputs, beta: float) -> float | None:
    if inputs.risk_free is None or inputs.equity_risk_premium is None:
        return None
    ke = (inputs.risk_free.value + beta * inputs.equity_risk_premium.value
          + inputs.size_premium.value)
    kd = inputs.cost_of_debt_pre_tax.value * (1 - inputs.tax_rate.value)
    wd = min(max(inputs.debt_weight.value, 0.0), 0.9)
    return (1 - wd) * ke + wd * kd


def _per_share(ev: float, inputs: Inputs) -> float:
    if inputs.net_debt is None or inputs.diluted_shares is None:
        raise ValueError("net debt and the diluted share count are required")
    return (ev - inputs.net_debt.value) / inputs.diluted_shares.value


def dcf(inputs: Inputs, s: Scenario, *, wacc_override: float | None = None,
        g_override: float | None = None) -> dict[str, Any]:
    if inputs.base_fcff is None:
        return {"state": INSUFFICIENT, "reason": "no cited base free cash flow to the firm"}
    if inputs.net_debt is None or inputs.diluted_shares is None:
        return {"state": INSUFFICIENT, "reason": "net debt or the diluted share count is "
                                                 "unknown"}
    if len(s.growth) != EXPLICIT_YEARS:
        return {"state": INSUFFICIENT, "reason": f"{EXPLICIT_YEARS} growth years needed"}
    r = wacc_override if wacc_override is not None else wacc(inputs, s.beta.value)
    if r is None:
        return {"state": INSUFFICIENT, "reason": "risk-free rate or equity risk premium "
                                                 "is not cited"}
    g = g_override if g_override is not None else s.terminal_growth.value
    if r <= g:
        return {"state": INSUFFICIENT, "reason": "discount rate is not above terminal growth"}
    cash, pv = inputs.base_fcff.value, 0.0
    for year, growth in enumerate(s.growth, start=1):
        cash *= 1 + growth.value
        pv += cash / (1 + r) ** year
    terminal = cash * (1 + g) / (r - g)
    pv_terminal = terminal / (1 + r) ** EXPLICIT_YEARS
    ev = pv + pv_terminal
    return {"state": COMPUTED, "enterprise_value": round(ev, 0),
            "per_share": round(_per_share(ev, inputs), 4), "discount_rate": round(r, 4),
            "terminal_growth": g, "terminal_share_of_ev": round(pv_terminal / ev, 3)}


def peer_multiple(inputs: Inputs, s: Scenario) -> dict[str, Any]:
    if inputs.peer_median_ev_to_ebit is None:
        return {"state": INSUFFICIENT, "reason": "no layer-peer median EV/EBIT"}
    if inputs.ebit is None or inputs.ebit.value <= 0:
        return {"state": INSUFFICIENT, "reason": "no positive cited EBIT"}
    if inputs.net_debt is None or inputs.diluted_shares is None:
        return {"state": INSUFFICIENT, "reason": "net debt or the diluted share count is "
                                                 "unknown"}
    multiple = inputs.peer_median_ev_to_ebit.value * (1 + s.multiple_adjustment.value)
    ev = multiple * inputs.ebit.value
    return {"state": COMPUTED, "enterprise_value": round(ev, 0),
            "per_share": round(_per_share(ev, inputs), 4), "multiple": round(multiple, 2)}


def sensitivity(inputs: Inputs, s: Scenario, *, step_r: float = 0.01,
                step_g: float = 0.005) -> dict[str, Any] | None:
    base_r = wacc(inputs, s.beta.value)
    if base_r is None:
        return None
    rs = [round(base_r + k * step_r, 4) for k in (-1, 0, 1)]
    gs = [round(s.terminal_growth.value + k * step_g, 4) for k in (-1, 0, 1)]
    grid = [[(lambda out: out.get("per_share"))(dcf(inputs, s, wacc_override=r,
                                                    g_override=g)) for g in gs] for r in rs]
    return {"rows_discount_rate": rs, "columns_terminal_growth": gs, "per_share": grid}


def run_engine(inputs: Inputs) -> dict[str, Any]:
    """All methods × scenarios, the triangulated range, or ``insufficient_evidence``."""
    methods: dict[str, dict[str, Any]] = {}
    for name, fn in (("fcff_dcf", dcf), ("peer_multiple", peer_multiple)):
        per = {sc: fn(inputs, inputs.scenarios[sc]) for sc in SCENARIOS
               if sc in inputs.scenarios}
        computed = all(v.get("state") == COMPUTED for v in per.values()) and len(per) == 3
        methods[name] = {"state": COMPUTED if computed else INSUFFICIENT,
                         "scenarios": per,
                         "reason": None if computed else next(
                             (v.get("reason") for v in per.values()
                              if v.get("state") != COMPUTED), "scenarios missing")}
    usable = [m for m in methods.values() if m["state"] == COMPUTED]
    range_: dict[str, Any]
    if len(usable) < MIN_METHODS:
        range_ = {"state": INSUFFICIENT,
                  "reason": f"{len(usable)} method(s) computable; at least {MIN_METHODS} "
                            "are needed for a range"}
    else:
        lows = [m["scenarios"]["bear"]["per_share"] for m in usable]
        mids = [m["scenarios"]["base"]["per_share"] for m in usable]
        highs = [m["scenarios"]["bull"]["per_share"] for m in usable]
        range_ = {"state": COMPUTED, "currency": inputs.currency,
                  "per_share_low": round(min(lows), 4),
                  "per_share_mid": round(sum(mids) / len(mids), 4),
                  "per_share_high": round(max(highs), 4),
                  "methods": [k for k, m in methods.items() if m["state"] == COMPUTED]}
    base = inputs.scenarios.get("base")
    return {
        "version": ENGINE_VERSION,
        "currency": inputs.currency,
        "valuation_range": range_,
        "methods": methods,
        "sensitivity_fcff_dcf": sensitivity(inputs, base) if base else None,
        "inputs": {
            "cited": {k: v.to_dict() for k, v in (
                ("base_fcff", inputs.base_fcff), ("ebit", inputs.ebit),
                ("net_debt", inputs.net_debt), ("diluted_shares", inputs.diluted_shares),
                ("risk_free", inputs.risk_free),
                ("equity_risk_premium", inputs.equity_risk_premium),
                ("peer_median_ev_to_ebit", inputs.peer_median_ev_to_ebit)) if v is not None},
            "assumptions": {
                "size_premium": inputs.size_premium.to_dict(),
                "cost_of_debt_pre_tax": inputs.cost_of_debt_pre_tax.to_dict(),
                "tax_rate": inputs.tax_rate.to_dict(),
                "debt_weight": inputs.debt_weight.to_dict(),
                **{f"{sc}.{k}": a.to_dict()
                   for sc, s in inputs.scenarios.items()
                   for k, a in (("terminal_growth", s.terminal_growth), ("beta", s.beta),
                                ("multiple_adjustment", s.multiple_adjustment))},
                **{f"{sc}.growth_year_{i}": a.to_dict()
                   for sc, s in inputs.scenarios.items() for i, a in enumerate(s.growth, 1)},
            },
        },
        "note": ("Computed by a deterministic engine from cited figures and named "
                 "assumptions, each listed; a range across methods and scenarios, not a "
                 "single figure and not a recommendation."),
    }


__all__ = ["Assumption", "Cited", "ENGINE_VERSION", "INSUFFICIENT", "Inputs", "MIN_METHODS",
           "Scenario", "dcf", "peer_multiple", "run_engine", "sensitivity", "wacc"]
