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

Methods added in slice 8.7 (each runs only when its inputs are given; otherwise it is
``not_applicable`` and never counted towards the range)
------------------------------------------------------------------------------------------
* ``contracted_dcf`` — the cited contracted cash flows (offtake / PPA / availability
  payments, one per future year) discounted at the cited risk-free rate + the scenario's
  named contract spread. No terminal value: a contract ends.
* ``rab_multiple`` — a cited regulated asset base × the scenario's named RAB multiple.
* ``sotp`` — sum of the parts: each segment's cited metric × its named multiple, the
  scenario's multiple adjustment applied to every segment.
* ``multiple:<key>`` — any further peer-median multiple (e.g. EV per MW from slice 8.1/8.2)
  × the subject's cited metric, with the scenario's multiple adjustment.
* **Replacement-cost floor** — cited capacity × a named cost per unit × (1 − the scenario's
  named haircut for age and condition). Reported under ``floors``: a floor, not a method,
  so it never moves the triangulated range.

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

ENGINE_VERSION = "2026-11-16.2"
EXPLICIT_YEARS = 5
MIN_METHODS = 2
SCENARIOS = ("bear", "base", "bull")
INSUFFICIENT = "insufficient_evidence"
COMPUTED = "computed"
NOT_APPLICABLE = "not_applicable"


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
    # Slice 8.7 — read only by the method that needs it.
    contract_spread: Assumption | None = None  # over the risk-free rate
    rab_multiple: Assumption | None = None  # x the regulated asset base
    replacement_haircut: Assumption | None = None  # 0..1 for age and condition


@dataclass(frozen=True)
class Segment:
    """One part of a sum of the parts: a cited metric × a named multiple."""

    name: str
    metric: Cited
    multiple: Assumption


@dataclass(frozen=True)
class PeerMultiple:
    """A further peer-median multiple applied to the subject's cited metric."""

    key: str
    subject_metric: Cited
    peer_median: Cited


@dataclass(frozen=True)
class Replacement:
    """Capacity that would have to be rebuilt, and what one unit costs."""

    capacity: Cited
    unit: str
    cost_per_unit: Assumption


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
    # Slice 8.7 — optional; a method without its inputs is ``not_applicable``.
    contracted_cash_flows: Sequence[Cited] = ()  # one per future year, in order
    regulated_asset_base: Cited | None = None
    segments: Sequence[Segment] = ()
    peer_multiples: Sequence[PeerMultiple] = ()
    replacement: Replacement | None = None


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


def _needs_bridge(inputs: Inputs) -> dict[str, Any] | None:
    if inputs.net_debt is None or inputs.diluted_shares is None:
        return {"state": INSUFFICIENT, "reason": "net debt or the diluted share count is "
                                                 "unknown"}
    return None


def _computed(ev: float, inputs: Inputs, **extra: Any) -> dict[str, Any]:
    return {"state": COMPUTED, "enterprise_value": round(ev, 0),
            "per_share": round(_per_share(ev, inputs), 4), **extra}


def contracted_dcf(inputs: Inputs, s: Scenario) -> dict[str, Any]:
    if not inputs.contracted_cash_flows:
        return {"state": NOT_APPLICABLE, "reason": "no cited contracted cash flows"}
    if inputs.risk_free is None or s.contract_spread is None:
        return {"state": INSUFFICIENT, "reason": "risk-free rate not cited or no named "
                                                 "contract spread"}
    if (bridge := _needs_bridge(inputs)) is not None:
        return bridge
    r = inputs.risk_free.value + s.contract_spread.value
    if r <= 0:
        return {"state": INSUFFICIENT, "reason": "the contract discount rate is not positive"}
    ev = sum(cf.value / (1 + r) ** t
             for t, cf in enumerate(inputs.contracted_cash_flows, start=1))
    return _computed(ev, inputs, discount_rate=round(r, 4),
                     years=len(inputs.contracted_cash_flows))


def rab_multiple(inputs: Inputs, s: Scenario) -> dict[str, Any]:
    if inputs.regulated_asset_base is None:
        return {"state": NOT_APPLICABLE, "reason": "no cited regulated asset base"}
    if s.rab_multiple is None or s.rab_multiple.value <= 0:
        return {"state": INSUFFICIENT, "reason": "no named RAB multiple"}
    if (bridge := _needs_bridge(inputs)) is not None:
        return bridge
    ev = inputs.regulated_asset_base.value * s.rab_multiple.value
    return _computed(ev, inputs, multiple=round(s.rab_multiple.value, 3))


def sotp(inputs: Inputs, s: Scenario) -> dict[str, Any]:
    if len(inputs.segments) < 2:
        return {"state": NOT_APPLICABLE, "reason": "fewer than two segments with a cited "
                                                   "metric"}
    if any(seg.metric.value <= 0 for seg in inputs.segments):
        return {"state": INSUFFICIENT, "reason": "a segment metric is zero or negative"}
    if (bridge := _needs_bridge(inputs)) is not None:
        return bridge
    values = [seg.metric.value * seg.multiple.value * (1 + s.multiple_adjustment.value)
              for seg in inputs.segments]
    parts = [{"segment": seg.name, "value": round(v, 0)}
             for seg, v in zip(inputs.segments, values)]
    return _computed(sum(values), inputs, parts=parts)


def multiple_method(pm: PeerMultiple) -> Any:
    def run(inputs: Inputs, s: Scenario) -> dict[str, Any]:
        if pm.subject_metric.value <= 0 or pm.peer_median.value <= 0:
            return {"state": INSUFFICIENT, "reason": f"{pm.key}: metric or median is not "
                                                     "positive"}
        if (bridge := _needs_bridge(inputs)) is not None:
            return bridge
        multiple = pm.peer_median.value * (1 + s.multiple_adjustment.value)
        return _computed(multiple * pm.subject_metric.value, inputs,
                         multiple=round(multiple, 4))
    return run


def replacement_floor(inputs: Inputs, s: Scenario) -> dict[str, Any]:
    rep = inputs.replacement
    if rep is None:
        return {"state": NOT_APPLICABLE, "reason": "no cited capacity with a named cost"}
    haircut = s.replacement_haircut.value if s.replacement_haircut else None
    if haircut is None or not 0 <= haircut < 1:
        return {"state": INSUFFICIENT, "reason": "no named haircut between 0 and 1"}
    if (bridge := _needs_bridge(inputs)) is not None:
        return bridge
    ev = rep.capacity.value * rep.cost_per_unit.value * (1 - haircut)
    return _computed(ev, inputs, unit=rep.unit, haircut=haircut)


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


def _method_state(per: dict[str, dict[str, Any]]) -> dict[str, Any]:
    computed = all(v.get("state") == COMPUTED for v in per.values()) and len(per) == 3
    return {"state": COMPUTED if computed else INSUFFICIENT, "scenarios": per,
            "reason": None if computed else next(
                (v.get("reason") for v in per.values() if v.get("state") != COMPUTED),
                "scenarios missing")}


def run_engine(inputs: Inputs) -> dict[str, Any]:
    """All methods × scenarios, the triangulated range, or ``insufficient_evidence``."""
    methods: dict[str, dict[str, Any]] = {}
    table: list[tuple[str, Any]] = [
        ("fcff_dcf", dcf), ("peer_multiple", peer_multiple),
        ("contracted_dcf", contracted_dcf), ("rab_multiple", rab_multiple), ("sotp", sotp),
        *((f"multiple:{pm.key}", multiple_method(pm)) for pm in inputs.peer_multiples),
    ]
    for name, fn in table:
        per = {sc: fn(inputs, inputs.scenarios[sc]) for sc in SCENARIOS
               if sc in inputs.scenarios}
        if per and all(v.get("state") == NOT_APPLICABLE for v in per.values()):
            if name not in ("fcff_dcf", "peer_multiple"):
                continue  # an 8.7 method without its inputs is simply absent
        methods[name] = _method_state(per)
    floors: dict[str, Any] = {}
    if inputs.replacement is not None:
        floors["replacement_cost"] = _method_state(
            {sc: replacement_floor(inputs, inputs.scenarios[sc]) for sc in SCENARIOS
             if sc in inputs.scenarios})
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
        **({"floors": floors} if floors else {}),
        "sensitivity_fcff_dcf": sensitivity(inputs, base) if base else None,
        "inputs": {
            "cited": {k: v.to_dict() for k, v in (
                ("base_fcff", inputs.base_fcff), ("ebit", inputs.ebit),
                ("net_debt", inputs.net_debt), ("diluted_shares", inputs.diluted_shares),
                ("risk_free", inputs.risk_free),
                ("equity_risk_premium", inputs.equity_risk_premium),
                ("peer_median_ev_to_ebit", inputs.peer_median_ev_to_ebit),
                ("regulated_asset_base", inputs.regulated_asset_base),
                ("replacement_capacity",
                 inputs.replacement.capacity if inputs.replacement else None))
                if v is not None},
            **({"contracted_cash_flows": [cf.to_dict() for cf in inputs.contracted_cash_flows]}
               if inputs.contracted_cash_flows else {}),
            **({"segments": [{"name": seg.name, "metric": seg.metric.to_dict(),
                              "multiple": seg.multiple.to_dict()}
                             for seg in inputs.segments]} if inputs.segments else {}),
            **({"peer_multiples": [{"key": pm.key, "subject_metric": pm.subject_metric.to_dict(),
                                    "peer_median": pm.peer_median.to_dict()}
                                   for pm in inputs.peer_multiples]}
               if inputs.peer_multiples else {}),
            "assumptions": {
                "size_premium": inputs.size_premium.to_dict(),
                "cost_of_debt_pre_tax": inputs.cost_of_debt_pre_tax.to_dict(),
                "tax_rate": inputs.tax_rate.to_dict(),
                "debt_weight": inputs.debt_weight.to_dict(),
                **{f"{sc}.{k}": a.to_dict()
                   for sc, s in inputs.scenarios.items()
                   for k, a in (("terminal_growth", s.terminal_growth), ("beta", s.beta),
                                ("multiple_adjustment", s.multiple_adjustment),
                                ("contract_spread", s.contract_spread),
                                ("rab_multiple", s.rab_multiple),
                                ("replacement_haircut", s.replacement_haircut))
                   if a is not None},
                **({"replacement_cost_per_unit": inputs.replacement.cost_per_unit.to_dict()}
                   if inputs.replacement else {}),
                **{f"{sc}.growth_year_{i}": a.to_dict()
                   for sc, s in inputs.scenarios.items() for i, a in enumerate(s.growth, 1)},
            },
        },
        "note": ("Computed by a deterministic engine from cited figures and named "
                 "assumptions, each listed; a range across methods and scenarios, not a "
                 "single figure and not a recommendation."),
    }


__all__ = ["Assumption", "Cited", "ENGINE_VERSION", "INSUFFICIENT", "Inputs", "MIN_METHODS",
           "NOT_APPLICABLE", "PeerMultiple", "Replacement", "Scenario", "Segment",
           "contracted_dcf", "dcf", "multiple_method", "peer_multiple", "rab_multiple",
           "replacement_floor", "run_engine", "sensitivity", "sotp", "wacc"]
