"""
Phase 27: Market Segment Discovery — thesis relevance + combined internal score.

Two deterministic (no LLM, no network) scoring steps layered on top of the
existing Phase 25 discovery score:

1. ``score_thesis_relevance`` — a PRE-SCAN score (0–100) for how well a
   universe candidate matches the parsed thesis (keyword / sector / industry /
   region overlap, source confidence, catalyst intent, weak-metadata penalty).

2. ``compute_combined_internal_score`` — a POST-SCAN blend of the thesis
   relevance and the Phase 25 discovery score into a single internal
   prioritization number, plus an internal-only interest label.

SAFETY — read carefully:
  * Every score here is an INTERNAL PRIORITIZATION signal only. It is NOT
    investment advice, NOT a recommendation, NOT a price target, NOT a fair
    value, and implies NO BUY/SELL/HOLD/WATCH action.
  * A high combined score only means "prioritize for internal human research",
    never "buy".
  * Interest labels are drawn from ``INTERNAL_INTEREST_LABELS`` only — none of
    them is an investment-action label.
"""

from __future__ import annotations

import re
from typing import Any

# Internal-only interest labels (NOT recommendations). These are the ONLY
# interest labels a thesis candidate may carry.
INTERNAL_INTEREST_LABELS = {
    "high_internal_research_interest",
    "medium_internal_research_interest",
    "low_internal_research_interest",
    "insufficient_data",
}

# Persisted on the candidate and passed through the forbidden-term safety scan,
# so it must not enumerate any blocked investment term.
THESIS_SCORE_NOTE = (
    "Internal thesis-relevance prioritization only. It ranks how closely a "
    "candidate matches the admin's research thesis for internal human triage "
    "and implies no investment action or financial advice."
)

_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _round1(value: float) -> float:
    return round(float(value), 1)


def _tokens(*values: Any) -> set[str]:
    out: set[str] = set()
    for v in values:
        if not v:
            continue
        for tok in _WORD_SPLIT_RE.split(str(v).lower()):
            if len(tok) > 2:
                out.add(tok)
    return out


# ---------------------------------------------------------------------------
# 1) Thesis relevance (pre-scan)
# ---------------------------------------------------------------------------


def score_thesis_relevance(
    item: dict[str, Any],
    parsed: dict[str, Any],
) -> dict[str, Any]:
    """
    Score a single universe candidate against the parsed thesis (0–100).

    ``item`` is a universe candidate dict (ticker, company_name, sector,
    industry, country, region, theme, universe_source, metadata_not_sourced …).
    ``parsed`` is a :class:`ParsedThesis` dict.

    Returns a dict with ``thesis_relevance_score``, ``matched_keywords``,
    ``relevance_reason`` and a component ``breakdown``.
    """
    parsed_themes = set(parsed.get("themes") or [])
    parsed_sectors = {s.lower() for s in parsed.get("sectors") or []}
    parsed_industries = {i.lower() for i in parsed.get("industries") or []}
    parsed_regions = set(parsed.get("regions") or [])
    parsed_countries = set(parsed.get("countries") or [])
    parsed_keywords = {k.lower() for k in parsed.get("keywords") or []}
    catalyst_hints = parsed.get("catalyst_hints") or []
    confidence = float(parsed.get("confidence") or 0.0)

    item_theme = item.get("theme")
    item_sector = (item.get("sector") or "").lower()
    item_industry = (item.get("industry") or "").lower()
    item_region = item.get("region")
    item_country = item.get("country")
    metadata_not_sourced = bool(item.get("metadata_not_sourced"))

    reasons: list[str] = []
    matched_keywords: list[str] = []

    # ── Theme match (dominant) ────────────────────────────────────────────
    theme_match = bool(item_theme and item_theme in parsed_themes)
    theme_pts = 45.0 if theme_match else 0.0
    if theme_match:
        reasons.append(f"matches theme '{item_theme}'")

    # ── Keyword / description overlap ─────────────────────────────────────
    item_tokens = _tokens(item_industry, item.get("company_name"), item_theme)
    keyword_tokens: set[str] = set()
    for kw in parsed_keywords:
        keyword_tokens |= {t for t in _WORD_SPLIT_RE.split(kw) if len(t) > 2}
    overlap = sorted(item_tokens & keyword_tokens)
    if overlap:
        matched_keywords.extend(overlap)
        reasons.append("keyword overlap: " + ", ".join(overlap[:4]))
    keyword_pts = min(len(overlap), 3) * 5.0  # up to +15

    # ── Sector / industry match ───────────────────────────────────────────
    sector_match = bool(item_sector and item_sector in parsed_sectors)
    sector_pts = 10.0 if sector_match else 0.0
    if sector_match:
        reasons.append(f"sector '{item.get('sector')}'")
    industry_match = bool(item_industry and item_industry in parsed_industries)
    industry_pts = 10.0 if industry_match else 0.0
    if industry_match:
        reasons.append(f"industry '{item.get('industry')}'")

    # ── Region / country satisfied ────────────────────────────────────────
    region_requested = bool(parsed_regions or parsed_countries)
    if not region_requested:
        region_satisfied = True
    else:
        region_satisfied = bool(
            (item_region and item_region in parsed_regions)
            or (item_country and item_country in parsed_countries)
        )
    region_pts = 12.0 if region_satisfied else 0.0
    if region_requested and region_satisfied:
        reasons.append(f"region '{item_region or item_country}'")

    # ── Catalyst intent + source confidence + metadata penalty ────────────
    catalyst_pts = 4.0 if catalyst_hints else 0.0
    confidence_pts = round(confidence * 4.0, 1)  # up to +4
    metadata_penalty = 15.0 if metadata_not_sourced else 0.0
    if metadata_not_sourced:
        reasons.append("company metadata not sourced (penalized)")

    breakdown = {
        "theme_pts": theme_pts,
        "keyword_pts": keyword_pts,
        "sector_pts": sector_pts,
        "industry_pts": industry_pts,
        "region_pts": region_pts,
        "catalyst_pts": catalyst_pts,
        "confidence_pts": confidence_pts,
        "metadata_penalty": metadata_penalty,
    }
    raw = (
        theme_pts
        + keyword_pts
        + sector_pts
        + industry_pts
        + region_pts
        + catalyst_pts
        + confidence_pts
        - metadata_penalty
    )
    score = _round1(_clamp(raw))

    reason = "; ".join(reasons) if reasons else "weak thesis match"
    return {
        "thesis_relevance_score": score,
        "matched_keywords": sorted(set(matched_keywords)),
        "relevance_reason": reason,
        "breakdown": breakdown,
    }


# ---------------------------------------------------------------------------
# 2) Combined internal score (post-scan)
# ---------------------------------------------------------------------------


def _interest_label(combined: float, discovery_insufficient: bool) -> str:
    if discovery_insufficient:
        return "insufficient_data"
    if combined >= 65.0:
        return "high_internal_research_interest"
    if combined >= 40.0:
        return "medium_internal_research_interest"
    if combined >= 20.0:
        return "low_internal_research_interest"
    return "insufficient_data"


def compute_combined_internal_score(
    *,
    thesis_relevance_score: float | None,
    discovery_score: float | None,
    catalyst_score: float | None,
    source_quality_score: float | None,
    missing_info_count: int | None,
    discovery_grade: str | None = None,
) -> dict[str, Any]:
    """
    Blend the thesis relevance and the Phase 25 discovery signals into a single
    internal prioritization score (0–100) plus an internal-only interest label.

        combined_internal_score =
            0.45 * thesis_relevance_score
          + 0.35 * discovery_score
          + 0.10 * catalyst_score
          + 0.10 * source_quality_score
          - missing_data_penalty
    """
    thesis = float(thesis_relevance_score or 0.0)
    discovery = float(discovery_score or 0.0)
    catalyst = float(catalyst_score or 0.0)
    source_quality = float(source_quality_score or 0.0)
    missing = int(missing_info_count or 0)

    missing_data_penalty = min(missing * 1.5, 15.0)

    combined = (
        0.45 * thesis
        + 0.35 * discovery
        + 0.10 * catalyst
        + 0.10 * source_quality
        - missing_data_penalty
    )
    combined_score = _round1(_clamp(combined))

    discovery_insufficient = discovery_grade == "data_insufficient"
    label = _interest_label(combined_score, discovery_insufficient)

    explanation = (
        f"{THESIS_SCORE_NOTE} Combined internal score {combined_score:.1f}/100 "
        f"(interest: {label}). Blend of thesis relevance {thesis:.0f}, "
        f"discovery score {discovery:.0f}, catalyst {catalyst:.0f}, source "
        f"quality {source_quality:.0f}, less a data-gap penalty of "
        f"{missing_data_penalty:.0f}. Internal human research triage only."
    )

    return {
        "combined_internal_score": combined_score,
        "internal_interest_label": label,
        "missing_data_penalty": _round1(missing_data_penalty),
        "explanation": explanation,
    }
