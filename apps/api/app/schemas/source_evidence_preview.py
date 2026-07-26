"""
Request / response schemas for the read-only source evidence-preview endpoint
(Phase 29B).

``POST /api/v1/sources/evidence-preview`` runs the source-registry connectors
for one company and returns their bounded, tiered evidence plus honest gaps. It
is an internal admin/validation aid — never a public route, never a URL fetcher:
the request carries only issuer identity (ticker / exchange), never a URL.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.services.sources.evidence import EvidenceItem
from app.services.sources.gaps import SourceGap

# Hard cap on how many source ids one request may target.
MAX_SOURCE_IDS = 12
# Hard cap on evidence items returned (defensive; connectors also self-cap).
MAX_PREVIEW_ITEMS = 40


class EvidencePreviewRequest(BaseModel):
    """Identity-only request. No URL field — this is not a fetch proxy.

    ``include_document_text`` (Phase 29B.2) opts this preview into bounded
    annual-report document extraction (fetch one already-discovered, allowlisted
    document; extract excerpts; parse high-confidence facts). It is still
    identity-only — the document URL comes from the verified-issuer registry, not
    the request. ``max_items`` / ``max_excerpts`` bound the response.
    """

    ticker: str | None = None
    exchange: str | None = None
    company_name: str | None = None
    country: str | None = None
    source_ids: list[str] | None = Field(default=None)
    include_document_text: bool = False
    max_items: int | None = Field(default=None, ge=1, le=MAX_PREVIEW_ITEMS)
    max_excerpts: int | None = Field(default=None, ge=1, le=40)

    @field_validator("ticker", "exchange", "company_name", "country")
    @classmethod
    def _bound_str(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s[:120] if s else None

    @field_validator("source_ids")
    @classmethod
    def _bound_ids(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        # De-dup, keep order, bound length + count.
        seen: dict[str, None] = {}
        for sid in v:
            s = str(sid).strip()[:64]
            if s:
                seen.setdefault(s, None)
        return list(seen)[:MAX_SOURCE_IDS]


class EvidencePreviewResponse(BaseModel):
    """Bounded, secret-free connector output. Contains no credentials."""

    generated_at: datetime
    ticker: str | None = None
    exchange: str | None = None
    connector_layer_enabled: bool
    live_fetch_performed: bool
    document_extraction_performed: bool = False
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    source_gaps: list[SourceGap] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "Read-only source evidence preview. Connector output is internal, "
        "citation-bound research material — not investment advice, not a "
        "recommendation, and no rating, valuation, or return projection is "
        "produced. Any extracted annual-report text is a bounded excerpt of the "
        "issuer's own document — not the full filing — and parsed facts are "
        "unverified until reviewed. Human review is required."
    )


__all__ = [
    "EvidencePreviewRequest",
    "EvidencePreviewResponse",
    "MAX_SOURCE_IDS",
    "MAX_PREVIEW_ITEMS",
]
