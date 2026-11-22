"""The weekly industry-data refresh — V3.21.4 (ADR-111).

``industry_data_refresh`` is a WEEKLY periodic task on the durable worker
(``scheduling.periodic``, flag ``V3_INDUSTRY_DATA_REFRESH_ENABLED``). Each enabled source
fetches once for the configured countries and its release is stored in the macro store,
one source per transaction, so a publisher that is down costs that source only. The same
function backs ``scripts/ingest_industry_data.py`` (``--source``, ``--dry-run``).

Nothing here logs a URL, a body or a key: a source's outcome is logged as counts and a
reason.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.core.structured_logging import log_event
from app.services.industry_data.common import (
    SOURCE_EIA,
    SOURCE_EMBER,
    SOURCE_IRENA,
    SOURCES,
    IndustryRelease,
    configured_countries,
    store_industry_release,
)
from app.services.jobs.worker import JobContext, JobOutcome
from app.services.scheduling.periodic import (
    WEEKLY,
    PeriodicTask,
    periodic_job_handler,
    register_periodic_task,
)

logger = logging.getLogger(__name__)

JOB_TYPE = "industry_data_refresh"
TASK_KEY = "industry_data_refresh"
RESULT_TYPE = "industry_data_refresh"


@dataclass
class SourceOutcome:
    source_id: str
    parsed: int = 0
    written: int = 0
    fetch_attempts: int = 0
    vintage_at: str | None = None
    vintage_basis: str | None = None
    inert_reason: str | None = None
    error: str | None = None
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"source_id": self.source_id, "parsed": self.parsed, "written": self.written,
                "fetch_attempts": self.fetch_attempts, "vintage_at": self.vintage_at,
                "vintage_basis": self.vintage_basis, "inert_reason": self.inert_reason,
                "error": self.error, "skipped": self.skipped[:20]}


async def fetch_source(source_id: str, *, cfg: Any, countries: list[str],
                       fetchers: dict[str, Any] | None = None) -> IndustryRelease:
    """One source's release. ``fetchers`` injects ``fetcher``/``poster`` for tests."""
    injected = dict((fetchers or {}).get(source_id) or {})
    if source_id == SOURCE_EMBER:
        from app.services.industry_data.ember import fetch_ember

        return await fetch_ember(cfg=cfg, countries=countries, **injected)
    if source_id == SOURCE_IRENA:
        from app.services.industry_data.irena import fetch_irena

        return await fetch_irena(cfg=cfg, countries=countries, **injected)
    if source_id == SOURCE_EIA:
        from app.services.industry_data.eia import fetch_eia

        return await fetch_eia(cfg=cfg, **injected)
    raise ValueError(f"unknown industry data source {source_id!r}")


async def refresh_industry_data(
    session_factory: Callable[[], Any], *, cfg: Any = None,
    sources: Iterable[str] | None = None, countries: Iterable[str] | None = None,
    dry_run: bool = False, fetchers: dict[str, Any] | None = None,
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> list[SourceOutcome]:
    """Fetch and store each source. One transaction per source; ``dry_run`` rolls back."""
    if cfg is None:
        from app.core.config import settings as cfg
    wanted = list(sources) if sources else list(SOURCES)
    places = [c.upper() for c in countries] if countries else configured_countries(cfg)
    outcomes: list[SourceOutcome] = []
    for source_id in wanted:
        if progress is not None:
            await progress(source_id)
        outcome = SourceOutcome(source_id=source_id)
        try:
            release = await fetch_source(source_id, cfg=cfg, countries=places,
                                         fetchers=fetchers)
            outcome.parsed = len(release.observations)
            outcome.fetch_attempts = release.fetch_attempts
            outcome.inert_reason = release.inert_reason
            outcome.skipped = list(release.skipped)
            outcome.vintage_basis = release.vintage_basis
            outcome.vintage_at = (release.vintage_at.isoformat()
                                  if release.vintage_at else None)
            if release.observations:
                async with session_factory() as session:
                    outcome.written = await store_industry_release(session, release)
                    if dry_run:
                        await session.rollback()
                    else:
                        await session.commit()
        except Exception as exc:  # noqa: BLE001 - one source never costs the others
            outcome.error = type(exc).__name__
        log_event(logger, "v3_industry_data_source_refreshed", source_id=source_id,
                  parsed=outcome.parsed, written=outcome.written,
                  fetch_attempts=outcome.fetch_attempts, dry_run=dry_run,
                  inert=bool(outcome.inert_reason), error_type=outcome.error)
        outcomes.append(outcome)
    return outcomes


def _session_factory() -> Any:
    from app.db.session import async_session_factory

    return async_session_factory


@periodic_job_handler(JOB_TYPE)
async def run_industry_data_refresh_job(ctx: JobContext) -> JobOutcome:
    """Weekly (periodic) or manual. Payload: optional ``sources`` list."""
    requested = ctx.payload.get("sources")
    sources = [s for s in requested if s in SOURCES] if isinstance(requested, list) else None
    outcomes = await refresh_industry_data(_session_factory(), sources=sources,
                                           progress=ctx.checkpoint)
    warnings: list[str] = []
    for o in outcomes:
        if o.error:
            warnings.append(f"{o.source_id}: failed ({o.error})")
        elif o.inert_reason:
            warnings.append(f"{o.source_id}: {o.inert_reason}")
        elif not o.parsed:
            warnings.append(f"{o.source_id}: nothing parsed ({'; '.join(o.skipped[:2])})")
    return JobOutcome(result_type=RESULT_TYPE, warnings=tuple(w[:300] for w in warnings))


INDUSTRY_DATA_REFRESH_TASK = register_periodic_task(PeriodicTask(
    key=TASK_KEY, interval=WEEKLY, job_type=JOB_TYPE,
    flag_attr="v3_industry_data_refresh_enabled"))


__all__ = [
    "INDUSTRY_DATA_REFRESH_TASK",
    "JOB_TYPE",
    "TASK_KEY",
    "SourceOutcome",
    "fetch_source",
    "refresh_industry_data",
    "run_industry_data_refresh_job",
]
