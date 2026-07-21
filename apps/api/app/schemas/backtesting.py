"""
Phase 22: Judge + Backtesting Framework — Pydantic schemas.

IMPORTANT CONSTRAINTS:
  - No BUY/SELL/HOLD/WATCH public recommendations are produced.
  - No price targets, fair values, or upside percentages are produced.
  - All evaluations are internal historical quality assessments only.
  - Human review is required before any action.

Allowed internal judge statuses (never public recommendations):
  insufficient_data | useful_research | needs_better_sources |
  poor_evidence_quality | outcome_inconclusive | outcome_review_required

Forbidden output terms (must not appear in evaluation outputs):
  BUY | SELL | HOLD | WATCH | price target | fair value | upside %
  guaranteed return | personalized advice
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.services.safety_terms import FORBIDDEN_PHRASES, RATING_TOKENS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BACKTESTING_VERSION = "22.0.0"

INTERNAL_DISCLAIMER = (
    "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. "
    "NOT A PUBLIC RECOMMENDATION. "
    "All backtesting and judge evaluations are internal historical quality "
    "assessments of past research drafts. "
    "No BUY/SELL/HOLD/WATCH recommendations are produced. "
    "No price targets, fair values, or upside percentages are produced. "
    "Human review is required before any action. "
    "Historical evaluation does not predict future results."
)

# Allowed judge status labels — internal evaluation only, never public advice
ALLOWED_JUDGE_STATUSES = {
    "insufficient_data",
    "useful_research",
    "needs_better_sources",
    "poor_evidence_quality",
    "outcome_inconclusive",
    "outcome_review_required",
}

# Forbidden output terms — judge outputs must not contain these.
#
# DEPRECATED as a matching source: scanning is done by the shared three-tier
# scanner in ``app.services.safety_terms``, which knows that a bare substring
# match flags "ENEOS Holdings" for "HOLD". This name survives because it is
# part of a published response schema; it is now a flat re-export of the
# shared vocabulary for documentation and API-shape purposes only. Do not
# iterate it to implement a new gate — call ``safety_terms.scan_text``.
FORBIDDEN_OUTPUT_TERMS = list(RATING_TOKENS) + list(FORBIDDEN_PHRASES)

# Supported evaluation horizons (days)
SUPPORTED_HORIZONS = {30, 90, 180, 365}

# ---------------------------------------------------------------------------
# Historical outcome (computed from mock/offline provider)
# ---------------------------------------------------------------------------


class HistoricalOutcome(BaseModel):
    """Historical price-based evaluation metrics for a past period.

    IMPORTANT: These are historical evaluation metrics only.
    They are NOT future return forecasts or investment recommendations.
    """

    ticker: str
    exchange: str | None = None
    start_date: date
    end_date: date
    horizon_days: int
    benchmark_symbol: str | None = None

    # Raw historical price points (for evaluation of past data quality)
    start_price: float | None = None
    end_price: float | None = None
    benchmark_start_price: float | None = None
    benchmark_end_price: float | None = None

    # Computed historical metrics
    absolute_return: float | None = None  # (end - start) / start
    benchmark_return: float | None = None
    relative_return: float | None = None  # absolute_return - benchmark_return
    volatility_proxy: float | None = None  # simple range-based proxy
    max_drawdown_proxy: float | None = None  # simple proxy

    # Data availability
    data_available: bool = False
    missing_data: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provider_name: str = "mock"
    source_tier: str = "mock"
    data_quality: str = "mock"

    disclaimer: str = (
        "Historical evaluation data only. Not a forecast. Not investment advice."
    )


# ---------------------------------------------------------------------------
# Judge evaluation
# ---------------------------------------------------------------------------


class JudgeEvaluation(BaseModel):
    """Internal research quality evaluation by the ResearchJudgeService.

    IMPORTANT: This is a deterministic quality assessment of past research
    drafts. It does NOT produce investment recommendations.
    Forbidden: BUY/SELL/HOLD/WATCH, price targets, fair values, upside %.
    """

    report_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    ticker: str | None = None

    # Aggregate quality score [0.0–1.0]
    judge_score: float = Field(ge=0.0, le=1.0)

    # Sub-scores [0.0–1.0]
    evidence_quality_score: float = Field(ge=0.0, le=1.0)
    risk_coverage_score: float = Field(ge=0.0, le=1.0)
    outcome_alignment_score: float = Field(ge=0.0, le=1.0, default=0.0)
    data_completeness_score: float = Field(ge=0.0, le=1.0)

    # Internal status — NOT a public recommendation
    judge_status: str = "insufficient_data"

    calibration_notes: list[str] = Field(default_factory=list)
    lessons_learned: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    # Safety gate — forbidden terms check
    safety_passed: bool = True
    forbidden_terms_found: list[str] = Field(default_factory=list)

    evaluated_at: datetime | None = None
    judge_version: str = BACKTESTING_VERSION

    disclaimer: str = INTERNAL_DISCLAIMER


# ---------------------------------------------------------------------------
# Backtest run schemas
# ---------------------------------------------------------------------------


class BacktestRunCreate(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    description: str | None = None
    horizon_days: int | None = Field(default=90, ge=1, le=3650)
    benchmark_symbol: str | None = None
    provider_name: str = "mock"
    parameters: dict[str, Any] = Field(default_factory=dict)


class BacktestRunResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    status: str
    horizon_days: int | None = None
    benchmark_symbol: str | None = None
    provider_name: str
    parameters_json: dict[str, Any] | None = None
    summary_json: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    disclaimer: str = INTERNAL_DISCLAIMER

    model_config = {"from_attributes": True}


class BacktestRunListResponse(BaseModel):
    runs: list[BacktestRunResponse]
    total: int
    disclaimer: str = INTERNAL_DISCLAIMER


# ---------------------------------------------------------------------------
# Backtest result schemas
# ---------------------------------------------------------------------------


class BacktestResultResponse(BaseModel):
    id: uuid.UUID
    backtest_run_id: uuid.UUID
    report_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    scorecard_id: uuid.UUID | None = None
    ticker: str | None = None
    exchange: str | None = None
    evaluation_start_date: date | None = None
    evaluation_end_date: date | None = None
    horizon_days: int | None = None
    benchmark_symbol: str | None = None
    outcome_json: dict[str, Any] | None = None
    judge_evaluation_json: dict[str, Any] | None = None
    warnings_json: list[str] | None = None
    missing_data_json: list[str] | None = None
    status: str
    created_at: datetime
    disclaimer: str = INTERNAL_DISCLAIMER

    model_config = {"from_attributes": True}


class BacktestResultListResponse(BaseModel):
    results: list[BacktestResultResponse]
    total: int
    disclaimer: str = INTERNAL_DISCLAIMER


# ---------------------------------------------------------------------------
# Backtest run summary
# ---------------------------------------------------------------------------


class BacktestRunSummary(BaseModel):
    backtest_run_id: uuid.UUID
    name: str
    status: str
    total_results: int
    completed_results: int
    failed_results: int
    avg_judge_score: float | None = None
    status_breakdown: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = INTERNAL_DISCLAIMER


# ---------------------------------------------------------------------------
# Judge request
# ---------------------------------------------------------------------------


class JudgeReportRequest(BaseModel):
    report_id: uuid.UUID
    include_outcome_alignment: bool = False
    horizon_days: int | None = Field(default=None, ge=1, le=3650)
    benchmark_symbol: str | None = None


class JudgeReportResponse(BaseModel):
    report_id: uuid.UUID
    evaluation: JudgeEvaluation
    disclaimer: str = INTERNAL_DISCLAIMER
