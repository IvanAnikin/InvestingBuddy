"""Discovery on the durable job contract — W6a.

WHY THIS MODULE EXISTS
======================
A Discovery scan used to run in a FastAPI ``BackgroundTask`` inside the API
process. The run ROW was durable; the work was not. A container recycle killed
the scan mid-universe and the run sat in ``running`` until a 30-minute guess
decided it was abandoned. The product requirement is plain: *a user starts
Discovery, leaves the page, returns later and sees the same run; a reload never
cancels work.* A reload never did cancel a BackgroundTask — but a recycle did,
and nothing brought the work back.

This is the same thin adapter ``company_research_job`` is for company research.
The scan itself is still ``market_discovery_service.process_run``; this module
only changes *who owns the work*: a committed ``research_jobs`` row that a
leased worker claims, heartbeats and — after a crash — reclaims.

WHAT A RETRY MEANS HERE
=======================
``process_run(owned_by_lease=True)`` resumes rather than restarts: the run's
committed ``processed_count`` is a cursor into its persisted universe, so a retry
skips the tickers already done and never writes a second candidate for them.
The dynamic stage is already idempotent on resume (its own ``STAGE_KEY``).

HOW THE RUN AND THE JOB STAY IN AGREEMENT
=========================================
The run row keeps its own status vocabulary (no new statuses). The job decides
lifecycle; the run reports content. They are reconciled in three places:

* the handler finishes the run itself on success, and marks it ``cancelled``
  when a cancellation stops it at a ticker boundary;
* a terminal OBSERVER marks the run ``failed`` when the job dead-letters or fails
  permanently — the handler cannot report its own failure classification;
* a SWEEP repairs what no notification reaches: a job retired as abandoned on
  the claim path, a pending job cancelled outright, or a lost notification.

A lost lease writes nothing: another worker owns the job and the run now.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.structured_logging import log_event
from app.services.jobs import job_contract as contract
from app.services.jobs import lineage
from app.services.jobs.job_contract import JobView
from app.services.jobs.job_store import JobStore
from app.services.jobs.worker import (
    JobCancelled,
    JobContext,
    JobOutcome,
    register_handler,
    register_sweep_hook,
    register_terminal_observer,
)

if TYPE_CHECKING:
    from app.models.discovery import DiscoveryRun

logger = logging.getLogger(__name__)

#: The ``research_jobs.job_type`` for one Discovery scan.
JOB_TYPE = "discovery_research"

#: ``research_jobs.result_type`` of a finished scan; ``result_ref`` is the run id.
RESULT_TYPE = "discovery_run"

#: Job statuses that mean the work ended WITHOUT the handler finishing the run.
_UNFINISHED_TERMINAL = (
    contract.STATUS_FAILED,
    contract.STATUS_DEAD_LETTER,
    contract.STATUS_CANCELLED,
)

#: How far back the sweep looks for an open run, and how many open runs it reads
#: per pass. Bounded so the repair path can never become the outage.
SWEEP_LOOKBACK = timedelta(days=7)
SWEEP_LIMIT = 200


class DiscoveryRunMissing(RuntimeError):
    """The job names a run that does not exist. Retrying cannot create it."""

    job_transient = False


def durable_enabled() -> bool:
    """Whether new Discovery runs are scheduled on the durable worker.

    BOTH flags: the master durable-jobs switch (which also starts the in-process
    worker) and the Discovery-specific one. Read at call time, never captured at
    import, so a test or an operator flipping either takes effect at once.
    """
    from app.core.config import settings

    return bool(
        getattr(settings, "v3_durable_jobs_enabled", False)
        and getattr(settings, "v3_discovery_durable_enabled", False)
    )


def dedup_key(run_id: uuid.UUID | str) -> str:
    """The idempotency base key for one run. One run, one live job."""
    return f"{JOB_TYPE}:{run_id}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _session_factory() -> async_sessionmaker[AsyncSession]:
    from app.db.session import async_session_factory

    return async_session_factory


# ---------------------------------------------------------------------------
# Submission and reads
# ---------------------------------------------------------------------------


async def submit(
    run: DiscoveryRun, *, store: JobStore | None = None
) -> tuple[JobView, bool]:
    """Commit a durable job for ``run``, or join the live one.

    Returns ``(view, created)``. The row is committed before this returns, so the
    201 the endpoint sends is backed by work that survives the process.
    """
    store = store or JobStore()
    view, created = await store.enqueue(
        job_type=JOB_TYPE,
        idempotency_key=dedup_key(run.id),
        # Job INPUTS only — never a credential. Discovery is not about one
        # company, so ``company_id`` stays NULL.
        payload={"discovery_run_id": str(run.id)},
        company_id=None,
    )
    log_event(
        logger,
        "v3_discovery_research_submitted" if created else "v3_discovery_research_joined",
        job_id=view.id,
        run_id=run.id,
        attempt=view.attempt,
        status=view.status,
    )
    return view, created


async def existing_job(
    run_id: uuid.UUID | str, *, store: JobStore | None = None
) -> JobView | None:
    """The newest job for ``run_id``, or None when none exists. Never raises.

    Used by the enqueue fallback. When even this lookup fails the database is
    unreachable, so the enqueue that just failed almost certainly did not commit
    either; None (fall back to the BackgroundTask) is then the answer that keeps
    the run from sitting in ``pending`` with nothing to process it.
    """
    try:
        return await (store or JobStore()).latest_for_base_key(dedup_key(run_id))
    except Exception as exc:  # noqa: BLE001 - the caller must still decide
        logger.warning(
            "v3_discovery_job_lookup_failed run_id=%s error_type=%s",
            run_id,
            type(exc).__name__,
        )
        return None


def job_summary_from_view(view: JobView, *, now: datetime | None = None) -> dict[str, Any]:
    """The additive ``DiscoveryRunRead.job`` block. Never exposes the lease owner."""
    return {
        "job_id": view.id,
        "job_status": contract.derive_status(view, now or _utcnow()),
        "attempt": view.attempt,
        "max_attempts": view.max_attempts,
    }


async def job_summary(
    run_id: uuid.UUID | str, *, store: JobStore | None = None
) -> dict[str, Any] | None:
    """The newest durable job for ONE run, or None. Never raises.

    A read must not fail because a lineage lookup did: the run itself is the
    answer the caller asked for, and the job block is optional.
    """
    from app.core.config import settings

    if not getattr(settings, "v3_durable_jobs_enabled", False):
        return None
    try:
        view = await (store or JobStore()).latest_for_base_key(dedup_key(run_id))
    except Exception as exc:  # noqa: BLE001 - optional enrichment only
        logger.warning(
            "v3_discovery_job_lookup_failed run_id=%s error_type=%s",
            run_id,
            type(exc).__name__,
        )
        return None
    return job_summary_from_view(view) if view is not None else None


# ---------------------------------------------------------------------------
# Run reconciliation
# ---------------------------------------------------------------------------


async def _finish_run(run_id: uuid.UUID, *, status: str, warning: str) -> bool:
    """Move a NON-terminal run to ``status`` with one warning. True when written.

    Row-locked and guarded on the run still being open, so a run the handler
    already finished — or a replayed notification — is left exactly as it is.
    """
    from app.models.discovery import DiscoveryRun
    from app.services.market_discovery_service import TERMINAL_RUN_STATUSES

    async with _session_factory()() as session:
        run = (
            await session.execute(
                sa.select(DiscoveryRun)
                .where(DiscoveryRun.id == run_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return False
        now = _utcnow()
        run.status = status
        run.warnings = [*(run.warnings or []), warning][:200]
        run.completed_at = now
        run.updated_at = now
        await session.commit()
    log_event(logger, "discovery_run_reconciled", run_id=run_id, status=status)
    return True


def _run_status_for(job: JobView) -> tuple[str, str] | None:
    """What an ended-but-unfinished job means for its run, or None."""
    if job.status == contract.STATUS_DEAD_LETTER:
        attempts = job.attempt or job.max_attempts
        return (
            "failed",
            f"The research worker gave up after {attempts} attempts; the run did "
            "not finish. Candidates already listed were kept. Starting a new run "
            "is safe.",
        )
    if job.status == contract.STATUS_FAILED:
        return (
            "failed",
            "The research worker stopped on a permanent error "
            f"({job.error_class or 'unknown'}); the run was not retried.",
        )
    if job.status == contract.STATUS_CANCELLED:
        return ("cancelled", "The discovery run was cancelled before it finished.")
    return None


async def reconcile_run_with_job(job: JobView, run_id: uuid.UUID) -> bool:
    """Bring ``run_id`` in line with an ended job. True when the run moved."""
    decided = _run_status_for(job)
    if decided is None:
        return False
    status, warning = decided
    return await _finish_run(run_id, status=status, warning=warning)


def _run_id_from(payload: dict[str, Any] | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str((payload or {})["discovery_run_id"]))
    except (KeyError, TypeError, ValueError):
        return None


@register_terminal_observer
async def reconcile_discovery_run(
    job: Any, *, completed: bool, dead_lettered: bool
) -> None:
    """A Discovery job ended without finishing its run: say so on the run.

    ``completed`` jobs are left alone — the handler already wrote the run's own
    final status, which is the more specific answer.
    """
    if getattr(job, "job_type", None) != JOB_TYPE or completed:
        return
    run_id = _run_id_from(await JobStore().get_payload(job.id))
    if run_id is None:
        return
    await reconcile_run_with_job(job, run_id)


@register_sweep_hook
async def reconcile_orphaned_discovery_runs() -> None:
    """Repair every open run whose job ended without anyone telling it.

    Three ways that happens, none of which reaches the observer: a job whose
    worker was killed on every attempt is dead-lettered on the CLAIM path; a
    pending job cancelled by ``request_cancel`` never meets a worker; and an
    observer's own write can be lost. Without this, the run would read
    ``running`` for ever and the page would poll it for ever.

    Inert unless ``V3_DURABLE_JOBS_ENABLED``: this module is imported by the API
    router, so the hook is registered in every process that loads the app, and
    with durable jobs off there is no ``discovery_research`` job to reconcile —
    a worker's startup sweep must not spend a database round trip finding that out.
    """
    from app.core.config import settings

    if not getattr(settings, "v3_durable_jobs_enabled", False):
        return

    from app.models.discovery import DiscoveryRun

    # The bound applies to OPEN runs — the population that needs repair — not to
    # ended jobs: a limit over ended jobs fills up with ones already reconciled
    # and an older stranded run falls off the end for ever.
    since = _utcnow() - SWEEP_LOOKBACK
    async with _session_factory()() as session:
        open_ids = (
            (
                await session.execute(
                    sa.select(DiscoveryRun.id)
                    .where(
                        DiscoveryRun.status.in_(["pending", "running"]),
                        DiscoveryRun.created_at >= since,
                    )
                    .order_by(DiscoveryRun.created_at.desc())
                    .limit(SWEEP_LIMIT)
                )
            )
            .scalars()
            .all()
        )
    if not open_ids:
        return

    store = JobStore()
    for run_id in open_ids:
        # The LATEST generation only: an older ended job says nothing about a run
        # a newer job now owns. A run with no job (the BackgroundTask path) is
        # skipped — nothing here can speak for it.
        latest = await store.latest_for_base_key(dedup_key(run_id))
        if latest is None or latest.status not in _UNFINISHED_TERMINAL:
            continue
        await reconcile_run_with_job(latest, run_id)


# ---------------------------------------------------------------------------
# The handler
# ---------------------------------------------------------------------------


@register_handler(JOB_TYPE)
async def run_discovery_research_job(ctx: JobContext) -> JobOutcome:
    """Execute one Discovery scan under a lease.

    Raises rather than swallowing: the contract classifies the failure (a
    transient one is retried, and the retry RESUMES). Only two outcomes are
    written here — the run's own final status by ``process_run``, and
    ``cancelled`` when a cancellation stops it at a ticker boundary. A lost lease
    writes nothing.
    """
    from app.services import market_discovery_service as svc

    run_id = _run_id_from(ctx.payload)
    if run_id is None:
        raise DiscoveryRunMissing("payload carries no discovery_run_id")

    try:
        async with _session_factory()() as session:
            run = await svc.get_run(session, run_id)
            if run is None:
                raise DiscoveryRunMissing(f"discovery run {run_id} not found")
            if run.status in svc.TERMINAL_RUN_STATUSES:
                # Already finished (a replay, or the BackgroundTasks fallback got
                # there first). Nothing to do, and nothing went wrong.
                log_event(
                    logger,
                    "v3_discovery_research_noop",
                    job_id=ctx.job_id,
                    run_id=run_id,
                    run_status=run.status,
                )
                return JobOutcome(result_type=RESULT_TYPE, result_ref=run_id)
            run = await svc.process_run(
                session,
                run,
                owned_by_lease=True,
                progress=ctx.checkpoint,
                durable_job_id=lineage.coerce_job_id(ctx.job.id),
            )
            final_status = run.status
    except JobCancelled:
        await _finish_run(
            run_id,
            status="cancelled",
            warning="The discovery run was cancelled before it finished.",
        )
        raise

    # A run that finished as ``failed`` (every ticker errored) still RAN: the
    # job completed, with a warning naming the run's own verdict.
    warnings: tuple[str, ...] = (
        () if final_status == "completed" else (f"run_status={final_status}",)
    )
    return JobOutcome(result_type=RESULT_TYPE, result_ref=run_id, warnings=warnings)
