"""
Provider-aware token pacing + per-run usage accounting — Phase 32A TPM slice.

WHY THIS EXISTS — the staging Azure OpenAI deployment (``gpt-4.1-mini``,
GlobalStandard capacity 10 ≈ 10k tokens/minute) cannot absorb a full 8-agent
council (~48k tokens) fired back-to-back. The committee chair runs LAST and is
the LARGEST request (evidence pack + all prior agent summaries), so it is the
agent that repeatedly hits ``LLMRateLimitError`` — turning an infrastructure
limit into what looks like an evidence judgement. Reproduced live 3x on
staging (2026-08-22 product-readiness campaign).

This module provides the ONE shared pacing/accounting primitive all three
councils (company, discovery, field review) use — mirroring how
``retry_engine`` is the one shared retry primitive:

  * ``TokenBudgetPacer`` — a process-local sliding-window token budget for one
    provider deployment. ``acquire`` waits (bounded) until an estimated request
    fits under the configured tokens-per-minute capacity, reserving explicit
    headroom for the chair so earlier agents cannot starve it. It is
    ADVISORY by design: after the bounded wait it always lets the attempt
    proceed — the provider's own 429 (handled by the bounded retry engines) is
    the correctness backstop, so an imperfect token estimate can never wedge a
    council or skip an agent.
  * ``CouncilUsageTracker`` — per-run accounting (prompt/completion/total
    tokens, attempts, retries, 429 count, per-agent last error) feeding the
    ``*_run_summary`` structured log events and the chair
    failure-vs-judgement metadata.

Design constraints:
  * Single-event-loop cooperative concurrency (FastAPI BackgroundTasks + async
    jobs all share the app's loop): bookkeeping between ``await`` points needs
    no lock. Two councils in the same process share one pacer via
    ``get_shared_pacer`` — that is exactly what prevents a concurrent burst.
  * Never logs prompts, completions, or credentials. Token COUNTS only.
  * Deterministic and fully injectable (clock/sleeper) for tests — no test
    ever sleeps through a real TPM window.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# Fixed per-request overhead (message framing etc.) added to every estimate.
_REQUEST_OVERHEAD_TOKENS = 16


def estimate_tokens(text: str | None) -> int:
    """Cheap deterministic token estimate (~4 chars/token heuristic).

    Deliberately provider-agnostic and dependency-free (no tokenizer import).
    Correctness never depends on this being exact: the pacer is advisory and
    real usage is settled from the provider's own usage metadata afterwards.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_request_tokens(system: str, user: str, max_output_tokens: int) -> int:
    """Estimate the TPM cost of one request.

    Counts the FULL configured output budget, matching how Azure OpenAI's
    rate limiter admission-checks a request (prompt tokens + max_tokens).
    """
    return (
        estimate_tokens(system)
        + estimate_tokens(user)
        + max(0, int(max_output_tokens))
        + _REQUEST_OVERHEAD_TOKENS
    )


class _Entry:
    """One in-window token expenditure (mutable so it can be settled)."""

    __slots__ = ("at", "tokens")

    def __init__(self, at: float, tokens: int) -> None:
        self.at = at
        self.tokens = tokens


@dataclass
class PacerLease:
    """Handle returned by ``acquire``; settle it with the real token usage."""

    entry: _Entry
    waited_seconds: float = 0.0


class TokenBudgetPacer:
    """Sliding-window tokens-per-minute pacer for ONE provider deployment.

    ``capacity_tpm`` is the deployment's tokens-per-minute quota. A configured
    ``reserve_tokens`` slice is withheld from non-reserved requests so the
    chair (which passes ``use_reserve=True``) always finds headroom. The
    reserve is clamped to half the capacity — a larger reserve would be
    incoherent (ordinary agents could never run).
    """

    def __init__(
        self,
        *,
        capacity_tpm: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[Any]] | None = None,
    ) -> None:
        self._capacity = max(1, int(capacity_tpm))
        self._window = float(window_seconds)
        self._clock = clock
        self._sleeper = sleeper if sleeper is not None else asyncio.sleep
        self._entries: list[_Entry] = []
        # Cumulative wait imposed by this pacer (observability only).
        self.total_waited_seconds = 0.0

    @property
    def capacity_tpm(self) -> int:
        return self._capacity

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        self._entries = [e for e in self._entries if e.at > cutoff]

    def used_in_window(self, now: float | None = None) -> int:
        """Tokens spent (or provisionally leased) inside the current window."""
        current = self._clock() if now is None else now
        self._prune(current)
        return sum(e.tokens for e in self._entries)

    def _seconds_until_fits(self, needed: int, effective: int, now: float) -> float:
        """Shortest additional wait after which ``needed`` fits under ``effective``.

        Walks the window's entries oldest-first, accumulating the tokens each
        expiry frees, until the request fits. ``needed`` is pre-clamped by the
        caller to at most ``effective``, so this always terminates with a wait
        bounded by one full window.
        """
        used = sum(e.tokens for e in self._entries)
        freed = 0
        for entry in sorted(self._entries, key=lambda e: e.at):
            freed += entry.tokens
            if used - freed + needed <= effective:
                return max(0.0, (entry.at + self._window) - now)
        return max(0.0, self._window)

    async def acquire(
        self,
        estimated_tokens: int,
        *,
        reserve_tokens: int = 0,
        use_reserve: bool = False,
        max_wait_seconds: float = 0.0,
    ) -> PacerLease:
        """Wait (bounded) until the estimated request fits, then lease it.

        ADVISORY: when the bounded wait is exhausted the lease is granted
        anyway — the provider's 429 plus the bounded retry engine is the
        correctness backstop. This can overshoot the window (that is the
        provider's call to make), but it can never wedge a council, skip an
        agent, or loop unboundedly.
        """
        est = max(1, int(estimated_tokens))
        reserve = min(max(0, int(reserve_tokens)), self._capacity // 2)
        effective = self._capacity if use_reserve else self._capacity - reserve
        # A single request larger than the effective slice can never "fit";
        # wait for the best achievable headroom instead of forever.
        needed = min(est, effective)

        waited = 0.0
        while True:
            now = self._clock()
            self._prune(now)
            used = sum(e.tokens for e in self._entries)
            if used + needed <= effective:
                break
            remaining = max_wait_seconds - waited
            if remaining <= 0:
                break
            wait = min(self._seconds_until_fits(needed, effective, now), remaining)
            if wait <= 0:
                break
            await self._sleeper(wait)
            waited += wait

        entry = _Entry(at=self._clock(), tokens=est)
        self._entries.append(entry)
        self.total_waited_seconds += waited
        return PacerLease(entry=entry, waited_seconds=waited)

    def settle(self, lease: PacerLease, actual_total_tokens: int | None) -> None:
        """Replace a lease's estimated tokens with the provider-reported truth.

        ``None`` keeps the estimate (provider reported nothing). ``0`` is the
        correct settlement for a rate-limited request that consumed no quota.
        """
        if actual_total_tokens is None:
            return
        lease.entry.tokens = max(0, int(actual_total_tokens))


# ---------------------------------------------------------------------------
# Shared process-local pacer registry (one pacer per provider deployment)
# ---------------------------------------------------------------------------

_PACERS: dict[tuple[str, str, int], TokenBudgetPacer] = {}


def get_shared_pacer(
    provider: str | None,
    deployment: str | None,
    capacity_tpm: int,
) -> TokenBudgetPacer | None:
    """The process-wide pacer for one (provider, deployment) — or None.

    ``capacity_tpm <= 0`` disables pacing entirely (the configured default), so
    a plain deploy is byte-identical to the pre-slice behaviour. All councils
    in the process share the returned instance — that shared window is what
    stops two concurrent councils from bursting the same deployment.
    """
    if capacity_tpm <= 0 or not provider:
        return None
    key = (str(provider), str(deployment or ""), int(capacity_tpm))
    pacer = _PACERS.get(key)
    if pacer is None:
        pacer = TokenBudgetPacer(capacity_tpm=capacity_tpm)
        _PACERS[key] = pacer
    return pacer


def reset_shared_pacers() -> None:
    """Test hook: drop all shared pacers (never used in production code)."""
    _PACERS.clear()


# ---------------------------------------------------------------------------
# Per-run usage accounting
# ---------------------------------------------------------------------------


@dataclass
class AttemptRecord:
    """Token usage + outcome of ONE attempt (counts only — never text)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False
    error_type: str | None = None


@dataclass
class CouncilUsageTracker:
    """Per-council-run accounting for the run-summary event + chair semantics.

    Purely additive bookkeeping: never raises, never logs by itself, carries
    token COUNTS and error CLASS NAMES only.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    any_estimated: bool = False
    rate_limit_429_count: int = 0
    retry_attempts: int = 0
    attempts_by_agent: dict[str, int] = field(default_factory=dict)
    last_by_agent: dict[str, AttemptRecord] = field(default_factory=dict)
    paced_wait_seconds: float = 0.0

    def record_attempt(
        self,
        agent_name: str,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        estimated: bool,
        error_type: str | None,
        paced_wait_seconds: float = 0.0,
    ) -> None:
        # Any attempt after an agent's first is a retry, regardless of which
        # orchestration pass issued it.
        is_retry = self.attempts_by_agent.get(agent_name, 0) >= 1
        record = AttemptRecord(
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            total_tokens=int(total_tokens or 0),
            estimated=estimated,
            error_type=error_type,
        )
        self.prompt_tokens += record.prompt_tokens
        self.completion_tokens += record.completion_tokens
        self.total_tokens += record.total_tokens
        self.any_estimated = self.any_estimated or (
            estimated and record.total_tokens > 0
        )
        if error_type == "LLMRateLimitError":
            self.rate_limit_429_count += 1
        if is_retry:
            self.retry_attempts += 1
        self.attempts_by_agent[agent_name] = (
            self.attempts_by_agent.get(agent_name, 0) + 1
        )
        self.last_by_agent[agent_name] = record
        self.paced_wait_seconds += paced_wait_seconds

    def attempts_for(self, agent_name: str) -> int:
        return self.attempts_by_agent.get(agent_name, 0)

    def last_error_for(self, agent_name: str) -> str | None:
        record = self.last_by_agent.get(agent_name)
        return record.error_type if record is not None else None

    def summary_fields(self) -> dict[str, Any]:
        """Flat fields for a ``*_run_summary`` structured log event."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "tokens_estimated": self.any_estimated,
            "rate_limit_429_count": self.rate_limit_429_count,
            "retry_attempts": self.retry_attempts,
            "paced_wait_ms": int(self.paced_wait_seconds * 1000),
        }

    def usage_metadata(self) -> dict[str, Any]:
        """Bounded usage dict for a council result's metadata surface."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated": self.any_estimated,
            "rate_limit_429_count": self.rate_limit_429_count,
            "retry_attempts": self.retry_attempts,
            "paced_wait_ms": int(self.paced_wait_seconds * 1000),
        }
