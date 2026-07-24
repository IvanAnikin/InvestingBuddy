"""
Tiny in-process TTL cache for connector results — Phase 29A.

Free upstreams (SEC EDGAR, GLEIF, RSS/Atom feeds) reward re-use: a short cache
avoids re-fetching the same filing list twice inside one run. This is a minimal,
monotonic-clock cache with no external dependency. It is *not* wired into any
live path in 29A — it is framework groundwork for the connector phases.
"""

from __future__ import annotations

import time
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """A minimal key→value cache with per-entry time-to-live (seconds)."""

    def __init__(self, ttl_seconds: float = 300.0, max_entries: int = 512) -> None:
        self.ttl_seconds = max(0.0, ttl_seconds)
        self.max_entries = max(1, max_entries)
        self._store: dict[str, tuple[float, T]] = {}

    def _now(self) -> float:
        return time.monotonic()

    def get(self, key: str) -> T | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self.ttl_seconds and self._now() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: T) -> None:
        # Cheap size bound: drop the oldest-inserted entry when full.
        if key not in self._store and len(self._store) >= self.max_entries:
            oldest = next(iter(self._store))
            self._store.pop(oldest, None)
        self._store[key] = (self._now() + self.ttl_seconds, value)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


__all__ = ["TTLCache"]
