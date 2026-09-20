"""Reading and writing research decisions. V3.17.3.

The predicate in :mod:`.predicates` is pure and cannot reach a database. This module is
the half that can, kept separate so the decision logic stays testable without one.

Nothing here decides anything. It gathers facts, writes rows, and moves a decision through
the state machine; every "should we?" belongs to the predicate.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research_decision import (
    OPEN_STATUSES,
    STATUS_QUEUED,
    STATUS_RESEARCH_REQUIRED,
    ResearchDecision,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def open_decision_for_company(
    session: AsyncSession, company_id: uuid.UUID
) -> ResearchDecision | None:
    """The live decision for this company, if there is one.

    The database also enforces "at most one" through ``ux_research_decisions_one_open``.
    This read exists so the controller can refuse *with a reason a human can read*
    instead of catching an IntegrityError and having to guess which constraint fired.
    """
    stmt = select(ResearchDecision).where(
        ResearchDecision.company_id == company_id,
        ResearchDecision.status.in_(OPEN_STATUSES),
    )
    return (await session.execute(stmt)).scalars().first()


async def seconds_since_last_completed_research(
    session: AsyncSession, company_id: uuid.UUID
) -> float | None:
    """How long since this company's last finished research job, or ``None``.

    ``None`` means never, which serves no cooldown — a different answer from "long ago",
    and the predicate treats them differently.

    Reads ``research_jobs`` rather than ``research_decisions`` on purpose: research that a
    human started by hand still means the corpus was just refreshed, and a cooldown that
    only counted escalation's own runs would re-research a company somebody looked at
    thirty seconds ago.
    """
    from app.models.research_job import ResearchJob

    stmt = select(func.max(ResearchJob.finished_at)).where(
        ResearchJob.company_id == company_id,
        ResearchJob.status == "completed",
    )
    last = (await session.execute(stmt)).scalar_one_or_none()
    if last is None:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return max(0.0, (_utcnow() - last).total_seconds())


async def decisions_created_for_run(
    session: AsyncSession, discovery_run_id: uuid.UUID
) -> int:
    """How many decisions this discovery run has already produced.

    Counts every decision including terminal ones: the fan-out cap is a bound on what one
    council may *start*, and a decision that has since finished still consumed a slot.
    """
    stmt = select(func.count()).where(
        ResearchDecision.discovery_run_id == discovery_run_id
    )
    return int((await session.execute(stmt)).scalar_one() or 0)


class MissingEvidenceBaseline(RuntimeError):
    """A decision that could execute research was about to exist without a baseline.

    Raised rather than logged, and raised at the two places where the invariant can
    actually be broken (:func:`create_decision` and ``controller._enqueue``), because the
    failure this guards is silent by nature: the round runs perfectly, the delta is
    computed against nothing, and the answer looks like a very successful round.
    """


async def create_decision(
    session: AsyncSession,
    *,
    company_id: uuid.UUID | None,
    discovery_run_id: uuid.UUID | None,
    discovery_candidate_id: uuid.UUID | None,
    source: str,
    decision: str,
    reason: str,
    max_rounds: int,
    evidence_before: dict[str, int] | None,
    escalation_round: int = 0,
    priority: int = 100,
) -> ResearchDecision:
    """Write the decision, **with the baseline its first round will be measured against.**

    Two states rather than one because they can fail independently: the decision is the
    platform's own record that it intends to research, and the job is the queue's record
    that it will. A decision written and never queued is visible and fixable; a job
    queued with no decision behind it is work nobody can explain.

    WHY ``evidence_before`` IS A REQUIRED ARGUMENT AND NOT AN OPTIONAL ONE
    ---------------------------------------------------------------------
    V3.17.1–7 wrote ``evidence_before_json`` at exactly one place —
    ``controller.complete_round``, for the *next* round — so every round 0 in production
    was measured against ``EvidenceSnapshot.from_dict(None)``, all zeros. A company
    holding 383 indexed chunks and 32 open gaps before research began recorded that
    entire pre-existing corpus as the round's own acquisition, and the arithmetic tell
    reached the database as ``closable_gaps_closed: -14``.

    A default would let that return the moment somebody adds a caller and does not think
    about it. Passing it is therefore **mandatory**, and passing ``None`` for a decision
    that names a company is an error rather than a permission: the only decision allowed
    to have no baseline is one with no company, which can never execute research at all.
    """
    if company_id is not None and not evidence_before:
        raise MissingEvidenceBaseline(
            "A research decision for a company must carry the evidence snapshot its "
            "first round will be measured against. Snapshot the company's evidence "
            "with `snapshot_evidence` BEFORE creating the decision — a round measured "
            "against a missing baseline reports pre-existing evidence as new "
            "acquisition."
        )

    row = ResearchDecision(
        id=uuid.uuid4(),
        company_id=company_id,
        discovery_run_id=discovery_run_id,
        discovery_candidate_id=discovery_candidate_id,
        source=source,
        decision=decision,
        status=STATUS_RESEARCH_REQUIRED,
        reason=reason,
        priority=priority,
        escalation_round=escalation_round,
        max_rounds=max_rounds,
        # Persisted in the same flush as the row itself, and therefore long before any
        # job for it can exist: `controller._enqueue` refuses a decision without one.
        evidence_before_json=dict(evidence_before) if evidence_before else None,
    )
    session.add(row)
    await session.flush()
    return row


async def mark_queued(
    session: AsyncSession, decision: ResearchDecision, *, job_id: uuid.UUID | None
) -> None:
    decision.status = STATUS_QUEUED
    decision.last_job_id = job_id
    decision.updated_at = _utcnow()
    await session.flush()


async def mark_terminal(
    session: AsyncSession,
    decision: ResearchDecision,
    *,
    status: str,
    terminal_reason: str,
    improvement: dict[str, Any] | None = None,
) -> None:
    """Close a decision, always with a reason.

    ``terminal_reason`` is required rather than optional: a decision that stopped and
    cannot say why is the silent absence this table exists to prevent.
    """
    decision.status = status
    decision.terminal_reason = terminal_reason
    if improvement is not None:
        decision.improvement_json = improvement
    decision.updated_at = _utcnow()
    await session.flush()


__all__ = [
    "MissingEvidenceBaseline",
    "create_decision",
    "decisions_created_for_run",
    "mark_queued",
    "mark_terminal",
    "open_decision_for_company",
    "seconds_since_last_completed_research",
]
