"""The research-escalation queue, readable. V3.17.5.

ADMIN/INTERNAL ONLY. These endpoints exist so a human can see what the autonomous
research loop is doing — which decision was taken, why, what round it is on, what the
last round actually acquired, and why it stopped.

They are deliberately read-only apart from ``cancel``. Escalation is started by
``POST /market-discovery/runs/{id}/escalate`` and advanced by the worker; a second way to
create work would be a second source of truth.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.company import Company
from app.models.research_decision import (
    OPEN_STATUSES,
    STATUS_ABANDONED,
    TERMINAL_OPERATOR_CANCELLED,
    ResearchDecision,
)
from app.schemas.research_decision import (
    EvidenceDeltaRead,
    ResearchDecisionList,
    ResearchDecisionRead,
)

router = APIRouter(prefix="/research-decisions", tags=["research-decisions"])

#: Raised by PostgreSQL when `research_decisions` does not exist yet.
_UNDEFINED_TABLE = "UndefinedTable"


def _schema_missing(exc: Exception) -> bool:
    """Is this "migration 040 has not been applied here" rather than a real failure?

    Migrations are deliberately manual in this platform, so a deploy can legitimately
    carry code whose table does not exist yet. That is a KNOWN, NAMEABLE state — not an
    internal error, and emphatically not an empty list, which would claim there are no
    research decisions when the truth is that the feature's table is absent.
    """
    seen = {type(exc).__name__}
    cause = exc.__cause__ or exc.__context__
    while cause is not None and len(seen) < 8:
        seen.add(type(cause).__name__)
        cause = cause.__cause__ or cause.__context__
    return _UNDEFINED_TABLE in seen or "no such table" in str(exc).lower()


def _schema_missing_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "The research-escalation tables are not present in this environment. "
            "Migration 040 has not been applied here. This is a schema state, not a "
            "failure, and it is reported rather than answered with an empty list — "
            "there being no table is a different fact from there being no decisions."
        ),
    )

_INTERNAL = (
    "INTERNAL ADMIN ONLY. Not investment advice, not a recommendation, no price "
    "target or valuation."
)


async def _to_read(
    session: AsyncSession, row: ResearchDecision
) -> ResearchDecisionRead:
    """Project one decision, joining only what a reader needs to identify it.

    The job's own state is read from ``research_jobs`` rather than mirrored onto the
    decision, because a mirrored status is a second copy that goes stale — the queue is
    the authority on what the queue is doing.
    """
    company = (
        await session.get(Company, row.company_id) if row.company_id else None
    )

    job_status = job_attempt = job_max = None
    if row.last_job_id is not None:
        from app.models.research_job import ResearchJob

        job = await session.get(ResearchJob, row.last_job_id)
        if job is not None:
            job_status = job.status
            job_attempt = job.attempt
            job_max = job.max_attempts

    improvement = None
    if row.improvement_json:
        improvement = EvidenceDeltaRead(
            **{
                k: v
                for k, v in row.improvement_json.items()
                if k in EvidenceDeltaRead.model_fields
            }
        )

    return ResearchDecisionRead(
        id=row.id,
        company_id=row.company_id,
        ticker=getattr(company, "ticker", None),
        exchange=getattr(company, "exchange", None),
        company_name=getattr(company, "name", None),
        discovery_run_id=row.discovery_run_id,
        discovery_candidate_id=row.discovery_candidate_id,
        source=row.source,
        decision=row.decision,
        status=row.status,
        reason=row.reason,
        priority=row.priority,
        escalation_round=row.escalation_round,
        max_rounds=row.max_rounds,
        last_job_id=row.last_job_id,
        job_status=job_status,
        job_attempt=job_attempt,
        job_max_attempts=job_max,
        evidence_before=row.evidence_before_json,
        evidence_after=row.evidence_after_json,
        improvement=improvement,
        terminal_reason=row.terminal_reason,
        # Passed through as None when unknown. NEVER defaulted to 0.0.
        cost_usd_total=(
            float(row.cost_usd_total) if row.cost_usd_total is not None else None
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "",
    response_model=ResearchDecisionList,
    summary="List research decisions (admin only)",
    description=(
        "The research-escalation queue. Newest first. Filter by status, by "
        "`open=true` for decisions still in flight, or by company. " + _INTERNAL
    ),
)
async def list_research_decisions(
    status_filter: str | None = Query(None, alias="status"),
    open_only: bool = Query(False, alias="open"),
    company_id: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> ResearchDecisionList:
    stmt = select(ResearchDecision)
    count_stmt = select(func.count(ResearchDecision.id))
    if status_filter:
        stmt = stmt.where(ResearchDecision.status == status_filter)
        count_stmt = count_stmt.where(ResearchDecision.status == status_filter)
    if open_only:
        stmt = stmt.where(ResearchDecision.status.in_(OPEN_STATUSES))
        count_stmt = count_stmt.where(ResearchDecision.status.in_(OPEN_STATUSES))
    if company_id:
        stmt = stmt.where(ResearchDecision.company_id == company_id)
        count_stmt = count_stmt.where(ResearchDecision.company_id == company_id)

    stmt = stmt.order_by(ResearchDecision.created_at.desc()).limit(limit)
    try:
        rows = (await db.execute(stmt)).scalars().all()
        total = int((await db.execute(count_stmt)).scalar_one() or 0)
        decisions = [await _to_read(db, r) for r in rows]
    except Exception as exc:  # noqa: BLE001 - re-raised unless it is the known state
        if _schema_missing(exc):
            raise _schema_missing_error() from exc
        raise

    return ResearchDecisionList(decisions=decisions, total=total)


@router.get(
    "/{decision_id}",
    response_model=ResearchDecisionRead,
    summary="One research decision with its evidence delta (admin only)",
    description="The decision, its current job, and what the last round changed. "
    + _INTERNAL,
)
async def get_research_decision(
    decision_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ResearchDecisionRead:
    try:
        row = await db.get(ResearchDecision, decision_id)
    except Exception as exc:  # noqa: BLE001 - re-raised unless it is the known state
        if _schema_missing(exc):
            raise _schema_missing_error() from exc
        raise
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Research decision {decision_id} not found",
        )
    return await _to_read(db, row)


@router.post(
    "/{decision_id}/cancel",
    response_model=ResearchDecisionRead,
    summary="Stop a research decision (admin only)",
    description=(
        "Operator stop. Moves an open decision to `abandoned` with "
        "`operator_cancelled`, which frees the company for a future decision. A "
        "decision that has already reached a terminal state is returned unchanged — "
        "reopening a closed record would rewrite history. " + _INTERNAL
    ),
)
async def cancel_research_decision(
    decision_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ResearchDecisionRead:
    from app.services.escalation import store

    try:
        row = await db.get(ResearchDecision, decision_id)
    except Exception as exc:  # noqa: BLE001 - re-raised unless it is the known state
        if _schema_missing(exc):
            raise _schema_missing_error() from exc
        raise
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Research decision {decision_id} not found",
        )
    if row.status in OPEN_STATUSES:
        await store.mark_terminal(
            db,
            row,
            status=STATUS_ABANDONED,
            terminal_reason=TERMINAL_OPERATOR_CANCELLED,
        )
        # The queued job is asked to stop too. Cooperative — nothing is killed.
        if row.last_job_id is not None:
            from app.services.jobs.job_store import JobStore

            await JobStore().request_cancel(row.last_job_id)
        await db.commit()
    return await _to_read(db, row)
