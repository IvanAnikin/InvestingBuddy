"""The web research budget — open-web W1 (spec §19.1, threat model QI-05).

Checked **before** every provider call, and a call that fails still spends: a 429, a
timeout and an empty answer each cost one query against the run and against the
platform's daily cap. Otherwise an outage would be the cheapest way to issue unlimited
queries.

Two scopes:

* **per run** — the profile's limits (spec §19.1), narrowed (never widened) by the
  operator's ``V3_RUN_MAX_WEB_SEARCHES``;
* **per platform per UTC day** — ``V3_WEB_SEARCH_MAX_QUERIES_PER_DAY``, counted from the
  ``network_call_count`` of today's ``web_search_queries`` rows. A cache serve and a
  refusal made no network call and do not count.

The daily count is read once when the run starts and then incremented locally. Two
concurrent runs can therefore overshoot the cap by at most one run's in-flight
reservations; the worker runs one job at a time today (spec §19.4), so this is exact in
practice and documented rather than locked.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

LIMIT_QUERIES = "max_queries"
LIMIT_RESULTS = "max_results"
LIMIT_FETCHES = "max_fetches"
LIMIT_PDFS = "max_pdfs"
LIMIT_BYTES = "max_bytes"
LIMIT_WALL = "max_wall_seconds"
LIMIT_DAILY = "daily_platform_cap"

#: The prefix every budget refusal code carries, e.g. ``budget:max_queries``.
BUDGET_REFUSAL_PREFIX = "budget:"

_MB = 1024 * 1024


@dataclass(frozen=True)
class WebBudgetLimits:
    """One profile's limits. ``0`` means that dimension is not permitted at all."""

    max_queries: int
    max_results: int
    max_fetches: int
    max_pdfs: int
    max_bytes: int
    max_wall_seconds: float


#: Spec §19.1, recommended initial values. Keyed by ``<entry point>_<depth>``.
PROFILES: dict[str, WebBudgetLimits] = {
    "discovery_standard": WebBudgetLimits(24, 200, 40, 8, 80 * _MB, 6 * 60),
    "discovery_deep": WebBudgetLimits(48, 400, 80, 15, 160 * _MB, 10 * 60),
    "company_quick": WebBudgetLimits(6, 40, 8, 2, 20 * _MB, 2 * 60),
    "company_standard": WebBudgetLimits(16, 120, 30, 6, 60 * _MB, 6 * 60),
    "company_deep": WebBudgetLimits(36, 300, 70, 12, 150 * _MB, 12 * 60),
    "company_max": WebBudgetLimits(60, 300, 100, 20, 150 * _MB, 20 * 60),
    "followup": WebBudgetLimits(6, 40, 12, 3, 30 * _MB, 3 * 60),
}


def limits_for(profile: str, cfg: Any | None = None) -> WebBudgetLimits:
    """The profile's limits, narrowed by the operator's ``V3_RUN_MAX_WEB_SEARCHES``.

    An unknown profile is a programming error and raises: guessing a budget is how a run
    ends up unbounded.
    """
    if profile not in PROFILES:
        raise KeyError(f"unknown web research budget profile {profile!r}")
    limits = PROFILES[profile]
    if cfg is None:
        from app.core.config import settings as cfg  # noqa: PLW0127
    operator_cap = int(getattr(cfg, "v3_run_max_web_searches", 0) or 0)
    if operator_cap > 0 and operator_cap < limits.max_queries:
        limits = replace(limits, max_queries=operator_cap)
    return limits


@dataclass
class WebResearchBudget:
    """Counters for one run, plus the platform's daily search cap."""

    limits: WebBudgetLimits
    #: ``V3_WEB_SEARCH_MAX_QUERIES_PER_DAY``. 0 means no calls, not unbounded.
    daily_cap: int
    #: Network calls already made today (UTC) before this run started.
    daily_used: int = 0
    clock: Callable[[], float] = time.monotonic
    queries_reserved: int = 0
    results_seen: int = 0
    fetches: int = 0
    pdfs: int = 0
    bytes_downloaded: int = 0
    started_at: float = field(default=0.0)

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = self.clock()

    @property
    def elapsed_seconds(self) -> float:
        return self.clock() - self.started_at

    def _wall_exhausted(self) -> bool:
        return self.elapsed_seconds >= self.limits.max_wall_seconds

    def query_refusal(self) -> str | None:
        """Which limit would stop the next provider call, or ``None``."""
        if self._wall_exhausted():
            return BUDGET_REFUSAL_PREFIX + LIMIT_WALL
        if self.queries_reserved >= self.limits.max_queries:
            return BUDGET_REFUSAL_PREFIX + LIMIT_QUERIES
        if self.daily_used >= self.daily_cap:
            return BUDGET_REFUSAL_PREFIX + LIMIT_DAILY
        return None

    def reserve_query(self) -> str | None:
        """Reserve one provider call **before** making it. Returns a refusal or ``None``.

        The reservation is never returned: a failed call spent its slot (QI-05).
        Synchronous check-and-increment, so it is atomic under asyncio.
        """
        refusal = self.query_refusal()
        if refusal is not None:
            return refusal
        self.queries_reserved += 1
        self.daily_used += 1
        return None

    def record_results(self, count: int) -> int:
        """Admit up to ``count`` results; returns how many fit under ``max_results``."""
        room = max(0, self.limits.max_results - self.results_seen)
        admitted = min(max(0, count), room)
        self.results_seen += admitted
        return admitted

    def fetch_refusal(self, *, is_pdf: bool = False) -> str | None:
        """Which limit would stop the next fetch (used from W2), or ``None``."""
        if self._wall_exhausted():
            return BUDGET_REFUSAL_PREFIX + LIMIT_WALL
        if self.fetches >= self.limits.max_fetches:
            return BUDGET_REFUSAL_PREFIX + LIMIT_FETCHES
        if is_pdf and self.pdfs >= self.limits.max_pdfs:
            return BUDGET_REFUSAL_PREFIX + LIMIT_PDFS
        if self.bytes_downloaded >= self.limits.max_bytes:
            return BUDGET_REFUSAL_PREFIX + LIMIT_BYTES
        return None

    def record_fetch(self, *, byte_count: int, is_pdf: bool = False) -> None:
        self.fetches += 1
        if is_pdf:
            self.pdfs += 1
        self.bytes_downloaded += max(0, int(byte_count))

    def to_dict(self) -> dict[str, Any]:
        return {
            "queries_reserved": self.queries_reserved,
            "max_queries": self.limits.max_queries,
            "results_seen": self.results_seen,
            "max_results": self.limits.max_results,
            "daily_used": self.daily_used,
            "daily_cap": self.daily_cap,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


def utc_day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


async def network_calls_today(session: Any, *, now: datetime | None = None) -> int:
    """Sum of ``network_call_count`` over today's (UTC) ``web_search_queries`` rows."""
    import sqlalchemy as sa

    from app.models.web_research import WebSearchQuery

    total = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(WebSearchQuery.network_call_count), 0)).where(
            WebSearchQuery.created_at >= utc_day_start(now)
        )
    )
    return int(total or 0)


async def budget_for_run(
    session: Any,
    profile: str,
    *,
    cfg: Any | None = None,
    now: datetime | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> WebResearchBudget:
    """A run's budget, with today's platform usage already loaded."""
    if cfg is None:
        from app.core.config import settings as cfg  # noqa: PLW0127
    return WebResearchBudget(
        limits=limits_for(profile, cfg),
        daily_cap=max(0, int(getattr(cfg, "v3_web_search_max_queries_per_day", 0) or 0)),
        daily_used=await network_calls_today(session, now=now),
        clock=clock,
    )


__all__ = [
    "BUDGET_REFUSAL_PREFIX",
    "LIMIT_DAILY",
    "LIMIT_QUERIES",
    "LIMIT_RESULTS",
    "LIMIT_WALL",
    "PROFILES",
    "WebBudgetLimits",
    "WebResearchBudget",
    "budget_for_run",
    "limits_for",
    "network_calls_today",
    "utc_day_start",
]
