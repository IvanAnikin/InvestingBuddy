"""
Rate-limit policy + a minimal in-process limiter — Phase 29A.

It exists so a connector can *declare* how politely it must consume its upstream
(SEC EDGAR, GLEIF and friends are free but expect a low, identified request rate).
``SlidingWindowLimiter`` is wired for the first time by open-web W2
(``services/web_research/limiter.py``): one window per registrable domain paces
open-web fetches to at most one request per second (longer under ``Crawl-delay``).

``RateLimitPolicy`` is a plain, serialisable description — it contains no
secrets and is safe to expose in the registry/health API.
"""

from __future__ import annotations

import time
from collections import deque

from pydantic import BaseModel


class RateLimitPolicy(BaseModel):
    """A safe, declarative description of how to pace calls to a source."""

    requests_per_minute: int | None = None
    requests_per_day: int | None = None
    min_interval_seconds: float | None = None
    max_concurrency: int = 1
    note: str | None = None

    def describe(self) -> str:
        """A short human summary for the registry UI."""
        bits: list[str] = []
        if self.requests_per_minute:
            bits.append(f"{self.requests_per_minute}/min")
        if self.requests_per_day:
            bits.append(f"{self.requests_per_day}/day")
        if self.min_interval_seconds:
            bits.append(f"≥{self.min_interval_seconds:g}s apart")
        bits.append(f"concurrency {self.max_concurrency}")
        summary = ", ".join(bits)
        return f"{summary} ({self.note})" if self.note else summary


class SlidingWindowLimiter:
    """A tiny monotonic-clock sliding-window limiter (in-process, best-effort).

    Not distributed and not persisted — it only smooths bursts inside one worker.
    ``allow()`` is non-blocking and returns whether a call may proceed now;
    ``wait_seconds()`` says how long until it would. An event exactly
    ``per_seconds`` old has left the window, so "1 per second" admits a call at
    t=0 and the next at t=1.0.
    """

    def __init__(self, max_events: int, per_seconds: float) -> None:
        self.max_events = max(1, max_events)
        self.per_seconds = max(0.001, per_seconds)
        self._events: deque[float] = deque()

    def _expire(self, t: float) -> None:
        cutoff = t - self.per_seconds
        while self._events and self._events[0] <= cutoff:
            self._events.popleft()

    def allow(self, now: float | None = None) -> bool:
        t = time.monotonic() if now is None else now
        self._expire(t)
        if len(self._events) >= self.max_events:
            return False
        self._events.append(t)
        return True

    def wait_seconds(self, now: float | None = None) -> float:
        """Seconds until :meth:`allow` would admit a call (0.0 when it would now)."""
        t = time.monotonic() if now is None else now
        self._expire(t)
        if len(self._events) < self.max_events:
            return 0.0
        return max(0.0, self._events[0] + self.per_seconds - t)


__all__ = ["RateLimitPolicy", "SlidingWindowLimiter"]
