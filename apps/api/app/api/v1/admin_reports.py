"""
Admin-only report review endpoints (Phase 11).

These endpoints implement the human review loop for draft reports.
They are NEVER public-facing. No authentication is enforced in Phase 11 —
auth is documented as Phase 12 future work. Access must be restricted at the
network/infrastructure level (VPN, IP allowlist, or internal LB only).

Important constraints:
- Internal approval ≠ public publication. No publish action exists here.
- All outputs remain draft/internal — never investment advice.
- Every action creates an immutable audit event in report_review_events.
- Human reviewer remains fully responsible for review decisions.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.report import (
    ReviewActionRequest,
    ReviewActionResponse,
    ReviewEventList,
    ReviewEventRead,
)
from app.services import report_service

router = APIRouter(tags=["admin-reports"])

_INTERNAL_DISCLAIMER = (
    "INTERNAL ADMIN ONLY. This action does not constitute public publication. "
    "Output is not investment advice. Human reviewer remains responsible."
)


# ---------------------------------------------------------------------------
# Mark under review
# ---------------------------------------------------------------------------


@router.post(
    "/admin/reports/{report_id}/mark-under-review",
    response_model=ReviewActionResponse,
    status_code=200,
)
async def mark_under_review(
    report_id: uuid.UUID,
    request: ReviewActionRequest,
    db: AsyncSession = Depends(get_db),
) -> ReviewActionResponse:
    """
    Mark a draft report as under_review.

    Allowed from: draft, needs_revision.
    Creates an immutable audit event. Does not publish the report.
    """
    report, event = await report_service.mark_under_review(db, report_id, request)
    return ReviewActionResponse(
        report_id=report.id,
        action="mark_under_review",
        from_status=event.from_status,
        to_status=event.to_status,
        note=event.note,
        actor_label=event.actor_label,
        message=f"Report moved to under_review. {_INTERNAL_DISCLAIMER}",
    )


# ---------------------------------------------------------------------------
# Approve internally
# ---------------------------------------------------------------------------


@router.post(
    "/admin/reports/{report_id}/approve",
    response_model=ReviewActionResponse,
    status_code=200,
)
async def approve_report(
    report_id: uuid.UUID,
    request: ReviewActionRequest,
    db: AsyncSession = Depends(get_db),
) -> ReviewActionResponse:
    """
    Approve a report internally (approved_internal).

    Allowed from: under_review.

    Important:
    - This is INTERNAL approval only. It does NOT publish the report publicly.
    - If the report has human_review_required=true or schema validation warnings,
      acknowledge_warnings=true must be set in the request.
    - Creates an immutable audit event.
    - This output is not investment advice and is not a public recommendation.
    """
    report, event = await report_service.approve_report(db, report_id, request)
    return ReviewActionResponse(
        report_id=report.id,
        action="approve",
        from_status=event.from_status,
        to_status=event.to_status,
        note=event.note,
        actor_label=event.actor_label,
        message=(
            "Report approved internally (approved_internal). "
            "PUBLIC PUBLISHING IS NOT IMPLEMENTED. "
            f"{_INTERNAL_DISCLAIMER}"
        ),
    )


# ---------------------------------------------------------------------------
# Reject internally
# ---------------------------------------------------------------------------


@router.post(
    "/admin/reports/{report_id}/reject",
    response_model=ReviewActionResponse,
    status_code=200,
)
async def reject_report(
    report_id: uuid.UUID,
    request: ReviewActionRequest,
    db: AsyncSession = Depends(get_db),
) -> ReviewActionResponse:
    """
    Reject a report (rejected_internal).

    Allowed from: under_review, needs_revision, draft.
    A non-empty note is required.
    Creates an immutable audit event.
    """
    if not request.note or not request.note.strip():
        raise HTTPException(
            status_code=422,
            detail="A non-empty 'note' is required to reject a report.",
        )
    report, event = await report_service.reject_report(db, report_id, request)
    return ReviewActionResponse(
        report_id=report.id,
        action="reject",
        from_status=event.from_status,
        to_status=event.to_status,
        note=event.note,
        actor_label=event.actor_label,
        message=f"Report rejected (rejected_internal). {_INTERNAL_DISCLAIMER}",
    )


# ---------------------------------------------------------------------------
# Needs revision
# ---------------------------------------------------------------------------


@router.post(
    "/admin/reports/{report_id}/needs-revision",
    response_model=ReviewActionResponse,
    status_code=200,
)
async def needs_revision(
    report_id: uuid.UUID,
    request: ReviewActionRequest,
    db: AsyncSession = Depends(get_db),
) -> ReviewActionResponse:
    """
    Mark a report as needing revision (needs_revision).

    Allowed from: under_review.
    A non-empty note describing what needs to change is required.
    Creates an immutable audit event.
    """
    if not request.note or not request.note.strip():
        raise HTTPException(
            status_code=422,
            detail="A non-empty 'note' is required when requesting revision.",
        )
    report, event = await report_service.needs_revision(db, report_id, request)
    return ReviewActionResponse(
        report_id=report.id,
        action="needs_revision",
        from_status=event.from_status,
        to_status=event.to_status,
        note=event.note,
        actor_label=event.actor_label,
        message=f"Report marked as needs_revision. {_INTERNAL_DISCLAIMER}",
    )


# ---------------------------------------------------------------------------
# Review event log
# ---------------------------------------------------------------------------


@router.get(
    "/admin/reports/{report_id}/review-events",
    response_model=ReviewEventList,
)
async def get_review_events(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ReviewEventList:
    """
    Return the immutable audit trail of all review actions taken on this report.

    Events are ordered chronologically (oldest first).
    The report must exist — 404 if not found.
    """
    events = await report_service.get_review_events(db, report_id)
    return ReviewEventList(
        items=[ReviewEventRead.model_validate(e) for e in events],
        total=len(events),
    )


# ---------------------------------------------------------------------------
# Safety: no publish endpoint exists
# ---------------------------------------------------------------------------
# There is intentionally no POST /admin/reports/{id}/publish endpoint.
# Public publishing is not implemented in Phase 11.
# See docs/ROADMAP.md for the planned Phase 12+ publishing workflow.
