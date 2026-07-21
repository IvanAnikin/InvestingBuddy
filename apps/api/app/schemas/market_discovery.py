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

from pydantic import BaseModel, Field, computed_field, field_validator

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


class ThesisDiscoveryRunCreate(BaseModel):
    """
    Phase 27 — request payload to create a THESIS discovery run.

    An admin describes a market segment / theme / region in natural language;
    the backend parses it, builds a bounded real-company universe, and scans it
    through the existing Phase 25 discovery pipeline. This is internal research
    triage only — never an investment recommendation.
    """

    thesis_text: str = Field(
        min_length=3,
        max_length=2000,
        description="Natural-language market segment / theme / region to search.",
    )
    region: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    exchange: str | None = Field(default=None, max_length=20)
    sector: str | None = Field(default=None, max_length=100)
    industry: str | None = Field(default=None, max_length=100)
    industry_keywords: list[str] | None = Field(
        default=None, description="Optional explicit industry/theme keywords."
    )
    market_cap_bucket: str | None = Field(default=None, max_length=30)
    max_universe_size: int = Field(
        default=25, ge=1, le=50, description="Hard cap on generated universe size."
    )
    max_candidates: int = Field(default=10, ge=1, le=50)
    provider_name: str | None = Field(default="free_real", max_length=50)
    lookback_days: int | None = Field(default=None, ge=1, le=365)
    created_by: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class DiscoveryRunRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    status: str
    # Phase 27 — "ticker" (manual/curated) | "thesis" (segment-generated).
    mode: str = "ticker"
    provider_name: str
    universe_source: str
    universe_count: int
    requested_tickers: list[str] | None
    # Phase 27 thesis fields (NULL for ticker runs).
    thesis_text: str | None = None
    parsed_thesis_json: dict | None = None
    universe_json: dict | None = None
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
    # Phase 25.1 — async execution metadata. Runs are created quickly
    # (status="pending"/"running") and processed in the background; the UI polls
    # this endpoint for progress.
    is_async: bool = True
    message: str | None = None
    disclaimer: str = INTERNAL_DISCLAIMER

    @field_validator("mode", mode="before")
    @classmethod
    def _default_mode(cls, v: str | None) -> str:
        # A transient/legacy ORM row may not have ``mode`` set (the column
        # server-default applies at INSERT). Default to the ticker flow.
        return v or "ticker"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def progress_pct(self) -> float:
        """Processed fraction of the universe (0–100), rounded to 1 decimal."""
        if not self.universe_count:
            return 0.0
        pct = (self.processed_count / self.universe_count) * 100.0
        return round(min(100.0, max(0.0, pct)), 1)


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def progress_pct(self) -> float:
        """Processed fraction of the universe (0–100), rounded to 1 decimal."""
        if not self.universe_count:
            return 0.0
        pct = (self.processed_count / self.universe_count) * 100.0
        return round(min(100.0, max(0.0, pct)), 1)


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

    # Phase 27 — thesis relevance + blended internal score (thesis runs only;
    # NULL for ticker runs). Internal prioritization signals only.
    thesis_relevance_score: float | None = None
    combined_internal_score: float | None = None

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
    # Phase 27 — matched keywords, relevance reason, internal-only interest
    # label, and universe source/tier (NULL for ticker runs).
    thesis_match_json: dict | None = None


class DiscoveryCandidateListResponse(BaseModel):
    candidates: list[DiscoveryCandidateRead]
    total: int
    run_id: uuid.UUID
    disclaimer: str = INTERNAL_DISCLAIMER


# ---------------------------------------------------------------------------
# Run-analysis (promote a candidate to the full analysis workflow)
# ---------------------------------------------------------------------------


class SupportedTheme(BaseModel):
    """One research theme the thesis parser can match (Phase 27.1B)."""

    id: str
    label: str
    keywords: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    regions: list[str] = Field(
        default_factory=list,
        description="Regions the curated registry actually covers for this theme.",
    )
    countries: list[str] = Field(default_factory=list)
    universe_company_count: int = Field(
        default=0,
        description=(
            "How many curated issuers back this theme. A bounded bootstrap "
            "count, not full-market coverage."
        ),
    )


class SupportedSectorAlias(BaseModel):
    """A canonical sector plus the aliases and industries that resolve to it."""

    sector: str
    aliases: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)


class SupportedThemesResponse(BaseModel):
    """Themes + sector taxonomy the admin UI offers as thesis starting points."""

    themes: list[SupportedTheme]
    sectors: list[SupportedSectorAlias]
    examples: list[str] = Field(
        default_factory=list,
        description="Flattened example thesis queries across all themes.",
    )
    coverage_note: str
    disclaimer: str = INTERNAL_DISCLAIMER


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
