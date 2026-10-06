"""Negative fetch cache — open-web W2 (spec §18).

A URL that answered 401/402/403/404/410, sat behind a paywall, login, consent wall or
CAPTCHA, or was refused by policy is not asked again for 24 h — and for 7 days once it
fails again after that. Keyed by CANONICAL URL, so ``?utm_source=x`` does not buy a
second attempt.

In-process and bounded (one worker; spec §19.4). A restart forgets it, which costs at
most one extra request per URL; the ``web_fetch_attempts`` rows keep the history.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

FIRST_TTL_SECONDS = 24 * 3600.0
REPEAT_TTL_SECONDS = 7 * 24 * 3600.0
_MAX_ENTRIES = 20_000


@dataclass
class _Entry:
    reason: str
    expires_at: float
    strikes: int


class NegativeCache:
    def __init__(
        self,
        *,
        first_ttl_seconds: float = FIRST_TTL_SECONDS,
        repeat_ttl_seconds: float = REPEAT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = _MAX_ENTRIES,
    ) -> None:
        self._first = first_ttl_seconds
        self._repeat = repeat_ttl_seconds
        self._clock = clock
        self._max = max(1, max_entries)
        self._entries: dict[str, _Entry] = {}

    def get(self, key: str | None) -> str | None:
        """The cached failure reason while the entry is live, else None."""
        if not key:
            return None
        entry = self._entries.get(key)
        if entry is None or self._clock() >= entry.expires_at:
            return None
        return entry.reason

    def record(self, key: str | None, reason: str) -> float:
        """Record a failure; returns the TTL applied (24 h, then 7 d on a repeat).

        The strike count survives expiry, which is what makes the second failure after
        a 24 h wait a *repeat*.
        """
        if not key:
            return 0.0
        previous = self._entries.pop(key, None)
        strikes = (previous.strikes if previous else 0) + 1
        ttl = self._first if strikes == 1 else self._repeat
        if len(self._entries) >= self._max:
            self._entries.pop(next(iter(self._entries)), None)
        self._entries[key] = _Entry(reason, self._clock() + ttl, strikes)
        return ttl

    def strikes(self, key: str) -> int:
        entry = self._entries.get(key)
        return entry.strikes if entry else 0

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


__all__ = ["FIRST_TTL_SECONDS", "REPEAT_TTL_SECONDS", "NegativeCache"]
