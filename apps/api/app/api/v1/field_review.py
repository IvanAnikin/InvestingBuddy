"""
Deep Field Review — API endpoints (Phase 32A Slice 6D).

ADMIN / INTERNAL ONLY. No public routes. (Admin protection is enforced at the
Next.js OAuth + proxy layer — see the note in ``app/main.py`` — so these routes
carry only ``Depends(get_db)`` plus this explicit internal-only contract.)

A Deep Field Review compares the ALREADY-COMPLETED, already-persisted deep
analyses of 2+ candidates from ONE discovery run and produces an internal
RESEARCH-PRIORITY shortlist. It is a THIRD, separate council: NOT the discovery
council (which triages a candidate LIST before any analysis exists) and NOT the
single-company council (which analyses ONE company).

Hard guarantees:
  - No BUY/SELL/HOLD/WATCH labels are ever returned.
  - No price target, fair value, intrinsic value, upside/downside, or return
    projection is ever returned.
  - The three priority buckets are internal research-workflow states only.
  - Nothing is re-analysed, re-fetched, or recomputed.
  - Every result is human-review-required and never publication-ready.

Endpoints:
  POST /api/v1/discovery-runs/{run_id}/field-review
  GET  /api/v1/discovery-runs/{run_id}/field-review
  GET  /api/v1/discovery-runs/{run_id}/field-review-eligibility
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.field_review import (
    FieldReviewEligibilityCandidate,
    FieldReviewEligibilityResponse,
    FieldReviewMissingCandidate,
    FieldReviewResponse,
    InsufficientCandidatesDetail,
)
from app.services import field_review_service as svc
from app.services import market_discovery_service as discovery_svc
from app.services.field_review_service import (
    FieldReviewDisabledError,
    InsufficientAnalyzedCandidatesError,
)

router = APIRouter(prefix="/discovery-runs", tags=["deep-field-review"])

_INTERNAL = (
    "INTERNAL ADMIN ONLY. Not investment advice. Not a public recommendation. "
    "The three priority buckets are an internal research-priority signal only — "
    "no rating, no price target, no fair value, no return projection. Human "
    "review required."
)


def _insufficient_detail(exc: InsufficientAnalyzedCandidatesError) -> dict:
    return InsufficientCandidatesDetail(
        message=str(exc),
        included_candidate_count=exc.included,
        required_candidate_count=exc.required,
        missing_candidates=[
            FieldReviewMissingCandidate.model_validate(m) for m in exc.missing
        ],
    ).model_dump()


@router.post(
    "/{run_id}/field-review",
    response_model=FieldReviewResponse,
    summary="Start an async Deep Field Review over a discovery run (admin only)",
    description=(
        "ADMIN/INTERNAL ONLY. Starts a DEEP FIELD REVIEW ASYNCHRONOUSLY and "
        "returns IMMEDIATELY with a job status (pending) — it does NOT block "
        "until every LLM agent finishes. Poll GET "
        "/discovery-runs/{run_id}/field-review for progress and the completed "
        "review.\n\n"
        "The Deep Field Review is NOT the discovery council and NOT the "
        "single-company council: it compares the ALREADY-COMPLETED, persisted "
        "analyses of the run's candidates against each other and produces an "
        "internal RESEARCH-PRIORITY shortlist (strongest_candidates / "
        "second_tier / blocked_insufficient_evidence). Nothing is re-analysed or "
        "re-fetched.\n\n"
        "Returns 422 when fewer than FIELD_REVIEW_MIN_CANDIDATES of the run's "
        "candidates have a usable completed analysis (the response lists every "
        "candidate that could not be compared and why). Returns 409 when "
        "LLM_COUNCIL_ENABLED or LLM_FIELD_REVIEW_COUNCIL_ENABLED is off and no "
        "prior review exists — no LLM call, no fake result in production. If a "
        "job is already running the current status is returned (no second job "
        "starts); if a completed review exists it is returned unless force=true. "
        + _INTERNAL
    ),
)
async def create_field_review(
    run_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    force: bool = Query(
        default=False,
        description="Re-run even if a completed review already exists.",
    ),
    db: AsyncSession = Depends(get_db),
) -> FieldReviewResponse:
    run = await discovery_svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Discovery run {run_id} not found",
        )
    try:
        row, scheduled = await svc.start_field_review(db, run, force=force)
    except FieldReviewDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except InsufficientAnalyzedCandidatesError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_insufficient_detail(exc),
        ) from exc

    if scheduled:
        # Run the (already-committed pending) job in the background using its own
        # DB session — never the request-scoped one. Only the primitive id is
        # handed to the task.
        background_tasks.add_task(svc.process_field_review_task, str(row.id))
        message = "Deep Field Review started."
    elif row.status in {"pending", "running"}:
        message = "Deep Field Review already in progress."
    else:
        message = "Returning the existing Deep Field Review."

    candidates = await svc.get_candidate_summaries(db, row.id)
    return FieldReviewResponse.from_row(run_id, row, candidates, message=message)


@router.get(
    "/{run_id}/field-review",
    response_model=FieldReviewResponse,
    summary="Get the async Deep Field Review job status / result (admin only)",
    description=(
        "ADMIN/INTERNAL ONLY. Returns the current Deep Field Review job state: "
        "pending/running while a background job is in flight, the completed "
        "comparative result when done, insufficient_candidates when the field "
        "was too small to compare, or failed with a safe reason. A completed "
        "review stays readable even after the flags are later turned off. When "
        "no job has ever run and the review is disabled, a 'disabled' response "
        "is returned; when no job has run and it is enabled, 404. " + _INTERNAL
    ),
)
async def get_field_review(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> FieldReviewResponse:
    run = await discovery_svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Discovery run {run_id} not found",
        )
    row = await svc.get_latest_field_review(db, run_id)
    if row is None:
        # No field review has ever run for this discovery run. If the feature is
        # disabled, surface a clear disabled state for the polling UI.
        if not svc.field_review_enabled():
            return FieldReviewResponse.disabled_response(run_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Deep Field Review found for this discovery run.",
        )
    candidates = await svc.get_candidate_summaries(db, row.id)
    return FieldReviewResponse.from_row(run_id, row, candidates)


@router.get(
    "/{run_id}/field-review-eligibility",
    response_model=FieldReviewEligibilityResponse,
    summary="Which candidates a Deep Field Review could compare now (admin only)",
    description=(
        "ADMIN/INTERNAL ONLY. Returns the SAME eligibility verdict the Deep "
        "Field Review itself applies — it calls the review's own candidate "
        "resolver, so the admin UI can never advertise an eligibility the "
        "backend would reject with a 422.\n\n"
        "``with_full_analysis_count`` counts candidates whose linked analysis "
        "report exists, is a FINAL report, and is schema-valid (regardless of "
        "the per-review company cap). ``included_count`` is the subset also "
        "within the cap — what a review started right now would compare. "
        "``not_comparable_count`` counts candidates that WERE analysed but "
        "cannot be compared (report deleted / draft only / schema-invalid / "
        "over the cap); candidates never analysed are reported separately as "
        "``not_yet_analyzed_count``.\n\n"
        "Counts and identifiers only — no report content, no rating, no "
        "valuation. Does not start anything and never runs an LLM. " + _INTERNAL
    ),
)
async def get_field_review_eligibility(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> FieldReviewEligibilityResponse:
    run = await discovery_svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Discovery run {run_id} not found",
        )
    summary = await svc.summarize_field_eligibility(db, run)
    return FieldReviewEligibilityResponse(
        discovery_run_id=run_id,
        candidate_count=summary.candidate_count,
        with_full_analysis_count=summary.with_full_analysis_count,
        included_count=summary.included_count,
        not_comparable_count=summary.not_comparable_count,
        not_yet_analyzed_count=summary.not_yet_analyzed_count,
        required_candidate_count=summary.required_candidate_count,
        max_companies=summary.max_companies,
        candidates=[
            FieldReviewEligibilityCandidate.model_validate(row)
            for row in summary.candidates
        ],
    )
