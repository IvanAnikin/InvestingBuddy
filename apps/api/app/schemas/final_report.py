"""
Phase 16: Final Report Generator — Pydantic schemas.

All responses include a static disclaimer.
No investment recommendations, BUY/SELL/HOLD/WATCH public ratings,
price targets, fair values, or upside percentages are ever produced.
Human review is required before any action.

Internal statuses are research queue labels only — not public advice.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FINAL_REPORT_VERSION = "16.0.0"

# Allowed internal status labels — research queue only, never public recommendations
ALLOWED_INTERNAL_STATUSES = {
    "not_enough_data",
    "low_priority_research",
    "needs_primary_sources",
    "ready_for_deeper_analysis",
    "high_priority_for_human_review",
    "reject_due_to_data_quality",
}

INTERNAL_DISCLAIMER = (
    "INTERNAL ADMIN DRAFT ONLY. NOT INVESTMENT ADVICE. "
    "NOT A PUBLIC RECOMMENDATION. "
    "All sections are AI-generated internal research notes. "
    "Human review is required before any action. "
    "No public trading recommendation has been produced. "
    "No valuation estimates or return projections have been produced."
)

# ---------------------------------------------------------------------------
# Section schemas
# ---------------------------------------------------------------------------


class ReportSectionStatus(BaseModel):
    name: str
    present: bool
    sourced_fact_count: int = 0
    model_interpretation_count: int = 0
    missing_data_count: int = 0
    assumption_count: int = 0
    human_review_items: list[str] = Field(default_factory=list)
    content: dict[str, Any] = Field(default_factory=dict)


class SafetyValidationResult(BaseModel):
    """Result of the safety gate scan for forbidden output terms."""

    passed: bool
    forbidden_terms_found: list[str] = Field(default_factory=list)
    scanned_sections: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocks_approval: bool = False


class HumanReviewChecklistItem(BaseModel):
    item: str
    required: bool = True
    completed: bool = False
    note: str | None = None


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class RegenerateSectionRequest(BaseModel):
    section_name: str = Field(
        ...,
        description=(
            "Name of the section to regenerate. One of: executive_summary, "
            "company_identity, discovery_rationale, data_availability_summary, "
            "financial_snapshot, internal_scorecard, valuation_readiness, "
            "bull_case, bear_case, risk_analysis, source_quality_review, "
            "citation_validation_review, research_completeness_review, "
            "missing_information, committee_chair_summary, workflow_status, "
            "human_review_checklist, source_citation_appendix."
        ),
    )
    notes: str | None = Field(None, description="Optional admin notes for regeneration")


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class FinalReportResponse(BaseModel):
    """
    Response from any final report generation endpoint.

    Includes all validation results and the disclaimer.
    Human review is always required.
    """

    report_id: uuid.UUID
    status: str = "draft"
    review_status: str = "draft"

    # validation results
    schema_valid: bool
    safety_valid: bool
    human_review_required: bool = True
    # Phase 26 — orthogonal to schema_valid: whether enough SOURCED data exists.
    # A schema-complete report built from not_sourced stand-ins is research_complete=False.
    research_complete: bool = False
    # Public publishing is not implemented — always False, never set True here.
    publication_ready: bool = False

    # internal status (research queue label — never public recommendation)
    internal_status: str | None = None

    # section inventory
    sections_generated: list[str] = Field(default_factory=list)
    missing_sections: list[str] = Field(default_factory=list)

    # validation details
    safety_validation: SafetyValidationResult | None = None
    schema_validation_errors: list[str] = Field(default_factory=list)
    schema_validation_warnings: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)

    # source linkage
    scorecard_id: uuid.UUID | None = None
    source_count: int = 0
    citation_count: int = 0

    # Phase 28A — LLM analysis council metadata (honest; llm_used is never
    # fabricated). When the council is disabled/unavailable these keep their
    # defaults and the report says the LLM was not used.
    llm_used: bool = False
    llm_provider: str | None = None
    llm_model: str | None = None
    council_version: str | None = None
    council_agents_completed: int = 0
    council_agents_failed: int = 0
    council_agents_skipped: int = 0
    evidence_pack_version: str | None = None
    evidence_item_count: int = 0
    committee_label: str | None = None
    # Phase 32A TPM slice — WHO produced the committee synthesis:
    # "llm_chair" (evidence-based judgement, possibly after bounded retries) or
    # "deterministic_fallback" (the chair never completed; the label is a
    # failure default and must not be read as a judgement). None = no council.
    committee_label_basis: str | None = None
    # Provider error CLASS NAME that ended the chair (e.g. "LLMRateLimitError")
    # — infrastructure failure exposed separately from the semantic label.
    chair_error_type: str | None = None

    # human review checklist
    human_review_checklist: list[HumanReviewChecklistItem] = Field(
        default_factory=list
    )

    # mandatory disclaimer — always included
    disclaimer: str = INTERNAL_DISCLAIMER

    model_config = {"from_attributes": True}


class FinalReportValidateResponse(BaseModel):
    """Response from the validate endpoint (re-validates an existing report)."""

    report_id: uuid.UUID
    schema_valid: bool
    safety_valid: bool
    human_review_required: bool = True
    # Phase 26 — structural completeness is not research completeness.
    research_complete: bool = False
    publication_ready: bool = False  # public publishing is not implemented
    safety_validation: SafetyValidationResult | None = None
    schema_validation_errors: list[str] = Field(default_factory=list)
    schema_validation_warnings: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    sections_present: list[str] = Field(default_factory=list)
    missing_sections: list[str] = Field(default_factory=list)
    disclaimer: str = INTERNAL_DISCLAIMER


class RegenerateSectionResponse(BaseModel):
    """Response from the regenerate-section endpoint."""

    report_id: uuid.UUID
    section_name: str
    regenerated: bool
    safety_valid: bool
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = INTERNAL_DISCLAIMER
