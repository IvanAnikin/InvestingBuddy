"""
Phase 25: Real Market Candidate Discovery — deterministic scoring service.

Computes an INTERNAL PRIORITIZATION score for a discovery candidate from the
signals gathered by the discovery signal extractor. Everything here is
deterministic (no LLM, no network) so a scan is reproducible and CI-safe.

SAFETY — read carefully:
  * ``candidate_score`` and every component score are internal ranking signals
    ONLY. They are NOT investment advice, NOT a recommendation, NOT a price
    target, NOT a fair value, and imply NO BUY/SELL/HOLD/WATCH action.
  * A high score does not mean "buy". It only means "prioritize for internal
    human research".
  * Score explanations must always state that the score is an internal
    prioritization signal only and never use investment-action language.

Scoring formula (all components 0–100):

    candidate_score =
        0.30 * momentum_score
      + 0.25 * catalyst_score
      + 0.20 * fundamentals_score
      + 0.15 * source_quality_score
      + 0.10 * data_completeness_score
      - risk_penalty            (0–40 points)

The result is clamped to the 0–100 range.
"""

from __future__ import annotations

from typing import Any

from app.models.discovery import ALLOWED_CANDIDATE_LABELS

# NOTE: This note is persisted on the candidate and passed through the
# forbidden-term safety scan. It must NOT enumerate any blocked investment
# term (the scan matches literal substrings like "price target"/"fair value"/
# "recommendation"). Keep the wording free of every blocked term.
INTERNAL_SCORE_NOTE = (
    "Internal prioritization score only. It ranks a candidate for internal "
    "human research triage and implies no investment action or financial advice."
)

# Momentum labels produced by the trend signal engine.
_MOMENTUM_POSITIVE = "positive_momentum_candidate"
_MOMENTUM_NEUTRAL = "neutral_momentum"
_MOMENTUM_NEGATIVE = "negative_momentum"
_MOMENTUM_INSUFFICIENT = "insufficient_price_history"

_COVERAGE_BASE = {
    "strong": 75.0,
    "adequate": 60.0,
    "limited": 40.0,
    "filings_only": 30.0,
    "stale": 20.0,
    "none_found": 10.0,
    "provider_unavailable": 5.0,
}


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _round1(value: float) -> float:
    return round(float(value), 1)


def _present(value: Any) -> bool:
    return value is not None


# ---------------------------------------------------------------------------
# Component scoring
# ---------------------------------------------------------------------------


def score_momentum(trend: dict[str, Any]) -> float:
    """Momentum component (0–100) from the T6 trend signal. Bounded."""
    label = trend.get("momentum_label")
    has_history = bool(trend.get("has_price_history"))
    if not has_history or label in (None, _MOMENTUM_INSUFFICIENT):
        return 0.0

    base = {
        _MOMENTUM_POSITIVE: 65.0,
        _MOMENTUM_NEUTRAL: 45.0,
        _MOMENTUM_NEGATIVE: 25.0,
    }.get(label, 40.0)

    rets = [
        r
        for r in (trend.get("return_1m"), trend.get("return_3m"), trend.get("return_6m"))
        if isinstance(r, (int, float))
    ]
    avg_ret = sum(rets) / len(rets) if rets else 0.0
    # Cap extreme values before scaling so a single blow-off move cannot dominate.
    ret_adj = _clamp(avg_ret, -30.0, 30.0) * 0.5  # ±15

    ma_bonus = 0.0
    if isinstance(trend.get("pct_above_ma50"), (int, float)) and trend["pct_above_ma50"] > 0:
        ma_bonus += 5.0
    if isinstance(trend.get("pct_above_ma200"), (int, float)) and trend["pct_above_ma200"] > 0:
        ma_bonus += 5.0

    return _round1(_clamp(base + ret_adj + ma_bonus))


def score_catalyst(catalyst: dict[str, Any]) -> float:
    """Catalyst component (0–100). Aggregator-only evidence is down-weighted."""
    coverage = catalyst.get("coverage_status")
    base = _COVERAGE_BASE.get(coverage or "none_found", 10.0)

    total_events = int(catalyst.get("total_events") or 0)
    high_strength = int(catalyst.get("high_strength_count") or 0)
    primary = int(catalyst.get("primary_or_regulator_event_count") or 0)
    press = int(catalyst.get("press_release_event_count") or 0)
    aggregator = int(catalyst.get("aggregator_only_count") or 0)
    warnings = catalyst.get("warnings") or []

    score = base
    score += min(total_events, 8) * 2.0            # up to +16
    score += min(high_strength * 4.0, 12.0)        # up to +12
    score += min(primary * 3.0, 12.0)              # up to +12 (T1/T2 evidence)
    score += min(press * 2.0, 8.0)                 # up to +8 (company primary source)
    score -= min(aggregator * 2.0, 8.0)            # aggregator-only down-weighted
    score -= min(len(warnings), 3) * 2.0           # warnings reduce score

    return _round1(_clamp(score))


def score_fundamentals(fundamentals: dict[str, Any], market: dict[str, Any]) -> float:
    """Fundamentals component (0–100). Missing/stale data lowers the score."""
    if not fundamentals.get("available"):
        return 0.0

    base = 45.0
    for key in (
        "revenue_mln",
        "net_income_mln",
        "free_cash_flow_mln",
        "total_debt_mln",
        "cash_mln",
    ):
        if _present(fundamentals.get(key)):
            base += 7.0  # up to +35

    if _present(market.get("market_cap_mln")):
        base += 8.0
    if _present(market.get("enterprise_value_mln")):
        base += 5.0
    if _present(market.get("pe_ratio")):
        base += 4.0

    if fundamentals.get("stale"):
        base -= 15.0  # stale annual data is less useful

    return _round1(_clamp(base))


def score_source_quality(source_quality: dict[str, Any]) -> float:
    """Source-quality component (0–100). T1/T2 sources raise it, T5-only lowers it."""
    overall = (source_quality.get("overall") or "insufficient").lower()
    base = {
        "strong": 80.0,
        "medium": 55.0,
        "adequate": 55.0,
        "weak": 35.0,
        "insufficient": 15.0,
    }.get(overall, 25.0)

    base += min(int(source_quality.get("strong_sources_count") or 0) * 3.0, 12.0)
    base -= min(int(source_quality.get("weak_sources_count") or 0) * 2.0, 8.0)
    base -= min(int(source_quality.get("aggregator_only_count") or 0) * 3.0, 12.0)

    tiers = source_quality.get("source_tiers") or {}
    tier_keys = " ".join(str(k) for k in tiers.keys())
    if "T1" in tier_keys or "T2" in tier_keys:
        base += 8.0

    return _round1(_clamp(base))


def score_data_completeness(completeness: dict[str, Any]) -> float:
    """Data-completeness component (0–100). Fewer gaps is better."""
    missing = int(completeness.get("missing_info_count") or 0)
    blocking = int(completeness.get("blocking_gap_count") or 0)

    score = 100.0
    score -= min(missing * 3.0, 60.0)
    score -= min(blocking * 10.0, 40.0)
    # NOTE: schema_valid being False is EXPECTED at this phase and is not a
    # fatal blocker — it deliberately does not penalize completeness here.
    return _round1(_clamp(score))


def compute_risk_penalty(signal: dict[str, Any]) -> float:
    """Risk penalty in points (0–40) subtracted from the weighted score."""
    penalty = 0.0
    trend = signal.get("trend") or {}
    fundamentals = signal.get("fundamentals") or {}
    catalyst = signal.get("catalyst") or {}
    identity = signal.get("identity") or {}
    warnings = signal.get("warnings") or []

    if signal.get("provider_failed"):
        penalty += 25.0
    if signal.get("is_mock"):
        penalty += 20.0
    if not trend.get("has_price_history"):
        penalty += 8.0
    if not fundamentals.get("available"):
        penalty += 8.0
    if int(catalyst.get("total_events") or 0) == 0:
        penalty += 5.0
    # Severe missing identity (no sector, no LEI, no website).
    if not identity.get("sector") and not identity.get("lei") and not identity.get("website"):
        penalty += 4.0
    penalty += min(len(warnings), 5) * 1.0

    return _round1(min(penalty, 40.0))


# ---------------------------------------------------------------------------
# Grade + labels
# ---------------------------------------------------------------------------


def _derive_grade(signal: dict[str, Any], candidate_score: float) -> str:
    trend = signal.get("trend") or {}
    fundamentals = signal.get("fundamentals") or {}
    catalyst = signal.get("catalyst") or {}

    insufficient = (
        signal.get("provider_failed")
        or signal.get("is_mock")
        or (
            not fundamentals.get("available")
            and not trend.get("has_price_history")
            and int(catalyst.get("total_events") or 0) == 0
        )
    )
    if insufficient:
        return "data_insufficient"
    if candidate_score >= 65.0:
        return "high_internal_interest"
    if candidate_score >= 40.0:
        return "medium_internal_interest"
    return "low_internal_interest"


def _derive_labels(signal: dict[str, Any], grade: str) -> list[str]:
    trend = signal.get("trend") or {}
    fundamentals = signal.get("fundamentals") or {}
    catalyst = signal.get("catalyst") or {}
    completeness = signal.get("completeness") or {}

    labels: list[str] = ["internal_research_candidate", "needs_human_review"]

    if trend.get("momentum_label") == _MOMENTUM_POSITIVE:
        labels.append("positive_momentum_candidate")

    coverage = catalyst.get("coverage_status")
    if coverage in ("strong", "adequate") or int(catalyst.get("total_events") or 0) >= 3:
        labels.append("catalyst_rich_candidate")

    if fundamentals.get("available"):
        labels.append("fundamentals_available")

    missing_info = int(completeness.get("missing_info_count") or 0)
    if signal.get("is_mock") or signal.get("provider_failed") or missing_info >= 8:
        labels.append("data_sparse")

    if grade == "data_insufficient":
        labels.append("research_incomplete")

    # Guarantee only allowed labels ever leave this module.
    safe = [label for label in labels if label in ALLOWED_CANDIDATE_LABELS]
    # De-dup while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for label in safe:
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


def _build_explanation(
    grade: str,
    candidate_score: float,
    components: dict[str, float],
) -> str:
    return (
        f"{INTERNAL_SCORE_NOTE} "
        f"Internal prioritization score {candidate_score:.1f}/100 "
        f"(grade: {grade}). Components — momentum {components['momentum_score']:.0f}, "
        f"catalyst {components['catalyst_score']:.0f}, "
        f"fundamentals {components['fundamentals_score']:.0f}, "
        f"source quality {components['source_quality_score']:.0f}, "
        f"data completeness {components['data_completeness_score']:.0f}, "
        f"risk penalty {components['risk_penalty_score']:.0f}. "
        "This ranks the candidate for internal human research only."
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def score_signal(signal: dict[str, Any]) -> dict[str, Any]:
    """
    Score a discovery signal and return an internal prioritization result.

    Returns a dict with:
      candidate_score, candidate_score_grade,
      momentum_score, fundamentals_score, catalyst_score,
      source_quality_score, data_completeness_score, risk_penalty_score,
      labels (list[str]), explanation (str)
    """
    trend = signal.get("trend") or {}
    fundamentals = signal.get("fundamentals") or {}
    market = signal.get("market") or {}
    catalyst = signal.get("catalyst") or {}
    source_quality = signal.get("source_quality") or {}
    completeness = signal.get("completeness") or {}

    momentum_score = score_momentum(trend)
    catalyst_score = score_catalyst(catalyst)
    fundamentals_score = score_fundamentals(fundamentals, market)
    source_quality_score = score_source_quality(source_quality)
    data_completeness_score = score_data_completeness(completeness)
    risk_penalty_score = compute_risk_penalty(signal)

    weighted = (
        0.30 * momentum_score
        + 0.25 * catalyst_score
        + 0.20 * fundamentals_score
        + 0.15 * source_quality_score
        + 0.10 * data_completeness_score
    )
    candidate_score = _round1(_clamp(weighted - risk_penalty_score))

    components = {
        "momentum_score": momentum_score,
        "catalyst_score": catalyst_score,
        "fundamentals_score": fundamentals_score,
        "source_quality_score": source_quality_score,
        "data_completeness_score": data_completeness_score,
        "risk_penalty_score": risk_penalty_score,
    }

    grade = _derive_grade(signal, candidate_score)
    labels = _derive_labels(signal, grade)
    explanation = _build_explanation(grade, candidate_score, components)

    return {
        "candidate_score": candidate_score,
        "candidate_score_grade": grade,
        **components,
        "labels": labels,
        "explanation": explanation,
    }
