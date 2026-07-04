"""
Phase 14: Company Discovery / Screener — Pydantic schemas.

All schemas are admin/internal only. No BUY/SELL/HOLD/WATCH recommendations,
price targets, fair values, or upside percentages are present or accepted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Allowed values (validation helpers)
# ---------------------------------------------------------------------------

ALLOWED_CANDIDATE_STATUSES = {
    "candidate_found",
    "needs_data",
    "needs_primary_sources",
    "ready_for_deeper_analysis",
    "rejected_by_screen",
    "error",
}

FORBIDDEN_CANDIDATE_STATUSES = {
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "price_target",
    "fair_value",
    "upside_percent",
}

ALLOWED_THEMES = {
    "energy_transition",
    "electrification_grid",
    "defense_security",
    "industrial_resilience",
    "real_assets",
    "materials_mining",
}


# ---------------------------------------------------------------------------
# Screening Universe
# ---------------------------------------------------------------------------


class ScreeningUniverseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    region: str | None = None
    exchange: str | None = None
    sector_filter: str | None = None
    theme: str | None = None
    provider_name: str = "mock"

    @model_validator(mode="after")
    def validate_theme(self) -> "ScreeningUniverseCreate":
        if self.theme and self.theme not in ALLOWED_THEMES:
            raise ValueError(
                f"theme must be one of {sorted(ALLOWED_THEMES)}, got '{self.theme}'"
            )
        return self


class ScreeningUniverseRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    region: str | None
    exchange: str | None
    sector_filter: str | None
    theme: str | None
    provider_name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ScreeningUniverseList(BaseModel):
    items: list[ScreeningUniverseRead]
    total: int


# ---------------------------------------------------------------------------
# Screening Run
# ---------------------------------------------------------------------------


class ScreeningRunCreate(BaseModel):
    universe_id: uuid.UUID
    max_candidates: int = Field(default=50, ge=1, le=500)
    market_cap_min: float | None = Field(default=None, ge=0)
    market_cap_max: float | None = Field(default=None, ge=0)
    keyword_search: str | None = None

    @model_validator(mode="after")
    def validate_market_cap_range(self) -> "ScreeningRunCreate":
        if (
            self.market_cap_min is not None
            and self.market_cap_max is not None
            and self.market_cap_min > self.market_cap_max
        ):
            raise ValueError("market_cap_min must be <= market_cap_max")
        return self


class ScreeningRunRead(BaseModel):
    id: uuid.UUID
    universe_id: uuid.UUID
    status: str
    provider_name: str
    started_at: datetime | None
    completed_at: datetime | None
    parameters_json: dict[str, Any] | None
    summary_json: dict[str, Any] | None
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ScreeningRunList(BaseModel):
    items: list[ScreeningRunRead]
    total: int


# ---------------------------------------------------------------------------
# Screening Candidate
# ---------------------------------------------------------------------------


class ScreeningCandidateRead(BaseModel):
    id: uuid.UUID
    screening_run_id: uuid.UUID
    company_id: uuid.UUID | None
    ticker: str
    exchange: str | None
    name: str | None
    country: str | None
    sector: str | None
    provider_symbol: str | None
    market_cap: float | None
    currency: str | None
    candidate_status: str
    discovery_reasons_json: list[str] | None
    available_data_json: list[str] | None
    missing_data_json: list[str] | None
    source_tier: str | None
    data_quality: str | None
    warnings_json: list[str] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ScreeningCandidateList(BaseModel):
    items: list[ScreeningCandidateRead]
    total: int


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


class PromoteCandidateResponse(BaseModel):
    """
    Result of promoting a candidate to the company analysis funnel.

    Promotion creates or identifies a Company record and makes it available
    for the existing company-analysis workflow.  Nothing is published.
    No recommendation is made or implied.
    """

    candidate_id: uuid.UUID
    company_id: uuid.UUID
    ticker: str
    exchange: str | None
    name: str | None
    promoted: bool
    company_created: bool
    new_candidate_status: str
    message: str
