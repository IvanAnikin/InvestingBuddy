"""Periodic-task registry on the durable worker's sweep — V3.21.2 (ADR-109).

WHY HERE, AND NOT A CRON
========================
The durable worker already runs a sweep every 300 s (``worker.register_sweep_hook``). A
periodic task rides that sweep: no new process, no scheduler dependency, no timer of its
own. When a task is DUE it is **enqueued** as an ordinary durable job, so it runs under a
lease, retries on a transient failure and shows in the job table like every other job.

WHY IT CANNOT RUN TWICE IN ONE BUCKET
=====================================
Each task has an interval of one day, one ISO week or six hours, and the interval maps to
a UTC **bucket** (``2026-11-22``, ``2026-W47``, ``2026-11-22T06``). A task is due when its
``source_poll_state.last_started_at`` is before the current bucket's start. Two guards:

1. the bucket is CLAIMED with a guarded UPDATE (``... WHERE last_started_at IS NULL OR
   last_started_at < :bucket_start``); only one process sees ``rowcount == 1``;
2. the job's idempotency key is ``<task key>:<bucket>``, and a job already on record for
   that key (any status) is never enqueued again.

So two worker restarts on the same day, or two processes, give one pass.

WHAT A TASK IS
==============
``PeriodicTask(key, interval, job_type, flag_attr, payload_fn)``. ``flag_attr`` names the
task's own settings flag; ``V3_PERIODIC_TASKS_ENABLED`` is the master switch, and nothing
runs unless ``V3_DURABLE_JOBS_ENABLED`` (there is no worker to run it otherwise). Later
slices register their own tasks with :func:`register_periodic_task` and wrap their
handler with :func:`periodic_job_handler`, which stamps ``last_ok_at`` / ``last_error``.

Nothing here imports a research domain, and nothing here logs a payload.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.structured_logging import log_event
from app.models.source_poll_state import SourcePollState
from app.services.jobs.job_store import JobStore
from app.services.jobs.worker import (
    JobContext,
    JobHandler,
    JobOutcome,
    LeaseLostError,
    register_handler,
    register_sweep_hook,
)

logger = logging.getLogger(__name__)

DAILY = timedelta(days=1)
WEEKLY = timedelta(days=7)
SIX_HOURLY = timedelta(hours=6)
#: The intervals a bucket can be derived from. Anything else is refused at registration:
#: an interval with no calendar bucket has no idempotency key that survives a restart.
SUPPORTED_INTERVALS: frozenset[timedelta] = frozenset({DAILY, WEEKLY, SIX_HOURLY})

#: Payload key naming the task, read by :func:`periodic_job_handler`.
PAYLOAD_TASK_KEY = "periodic_task_key"
PAYLOAD_BUCKET = "periodic_bucket"

_ERROR_MAX = 300


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class PeriodicTask:
    """One registered periodic task."""

    key: str
    interval: timedelta
    job_type: str
    #: Settings attribute that switches this task on; ``None`` means master switch only.
    flag_attr: str | None = None
    #: Builds the job payload for one bucket. Must return JSON-safe values.
    payload_fn: Callable[[datetime], dict[str, Any]] | None = None


_tasks: dict[str, PeriodicTask] = {}


def register_periodic_task(task: PeriodicTask) -> PeriodicTask:
    """Register (or replace, by key) a periodic task. Idempotent on re-import."""
    if task.interval not in SUPPORTED_INTERVALS:
        raise ValueError(
            f"periodic task {task.key!r}: interval {task.interval} has no calendar bucket; "
            "use DAILY, WEEKLY or SIX_HOURLY")
    if not task.key or ":" in task.key:
        raise ValueError("a periodic task key is non-empty and has no ':'")
    _tasks[task.key] = task
    return task


def registered_tasks() -> list[PeriodicTask]:
    return [_tasks[k] for k in sorted(_tasks)]


def clear_periodic_tasks() -> None:
    """Drop every task. For tests."""
    _tasks.clear()


# ── buckets ──────────────────────────────────────────────────────────────── #


def bucket_start(interval: timedelta, now: datetime) -> datetime:
    """The UTC start of the bucket ``now`` falls in."""
    now = _aware(now) or _utcnow()
    now = now.astimezone(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if interval == DAILY:
        return day
    if interval == WEEKLY:
        return day - timedelta(days=day.isoweekday() - 1)  # ISO Monday
    if interval == SIX_HOURLY:
        return day.replace(hour=(now.hour // 6) * 6)
    raise ValueError(f"unsupported interval {interval}")


def bucket_label(interval: timedelta, now: datetime) -> str:
    """``2026-11-22`` (daily), ``2026-W47`` (weekly), ``2026-11-22T06`` (6-hourly)."""
    start = bucket_start(interval, now)
    if interval == DAILY:
        return start.date().isoformat()
    if interval == WEEKLY:
        year, week, _ = start.isocalendar()
        return f"{year}-W{week:02d}"
    return f"{start.date().isoformat()}T{start.hour:02d}"


def idempotency_key_for(task: PeriodicTask, now: datetime) -> str:
    return f"{task.key}:{bucket_label(task.interval, now)}"


def is_due(task: PeriodicTask, last_started_at: datetime | None, now: datetime) -> bool:
    """Due when it has never started, or last started before this bucket began."""
    started = _aware(last_started_at)
    return started is None or started < bucket_start(task.interval, now)


def task_enabled(task: PeriodicTask, cfg: Any) -> bool:
    if not getattr(cfg, "v3_periodic_tasks_enabled", False):
        return False
    return task.flag_attr is None or bool(getattr(cfg, task.flag_attr, False))


# ── state ────────────────────────────────────────────────────────────────── #


def _default_factory() -> async_sessionmaker[AsyncSession]:
    from app.db.session import async_session_factory

    return async_session_factory


async def _claim(
    factory: async_sessionmaker[AsyncSession], task: PeriodicTask, now: datetime
) -> tuple[bool, datetime | None]:
    """Claim this bucket for ``task``. Returns ``(claimed, previous last_started_at)``."""
    start = bucket_start(task.interval, now)
    async with factory() as session:
        row = await session.get(SourcePollState, task.key)
        if row is None:
            session.add(SourcePollState(task_key=task.key, last_started_at=now,
                                        updated_at=now))
            try:
                await session.commit()
                return True, None
            except IntegrityError:
                await session.rollback()  # another process inserted it; fall through
        previous = (await session.execute(
            sa.select(SourcePollState.last_started_at)
            .where(SourcePollState.task_key == task.key)
            .execution_options(populate_existing=True))).scalar_one_or_none()
        result = await session.execute(
            sa.update(SourcePollState)
            .where(SourcePollState.task_key == task.key,
                   sa.or_(SourcePollState.last_started_at.is_(None),
                          SourcePollState.last_started_at < start))
            .values(last_started_at=now, updated_at=now)
            .execution_options(synchronize_session=False))
        await session.commit()
        return (getattr(result, "rowcount", 0) or 0) == 1, previous


async def _release(factory: async_sessionmaker[AsyncSession], task_key: str,
                   claimed_at: datetime, previous: datetime | None, error: str) -> None:
    """Undo a claim whose enqueue failed, so the next sweep tries the bucket again."""
    async with factory() as session:
        await session.execute(
            sa.update(SourcePollState)
            .where(SourcePollState.task_key == task_key,
                   SourcePollState.last_started_at == claimed_at)
            .values(last_started_at=previous, last_error=error[:_ERROR_MAX],
                    updated_at=_utcnow())
            .execution_options(synchronize_session=False))
        await session.commit()


async def stamp_result(
    task_key: str, *, ok: bool, error: str | None = None,
    detail: dict[str, Any] | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    now: datetime | None = None,
) -> None:
    """Record how a periodic task's job ended: ``last_ok_at`` or ``last_error``."""
    factory = session_factory or _default_factory()
    stamp = _aware(now) or _utcnow()
    async with factory() as session:
        row = await session.get(SourcePollState, task_key)
        if row is None:
            row = SourcePollState(task_key=task_key)
            session.add(row)
        if ok:
            row.last_ok_at = stamp
            row.last_error = None
        else:
            row.last_error = (error or "failed")[:_ERROR_MAX]
        if detail is not None:
            row.detail_json = dict(detail)
        row.updated_at = stamp
        await session.commit()


# ── the sweep ────────────────────────────────────────────────────────────── #


async def run_periodic_tasks(
    *, cfg: Any = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    store: JobStore | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Enqueue every enabled, due task once for its bucket. Returns the idempotency keys
    enqueued. Never raises for one task: a failure is logged by type and the next task
    still runs."""
    if cfg is None:
        from app.core.config import settings as cfg
    if not getattr(cfg, "v3_periodic_tasks_enabled", False):
        return []
    factory = session_factory or _default_factory()
    job_store = store or JobStore(factory)
    stamp = _aware(now) or _utcnow()
    enqueued: list[str] = []
    for task in registered_tasks():
        if not task_enabled(task, cfg):
            continue
        key = idempotency_key_for(task, stamp)
        try:
            if await job_store.latest_for_base_key(key) is not None:
                continue  # this bucket's job is already on record
            claimed, previous = await _claim(factory, task, stamp)
            if not claimed:
                continue
            payload = dict(task.payload_fn(stamp) if task.payload_fn else {})
            payload[PAYLOAD_TASK_KEY] = task.key
            payload[PAYLOAD_BUCKET] = bucket_label(task.interval, stamp)
            try:
                view, created = await job_store.enqueue(
                    job_type=task.job_type, idempotency_key=key, payload=payload,
                    now=stamp)
            except Exception as exc:
                await _release(factory, task.key, stamp, previous,
                               f"enqueue failed: {type(exc).__name__}")
                raise
            if created:
                enqueued.append(key)
            log_event(logger, "v3_periodic_task_enqueued", task_key=task.key,
                      idempotency_key=key, job_id=view.id, created=created)
        except Exception as exc:  # noqa: BLE001 - one task never costs the sweep
            logger.warning("v3_periodic_task_failed task_key=%s error_type=%s",
                           task.key, type(exc).__name__)
    return enqueued


@register_sweep_hook
async def periodic_tasks_sweep() -> None:
    """The worker's sweep entry point. Inert unless durable jobs are on."""
    from app.core.config import settings

    if not getattr(settings, "v3_durable_jobs_enabled", False):
        return
    await run_periodic_tasks(cfg=settings)


# ── the handler wrapper ──────────────────────────────────────────────────── #


def periodic_job_handler(
    job_type: str,
    *,
    session_factory: Callable[[], async_sessionmaker[AsyncSession]] | None = None,
) -> Callable[[JobHandler], JobHandler]:
    """Register a durable job handler that stamps its periodic task's state.

    The stamp is keyed on the payload's ``periodic_task_key``; a job enqueued by hand (a
    manual run) carries none and stamps nothing. A lost lease stamps nothing: another
    worker owns the job.
    """

    def _decorate(fn: Callable[[JobContext], Awaitable[JobOutcome | None]]) -> JobHandler:
        @wraps(fn)
        async def _wrapped(ctx: JobContext) -> JobOutcome | None:
            task_key = ctx.payload.get(PAYLOAD_TASK_KEY)
            factory = session_factory() if session_factory else None
            try:
                outcome = await fn(ctx)
            except LeaseLostError:
                raise
            except Exception as exc:
                if task_key:
                    try:
                        await stamp_result(str(task_key), ok=False,
                                           error=type(exc).__name__,
                                           session_factory=factory)
                    except Exception:  # noqa: BLE001 - the stamp never masks the error
                        logger.warning("v3_periodic_stamp_failed task_key=%s", task_key)
                raise
            if task_key:
                await stamp_result(str(task_key), ok=True, session_factory=factory)
            return outcome

        register_handler(job_type)(_wrapped)
        return _wrapped

    return _decorate


__all__ = [
    "DAILY",
    "PAYLOAD_BUCKET",
    "PAYLOAD_TASK_KEY",
    "SIX_HOURLY",
    "SUPPORTED_INTERVALS",
    "WEEKLY",
    "PeriodicTask",
    "bucket_label",
    "bucket_start",
    "clear_periodic_tasks",
    "idempotency_key_for",
    "is_due",
    "periodic_job_handler",
    "periodic_tasks_sweep",
    "register_periodic_task",
    "registered_tasks",
    "run_periodic_tasks",
    "stamp_result",
    "task_enabled",
]
