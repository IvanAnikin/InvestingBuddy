"""Valuation engine inputs and the report block — V3.20 slice 8.6 wiring (ADR-099).

Pure. Builds ``valuation_engine.Inputs`` from what this run already holds, runs the engine
and turns its range into the report's top-level ``valuation`` block, set against a dated
price.

WHERE EACH INPUT COMES FROM
* Cited (a figure with its source, period and currency):
  - net debt and EBIT: the 8.1 enterprise-value block;
  - diluted shares: the newest year of the 8.5 dilution series;
  - base free cash flow to the firm: operating cash flow − capital expenditure, from the
    newest annual period that holds both (the 8.4 ``pick_annual``);
  - the layer-peer median EV/EBIT: the 8.2 block;
  - the risk-free rate: FRED DGS10 (USD only), fetched by the step;
  - the equity risk premium: ``V3_EQUITY_RISK_PREMIUM_JSON`` — a figure the owner enters
    WITH its publication URL and date. Empty means the DCF is ``insufficient_evidence``.
* Named assumptions (``proposed_by="engine_default"``, each with its rationale): growth
  per scenario, terminal growth, beta, the peer-multiple adjustment, the size premium,
  the cost of debt, the tax rate and the debt weight. No model proposes them yet.

The price is the dated market capitalisation divided by the diluted share count; both
dates are shown. ``relative_position`` (the only ADR-099 term) is an enum at
``valuation.relative_position``, allowed there by the gate and nowhere else.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.calculations import valuation_engine as eng
from app.services.valuation.precedents import precedent_peer_multiple

BLOCK_VERSION = "2026-11-16.1"
SMALL_CAP_USD = 2_000_000_000
#: Bounds on the growth a filed revenue history may imply for the base scenario.
GROWTH_FLOOR, GROWTH_CAP = -0.05, 0.15
SCENARIO_SPREAD = 0.03

UNDERVALUED, IN_LINE, OVERVALUED = "undervalued", "in_line", "overvalued"


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _a(value: float, rationale: str) -> eng.Assumption:
    return eng.Assumption(value=round(value, 4), rationale=rationale)


def equity_risk_premium(raw: str | None) -> eng.Cited | None:
    """The owner-entered ERP: ``{"value", "source_url", "as_of"}``; anything else is None."""
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        return None
    if not isinstance(data, Mapping):
        return None
    value, url = _f(data.get("value")), data.get("source_url")
    if value is None or not 0 < value < 0.15 or not isinstance(url, str) or not url:
        return None
    return eng.Cited(value=value, source_url=url, period=str(data.get("as_of") or "") or None)


def revenue_growth(history: Sequence[Mapping[str, Any]]) -> tuple[float | None, str]:
    """Compound annual growth over the held annual revenues (oldest → newest)."""
    rows = [r for r in history if (_f(r.get("value")) or 0) > 0]
    if len(rows) < 2:
        return None, "fewer than two annual revenue figures are held"
    first, last = _f(rows[0]["value"]), _f(rows[-1]["value"])
    assert first is not None and last is not None
    years = len(rows) - 1
    cagr = (last / first) ** (1 / years) - 1
    bounded = max(GROWTH_FLOOR, min(GROWTH_CAP, cagr))
    return bounded, (f"filed revenue grew {cagr:.1%} a year over {years} year(s) "
                     f"({rows[0].get('period')} to {rows[-1].get('period')}), bounded to "
                     f"{GROWTH_FLOOR:.0%}..{GROWTH_CAP:.0%}")


def build_inputs(
    *, ev_block: Mapping[str, Any] | None, peers_block: Mapping[str, Any] | None,
    depth_block: Mapping[str, Any] | None, cash_flow: Mapping[str, Any] | None,
    revenue_history: Sequence[Mapping[str, Any]], risk_free: eng.Cited | None,
    erp: eng.Cited | None, market_cap_usd: float | None,
    precedents_block: Mapping[str, Any] | None = None,
) -> eng.Inputs | None:
    """``None`` when there is no computed EV (no currency, no bridge to per share)."""
    ev = (ev_block or {}).get("enterprise_value") or {}
    if ev.get("state") != "computed" or not ev.get("currency"):
        return None
    ccy = str(ev["currency"])
    nd = (ev_block or {}).get("net_debt") or {}
    first_src = next(iter(nd.get("sources") or []), None)
    nd_url = (first_src if isinstance(first_src, str) else
              first_src.get("source_url") if isinstance(first_src, Mapping) else None)
    net_debt = (eng.Cited(value=float(nd["value"]), currency=ccy, period=nd.get("period"),
                          source_url=nd_url)
                if nd.get("state") == "computed" and _f(nd.get("value")) is not None else None)
    mult = next((m for m in (ev_block or {}).get("multiples") or []
                 if m.get("key") == "ev_to_ebit" and _f(m.get("ebit")) is not None), None)
    ebit = (eng.Cited(value=float(mult["ebit"]), currency=ccy, period=mult.get("period"),
                      source_url=mult.get("source_url")) if mult else None)
    years = (((depth_block or {}).get("dilution") or {}).get("years") or [])
    shares = (eng.Cited(value=float(years[-1]["shares"]), currency=None,
                        period=years[-1].get("period_end"),
                        source_url=years[-1].get("source_url"))
              if years and (_f(years[-1].get("shares")) or 0) > 0 else None)
    fcff = None
    if cash_flow and (cash_flow.get("currency") or "").upper() == ccy:
        cfo = _f(cash_flow.get("operating_cash_flow"))
        capex = _f(cash_flow.get("capital_expenditure"))
        if cfo is not None and capex is not None:
            src: Mapping[str, Any] = next(iter(cash_flow.get("sources") or []), {}) or {}
            fcff = eng.Cited(value=cfo - abs(capex), currency=ccy,
                             period=cash_flow.get("period"), source_url=src.get("source_url"))
    median = _f((peers_block or {}).get("median_ev_to_ebit"))
    # Cited from the comparable-set records (each peer row carries its record id).
    peer = (eng.Cited(value=median, currency=None,
                      period=(peers_block or {}).get("median_basis"), source_url=None)
            if median and median > 0 else None)
    rf = risk_free if risk_free is not None and ccy == "USD" else None

    growth, why = revenue_growth(revenue_history)
    if growth is None:
        growth, why = 0.0, f"{why}; zero growth assumed"
    small = market_cap_usd is not None and market_cap_usd < SMALL_CAP_USD
    mc = _f(((ev_block or {}).get("market_cap") or {}).get("value"))
    nd_v = net_debt.value if net_debt else None
    weight = (max(0.0, min(0.6, nd_v / (nd_v + mc)))
              if nd_v is not None and mc and nd_v + mc > 0 else 0.0)

    def scenario(shift: float, terminal: float, beta: float, adj: float, tag: str
                 ) -> eng.Scenario:
        return eng.Scenario(
            growth=[_a(growth + shift, f"{tag}: {why}, {shift:+.0%}")] * eng.EXPLICIT_YEARS,
            terminal_growth=_a(terminal, f"{tag}: long-run nominal growth assumption"),
            beta=_a(beta, f"{tag}: no cited beta is held; market beta 1.0 {beta - 1:+.1f}"),
            multiple_adjustment=_a(adj, f"{tag}: adjustment to the layer-peer median"),
        )

    return eng.Inputs(
        currency=ccy, base_fcff=fcff, ebit=ebit, net_debt=net_debt, diluted_shares=shares,
        risk_free=rf, equity_risk_premium=erp,
        size_premium=_a(0.02 if small else 0.0,
                        "small-cap premium for a market cap under USD 2bn" if small
                        else "no size premium above USD 2bn"),
        cost_of_debt_pre_tax=_a((rf.value if rf else 0.04) + 0.02,
                                "risk-free rate plus a 2-point credit spread"),
        tax_rate=_a(0.21 if ccy == "USD" else 0.25,
                    "US federal statutory rate" if ccy == "USD" else "a generic 25% rate"),
        debt_weight=_a(weight, "net debt / (net debt + market cap), bounded to 0..0.6"),
        peer_median_ev_to_ebit=peer,
        # V3.20 slice 8.3 — a precedent EV/EBIT method, only from >=3 approved deals.
        peer_multiples=tuple(pm for pm in (precedent_peer_multiple(precedents_block, ev_block),)
                             if pm is not None),
        scenarios={
            "bear": scenario(-SCENARIO_SPREAD, 0.015, 1.2, -0.15, "bear"),
            "base": scenario(0.0, 0.02, 1.0, 0.0, "base"),
            "bull": scenario(SCENARIO_SPREAD, 0.025, 0.9, 0.10, "bull"),
        },
    )


def price_reference(ev_block: Mapping[str, Any] | None,
                    shares: eng.Cited | None) -> dict[str, Any] | None:
    mc = (ev_block or {}).get("market_cap") or {}
    cap = _f(mc.get("value"))
    if cap is None or shares is None or shares.value <= 0:
        return None
    return {"per_share": round(cap / shares.value, 4), "currency": mc.get("currency"),
            "market_cap_as_of": mc.get("as_of"), "market_cap_source_url": mc.get("source_url"),
            "shares_period": shares.period,
            "formula": "market_cap / diluted weighted-average shares"}


def valuation_block(inputs: eng.Inputs | None, ev_block: Mapping[str, Any] | None
                    ) -> dict[str, Any] | None:
    if inputs is None:
        return None
    result = eng.run_engine(inputs)
    rng = result.get("valuation_range") or {}
    price = price_reference(ev_block, inputs.diluted_shares)
    position: str | None = None
    vs: dict[str, Any] | None = None
    if rng.get("state") == eng.COMPUTED and price is not None and (
            price.get("currency") == rng.get("currency")):
        p = price["per_share"]
        low, mid, high = rng["per_share_low"], rng["per_share_mid"], rng["per_share_high"]
        position = UNDERVALUED if p < low else OVERVALUED if p > high else IN_LINE
        vs = {k: round(100 * (v - p) / p, 1) for k, v in
              (("low", low), ("mid", mid), ("high", high))} if p > 0 else None
    return {
        "block_version": BLOCK_VERSION,
        **result,
        "price_reference": price,
        "relative_position": position,
        "upside_downside_pct": vs,
    }


__all__ = ["BLOCK_VERSION", "build_inputs", "equity_risk_premium", "price_reference",
           "revenue_growth", "valuation_block"]
