"""The ``trade_press_poll`` durable job and its 6-hourly periodic task — V3.21.6.

Registered through ``scheduling.periodic`` (ADR-109): the task is enqueued once per UTC
6-hour bucket when ``V3_PERIODIC_TASKS_ENABLED`` and ``V3_TRADE_PRESS_FEEDS_ENABLED`` are
on (and a durable worker exists). The handler polls the nine feeds once
(``trade_press.poller.poll_all``); the wrapper stamps ``last_ok_at`` / ``last_error`` on
the task's ``source_poll_state`` row. A manual run: ``scripts/poll_trade_press.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.structured_logging import log_event
from app.services.jobs.worker import JobContext, JobOutcome
from app.services.scheduling.periodic import (
    SIX_HOURLY,
    PeriodicTask,
    periodic_job_handler,
    register_periodic_task,
)
from app.services.trade_press.poller import JOB_TYPE, TASK_KEY, poll_all

logger = logging.getLogger(__name__)

RESULT_TYPE = "trade_press_poll"


def _session_factory() -> Any:
    from app.db.session import async_session_factory

    return async_session_factory


@periodic_job_handler(JOB_TYPE)
async def run_trade_press_poll_job(ctx: JobContext) -> JobOutcome:
    """Poll every trade-press feed once. No payload is read beyond the task key."""
    async with _session_factory()() as session:
        summary = await poll_all(session)
    log_event(logger, "v3_trade_press_poll_completed", job_id=ctx.job_id,
              feeds=len(summary.feeds), stored=summary.stored,
              already_held=summary.already_held, mentions=summary.mentions,
              failed=summary.failed)
    warnings: tuple[str, ...] = ()
    if summary.failed:
        warnings = (f"{summary.failed} feed(s) could not be polled",)
    return JobOutcome(result_type=RESULT_TYPE, warnings=warnings)


TRADE_PRESS_POLL_TASK = register_periodic_task(PeriodicTask(
    key=TASK_KEY, interval=SIX_HOURLY, job_type=JOB_TYPE,
    flag_attr="v3_trade_press_feeds_enabled"))


__all__ = ["JOB_TYPE", "RESULT_TYPE", "TASK_KEY", "TRADE_PRESS_POLL_TASK",
           "run_trade_press_poll_job"]
