"""
Phase 15: Scoring + Valuation Framework — Deterministic scoring engine.

Produces multi-dimension internal research attractiveness scores (0–100) for
screening candidates and company analysis outputs.

IMPORTANT CONSTRAINTS:
  - No BUY/SELL/HOLD/WATCH recommendations are produced.
  - No price targets, fair values, or upside percentages are produced.
  - All internal_status values are research queue labels (admin-only).
  - "high_priority_for_human_review" is NOT investment advice.
  - Human admin must review all high-priority items before further action.
  - Scores from mock/T6 data can never exceed 30/100 overall.
  - Scores from T5-only data can never exceed 60/100 overall.
  - Only T1/T2-backed data can reach the highest score bands.

Score categories (0–100 integers):
  business_quality_score        — sector clarity, name quality, description richness
  financial_strength_score      — available financial data completeness
  growth_context_score          — theme alignment, sector tailwinds
  valuation_readiness_score     — key multiples inputs available
  source_quality_score          — weighted T1>T2>T5>T6 tier scoring
  risk_penalty_score            — data and source risk factors (higher = more risk)
  data_completeness_score       — ratio of available vs expected data fields
  theme_alignment_score         — match quality against research themes
  catalyst_visibility_score     — catalysts and triggers visible
  overall_research_attractiveness_score — weighted composite (0–100)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Allowed internal statuses (research queue labels — never public recommendations)
ALLOWED_INTERNAL_STATUSES = {
    "not_enough_data",
    "low_priority_research",
    "needs_primary_sources",
    "ready_for_deeper_analysis",
    "high_priority_for_human_review",
    "reject_due_to_data_quality",
}

# Forbidden output terms — never appear in any score output
_FORBIDDEN_TERMS = {
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "REJECT",
    "SHORTLIST",
    "price target",
    "target price",
    "fair value",
    "upside of",
    "downside of",
    "undervalued",
    "overvalued",
    "upside_percent",
}

# Source tier score multipliers
_TIER_QUALITY_SCORES: dict[str, int] = {
    "T1_primary_filing": 95,
    "T2_regulator_or_gov": 85,
    "T3_industry_specialist": 70,
    "T4_quality_media": 60,
    "T5_api_aggregator": 40,
    "T6_model_estimate": 15,
}

# Expected financial data fields for completeness scoring (candidate context)
_EXPECTED_CANDIDATE_FIELDS = [
    "ticker",
    "exchange",
    "name",
    "country",
    "sector",
    "market_cap",
    "currency",
    "market_cap_usd_m",
    "revenue_ttm",
    "ebitda",
    "ev_ebitda",
    "pe_ratio",
    "fcf_ttm",
    "net_debt",
    "shares_outstanding",
]

# Expected fields for valuation readiness (company analysis context)
_VALUATION_BASIC_MULTIPLES_FIELDS = [
    "market_cap",
    "revenue_ttm",
    "ebitda",
    "shares_outstanding",
]

_VALUATION_DEEPER_FIELDS = [
    "enterprise_value",
    "ebit",
    "net_income",
    "free_cash_flow",
    "debt",
    "cash",
    "historical_price",
]

# Theme keywords for theme-alignment scoring
_THEME_KEYWORDS: dict[str, list[str]] = {
    "energy_transition": [
        "renewable", "solar", "wind", "hydrogen", "battery", "storage",
        "clean energy", "decarbonization", "electrolysis", "offshore",
    ],
    "electrification_grid": [
        "grid", "transmission", "distribution", "substation", "transformer",
        "cable", "interconnection", "smart grid", "electricity network",
    ],
    "defense_security": [
        "defense", "defence", "military", "aerospace", "radar", "surveillance",
        "cybersecurity", "armament", "missile", "nato",
    ],
    "industrial_resilience": [
        "manufacturing", "industrial", "automation", "robotics", "logistics",
        "infrastructure", "rail", "port", "reshoring", "supply chain",
    ],
    "real_assets": [
        "real estate", "infrastructure", "utilities", "pipeline", "storage",
        "port", "airport", "toll road", "reit",
    ],
    "materials_mining": [
        "mining", "mineral", "copper", "lithium", "nickel", "cobalt",
        "rare earth", "iron ore", "aluminium", "aluminum", "zinc", "gold",
    ],
}

# Score band caps by data source tier
_TIER_OVERALL_CAP: dict[str, int] = {
    "T1_primary_filing": 100,
    "T2_regulator_or_gov": 90,
    "T3_industry_specialist": 80,
    "T4_quality_media": 75,
    "T5_api_aggregator": 60,
    "T6_model_estimate": 30,
    "mock": 25,
}

# Dimension weights for overall composite score
_DIMENSION_WEIGHTS: dict[str, float] = {
    "source_quality_score": 0.20,
    "data_completeness_score": 0.18,
    "theme_alignment_score": 0.15,
    "business_quality_score": 0.12,
    "financial_strength_score": 0.12,
    "valuation_readiness_score": 0.10,
    "growth_context_score": 0.08,
    "catalyst_visibility_score": 0.05,
    # risk_penalty_score is subtracted separately
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DimensionScore:
    """A single scored dimension with explanation and evidence."""

    score: int                          # 0–100
    explanation: str
    evidence_used: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ScorecardResult:
    """
    Full multi-dimension scorecard output.

    NOT investment advice. NOT a public recommendation.
    internal_status is a research queue label for admin use only.
    """

    overall_score: int                  # 0–100 composite
    internal_status: str                # from ALLOWED_INTERNAL_STATUSES only
    scores: dict[str, DimensionScore]   # dimension name → DimensionScore
    warnings: list[str]
    missing_data: list[str]
    reasoning: str
    source_quality_summary: dict
    next_research_steps: list[str]

    def to_dict(self) -> dict:
        return {
            "overall_score": self.overall_score,
            "internal_status": self.internal_status,
            "scores": {
                k: {
                    "score": v.score,
                    "explanation": v.explanation,
                    "evidence_used": v.evidence_used,
                    "missing_data": v.missing_data,
                    "warnings": v.warnings,
                }
                for k, v in self.scores.items()
            },
            "warnings": self.warnings,
            "missing_data": self.missing_data,
            "reasoning": self.reasoning,
            "source_quality_summary": self.source_quality_summary,
            "next_research_steps": self.next_research_steps,
            "disclaimer": (
                "INTERNAL SCORE ONLY. Not investment advice. "
                "Not a public recommendation. Human review required."
            ),
        }


# ---------------------------------------------------------------------------
# Valuation readiness
# ---------------------------------------------------------------------------


@dataclass
class ValuationReadinessResult:
    """
    Describes whether data is available for future valuation work.

    Does NOT produce a price target, fair value, or upside estimate.
    allowed_methods lists what MIGHT be possible later if data improves —
    not what is concluded now.
    """

    # "not_ready" | "partial" | "ready_for_basic_multiples" | "ready_for_deeper_valuation"
    valuation_readiness: str
    available_inputs: list[str]
    missing_inputs: list[str]
    blocked_methods: list[str]
    allowed_methods: list[str]      # future possibilities only — not current conclusions
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "valuation_readiness": self.valuation_readiness,
            "available_inputs": self.available_inputs,
            "missing_inputs": self.missing_inputs,
            "blocked_methods": self.blocked_methods,
            "allowed_methods": self.allowed_methods,
            "warnings": self.warnings,
            "disclaimer": (
                "Valuation readiness check only. "
                "No fair value, price target, or upside estimate is produced here. "
                "Further analysis requires primary source data and human review."
            ),
        }


class ValuationReadinessService:
    """
    Checks whether the system has enough data for later valuation work.

    Produces only a readiness classification — no price target or fair value.
    """

    def check(
        self,
        available_data: list[str],
        source_tier: str,
        is_mock: bool = False,
    ) -> ValuationReadinessResult:
        """
        Determine valuation readiness from available data fields.

        Args:
            available_data: list of field names available from the data source.
            source_tier: T1–T6 tier of the data source.
            is_mock: True when data comes from a mock/synthetic provider.

        Returns:
            ValuationReadinessResult — never produces price target or fair value.
        """
        warnings: list[str] = []
        available_set = set(available_data)

        if is_mock or source_tier == "T6_model_estimate":
            return ValuationReadinessResult(
                valuation_readiness="not_ready",
                available_inputs=[],
                missing_inputs=_VALUATION_BASIC_MULTIPLES_FIELDS + _VALUATION_DEEPER_FIELDS,
                blocked_methods=[
                    "EV/EBITDA (mock data — not reliable)",
                    "P/E ratio (mock data — not reliable)",
                    "DCF (mock data — not reliable)",
                    "FCF yield (mock data — not reliable)",
                ],
                allowed_methods=[],
                warnings=[
                    "Mock/synthetic data detected. Valuation readiness cannot be assessed. "
                    "Primary source data required."
                ],
            )

        if source_tier == "T5_api_aggregator":
            warnings.append(
                "T5 aggregator data only. Valuation readiness is provisional. "
                "Primary source (T1/T2) validation required before any valuation work."
            )

        # Check basic multiples inputs
        basic_available = [f for f in _VALUATION_BASIC_MULTIPLES_FIELDS if f in available_set]
        basic_missing = [f for f in _VALUATION_BASIC_MULTIPLES_FIELDS if f not in available_set]

        # Check deeper valuation inputs
        deeper_available = [f for f in _VALUATION_DEEPER_FIELDS if f in available_set]
        deeper_missing = [f for f in _VALUATION_DEEPER_FIELDS if f not in available_set]

        all_available = basic_available + deeper_available
        all_missing = basic_missing + deeper_missing

        # Determine readiness level
        if not basic_available:
            valuation_readiness = "not_ready"
            blocked_methods = [
                "EV/EBITDA (market_cap, revenue_ttm, ebitda missing)",
                "P/E ratio (market_cap, shares_outstanding missing)",
                "DCF (free_cash_flow, revenue_ttm missing)",
                "FCF yield (free_cash_flow, market_cap missing)",
            ]
            allowed_methods: list[str] = []
        elif len(basic_available) >= len(_VALUATION_BASIC_MULTIPLES_FIELDS) // 2:
            # At least half of basic inputs available
            if len(deeper_available) >= 3:
                valuation_readiness = "ready_for_deeper_valuation"
                blocked_methods = [
                    f"Method requiring {m}" for m in deeper_missing[:3]
                ]
                allowed_methods = [
                    "EV/EBITDA input available (T5 tier — requires T1/T2 confirmation)",
                    "Basic multiple analysis may be possible with T1/T2 data",
                    "FCF data available — yield analysis possible with primary sources",
                ]
            else:
                valuation_readiness = "ready_for_basic_multiples"
                blocked_methods = [
                    "DCF (free_cash_flow missing)"
                    if "free_cash_flow" not in available_set
                    else "",
                    "Full EV/EBITDA (enterprise_value missing)"
                    if "enterprise_value" not in available_set
                    else "",
                ]
                blocked_methods = [b for b in blocked_methods if b]
                allowed_methods = [
                    "EV/EBITDA input available (requires T1/T2 confirmation)",
                    "Basic multiple analysis may be possible later",
                ]
        else:
            valuation_readiness = "partial"
            blocked_methods = [
                "Full DCF (multiple inputs missing)",
                "EV/EBITDA (ebitda or enterprise_value missing)",
            ]
            allowed_methods = [
                "Basic market cap analysis possible (limited utility)",
                "Further data collection required before meaningful multiples",
            ]

        return ValuationReadinessResult(
            valuation_readiness=valuation_readiness,
            available_inputs=all_available,
            missing_inputs=all_missing,
            blocked_methods=blocked_methods,
            allowed_methods=allowed_methods,
            warnings=warnings,
        )


# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------


class ScoringEngine:
    """
    Deterministic multi-dimension research attractiveness scorer.

    Produces a ScorecardResult with 0–100 scores across 10 dimensions.
    Never produces BUY/SELL/HOLD/WATCH, price targets, or fair values.

    Score caps enforced by source tier:
      T6/mock: overall ≤ 30
      T5:      overall ≤ 60
      T1/T2:   overall ≤ 100
    """

    def __init__(self) -> None:
        self._vr_service = ValuationReadinessService()

    def score_candidate(
        self,
        candidate_data: dict[str, Any],
    ) -> ScorecardResult:
        """
        Score a screening candidate.

        candidate_data keys (all optional, gracefully handled when absent):
          ticker, exchange, name, country, sector, market_cap, currency,
          source_tier, data_quality, discovery_reasons (list[str]),
          available_data (list[str]), missing_data (list[str]),
          warnings (list[str])
        """
        warnings: list[str] = []

        ticker = candidate_data.get("ticker", "")
        name = candidate_data.get("name") or ""
        sector = candidate_data.get("sector") or ""
        country = candidate_data.get("country") or ""
        source_tier = candidate_data.get("source_tier", "T6_model_estimate")
        data_quality = candidate_data.get("data_quality", "D_weak_or_stale")
        discovery_reasons = candidate_data.get("discovery_reasons", []) or []
        available_data = candidate_data.get("available_data", []) or []
        missing_data = candidate_data.get("missing_data", []) or []
        candidate_warnings = candidate_data.get("warnings", []) or []
        warnings.extend(candidate_warnings)

        is_mock = source_tier == "T6_model_estimate" or data_quality == "D_weak_or_stale"

        # ── Individual dimension scores ───────────────────────────────────────
        source_quality = self._score_source_quality(source_tier, is_mock, warnings)
        data_completeness = self._score_data_completeness(
            available_data, missing_data, _EXPECTED_CANDIDATE_FIELDS
        )
        theme_alignment = self._score_theme_alignment(
            discovery_reasons, name, sector
        )
        business_quality = self._score_business_quality(
            ticker, name, sector, country
        )
        financial_strength = self._score_financial_strength(
            available_data, missing_data, source_tier
        )
        valuation_readiness_score = self._score_valuation_readiness(
            available_data, source_tier, is_mock
        )
        growth_context = self._score_growth_context(
            discovery_reasons, sector, source_tier
        )
        catalyst_visibility = self._score_catalyst_visibility(
            discovery_reasons, missing_data
        )
        risk_penalty = self._score_risk_penalty(
            source_tier, is_mock, missing_data, candidate_warnings
        )

        scores = {
            "source_quality_score": source_quality,
            "data_completeness_score": data_completeness,
            "theme_alignment_score": theme_alignment,
            "business_quality_score": business_quality,
            "financial_strength_score": financial_strength,
            "valuation_readiness_score": valuation_readiness_score,
            "growth_context_score": growth_context,
            "catalyst_visibility_score": catalyst_visibility,
            "risk_penalty_score": risk_penalty,
        }

        # ── Composite score ───────────────────────────────────────────────────
        overall = self._compute_overall(scores, source_tier, is_mock)

        # ── Internal status ───────────────────────────────────────────────────
        internal_status = self._determine_internal_status(
            overall, source_tier, is_mock, missing_data, data_completeness.score
        )

        # Validate status
        if internal_status not in ALLOWED_INTERNAL_STATUSES:
            warnings.append(
                f"SAFETY: computed status '{internal_status}' not in allowed list. "
                "Falling back to 'not_enough_data'."
            )
            internal_status = "not_enough_data"

        # ── Safety gate on all output text ────────────────────────────────────
        all_text = " ".join(
            [internal_status]
            + discovery_reasons
            + [s.explanation for s in scores.values()]
        )
        safety_violations = _check_forbidden_terms(all_text)
        if safety_violations:
            warnings.extend(safety_violations)
            internal_status = "not_enough_data"
            warnings.append(
                "SAFETY: forbidden content detected. Status downgraded."
            )

        # ── Source quality summary ────────────────────────────────────────────
        source_quality_summary = {
            "source_tier": source_tier,
            "data_quality": data_quality,
            "is_mock": is_mock,
            "source_quality_score": source_quality.score,
            "note": _source_quality_note(source_tier),
        }

        # ── Next research steps ───────────────────────────────────────────────
        next_steps = _build_next_steps(
            internal_status, source_tier, missing_data, is_mock
        )

        reasoning = (
            f"INTERNAL CANDIDATE SCORECARD — {name or ticker}. "
            f"Source tier: {source_tier}. "
            f"Overall score: {overall}/100. "
            f"Internal status: '{internal_status}'. "
            f"Data completeness: {data_completeness.score}/100. "
            f"Source quality: {source_quality.score}/100. "
            f"Theme alignment: {theme_alignment.score}/100. "
            "NOT investment advice. NOT a public recommendation. "
            "Human review required before any further action."
        )

        return ScorecardResult(
            overall_score=overall,
            internal_status=internal_status,
            scores=scores,
            warnings=warnings,
            missing_data=missing_data,
            reasoning=reasoning,
            source_quality_summary=source_quality_summary,
            next_research_steps=next_steps,
        )

    def score_company_analysis(
        self,
        company_snapshot: dict[str, Any],
        financial_data_summary: dict[str, Any] | None = None,
        source_quality_summary: dict[str, Any] | None = None,
        research_completeness_summary: dict[str, Any] | None = None,
        citation_validation_summary: dict[str, Any] | None = None,
        bull_case_summary: dict[str, Any] | None = None,
        bear_case_summary: dict[str, Any] | None = None,
        risk_summary: dict[str, Any] | None = None,
        valuation_guard_summary: dict[str, Any] | None = None,
        committee_chair_summary: dict[str, Any] | None = None,
    ) -> ScorecardResult:
        """
        Score a company from the full company-analysis workflow outputs.

        Consumes all Analysis Council outputs to produce a research
        attractiveness score.  No final valuation or recommendation is made.
        """
        warnings: list[str] = []

        identity = company_snapshot.get("company_identity", {})
        provider_meta = company_snapshot.get("provider_metadata", {})
        ticker = identity.get("ticker", "N/A")
        name = identity.get("legal_name", "Unknown")
        sector = company_snapshot.get("profile", {}).get("sector", "")
        source_tier = provider_meta.get("source_tier", "T6_model_estimate")
        is_mock = company_snapshot.get("is_mock", True)

        fd = financial_data_summary or {}
        sq = source_quality_summary or {}
        rc = research_completeness_summary or {}
        cv = citation_validation_summary or {}
        bc = bull_case_summary or {}
        br = bear_case_summary or {}
        vg = valuation_guard_summary or {}
        cc = committee_chair_summary or {}

        available_count = fd.get("available_count", 0)
        missing_count = fd.get("missing_count", 10)
        available_data = [
            f"field_{i}" for i in range(available_count)
        ]
        missing_data_list = [
            f"field_{i}" for i in range(missing_count)
        ]

        # ── Individual dimension scores ───────────────────────────────────────
        source_quality = self._score_source_quality_from_summary(sq, source_tier, is_mock)
        data_completeness = self._score_data_completeness(
            available_data, missing_data_list, list(range(available_count + missing_count))
        )
        theme_alignment = self._score_theme_alignment_from_context(
            bc, br, sector
        )
        business_quality = self._score_business_quality(ticker, name, sector, "")
        financial_strength = self._score_financial_strength_from_summary(fd, source_tier)
        valuation_readiness_score = self._score_valuation_readiness_from_guard(vg)
        growth_context = self._score_growth_context_from_council(bc, br)
        catalyst_visibility = self._score_catalyst_visibility_from_council(bc, cc)
        risk_penalty = self._score_risk_penalty_from_council(vg, sq, rc, is_mock)

        scores = {
            "source_quality_score": source_quality,
            "data_completeness_score": data_completeness,
            "theme_alignment_score": theme_alignment,
            "business_quality_score": business_quality,
            "financial_strength_score": financial_strength,
            "valuation_readiness_score": valuation_readiness_score,
            "growth_context_score": growth_context,
            "catalyst_visibility_score": catalyst_visibility,
            "risk_penalty_score": risk_penalty,
        }

        # ── Composite score ───────────────────────────────────────────────────
        overall = self._compute_overall(scores, source_tier, is_mock)

        # ── Internal status ───────────────────────────────────────────────────
        committee_status = cc.get("provisional_internal_status", "")
        internal_status = self._determine_internal_status_from_council(
            overall, source_tier, is_mock, committee_status, rc, sq, cv
        )

        if internal_status not in ALLOWED_INTERNAL_STATUSES:
            warnings.append(
                f"SAFETY: computed status '{internal_status}' not allowed. "
                "Falling back to 'not_enough_data'."
            )
            internal_status = "not_enough_data"

        # ── Safety gate ───────────────────────────────────────────────────────
        all_text = " ".join(
            [internal_status, name, ticker]
            + [s.explanation for s in scores.values()]
        )
        safety_violations = _check_forbidden_terms(all_text)
        if safety_violations:
            warnings.extend(safety_violations)
            internal_status = "not_enough_data"
            warnings.append("SAFETY: forbidden content detected. Status downgraded.")

        # ── Source quality summary ────────────────────────────────────────────
        src_summary = {
            "source_tier": source_tier,
            "overall_source_quality": sq.get("overall_source_quality", "unknown"),
            "is_mock": is_mock,
            "source_quality_score": source_quality.score,
            "note": _source_quality_note(source_tier),
        }

        next_steps = _build_next_steps_from_council(
            internal_status, source_tier, is_mock, rc, vg
        )

        reasoning = (
            f"INTERNAL COMPANY ANALYSIS SCORECARD — {name} ({ticker}). "
            f"Source tier: {source_tier}. "
            f"Overall score: {overall}/100. "
            f"Internal status: '{internal_status}'. "
            "NOT investment advice. NOT a public recommendation. "
            "Human review required before any further action."
        )

        if is_mock:
            warnings.append(
                "Mock provider data — scorecard is illustrative only. "
                "Overall score capped at 30/100."
            )

        return ScorecardResult(
            overall_score=overall,
            internal_status=internal_status,
            scores=scores,
            warnings=warnings,
            missing_data=missing_data_list,
            reasoning=reasoning,
            source_quality_summary=src_summary,
            next_research_steps=next_steps,
        )

    # ── Dimension scorers (candidate context) ────────────────────────────────

    def _score_source_quality(
        self, source_tier: str, is_mock: bool, warnings: list[str]
    ) -> DimensionScore:
        if is_mock:
            return DimensionScore(
                score=5,
                explanation="Mock/synthetic data — source quality is not meaningful.",
                warnings=["Source quality cannot be assessed with mock data."],
            )
        base = _TIER_QUALITY_SCORES.get(source_tier, 10)
        evidence = [f"Source tier: {source_tier}"]
        warn: list[str] = []
        if source_tier in ("T5_api_aggregator", "T6_model_estimate"):
            warn.append(
                f"{source_tier} data only. Primary source (T1/T2) validation required."
            )
        return DimensionScore(
            score=base,
            explanation=f"Source quality score for tier {source_tier}.",
            evidence_used=evidence,
            warnings=warn,
        )

    def _score_data_completeness(
        self,
        available: list[str],
        missing: list[str],
        expected: list,
    ) -> DimensionScore:
        total = len(available) + len(missing)
        if total == 0:
            return DimensionScore(
                score=0,
                explanation="No data fields found.",
                missing_data=["all fields"],
                warnings=["No data available to assess completeness."],
            )
        completeness_ratio = len(available) / total
        score = int(completeness_ratio * 100)
        return DimensionScore(
            score=score,
            explanation=f"{len(available)}/{total} expected fields available ({score}%).",
            evidence_used=[f"Available: {', '.join(available[:5])}"]
            if available
            else [],
            missing_data=missing[:10],
        )

    def _score_theme_alignment(
        self,
        discovery_reasons: list[str],
        name: str,
        sector: str,
    ) -> DimensionScore:
        if not discovery_reasons:
            return DimensionScore(
                score=10,
                explanation="No discovery reasons provided — theme alignment unknown.",
                missing_data=["discovery_reasons"],
                warnings=["Theme alignment cannot be determined without discovery reasons."],
            )

        combined = " ".join(discovery_reasons + [name, sector]).lower()
        best_match_score = 10
        best_theme = None

        for theme, keywords in _THEME_KEYWORDS.items():
            matched = [kw for kw in keywords if kw in combined]
            match_score = min(100, len(matched) * 15 + 10)
            if match_score > best_match_score:
                best_match_score = match_score
                best_theme = theme

        return DimensionScore(
            score=min(100, best_match_score),
            explanation=f"Best theme match: {best_theme or 'none'}. "
            f"Theme alignment score: {best_match_score}/100.",
            evidence_used=discovery_reasons[:3],
        )

    def _score_business_quality(
        self, ticker: str, name: str, sector: str, country: str
    ) -> DimensionScore:
        score = 0
        evidence: list[str] = []

        if ticker and len(ticker) >= 2:
            score += 20
            evidence.append(f"Ticker: {ticker}")
        if name and len(name) >= 3:
            score += 20
            evidence.append(f"Name: {name}")
        if sector and len(sector) >= 3:
            score += 30
            evidence.append(f"Sector: {sector}")
        if country and len(country) >= 2:
            score += 15
            evidence.append(f"Country: {country}")

        # Sector richness bonus
        known_sectors = {
            "energy", "utilities", "materials", "industrials",
            "financials", "technology", "healthcare", "real estate",
        }
        if sector and sector.lower() in known_sectors:
            score = min(100, score + 15)

        final_score = min(100, score)
        return DimensionScore(
            score=final_score,
            explanation=(
                f"Business quality based on identity completeness. Score: {final_score}/100."
            ),
            evidence_used=evidence,
        )

    def _score_financial_strength(
        self,
        available_data: list[str],
        missing_data: list[str],
        source_tier: str,
    ) -> DimensionScore:
        financial_fields = {
            "market_cap", "market_cap_usd_m", "revenue_ttm", "ebitda",
            "ev_ebitda", "pe_ratio", "fcf_ttm", "net_debt", "shares_outstanding",
        }
        available_set = set(available_data)
        financial_available = [f for f in financial_fields if f in available_set]

        if not financial_available:
            return DimensionScore(
                score=5,
                explanation="No financial data fields available.",
                missing_data=list(financial_fields),
                warnings=["Financial strength cannot be assessed without financial data."],
            )

        ratio = len(financial_available) / len(financial_fields)
        base_score = int(ratio * 80)

        # Tier penalty
        if source_tier == "T6_model_estimate":
            base_score = max(0, base_score - 20)
            n = len(financial_available)
            return DimensionScore(
                score=base_score,
                explanation=f"Financial data from T6 mock source only — {n} fields.",
                evidence_used=financial_available,
                warnings=["T6 data — financial strength assessment is not meaningful."],
            )
        if source_tier == "T5_api_aggregator":
            base_score = max(0, base_score - 10)
            n = len(financial_available)
            return DimensionScore(
                score=base_score,
                explanation=f"Financial data from T5 aggregator — {n} fields available.",
                evidence_used=financial_available,
                warnings=["T5 data — requires T1/T2 validation before use."],
            )

        n_avail = len(financial_available)
        n_total = len(financial_fields)
        return DimensionScore(
            score=min(100, base_score + 10),
            explanation=f"{n_avail}/{n_total} financial fields available.",
            evidence_used=financial_available,
        )

    def _score_valuation_readiness(
        self,
        available_data: list[str],
        source_tier: str,
        is_mock: bool,
    ) -> DimensionScore:
        vr = self._vr_service.check(available_data, source_tier, is_mock)
        readiness_scores = {
            "not_ready": 5,
            "partial": 30,
            "ready_for_basic_multiples": 60,
            "ready_for_deeper_valuation": 85,
        }
        score = readiness_scores.get(vr.valuation_readiness, 5)
        return DimensionScore(
            score=score,
            explanation=f"Valuation readiness: {vr.valuation_readiness}. "
            f"Available inputs: {len(vr.available_inputs)}. "
            "Readiness check only — no valuation conclusions produced.",
            evidence_used=vr.available_inputs[:5],
            missing_data=vr.missing_inputs[:5],
            warnings=vr.warnings,
        )

    def _score_growth_context(
        self,
        discovery_reasons: list[str],
        sector: str,
        source_tier: str,
    ) -> DimensionScore:
        if not discovery_reasons:
            return DimensionScore(
                score=10,
                explanation="Growth context cannot be assessed without discovery reasons.",
                missing_data=["discovery_reasons"],
            )

        combined = " ".join(discovery_reasons + [sector]).lower()
        growth_indicators = [
            "growth", "expansion", "market share", "tailwind", "transition",
            "demand", "adoption", "scale", "pipeline", "backlog",
        ]
        matches = [g for g in growth_indicators if g in combined]
        score = min(100, len(matches) * 15 + 20)

        if source_tier in ("T5_api_aggregator", "T6_model_estimate"):
            score = min(score, 50)

        return DimensionScore(
            score=score,
            explanation=f"Growth context from discovery reasons. "
            f"{len(matches)} growth indicators found.",
            evidence_used=matches[:5],
        )

    def _score_catalyst_visibility(
        self, discovery_reasons: list[str], missing_data: list[str]
    ) -> DimensionScore:
        if not discovery_reasons:
            return DimensionScore(
                score=10,
                explanation="Catalyst visibility unknown without discovery reasons.",
                missing_data=["discovery_reasons"],
            )

        combined = " ".join(discovery_reasons).lower()
        catalyst_terms = [
            "catalyst", "trigger", "contract", "award", "permit", "deal",
            "announcement", "event", "approval", "launch", "milestone",
        ]
        matches = [c for c in catalyst_terms if c in combined]
        score = min(100, len(matches) * 20 + 15)

        return DimensionScore(
            score=score,
            explanation=f"Catalyst visibility: {len(matches)} catalyst indicators found.",
            evidence_used=matches[:5],
        )

    def _score_risk_penalty(
        self,
        source_tier: str,
        is_mock: bool,
        missing_data: list[str],
        candidate_warnings: list[str],
    ) -> DimensionScore:
        penalty = 0
        evidence: list[str] = []

        if is_mock:
            penalty += 40
            evidence.append("Mock data: high risk penalty applied.")
        elif source_tier == "T5_api_aggregator":
            penalty += 20
            evidence.append("T5 aggregator data: moderate risk penalty.")
        elif source_tier == "T6_model_estimate":
            penalty += 35
            evidence.append("T6 model estimate: high risk penalty.")

        penalty += min(30, len(missing_data) * 2)
        penalty += min(20, len(candidate_warnings) * 5)
        penalty = min(100, penalty)

        return DimensionScore(
            score=penalty,
            explanation=f"Risk penalty score: {penalty}/100 (higher = more risk).",
            evidence_used=evidence,
            warnings=candidate_warnings[:3],
        )

    # ── Dimension scorers (company analysis context) ─────────────────────────

    def _score_source_quality_from_summary(
        self, sq: dict, source_tier: str, is_mock: bool
    ) -> DimensionScore:
        if is_mock:
            return DimensionScore(
                score=5,
                explanation="Mock provider — source quality not meaningful.",
                warnings=["Source quality cannot be assessed with mock data."],
            )
        overall_sq = sq.get("overall_source_quality", "insufficient")
        sq_scores = {
            "strong": 90, "adequate": 65, "weak": 30, "insufficient": 10
        }
        score = sq_scores.get(overall_sq, 10)
        warn: list[str] = []
        if overall_sq in ("weak", "insufficient"):
            warn.append(f"Source quality is '{overall_sq}' — primary source validation required.")
        return DimensionScore(
            score=score,
            explanation=f"Source quality from Research Team assessment: {overall_sq}.",
            evidence_used=[f"overall_source_quality={overall_sq}"],
            warnings=warn,
        )

    def _score_financial_strength_from_summary(
        self, fd: dict, source_tier: str
    ) -> DimensionScore:
        available = fd.get("available_count", 0)
        missing = fd.get("missing_count", 10)
        total = available + missing
        if total == 0:
            return DimensionScore(
                score=0, explanation="No financial data summary available."
            )
        ratio = available / total
        base = int(ratio * 80)
        if source_tier in ("T5_api_aggregator", "T6_model_estimate"):
            base = max(0, base - 15)
        return DimensionScore(
            score=min(100, base),
            explanation=f"{available}/{total} financial fields available.",
        )

    def _score_theme_alignment_from_context(
        self, bc: dict, br: dict, sector: str
    ) -> DimensionScore:
        bull_points = bc.get("positive_thesis_points", [])
        if not bull_points:
            return DimensionScore(
                score=15,
                explanation="No bull case points — theme alignment assumed minimal.",
            )
        combined = " ".join(bull_points + [sector]).lower()
        best_score = 15
        for theme, keywords in _THEME_KEYWORDS.items():
            matched = [kw for kw in keywords if kw in combined]
            s = min(100, len(matched) * 15 + 15)
            if s > best_score:
                best_score = s
        return DimensionScore(
            score=min(100, best_score),
            explanation=f"Theme alignment from bull case: {best_score}/100.",
            evidence_used=bull_points[:3],
        )

    def _score_valuation_readiness_from_guard(self, vg: dict) -> DimensionScore:
        readiness = vg.get("valuation_readiness", "not_ready")
        readiness_scores = {
            "not_ready": 5, "partial": 30,
            "ready_for_basic_multiples": 60, "ready_for_deeper_valuation": 85,
            "ready": 80,
        }
        score = readiness_scores.get(readiness, 5)
        return DimensionScore(
            score=score,
            explanation=f"Valuation readiness from ValuationGuardAgent: {readiness}. "
            "Readiness check only — no valuation conclusions produced.",
            evidence_used=vg.get("available_valuation_inputs", [])[:5],
            missing_data=vg.get("missing_valuation_inputs", [])[:5],
            warnings=vg.get("warnings", []),
        )

    def _score_growth_context_from_council(self, bc: dict, br: dict) -> DimensionScore:
        bull_pts = bc.get("positive_thesis_points", [])
        tailwinds = bc.get("potential_tailwinds", [])
        headwinds = br.get("potential_headwinds", [])
        if not bull_pts and not tailwinds:
            return DimensionScore(
                score=10,
                explanation="No council outputs for growth context.",
            )
        score = min(100, len(bull_pts) * 12 + len(tailwinds) * 10 - len(headwinds) * 5 + 10)
        score = max(5, score)
        return DimensionScore(
            score=score,
            explanation=f"Growth context: {len(bull_pts)} bull points, "
            f"{len(tailwinds)} tailwinds, {len(headwinds)} headwinds.",
            evidence_used=(bull_pts + tailwinds)[:4],
        )

    def _score_catalyst_visibility_from_council(self, bc: dict, cc: dict) -> DimensionScore:
        thesis_pts = bc.get("positive_thesis_points", [])
        next_steps = cc.get("research_next_steps", [])
        if not thesis_pts:
            return DimensionScore(
                score=10, explanation="No thesis points — catalyst visibility unknown."
            )
        combined = " ".join(thesis_pts + next_steps).lower()
        catalyst_terms = [
            "catalyst", "trigger", "contract", "award", "permit", "deal",
            "announcement", "event", "approval", "launch",
        ]
        matches = [c for c in catalyst_terms if c in combined]
        score = min(100, len(matches) * 20 + 10)
        return DimensionScore(
            score=score,
            explanation=f"Catalyst visibility: {len(matches)} indicators from council.",
            evidence_used=matches[:5],
        )

    def _score_risk_penalty_from_council(
        self, vg: dict, sq: dict, rc: dict, is_mock: bool
    ) -> DimensionScore:
        penalty = 0
        evidence: list[str] = []
        if is_mock:
            penalty += 40
            evidence.append("Mock data: high penalty.")
        overall_sq = sq.get("overall_source_quality", "insufficient")
        if overall_sq in ("weak", "insufficient"):
            penalty += 25
            evidence.append(f"Source quality {overall_sq}: penalty applied.")
        blocking_gaps = len(rc.get("blocking_gaps", []))
        penalty += min(20, blocking_gaps * 5)
        if blocking_gaps > 0:
            evidence.append(f"{blocking_gaps} blocking research gaps.")
        vg_blockers = len(vg.get("valuation_blockers", []))
        penalty += min(15, vg_blockers * 5)
        penalty = min(100, penalty)
        return DimensionScore(
            score=penalty,
            explanation=f"Risk penalty: {penalty}/100 (higher = more risk).",
            evidence_used=evidence,
        )

    # ── Composite score computation ───────────────────────────────────────────

    def _compute_overall(
        self,
        scores: dict[str, DimensionScore],
        source_tier: str,
        is_mock: bool,
    ) -> int:
        weighted_sum = sum(
            scores[dim].score * weight
            for dim, weight in _DIMENSION_WEIGHTS.items()
            if dim in scores
        )
        _default_risk = DimensionScore(score=0, explanation="")
        risk_penalty_raw = scores.get("risk_penalty_score", _default_risk).score

        # Reduce penalty impact to 20% of overall
        risk_deduction = int(risk_penalty_raw * 0.20)
        raw_overall = int(weighted_sum) - risk_deduction
        raw_overall = max(0, min(100, raw_overall))

        # Apply source tier cap
        tier = "mock" if is_mock and source_tier == "T6_model_estimate" else source_tier
        cap = _TIER_OVERALL_CAP.get(tier, 25)
        return min(raw_overall, cap)

    # ── Internal status determination ─────────────────────────────────────────

    def _determine_internal_status(
        self,
        overall: int,
        source_tier: str,
        is_mock: bool,
        missing_data: list[str],
        data_completeness_score: int,
    ) -> str:
        if is_mock:
            return "not_enough_data"
        if source_tier in ("T5_api_aggregator", "T6_model_estimate"):
            if overall >= 50:
                return "needs_primary_sources"
            return "not_enough_data"
        if overall < 20 or data_completeness_score < 20:
            return "not_enough_data"
        if overall < 35:
            return "low_priority_research"
        if overall < 55:
            return "needs_primary_sources"
        if overall < 70:
            return "ready_for_deeper_analysis"
        return "high_priority_for_human_review"

    def _determine_internal_status_from_council(
        self,
        overall: int,
        source_tier: str,
        is_mock: bool,
        committee_status: str,
        rc: dict,
        sq: dict,
        cv: dict,
    ) -> str:
        if is_mock:
            return "not_enough_data"

        citation_status = cv.get("status", "unknown")
        if citation_status == "failed":
            return "reject_due_to_data_quality"

        overall_sq = sq.get("overall_source_quality", "insufficient")
        if overall_sq == "insufficient":
            return "reject_due_to_data_quality"

        if source_tier in ("T5_api_aggregator", "T6_model_estimate"):
            return "needs_primary_sources"

        blocking_gaps = len(rc.get("blocking_gaps", []))
        if blocking_gaps > 5:
            return "low_priority_research"

        if overall >= 70 and overall_sq in ("strong", "adequate"):
            return "high_priority_for_human_review"
        if overall >= 55:
            return "ready_for_deeper_analysis"
        if overall >= 35:
            return "needs_primary_sources"
        if overall >= 20:
            return "low_priority_research"
        return "not_enough_data"


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _check_forbidden_terms(text: str) -> list[str]:
    """Return list of any forbidden terms found in text."""
    found: list[str] = []
    upper = text.upper()
    lower = text.lower()
    for term in _FORBIDDEN_TERMS:
        if term.upper() in upper or term.lower() in lower:
            found.append(f"Forbidden content detected: '{term}'")
    return found


def _source_quality_note(source_tier: str) -> str:
    notes = {
        "T1_primary_filing": "T1 primary filing — highest credibility.",
        "T2_regulator_or_gov": "T2 regulator/gov — high credibility.",
        "T3_industry_specialist": "T3 industry specialist — moderate credibility.",
        "T4_quality_media": "T4 quality media — moderate credibility.",
        "T5_api_aggregator": "T5 aggregator — requires primary source validation.",
        "T6_model_estimate": "T6 model estimate — not credible for final analysis.",
    }
    return notes.get(source_tier, "Unknown source tier.")


def _build_next_steps(
    internal_status: str,
    source_tier: str,
    missing_data: list[str],
    is_mock: bool,
) -> list[str]:
    steps: list[str] = []
    if is_mock:
        steps.append("Replace mock data with real financial data from a T1/T2 source.")
        return steps
    if source_tier in ("T5_api_aggregator", "T6_model_estimate"):
        steps.append("Obtain primary source data (T1: company filing, T2: regulator).")
    if "market_cap" in missing_data:
        steps.append("Resolve market cap from exchange data or company IR.")
    if "revenue_ttm" in missing_data:
        steps.append("Obtain trailing-twelve-month revenue from company filings.")
    if internal_status == "not_enough_data":
        steps.append("Collect minimum required data fields before scoring can be meaningful.")
    if internal_status == "high_priority_for_human_review":
        steps.append(
            "HIGH PRIORITY: Admin human review required. "
            "This is NOT a recommendation — it is a research queue signal."
        )
    return steps or ["No specific next steps identified — further data collection recommended."]


def _build_next_steps_from_council(
    internal_status: str,
    source_tier: str,
    is_mock: bool,
    rc: dict,
    vg: dict,
) -> list[str]:
    steps: list[str] = []
    if is_mock:
        steps.append("Replace mock data before meaningful analysis.")
        return steps
    steps.extend(rc.get("next_research_tasks", [])[:4])
    steps.extend(vg.get("allowed_next_steps", [])[:2])
    if internal_status == "high_priority_for_human_review":
        steps.append(
            "HIGH PRIORITY: Admin human review required. "
            "NOT investment advice — research queue signal only."
        )
    return steps or ["Further data collection required."]
