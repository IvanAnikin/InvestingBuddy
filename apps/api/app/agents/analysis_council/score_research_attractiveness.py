"""
Phase 15: Score Research Attractiveness Agent Node.

A deterministic LangGraph node that runs after the Analysis Council phase
and scores the company's research attractiveness using all available council
outputs.

Produces an internal scorecard (0–100) and sets internal_status.

CONSTRAINTS:
  - No BUY/SELL/HOLD/WATCH/REJECT recommendations.
  - No price targets, fair values, or upside percentages.
  - internal_status is a research queue label (admin-only).
  - "high_priority_for_human_review" is NOT investment advice.
  - Always returns — never raises (non-fatal node).
  - Human admin review required before any action on scored candidates.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.scoring_engine import ScoringEngine

logger = logging.getLogger(__name__)

_engine = ScoringEngine()


def run_score_research_attractiveness(
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
) -> dict[str, Any]:
    """
    Score research attractiveness from Analysis Council outputs.

    Always returns a dict — never raises.  Errors are caught and returned
    as a minimal scorecard with internal_status='not_enough_data'.

    Returns:
        dict with keys:
          overall_score        — 0–100 integer
          internal_status      — from ALLOWED_INTERNAL_STATUSES
          scores               — dict of dimension scores
          warnings             — list of warnings
          missing_data         — list of missing data items
          reasoning            — human-readable explanation
          source_quality_summary — dict
          next_research_steps  — list of strings
          disclaimer           — static disclaimer text
    """
    try:
        result = _engine.score_company_analysis(
            company_snapshot=company_snapshot or {},
            financial_data_summary=financial_data_summary,
            source_quality_summary=source_quality_summary,
            research_completeness_summary=research_completeness_summary,
            citation_validation_summary=citation_validation_summary,
            bull_case_summary=bull_case_summary,
            bear_case_summary=bear_case_summary,
            risk_summary=risk_summary,
            valuation_guard_summary=valuation_guard_summary,
            committee_chair_summary=committee_chair_summary,
        )
        return result.to_dict()
    except Exception as exc:
        logger.warning("score_research_attractiveness failed: %s", exc, exc_info=True)
        return {
            "overall_score": 0,
            "internal_status": "not_enough_data",
            "scores": {},
            "warnings": [f"Scoring node failed: {exc}"],
            "missing_data": [],
            "reasoning": "Scoring failed — using fallback not_enough_data status.",
            "source_quality_summary": {},
            "next_research_steps": ["Investigate scoring node failure before proceeding."],
            "disclaimer": (
                "INTERNAL SCORE ONLY. Not investment advice. "
                "Not a public recommendation. Human review required."
            ),
        }
