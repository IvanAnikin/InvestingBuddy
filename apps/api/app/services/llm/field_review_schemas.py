"""
Structured schemas for the Phase 32A Slice 6D DEEP FIELD REVIEW council.

Three councils exist in this codebase and they must never be conflated:

  * ``schemas.py`` / ``council.py``                  — ONE company, deep analysis.
  * ``discovery_schemas.py`` / ``discovery_council.py`` — a discovery run's
    CANDIDATE LIST, shallow triage BEFORE any analysis exists.
  * this module / ``field_review_council.py``        — the DEEP FIELD REVIEW: a
    COMPARATIVE review of the ALREADY-PERSISTED full analyses of 2+ candidates
    from the SAME discovery run.

Two families of types, mirroring the other two councils:

  Field pack     — the bounded, already-persisted input the council may read.
  Council output — the structured, citation-bound output each agent returns.

Design rules these types enforce or support:
  - Run-level facts get stable ids ``R1, R2, …``; each COMPANY gets ``F1, F2, …``.
    Agents may cite ONLY those ids.
  - No agent may emit a rating (BUY/SELL/HOLD/WATCH), price target, fair value,
    intrinsic value, or upside/downside. That is enforced by the shared safety
    scanner + the field-review citation checker; the controlled label sets are
    constrained here.
  - The ONLY per-company placement labels are internal research-priority buckets
    (``ALLOWED_PRIORITY_TIERS``) — never public recommendations.
  - Nothing here is recomputed: every company summary field re-presents data that
    is ALREADY persisted on that company's report / discovery candidate. A field
    with no persisted source stays ``None`` and is rendered as not-available —
    never guessed.
  - Output is internal-only, human-review-required, never publication-ready.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Reuse the 28A agent lifecycle statuses so the three councils cannot drift.
from app.services.llm.schemas import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
)

__all__ = [
    "FIELD_REVIEW_COUNCIL_VERSION",
    "FIELD_REVIEW_PACK_VERSION",
    "FIELD_REVIEW_AGENT_ORDER",
    "AGENT_COMPARATIVE_FINANCIAL_QUALITY",
    "AGENT_THEMATIC_RELEVANCE_MATERIALITY",
    "AGENT_COMPARATIVE_BUSINESS_QUALITY_MOAT",
    "AGENT_COMPARATIVE_CATALYSTS",
    "AGENT_COMPARATIVE_RISK",
    "AGENT_COMPARATIVE_EVIDENCE_SOURCE_QUALITY",
    "AGENT_FIELD_RED_TEAM",
    "AGENT_FIELD_CHAIR",
    "FIELD_CRITICAL_AGENTS",
    "FIELD_RESERVED_AGENTS",
    "ALLOWED_PRIORITY_TIERS",
    "ALLOWED_FIELD_QUALITY",
    "DEFAULT_FIELD_QUALITY",
    "ALLOWED_CONFIDENCE",
    "DEFAULT_CONFIDENCE",
    "FIELD_REVIEW_DISCLAIMER",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
    "FieldRunContext",
    "FieldRunFact",
    "FieldNamedValue",
    "FieldDiscoveryRelevance",
    "FieldDocumentCoverage",
    "FieldEvidenceQuality",
    "FieldCouncilCompletion",
    "FieldCompanyCouncilVerdict",
    "FieldReviewCompanySummary",
    "FieldReviewPack",
    "FieldCompanyNote",
    "FieldNote",
    "FieldPriorityEntry",
    "FieldChairVerdict",
    "FieldReviewAgentOutput",
    "FieldReviewResult",
]

# ---------------------------------------------------------------------------
# Versioning + controlled vocabularies
# ---------------------------------------------------------------------------

FIELD_REVIEW_PACK_VERSION = "v1"
FIELD_REVIEW_COUNCIL_VERSION = "v1"

# The eight comparative agents, in run order. ``field_red_team`` additionally
# receives the prior agents' summaries; ``field_chair`` runs LAST and receives
# everything.
AGENT_COMPARATIVE_FINANCIAL_QUALITY = "comparative_financial_quality"
AGENT_THEMATIC_RELEVANCE_MATERIALITY = "thematic_relevance_materiality"
AGENT_COMPARATIVE_BUSINESS_QUALITY_MOAT = "comparative_business_quality_moat"
AGENT_COMPARATIVE_CATALYSTS = "comparative_catalysts"
AGENT_COMPARATIVE_RISK = "comparative_risk"
AGENT_COMPARATIVE_EVIDENCE_SOURCE_QUALITY = "comparative_evidence_source_quality"
AGENT_FIELD_RED_TEAM = "field_red_team"
AGENT_FIELD_CHAIR = "field_chair"

FIELD_REVIEW_AGENT_ORDER: tuple[str, ...] = (
    AGENT_COMPARATIVE_FINANCIAL_QUALITY,
    AGENT_THEMATIC_RELEVANCE_MATERIALITY,
    AGENT_COMPARATIVE_BUSINESS_QUALITY_MOAT,
    AGENT_COMPARATIVE_CATALYSTS,
    AGENT_COMPARATIVE_RISK,
    AGENT_COMPARATIVE_EVIDENCE_SOURCE_QUALITY,
    AGENT_FIELD_RED_TEAM,
    AGENT_FIELD_CHAIR,
)

# Agents whose failure most degrades the review — they get the larger retry
# allowance and the reserved slice of the wall-time budget.
FIELD_CRITICAL_AGENTS: frozenset[str] = frozenset(
    {
        AGENT_COMPARATIVE_FINANCIAL_QUALITY,
        AGENT_COMPARATIVE_EVIDENCE_SOURCE_QUALITY,
        AGENT_FIELD_RED_TEAM,
        AGENT_FIELD_CHAIR,
    }
)
# The two agents the budget reserve specifically protects, so they retain retry
# capacity after earlier agents drain the shared budget.
FIELD_RESERVED_AGENTS: frozenset[str] = frozenset(
    {AGENT_FIELD_RED_TEAM, AGENT_FIELD_CHAIR}
)

# The ONLY per-company placement buckets. These are internal RESEARCH-PRIORITY
# workflow states, never public recommendations. BUY/SELL/HOLD/WATCH are absent
# by construction, and no bucket implies a trade, a valuation, or a return.
ALLOWED_PRIORITY_TIERS: frozenset[str] = frozenset(
    {
        "strongest_candidates",
        "second_tier",
        "blocked_insufficient_evidence",
    }
)

# The ONLY field-quality labels the field chair may return.
ALLOWED_FIELD_QUALITY: frozenset[str] = frozenset(
    {"strong", "adequate", "thin", "failed"}
)
DEFAULT_FIELD_QUALITY = "thin"

ALLOWED_CONFIDENCE: frozenset[str] = frozenset({"low", "medium", "high"})
DEFAULT_CONFIDENCE = "low"

# Attached to EVERY stored / returned field-review payload.
#
# NOTE: the shared safety scanner runs over the STORED payload, and this string
# is part of it — so the disclaimer must NOT enumerate the forbidden phrases it
# is disclaiming (e.g. spelling out a price objective term would itself trip the
# Tier-3 phrase scanner). It says what is not produced in scanner-safe wording,
# exactly as the other two councils' disclaimers do.
FIELD_REVIEW_DISCLAIMER = (
    "Internal, citation-bound COMPARATIVE research-priority aid across "
    "previously-analyzed companies from ONE discovery run. It compares "
    "already-persisted analyses; it does not re-analyse, re-fetch, or recompute "
    "anything. NOT investment advice and NOT a public recommendation. No rating, "
    "no valuation conclusion, and no return projection is produced. Human review "
    "is required."
)


# ---------------------------------------------------------------------------
# Field pack — bounded, already-persisted input
# ---------------------------------------------------------------------------


class FieldRunContext(BaseModel):
    """Identity + shape of the discovery run being field-reviewed."""

    discovery_run_id: str | None = None
    mode: str | None = None  # ticker | thesis
    status: str | None = None
    thesis_text: str | None = None
    parsed_theme: str | None = None
    region: str | None = None
    country: str | None = None
    sector: str | None = None
    candidate_count: int = 0
    analyzed_candidate_count: int = 0
    included_company_count: int = 0
    missing_candidate_count: int = 0


class FieldRunFact(BaseModel):
    """One bounded, cited run-level fact. Agents cite these by ``id`` (R1, R2…)."""

    id: str
    label: str
    detail: str | None = None


class FieldNamedValue(BaseModel):
    """One already-persisted datapoint re-presented with its own provenance.

    Never computed here. ``value`` is rendered as a string exactly as it was
    persisted; ``source`` / ``source_tier`` / ``as_of`` / ``unit`` come from the
    stored datapoint and are ``None`` when the report did not carry them.
    """

    field: str
    value: str | None = None
    unit: str | None = None
    as_of: str | None = None
    source: str | None = None
    source_tier: str | None = None
    provenance: str | None = None


class FieldDiscoveryRelevance(BaseModel):
    """Discovery-time relevance signals, read straight off the candidate row.

    INTERNAL PRIORITIZATION signals only — never a valuation and never a
    recommendation. Nothing here is recomputed by the field review.
    """

    rank: int | None = None
    candidate_score: float | None = None
    candidate_score_grade: str | None = None
    thesis_relevance_score: float | None = None
    combined_internal_score: float | None = None
    labels: list[str] = Field(default_factory=list)
    source_quality: str | None = None
    catalyst_coverage_status: str | None = None


class FieldDocumentCoverage(BaseModel):
    """Primary-document / extraction coverage, from the persisted view service."""

    attempted_count: int = 0
    extracted_count: int = 0
    metadata_only_count: int = 0
    failed_count: int = 0
    native_count: int = 0
    ocr_count: int = 0
    validated_fact_count: int = 0
    reused_count: int = 0


class FieldEvidenceQuality(BaseModel):
    """Evidence / source quality + tier mix, from the persisted report."""

    total_sources: int = 0
    total_citations: int = 0
    overall_source_quality: str | None = None
    strong_sources_count: int | None = None
    weak_sources_count: int | None = None
    source_type_distribution: dict[str, int] = Field(default_factory=dict)
    source_tiers: list[str] = Field(default_factory=list)


class FieldCouncilCompletion(BaseModel):
    """How completely the COMPANY council ran for this company's report."""

    llm_used: bool = False
    agents_completed: int = 0
    agents_failed: int = 0
    agents_skipped: int = 0
    chair_fallback_used: bool = False


class FieldCompanyCouncilVerdict(BaseModel):
    """The company council chair's stored verdict — read-only, never re-run."""

    committee_label: str | None = None
    provisional_internal_status: str | None = None
    quality_gate_status: str | None = None
    summary: str | None = None
    primary_open_questions: list[str] = Field(default_factory=list)


class FieldReviewCompanySummary(BaseModel):
    """One company's bounded comparative summary. Agents cite it by ``id`` (F#).

    Every field is sourced from an ALREADY-PERSISTED report / discovery candidate
    row. An absent source leaves the field ``None`` / empty — it is rendered as
    not-available and is NEVER guessed, defaulted to a plausible value, or
    carried over from another company.
    """

    id: str
    discovery_candidate_id: str | None = None
    report_id: str | None = None

    # ── Identity ─────────────────────────────────────────────────────────
    ticker: str | None = None
    exchange: str | None = None
    company_name: str | None = None
    country: str | None = None
    sector: str | None = None
    industry: str | None = None

    # ── Discovery relevance (read straight off the candidate row) ────────
    discovery: FieldDiscoveryRelevance = Field(
        default_factory=FieldDiscoveryRelevance
    )

    # ── Already-persisted analysis content ───────────────────────────────
    financial_facts: list[FieldNamedValue] = Field(default_factory=list)
    missing_financial_fields: list[str] = Field(default_factory=list)
    primary_documents: FieldDocumentCoverage = Field(
        default_factory=FieldDocumentCoverage
    )
    evidence_quality: FieldEvidenceQuality = Field(
        default_factory=FieldEvidenceQuality
    )
    financial_strength_notes: list[str] = Field(default_factory=list)
    business_moat_notes: list[str] = Field(default_factory=list)
    catalyst_notes: list[str] = Field(default_factory=list)
    catalyst_coverage_status: str | None = None
    risk_notes: list[str] = Field(default_factory=list)
    # The QUALITATIVE readiness label only (e.g. "not_ready"). Never a number,
    # never a valuation, never a price objective.
    valuation_readiness: str | None = None
    company_council_verdict: FieldCompanyCouncilVerdict = Field(
        default_factory=FieldCompanyCouncilVerdict
    )
    # Stored (read-only) summaries of the company council's financial_analyst /
    # source_quality_critic / red_team agents. No company-council agent is re-run.
    financial_analyst_summary: str | None = None
    source_critic_summary: str | None = None
    red_team_summary: str | None = None
    unresolved_gaps: list[str] = Field(default_factory=list)
    research_completeness_sections_complete: int | None = None
    research_completeness_sections_incomplete: int | None = None
    research_completeness_blocking_gaps: int | None = None
    council_completion: FieldCouncilCompletion = Field(
        default_factory=FieldCouncilCompletion
    )
    # real | mock | mixed | unknown — carried from the report, never guessed.
    data_provenance: str = "unknown"
    # Honest, machine-generated caveats (e.g. "data_provenance=mock"). A caveated
    # company is INCLUDED in the comparison, never silently dropped.
    caveats: list[str] = Field(default_factory=list)


class FieldReviewPack(BaseModel):
    """The complete, bounded input the Deep Field Review council analyses."""

    pack_version: str = FIELD_REVIEW_PACK_VERSION
    run: FieldRunContext = Field(default_factory=FieldRunContext)
    run_facts: list[FieldRunFact] = Field(default_factory=list)
    companies: list[FieldReviewCompanySummary] = Field(default_factory=list)
    known_gaps: list[str] = Field(default_factory=list)
    do_not_infer: list[str] = Field(default_factory=list)

    @property
    def item_count(self) -> int:
        """Total citeable items (run facts + companies)."""
        return len(self.run_facts) + len(self.companies)

    @property
    def company_count(self) -> int:
        return len(self.companies)

    def evidence_ids(self) -> set[str]:
        return {f.id for f in self.run_facts} | {c.id for c in self.companies}

    def company_ids(self) -> set[str]:
        return {c.id for c in self.companies}

    def company_by_id(self, cid: str) -> FieldReviewCompanySummary | None:
        for c in self.companies:
            if c.id == cid:
                return c
        return None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Council agent output
# ---------------------------------------------------------------------------


class FieldCompanyNote(BaseModel):
    """One agent's internal comparative note about a single company."""

    company_ref: str | None = None  # a company citation id (F1, F2…)
    ticker: str | None = None
    exchange: str | None = None
    rationale: str = ""
    citation_ids: list[str] = Field(default_factory=list)
    confidence: str = DEFAULT_CONFIDENCE  # low | medium | high


class FieldNote(BaseModel):
    """One agent's field-level (cross-company) claim. Must cite pack ids."""

    claim: str
    citation_ids: list[str] = Field(default_factory=list)
    confidence: str = DEFAULT_CONFIDENCE  # low | medium | high


class FieldPriorityEntry(BaseModel):
    """One company placed into an internal research-priority bucket by the chair.

    NOT a recommendation, NOT a rating, NOT a valuation. It says only "look at
    this one next", with a cited rationale and any honest caveats.
    """

    company_ref: str
    ticker: str | None = None
    exchange: str | None = None
    rationale: str = ""
    citation_ids: list[str] = Field(default_factory=list)
    confidence: str = DEFAULT_CONFIDENCE  # low | medium | high
    caveats: list[str] = Field(default_factory=list)


class FieldChairVerdict(BaseModel):
    """The field chair's internal research-prioritization output. Three buckets."""

    strongest_candidates: list[FieldPriorityEntry] = Field(default_factory=list)
    second_tier: list[FieldPriorityEntry] = Field(default_factory=list)
    blocked_insufficient_evidence: list[FieldPriorityEntry] = Field(
        default_factory=list
    )
    field_uncertainties: list[str] = Field(default_factory=list)
    field_quality: str = DEFAULT_FIELD_QUALITY  # strong|adequate|thin|failed

    def entries(self) -> list[tuple[str, FieldPriorityEntry]]:
        """(tier, entry) pairs across all three buckets, in tier order."""
        pairs: list[tuple[str, FieldPriorityEntry]] = []
        for tier in (
            "strongest_candidates",
            "second_tier",
            "blocked_insufficient_evidence",
        ):
            for entry in getattr(self, tier):
                pairs.append((tier, entry))
        return pairs


class FieldReviewAgentOutput(BaseModel):
    """Structured output from one field-review agent — the only shape stored."""

    agent_name: str
    status: str = STATUS_COMPLETED  # completed | failed | skipped
    summary: str = ""
    company_notes: list[FieldCompanyNote] = Field(default_factory=list)
    field_notes: list[FieldNote] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    next_research_tasks: list[str] = Field(default_factory=list)
    # field_chair only.
    chair_verdict: FieldChairVerdict | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class FieldReviewResult(BaseModel):
    """Aggregated field-review output + honest run metadata.

    ``llm_used`` is the single source of truth: True only when a real (or fake,
    in tests) client actually ran the council — never fabricated.
    """

    council_version: str = FIELD_REVIEW_COUNCIL_VERSION
    llm_used: bool = False
    provider: str | None = None
    model: str | None = None
    deployment: str | None = None
    pack_version: str = FIELD_REVIEW_PACK_VERSION
    item_count: int = 0
    company_count: int = 0
    agents: list[FieldReviewAgentOutput] = Field(default_factory=list)
    agents_completed: int = 0
    agents_failed: int = 0
    agents_skipped: int = 0
    field_quality: str | None = None
    strongest_candidates: list[dict[str, Any]] = Field(default_factory=list)
    second_tier: list[dict[str, Any]] = Field(default_factory=list)
    blocked_insufficient_evidence: list[dict[str, Any]] = Field(default_factory=list)
    field_uncertainties: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    next_research_tasks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    safety_valid: bool = True

    def recount(self) -> None:
        """Refresh the completed/failed/skipped tallies from ``agents``."""
        self.agents_completed = sum(
            1 for a in self.agents if a.status == STATUS_COMPLETED
        )
        self.agents_failed = sum(1 for a in self.agents if a.status == STATUS_FAILED)
        self.agents_skipped = sum(1 for a in self.agents if a.status == STATUS_SKIPPED)

    def tier_by_company_ref(self) -> dict[str, str]:
        """company_ref -> priority tier, for persisting per-candidate placement."""
        mapping: dict[str, str] = {}
        for tier, entries in (
            ("strongest_candidates", self.strongest_candidates),
            ("second_tier", self.second_tier),
            ("blocked_insufficient_evidence", self.blocked_insufficient_evidence),
        ):
            for entry in entries:
                ref = entry.get("company_ref")
                if isinstance(ref, str) and ref and ref not in mapping:
                    mapping[ref] = tier
        return mapping

    def to_storage_dict(self, *, created_at: str | None = None) -> dict[str, Any]:
        """Compact, safety-scanned payload persisted in ``field_review_runs``.

        Deployment is intentionally omitted — it can name an internal Azure
        resource, so it never leaves the process in persisted metadata. No raw
        prompts or full completions are ever stored; every text field here is
        already-safety-scanned agent output.
        """
        return {
            "type": "deep_field_review",
            "llm_used": self.llm_used,
            "council_version": self.council_version,
            "provider": self.provider,
            "model": self.model,
            "pack_version": self.pack_version,
            "item_count": self.item_count,
            "company_count": self.company_count,
            "agents_completed": self.agents_completed,
            "agents_failed": self.agents_failed,
            "agents_skipped": self.agents_skipped,
            "field_quality": self.field_quality,
            "strongest_candidates": list(self.strongest_candidates),
            "second_tier": list(self.second_tier),
            "blocked_insufficient_evidence": list(self.blocked_insufficient_evidence),
            "field_uncertainties": list(self.field_uncertainties),
            "evidence_gaps": list(self.evidence_gaps),
            "next_research_tasks": list(self.next_research_tasks),
            "agent_outputs": {a.agent_name: a.to_dict() for a in self.agents},
            "warnings": list(self.warnings),
            "safety_valid": self.safety_valid,
            "human_review_required": True,
            "publication_ready": False,
            "disclaimer": FIELD_REVIEW_DISCLAIMER,
            "created_at": created_at,
        }

    @classmethod
    def disabled(cls) -> "FieldReviewResult":
        """The honest 'field review council not used' result."""
        return cls(llm_used=False, provider=None, model=None)
