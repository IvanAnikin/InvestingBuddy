"""
Structured schemas for the Phase 28A single-company LLM analysis council.

Everything the council produces is typed here so validation happens at the
data layer, not by ad-hoc dict poking. Two families of types:

  Evidence pack  — the bounded, cited input the council is allowed to read.
  Council output — the structured, citation-bound output each agent returns.

Design rules that these types enforce or support:
  - Every evidence item has a stable id (E1, E2, ...). Agents may cite ONLY ids.
  - SEC EDGAR is a *transport* (T2_regulator_or_gov); a company filing pulled
    through EDGAR is *content* T1_primary_filing. Both are recorded separately.
  - No agent may emit a rating (BUY/SELL/HOLD/WATCH), price target, fair value,
    or upside/downside. That is enforced by the safety scanner + citation
    checker, but the committee-chair label set is constrained here.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Versioning + controlled vocabularies
# ---------------------------------------------------------------------------

EVIDENCE_PACK_VERSION = "v1"
COUNCIL_VERSION = "v1"

# Source tiers — mirror app.integrations.financial_data_provider.SourceTier
# values, kept as bare strings here so this module has no import cycle.
TIER_T1_PRIMARY_FILING = "T1_primary_filing"
TIER_T1_PRIMARY_COMPANY_SOURCE = "T1_primary_company_source"
TIER_T2_REGULATOR_OR_GOV = "T2_regulator_or_gov"
TIER_T3_INDUSTRY_SPECIALIST = "T3_industry_specialist"
TIER_T4_QUALITY_MEDIA = "T4_quality_media"
TIER_T5_API_AGGREGATOR = "T5_api_aggregator"
TIER_T6_MODEL_ESTIMATE = "T6_model_estimate"

# The eight council agents, in run order. The committee chair runs last and
# receives the prior agents' summaries as additional (still-cited) context.
AGENT_FINANCIAL_ANALYST = "financial_analyst"
AGENT_BUSINESS_MOAT = "business_moat"
AGENT_CATALYST = "catalyst"
AGENT_RISK_GOVERNANCE = "risk_governance"
AGENT_VALUATION_GUARD = "valuation_guard"
AGENT_SOURCE_QUALITY_CRITIC = "source_quality_critic"
AGENT_RED_TEAM = "red_team"
AGENT_COMMITTEE_CHAIR = "committee_chair"

COUNCIL_AGENT_ORDER: tuple[str, ...] = (
    AGENT_FINANCIAL_ANALYST,
    AGENT_BUSINESS_MOAT,
    AGENT_CATALYST,
    AGENT_RISK_GOVERNANCE,
    AGENT_VALUATION_GUARD,
    AGENT_SOURCE_QUALITY_CRITIC,
    AGENT_RED_TEAM,
    AGENT_COMMITTEE_CHAIR,
)

# The ONLY labels the committee chair may return. These are internal research
# workflow states, never public recommendations. BUY/SELL/HOLD/WATCH are absent
# by construction.
ALLOWED_COMMITTEE_LABELS: frozenset[str] = frozenset(
    {
        "internal_research_candidate",
        "requires_more_evidence",
        "insufficient_data",
        "monitor_for_new_evidence",
        "reject_for_now",
    }
)
DEFAULT_COMMITTEE_LABEL = "insufficient_data"

# Agent lifecycle statuses.
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Evidence pack
# ---------------------------------------------------------------------------


class EvidenceCompany(BaseModel):
    """The company the evidence pack is about (identity only — not a claim)."""

    ticker: str | None = None
    exchange: str | None = None
    company_name: str | None = None
    legal_name: str | None = None
    country: str | None = None
    sector: str | None = None
    industry: str | None = None


class SourcePolicy(BaseModel):
    """What the council is and is not allowed to treat as evidence."""

    allowed_tiers: list[str] = Field(default_factory=list)
    excluded_sources: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    """One bounded, cited piece of evidence.

    ``transport_tier`` is the infrastructure the content was retrieved through
    (e.g. SEC EDGAR = T2_regulator_or_gov). ``content_tier`` is the nature of
    the content itself (e.g. a 10-K filing = T1_primary_filing). ``source_tier``
    is kept for backwards-compatibility and mirrors ``content_tier``.
    """

    id: str
    source_tier: str
    source_type: str
    provider_transport: str | None = None
    transport_tier: str | None = None
    content_tier: str | None = None
    title: str | None = None
    url: str | None = None
    date: str | None = None
    excerpt: str | None = None
    data_quality: str | None = None
    fields_supported: list[str] = Field(default_factory=list)


class EvidencePack(BaseModel):
    """The complete, bounded input the council analyses. Nothing else is read."""

    evidence_pack_version: str = EVIDENCE_PACK_VERSION
    company: EvidenceCompany = Field(default_factory=EvidenceCompany)
    source_policy: SourcePolicy = Field(default_factory=SourcePolicy)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    known_gaps: list[str] = Field(default_factory=list)
    do_not_infer: list[str] = Field(default_factory=list)

    @property
    def item_count(self) -> int:
        return len(self.evidence_items)

    def evidence_ids(self) -> set[str]:
        return {item.id for item in self.evidence_items}

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Council agent output
# ---------------------------------------------------------------------------


class AgentKeyPoint(BaseModel):
    claim: str
    citation_ids: list[str] = Field(default_factory=list)
    confidence: str = "low"  # low | medium | high
    data_quality: str = "C"  # A | B | C | D
    # Set by the citation checker when a claim is an explicit limitation or a
    # clearly-labelled model inference (so an un-cited claim is not treated as
    # an unsupported factual assertion).
    is_limitation: bool = False
    is_model_inference: bool = False


class AgentRiskGap(BaseModel):
    item: str
    citation_ids: list[str] = Field(default_factory=list)
    severity: str = "low"  # low | medium | high


class CouncilAgentOutput(BaseModel):
    """Structured output from one council agent — the only shape ever stored."""

    agent_name: str
    status: str = STATUS_COMPLETED  # completed | failed | skipped
    summary: str = ""
    key_points: list[AgentKeyPoint] = Field(default_factory=list)
    risks_or_gaps: list[AgentRiskGap] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    # committee_chair only — one of ALLOWED_COMMITTEE_LABELS.
    committee_label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class CouncilResult(BaseModel):
    """Aggregated council output + honest run metadata.

    ``llm_used`` is the single source of truth the report metadata reflects. It
    is True only when a real (or fake, in tests) client actually ran the
    council — never fabricated.
    """

    council_version: str = COUNCIL_VERSION
    llm_used: bool = False
    provider: str | None = None
    model: str | None = None
    deployment: str | None = None
    evidence_pack_version: str = EVIDENCE_PACK_VERSION
    evidence_item_count: int = 0
    agents: list[CouncilAgentOutput] = Field(default_factory=list)
    agents_completed: int = 0
    agents_failed: int = 0
    agents_skipped: int = 0
    committee_label: str | None = None
    warnings: list[str] = Field(default_factory=list)

    def recount(self) -> None:
        """Refresh the completed/failed/skipped tallies from ``agents``."""
        self.agents_completed = sum(
            1 for a in self.agents if a.status == STATUS_COMPLETED
        )
        self.agents_failed = sum(1 for a in self.agents if a.status == STATUS_FAILED)
        self.agents_skipped = sum(1 for a in self.agents if a.status == STATUS_SKIPPED)

    def to_report_dict(self) -> dict[str, Any]:
        """Full council payload embedded into the report content (safety-scanned).

        Every text field here is scanned by the report-level safety gate, so it
        must already be safe (the council quarantines unsafe agent output before
        it reaches this point).
        """
        return {
            "type": "llm_council_analysis",
            "council_version": self.council_version,
            "llm_used": self.llm_used,
            "provider": self.provider,
            "model": self.model,
            "evidence_pack_version": self.evidence_pack_version,
            "evidence_item_count": self.evidence_item_count,
            "agents_completed": self.agents_completed,
            "agents_failed": self.agents_failed,
            "agents_skipped": self.agents_skipped,
            "committee_label": self.committee_label,
            "agents": [a.to_dict() for a in self.agents],
            "warnings": list(self.warnings),
            "disclaimer": (
                "LLM council output is an internal, citation-bound research aid. "
                "It is NOT investment advice and NOT a public recommendation. No "
                "rating, no valuation conclusion, and no return projection is "
                "produced. Every claim cites bounded evidence; human review is "
                "required."
            ),
            "human_review_required": True,
        }

    def to_metadata_dict(self) -> dict[str, Any]:
        """Compact metadata for the report's source-summary JSON + API response.

        Deployment is intentionally omitted here — it can name an internal Azure
        resource, so it never leaves the process in persisted metadata.
        """
        return {
            "llm_used": self.llm_used,
            "council_version": self.council_version,
            "provider": self.provider,
            "model": self.model,
            "evidence_pack_version": self.evidence_pack_version,
            "evidence_item_count": self.evidence_item_count,
            "agents_completed": self.agents_completed,
            "agents_failed": self.agents_failed,
            "agents_skipped": self.agents_skipped,
            "committee_label": self.committee_label,
            "agents": [a.to_dict() for a in self.agents],
        }

    @classmethod
    def disabled(cls) -> "CouncilResult":
        """The honest 'LLM not used' result (deterministic path)."""
        return cls(llm_used=False, provider=None, model=None)
