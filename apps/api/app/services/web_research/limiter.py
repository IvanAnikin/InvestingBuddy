"""Per-host pacing and concurrency for open-web fetches — open-web W2 (spec §9.2, §19.4).

* **Pacing:** at most one request per second per REGISTRABLE DOMAIN (the existing
  :class:`~app.services.sources.rate_limit.SlidingWindowLimiter`, wired for the first
  time), widened to a site's ``Crawl-delay`` up to 10 s. robots.txt and TDMRep lookups
  are paced like any other request to the site.
* **Concurrency:** at most 2 requests in flight per registrable domain, and at most 4
  open-web requests in flight in the whole process (B1, one worker).

The per-run per-domain page cap lives in the run's budget
(:meth:`WebResearchBudget.domain_refusal`), not here: this limiter is process-wide.

Order inside :meth:`OpenWebLimiter.slot`: domain semaphore → wait (WITHOUT a global
slot) until the domain's window admits → take a global slot → re-check the window
(another request may have started meanwhile; if so give the global slot back and wait
again) → record → request. A site's 10 s ``Crawl-delay`` therefore never holds a global
slot idle (review M4), and the interval is measured between real request starts.
``Crawl-delay`` read from several origins of one registrable domain applies its
maximum.

The clock and the sleep are injectable: tests drive the pacing with a fake clock and
assert the observed intervals without sleeping.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from app.services.sources.rate_limit import SlidingWindowLimiter

MIN_INTERVAL_SECONDS = 1.0
MAX_CRAWL_DELAY_SECONDS = 10.0
PER_HOST_CONCURRENCY = 2
GLOBAL_CONCURRENCY = 4
_MAX_DOMAINS = 2048

Sleep = Callable[[float], Awaitable[None]]


@dataclass
class _DomainState:
    semaphore: asyncio.Semaphore
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    window: SlidingWindowLimiter = field(
        default_factory=lambda: SlidingWindowLimiter(1, MIN_INTERVAL_SECONDS)
    )
    last_used: float = 0.0
    in_flight: int = 0


class OpenWebLimiter:
    """Process-wide politeness for open-web fetches."""

    def __init__(
        self,
        *,
        global_concurrency: int = GLOBAL_CONCURRENCY,
        per_host_concurrency: int = PER_HOST_CONCURRENCY,
        min_interval_seconds: float = MIN_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._global = asyncio.Semaphore(max(1, global_concurrency))
        self._per_host = max(1, per_host_concurrency)
        self._min_interval = max(0.001, min_interval_seconds)
        self._clock = clock
        self._sleep = sleep
        self._domains: dict[str, _DomainState] = {}
        #: ``(domain, start time)`` of every request admitted, for timing logs/tests.
        self.starts: list[tuple[str, float]] = []

    def _state(self, domain: str) -> _DomainState:
        state = self._domains.get(domain)
        if state is None:
            if len(self._domains) >= _MAX_DOMAINS:
                self._evict_idle()
            window = SlidingWindowLimiter(1, self._min_interval)
            state = _DomainState(asyncio.Semaphore(self._per_host), window=window)
            self._domains[domain] = state
        return state

    def _evict_idle(self) -> None:
        now = self._clock()
        for key, state in list(self._domains.items()):
            if (
                state.in_flight == 0
                and not state.lock.locked()
                and now - state.last_used > MAX_CRAWL_DELAY_SECONDS
                and not getattr(state.semaphore, "_waiters", None)
            ):
                self._domains.pop(key, None)

    def interval_for(self, domain: str) -> float:
        return self._state(domain).window.per_seconds

    def set_crawl_delay(self, domain: str, seconds: float | None) -> float:
        """Widen ``domain``'s interval to a ``Crawl-delay`` (clamped to 1–10 s).

        Never narrows: two origins of one domain with different delays get the larger.
        """
        state = self._state(domain)
        if seconds is not None and seconds > 0:
            wanted = min(float(seconds), MAX_CRAWL_DELAY_SECONDS)
            state.window.per_seconds = max(state.window.per_seconds, self._min_interval, wanted)
        return state.window.per_seconds

    @asynccontextmanager
    async def slot(self, domain: str) -> AsyncIterator[float]:
        """Hold a request slot for ``domain``; yields the (clock) start time."""
        state = self._state(domain)
        state.in_flight += 1
        try:
            async with state.semaphore:
                while True:
                    wait = state.window.wait_seconds(self._clock())
                    if wait > 0:
                        await self._sleep(wait)  # no global slot held while pacing
                        continue
                    await self._global.acquire()
                    # No await between this check and the record: atomic under asyncio.
                    if state.window.wait_seconds(self._clock()) <= 0:
                        break
                    self._global.release()
                started = self._clock()
                state.window.allow(started)
                state.last_used = started
                if len(self.starts) < 10_000:
                    self.starts.append((domain, started))
                try:
                    yield started
                finally:
                    self._global.release()
        finally:
            state.in_flight -= 1


__all__ = [
    "GLOBAL_CONCURRENCY",
    "MAX_CRAWL_DELAY_SECONDS",
    "MIN_INTERVAL_SECONDS",
    "PER_HOST_CONCURRENCY",
    "OpenWebLimiter",
]
