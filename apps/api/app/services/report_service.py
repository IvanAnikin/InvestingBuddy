import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import Report
from app.models.review_event import ReportReviewEvent
from app.schemas.report import ReportCreate, ReviewActionRequest


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Report read operations
# ---------------------------------------------------------------------------


async def list_reports(
    db: AsyncSession, limit: int = 50, offset: int = 0
) -> tuple[list[Report], int]:
    count_result = await db.execute(select(func.count()).select_from(Report))
    total = count_result.scalar_one()
    result = await db.execute(
        select(Report).order_by(Report.created_at.desc()).offset(offset).limit(limit)
    )
    return list(result.scalars().all()), total


async def get_report(db: AsyncSession, report_id: uuid.UUID) -> Report | None:
    result = await db.execute(select(Report).where(Report.id == report_id))
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Report write operations
# ---------------------------------------------------------------------------


async def create_draft_report(db: AsyncSession, data: ReportCreate) -> Report:
    report = Report(
        title=data.title,
        slug=data.slug,
        report_type=data.report_type,
        summary=data.summary,
        content_markdown=data.content_markdown,
        period_start=data.period_start,
        period_end=data.period_end,
        created_by_agent_run_id=data.created_by_agent_run_id,
        status="draft",
        review_status="draft",
        human_review_required=data.human_review_required,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


# ---------------------------------------------------------------------------
# Phase 11: Review workflow operations
# ---------------------------------------------------------------------------

# Allowed status transitions for each review action.
# Any current status not listed in the value set will be rejected.
_ALLOWED_FROM: dict[str, set[str]] = {
    "mark_under_review": {"draft", "needs_revision"},
    "approve": {"under_review"},
    "reject": {"under_review", "needs_revision", "draft"},
    "needs_revision": {"under_review"},
}

_ACTION_TO_STATUS: dict[str, str] = {
    "mark_under_review": "under_review",
    "approve": "approved_internal",
    "reject": "rejected_internal",
    "needs_revision": "needs_revision",
}

# Actions that mandate a non-empty note.
_NOTE_REQUIRED = {"reject", "needs_revision"}


async def _get_report_or_404(db: AsyncSession, report_id: uuid.UUID) -> Report:
    report = await get_report(db, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


async def _create_review_event(
    db: AsyncSession,
    report_id: uuid.UUID,
    action: str,
    from_status: str | None,
    to_status: str,
    note: str | None,
    actor_label: str | None,
) -> ReportReviewEvent:
    event = ReportReviewEvent(
        report_id=report_id,
        action=action,
        from_status=from_status,
        to_status=to_status,
        note=note,
        actor_label=actor_label,
    )
    db.add(event)
    return event


async def _apply_review_action(
    db: AsyncSession,
    report_id: uuid.UUID,
    action: str,
    request: ReviewActionRequest,
) -> tuple[Report, ReportReviewEvent]:
    """Core review transition: validate, mutate report, create audit event."""
    report = await _get_report_or_404(db, report_id)

    # Note required for reject / needs_revision.
    if action in _NOTE_REQUIRED and not (request.note and request.note.strip()):
        raise HTTPException(
            status_code=422,
            detail=f"A non-empty 'note' is required for the '{action}' action.",
        )

    # Status transition guard.
    allowed_from = _ALLOWED_FROM.get(action, set())
    if report.review_status not in allowed_from:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot apply action '{action}' when review_status is "
                f"'{report.review_status}'. "
                f"Allowed from: {sorted(allowed_from)}."
            ),
        )

    # Approval requires explicit acknowledgement when warnings exist.
    if action == "approve":
        has_warnings = report.human_review_required is not False
        schema_issue = False
        # Check content for schema validation marker (best-effort, non-blocking if absent).
        if report.content_markdown and "schema_valid: false" in report.content_markdown.lower():
            schema_issue = True
        if (has_warnings or schema_issue) and not request.acknowledge_warnings:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This report has warnings (human_review_required=true or schema "
                    "validation issues). Set acknowledge_warnings=true to proceed with approval."
                ),
            )

    from_status = report.review_status
    to_status = _ACTION_TO_STATUS[action]
    now = _utcnow()

    # Mutate report.
    report.review_status = to_status
    report.reviewed_at = now
    report.reviewer_note = request.note

    if action == "approve":
        report.review_decision_reason = request.note
        report.approved_by = request.actor_label or "admin"
        report.rejected_by = None
    elif action == "reject":
        report.review_decision_reason = request.note
        report.rejected_by = request.actor_label or "admin"
        report.approved_by = None
    elif action == "needs_revision":
        report.review_decision_reason = request.note

    # Audit event.
    event = await _create_review_event(
        db=db,
        report_id=report.id,
        action=action,
        from_status=from_status,
        to_status=to_status,
        note=request.note,
        actor_label=request.actor_label,
    )

    await db.commit()
    await db.refresh(report)
    await db.refresh(event)
    return report, event


async def mark_under_review(
    db: AsyncSession,
    report_id: uuid.UUID,
    request: ReviewActionRequest,
) -> tuple[Report, ReportReviewEvent]:
    return await _apply_review_action(db, report_id, "mark_under_review", request)


async def approve_report(
    db: AsyncSession,
    report_id: uuid.UUID,
    request: ReviewActionRequest,
) -> tuple[Report, ReportReviewEvent]:
    return await _apply_review_action(db, report_id, "approve", request)


async def reject_report(
    db: AsyncSession,
    report_id: uuid.UUID,
    request: ReviewActionRequest,
) -> tuple[Report, ReportReviewEvent]:
    return await _apply_review_action(db, report_id, "reject", request)


async def needs_revision(
    db: AsyncSession,
    report_id: uuid.UUID,
    request: ReviewActionRequest,
) -> tuple[Report, ReportReviewEvent]:
    return await _apply_review_action(db, report_id, "needs_revision", request)


async def get_review_events(
    db: AsyncSession, report_id: uuid.UUID
) -> list[ReportReviewEvent]:
    await _get_report_or_404(db, report_id)
    result = await db.execute(
        select(ReportReviewEvent)
        .where(ReportReviewEvent.report_id == report_id)
        .order_by(ReportReviewEvent.created_at.asc())
    )
    return list(result.scalars().all())
