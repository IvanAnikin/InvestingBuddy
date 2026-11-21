"""What today's price implies — V3.20 slice 8.4 (spec §3.8), deterministic.

Reverse arithmetic over the dated enterprise value (slice 8.1), the layer-peer median
(slice 8.2) and the issuer's own filed figures. Every figure is cited or a NAMED
assumption; nothing is estimated and no model is called.

* **Peer-relative position** — the subject's EV/EBIT over the layer-peer median:
  ``below_peer_median`` (≤ 0.85×), ``in_line``, ``above_peer_median`` (≥ 1.15×).
* **Implied operating margin** — the EBIT margin at which today's EV would equal the
  layer-peer median EV/EBIT: ``EV ÷ median ÷ revenue``, shown beside the filed margin.
* **Implied perpetual growth** — the constant growth of free cash flow (operating cash
  flow − capital expenditure, newest annual period, filed) at which a Gordon model gives
  today's EV: ``g = (r·EV − FCF) ÷ (EV + FCF)``, for each discount rate on a named,
  illustrative grid. NOT a cited cost of capital: the grid is labelled an assumption.
  ``not_meaningful`` when free cash flow is zero or negative.
* **Observed discount characteristics** — only when the position is below the peer
  median: characteristics the other blocks observed that often accompany a lower
  multiple (small size, leverage, refinancing wall, dilution, obscurity, little passive
  ownership, a controlling holder, a non-USD listing, a run of results below
  expectations). Each names its block, value and threshold. **None is established here
  as the cause** — the block says so.

The vocabulary avoids the safety gate's phrase tier: no value judgement is made, and the
words describe what the market price implies, not what the company is worth.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

BLOCK_VERSION = "2026-11-16.1"
BELOW, IN_LINE, ABOVE = "below_peer_median", "in_line", "above_peer_median"
LOW_BAND, HIGH_BAND = Decimal("0.85"), Decimal("1.15")
#: Named, illustrative discount rates — an assumption, not a cited cost of capital.
DISCOUNT_RATE_GRID: tuple[Decimal, ...] = (Decimal("0.08"), Decimal("0.10"),
                                           Decimal("0.12"))
GRID_RATIONALE = ("an illustrative grid spanning common cost-of-capital levels for listed "
                  "industrial companies; not derived from cited market rates")

# Observed-characteristic thresholds.
LEVERAGE_NET_DEBT_TO_EBIT = Decimal("3")
NEAR_MATURITY_SHARE = Decimal("0.25")
DILUTION_PCT = Decimal("5")
OBSCURITY_SCORE = 60
PASSIVE_PCT = Decimal("5")
RESULTS_STREAK = 2

COMPUTED, NOT_AVAILABLE, NOT_MEANINGFUL = "computed", "not_available", "not_meaningful"


def _d(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _f(value: Decimal | None, places: int = 4) -> float | None:
    return None if value is None else float(round(value, places))


def _ev(ev_block: Mapping[str, Any] | None) -> tuple[Decimal | None, str | None]:
    ev = (ev_block or {}).get("enterprise_value") or {}
    if ev.get("state") != COMPUTED:
        return None, None
    return _d(ev.get("value")), ev.get("currency")


def _ev_to_ebit(ev_block: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    return next((m for m in (ev_block or {}).get("multiples") or []
                 if m.get("key") == "ev_to_ebit"), None)


def peer_position(ev_block: Mapping[str, Any] | None,
                  peers_block: Mapping[str, Any] | None) -> dict[str, Any]:
    mult = _ev_to_ebit(ev_block) or {}
    subject = _d(mult.get("value")) if mult.get("state") == COMPUTED else None
    median = _d((peers_block or {}).get("median_ev_to_ebit"))
    if subject is None or median is None or median <= 0:
        return {"state": NOT_AVAILABLE, "reason": (
            "no computed EV/EBIT for this company" if subject is None else
            (peers_block or {}).get("median_reason") or "no layer-peer median EV/EBIT")}
    ratio = subject / median
    position = BELOW if ratio <= LOW_BAND else ABOVE if ratio >= HIGH_BAND else IN_LINE
    return {"state": COMPUTED, "position": position, "subject_ev_to_ebit": _f(subject, 2),
            "peer_median_ev_to_ebit": _f(median, 2), "ratio": _f(ratio, 3),
            "peer_count": (peers_block or {}).get("peer_count"),
            "bands": {"below": float(LOW_BAND), "above": float(HIGH_BAND)}}


def implied_margin(ev_block: Mapping[str, Any] | None, peers_block: Mapping[str, Any] | None,
                   revenue: Mapping[str, Any] | None) -> dict[str, Any]:
    ev, ccy = _ev(ev_block)
    median = _d((peers_block or {}).get("median_ev_to_ebit"))
    rev = _d((revenue or {}).get("value"))
    if ev is None or median is None or median <= 0:
        return {"state": NOT_AVAILABLE,
                "reason": "needs a computed EV and a layer-peer median EV/EBIT"}
    if rev is None or rev <= 0 or (revenue or {}).get("currency") != ccy:
        return {"state": NOT_AVAILABLE,
                "reason": "no annual revenue held in the EV's currency"}
    margin = ev / median / rev
    mult = _ev_to_ebit(ev_block) or {}
    ebit = _d(mult.get("ebit"))
    return {"state": COMPUTED, "implied_ebit_margin": _f(margin),
            "filed_ebit_margin": _f(ebit / rev) if ebit is not None else None,
            "revenue": _f(rev, 0), "revenue_period": (revenue or {}).get("period"),
            "revenue_source_url": (revenue or {}).get("source_url"), "currency": ccy,
            "formula": "enterprise_value / peer_median_ev_to_ebit / revenue"}


def implied_growth(ev_block: Mapping[str, Any] | None,
                   cash_flow: Mapping[str, Any] | None) -> dict[str, Any]:
    ev, ccy = _ev(ev_block)
    cfo = _d((cash_flow or {}).get("operating_cash_flow"))
    capex = _d((cash_flow or {}).get("capital_expenditure"))
    if ev is None or ev <= 0:
        return {"state": NOT_AVAILABLE, "reason": "no computed, positive EV"}
    if cfo is None or capex is None or (cash_flow or {}).get("currency") != ccy:
        return {"state": NOT_AVAILABLE, "reason": (
            "no annual operating cash flow and capital expenditure for one period in the "
            "EV's currency")}
    fcf = cfo - abs(capex)
    base = {"free_cash_flow": _f(fcf, 0), "operating_cash_flow": _f(cfo, 0),
            "capital_expenditure": _f(abs(capex), 0),
            "period": (cash_flow or {}).get("period"), "currency": ccy,
            "sources": list((cash_flow or {}).get("sources") or [])}
    if fcf <= 0:
        return {"state": NOT_MEANINGFUL, **base,
                "reason": "free cash flow is zero or negative; no growth rate solves it"}
    grid = [{"discount_rate": float(r),
             "implied_perpetual_growth": _f((r * ev - fcf) / (ev + fcf))}
            for r in DISCOUNT_RATE_GRID]
    return {"state": COMPUTED, **base, "grid": grid,
            "discount_rate_assumption": {"kind": "assumption",
                                         "values": [float(r) for r in DISCOUNT_RATE_GRID],
                                         "rationale": GRID_RATIONALE},
            "formula": "g = (r * EV - FCF) / (EV + FCF)",
            "fcf_basis": ("operating cash flow - capital expenditure; operating cash flow "
                          "is after interest paid, so this is not a pure firm-level cash "
                          "flow")}


def _char(key: str, label: str, block: str, observed: Any, threshold: Any) -> dict[str, Any]:
    return {"key": key, "label": label, "source_block": block, "observed": observed,
            "threshold": threshold}


def discount_characteristics(blocks: Mapping[str, Mapping[str, Any] | None], *,
                             size_fit: Mapping[str, Any] | None,
                             ev_block: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Observed characteristics (pure). ``blocks`` keyed ``section.block``."""
    out: list[dict[str, Any]] = []
    peers = blocks.get("valuation_context.layer_peers") or {}
    caps = sorted(c for c in (_d(p.get("market_cap_usd")) for p in peers.get("peers") or [])
                  if c is not None)
    cap = _d((size_fit or {}).get("market_cap_usd"))
    if cap is not None and caps and cap < caps[len(caps) // 2]:
        out.append(_char("smaller_than_peers", "Smaller than the median layer peer",
                         "valuation_context.layer_peers", _f(cap, 0),
                         f"peer median market cap USD {_f(caps[len(caps) // 2], 0)}"))
    nd = _d(((ev_block or {}).get("net_debt") or {}).get("value"))
    ebit = _d((_ev_to_ebit(ev_block) or {}).get("ebit"))
    if nd is not None and ebit is not None and ebit > 0 and nd / ebit > LEVERAGE_NET_DEBT_TO_EBIT:
        out.append(_char("leverage", "Net debt above 3x operating profit",
                         "valuation_context.enterprise_value", _f(nd / ebit, 2),
                         float(LEVERAGE_NET_DEBT_TO_EBIT)))
    depth = blocks.get("financial_capacity.financial_depth") or {}
    ladder = depth.get("debt_maturity_ladder") or {}
    total = _d(ladder.get("total"))
    near = sum((_d(b.get("value")) or Decimal(0) for b in ladder.get("buckets") or []
                if b.get("bucket") in ("next_12_months", "year_2")), Decimal(0))
    if total and total > 0 and near / total >= NEAR_MATURITY_SHARE:
        out.append(_char("refinancing_wall", "A quarter or more of debt matures within two years",
                         "financial_capacity.financial_depth", _f(near / total, 3),
                         float(NEAR_MATURITY_SHARE)))
    years = (depth.get("dilution") or {}).get("years") or []
    change = _d(years[-1].get("change_pct")) if years else None
    if change is not None and change > DILUTION_PCT:
        out.append(_char("dilution", "Diluted share count up more than 5% in the latest year",
                         "financial_capacity.financial_depth", _f(change, 2),
                         float(DILUTION_PCT)))
    score = (blocks.get("valuation_context.obscurity") or {}).get("score")
    if isinstance(score, int) and score >= OBSCURITY_SCORE:
        out.append(_char("obscurity", "Little followed (obscurity score 60 or more)",
                         "valuation_context.obscurity", score, OBSCURITY_SCORE))
    passive = blocks.get("valuation_context.passive_ownership") or {}
    pct = _d(passive.get("pct_of_shares_outstanding"))
    if passive.get("state") == "held" and pct is not None and pct < PASSIVE_PCT:
        out.append(_char("low_passive_ownership",
                         "Under 5% of shares held by US-registered funds",
                         "valuation_context.passive_ownership", _f(pct, 2),
                         float(PASSIVE_PCT)))
    group = (blocks.get("business_model.relationships") or {}).get("parent_and_group") or {}
    parents = [p.get("counterparty_name") for p in (group.get("direct_parents") or [])
               if p.get("counterparty_name")]
    if parents:
        out.append(_char("controlling_holder", "Has a disclosed parent company",
                         "business_model.relationships", parents[:3], "any"))
    _, ccy = _ev(ev_block)
    if ccy and ccy != "USD":
        out.append(_char("non_usd_listing", "Priced outside US dollars",
                         "valuation_context.enterprise_value", ccy, "USD"))
    streak = (blocks.get("financial_capacity.earnings_track_record") or {}).get(
        "eps_streak") or {}
    quarters = streak.get("quarters")
    if streak.get("direction") == "below" and isinstance(quarters, int) \
            and quarters >= RESULTS_STREAK:
        out.append(_char("results_below_expectations",
                         "Two or more consecutive quarters below the held EPS expectation",
                         "financial_capacity.earnings_track_record", quarters,
                         RESULTS_STREAK))
    return out


def market_implied_block(
    *, ev_block: Mapping[str, Any] | None, peers_block: Mapping[str, Any] | None,
    revenue: Mapping[str, Any] | None, cash_flow: Mapping[str, Any] | None,
    blocks: Mapping[str, Mapping[str, Any] | None], size_fit: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """The block, or ``None`` when there is no computed EV (nothing to reverse)."""
    if _ev(ev_block)[0] is None:
        return None
    position = peer_position(ev_block, peers_block)
    chars: Sequence[dict[str, Any]] | None = None
    if position.get("position") == BELOW:
        chars = discount_characteristics(blocks, size_fit=size_fit, ev_block=ev_block)
    return {
        "version": BLOCK_VERSION,
        "peer_position": position,
        "implied_margin": implied_margin(ev_block, peers_block, revenue),
        "implied_growth": implied_growth(ev_block, cash_flow),
        "discount_characteristics": None if chars is None else list(chars),
        "note": (
            "Reverse arithmetic: what today's enterprise value implies under stated "
            "formulas, from filed figures and a named discount-rate grid. Observed "
            "characteristics often accompany a lower multiple; none is established here as "
            "the cause. Not a judgement of value."),
    }


__all__ = [
    "BLOCK_VERSION",
    "DISCOUNT_RATE_GRID",
    "discount_characteristics",
    "implied_growth",
    "implied_margin",
    "market_implied_block",
    "peer_position",
]
