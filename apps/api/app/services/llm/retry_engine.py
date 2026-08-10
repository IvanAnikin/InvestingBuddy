"""
Generic bounded-retry engine for LLM council orchestration — Phase 32A Slice 6A.

Phase 32A Slice 4 added bounded transient-error retry/backoff/wall-budget/
deterministic-fallback machinery to the single-COMPANY council
(``app.services.llm.council``). This module extracts the agent-shape-agnostic
core of that machinery so a SECOND orchestrator (the run-level discovery
council, ``app.services.llm.discovery_council``, wired in Slice 6A step 2) can
reuse the exact same bounded-retry behaviour instead of re-implementing it.

Everything here operates on a plain ``agent_name: str`` plus caller-supplied
callables — it never imports ``app.services.llm.schemas`` (or any other
company-specific agent-output schema) and never references a specific agent
name constant (e.g. ``AGENT_FINANCIAL_ANALYST``). A caller (``council.py`` or
``discovery_council.py``) supplies:

  - an ``attempt`` callable that runs ONE attempt for an agent and returns
    ``(output, issues, exc, duration_ms)`` (mirrors ``council._timed_attempt``);
  - small mutation/lookup callbacks (``append_output``, ``extend_warnings``,
    ``replace_agent``, ``status_of``, ``log_outcome``) so this module never
    touches a caller-specific result object directly;
  - an ``output_factory`` callable (e.g. the ``CouncilAgentOutput`` model
    class) so the synthetic budget-exhausted placeholder this module builds
    comes out as the right type for the caller. ``build_deterministic_synthesis``
    deliberately does NOT take an ``output_factory`` (see its docstring): the
    two callers' chair-equivalent output shapes (``committee_label`` +
    ``key_points``/``risks_or_gaps`` vs ``run_quality`` +
    ``candidate_notes``/``run_notes``) differ enough that baking one caller's
    field names into this shared module would leak a company-specific (or
    discovery-specific) shape back in — each caller builds its own final typed
    output from the generic ``DeterministicSynthesis`` this module returns;
  - the numeric budget/backoff config values as explicit parameters — this
    module never reads a ``Settings`` object.

What stays OUT of this module (by design):
  - the critical-agent set and retry-priority-order policy (caller-specific:
    the company council's depends on ``has_financial_evidence``; the discovery
    council's is a fixed set);
  - building the (system, user) LLM messages for an agent (imports
    ``app.services.llm.prompts`` / ``discovery_prompts``, caller-specific);
  - constructing a specific agent's *failed* placeholder for a real provider
    error (stays close to the schema-validation/sanitization path in each
    caller);
  - the caller's own final chair-fallback output shape (see above).

Logging here mirrors the pre-extraction ``council.py`` log events byte-for-byte
(same event names + fields) so this refactor is behavior-preserving. Never
logs prompts, completions, evidence excerpts, or credentials.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.structured_logging import log_event
from app.services.llm.client import LLMRateLimitError, is_transient_llm_error

# ``attempt(agent_name)`` runs ONE attempt and returns
# ``(output, issues, exc, duration_ms)`` — never raises (mirrors
# ``council._timed_attempt`` / ``council._run_agent_attempt``).
AttemptResult = tuple[Any, list[str], "Exception | None", int]
AttemptFn = Callable[[str], Awaitable[AttemptResult]]

# Replace a failed placeholder for ``agent_name`` in place with the recovered
# ``output`` (and refresh warnings with ``issues``) — mirrors
# ``council._replace_agent``.
ReplaceAgentFn = Callable[[str, Any, list[str]], None]

# Emit the safe completed/failed telemetry for one attempt — mirrors
# ``council._log_agent_outcome``. ``attempt_number`` is ``None`` for the
# initial-pass attempt, else the 1-based retry attempt number.
LogOutcomeFn = Callable[[str, Any, "Exception | None", int, "int | None"], None]

# Look up the CURRENT status string for ``agent_name`` (``None`` if absent).
StatusOfFn = Callable[[str], "str | None"]

AppendOutputFn = Callable[[Any], None]
ExtendWarningsFn = Callable[[list[str]], None]

# Builds the caller's agent-output type (e.g. ``CouncilAgentOutput``) from
# keyword fields — the one company-specific "shape" this module needs.
OutputFactoryFn = Callable[..., Any]

BudgetExhaustedFn = Callable[[str], Any]


async def retry_agent(
    agent_name: str,
    *,
    max_attempts: int,
    is_reserved: bool,
    attempt: AttemptFn,
    replace_agent: ReplaceAgentFn,
    log_outcome: LogOutcomeFn,
    log: logging.Logger,
    report_id: str | None,
    ticker: str | None,
    provider: str,
    council_version: str,
    deadline: float,
    reserve: float,
    clock: Callable[[], float],
    sleeper: Callable[[float], Awaitable[Any]],
    rng: random.Random,
    transient_failures: dict[str, Exception],
    base_backoff_seconds: float,
    max_backoff_seconds: float,
    max_retry_after_seconds: float,
    completed_status: str,
) -> None:
    """Bounded retry loop for ONE transiently-failed agent.

    Attempt caps, a total-deadline budget gate (with a reserve protecting
    ``is_reserved`` agents), a capped honored retry-after, and capped jittered
    exponential backoff make this strictly bounded — no uncontrolled loop. On
    success the placeholder is REPLACED in place (via ``replace_agent``); on
    exhaustion/permanent error the agent stays failed.

    Extracted verbatim (modulo parameterization) from
    ``council._retry_agent`` — Phase 32A Slice 4.
    """
    for attempt_number in range(1, max_attempts + 1):
        effective_deadline = deadline if is_reserved else deadline - reserve
        remaining = effective_deadline - clock()
        if remaining <= 0:
            log_event(
                log,
                "llm_agent_retry_skipped",
                level=logging.WARNING,
                report_id=report_id,
                ticker=ticker,
                agent_name=agent_name,
                provider=provider,
                council_version=council_version,
                attempt=attempt_number,
                reason="budget_exhausted",
            )
            return

        last_exc = transient_failures.get(agent_name)
        capped_retry_after: float | None = None
        if isinstance(last_exc, LLMRateLimitError) and last_exc.retry_after is not None:
            capped_retry_after = min(float(last_exc.retry_after), max_retry_after_seconds)
            wait = capped_retry_after
        else:
            backoff = min(
                base_backoff_seconds * (2 ** (attempt_number - 1)), max_backoff_seconds
            )
            wait = backoff + rng.uniform(0, base_backoff_seconds)
        wait = min(wait, remaining)
        if wait <= 0:
            return

        log_event(
            log,
            "llm_agent_retry",
            level=logging.WARNING,
            report_id=report_id,
            ticker=ticker,
            agent_name=agent_name,
            provider=provider,
            council_version=council_version,
            attempt=attempt_number,
            max_attempts=max_attempts,
            error_type=type(last_exc).__name__ if last_exc is not None else None,
            retry_after=capped_retry_after,
            backoff_ms=int(wait * 1000),
        )
        await sleeper(wait)

        output, issues, exc, duration_ms = await attempt(agent_name)
        if exc is None and getattr(output, "status", None) == completed_status:
            replace_agent(agent_name, output, issues)
            transient_failures.pop(agent_name, None)
            log_outcome(agent_name, output, None, duration_ms, attempt_number)
            return
        if exc is None:
            # Quarantined / unparsable on retry — a PERMANENT outcome. Keep the
            # honest failed result and stop retrying.
            replace_agent(agent_name, output, issues)
            transient_failures.pop(agent_name, None)
            log_outcome(agent_name, output, None, duration_ms, attempt_number)
            return
        log_outcome(agent_name, output, exc, duration_ms, attempt_number)
        if not is_transient_llm_error(exc):
            # Permanent provider error — stop; leave the failed placeholder.
            return
        # Transient again — record for the next backoff and keep looping.
        transient_failures[agent_name] = exc


async def run_with_retries(
    *,
    agent_order: tuple[str, ...],
    critical: frozenset[str],
    priority_order: list[str],
    reserved: frozenset[str],
    attempt: AttemptFn,
    append_output: AppendOutputFn,
    extend_warnings: ExtendWarningsFn,
    replace_agent: ReplaceAgentFn,
    status_of: StatusOfFn,
    log_outcome: LogOutcomeFn,
    budget_exhausted_output: BudgetExhaustedFn,
    log: logging.Logger,
    report_id: str | None,
    ticker: str | None,
    provider: str,
    council_version: str,
    clock: Callable[[], float],
    sleeper: Callable[[float], Awaitable[Any]],
    rng: random.Random,
    total_budget_seconds: float,
    critical_reserve_seconds: float,
    max_retries: int,
    critical_max_retries: int,
    base_backoff_seconds: float,
    max_backoff_seconds: float,
    max_retry_after_seconds: float,
    completed_status: str,
    failed_status: str,
    initial_pass_delay_seconds: float = 0.0,
) -> None:
    """The ON path: initial pass under a deadline + a priority retry pass.

    ``agent_order`` is attempted once each, in order, honoring the total
    ``deadline`` (an agent that cannot even START before the deadline gets
    ``budget_exhausted_output(agent_name)`` instead of a real attempt).
    Agents left transiently-failed are retried in ``priority_order``,
    each bounded by ``retry_agent`` — ``critical_max_retries`` extra attempts
    for agents in ``critical``, ``max_retries`` otherwise, with the
    ``critical_reserve_seconds`` reserve protecting agents in ``reserved``.

    ``initial_pass_delay_seconds`` (default ``0.0`` — OFF, so every existing
    caller is byte-identical) paces the INITIAL pass: a fixed wait between two
    consecutive agent attempts. The initial pass is already strictly sequential
    (no ``asyncio.gather``), but with no pacing every request fires the instant
    the previous one returns, which is what pushes a large council over a
    provider's short-window token/request-rate limits. The delay is never
    applied after the LAST agent, never when it would cross the ``deadline``,
    and never in the retry pass (which has its own jittered backoff).

    Extracted verbatim (modulo parameterization) from
    ``council._run_council_with_retries`` — Phase 32A Slice 4.
    """
    start = clock()
    deadline = start + total_budget_seconds
    reserve = critical_reserve_seconds
    transient_failures: dict[str, Exception] = {}

    # --- Initial pass: attempt every agent once, in order, honoring the deadline.
    last_index = len(agent_order) - 1
    for index, agent_name in enumerate(agent_order):
        if clock() >= deadline:
            placeholder = budget_exhausted_output(agent_name)
            append_output(placeholder)
            extend_warnings([f"{agent_name}: budget_exhausted"])
            log_event(
                log,
                "llm_agent_failed",
                level=logging.WARNING,
                report_id=report_id,
                ticker=ticker,
                agent_name=agent_name,
                provider=provider,
                council_version=council_version,
                duration_ms=0,
                status=failed_status,
                reason="budget_exhausted",
            )
            continue
        output, issues, exc, duration_ms = await attempt(agent_name)
        append_output(output)
        extend_warnings(issues)
        log_outcome(agent_name, output, exc, duration_ms, None)
        if exc is not None and is_transient_llm_error(exc):
            transient_failures[agent_name] = exc
        # Optional inter-agent pacing (see the docstring). Never after the last
        # agent, and never when the wait would eat into the deadline — the
        # budget belongs to real attempts, not to pacing.
        if (
            initial_pass_delay_seconds > 0
            and index < last_index
            and clock() + initial_pass_delay_seconds < deadline
        ):
            await sleeper(initial_pass_delay_seconds)

    # --- Retry pass: only transiently-failed agents, in priority order.
    for agent_name in priority_order:
        if agent_name not in transient_failures:
            continue
        current_status = status_of(agent_name)
        if current_status is None or current_status != failed_status:
            continue
        await retry_agent(
            agent_name,
            max_attempts=(
                critical_max_retries if agent_name in critical else max_retries
            ),
            is_reserved=agent_name in reserved,
            attempt=attempt,
            replace_agent=replace_agent,
            log_outcome=log_outcome,
            log=log,
            report_id=report_id,
            ticker=ticker,
            provider=provider,
            council_version=council_version,
            deadline=deadline,
            reserve=reserve,
            clock=clock,
            sleeper=sleeper,
            rng=rng,
            transient_failures=transient_failures,
            base_backoff_seconds=base_backoff_seconds,
            max_backoff_seconds=max_backoff_seconds,
            max_retry_after_seconds=max_retry_after_seconds,
            completed_status=completed_status,
        )


def build_budget_exhausted_output(
    agent_name: str,
    output_factory: OutputFactoryFn,
    *,
    failed_status: str,
) -> Any:
    """A failed placeholder for an agent that could not START before the deadline.

    Extracted verbatim from ``council._budget_exhausted_output`` — Phase 32A
    Slice 4. ``output_factory`` builds the caller's agent-output type (e.g.
    ``CouncilAgentOutput``) from these fields.
    """
    return output_factory(
        agent_name=agent_name,
        status=failed_status,
        summary="[Agent did not run: council time budget exhausted before it could start.]",
        safety_notes=["Agent did not run: council total time budget exhausted."],
    )


@dataclass(frozen=True)
class DeterministicSynthesis:
    """The generic, schema-agnostic result of ``build_deterministic_synthesis``.

    Deliberately carries only plain data (never a specific agent-output type)
    so this module stays agent-shape-agnostic (Phase 32A Slice 6A step 2): the
    caller (``council.py`` / ``discovery_council.py``) builds ITS OWN final
    output object from these fields, using whatever field names its own
    agent-output schema actually has (e.g. ``committee_label`` vs
    ``run_quality``) — that mapping never lives in this shared module.
    """

    completed: list[str]
    failed: list[str]
    summary: str
    safety_note: str


def build_deterministic_synthesis(
    agents: list[Any],
    order: tuple[str, ...],
    chair_agent_name: str,
    *,
    completed_status: str,
    failed_status: str,
    chair_role_label: str = "committee chair",
    summary_noun: str | None = None,
) -> DeterministicSynthesis:
    """A deterministic, non-consensus chair-role summary (req #11-12).

    Built only from ALREADY-VALIDATED stored agent outputs. It NEVER makes a
    recommendation, valuation conclusion, or numeric price objective: the
    returned ``summary``/``safety_note`` text deliberately avoids the forbidden
    safety substrings (e.g. "price target", "fair value") so it survives the
    citation/safety sanitizer *as-is*. The caller still owns building the final
    typed output (including any citation-bearing fields, which it must leave
    empty for a fallback to carry no citations, and an honest not-a-consensus
    label in its own vocabulary).

    ``chair_role_label`` names the chair-equivalent role in the "LLM ... did
    not complete/is unavailable" phrasing (default "committee chair"). The
    leading "Deterministic ... summary" noun defaults to the SAME label but
    can be overridden via ``summary_noun`` — the company council passes
    ``summary_noun="committee"`` to reproduce its pre-Slice-6A wording
    ("Deterministic committee summary ...") byte-for-byte, since that phrase
    was never symmetric with the "committee chair" used elsewhere in the same
    sentence; the discovery council uses the default (both slots say
    "discovery chair").

    Extracted (and generalized — Slice 6A step 2) from
    ``council._deterministic_chair_fallback`` — Phase 32A Slice 4.
    """
    summary_noun = summary_noun or chair_role_label
    completed = [
        a.agent_name
        for a in agents
        if a.status == completed_status and a.agent_name != chair_agent_name
    ]
    failed = [
        a.agent_name
        for a in agents
        if a.status == failed_status and a.agent_name != chair_agent_name
    ]
    non_chair_total = len(order) - 1
    summary = (
        f"Deterministic {summary_noun} summary (LLM {chair_role_label} "
        "unavailable). "
        f"{len(completed)} of {non_chair_total} non-chair council agents completed. "
        f"Completed: {', '.join(completed) or 'none'}. "
        f"Did not complete: {', '.join(failed) or 'none'}. "
        "This is a partial, non-consensus internal summary. It states no "
        "recommendation, no valuation conclusion, and no numeric price objective. "
        "Further evidence and human review are required."
    )
    safety_note = (
        f"Deterministic fallback: the LLM {chair_role_label} did not complete. No "
        "consensus, no recommendation, and no valuation conclusion is implied; "
        "human review is required."
    )
    return DeterministicSynthesis(
        completed=completed, failed=failed, summary=summary, safety_note=safety_note
    )
