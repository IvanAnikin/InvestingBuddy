"""
API response schemas for the source registry + health endpoints (Phase 29A).

These are read-only, secret-free envelopes over the framework models in
``app.services.sources``. They power ``GET /api/v1/sources/registry`` and
``GET /api/v1/sources/health``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.sources.connector_base import ConnectorHealth
from app.services.sources.gaps import SourceGap
from app.services.sources.registry import RegisteredSource


class TierInfo(BaseModel):
    code: str
    rank: int
    label: str
    description: str


class SourceRegistryResponse(BaseModel):
    """The full, safe source registry. Contains no secrets."""

    generated_at: datetime
    summary: dict[str, int]
    tiers: list[TierInfo] = Field(default_factory=list)
    sources: list[RegisteredSource] = Field(default_factory=list)
    gaps: list[SourceGap] = Field(default_factory=list)
    disclaimer: str = (
        "Source registry is an internal capability catalogue. Enabled sources are "
        "wired today; planned sources are placeholders for future connector phases "
        "and produce no evidence yet. No secrets are ever exposed here."
    )


class SourceHealthResponse(BaseModel):
    """Safe connector health only — no secrets, no raw error bodies."""

    generated_at: datetime
    connectors: list[ConnectorHealth] = Field(default_factory=list)


__all__ = ["TierInfo", "SourceRegistryResponse", "SourceHealthResponse"]
