"""The controller — the thing that was missing. V3.17.3.

WHAT THIS CLOSES
================
The discovery council has been emitting ``research_next`` per candidate for several
phases and **nothing has ever read it**. V3.17.1 gave the decision a table; V3.17.2 gave
it a predicate; this module is the part that turns a decision into work, which is the
part that never existed.

The test that matters for this slice is therefore not "a decision row was written". It is
*"a decision causes a ``research_jobs`` row to exist"* — the design says so explicitly,
because a test asserting the decision exists would pass today, against code that connects
to nothing.

THE ORDER OF OPERATIONS IS LOAD-BEARING
=======================================
decision row → job row → decision marked queued.

Not job-first. A job with no decision behind it is work nobody can explain, and it would
already be claimable by a worker before the record of *why* exists. Decision-first means
the worst case is a decision in ``research_required`` that was never queued — visible,
queryable, and fixable — rather than paid work with no audit trail.

WHY IT REFUSES WHEN THE WORKER IS OFF
=====================================
``research_jobs`` rows are only executed by ``worker.run_worker``, which
``V3_DURABLE_JOBS_ENABLED`` gates. That flag is **absent in production**.

Creating decisions anyway would be worse than doing nothing: a decision enters an OPEN
state, ``ux_research_decisions_one_open`` then forbids any further decision for that
company, and with no worker to advance it the company is locked out **permanently** by a
feature that is not even switched on. So the controller refuses up front and says which
flag is missing.

That refusal is not a policy decision about whether to turn the worker on. It is the
reason a caller gets an explanation instead of a silent permanent lock.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research_decision import (
    DECISION_RESEARCH_NEXT,
    SOURCE_DISCOVERY_COUNCIL,
)
from app.services.escalation import store
from app.services.escalation.predicates import (
    EscalationInputs,
    EscalationVerdict,
    evaluate,
)

logger = logging.getLogger(__name__)

#: Refusals that belong to the controller rather than to a candidate's own facts. The
#: per-candidate ones live in `predicates.REFUSAL_CODES`.
REFUSED_ESCALATION_DISABLED = "escalation_disabled"
REFUSED_DURABLE_JOBS_DISABLED = "durable_jobs_disabled"
REFUSED_NO_COMPANY = "candidate_has_no_company"


@dataclass
class CandidateFacts:
    """One candidate, reduced to what the decision rests on.

    Assembled by the caller from the discovery candidate and the council's coerced
    action, so the controller never has to know the shape of a council envelope.
    """

    candidate_id: uuid.UUID | None
    company_id: uuid.UUID | None
    coerced_action: str | None
    blocking_gap_count: int | None = None
    confidence: float | None = None


@dataclass
class EscalationOutcome:
    """What the controller did, in enough detail to explain it without a second query."""

    created: list[uuid.UUID] = field(default_factory=list)
    refusals: list[dict[str, Any]] = field(default_factory=list)
    #: A controller-level refusal that stopped the whole call before any candidate was
    #: considered. ``None`` when the run was evaluated normally.
    blocked_reason: str | None = None
    blocked_detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": [str(i) for i in self.created],
            "created_count": len(self.created),
            "refusals": list(self.refusals),
            "refused_count": len(self.refusals),
            "blocked_reason": self.blocked_reason,
            "blocked_detail": self.blocked_detail,
        }


def _flags() -> tuple[bool, bool, int, int, float | None]:
    """Read at call time, never captured at import.

    A module-level boolean is how a feature flag quietly becomes permanent — the same
    reason ``company_research_job.durable_enabled()`` reads it fresh.
    """
    from app.core.config import settings

    cap = float(getattr(settings, "v3_escalation_cost_cap_usd", 0.0) or 0.0)
    return (
        bool(getattr(settings, "v3_research_escalation_enabled", False)),
        bool(getattr(settings, "v3_durable_jobs_enabled", False)),
        int(getattr(settings, "v3_escalation_max_rounds", 2)),
        int(getattr(settings, "v3_escalation_max_decisions_per_run", 5)),
        cap if cap > 0 else None,
    )


async def escalate_discovery_run(
    session: AsyncSession,
    *,
    discovery_run_id: uuid.UUID,
    candidates: "list[CandidateFacts]",
    enqueue: Any = None,
) -> EscalationOutcome:
    """Evaluate every candidate of a reviewed discovery run, and queue what qualifies.

    ``enqueue`` is injected so the consumer test can assert the job row without running a
    worker; it defaults to the real durable submit path.
    """
    outcome = EscalationOutcome()
    escalation_on, durable_on, max_rounds, max_per_run, cost_cap = _flags()

    if not escalation_on:
        outcome.blocked_reason = REFUSED_ESCALATION_DISABLED
        outcome.blocked_detail = (
            "V3_RESEARCH_ESCALATION_ENABLED is not set, so no research decision was "
            "created. Escalation starts paid research without a human clicking, and it "
            "is off until deliberately enabled."
        )
        return outcome

    if not durable_on:
        # See the module docstring: creating decisions here would lock every affected
        # company out of future decisions, permanently, with nothing able to advance them.
        outcome.blocked_reason = REFUSED_DURABLE_JOBS_DISABLED
        outcome.blocked_detail = (
            "V3_DURABLE_JOBS_ENABLED is not set, so nothing would execute a queued job. "
            "No decision was created: an open decision with no worker to advance it "
            "would block this company from every future decision, permanently."
        )
        return outcome

    created_so_far = await store.decisions_created_for_run(session, discovery_run_id)

    for candidate in candidates:
        if candidate.company_id is None:
            # A candidate that was never promoted to a Company has nothing to research.
            outcome.refusals.append(
                {
                    "candidate_id": str(candidate.candidate_id),
                    "clause": REFUSED_NO_COMPANY,
                    "reason": (
                        "This candidate has not been promoted to a company, so there is "
                        "nothing to research."
                    ),
                }
            )
            continue

        existing = await store.open_decision_for_company(session, candidate.company_id)
        elapsed = await store.seconds_since_last_completed_research(
            session, candidate.company_id
        )

        verdict: EscalationVerdict = evaluate(
            EscalationInputs(
                coerced_action=candidate.coerced_action,
                blocking_gap_count=candidate.blocking_gap_count,
                confidence=candidate.confidence,
                has_open_decision=existing is not None,
                seconds_since_last_completed_research=elapsed,
                escalation_round=0,
                max_rounds=max_rounds,
                # Round 0: nothing has been spent on a decision that does not exist yet.
                # A known zero, not an unknown — see `_clause_budget`.
                cost_usd_so_far=0.0,
                cost_cap_usd=cost_cap,
                decisions_created_this_run=created_so_far + len(outcome.created),
                max_decisions_per_run=max_per_run,
            )
        )

        if not verdict.should_create:
            outcome.refusals.append(
                {
                    "candidate_id": str(candidate.candidate_id),
                    "company_id": str(candidate.company_id),
                    "clause": verdict.refused_clause,
                    "reason": verdict.reason,
                }
            )
            continue

        decision = await store.create_decision(
            session,
            company_id=candidate.company_id,
            discovery_run_id=discovery_run_id,
            discovery_candidate_id=candidate.candidate_id,
            source=SOURCE_DISCOVERY_COUNCIL,
            decision=DECISION_RESEARCH_NEXT,
            reason=verdict.reason,
            max_rounds=max_rounds,
        )

        job_id = await _enqueue(session, decision, enqueue=enqueue)
        await store.mark_queued(session, decision, job_id=job_id)
        outcome.created.append(decision.id)

        logger.info(
            "v3_escalation_decision_created",
            extra={
                "decision_id": str(decision.id),
                "company_id": str(candidate.company_id),
                "discovery_run_id": str(discovery_run_id),
                "job_id": str(job_id) if job_id else None,
            },
        )

    return outcome


async def _enqueue(
    session: AsyncSession, decision: Any, *, enqueue: Any = None
) -> uuid.UUID | None:
    """Put the decision on the existing queue. No second queue is built here.

    ``idempotency_key`` is the **decision id**, which is what makes the link a fact rather
    than a convention: the same decision can never produce two jobs, and the job can
    always be traced back to the decision that ordered it.
    """
    if enqueue is not None:
        return await enqueue(session, decision)

    from app.services.jobs.job_store import JobStore

    company = await session.get(_company_model(), decision.company_id)
    view, _created = await JobStore().enqueue(
        job_type="company_research",
        idempotency_key=f"escalation:{decision.id}",
        company_id=decision.company_id,
        payload={
            "company_id": str(decision.company_id),
            "use_llm": True,
            # The escalation context, so the run knows it is a round rather than a
            # first look.
            "escalation": {
                "decision_id": str(decision.id),
                "round": int(decision.escalation_round),
                "max_rounds": int(decision.max_rounds),
            },
            "company": {
                "id": str(decision.company_id),
                "ticker": getattr(company, "ticker", None),
                "exchange": getattr(company, "exchange", None),
                "name": getattr(company, "name", None),
            },
        },
    )
    return getattr(view, "id", None)


def _company_model():  # noqa: ANN202
    from app.models.company import Company

    return Company


__all__ = [
    "REFUSED_DURABLE_JOBS_DISABLED",
    "REFUSED_ESCALATION_DISABLED",
    "REFUSED_NO_COMPANY",
    "CandidateFacts",
    "EscalationOutcome",
    "escalate_discovery_run",
]
