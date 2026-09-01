"""
Structured schemas for the Phase 28B run-level LLM discovery council.

Where Phase 28A (``schemas.py``) analyses ONE company, Phase 28B analyses a whole
discovery RUN: it reads a bounded evidence pack summarising the run and its
candidate set and decides which candidates deserve deeper internal research.

Two families of types, mirroring 28A:

  Evidence pack  — the bounded, cited input the council is allowed to read.
  Council output — the structured, citation-bound output each agent returns.

Design rules these types enforce or support:
  - Run-level facts get stable ids ``R1, R2, …``; each candidate gets ``C1, C2, …``.
    Agents may cite ONLY those ids.
  - No agent may emit a rating (BUY/SELL/HOLD/WATCH), price target, fair value,
    or upside/downside. That is enforced by the shared safety scanner + the
    discovery citation checker; the controlled label sets are constrained here.
  - The only per-candidate action labels are internal research-workflow states
    (``ALLOWED_INTERNAL_ACTIONS``) — never public recommendations.
  - Output is internal-only, human-review-required, never publication-ready.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Reuse the 28A agent lifecycle statuses so the two councils cannot drift.
from app.services.llm.schemas import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
)

__all__ = [
    "DISCOVERY_COUNCIL_VERSION",
    "DISCOVERY_EVIDENCE_PACK_VERSION",
    "DISCOVERY_COUNCIL_AGENT_ORDER",
    "AGENT_RUN_COORDINATOR",
    "AGENT_CANDIDATE_PRIORITIZATION",
    "AGENT_NOVELTY_COVERAGE",
    "AGENT_DIVERSITY_ANTI_CONVERGENCE",
    "AGENT_EVIDENCE_SUFFICIENCY",
    "AGENT_RISK_GATEKEEPER",
    "AGENT_RUN_RED_TEAM",
    "AGENT_DISCOVERY_CHAIR",
    "CRITICAL_ALWAYS",
    "RESERVED_AGENTS",
    "ALLOWED_INTERNAL_ACTIONS",
    "DEFAULT_INTERNAL_ACTION",
    "ALLOWED_RUN_QUALITY",
    "DEFAULT_RUN_QUALITY",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
    "RunContext",
    "RunFact",
    "CandidateEvidence",
    "DiscoveryEvidencePack",
    "CandidateNote",
    "RunNote",
    "DiscoveryCouncilAgentOutput",
    "DiscoveryCouncilResult",
]

# ---------------------------------------------------------------------------
# Versioning + controlled vocabularies
# ---------------------------------------------------------------------------

DISCOVERY_EVIDENCE_PACK_VERSION = "v1"
DISCOVERY_COUNCIL_VERSION = "v1"

# The eight run-level council agents, in run order. The discovery chair runs
# last and receives the prior agents' summaries as additional (still-cited)
# context.
AGENT_RUN_COORDINATOR = "run_coordinator"
AGENT_CANDIDATE_PRIORITIZATION = "candidate_prioritization"
AGENT_NOVELTY_COVERAGE = "novelty_coverage"
AGENT_DIVERSITY_ANTI_CONVERGENCE = "diversity_anti_convergence"
AGENT_EVIDENCE_SUFFICIENCY = "evidence_sufficiency"
AGENT_RISK_GATEKEEPER = "risk_gatekeeper"
AGENT_RUN_RED_TEAM = "run_red_team"
AGENT_DISCOVERY_CHAIR = "discovery_chair"

DISCOVERY_COUNCIL_AGENT_ORDER: tuple[str, ...] = (
    AGENT_RUN_COORDINATOR,
    AGENT_CANDIDATE_PRIORITIZATION,
    AGENT_NOVELTY_COVERAGE,
    AGENT_DIVERSITY_ANTI_CONVERGENCE,
    AGENT_EVIDENCE_SUFFICIENCY,
    AGENT_RISK_GATEKEEPER,
    AGENT_RUN_RED_TEAM,
    AGENT_DISCOVERY_CHAIR,
)

# Phase 32A Slice 6A — discovery-council reliability. Agents that are ALWAYS
# treated as critical for retry prioritization + the reserved-budget guarantee.
# Mirrors the company council's ``CRITICAL_ALWAYS`` (``schemas.py``); unlike the
# company council, the discovery-council critical set does not depend on the
# evidence pack's contents — it is fixed.
CRITICAL_ALWAYS: frozenset[str] = frozenset(
    {
        AGENT_RUN_COORDINATOR,
        AGENT_RISK_GATEKEEPER,
        AGENT_RUN_RED_TEAM,
        AGENT_DISCOVERY_CHAIR,
    }
)
# The two agents the total-budget reserve specifically protects, so they retain
# retry capacity after earlier (non-reserved) agents drain the shared budget.
# Mirrors the company council's ``RESERVED_AGENTS``.
RESERVED_AGENTS: frozenset[str] = frozenset(
    {AGENT_RUN_RED_TEAM, AGENT_DISCOVERY_CHAIR}
)

# The ONLY per-candidate action labels any agent may return. These are internal
# research-workflow states, never public recommendations. BUY/SELL/HOLD/WATCH are
# absent by construction (and "monitor"/"reject" here are lower-case snake_case,
# so they never trip the ALL-CAPS rating-token scanner).
ALLOWED_INTERNAL_ACTIONS: frozenset[str] = frozenset(
    {
        "research_next",
        "monitor_for_evidence",
        "insufficient_data",
        "reject_for_now",
    }
)
DEFAULT_INTERNAL_ACTION = "insufficient_data"

# The ONLY run-quality labels the discovery chair may return.
ALLOWED_RUN_QUALITY: frozenset[str] = frozenset(
    {"strong", "adequate", "thin", "failed"}
)
DEFAULT_RUN_QUALITY = "thin"

# What separates one candidate from another. The discovery council's persisted
# output had the same defect the company council's did: its per-candidate
# rationales were dominated by data-coverage counts, so the comparison a reader
# saw was "which candidate has fewer missing fields" rather than "which
# business looks most worth the work".
ALLOWED_COMPARISON_DIMENSIONS: frozenset[str] = frozenset(
    {
        "growth_quality",
        "profitability",
        "cash_generation",
        "balance_sheet_resilience",
        "business_quality",
        "catalysts",
        "downside_risk",
        "valuation_context",
        "evidence_confidence",
    }
)


# ---------------------------------------------------------------------------
# Evidence pack
# ---------------------------------------------------------------------------


class RunContext(BaseModel):
    """Identity + shape of the discovery run (summary only — not a claim)."""

    run_id: str | None = None
    mode: str | None = None  # ticker | thesis
    status: str | None = None
    thesis_text: str | None = None
    parsed_theme: str | None = None
    region: str | None = None
    country: str | None = None
    sector: str | None = None
    industry: str | None = None
    provider: str | None = None
    lookback_days: int | None = None
    universe_count: int = 0
    candidate_count: int = 0
    error_count: int = 0
    warning_count: int = 0


class RunFact(BaseModel):
    """One bounded, cited run-level fact. Agents cite these by ``id`` (R1, R2…)."""

    id: str
    label: str
    detail: str | None = None


class CandidateEvidence(BaseModel):
    """One candidate as bounded evidence. Agents cite it by ``id`` (C1, C2…).

    ``candidate_id`` is the internal DB id (opaque); ``id`` is the citation id
    the council uses. Both are recorded so the review can be mapped back to the
    real candidate without the model having to echo a UUID.
    """

    id: str
    candidate_id: str | None = None
    ticker: str | None = None
    exchange: str | None = None
    company_name: str | None = None
    country: str | None = None
    sector: str | None = None
    industry: str | None = None
    thesis_relevance_score: float | None = None
    combined_internal_score: float | None = None
    candidate_score: float | None = None
    candidate_score_grade: str | None = None
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    data_coverage: dict[str, Any] = Field(default_factory=dict)
    catalyst_summary: dict[str, Any] = Field(default_factory=dict)
    safety_valid: bool | None = None
    human_review_required: bool = True
    is_public: bool = False
    warnings: list[str] = Field(default_factory=list)


class DiscoveryEvidencePack(BaseModel):
    """The complete, bounded input the discovery council analyses."""

    evidence_pack_version: str = DISCOVERY_EVIDENCE_PACK_VERSION
    run: RunContext = Field(default_factory=RunContext)
    run_facts: list[RunFact] = Field(default_factory=list)
    candidates: list[CandidateEvidence] = Field(default_factory=list)
    known_gaps: list[str] = Field(default_factory=list)
    run_warnings: list[str] = Field(default_factory=list)
    do_not_infer: list[str] = Field(default_factory=list)

    @property
    def item_count(self) -> int:
        """Total citeable evidence items (run facts + candidates)."""
        return len(self.run_facts) + len(self.candidates)

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    def evidence_ids(self) -> set[str]:
        return {f.id for f in self.run_facts} | {c.id for c in self.candidates}

    def candidate_ids(self) -> set[str]:
        return {c.id for c in self.candidates}

    def candidate_by_id(self, cid: str) -> CandidateEvidence | None:
        for c in self.candidates:
            if c.id == cid:
                return c
        return None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Council agent output
# ---------------------------------------------------------------------------


class CandidateNote(BaseModel):
    """One agent's internal note about a single candidate."""

    candidate_ref: str | None = None  # a candidate citation id (C1, C2…)
    ticker: str | None = None
    exchange: str | None = None
    internal_action: str = DEFAULT_INTERNAL_ACTION
    rationale: str = ""
    citation_ids: list[str] = Field(default_factory=list)
    confidence: str = "low"  # low | medium | high
    # What the candidate looks like as a BUSINESS, not as a data package.
    # Empty on reviews produced before these fields existed, which readers
    # treat as "not assessed" rather than as an error.
    upside_drivers: list[str] = Field(default_factory=list)
    downside_drivers: list[str] = Field(default_factory=list)
    resilience: str = ""
    key_financial_signal: str = ""
    # Which of ALLOWED_COMPARISON_DIMENSIONS this candidate stands out on.
    strongest_dimension: str | None = None


class RunNote(BaseModel):
    """One agent's run-level claim (must cite run/candidate evidence ids)."""

    claim: str
    citation_ids: list[str] = Field(default_factory=list)
    confidence: str = "low"  # low | medium | high


class DiscoveryCouncilAgentOutput(BaseModel):
    """Structured output from one discovery-council agent — the only shape stored."""

    agent_name: str
    status: str = STATUS_COMPLETED  # completed | failed | skipped
    summary: str = ""
    candidate_notes: list[CandidateNote] = Field(default_factory=list)
    run_notes: list[RunNote] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    next_source_tasks: list[str] = Field(default_factory=list)
    # discovery_chair only — one of ALLOWED_RUN_QUALITY.
    run_quality: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class DiscoveryCouncilResult(BaseModel):
    """Aggregated discovery-council output + honest run metadata.

    ``llm_used`` is the single source of truth: True only when a real (or fake,
    in tests) client actually ran the council — never fabricated.
    """

    council_version: str = DISCOVERY_COUNCIL_VERSION
    llm_used: bool = False
    provider: str | None = None
    model: str | None = None
    deployment: str | None = None
    evidence_pack_version: str = DISCOVERY_EVIDENCE_PACK_VERSION
    evidence_item_count: int = 0
    candidate_count: int = 0
    agents: list[DiscoveryCouncilAgentOutput] = Field(default_factory=list)
    agents_completed: int = 0
    agents_failed: int = 0
    agents_skipped: int = 0
    run_quality: str | None = None
    candidates_to_research_next: list[dict[str, Any]] = Field(default_factory=list)
    candidates_to_monitor: list[dict[str, Any]] = Field(default_factory=list)
    candidates_to_reject: list[dict[str, Any]] = Field(default_factory=list)
    candidates_insufficient_data: list[dict[str, Any]] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    next_source_tasks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    safety_valid: bool = True
    # Phase 32A Slice 6A: set True only when the LLM discovery chair did not
    # complete AND the retry bundle (``llm_discovery_council_retry_enabled``) is
    # on, so a DETERMINISTIC, non-consensus discovery-chair summary was attached
    # below. Default False keeps the OFF path identical (mirrors the company
    # council's ``chair_fallback_used`` — Phase 32A Slice 4).
    chair_fallback_used: bool = False
    # Phase 32A Slice 6A: the deterministic discovery-chair synthesis attached
    # when the LLM chair failed. It is NEVER a recommendation/valuation/price
    # objective (``run_quality="failed"``, empty candidate_notes/run_notes ⇒ no
    # citations). Kept SEPARATE from ``agents`` so the failed LLM chair still
    # shows in the honest completed/failed counts (the council is visibly
    # partial); default None keeps the OFF path identical.
    deterministic_chair: DiscoveryCouncilAgentOutput | None = None
    # Phase 32A TPM slice — failure-vs-judgement semantics (mirrors the company
    # council's fields): WHO produced the chair synthesis ("llm_chair" |
    # "deterministic_fallback" | None), how many attempts the chair made, and
    # (on failure) the provider error CLASS NAME, exposed separately from the
    # semantic ``run_quality`` label.
    chair_synthesis_basis: str | None = None
    chair_attempts: int = 0
    chair_error_type: str | None = None
    # Bounded token-usage/throttling accounting for the whole run (counts only).
    token_usage: dict[str, Any] | None = None

    def recount(self) -> None:
        """Refresh the completed/failed/skipped tallies from ``agents``."""
        self.agents_completed = sum(
            1 for a in self.agents if a.status == STATUS_COMPLETED
        )
        self.agents_failed = sum(1 for a in self.agents if a.status == STATUS_FAILED)
        self.agents_skipped = sum(1 for a in self.agents if a.status == STATUS_SKIPPED)

    def to_storage_dict(self, *, created_at: str | None = None) -> dict[str, Any]:
        """Compact, safety-scanned payload persisted under the run metadata JSON.

        Deployment is intentionally omitted — it can name an internal Azure
        resource, so it never leaves the process in persisted metadata. No raw
        prompts or full completions are ever stored; every text field here is
        the already-safety-scanned agent output.
        """
        payload: dict[str, Any] = {
            "type": "llm_discovery_council_review",
            "llm_used": self.llm_used,
            "council_version": self.council_version,
            "provider": self.provider,
            "model": self.model,
            "evidence_pack_version": self.evidence_pack_version,
            "evidence_item_count": self.evidence_item_count,
            "candidate_count": self.candidate_count,
            "agents_completed": self.agents_completed,
            "agents_failed": self.agents_failed,
            "agents_skipped": self.agents_skipped,
            "run_quality": self.run_quality,
            "candidates_to_research_next": list(self.candidates_to_research_next),
            "candidates_to_monitor": list(self.candidates_to_monitor),
            "candidates_to_reject": list(self.candidates_to_reject),
            "candidates_insufficient_data": list(self.candidates_insufficient_data),
            "evidence_gaps": list(self.evidence_gaps),
            "next_source_tasks": list(self.next_source_tasks),
            "agent_outputs": {a.agent_name: a.to_dict() for a in self.agents},
            "warnings": list(self.warnings),
            "safety_valid": self.safety_valid,
            "human_review_required": True,
            "publication_ready": False,
            "disclaimer": (
                "Internal, citation-bound discovery-run research aid. NOT "
                "investment advice and NOT a public recommendation. No rating, "
                "no valuation conclusion, and no return projection is produced. "
                "Every claim cites bounded run/candidate evidence; human review "
                "is required."
            ),
            "created_at": created_at,
        }
        # Phase 32A TPM slice: failure-vs-judgement semantics + bounded usage
        # accounting (counts only — never prompts/completions).
        if self.chair_synthesis_basis is not None:
            payload["chair_synthesis_basis"] = self.chair_synthesis_basis
        if self.chair_attempts:
            payload["chair_attempts"] = self.chair_attempts
        if self.chair_error_type is not None:
            payload["chair_error_type"] = self.chair_error_type
        if self.token_usage is not None:
            payload["token_usage"] = dict(self.token_usage)
        # Phase 32A Slice 6A: surface the deterministic discovery-chair fallback
        # ONLY when it fired (keeps the OFF path + the retried-and-completed path
        # byte-identical — mirrors the company council's Slice-4 pattern).
        if self.chair_fallback_used:
            payload["chair_fallback_used"] = True
            if self.deterministic_chair is not None:
                payload["deterministic_discovery_chair"] = (
                    self.deterministic_chair.to_dict()
                )
        return payload

    @classmethod
    def disabled(cls) -> "DiscoveryCouncilResult":
        """The honest 'discovery council not used' result (deterministic path)."""
        return cls(llm_used=False, provider=None, model=None)
