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
    # Phase 32A Slice 2: news materiality carried from the upstream deterministic
    # relevance scorer (high | medium | low | irrelevant). Only populated for
    # news/catalyst items when the category-budget flag is on; ``None`` otherwise
    # (and for every non-news item). Additive + defaulted so the off-state pack is
    # unchanged. Never a claim — purely a ranking signal for the budgeter.
    relevance_level: str | None = None
    # Phase 32A Slice 3: runtime-ONLY persistence carriers, EXCLUDED from every
    # serialization (``model_dump`` / ``model_dump_json``) so the evidence-pack
    # JSON the council reads and everything persisted/logged is byte-identical.
    # They preserve the upstream framework item's stable ``source_id`` + structured
    # ``primary_fact`` + ``provenance`` — which ``add_framework_item`` otherwise
    # drops — so a cited E# can resolve to a canonical Source/Citation at persist
    # time. Read only when ``report_citation_persistence_enabled`` is on.
    source_id: str | None = Field(default=None, exclude=True)
    primary_fact: dict[str, Any] | None = Field(default=None, exclude=True)
    provenance: list[str] = Field(default_factory=list, exclude=True)


class EvidencePack(BaseModel):
    """The complete, bounded input the council analyses. Nothing else is read."""

    evidence_pack_version: str = EVIDENCE_PACK_VERSION
    company: EvidenceCompany = Field(default_factory=EvidenceCompany)
    source_policy: SourcePolicy = Field(default_factory=SourcePolicy)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    known_gaps: list[str] = Field(default_factory=list)
    do_not_infer: list[str] = Field(default_factory=list)
    # Phase 29B.2: set by the deterministic evidence budgeter when it compresses
    # the pack (de-dup + tier-preferring truncation) so the omission is honest,
    # not silent. 0 / None when no budgeting was applied.
    omitted_evidence_count: int = 0
    omitted_reason: str | None = None

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


class PersistableEvidence(BaseModel):
    """A runtime snapshot of ONE council evidence-pack item (Phase 32A Slice 3).

    Retained on ``CouncilResult`` so a claim's run-local ``E#`` alias can be
    resolved to a canonical Source + Citation at persist time. ``uid`` is a stable
    per-item identity; ``alias`` is the run-local presentation ``E#`` (positional,
    never a cross-run key). Populated only when
    ``report_citation_persistence_enabled`` is on — empty otherwise, so nothing new
    is serialized/persisted in the dark path. Carries only bounded, secret-stripped
    fields (no raw document body).
    """

    uid: str
    alias: str
    source_tier: str | None = None
    source_type: str | None = None
    provider_transport: str | None = None
    transport_tier: str | None = None
    content_tier: str | None = None
    title: str | None = None
    url: str | None = None
    date: str | None = None
    excerpt: str | None = None
    data_quality: str | None = None
    fields_supported: list[str] = Field(default_factory=list)
    relevance_level: str | None = None
    # Preserved upstream framework provenance (present only for connector items).
    source_id: str | None = None
    primary_fact: dict[str, Any] | None = None
    provenance: list[str] = Field(default_factory=list)


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
    # Phase 29B.2: compact, secret-free summary of any bounded primary-document
    # (annual-report) evidence the connector layer extracted for this company.
    # Empty unless both the connector + document-extraction flags are on. Carries
    # no raw document text — only counts, domain, tier, warnings.
    primary_documents: list[dict[str, Any]] = Field(default_factory=list)
    # Phase 29B.3: structured, bounded HIGH-CONFIDENCE primary facts parsed from
    # the annual-report document (field / value / numeric_value / unit / currency
    # / scale / period + short page/excerpt provenance + confidence). Empty unless
    # a real high-confidence fact exists. Carries no raw document text or excerpt
    # body — only the fact fields + short provenance.
    primary_facts: list[dict[str, Any]] = Field(default_factory=list)
    # Phase 29C.1: bounded, reference-only MACRO CONTEXT for this company's broad
    # theme (sector/industry → official macro statistics publishers). Each entry
    # is a source reference (identity + landing URL + the indicators it covers)
    # plus an honest "figures not fetched" gap. Empty unless ``source_macro_enabled``
    # is on. Carries NO figures, NO dates, and is never a company catalyst or a
    # recommendation — thesis-level background context only.
    macro_context: list[dict[str, Any]] = Field(default_factory=list)
    # Phase 29D.1: bounded, reference-only EVENT CONTEXT for this company's broad
    # theme (sector/industry → official procurement / tender venues, e.g. EU TED /
    # USAspending.gov). Each entry is a WEAK source reference (identity + landing
    # URL + which tenders / awards the venue publishes) plus an honest "live
    # tenders / awards not fetched" gap. Empty unless ``source_event_enabled`` is
    # on. Carries NO specific award, contractor, amount, contract number, or date,
    # and is never a company-specific claim, catalyst, materiality claim, or trade
    # signal — weak thesis-level research-priority background context only.
    event_context: list[dict[str, Any]] = Field(default_factory=list)
    # Phase 30A: bounded, MACHINE-ASSISTED English renderings of non-English
    # evidence excerpts. Each entry ALWAYS preserves the original excerpt + its
    # token-stripped source URL (the citation of record) and is clearly marked
    # machine-assisted / needs human review — NEVER an official translation.
    # Additive context only (the original evidence is never removed or replaced).
    # Empty unless ``source_translation_enabled`` is on and a non-English excerpt
    # was found. Bounded per-excerpt and by ``source_translation_max_excerpts`` —
    # never a whole document.
    translated_excerpts: list[dict[str, Any]] = Field(default_factory=list)
    # Phase 31 hotfix: bounded, secret-free PRIMARY-SOURCE REFERENCES — verified
    # metadata-only items (issuer IR page / annual-report index / regulator venue)
    # that LOCATE a primary source but are NOT extracted document text and NOT a
    # parsed financial fact. Empty unless the connector layer surfaced references.
    primary_source_references: list[dict[str, Any]] = Field(default_factory=list)
    # Phase 31 hotfix: counts distinguishing reference availability from extraction:
    # primary_source_reference_count / primary_document_reference_count /
    # metadata_only_source_count / extracted_primary_document_count / source_gap_count.
    source_reference_counts: dict[str, int] = Field(default_factory=dict)
    # Phase 31 hotfix: bounded, de-duplicated honest connector source-gap messages
    # (e.g. "annual-report links not identified without live extraction"). No secrets.
    source_gaps: list[str] = Field(default_factory=list)
    # Phase 32A Slice 3: runtime-ONLY snapshot of the (post-budget) evidence pack
    # so a cited ``E#`` alias can be resolved to a canonical Source/Citation at
    # persist time. EXCLUDED from serialization (``to_report_dict`` /
    # ``to_metadata_dict`` build explicit dicts and never include it), so the
    # persisted report body + source-summary JSON are byte-identical. Populated by
    # ``run_council`` only when ``report_citation_persistence_enabled`` is on.
    persistable_evidence: list[PersistableEvidence] = Field(
        default_factory=list, exclude=True
    )

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
            "primary_documents": list(self.primary_documents),
            "primary_facts": list(self.primary_facts),
            "macro_context": list(self.macro_context),
            "event_context": list(self.event_context),
            "translated_excerpts": list(self.translated_excerpts),
            "primary_source_references": list(self.primary_source_references),
            "source_reference_counts": dict(self.source_reference_counts),
            "source_gaps": list(self.source_gaps),
        }

    @classmethod
    def disabled(cls) -> "CouncilResult":
        """The honest 'LLM not used' result (deterministic path)."""
        return cls(llm_used=False, provider=None, model=None)
