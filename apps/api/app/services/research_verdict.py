"""Research verdict — V3.20 slice 9.2 (ADR-098), deterministic.

An eight-pillar rubric scored from the assembled professional report, then a verdict
``PASS | WATCHLIST | SHORTLIST`` computed from three inputs only: the pillar scores, the
evidence confidence and the valuation range state (ADR-099). Same stored report, same
verdict. No model is called; the label is never written by one.

SCORING (``RUBRIC_VERSION``)
* Each pillar reads named report sections (and, for some, typed blocks). A pillar starts
  at 3 and moves by the balance of supportive vs adverse findings in its sections
  (``±2`` at most) and by named block signals (each ``±1``). The score is clamped to 1–5.
* A pillar with no directional finding and no block signal is NOT scored
  (``insufficient_evidence``). Missing evidence lowers confidence; it never becomes a 3.
* Every score lists what it rests on: the finding labels (with their source quality) and
  the block signals (with the block they came from).

VERDICT (``THRESHOLDS``)
* composite = weight-renormalised mean of the scored pillars.
* ``SHORTLIST`` needs composite ≥ ``shortlist_min_composite`` and no cap.
* ``WATCHLIST`` needs composite ≥ ``watchlist_min_composite`` (or a capped SHORTLIST).
* otherwise ``PASS``.
* Caps (each one forbids ``SHORTLIST``): a key pillar — financial quality
  (``balance_sheet_resilience``), competitive position (``moat_competitive``) or
  valuation (``valuation_gap``) — unscored or resting mainly on thin (web-only) sources;
  the valuation range ``insufficient_evidence``; any pillar scored 1; confidence below
  ``shortlist_min_confidence``; scored-weight coverage below ``min_coverage``.

SAFETY: the label sits at ``verdict.recommendation`` only, which the gate allows by path
(ADR-098 §3, ``safety_terms.VERDICT_ALLOWANCE``). No other string in this block names a
label, and none says buy, sell, hold, size or target.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

RUBRIC_VERSION = "2026-11-16.1"

PASS, WATCHLIST, SHORTLIST = "PASS", "WATCHLIST", "SHORTLIST"
VERDICTS = (PASS, WATCHLIST, SHORTLIST)
SCORED, UNSCORED = "scored", "insufficient_evidence"

THRESHOLDS: dict[str, Any] = {
    "version": RUBRIC_VERSION,
    "shortlist_min_composite": 3.6,
    "watchlist_min_composite": 3.0,
    "shortlist_min_confidence": 0.6,
    "min_coverage": 0.5,
    "thin_share_max": 0.5,
    "min_directional_findings": 2,
}

#: Source kinds that alone make a finding thin (D-quality): open-web text.
THIN_SOURCE_KINDS: frozenset[str] = frozenset(
    {"web", "website", "web_search", "web_context", "news"})

#: Plain meanings, stated on every rendering (ADR-098 §1). No label token appears here;
#: the reader sees the label beside its meaning.
MEANINGS: dict[str, str] = {
    SHORTLIST: ("The evidence supports this candidate enough to justify further work and a "
                "decision by the reader."),
    WATCHLIST: ("The thesis is plausible but the evidence is incomplete: monitor it for the "
                "named missing items and catalysts."),
    PASS: "Not pursued further on current evidence.",
}
NOTE = ("A research triage computed by fixed, versioned rules from the cited evidence in "
        "this report. It says what to research next. It is not investment advice, not a "
        "recommendation to trade and not a forecast.")


@dataclass(frozen=True)
class Pillar:
    key: str
    label: str
    weight: float
    sections: tuple[str, ...]
    domains: tuple[str, ...] = ()  # empty = every domain in the sections


PILLARS: tuple[Pillar, ...] = (
    Pillar("asset_quality", "Asset quality", 0.15, ("operations",)),
    Pillar("balance_sheet_resilience", "Financial quality and balance sheet", 0.15,
           ("financial_capacity",)),
    Pillar("valuation_gap", "Valuation", 0.15, ("valuation_context",)),
    Pillar("moat_competitive", "Competitive position", 0.15, ("competitive_position",)),
    Pillar("macro_geo_tailwind", "Structural tailwind", 0.10,
           ("industry_and_market", "thesis_fit")),
    Pillar("catalyst_proximity", "Catalysts", 0.10, ("growth_and_catalysts",)),
    Pillar("management_governance", "Management and governance", 0.10,
           ("risks_and_counter_thesis", "business_model"), ("governance",)),
    Pillar("underresearched_edge", "Under-researched edge", 0.10, ()),
)
KEY_PILLARS: tuple[str, ...] = ("balance_sheet_resilience", "moat_competitive",
                                "valuation_gap")
assert abs(sum(p.weight for p in PILLARS) - 1.0) < 1e-9


def _section(report: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    for s in report.get("sections") or ():
        if isinstance(s, Mapping) and s.get("key") == key:
            return s
    return {}


def _thin(finding: Mapping[str, Any]) -> bool:
    kinds = {str(k) for k in finding.get("source_kinds") or () if k}
    return not kinds or kinds <= THIN_SOURCE_KINDS


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _signal(block: str, signal: str, delta: int, observed: Any = None,
            source_url: Any = None) -> dict[str, Any]:
    return {"kind": "block", "block": block, "signal": signal, "delta": delta,
            "observed": observed, "source_url": source_url, "quality": "primary"}


def _finding_items(report: Mapping[str, Any], pillar: Pillar) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in pillar.sections:
        for f in _section(report, key).get("findings") or ():
            if not isinstance(f, Mapping) or f.get("superseded_by_finding_id"):
                continue
            if pillar.domains and f.get("domain") not in pillar.domains:
                continue
            if f.get("direction") not in ("supportive", "adverse"):
                continue
            out.append({"kind": "finding", "label": f.get("label"),
                        "finding_id": f.get("finding_id"), "direction": f.get("direction"),
                        "quality": "thin" if _thin(f) else "primary"})
    return out


def _finding_delta(items: Sequence[Mapping[str, Any]]) -> int:
    s = sum(1 for i in items if i.get("direction") == "supportive")
    a = sum(1 for i in items if i.get("direction") == "adverse")
    if s + a == 0:
        return 0
    if s + a < THRESHOLDS["min_directional_findings"]:
        return 1 if s > a else -1
    return max(-2, min(2, round(2 * (s - a) / (s + a))))


def _block_signals(report: Mapping[str, Any], pillar: str,
                   valuation: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    vc = _section(report, "valuation_context")
    implied = vc.get("market_implied") or {}
    chars = {c.get("key"): c for c in implied.get("discount_characteristics") or ()
             if isinstance(c, Mapping)}
    if pillar == "balance_sheet_resilience":
        for key in ("leverage", "refinancing_wall", "dilution"):
            if key in chars:
                out.append(_signal("valuation_context.market_implied", key, -1,
                                   chars[key].get("observed")))
        nd = (vc.get("enterprise_value") or {}).get("net_debt") or {}
        value = _num(nd.get("value"))
        if nd.get("state") == "computed" and value is not None and value < 0:
            out.append(_signal("valuation_context.enterprise_value", "net_cash", 1, value,
                               next(iter(nd.get("sources") or []), None)))
    elif pillar == "valuation_gap":
        position = (valuation or {}).get("relative_position")
        if position in ("undervalued", "overvalued", "in_line"):
            delta = {"undervalued": 2, "in_line": 0, "overvalued": -2}[position]
            # Restated as a neutral code: the ADR-099 term is allowed only at
            # ``valuation.relative_position``, never in this block.
            code = {"undervalued": "price_below_range", "in_line": "price_within_range",
                    "overvalued": "price_above_range"}[position]
            out.append(_signal("valuation", "position_vs_range", delta, code))
        else:
            peer = implied.get("peer_position") or {}
            if peer.get("state") == "computed":
                delta = {"below_peer_median": 1, "above_peer_median": -1}.get(
                    str(peer.get("position")), 0)
                out.append(_signal("valuation_context.market_implied", "peer_position",
                                   delta, peer.get("position")))
    elif pillar == "macro_geo_tailwind":
        dims = _section(report, "thesis_fit").get("dimensions") or ()
        states = [d.get("status") for d in dims if isinstance(d, Mapping)]
        if states:
            evidenced = states.count("evidenced") / len(states)
            missing = states.count("not_established") / len(states)
            delta = 1 if evidenced >= 2 / 3 else -1 if missing > 0.5 else 0
            out.append(_signal("thesis_fit.dimensions", "dimensions_evidenced", delta,
                               f"{states.count('evidenced')} of {len(states)}"))
    elif pillar == "catalyst_proximity":
        events = (_section(report, "growth_and_catalysts").get("change_events") or {}
                  ).get("events") or ()
        if events:
            out.append(_signal("growth_and_catalysts.change_events", "change_events", 1,
                               len(events)))
        streak = ((_section(report, "financial_capacity").get("earnings_track_record")
                   or {}).get("eps_streak") or {})
        quarters = streak.get("quarters")
        if isinstance(quarters, int) and quarters >= 2 and streak.get("direction") in (
                "above", "below"):
            out.append(_signal("financial_capacity.earnings_track_record", "eps_streak",
                               1 if streak["direction"] == "above" else -1,
                               f"{quarters} quarters {streak['direction']}"))
    return out


def _edge_score(report: Mapping[str, Any]) -> tuple[int | None, list[dict[str, Any]]]:
    """Under-researched edge: the measured obscurity score, not an assertion."""
    obscurity = _section(report, "valuation_context").get("obscurity") or {}
    score = obscurity.get("score")
    if not isinstance(score, int) or isinstance(score, bool):
        return None, []
    pillar_score = 5 if score >= 80 else 4 if score >= 60 else 3 if score >= 40 else (
        2 if score >= 20 else 1)
    return pillar_score, [_signal("valuation_context.obscurity", "obscurity_score",
                                  pillar_score - 3, score)]


def score_pillar(report: Mapping[str, Any], pillar: Pillar,
                 valuation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if pillar.key == "underresearched_edge":
        score, evidence = _edge_score(report)
        rule = "obscurity score: 80+ → 5, 60+ → 4, 40+ → 3, 20+ → 2, else 1"
    else:
        items = _finding_items(report, pillar)
        signals = _block_signals(report, pillar.key, valuation)
        evidence = items + signals
        score = (None if not evidence else
                 max(1, min(5, 3 + _finding_delta(items) + sum(s["delta"] for s in signals))))
        rule = ("3, moved by the supportive/adverse balance of the findings in "
                f"{', '.join(pillar.sections)} (at most 2) and by each block signal (1)")
    thin = sum(1 for e in evidence if e.get("quality") == "thin")
    return {
        "key": pillar.key, "label": pillar.label, "weight": pillar.weight,
        "state": SCORED if score is not None else UNSCORED, "score": score,
        "evidence": evidence, "thin_share": round(thin / len(evidence), 3) if evidence else None,
        "rule": rule,
    }


def verdict_block(report: Mapping[str, Any], *,
                  valuation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The ``verdict`` block (pure). ``valuation`` is the ADR-099 block, if any."""
    pillars = [score_pillar(report, p, valuation) for p in PILLARS]
    scored = [p for p in pillars if p["state"] == SCORED]
    coverage = round(sum(p["weight"] for p in scored), 3)
    evidence = [e for p in scored for e in p["evidence"]]
    primary_share = (round(sum(1 for e in evidence if e.get("quality") == "primary")
                           / len(evidence), 3) if evidence else 0.0)
    confidence = round(coverage * (0.5 + 0.5 * primary_share), 3)
    composite = (round(sum(p["score"] * p["weight"] for p in scored) / coverage, 2)
                 if scored else None)

    by_key = {p["key"]: p for p in pillars}
    caps: list[dict[str, str]] = []
    for key in KEY_PILLARS:
        p = by_key[key]
        if p["state"] != SCORED:
            caps.append({"key": f"{key}_unscored",
                         "reason": f"{p['label']}: no scored evidence"})
        elif (p["thin_share"] or 0) > THRESHOLDS["thin_share_max"]:
            caps.append({"key": f"{key}_thin",
                         "reason": f"{p['label']}: rests mainly on open-web sources"})
    range_state = ((valuation or {}).get("valuation_range") or {}).get("state")
    if range_state != "computed":
        caps.append({"key": "valuation_insufficient",
                     "reason": "no computed valuation range (ADR-099 fails closed)"})
    if any(p["score"] == 1 for p in scored):
        caps.append({"key": "pillar_at_one", "reason": "a pillar scored 1"})
    if confidence < THRESHOLDS["shortlist_min_confidence"]:
        caps.append({"key": "low_confidence",
                     "reason": f"evidence confidence {confidence} below "
                               f"{THRESHOLDS['shortlist_min_confidence']}"})
    if coverage < THRESHOLDS["min_coverage"]:
        caps.append({"key": "low_coverage",
                     "reason": f"scored pillars cover {coverage} of the weight"})

    if composite is None or composite < THRESHOLDS["watchlist_min_composite"]:
        label = PASS
    elif composite >= THRESHOLDS["shortlist_min_composite"] and not caps:
        label = SHORTLIST
    else:
        label = WATCHLIST
    capped = (composite is not None and composite >= THRESHOLDS["shortlist_min_composite"]
              and bool(caps))

    missing = [f"{p['label']}: no supportive or adverse cited evidence was found"
               for p in pillars if p["state"] != SCORED]
    triggers = [f"Primary-source evidence on {by_key[c['key'].rsplit('_', 1)[0]]['label']}"
                for c in caps if c["key"].endswith(("_unscored", "_thin"))
                and c["key"].rsplit("_", 1)[0] in by_key]
    if range_state != "computed":
        triggers.append("A computed valuation range from cited inputs")
    return {
        "version": RUBRIC_VERSION,
        "recommendation": label,
        "meaning": MEANINGS[label],
        "composite_score": composite,
        "confidence": {"value": confidence,
                       "band": "high" if confidence >= 0.7 else "medium"
                       if confidence >= 0.45 else "low",
                       "coverage": coverage, "primary_share": primary_share},
        "pillars": pillars,
        "caps": caps,
        "capped": capped,
        "thresholds": dict(THRESHOLDS),
        "missing_information": missing,
        "watchlist_triggers": triggers,
        "rationale": _rationale(composite, coverage, caps, capped),
        "note": NOTE,
    }


def _rationale(composite: float | None, coverage: float, caps: Sequence[Mapping[str, str]],
               capped: bool) -> str:
    if composite is None:
        return "No pillar could be scored from cited evidence."
    text = (f"Composite {composite} of 5 over pillars covering {coverage} of the weight.")
    if capped:
        text += " The composite alone would rank higher; held back by: " + "; ".join(
            c["reason"] for c in caps) + "."
    elif caps:
        text += " Limits on the evidence: " + "; ".join(c["reason"] for c in caps) + "."
    return text


def summary(block: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The compact form a portfolio review or record reader shows."""
    if not isinstance(block, Mapping) or block.get("recommendation") not in VERDICTS:
        return None
    return {"state": "available", "recommendation": block["recommendation"],
            "meaning": block.get("meaning"), "composite_score": block.get("composite_score"),
            "confidence_band": (block.get("confidence") or {}).get("band"),
            "version": block.get("version")}


__all__ = ["KEY_PILLARS", "MEANINGS", "PASS", "PILLARS", "RUBRIC_VERSION", "SHORTLIST",
           "THRESHOLDS", "VERDICTS", "WATCHLIST", "score_pillar", "summary", "verdict_block"]
