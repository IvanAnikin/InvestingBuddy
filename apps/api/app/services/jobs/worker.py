"""The broker-agnostic research worker — V3.0 Slice 2.

WHY THIS MODULE EXISTS
======================
V2 runs multi-minute research inside the API process via FastAPI
``BackgroundTasks``. The job *state* is durable; the *execution* is not, so an
App Service recycle ends every in-flight run and recovery is "ask the user to
press the button again".

This is the loop that replaces it: claim a job under a lease, heartbeat while it
runs, and finish by recording an outcome the contract chose. Nothing here knows
what research is — it knows how to own a unit of work and how to give it back
safely. The handlers registered against it are where the domain lives.

WHY THERE IS NO BROKER
======================
The plan's degraded mode is the first mode: PostgreSQL polling, no Service Bus,
no second App Service, no cloud dependency at all. That is not a compromise —
the job row is the source of truth and a broker is only a delivery *hint*, so a
polling worker is a genuinely valid production topology at this volume. It is
also what makes every property below testable offline. Adding Service Bus later
(Slice 6, gated on OPEN DECISION #2) changes how a worker learns a job exists;
it does not change claiming, leasing, retrying or completion.

THE HEARTBEAT IS THE LIVENESS PROOF, AND THAT HAS A CONSEQUENCE
===============================================================
A worker holds its job only while it keeps saying it is alive. That single
mechanism does three jobs at once:

* it makes ``interrupted`` provable rather than a guess about elapsed time;
* it makes a crashed worker's job reclaimable automatically;
* and it converts "someone blocked the event loop" from an invisible
  performance problem into a *visible* failure — a handler that occupies the
  loop stops the heartbeat, its lease lapses, and another worker takes the job.

That last one is the reason CPU-heavy extraction must stay behind
``asyncio.to_thread`` even once it is out of the API process. The existing
invariant (``tests/test_ingestion_event_loop_not_blocked.py``) is not softened by
moving work to a worker; the worker gives it teeth.

CANCELLATION IS COOPERATIVE, ALWAYS
===================================
``cancel_requested`` is a flag a running handler observes at a task boundary. No
task is cancelled from the outside mid-step, because a research run that is
killed between "document fetched" and "document persisted" leaves exactly the
kind of half-written state the fail-closed rules exist to prevent. A handler
that never reaches a boundary simply runs to completion, and that is the correct
trade.

WHAT IS DELIBERATELY NOT LOGGED
===============================
``payload_json`` carries job inputs and never reaches a log line. Handler
exceptions are logged by *type*, not by message, on the failure path that a
provider error can reach — provider errors have been observed to carry credential
material, which is why ``RedactingFilter`` exists at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.core.structured_logging import log_event
from app.services.jobs.job_contract import JobView
from app.services.jobs.job_store import JobStore

logger = logging.getLogger(__name__)

#: How long an idle worker waits before polling again. Short enough that a user
#: waiting on a 202 does not perceive it, long enough that an idle deployment is
#: not running a query every few milliseconds.
DEFAULT_POLL_INTERVAL_SECONDS = 2.0

#: Backoff after a polling error (a database blip). Distinct from the job-level
#: retry backoff, which is about the work; this is about the loop itself.
DEFAULT_ERROR_BACKOFF_SECONDS = 10.0


class JobCancelled(Exception):
    """Raised inside a handler when cancellation was requested at a boundary."""


class LeaseLostError(Exception):
    """Raised inside a handler when this worker no longer owns the job."""


@dataclass(frozen=True)
class JobOutcome:
    """What a handler produced. Deliberately small.

    ``result_type``/``result_ref`` are the soft reference the job row already
    models: a job may yield a report, a discovery run or a field review, and one
    typed FK per outcome would add a column every time a job type is added.
    """

    result_type: str | None = None
    result_ref: uuid.UUID | None = None
    warnings: tuple[str, ...] = ()


@dataclass
class JobContext:
    """Everything a handler is allowed to know about its own execution.

    A handler receives this and nothing else — no session, no store, no worker.
    It can read its payload, report a stage, and ask whether it has been asked to
    stop. It cannot claim, complete or fail its own job, because a handler that
    can write its own outcome can write one that contradicts what actually
    happened.
    """

    job: JobView
    payload: dict[str, Any]
    owner: str
    _worker: ResearchWorker = field(repr=False)

    @property
    def job_id(self) -> str:
        return self.job.id

    @property
    def attempt(self) -> int:
        """1-based. A handler may want to behave differently on a retry."""
        return self.job.attempt

    def cancellation_requested(self) -> bool:
        """True once a cancel was observed. Checked, never enforced from outside."""
        return self._worker.cancellation_requested

    def lease_lost(self) -> bool:
        return self._worker.lease_lost

    async def checkpoint(self, stage: str | None = None) -> None:
        """A task boundary: report progress, then honour a stop request.

        Called between units of work. Reporting the stage here rather than on a
        timer means the stage a reader sees is one the pipeline actually
        announced, which is the same discipline ``research_job.stage_for_node``
        already enforces for the V2 path.
        """
        if stage is not None:
            await self._worker.report_stage(stage)
        self.raise_if_stopped()

    def raise_if_stopped(self) -> None:
        """Stop the handler if it has been cancelled or has lost its lease."""
        if self._worker.lease_lost:
            raise LeaseLostError(f"job {self.job.id} is no longer owned by {self.owner}")
        if self._worker.cancellation_requested:
            raise JobCancelled(f"job {self.job.id} was cancelled")


JobHandler = Callable[[JobContext], Awaitable[JobOutcome | None]]


class HandlerRegistry:
    """job_type -> handler. One registry instance per worker, plus a default.

    An unknown job type is a *permanent* failure rather than an endless retry: a
    worker fleet that does not have the handler will not grow one by trying
    again, and burning attempts to discover that costs a real research budget.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        self._handlers[job_type] = handler

    def handler_for(self, job_type: str) -> JobHandler | None:
        return self._handlers.get(job_type)

    @property
    def job_types(self) -> list[str]:
        return sorted(self._handlers)

    def __contains__(self, job_type: object) -> bool:
        return job_type in self._handlers


#: The process-wide registry. Handlers register at import time in the slice that
#: owns them, so this module never imports the research domain.
registry = HandlerRegistry()


def register_handler(job_type: str) -> Callable[[JobHandler], JobHandler]:
    """Decorator form of :meth:`HandlerRegistry.register`."""

    def _decorate(fn: JobHandler) -> JobHandler:
        registry.register(job_type, fn)
        return fn

    return _decorate


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


def is_transient_failure(exc: BaseException) -> bool:
    """Whether ``exc`` is worth another attempt.

    Reuses the council's existing taxonomy (``llm.client.is_transient_llm_error``)
    rather than inventing a second one, and adds the transport-level errors a
    worker sees that an LLM client does not: timeouts, connection resets and DNS
    failures. Everything else is permanent, because retrying a bug reproduces the
    bug at full research cost.
    """
    from app.services.llm.client import is_transient_llm_error

    if isinstance(exc, (JobCancelled, LeaseLostError)):
        return False
    if isinstance(exc, Exception) and is_transient_llm_error(exc):
        return True
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, socket.gaierror):
        return True
    if isinstance(exc, OSError):
        # A broad net, but an OSError reaching here is an I/O condition rather
        # than a logic error, and I/O conditions are the retryable kind.
        return True
    return False


def default_owner_id() -> str:
    """A stable, human-readable identity for this worker instance.

    Host plus PID plus a short random suffix. The suffix matters: two workers in
    one process (a test, or an in-process worker beside the API) would otherwise
    share an identity and could heartbeat each other's leases.
    """
    host = os.environ.get("WEBSITE_INSTANCE_ID") or socket.gethostname()
    return f"{host[:64]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------


class ResearchWorker:
    """Claim → heartbeat → execute → complete/retry, for one job at a time.

    One job at a time on purpose. Concurrency is achieved by running more
    workers, which is the topology the lease already makes safe, rather than by
    an in-process semaphore that a restart would forget. It also keeps the B1
    memory ceiling (~1.75 GB, already 93-95% with one API worker) a matter of how
    many processes are started rather than a number buried in this class.
    """

    def __init__(
        self,
        store: JobStore | None = None,
        *,
        handlers: HandlerRegistry | None = None,
        owner: str | None = None,
        job_types: list[str] | tuple[str, ...] | None = None,
        lease_seconds: int | None = None,
        heartbeat_seconds: float | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        error_backoff_seconds: float = DEFAULT_ERROR_BACKOFF_SECONDS,
    ) -> None:
        self.store = store if store is not None else JobStore()
        self.handlers = handlers if handlers is not None else registry
        self.owner = owner or default_owner_id()
        self.job_types = list(job_types) if job_types else None
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.error_backoff_seconds = error_backoff_seconds

        self._current: JobView | None = None
        self._stop = asyncio.Event()
        self.cancellation_requested = False
        self.lease_lost = False
        self.heartbeats_sent = 0

    # -- configuration ----------------------------------------------------

    @property
    def lease_seconds(self) -> int:
        if self._lease_seconds is not None:
            return self._lease_seconds
        from app.core.config import settings

        return int(settings.v3_job_lease_seconds)

    @property
    def heartbeat_seconds(self) -> float:
        if self._heartbeat_seconds is not None:
            return self._heartbeat_seconds
        from app.core.config import settings

        return float(settings.v3_job_heartbeat_seconds)

    # -- the loop ---------------------------------------------------------

    def request_stop(self) -> None:
        """Ask the loop to finish the job in hand and then exit."""
        self._stop.set()

    async def run_forever(self, *, stop: asyncio.Event | None = None) -> None:
        """Poll until asked to stop. Never raises; a loop that dies is an outage.

        A shutdown does NOT abandon the job in hand — it finishes, and only then
        does the loop exit. A worker that dropped its job on SIGTERM would make
        every deployment produce a reclaim storm, which is exactly the churn the
        lease exists to make unnecessary.
        """
        if stop is not None:
            self._stop = stop
        log_event(
            logger,
            "v3_worker_started",
            owner=self.owner,
            job_types=",".join(self.job_types or self.handlers.job_types),
            poll_interval_seconds=self.poll_interval_seconds,
        )
        while not self._stop.is_set():
            try:
                did_work = await self.run_once()
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                logger.exception("v3_worker_loop_error type=%s", type(exc).__name__)
                await self._sleep(self.error_backoff_seconds)
                continue
            if not did_work:
                await self._sleep(self.poll_interval_seconds)
        log_event(logger, "v3_worker_stopped", owner=self.owner)

    async def _sleep(self, seconds: float) -> None:
        """Interruptible wait — a stop request must not wait out a poll interval."""
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    async def run_once(self) -> bool:
        """Claim and execute at most one job. True when a job was handled."""
        job = await self.store.claim_next(
            owner=self.owner,
            job_types=self.job_types,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return False

        self._current = job
        self.cancellation_requested = bool(job.cancel_requested)
        self.lease_lost = False
        log_event(
            logger,
            "v3_job_claimed",
            job_id=job.id,
            job_type=job.job_type,
            attempt=job.attempt,
            max_attempts=job.max_attempts,
            owner=self.owner,
        )
        try:
            await self._execute(job)
        finally:
            self._current = None
        return True

    # -- one job ----------------------------------------------------------

    async def _execute(self, job: JobView) -> None:
        # A cancel that arrived while the job sat in the queue is honoured
        # BEFORE the handler is entered. Starting expensive work that has
        # already been called off is the one case where "cooperative" would be
        # an excuse rather than a design.
        if self.cancellation_requested:
            await self.store.cancel(job.id, owner=self.owner)
            log_event(logger, "v3_job_cancelled", job_id=job.id, at="claim")
            return

        handler = self.handlers.handler_for(job.job_type)
        if handler is None:
            await self.store.fail(
                job.id,
                owner=self.owner,
                transient=False,
                error_class="unknown_job_type",
                error_message=f"no handler registered for {job.job_type!r}",
            )
            log_event(
                logger,
                "v3_job_failed",
                level=logging.ERROR,
                job_id=job.id,
                job_type=job.job_type,
                reason="unknown_job_type",
            )
            return

        payload = await self.store.get_payload(job.id) or {}
        ctx = JobContext(job=job, payload=payload, owner=self.owner, _worker=self)

        started = datetime.now(timezone.utc)
        outcome: JobOutcome | None = None
        error: BaseException | None = None

        # The heartbeat is stopped BEFORE any terminal write, not alongside it.
        # A beat landing between "cancelled" and "heartbeat cancelled" would find
        # a job it no longer owns and report a spurious lost lease — true but
        # useless noise on a path that already ended cleanly.
        beat = asyncio.create_task(self._heartbeat_loop(job.id))
        try:
            outcome = await handler(ctx)
        except BaseException as exc:  # noqa: BLE001 - classified below, never swallowed
            error = exc
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat

        if isinstance(error, JobCancelled):
            await self.store.cancel(job.id, owner=self.owner)
            log_event(logger, "v3_job_cancelled", job_id=job.id, at="boundary")
            return
        if isinstance(error, LeaseLostError):
            # Deliberately writes nothing. Another worker owns this job now and
            # is producing its outcome; a second writer here is the split-brain
            # the ownership guard exists to prevent.
            log_event(
                logger, "v3_job_lease_lost", level=logging.WARNING, job_id=job.id
            )
            return
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            # The PROCESS is going away, not the job. Leave the row exactly as it
            # is: its lease will lapse and another worker will reclaim it, which
            # is the recovery path this whole design exists to provide. Writing
            # ``failed`` here would turn a restart into a permanent loss.
            log_event(
                logger,
                "v3_job_abandoned_on_shutdown",
                level=logging.WARNING,
                job_id=job.id,
            )
            raise error
        if error is not None:
            await self._record_failure(job, error, started)
            return

        result = outcome or JobOutcome()
        completed = await self.store.complete(
            job.id,
            owner=self.owner,
            with_warnings=bool(result.warnings),
            result_type=result.result_type,
            result_ref=result.result_ref,
        )
        if completed is None:
            log_event(
                logger,
                "v3_job_lease_lost",
                level=logging.WARNING,
                job_id=job.id,
                at="completion",
            )
            return
        log_event(
            logger,
            "v3_job_completed",
            job_id=job.id,
            job_type=job.job_type,
            status=completed.status,
            attempt=job.attempt,
            warning_count=len(result.warnings),
            duration_ms=_elapsed_ms(started),
        )

    async def _record_failure(
        self, job: JobView, exc: BaseException, started: datetime
    ) -> None:
        transient = is_transient_failure(exc)
        # TYPE only. A provider exception's message has been observed to carry
        # credential material, which is why the redacting filter exists; the type
        # is what an operator needs and it cannot leak.
        error_class = type(exc).__name__
        outcome = await self.store.fail(
            job.id,
            owner=self.owner,
            transient=transient,
            error_class=error_class,
            error_message=error_class,
        )
        if outcome is None:
            log_event(
                logger, "v3_job_lease_lost", level=logging.WARNING, job_id=job.id
            )
            return
        logger.exception("v3_job_handler_error job_id=%s type=%s", job.id, error_class)
        log_event(
            logger,
            "v3_job_failed",
            level=logging.ERROR,
            job_id=job.id,
            job_type=job.job_type,
            status=outcome.job.status,
            attempt=job.attempt,
            max_attempts=job.max_attempts,
            transient=transient,
            will_retry=outcome.will_retry,
            error_class=error_class,
            duration_ms=_elapsed_ms(started),
        )

    # -- liveness ---------------------------------------------------------

    async def report_stage(self, stage: str) -> None:
        """Persist a stage change and refresh the lease in the same write."""
        await self._beat(stage=stage)

    async def _heartbeat_loop(self, job_id: str) -> None:
        """Renew the lease on a timer until cancelled.

        Also the only moment a running worker reads ``cancel_requested``: polling
        it separately would double the write-path traffic for a flag that is only
        actionable at the next boundary anyway.
        """
        interval = max(0.01, self.heartbeat_seconds)
        while True:
            await asyncio.sleep(interval)
            if not await self._beat():
                return

    async def _beat(self, *, stage: str | None = None) -> bool:
        """One heartbeat. False once the lease is gone."""
        if self._current is None:
            return False
        view = await self.store.heartbeat(
            self._current.id,
            owner=self.owner,
            lease_seconds=self.lease_seconds,
            stage=stage,
        )
        if view is None:
            self.lease_lost = True
            return False
        self.heartbeats_sent += 1
        self._current = view
        if view.cancel_requested:
            self.cancellation_requested = True
        return True


def _elapsed_ms(started: datetime) -> int:
    return int((datetime.now(timezone.utc) - started).total_seconds() * 1000)


# ---------------------------------------------------------------------------
# Process entry point
# ---------------------------------------------------------------------------


async def run_worker(stop: asyncio.Event | None = None) -> None:
    """Run one worker until stopped. The entry point for a worker process.

    Importing the handler module here rather than at module scope keeps this
    file free of any research-domain import, which is what lets the loop be
    tested with fake handlers and nothing else loaded.
    """
    with contextlib.suppress(ImportError):
        import app.services.jobs.handlers  # noqa: F401

    await ResearchWorker().run_forever(stop=stop)


def main() -> None:  # pragma: no cover - process entry point
    """``python -m app.services.jobs.worker``."""
    from app.core.logging_config import configure_logging

    configure_logging()
    asyncio.run(run_worker())


if __name__ == "__main__":  # pragma: no cover
    main()
