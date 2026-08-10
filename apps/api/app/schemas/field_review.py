"""
API schemas for the Deep Field Review (Phase 32A Slice 6D).

INTERNAL ADMIN ONLY. A Deep Field Review compares the ALREADY-COMPLETED,
already-persisted deep analyses of 2+ candidates from ONE discovery run and
produces an internal RESEARCH-PRIORITY shortlist. It is NOT the discovery
council (candidate-list triage) and NOT the single-company council.

The only per-company placements are the three internal research buckets
(strongest_candidates / second_tier / blocked_insufficient_evidence). No rating,
price target, fair value, intrinsic value, upside/downside, or return projection
is ever returned. Always human-review-required, never publication-ready. Raw
prompts and completions are never sent to the client.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.field_review import FieldReviewCandidateSummary, FieldReviewRun
from app.services.llm.field_review_schemas import FIELD_REVIEW_DISCLAIMER

__all__ = [
    "FieldPriorityEntryRead",
    "FieldReviewCandidateRow",
    "FieldReviewMissingCandidate",
    "FieldReviewResponse",
    "InsufficientCandidatesDetail",
]


class FieldPriorityEntryRead(BaseModel):
    """One company placed in an internal research-priority bucket by the chair."""

    company_ref: str | None = None
    discovery_candidate_id: str | None = None
    report_id: str | None = None
    ticker: str | None = None
    exchange: str | None = None
    rationale: str | None = None
    citation_ids: list[str] = Field(default_factory=list)
    confidence: str | None = None
    caveats: list[str] = Field(default_factory=list)


class FieldReviewCandidateRow(BaseModel):
    """One candidate considered by the review — included OR excluded."""

    model_config = ConfigDict(from_attributes=True)

    citation_ref: str
    discovery_candidate_id: uuid.UUID | None = None
    report_id: uuid.UUID | None = None
    ticker: str | None = None
    exchange: str | None = None
    included: bool = False
    exclusion_reason: str | None = None
    data_provenance: str | None = None
    priority_tier: str | None = None


class FieldReviewMissingCandidate(BaseModel):
    """A candidate that could not be compared, with its honest reason."""

    discovery_candidate_id: str | None = None
    report_id: str | None = None
    ticker: str | None = None
    exchange: str | None = None
    exclusion_reason: str | None = None


class InsufficientCandidatesDetail(BaseModel):
    """422 body: exactly why a comparative review is not possible yet."""

    message: str
    included_candidate_count: int
    required_candidate_count: int
    missing_candidates: list[FieldReviewMissingCandidate] = Field(
        default_factory=list
    )


class FieldReviewResponse(BaseModel):
    """A Deep Field Review job status + (when complete) its comparative result."""

    discovery_run_id: uuid.UUID
    field_review_run_id: uuid.UUID | None = None
    # pending | running | completed | completed_with_warnings | failed |
    # insufficient_candidates | disabled
    status: str | None = None
    review_available: bool = False
    message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None

    llm_used: bool = False
    council_version: str | None = None
    provider: str | None = None
    model: str | None = None
    pack_version: str | None = None
    item_count: int = 0
    company_count: int = 0

    included_candidate_count: int = 0
    missing_candidate_count: int = 0
    agents_completed: int = 0
    agents_failed: int = 0
    agents_skipped: int = 0

    # strong | adequate | thin | failed — an internal field-quality label.
    field_quality: str | None = None
    strongest_candidates: list[FieldPriorityEntryRead] = Field(default_factory=list)
    second_tier: list[FieldPriorityEntryRead] = Field(default_factory=list)
    blocked_insufficient_evidence: list[FieldPriorityEntryRead] = Field(
        default_factory=list
    )
    field_uncertainties: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    next_research_tasks: list[str] = Field(default_factory=list)
    agent_outputs: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    candidates: list[FieldReviewCandidateRow] = Field(default_factory=list)

    safety_valid: bool = True
    human_review_required: bool = True
    publication_ready: bool = False
    created_at: str | None = None
    disclaimer: str = FIELD_REVIEW_DISCLAIMER

    @classmethod
    def from_row(
        cls,
        discovery_run_id: uuid.UUID,
        row: FieldReviewRun,
        candidates: list[FieldReviewCandidateSummary] | None = None,
        *,
        message: str | None = None,
    ) -> "FieldReviewResponse":
        """Build the response from a persisted field-review row.

        The review payload's fields are spread at the top level when a completed
        review is attached; a queued/running job populates only the lifecycle
        fields. ``disclaimer`` is always the canonical one — a stored payload can
        never weaken it.
        """
        review = row.review_json if isinstance(row.review_json, dict) else {}
        resp = cls(
            discovery_run_id=discovery_run_id,
            field_review_run_id=row.id,
            status=row.status,
            review_available=bool(
                row.status in {"completed", "completed_with_warnings"} and review
            ),
            message=message,
            started_at=row.started_at.isoformat() if row.started_at else None,
            completed_at=row.completed_at.isoformat() if row.completed_at else None,
            error=row.error,
            llm_used=bool(row.llm_used),
            council_version=row.council_version,
            provider=row.provider,
            model=row.model,
            pack_version=review.get("pack_version"),
            item_count=int(review.get("item_count") or 0),
            company_count=int(review.get("company_count") or 0),
            included_candidate_count=row.included_candidate_count or 0,
            missing_candidate_count=row.missing_candidate_count or 0,
            agents_completed=row.agents_completed or 0,
            agents_failed=row.agents_failed or 0,
            agents_skipped=int(review.get("agents_skipped") or 0),
            field_quality=row.field_quality,
            strongest_candidates=[
                FieldPriorityEntryRead.model_validate(e)
                for e in review.get("strongest_candidates") or []
            ],
            second_tier=[
                FieldPriorityEntryRead.model_validate(e)
                for e in review.get("second_tier") or []
            ],
            blocked_insufficient_evidence=[
                FieldPriorityEntryRead.model_validate(e)
                for e in review.get("blocked_insufficient_evidence") or []
            ],
            field_uncertainties=list(review.get("field_uncertainties") or []),
            evidence_gaps=list(review.get("evidence_gaps") or []),
            next_research_tasks=list(review.get("next_research_tasks") or []),
            agent_outputs=dict(review.get("agent_outputs") or {}),
            warnings=list(row.warnings_json or review.get("warnings") or []),
            candidates=[
                FieldReviewCandidateRow.model_validate(c) for c in candidates or []
            ],
            safety_valid=(
                row.safety_valid if row.safety_valid is not None else True
            ),
            human_review_required=True,
            publication_ready=False,
            created_at=review.get("created_at"),
        )
        return resp

    @classmethod
    def disabled_response(
        cls, discovery_run_id: uuid.UUID, *, message: str | None = None
    ) -> "FieldReviewResponse":
        """A ``disabled`` lifecycle response — the review is off and none exists."""
        return cls(
            discovery_run_id=discovery_run_id,
            status="disabled",
            review_available=False,
            llm_used=False,
            message=message or "Deep Field Review is disabled.",
        )
