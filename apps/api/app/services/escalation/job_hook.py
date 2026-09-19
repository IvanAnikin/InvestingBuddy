"""Close the escalation loop: a terminal job advances its research decision. V3.17.7.

WHY THIS MODULE EXISTS
======================
``controller.complete_round`` measures what a round acquired, decides stop-or-continue,
and records both. It was implemented, reviewed and tested in V3.17.4 — and until V3.17.7
**nothing in production ever called it**. The tests called it directly, so the suite was
green while the live loop was open at exactly one joint:

    decision created -> job enqueued -> worker runs it -> job completes -> (nothing)

The observable consequence in production was a decision whose job had reached
``completed_with_warnings`` while the decision itself still read ``queued``, round 0, with
``evidence_before``, ``evidence_after``, ``improvement`` and ``terminal_reason`` all NULL
and ``updated_at`` still equal to its creation timestamp. Worse than a stall: because
``ux_research_decisions_one_open`` forbids a second open decision for a company, the
company was permanently locked out of all future escalation by a round that had in fact
finished successfully.

This module is that missing call, and nothing else.

WHY IT IS AN OBSERVER RATHER THAN A CALL INSIDE THE HANDLER
===========================================================
``run_company_research_job`` only sees its own success — it raises on failure and the
worker classifies it. But ``complete_round`` must be told the difference between
"research completed and found nothing" and "research did not complete", and a handler
cannot report its own failure classification. The worker knows both, so the worker owns
the seam and the domain registers against it.

WHY A FAILED ROUND IS STILL REPORTED
====================================
A job that dead-letters must still move its decision, or the lockout above happens for a
different reason. ``decide_next_state`` asks "did research actually happen?" before "did
it find anything?", so a failed round can never be recorded as
``exhausted_no_improvement`` — a provider outage must not read as a finished investigation.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.core.structured_logging import log_event
from app.services.jobs.worker import register_sweep_hook, register_terminal_observer

logger = logging.getLogger(__name__)


def _session_factory():  # noqa: ANN202 - imported late; see company_research_job
    from app.db.session import async_session_factory

    return async_session_factory


@register_terminal_observer
async def advance_research_decision(job: Any, *, completed: bool, dead_lettered: bool) -> None:
    """Advance the decision this job was executing, if it was executing one.

    Most jobs are ordinary company research with no decision attached; those cost one
    indexed lookup that finds nothing. A decision is matched by ``last_job_id`` and only
    while it is still OPEN, so a decision already moved by a concurrent worker — or by a
    replayed notification — is left exactly as it is.
    """
    from sqlalchemy import select

    from app.models.research_decision import OPEN_STATUSES, ResearchDecision
    from app.services.escalation.controller import complete_round

    try:
        job_id = uuid.UUID(str(job.id))
    except (TypeError, ValueError):
        return

    factory = _session_factory()
    async with factory() as session:
        decision = (
            (
                await session.execute(
                    select(ResearchDecision)
                    .where(ResearchDecision.last_job_id == job_id)
                    .where(ResearchDecision.status.in_(OPEN_STATUSES))
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if decision is None:
            return

        verdict = await complete_round(
            session,
            decision,
            job_completed=completed,
            job_dead_lettered=dead_lettered,
        )
        await session.commit()

    log_event(
        logger,
        "v3_escalation_round_closed",
        job_id=str(job_id),
        decision_id=str(decision.id),
        escalation_round=int(decision.escalation_round),
        terminal=bool(verdict.is_terminal),
        status=verdict.status,
        terminal_reason=verdict.terminal_reason or "",
    )


#: How many stranded decisions one sweep will repair. Bounded so a backlog is drained
#: over several passes instead of one unbounded query, and so a pathological table can
#: never turn the repair path into the outage.
RECONCILE_LIMIT = 50


@register_sweep_hook
async def reconcile_missed_rounds() -> None:
    """Repair every way an open decision can be left with nothing to advance it.

    The terminal observer above is a notification and notifications can be lost — it
    runs after its own commit, in its own session, and ``notify_terminal`` swallows
    whatever it raises so that a listener can never undo a finished job. A single
    dropped connection there would otherwise leave the decision OPEN for ever and
    ``ux_research_decisions_one_open`` would lock that company out permanently.

    This asks the database the same question instead of trusting a callback, so it also
    heals decisions stranded BEFORE this code existed. Runs at worker startup and on a
    slow cadence thereafter; bounded, idempotent, and safe to run from several workers at
    once because the rows are taken ``FOR UPDATE ... SKIP LOCKED``.
    """
    from app.services.escalation.controller import (
        reconcile_terminal_decisions,
        recover_stranded_decisions,
    )

    factory = _session_factory()
    async with factory() as session:
        # Two different strandings, both of which hold the company's lock for ever:
        #
        #   last_job_id IS NULL      the decision was written and the crash came before
        #                            its job was -> `recover_stranded_decisions` enqueues
        #                            the missing job.
        #   job already TERMINAL     the job ran and finished, but the notification that
        #                            should have closed the round was lost ->
        #                            `reconcile_terminal_decisions` closes it.
        #
        # Recovery runs FIRST: it gives a decision the job it never had, and the round
        # that job eventually finishes is then closed by a later pass. Doing it the other
        # way round would still work, one sweep later, but this ordering means a single
        # sweep leaves nothing obviously half-repaired.
        recovered = await recover_stranded_decisions(session, limit=RECONCILE_LIMIT)
        repaired = await reconcile_terminal_decisions(session, limit=RECONCILE_LIMIT)
        if recovered or repaired:
            await session.commit()

    if recovered:
        log_event(
            logger,
            "v3_escalation_recovered",
            count=len(recovered),
            decision_ids=",".join(str(d) for d in recovered),
        )
    if repaired:
        log_event(
            logger,
            "v3_escalation_reconciled",
            count=len(repaired),
            decision_ids=",".join(str(d) for d in repaired),
        )
