"""The weekly coverage-index refresh as a durable periodic job — V3.21 slice 21.7.

``research_coverage_refresh`` rides the periodic-task registry (ADR-109): WEEKLY, behind
``V3_RESEARCH_COVERAGE_ENABLED`` (and the master ``V3_PERIODIC_TASKS_ENABLED``). The
handler is registered unconditionally so a job enqueued before the flag was switched off
still drains. A refresh that fetched nothing (robots, network, drift) RAISES
:class:`CoverageRefreshError` so the task's ``source_poll_state.last_error`` says so; the
next week's bucket tries again. The refresh's counts are written to ``detail_json``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.core.structured_logging import log_event
from app.models.source_poll_state import SourcePollState
from app.services.jobs.worker import JobContext, JobOutcome
from app.services.research_coverage.sitemap import RefreshResult, refresh_edison_index
from app.services.scheduling.periodic import (
    PAYLOAD_TASK_KEY,
    WEEKLY,
    PeriodicTask,
    periodic_job_handler,
    register_periodic_task,
)

logger = logging.getLogger(__name__)

JOB_TYPE = "research_coverage_refresh"
TASK_KEY = "research_coverage_refresh"
RESULT_TYPE = "research_coverage_refresh"


class CoverageRefreshError(RuntimeError):
    """The refresh read nothing usable. Not retried within the bucket: next week is."""

    job_transient = False


def _session_factory() -> Any:
    from app.db.session import async_session_factory

    return async_session_factory


async def _write_detail(session: Any, task_key: str, result: RefreshResult) -> None:
    row = await session.get(SourcePollState, task_key)
    if row is None:
        row = SourcePollState(task_key=task_key)
        session.add(row)
    row.detail_json = result.to_dict()
    row.updated_at = datetime.now(timezone.utc)


async def run_refresh(*, task_key: str | None = TASK_KEY, fetch: Any = None,
                      now: datetime | None = None) -> RefreshResult:
    """One refresh in its own committed session, with its counts on the task row."""
    async with _session_factory()() as session:
        result = await refresh_edison_index(session, fetch=fetch, now=now)
        if task_key:
            await _write_detail(session, task_key, result)
        await session.commit()
    return result


@periodic_job_handler(JOB_TYPE)
async def run_research_coverage_refresh_job(ctx: JobContext) -> JobOutcome:
    task_key = ctx.payload.get(PAYLOAD_TASK_KEY)
    await ctx.checkpoint("research_coverage_refresh")
    result = await run_refresh(task_key=str(task_key) if task_key else None)
    log_event(logger, "v3_research_coverage_refresh_job", job_id=ctx.job_id,
              state=result.state, equity_pages=result.equity_pages,
              inserted=result.inserted, updated=result.updated)
    if not result.ok:
        raise CoverageRefreshError(f"coverage refresh {result.state}")
    return JobOutcome(result_type=RESULT_TYPE)


RESEARCH_COVERAGE_TASK = register_periodic_task(PeriodicTask(
    key=TASK_KEY, interval=WEEKLY, job_type=JOB_TYPE,
    flag_attr="v3_research_coverage_enabled"))


__all__ = [
    "JOB_TYPE",
    "RESEARCH_COVERAGE_TASK",
    "TASK_KEY",
    "CoverageRefreshError",
    "run_refresh",
    "run_research_coverage_refresh_job",
]
