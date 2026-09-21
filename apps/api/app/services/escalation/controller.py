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
**evidence snapshot → decision row (carrying it) → job row → decision marked queued.**

The snapshot comes first (V3.17.8). A job is the only thing that can change a company's
evidence, so measuring before the job exists is the only way to be certain the
measurement describes the world *before* the round. Measuring afterwards — or, as
V3.17.1–7 did, not at all — makes every round 0 compare against zero and report the
corpus the platform already held as its own acquisition.

The rest is unchanged, and deliberately not job-first: a job with no decision behind it
is work nobody can explain, and it would already be claimable by a worker before the
record of *why* exists. Decision-first means the worst case is a decision in
``research_required`` that was never queued — visible, queryable, and fixable — rather
than paid work with no audit trail.

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
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research_decision import (
    DECISION_RESEARCH_NEXT,
    OPEN_STATUSES,
    SOURCE_DISCOVERY_COUNCIL,
    STATUS_ABANDONED,
    STATUS_RESEARCH_REQUIRED,
    TERMINAL_EVIDENCE_BASELINE_MISSING,
    TERMINAL_RESEARCH_DID_NOT_COMPLETE,
)
from app.models.research_decision import ResearchDecision as ResearchDecisionModel
from app.services.escalation import store
from app.services.escalation.evidence import snapshot_evidence
from app.services.escalation.predicates import (
    EscalationInputs,
    EscalationVerdict,
    evaluate,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.services.escalation.rounds import RoundVerdict

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

        # THE BASELINE, TAKEN BEFORE ANYTHING CAN MOVE IT. Not after the decision, not
        # after the enqueue: at this point no job for this decision exists, so nothing
        # is able to add a chunk, a fact or a document on its behalf. `create_decision`
        # refuses a company decision without one, and `_enqueue` refuses to queue one.
        baseline = await snapshot_evidence(session, candidate.company_id)

        decision = await store.create_decision(
            session,
            company_id=candidate.company_id,
            discovery_run_id=discovery_run_id,
            discovery_candidate_id=candidate.candidate_id,
            source=SOURCE_DISCOVERY_COUNCIL,
            decision=DECISION_RESEARCH_NEXT,
            reason=verdict.reason,
            max_rounds=max_rounds,
            evidence_before=baseline.to_dict(),
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


def _refuse_without_baseline(decision: Any, *, round_index: int) -> None:
    """No paid round may become executable without something to measure it against.

    A decision with no company cannot execute research at all — it is closed by
    :func:`recover_stranded_decisions` rather than queued — so it is exempt, and that
    exemption is narrow on purpose.
    """
    if getattr(decision, "company_id", None) is None:
        return
    if not getattr(decision, "evidence_before_json", None):
        raise store.MissingEvidenceBaseline(
            f"Refusing to enqueue round {round_index} for research decision "
            f"{getattr(decision, 'id', None)}: it carries no evidence baseline, so what "
            "the round acquired could not be measured afterwards and pre-existing "
            "evidence would be counted as new acquisition."
        )


async def _enqueue(
    session: AsyncSession, decision: Any, *, enqueue: Any = None, round_index: int = 0
) -> uuid.UUID | None:
    """Put the decision on the existing queue. No second queue is built here.

    ``idempotency_key`` is the **decision id**, which is what makes the link a fact rather
    than a convention: the same decision can never produce two jobs, and the job can
    always be traced back to the decision that ordered it.

    THE CHOKE POINT FOR THE BASELINE INVARIANT (V3.17.8)
    ----------------------------------------------------
    Every path that can make escalation work executable goes through here: the discovery
    council's escalation, the next round authorised by :func:`complete_round`, and
    :func:`recover_stranded_decisions`. So the check lives here rather than in each of
    them — a future producer gets the guarantee without having to know it exists, which
    is the only version of this that survives the next slice.
    """
    _refuse_without_baseline(decision, round_index=round_index)

    if enqueue is not None:
        return await enqueue(session, decision)

    from app.services.jobs.job_store import JobStore

    company = await session.get(_company_model(), decision.company_id)
    view, _created = await JobStore().enqueue(
        job_type="company_research",
        idempotency_key=round_idempotency_key(decision.id, round_index),
        company_id=decision.company_id,
        payload={
            "company_id": str(decision.company_id),
            "use_llm": True,
            # V3.18.8 — the thesis that caused this escalation travels with every round.
            # It never did: the payload carried no discovery ids, so escalated research
            # could not know why it was running.
            "discovery_candidate_id": (
                str(decision.discovery_candidate_id)
                if getattr(decision, "discovery_candidate_id", None)
                else None
            ),
            # The escalation context, so the run knows it is a round rather than a
            # first look.
            "escalation": {
                "decision_id": str(decision.id),
                "round": round_index,
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
    "complete_round",
    "recover_stranded_decisions",
    "reconcile_terminal_decisions",
    "round_idempotency_key",
    "REFUSED_ESCALATION_DISABLED",
    "REFUSED_NO_COMPANY",
    "CandidateFacts",
    "EscalationOutcome",
    "escalate_discovery_run",
]


# --------------------------------------------------------------------------- #
# V3.17.4 — what happens after a round
# --------------------------------------------------------------------------- #


def round_idempotency_key(decision_id: uuid.UUID, escalation_round: int) -> str:
    """The job key for one decision's one round.

    DETERMINISTIC, and that is what makes the whole thing crash-safe. Because the key can
    be recomputed from the decision alone, enqueueing "again" after a process died is not
    a duplicate — the database recognises it as the same logical work and joins it. This
    is the property :func:`recover_stranded_decisions` depends on.
    """
    return f"escalation:{decision_id}:round:{escalation_round}"


async def complete_round(
    session: AsyncSession,
    decision: Any,
    *,
    job_completed: bool,
    job_dead_lettered: bool = False,
    enqueue: Any = None,
) -> "RoundVerdict":
    """Measure what the round acquired, decide what happens next, and record both.

    ORDER OF OPERATIONS, AND WHY
    ----------------------------
    The evidence is measured BEFORE anything is written, so the snapshot describes the
    world at the moment the round ended rather than after our own bookkeeping.

    When another round is authorised, the job is enqueued **before** the decision moves
    to ``reanalysis``. A crash between the two leaves a decision in
    ``evidence_updated`` with a live job — recoverable. The opposite order would leave a
    decision in ``reanalysis`` with nothing to execute, and because
    ``ux_research_decisions_one_open`` forbids a second open decision for that company,
    the company would be locked out permanently. See
    :func:`recover_stranded_decisions`.

    THE NEXT ROUND'S BASELINE IS WRITTEN BEFORE ITS JOB EXISTS (V3.17.8)
    -------------------------------------------------------------------
    The ``after`` snapshot of this round is the ``before`` snapshot of the next — that
    was already true, and is preserved. What changes is that it is flushed *before* the
    next round's job is enqueued rather than after, so the same rule holds for round
    *n+1* as for round 0: nothing that could move the evidence exists until the
    measurement it will be judged against is on the row. The job-before-status ordering
    above is untouched.

    A MISSING BASELINE IS NOT A BASELINE OF ZERO
    --------------------------------------------
    A decision created before V3.17.8, or by any path that somehow skipped the snapshot,
    has ``evidence_before_json`` NULL. Such a round is **unmeasurable**, and this
    function will not guess: it records the after-snapshot (a true measurement, worth
    keeping), records that no delta could be computed, and closes the decision with
    ``evidence_baseline_missing``. It does **not** fabricate a baseline from the current
    evidence — that would be indistinguishable from a round that acquired nothing, and
    would write a false zero-delta into a permanent record.
    """
    from app.services.escalation.cost import spend_for_decision
    from app.services.escalation.evidence import (
        EvidenceSnapshot,
        measure_evidence_delta,
        snapshot_evidence,
        unmeasurable_delta,
    )
    from app.services.escalation.rounds import (
        RoundInputs,
        decide_next_state,
        verdict_payload,
    )

    # `persisted`, NOT `from_dict`: the difference between a measured zero and a
    # measurement that was never taken is the entire subject of this slice.
    before = EvidenceSnapshot.persisted(decision.evidence_before_json)
    after = await snapshot_evidence(session, decision.company_id)

    if before is None:
        delta = unmeasurable_delta(
            "No pre-round evidence snapshot was recorded for this decision, so the "
            "evidence this round acquired cannot be separated from the evidence the "
            "platform already held. No delta is reported, because reporting zeros here "
            "would be indistinguishable from a round that genuinely acquired nothing."
        )
    else:
        delta = measure_evidence_delta(before, after)

    # WHAT THIS ROUND COST, RECOMPUTED FROM THE ROWS. V3.17.9.
    #
    # `cost_usd_total` had no producer at all before this line, so it was NULL on every
    # decision and the transition below could only ever reach `cost_unknown`. That read
    # as "no price book" — true, but it also concealed that the spend was UNATTRIBUTABLE,
    # which configuring prices would not have fixed.
    #
    # DERIVED, NEVER ACCUMULATED. The whole total is recomputed from
    # `research_run_consumption` every time, so closing the same round twice — the
    # terminal observer and the startup sweep can both do it — yields the same number
    # instead of twice it. `+=` here would be the double count.
    spend = await spend_for_decision(session, decision)
    decision.cost_usd_total = spend.cost_usd_total

    _escalation_on, _durable_on, max_rounds, _per_run, cost_cap = _flags()
    verdict = decide_next_state(
        RoundInputs(
            job_completed=job_completed,
            job_dead_lettered=job_dead_lettered,
            improved=bool(delta["improved"]),
            evidence_baseline_known=before is not None,
            open_closable_gaps_remain=after.open_closable_gaps > 0,
            escalation_round=int(decision.escalation_round),
            max_rounds=int(decision.max_rounds or max_rounds),
            # Unknown stays unknown. Never coerced to 0 — see `_clause_budget`. With no
            # price book configured this is legitimately None, and the loop stops on
            # `cost_unknown` rather than spending against a number nobody has.
            cost_usd_so_far=spend.cost_usd_total,
            cost_cap_usd=cost_cap,
        )
    )

    decision.evidence_after_json = after.to_dict()
    # The spend record goes in beside the verdict, not instead of it: a reader six months
    # from now has to be able to tell `cost NULL because nothing was linked` from
    # `cost NULL because nothing is priced`, and the column alone says neither.
    decision.improvement_json = {
        **verdict_payload(delta, verdict),
        "spend": spend.to_dict(),
    }
    decision.updated_at = datetime.now(timezone.utc)

    if verdict.is_terminal:
        await store.mark_terminal(
            session,
            decision,
            status=verdict.status,
            terminal_reason=verdict.terminal_reason or "",
        )
        return verdict

    # Another round. The baseline it will be measured against, flushed first; then the
    # job; then the status — see the docstring.
    next_round = int(decision.escalation_round) + 1
    decision.evidence_before_json = after.to_dict()
    await session.flush()

    job_id = await _enqueue(session, decision, enqueue=enqueue, round_index=next_round)
    decision.escalation_round = next_round
    decision.status = verdict.status
    decision.last_job_id = job_id
    await session.flush()
    return verdict


async def reconcile_terminal_decisions(
    session: AsyncSession, *, enqueue: Any = None, limit: int = 50
) -> list[uuid.UUID]:
    """Close rounds whose job already ended but whose decision was never advanced.

    WHY THIS EXISTS, AND WHY THE OBSERVER IS NOT ENOUGH
    ---------------------------------------------------
    ``jobs.worker.notify_terminal`` calls the escalation observer immediately after the
    job's terminal write commits, and it **deliberately swallows** anything the observer
    raises: a listener must never be able to undo an outcome that already happened.

    That safety has a cost. The observer runs in its own session, after its own commit,
    so a transient database error there is discarded along with the exception — and the
    job will never run again to produce a second notification. The decision then stays
    OPEN for ever, and ``ux_research_decisions_one_open`` locks that company out of every
    future decision. Exactly the outage V3.17.7 was written to end, reachable by a single
    dropped connection.

    So the notification is the FAST path and this is the DURABLE one: the database, not a
    callback, is the authority on what still needs finishing. Anything the observer
    missed — including everything it missed while the fix was not yet deployed — is
    healed the next time a worker runs this.

    WHAT IT WILL AND WILL NOT TOUCH
    -------------------------------
    * Only decisions in an OPEN status whose ``last_job_id`` names a job in
      :data:`job_contract.TERMINAL`. A retried job is returned to ``pending``, which is
      not terminal, so work that is still owed an attempt is never finalised.
    * Ordinary company research has no decision pointing at it and is invisible here.
    * A decision already closed — by the observer, or by another worker one microsecond
      ago — is skipped under the lock, not rewritten.

    CONCURRENCY
    -----------
    Rows are taken ``FOR UPDATE ... SKIP LOCKED`` so two workers reconcile disjoint sets
    rather than contending, and the OPEN re-check happens **after** the lock is held.
    ``complete_round`` is reused verbatim — its ``round_idempotency_key`` is deterministic,
    so even a next-round enqueue that somehow ran twice joins rather than duplicates.
    """
    from app.models.research_job import ResearchJob
    from app.services.jobs.job_contract import (
        HAS_RESULT,
        STATUS_DEAD_LETTER,
        TERMINAL,
    )

    stmt = (
        select(ResearchDecisionModel)
        .join(ResearchJob, ResearchJob.id == ResearchDecisionModel.last_job_id)
        .where(
            ResearchDecisionModel.status.in_(OPEN_STATUSES),
            ResearchJob.status.in_(tuple(TERMINAL)),
        )
        .order_by(ResearchDecisionModel.updated_at)
        .limit(limit)
    )
    # SQLite ignores row locking; PostgreSQL is where it matters and where it runs.
    try:
        stmt = stmt.with_for_update(of=ResearchDecisionModel, skip_locked=True)
    except Exception:  # noqa: BLE001 - dialect without row locking
        pass

    candidates = (await session.execute(stmt)).scalars().all()

    reconciled: list[uuid.UUID] = []
    for decision in candidates:
        # Re-read UNDER THE LOCK. Between the select and here, the observer for this very
        # job may have closed it; writing again would move a terminal decision.
        if decision.status not in OPEN_STATUSES:
            continue
        job = await session.get(ResearchJob, decision.last_job_id)
        if job is None or job.status not in TERMINAL:
            continue

        # HAS_RESULT is the contract's own "the work produced something" set. Naming it
        # here rather than restating {completed, completed_with_warnings} keeps this from
        # silently disagreeing with the state machine if a status is ever added.
        verdict = await complete_round(
            session,
            decision,
            job_completed=job.status in HAS_RESULT,
            job_dead_lettered=job.status == STATUS_DEAD_LETTER,
            enqueue=enqueue,
        )
        reconciled.append(decision.id)
        logger.info(
            "v3_escalation_decision_reconciled",
            extra={
                "decision_id": str(decision.id),
                "job_id": str(decision.last_job_id),
                "job_status": job.status,
                "terminal": bool(verdict.is_terminal),
                "verdict_status": verdict.status,
            },
        )
    return reconciled


async def recover_stranded_decisions(
    session: AsyncSession, *, enqueue: Any = None, limit: int = 50
) -> list[uuid.UUID]:
    """Re-enqueue work for open decisions that have none, and return what was fixed.

    WHY THIS EXISTS
    ---------------
    A decision row and a job row are written in **different transactions** — ``JobStore``
    owns its own session by design, so the two cannot be made atomic without breaking
    that boundary. Whichever order they are written in, a process that dies between them
    leaves one without the other.

    ``complete_round`` chooses the order that makes the survivable case survivable. This
    function closes the remaining one: an open decision whose job never landed would
    otherwise sit forever, and ``ux_research_decisions_one_open`` would lock that company
    out of every future decision — a permanent outage from a crash at the wrong
    microsecond.

    Safe to run repeatedly and safe to run concurrently, because
    :func:`round_idempotency_key` is deterministic: re-enqueueing a job that already
    exists joins it rather than duplicating it.

    RECOVERY MAY NOT INVENT A BASELINE (V3.17.8)
    --------------------------------------------
    Every decision created before V3.17.8 has ``evidence_before_json`` NULL, and this
    function is the one place that could quietly repair that by snapshotting the
    company's evidence now and calling it "before". It must not, in general: if the round
    has already run, "now" is *after*, and the resulting delta would report a successful
    round as having acquired nothing.

    So the snapshot is taken only where the platform can *prove* the round has not begun
    — see :func:`_round_zero_never_started`. Everywhere else the decision is closed with
    ``evidence_baseline_missing``, which frees the company (``ux_research_decisions_one_open``
    stops blocking it) without writing a measurement nobody took.
    """
    stmt = (
        select(ResearchDecisionModel)
        .where(
            ResearchDecisionModel.status.in_(OPEN_STATUSES),
            ResearchDecisionModel.last_job_id.is_(None),
        )
        .order_by(ResearchDecisionModel.updated_at)
        .limit(limit)
    )
    # V3.17.8. This function now WRITES a baseline, so two sweepers meeting the same row
    # could each take their own snapshot of it — and a snapshot taken after the other
    # sweeper's job has begun is an "after" measurement labelled "before". The same
    # `FOR UPDATE ... SKIP LOCKED` `reconcile_terminal_decisions` uses: disjoint sets,
    # no contention, and the re-check below happens with the lock held.
    # SQLite ignores row locking; PostgreSQL is where it matters and where it runs.
    try:
        stmt = stmt.with_for_update(of=ResearchDecisionModel, skip_locked=True)
    except Exception:  # noqa: BLE001 - dialect without row locking
        pass

    stranded = (await session.execute(stmt)).scalars().all()

    recovered: list[uuid.UUID] = []
    for decision in stranded:
        # Re-read UNDER THE LOCK. Between the select and here another sweeper may have
        # given this decision a job, or a baseline, or closed it.
        if decision.status not in OPEN_STATUSES or decision.last_job_id is not None:
            continue
        if decision.company_id is None:
            # Nothing to research. Close it honestly rather than leaving it open.
            await store.mark_terminal(
                session,
                decision,
                status=STATUS_ABANDONED,
                terminal_reason=TERMINAL_RESEARCH_DID_NOT_COMPLETE,
            )
            continue

        if not decision.evidence_before_json:
            if not await _round_zero_never_started(session, decision):
                # Work may already have run. Closing it is the truthful outcome: the
                # company is freed for a fresh decision that will carry a real baseline,
                # and nothing claims to know what this round did.
                await store.mark_terminal(
                    session,
                    decision,
                    status=STATUS_ABANDONED,
                    terminal_reason=TERMINAL_EVIDENCE_BASELINE_MISSING,
                )
                logger.info(
                    "v3_escalation_decision_closed_without_baseline",
                    extra={
                        "decision_id": str(decision.id),
                        "company_id": str(decision.company_id),
                        "escalation_round": int(decision.escalation_round),
                        "status_before": decision.status,
                    },
                )
                continue
            # Provably nothing has run for this decision, so the evidence as it stands
            # IS the pre-round evidence. Flushed before the enqueue below, exactly as
            # `escalate_discovery_run` orders it.
            baseline = await snapshot_evidence(session, decision.company_id)
            decision.evidence_before_json = baseline.to_dict()
            await session.flush()

        job_id = await _enqueue(
            session,
            decision,
            enqueue=enqueue,
            round_index=int(decision.escalation_round),
        )
        await store.mark_queued(session, decision, job_id=job_id)
        recovered.append(decision.id)
        logger.info(
            "v3_escalation_decision_recovered",
            extra={"decision_id": str(decision.id), "job_id": str(job_id)},
        )
    return recovered


async def _round_zero_never_started(session: AsyncSession, decision: Any) -> bool:
    """Can the platform PROVE no research has run for this decision yet?

    Three conditions, and all three are needed:

    * ``status == research_required`` — the decision has never been marked queued.
    * ``escalation_round == 0`` — a later round exists only because an earlier one ran.
    * **no ``research_jobs`` row carries this decision's round-0 idempotency key.**

    The third is the one that is not obvious. ``JobStore`` commits in its own session, so
    a job can exist while the decision that ordered it was rolled back to
    ``last_job_id IS NULL``; that job is claimable, and a worker may already have
    executed it. A baseline snapshotted after that would be an *after* snapshot wearing a
    "before" label — the exact substitution this slice removes, reintroduced by the
    repair path.

    Answering "no" costs a decision its open slot. Answering "yes" wrongly costs a false
    measurement in a permanent record, so the bias is deliberate.
    """
    if decision.status != STATUS_RESEARCH_REQUIRED:
        return False
    if int(decision.escalation_round or 0) != 0:
        return False

    from app.models.research_job import ResearchJob

    existing = (
        await session.execute(
            select(ResearchJob.id).where(
                ResearchJob.idempotency_key == round_idempotency_key(decision.id, 0)
            )
        )
    ).first()
    return existing is None
