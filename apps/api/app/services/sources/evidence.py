"""
Framework evidence models — Phase 29A.

Two typed, self-validating models the whole source framework produces and
consumes:

  ``EvidenceSource``  — a description of *where* evidence can come from (an entry
                        in the source registry).
  ``EvidenceItem``    — one bounded, cited piece of evidence a connector returns.

These are richer than the council's ``app.services.llm.schemas.EvidenceItem``
(which stays as-is for the existing single-company / discovery packs). This
model is the framework's canonical shape; ``to_council_item()`` adapts it into
the council pack shape so future connector-fed evidence can flow into the LLM
councils without either side reaching into the other's internals.

Hard invariants enforced here:
  * Every item declares a **content source tier** (the nature of the content).
    A missing/invalid tier is rejected — evidence with no provenance is not
    evidence.
  * URLs are **stripped of credential-bearing query parameters** before storage,
    so a ``?api_token=…`` can never be persisted or returned by the API.
  * Excerpts are **bounded** so a whole filing is never carried around.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.services.sources.rate_limit import RateLimitPolicy
from app.services.sources.redaction import strip_url_secrets
from app.services.sources.taxonomy import (
    AccessMode,
    CostModel,
    ProviderType,
    is_valid_tier,
    tier_rank,
)

EXCERPT_MAX = 400


class EvidenceSource(BaseModel):
    """A registry-level description of one source of evidence.

    Contains no secrets: a source knows its *policy* (cost, access mode, rate
    limits) and its *identity*, never a credential. Credentials live only in
    settings/Key Vault and are read by connectors at call time.
    """

    source_id: str
    name: str
    provider_type: ProviderType
    tier: str
    jurisdiction: str | None = None
    region: str | None = None
    language: str = "en"
    cost_model: CostModel = CostModel.unknown
    access_mode: AccessMode = AccessMode.unknown
    enabled: bool = False
    rate_limit_policy: RateLimitPolicy | None = None
    reliability_note: str | None = None
    connector_key: str | None = None

    @field_validator("tier")
    @classmethod
    def _tier_must_be_valid(cls, v: str) -> str:
        if not is_valid_tier(v):
            raise ValueError(f"Unknown source tier: {v!r}")
        return v


PRIMARY_FACT_VALUE_MAX = 160


class PrimaryFactRef(BaseModel):
    """A bounded, structured reference to one parsed primary fact — Phase 29B.3.

    Carried on the ``EvidenceItem`` that cites it (only on
    ``company_ir_financial_fact`` items). It holds ONLY the fact's structured
    fields plus a short provenance (page / excerpt id / confidence) — never the
    raw document text or the full excerpt body. This is what lets the final
    report surface a real T1 primary-filing datapoint (revenue, reporting
    currency, fiscal year, …) without re-parsing an excerpt string, while every
    inserted value keeps its own source URL + provenance and stays
    ``needs_human_review``.
    """

    field: str
    value: str
    numeric_value: float | None = None
    unit: str | None = None
    currency: str | None = None
    scale: str | None = None
    period: str | None = None
    # Best-effort entity/segment scope this fact was reported under (e.g.
    # "group" for a consolidated figure, or the heading text for a segment
    # breakdown, e.g. "Segment A" — a generic placeholder, never a real
    # company's segment name). ``None`` when it could not be determined from
    # the document structure — never guessed.
    scope: str | None = None
    source_url: str | None = None
    excerpt_id: str | None = None
    page_number: int | None = None
    confidence: str = "medium"  # low | medium | high
    needs_human_review: bool = True

    @field_validator("source_url")
    @classmethod
    def _strip_fact_url(cls, v: str | None) -> str | None:
        return strip_url_secrets(v)

    @field_validator("value")
    @classmethod
    def _bound_value(cls, v: str) -> str:
        """A fact value is short by construction; bound it so no excerpt body
        can ever ride along here."""
        s = str(v).strip()
        if len(s) <= PRIMARY_FACT_VALUE_MAX:
            return s
        return s[: PRIMARY_FACT_VALUE_MAX - 1].rstrip() + "…"


class EvidenceItem(BaseModel):
    """One bounded, cited piece of evidence.

    ``provider_transport_tier`` is the tier of the infrastructure the content was
    retrieved through (SEC EDGAR = ``T2_regulator_or_gov``).
    ``content_source_tier`` is the tier of the content itself (a 10-K =
    ``T1_primary_filing``). Both are recorded; ``content_source_tier`` is the
    required one and is what the council should weight by.
    """

    id: str
    source_id: str
    source_name: str | None = None

    # Transport (how it was fetched) vs content (what it is).
    provider_transport: str | None = None
    provider_transport_tier: str | None = None
    content_source: str | None = None
    content_source_tier: str

    source_type: str | None = None
    title: str | None = None
    url: str | None = None
    date: str | None = None

    language: str = "en"
    original_language: str | None = None
    requires_translation: bool = False

    excerpt: str | None = None
    fields_supported: list[str] = Field(default_factory=list)
    data_quality: str | None = None
    confidence: str | None = None

    # Best-effort entity/segment scope inferred from the document structure this
    # item came from (e.g. "group" for a consolidated figure, or a heading like
    # "Segment A" — a generic placeholder — for a segment breakdown). ``None``
    # when unknown — the inference is deliberately conservative and never
    # guesses a company-specific segment name; see
    # ``primary_document_extractor._infer_scope``.
    scope: str | None = None

    retrieved_at: datetime | None = None
    stale_after_days: int | None = None

    provenance: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    # Phase 29B.3: structured, bounded parsed-fact payload — set ONLY on
    # ``company_ir_financial_fact`` items. Carries no raw excerpt body / document
    # text (see PrimaryFactRef). Absent on every other evidence item.
    primary_fact: PrimaryFactRef | None = None

    # Phase 32A Slice 5 (3c-ii): the sha256 hex of the RAW document bytes for a
    # DEEP-ingested primary-document evidence item (excerpt / validated fact). Its
    # PRESENCE marks an item as deep-ingested, so the citation write can key the
    # canonical Source on the document identity (one Source per distinct document)
    # instead of the synthesized url+tier+excerpt hash. ``None`` on every shallow
    # (Phase 29B.2) / metadata-only / non-document item — never a secret (a hash).
    document_content_hash: str | None = None

    @field_validator("content_source_tier")
    @classmethod
    def _content_tier_required(cls, v: str) -> str:
        if not is_valid_tier(v):
            raise ValueError(
                f"content_source_tier is required and must be a known tier, got {v!r}"
            )
        return v

    @field_validator("provider_transport_tier")
    @classmethod
    def _transport_tier_valid(cls, v: str | None) -> str | None:
        if v is not None and not is_valid_tier(v):
            raise ValueError(f"Unknown provider_transport_tier: {v!r}")
        return v

    @field_validator("url")
    @classmethod
    def _strip_url(cls, v: str | None) -> str | None:
        return strip_url_secrets(v)

    @field_validator("excerpt")
    @classmethod
    def _bound_excerpt(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        return s if len(s) <= EXCERPT_MAX else s[: EXCERPT_MAX - 1].rstrip() + "…"

    @field_validator("provenance")
    @classmethod
    def _strip_provenance_urls(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for entry in v:
            s = str(entry)
            out.append(strip_url_secrets(s) or s)
        return out

    @property
    def tier(self) -> str:
        """The effective tier for scoring/weighting — the content tier."""
        return self.content_source_tier

    @property
    def tier_rank(self) -> int:
        return tier_rank(self.content_source_tier)

    def to_council_item(self) -> dict[str, Any]:
        """Adapt to the council pack's ``EvidenceItem`` shape (a plain dict).

        Kept as a dict rather than importing the council schema so the framework
        has no dependency on the LLM layer.
        """
        return {
            "id": self.id,
            "source_tier": self.content_source_tier,
            "source_type": self.source_type or "source",
            "provider_transport": self.provider_transport,
            "transport_tier": self.provider_transport_tier,
            "content_tier": self.content_source_tier,
            "title": self.title,
            "url": self.url,
            "date": self.date,
            "excerpt": self.excerpt,
            "data_quality": self.data_quality,
            "fields_supported": list(self.fields_supported),
            # Phase 30A: surface the honest language labels so a downstream
            # consumer (the translation layer) can see which excerpts are
            # non-English without re-detecting. Additive + safe.
            "original_language": self.original_language,
            "requires_translation": self.requires_translation,
            # Semantic-grounding signal (best-effort, may be None).
            "scope": self.scope,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_evidence_item(
    *,
    id: str,
    source_id: str,
    content_source_tier: str,
    provider_transport_tier: str | None = None,
    **kwargs: Any,
) -> EvidenceItem:
    """Convenience constructor that timestamps retrieval when omitted."""
    kwargs.setdefault("retrieved_at", _now())
    return EvidenceItem(
        id=id,
        source_id=source_id,
        content_source_tier=content_source_tier,
        provider_transport_tier=provider_transport_tier,
        **kwargs,
    )


__all__ = [
    "EXCERPT_MAX",
    "PRIMARY_FACT_VALUE_MAX",
    "EvidenceSource",
    "EvidenceItem",
    "PrimaryFactRef",
    "build_evidence_item",
]
