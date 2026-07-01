"""
Phase 15: Scoring + Valuation Framework — Pydantic schemas.

All schemas are admin/internal only.
No BUY/SELL/HOLD/WATCH recommendations, price targets, fair values,
or upside percentages are present or accepted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Disclaimers (injected into all API responses)
# ---------------------------------------------------------------------------

_DISCLAIMER = (
    "INTERNAL SCORE ONLY. Not investment advice. "
    "Not a public recommendation. Human review required before any action."
)

# ---------------------------------------------------------------------------
# Dimension score
# ---------------------------------------------------------------------------


class DimensionScoreRead(BaseModel):
    score: int = Field(ge=0, le=100)
    explanation: str
    evidence_used: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------


class ScorecardRead(BaseModel):
    """
    Public read schema for a scorecard.

    All scores are internal research queue data.
    internal_status is a research label for admin use only — not investment advice.
    """

    id: uuid.UUID
    score_type: str
    company_id: uuid.UUID | None
    screening_candidate_id: uuid.UUID | None
    report_id: uuid.UUID | None
    overall_score: int = Field(ge=0, le=100)
    internal_status: str
    scores: dict[str, Any] | None
    warnings: list[str] | None
    missing_data: list[str] | None
    source_quality_summary: dict[str, Any] | None
    provider_name: str | None
    created_at: datetime
    disclaimer: str = _DISCLAIMER

    model_config = {"from_attributes": True}


class ScorecardList(BaseModel):
    items: list[ScorecardRead]
    total: int
    disclaimer: str = _DISCLAIMER


# ---------------------------------------------------------------------------
# Valuation readiness
# ---------------------------------------------------------------------------


class ValuationReadinessRead(BaseModel):
    """
    Valuation readiness check result.

    No price target, fair value, or upside estimate is produced.
    allowed_methods lists what MIGHT be possible with better data — not current conclusions.
    """

    valuation_readiness: str
    available_inputs: list[str]
    missing_inputs: list[str]
    blocked_methods: list[str]
    allowed_methods: list[str]
    warnings: list[str]
    disclaimer: str = (
        "Valuation readiness check only. "
        "No fair value, price target, or upside estimate is produced. "
        "Further analysis requires primary source data and human review."
    )


# ---------------------------------------------------------------------------
# Score candidate response
# ---------------------------------------------------------------------------


class ScoreCandidateResponse(BaseModel):
    """
    Response from scoring a single candidate.

    Includes scorecard details with full disclaimer.
    """

    candidate_id: uuid.UUID
    scorecard_id: uuid.UUID
    overall_score: int = Field(ge=0, le=100)
    internal_status: str
    scores: dict[str, Any]
    warnings: list[str]
    missing_data: list[str]
    source_quality_summary: dict[str, Any]
    reasoning: str
    next_research_steps: list[str]
    valuation_readiness: ValuationReadinessRead | None = None
    provider_name: str | None = None
    created_at: datetime
    disclaimer: str = _DISCLAIMER


# ---------------------------------------------------------------------------
# Score run response
# ---------------------------------------------------------------------------


class ScoreRunResponse(BaseModel):
    """
    Summary response from scoring all candidates in a screening run.
    """

    run_id: uuid.UUID
    candidates_scored: int
    scorecards_created: int
    score_summary: dict[str, Any]
    disclaimer: str = _DISCLAIMER


# ---------------------------------------------------------------------------
# Ranked candidates
# ---------------------------------------------------------------------------


class RankedCandidateItem(BaseModel):
    """A candidate with its scorecard, ranked by overall_score."""

    candidate_id: str
    ticker: str
    exchange: str | None
    name: str | None
    country: str | None
    sector: str | None
    candidate_status: str
    source_tier: str | None
    data_quality: str | None
    warnings: list[str]
    scorecard: dict[str, Any] | None
    overall_score: int | None
    internal_status: str
    disclaimer: str = _DISCLAIMER


class RankedCandidateList(BaseModel):
    run_id: uuid.UUID
    items: list[RankedCandidateItem]
    total: int
    disclaimer: str = _DISCLAIMER
    note: str = (
        "Candidates are ranked by internal research attractiveness score. "
        "Ranking is NOT a public investment recommendation. "
        "Score is NOT investment advice. Human admin review is required."
    )
