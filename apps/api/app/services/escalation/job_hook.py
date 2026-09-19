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
from app.services.jobs.worker import register_terminal_observer

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
