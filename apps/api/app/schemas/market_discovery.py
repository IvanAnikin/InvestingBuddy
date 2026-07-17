"""
Phase 25: Real Market Candidate Discovery — Pydantic schemas.

Admin/internal only. These schemas describe an internal research-candidate
discovery queue. They intentionally expose NO recommendation fields:
  - no rating / recommendation
  - no price target / target price
  - no fair value / intrinsic value
  - no upside / downside
  - no undervalued / overvalued label
  - no BUY / SELL / HOLD / WATCH

``candidate_score`` and the component scores are INTERNAL PRIORITIZATION signals
only. Every candidate is human-review-required and non-public.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

INTERNAL_DISCLAIMER = (
    "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. NOT A PUBLIC RECOMMENDATION. "
    "Candidate scores are an internal prioritization signal only — not a "
    "recommendation, not a price target, not a fair value. Human review is "
    "required before any use."
)


# ---------------------------------------------------------------------------
# Run creation / reads
# ---------------------------------------------------------------------------


class DiscoveryRunCreate(BaseModel):
    """Request payload to create and execute an internal discovery run."""

    provider_name: str | None = Field(
        default=None,
        description="Provider to use (default: config DISCOVERY_DEFAULT_PROVIDER).",
        max_length=50,
    )
    universe_source: str = Field(
        default="curated_seed",
        description="curated_seed | manual_tickers",
        max_length=50,
    )
    # Only used when universe_source == "manual_tickers".
    tickers: list[str] | None = Field(
        default=None,
        description="Manual comma-split tickers (uppercased server-side).",
    )
    exchange: str = Field(
        default="US",
        description="Default exchange applied to tickers without one.",
        max_length=20,
    )
    lookback_days: int | None = Field(
        default=None, ge=1, le=365, description="Price/catalyst lookback window."
    )
    created_by: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class DiscoveryRunRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    status: str
    provider_name: str
    universe_source: str
    universe_count: int
    requested_tickers: list[str] | None
    processed_count: int
    candidate_count: int
    error_count: int
    lookback_days: int
    warnings: list[str] | None
    config_json: dict | None
    safety_notes: dict | None
    created_by: str | None
    human_review_required: bool
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    disclaimer: str = INTERNAL_DISCLAIMER


class DiscoveryRunSummary(BaseModel):
    """Compact aggregate view of a run (top scores, status breakdown)."""

    run_id: uuid.UUID
    status: str
    universe_count: int
    processed_count: int
    candidate_count: int
    error_count: int
    top_candidate_score: float | None
    grade_breakdown: dict[str, int]
    warnings: list[str]
    human_review_required: bool = True
    disclaimer: str = INTERNAL_DISCLAIMER


class DiscoveryRunListResponse(BaseModel):
    runs: list[DiscoveryRunRead]
    total: int
    disclaimer: str = INTERNAL_DISCLAIMER


# ---------------------------------------------------------------------------
# Candidate reads
# ---------------------------------------------------------------------------


class DiscoveryCandidateScoreBreakdown(BaseModel):
    """Component-score breakdown for a candidate (internal prioritization only)."""

    candidate_score: float | None
    candidate_score_grade: str | None
    momentum_score: float | None
    fundamentals_score: float | None
    catalyst_score: float | None
    source_quality_score: float | None
    data_completeness_score: float | None
    risk_penalty_score: float | None
    explanation: str | None


class DiscoveryCandidateRead(BaseModel):
    """Admin-friendly candidate row. No recommendation fields are exposed."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    discovery_run_id: uuid.UUID
    ticker: str
    exchange: str
    company_name: str | None
    sector: str | None
    industry: str | None
    country: str | None

    candidate_score: float | None
    candidate_score_grade: str | None
    rank: int | None

    momentum_score: float | None
    fundamentals_score: float | None
    catalyst_score: float | None
    source_quality_score: float | None
    data_completeness_score: float | None
    risk_penalty_score: float | None

    labels_json: list[str] | None
    score_explanation: str | None

    momentum_label: str | None
    catalyst_coverage_status: str | None
    latest_catalyst_date: date | None
    positive_catalyst_count: int
    high_strength_catalyst_count: int
    press_release_event_count: int
    news_event_count: int
    filing_event_count: int
    primary_or_regulator_event_count: int
    aggregator_only_event_count: int

    source_quality: str | None
    missing_info_count: int | None
    blocking_gap_count: int | None

    analysis_report_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None

    human_review_required: bool
    is_public: bool
    safety_valid: bool | None
    schema_valid: bool | None

    created_at: datetime

    disclaimer: str = INTERNAL_DISCLAIMER


class DiscoveryCandidateDetail(DiscoveryCandidateRead):
    """Full candidate detail including trend/financial/source breakdowns."""

    legal_name: str | None
    lei: str | None
    website: str | None

    return_1m: float | None
    return_3m: float | None
    return_6m: float | None
    pct_above_ma50: float | None
    pct_above_ma200: float | None

    latest_close: float | None
    market_cap_mln: float | None
    enterprise_value_mln: float | None
    pe_ratio: float | None
    revenue_mln: float | None
    revenue_growth_yoy_pct: float | None
    net_income_mln: float | None
    free_cash_flow_mln: float | None
    total_debt_mln: float | None
    cash_mln: float | None
    latest_annual_fy: str | None

    source_tiers_json: dict | None
    warnings_json: list[str] | None
    missing_sources_json: list[str] | None
    missing_fields_json: list[str] | None
    raw_signal_json: dict | None


class DiscoveryCandidateListResponse(BaseModel):
    candidates: list[DiscoveryCandidateRead]
    total: int
    run_id: uuid.UUID
    disclaimer: str = INTERNAL_DISCLAIMER


# ---------------------------------------------------------------------------
# Run-analysis (promote a candidate to the full analysis workflow)
# ---------------------------------------------------------------------------


class RunCandidateAnalysisResponse(BaseModel):
    candidate_id: uuid.UUID
    ticker: str
    status: str
    analysis_report_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    provider_name: str
    message: str
    human_review_required: bool = True
    disclaimer: str = INTERNAL_DISCLAIMER
