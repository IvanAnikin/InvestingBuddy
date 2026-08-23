"""
DEEP FIELD REVIEW council orchestrator — Phase 32A Slice 6D.

Runs the eight COMPARATIVE agents in order over a bounded field pack, enforces
citations + safety on every agent's output, aggregates the field chair's
research-priority verdict, and returns a ``FieldReviewResult`` with honest
metadata. A single agent failing (timeout, malformed JSON, provider error) is
isolated: that agent is marked ``failed`` and the review still returns.

This is the THIRD council in the codebase and must not be confused with the
other two: it compares the ALREADY-PERSISTED deep analyses of 2+ companies from
ONE discovery run. It never re-analyses a company, never fetches data, and never
produces a rating, price target, fair value, or return projection.

Retries are BOUNDED (Slice 4 discipline, re-implemented here against this
council's own schemas rather than shared, because the two councils have
different agent sets and different budgets): an initial pass under a total
deadline, then a priority-ordered retry pass for TRANSIENTLY-failed agents only
(429 / 5xx / timeout, classified by ``client.is_transient_llm_error``), honoring
a capped provider ``retry-after`` with capped jittered exponential backoff, with
a reserve protecting ``field_red_team`` + ``field_chair``. There is no unbounded
loop anywhere.

If the field chair itself still does not complete after its retries, a
DETERMINISTIC field-chair synthesis is attached (``chair_fallback_used`` +
``deterministic_field_chair``) so a partial council degrades to an honest,
non-fabricating explanation instead of three silently-empty priority buckets.
The failed LLM chair entry stays visible in ``agents``.

Logging is structured and safe (Phase 27.1D): it records ids, provider/model
names, statuses, counts and durations — never prompts, completions, pack
excerpts, or credentials.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.structured_logging import log_event
from app.services.llm import field_review_prompts as prompts
from app.services.llm import retry_engine
from app.services.llm.client import (
    LLMClient,
    LLMError,
    LLMRateLimitError,
    get_llm_client,
    is_transient_llm_error,
)
from app.services.llm.field_review_citation_checker import check_and_sanitize
from app.services.llm.field_review_schemas import (
    AGENT_FIELD_CHAIR,
    AGENT_FIELD_RED_TEAM,
    ALLOWED_FIELD_QUALITY,
    DEFAULT_FIELD_QUALITY,
    FIELD_CRITICAL_AGENTS,
    FIELD_RESERVED_AGENTS,
    FIELD_REVIEW_AGENT_ORDER,
    STATUS_COMPLETED,
    STATUS_FAILED,
    FieldChairVerdict,
    FieldReviewAgentOutput,
    FieldReviewPack,
    FieldReviewResult,
)
from app.services.llm.token_pacer import (
    CouncilUsageTracker,
    TokenBudgetPacer,
    estimate_request_tokens,
    get_shared_pacer,
)

__all__ = [
    "get_field_review_llm_client",
    "field_review_council_enabled",
    "run_field_review_council",
    "maybe_run_field_review_council",
]

_logger = logging.getLogger("app.services.llm.field_review_council")

_MAX_AGG_LIST = 30

# Agents that receive the prior agents' (already-safety-scanned) summaries.
_SYNTHESIS_AGENTS = frozenset({AGENT_FIELD_RED_TEAM, AGENT_FIELD_CHAIR})


def field_review_council_enabled(settings: Settings | None = None) -> bool:
    """True only when BOTH the shared client gate and the field-review gate are on."""
    cfg = settings or default_settings
    return bool(cfg.llm_council_enabled and cfg.llm_field_review_council_enabled)


def get_field_review_llm_client(settings: Settings | None = None) -> LLMClient | None:
    """Resolve a field-review client, or None when disabled/unavailable.

    Reuses ``get_llm_client`` for the shared flag/provider/credential logic, then
    adds the ``llm_field_review_council_enabled`` gate and swaps the 28A fake
    client for the field-review-shaped fake client. Never raises.
    """
    cfg = settings or default_settings
    if not cfg.llm_field_review_council_enabled:
        return None
    base = get_llm_client(cfg)
    if base is None:
        return None
    if base.is_fake:
        from app.services.llm.fake_field_review_client import (
            FakeFieldReviewLLMClient,
        )

        return FakeFieldReviewLLMClient()
    return base


# ---------------------------------------------------------------------------
# Output coercion + helpers
# ---------------------------------------------------------------------------


def _coerce_output(agent_name: str, raw: dict[str, Any]) -> FieldReviewAgentOutput:
    """Validate the model's dict into the agent output, tolerating drift.

    ``agent_name`` is always forced to the expected value — never trusted from
    the model — so an agent cannot impersonate another in the merged review.
    """
    payload = dict(raw) if isinstance(raw, dict) else {}
    payload["agent_name"] = agent_name
    try:
        return FieldReviewAgentOutput.model_validate(payload)
    except Exception:  # noqa: BLE001 - any validation drift becomes a failed agent
        return FieldReviewAgentOutput(
            agent_name=agent_name,
            status=STATUS_FAILED,
            summary="[Agent output could not be parsed into the required schema.]",
            safety_notes=["Malformed structured output rejected."],
        )


def _prior_summaries(outputs: list[FieldReviewAgentOutput]) -> str:
    lines = []
    for o in outputs:
        if o.status == STATUS_FAILED:
            continue
        summary = (o.summary or "").strip()
        if summary:
            lines.append(f"- {o.agent_name}: {summary}")
    return "\n".join(lines)


def _is_safety_quarantine(issues: list[str]) -> bool:
    return any("quarantined by safety gate" in i for i in issues)


def _dedupe(items: list[str], limit: int = _MAX_AGG_LIST) -> list[str]:
    seen: dict[str, None] = {}
    for it in items:
        s = (it or "").strip()
        if s:
            seen.setdefault(s, None)
    return list(seen)[:limit]


def _messages_for(
    agent_name: str, pack_json: str, result: FieldReviewResult
) -> tuple[str, str]:
    """Build (system, user) for one agent from the CURRENT council state.

    A synthesis agent's user message is rebuilt from the current (possibly
    recovered) prior summaries every time it is called, so a retried chair
    synthesizes over agents that recovered in the retry pass.
    """
    if agent_name == AGENT_FIELD_CHAIR:
        system = prompts.field_chair_system_prompt()
    else:
        system = prompts.system_prompt_for(agent_name)
    if agent_name in _SYNTHESIS_AGENTS:
        user = prompts.build_user_message(pack_json, _prior_summaries(result.agents))
    else:
        user = prompts.build_user_message(pack_json)
    return system, user


# ---------------------------------------------------------------------------
# Single-attempt primitive + telemetry
# ---------------------------------------------------------------------------


async def _run_agent_attempt(
    agent_name: str,
    pack_json: str,
    evidence_ids: set[str],
    company_ids: set[str],
    result: FieldReviewResult,
    client: LLMClient,
    cfg: Settings,
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> tuple[FieldReviewAgentOutput, list[str], Exception | None]:
    """Run ONE attempt for an agent. Never raises.

    Returns ``(output, issues, exc)``. On success ``output`` is the sanitized
    agent output and ``exc`` is None (``output.status`` may still be ``failed``
    if the safety gate quarantined it — a PERMANENT outcome). On an ``LLMError``
    ``output`` is the failed placeholder and ``exc`` is the (possibly transient)
    exception. ``known_gaps`` (the run's ``FieldReviewPack.known_gaps``) enables
    the gap-attribution grounding check (corrective, post-#99/#100).
    """
    system, user = _messages_for(agent_name, pack_json, result)
    # Phase 32A TPM slice: advisory provider-aware pacing shared with the other
    # two councils (same deployment, same window); the chair draws on its
    # reserved slice.
    lease = None
    paced_wait = 0.0
    if pacer is not None:
        lease = await pacer.acquire(
            estimate_request_tokens(system, user, cfg.llm_max_output_tokens),
            reserve_tokens=cfg.llm_council_chair_token_reserve,
            use_reserve=(agent_name == AGENT_FIELD_CHAIR),
            max_wait_seconds=cfg.llm_council_pacing_max_wait_seconds,
        )
        paced_wait = lease.waited_seconds
    try:
        raw = await client.complete_json(
            system,
            user,
            max_tokens=cfg.llm_max_output_tokens,
            temperature=cfg.llm_temperature,
            timeout=cfg.llm_request_timeout_seconds,
            repair_instruction=prompts.REPAIR_INSTRUCTION,
        )
    except LLMError as exc:
        usage = client.consume_usage()
        if pacer is not None and lease is not None:
            if isinstance(exc, LLMRateLimitError):
                pacer.settle(lease, usage.total_tokens if usage else 0)
            else:
                pacer.settle(lease, usage.total_tokens if usage else None)
        if tracker is not None:
            tracker.record_attempt(
                agent_name,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                estimated=bool(usage.estimated) if usage else False,
                error_type=type(exc).__name__,
                paced_wait_seconds=paced_wait,
            )
        placeholder = FieldReviewAgentOutput(
            agent_name=agent_name,
            status=STATUS_FAILED,
            summary="[Agent did not complete: provider error or timeout.]",
            safety_notes=[f"Agent failed ({type(exc).__name__})."],
        )
        return placeholder, [f"{agent_name}: {type(exc).__name__}"], exc
    usage = client.consume_usage()
    if pacer is not None and lease is not None:
        pacer.settle(lease, usage.total_tokens if usage else None)
    if tracker is not None:
        tracker.record_attempt(
            agent_name,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            estimated=bool(usage.estimated) if usage else False,
            error_type=None,
            paced_wait_seconds=paced_wait,
        )
    output = _coerce_output(agent_name, raw)
    sanitized, issues = check_and_sanitize(
        output,
        evidence_ids,
        company_ids,
        is_chair=agent_name == AGENT_FIELD_CHAIR,
        known_gaps=known_gaps,
    )
    return sanitized, issues, None


async def _timed_attempt(
    agent_name: str,
    pack_json: str,
    evidence_ids: set[str],
    company_ids: set[str],
    result: FieldReviewResult,
    client: LLMClient,
    cfg: Settings,
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> tuple[FieldReviewAgentOutput, list[str], Exception | None, int]:
    """``_run_agent_attempt`` plus a wall-clock duration_ms for logging."""
    started = time.perf_counter()
    output, issues, exc = await _run_agent_attempt(
        agent_name,
        pack_json,
        evidence_ids,
        company_ids,
        result,
        client,
        cfg,
        known_gaps,
        pacer,
        tracker,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    return output, issues, exc, duration_ms


def _log_agent_outcome(
    log: logging.Logger,
    agent_name: str,
    output: FieldReviewAgentOutput,
    exc: Exception | None,
    duration_ms: int,
    *,
    cfg: Settings,
    client: LLMClient,
    field_review_run_id: str | None,
    attempt: int | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> None:
    """Emit the safe completed/failed telemetry for one attempt (no prompts)."""
    # Phase 32A TPM slice: per-attempt token accounting (counts only).
    last = tracker.last_by_agent.get(agent_name) if tracker is not None else None
    usage_fields: dict[str, Any] = {}
    if last is not None:
        usage_fields = {
            "prompt_tokens": last.prompt_tokens,
            "completion_tokens": last.completion_tokens,
            "total_tokens": last.total_tokens,
            "tokens_estimated": last.estimated or None,
        }
    if exc is not None:
        log_event(
            log,
            "field_review_agent_failed",
            level=logging.WARNING,
            field_review_run_id=field_review_run_id,
            agent_name=agent_name,
            provider=client.provider_name,
            council_version=cfg.llm_field_review_council_version,
            duration_ms=duration_ms,
            status=STATUS_FAILED,
            reason=type(exc).__name__,
            attempt=attempt,
            retry_after_seconds=getattr(exc, "retry_after", None),
            **usage_fields,
        )
    elif output.status == STATUS_FAILED:
        log_event(
            log,
            "field_review_agent_failed",
            level=logging.WARNING,
            field_review_run_id=field_review_run_id,
            agent_name=agent_name,
            provider=client.provider_name,
            council_version=cfg.llm_field_review_council_version,
            duration_ms=duration_ms,
            status=STATUS_FAILED,
            reason="quarantined_or_unparsed",
            attempt=attempt,
            **usage_fields,
        )
    else:
        log_event(
            log,
            "field_review_agent_completed",
            field_review_run_id=field_review_run_id,
            agent_name=agent_name,
            provider=client.provider_name,
            council_version=cfg.llm_field_review_council_version,
            duration_ms=duration_ms,
            status=output.status,
            company_note_count=len(output.company_notes),
            attempt=attempt,
            **usage_fields,
        )


def _budget_exhausted_output(agent_name: str) -> FieldReviewAgentOutput:
    """A failed placeholder for an agent that could not START before the deadline."""
    return FieldReviewAgentOutput(
        agent_name=agent_name,
        status=STATUS_FAILED,
        summary=(
            "[Agent did not run: field review time budget exhausted before it "
            "could start.]"
        ),
        safety_notes=["Agent did not run: field review total time budget exhausted."],
    )


def _replace_agent(
    result: FieldReviewResult,
    agent_name: str,
    output: FieldReviewAgentOutput,
    issues: list[str],
) -> None:
    """Replace an agent's placeholder in place, preserving council order."""
    for index, existing in enumerate(result.agents):
        if existing.agent_name == agent_name:
            result.agents[index] = output
            break
    result.warnings.extend(issues)


def _deterministic_field_chair_fallback(
    agents: list[FieldReviewAgentOutput], order: tuple[str, ...]
) -> FieldReviewAgentOutput:
    """A deterministic, non-consensus FIELD-chair synthesis.

    Built only from ALREADY-VALIDATED stored agent outputs. It NEVER fabricates
    a ranking, a consensus, or a recommendation: all THREE priority buckets stay
    EMPTY (so the fallback carries no citations and places no company anywhere),
    the label is the honest ``field_quality="failed"``, and
    ``field_uncertainties`` states plainly that the chair could not complete,
    that no ranking exists, and that human review is required.

    Empty ``blocked_insufficient_evidence`` is deliberate: that bucket means
    "THIS company's own evidence was insufficient", which is a different — and
    here untrue — claim. The chair failing says nothing about any company's
    evidence, so no company is placed there.

    Thin field-review-specific adapter (mirroring
    ``council._deterministic_chair_fallback`` and
    ``discovery_council._deterministic_chair_fallback``) over
    ``retry_engine.build_deterministic_synthesis``: the shared engine returns
    the generic completed/failed prose, and THIS function builds the
    field-review-shaped output, because the three-bucket ``FieldChairVerdict``
    is specific to this council.

    The summary keeps the siblings' sentence skeleton but is composed here so it
    can name what this council's agents actually are ("comparative agents") and
    what is actually missing ("no ranking") instead of the company council's
    "no numeric price objective" — this council never emits a number at all. The
    safety note is the shared engine's, unchanged.
    """
    synthesis = retry_engine.build_deterministic_synthesis(
        agents,
        order,
        AGENT_FIELD_CHAIR,
        completed_status=STATUS_COMPLETED,
        failed_status=STATUS_FAILED,
        chair_role_label="field chair",
    )
    completed = ", ".join(synthesis.completed) or "none"
    failed = ", ".join(synthesis.failed) or "none"
    summary = (
        "Deterministic field chair summary (LLM field chair unavailable). "
        f"{len(synthesis.completed)} of {len(order) - 1} comparative agents "
        f"completed. Completed: {completed}. Did not complete: {failed}. "
        "This is a partial, non-consensus internal summary. It states no "
        "recommendation, no valuation conclusion, and no ranking. Further "
        "evidence and human review are required."
    )
    verdict = FieldChairVerdict(
        strongest_candidates=[],
        second_tier=[],
        blocked_insufficient_evidence=[],
        field_uncertainties=[
            "The LLM field chair did not complete, so NO comparative ranking "
            "or research-priority ordering was produced. All three priority "
            "buckets are empty because none could be produced honestly — not "
            "because any company was assessed and set aside.",
            "Comparative agents that completed (their summaries remain "
            f"available and usable): {completed}.",
            f"Comparative agents that did not complete: {failed}.",
            "Human review of the completed comparative agent summaries is "
            "required before any prioritization is drawn from this run.",
        ],
        field_quality="failed",
    )
    return FieldReviewAgentOutput(
        agent_name=AGENT_FIELD_CHAIR,
        status=STATUS_COMPLETED,
        summary=summary,
        company_notes=[],
        field_notes=[],
        evidence_gaps=[],
        unsupported_claims=[],
        safety_notes=[synthesis.safety_note],
        next_research_tasks=[],
        chair_verdict=verdict,
    )


def _retry_priority_order() -> list[str]:
    """Order transiently-failed agents are retried in (chair last)."""
    order = [
        a
        for a in FIELD_REVIEW_AGENT_ORDER
        if a in FIELD_CRITICAL_AGENTS and a not in FIELD_RESERVED_AGENTS
    ]
    order.extend(
        a
        for a in FIELD_REVIEW_AGENT_ORDER
        if a not in FIELD_CRITICAL_AGENTS and a not in FIELD_RESERVED_AGENTS
    )
    order.append(AGENT_FIELD_RED_TEAM)
    order.append(AGENT_FIELD_CHAIR)
    return order


# ---------------------------------------------------------------------------
# Bounded retry
# ---------------------------------------------------------------------------


async def _retry_agent(
    agent_name: str,
    *,
    is_critical: bool,
    is_reserved: bool,
    pack_json: str,
    evidence_ids: set[str],
    company_ids: set[str],
    result: FieldReviewResult,
    client: LLMClient,
    cfg: Settings,
    log: logging.Logger,
    field_review_run_id: str | None,
    deadline: float,
    reserve: float,
    clock: Callable[[], float],
    sleeper: Callable[[float], Awaitable[Any]],
    rng: random.Random,
    transient_failures: dict[str, Exception],
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> None:
    """Bounded retry loop for ONE transiently-failed agent.

    Attempt caps, a total-deadline budget gate (with a reserve protecting the two
    RESERVED agents), a capped honored retry-after, and capped jittered
    exponential backoff make this strictly bounded — no uncontrolled loop. On
    success the placeholder is REPLACED in place; on exhaustion/permanent error
    the agent stays failed.
    """
    max_extra = (
        cfg.llm_field_review_council_critical_max_retries
        if is_critical
        else cfg.llm_field_review_council_max_retries
    )
    base = cfg.llm_field_review_council_retry_base_backoff_seconds
    max_backoff = cfg.llm_field_review_council_retry_max_backoff_seconds
    max_retry_after = cfg.llm_field_review_council_retry_max_retry_after_seconds

    for attempt in range(1, max_extra + 1):
        effective_deadline = deadline if is_reserved else deadline - reserve
        remaining = effective_deadline - clock()
        if remaining <= 0:
            log_event(
                log,
                "field_review_agent_retry_skipped",
                level=logging.WARNING,
                field_review_run_id=field_review_run_id,
                agent_name=agent_name,
                provider=client.provider_name,
                council_version=cfg.llm_field_review_council_version,
                attempt=attempt,
                reason="budget_exhausted",
            )
            return

        last_exc = transient_failures.get(agent_name)
        capped_retry_after: float | None = None
        if isinstance(last_exc, LLMRateLimitError) and last_exc.retry_after is not None:
            capped_retry_after = min(float(last_exc.retry_after), max_retry_after)
            wait = capped_retry_after
        else:
            backoff = min(base * (2 ** (attempt - 1)), max_backoff)
            wait = backoff + rng.uniform(0, base)
        wait = min(wait, remaining)
        if wait <= 0:
            return

        log_event(
            log,
            "field_review_agent_retry",
            level=logging.WARNING,
            field_review_run_id=field_review_run_id,
            agent_name=agent_name,
            provider=client.provider_name,
            council_version=cfg.llm_field_review_council_version,
            attempt=attempt,
            max_attempts=max_extra,
            error_type=type(last_exc).__name__ if last_exc is not None else None,
            retry_after=capped_retry_after,
            backoff_ms=int(wait * 1000),
        )
        await sleeper(wait)

        output, issues, exc, duration_ms = await _timed_attempt(
            agent_name,
            pack_json,
            evidence_ids,
            company_ids,
            result,
            client,
            cfg,
            known_gaps,
            pacer,
            tracker,
        )
        if exc is None:
            # Completed OR quarantined/unparsable — both are PERMANENT outcomes.
            # Record the honest result and stop retrying either way.
            _replace_agent(result, agent_name, output, issues)
            transient_failures.pop(agent_name, None)
            _log_agent_outcome(
                log,
                agent_name,
                output,
                None,
                duration_ms,
                cfg=cfg,
                client=client,
                field_review_run_id=field_review_run_id,
                attempt=attempt,
                tracker=tracker,
            )
            return
        _log_agent_outcome(
            log,
            agent_name,
            output,
            exc,
            duration_ms,
            cfg=cfg,
            client=client,
            field_review_run_id=field_review_run_id,
            attempt=attempt,
            tracker=tracker,
        )
        if not is_transient_llm_error(exc):
            # Permanent provider error — stop; leave the failed placeholder.
            return
        # Transient again — record for the next backoff and keep looping.
        transient_failures[agent_name] = exc


# ---------------------------------------------------------------------------
# Council passes
# ---------------------------------------------------------------------------


async def _run_single_pass(
    *,
    pack_json: str,
    evidence_ids: set[str],
    company_ids: set[str],
    result: FieldReviewResult,
    client: LLMClient,
    cfg: Settings,
    log: logging.Logger,
    field_review_run_id: str | None,
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> None:
    """The retry-OFF path: one attempt per agent, no retries."""
    for agent_name in FIELD_REVIEW_AGENT_ORDER:
        output, issues, exc, duration_ms = await _timed_attempt(
            agent_name,
            pack_json,
            evidence_ids,
            company_ids,
            result,
            client,
            cfg,
            known_gaps,
            pacer,
            tracker,
        )
        result.agents.append(output)
        result.warnings.extend(issues)
        _log_agent_outcome(
            log,
            agent_name,
            output,
            exc,
            duration_ms,
            cfg=cfg,
            client=client,
            field_review_run_id=field_review_run_id,
            tracker=tracker,
        )


async def _run_with_retries(
    *,
    pack_json: str,
    evidence_ids: set[str],
    company_ids: set[str],
    result: FieldReviewResult,
    client: LLMClient,
    cfg: Settings,
    log: logging.Logger,
    field_review_run_id: str | None,
    clock: Callable[[], float],
    sleeper: Callable[[float], Awaitable[Any]],
    rng: random.Random,
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> None:
    """The retry-ON path: initial pass under a deadline + a priority retry pass."""
    deadline = clock() + cfg.llm_field_review_council_total_budget_seconds
    reserve = cfg.llm_field_review_council_critical_reserve_seconds
    transient_failures: dict[str, Exception] = {}

    for agent_name in FIELD_REVIEW_AGENT_ORDER:
        if clock() >= deadline:
            result.agents.append(_budget_exhausted_output(agent_name))
            result.warnings.append(f"{agent_name}: budget_exhausted")
            log_event(
                log,
                "field_review_agent_failed",
                level=logging.WARNING,
                field_review_run_id=field_review_run_id,
                agent_name=agent_name,
                provider=client.provider_name,
                council_version=cfg.llm_field_review_council_version,
                duration_ms=0,
                status=STATUS_FAILED,
                reason="budget_exhausted",
            )
            continue
        output, issues, exc, duration_ms = await _timed_attempt(
            agent_name,
            pack_json,
            evidence_ids,
            company_ids,
            result,
            client,
            cfg,
            known_gaps,
            pacer,
            tracker,
        )
        result.agents.append(output)
        result.warnings.extend(issues)
        _log_agent_outcome(
            log,
            agent_name,
            output,
            exc,
            duration_ms,
            cfg=cfg,
            client=client,
            field_review_run_id=field_review_run_id,
            tracker=tracker,
        )
        if exc is not None and is_transient_llm_error(exc):
            transient_failures[agent_name] = exc

    for agent_name in _retry_priority_order():
        if agent_name not in transient_failures:
            continue
        entry = next((a for a in result.agents if a.agent_name == agent_name), None)
        if entry is None or entry.status != STATUS_FAILED:
            continue
        await _retry_agent(
            agent_name,
            is_critical=agent_name in FIELD_CRITICAL_AGENTS,
            is_reserved=agent_name in FIELD_RESERVED_AGENTS,
            pack_json=pack_json,
            evidence_ids=evidence_ids,
            company_ids=company_ids,
            result=result,
            client=client,
            cfg=cfg,
            log=log,
            field_review_run_id=field_review_run_id,
            deadline=deadline,
            reserve=reserve,
            clock=clock,
            sleeper=sleeper,
            rng=rng,
            transient_failures=transient_failures,
            known_gaps=known_gaps,
            pacer=pacer,
            tracker=tracker,
        )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------



def _chair_failure_reason(attempts: int, last_error: str | None) -> str:
    """Why the chair did not complete — never ``None`` when it failed.

    Phase 32A TPM corrective (live staging, 2026-08-23): a chair that never got
    an attempt (the wall budget was exhausted before its turn) recorded NO
    error, so the failure surfaced as an empty ``chair_error_type`` — reading
    like "no error" next to a failure-default label. The three outcomes are now
    always distinguishable:

      * ``budget_exhausted``       — never ran; council wall budget ran out.
      * a provider error class     — ran and failed transiently/permanently
                                     (e.g. ``LLMRateLimitError``).
      * ``quarantined_or_unparsed``— ran and returned, but the safety/schema
                                     gate rejected the output (a CONTENT
                                     outcome, not an infrastructure one).
    """
    if last_error:
        return last_error
    return "budget_exhausted" if attempts == 0 else "quarantined_or_unparsed"

def _aggregate_chair(
    pack: FieldReviewPack, chair: FieldReviewAgentOutput | None
) -> dict[str, list[dict[str, Any]]]:
    """Turn the chair's verdict into the three persisted priority buckets.

    Ticker/exchange are resolved from the PACK (the authoritative persisted
    identity), never from whatever the model echoed back.
    """
    buckets: dict[str, list[dict[str, Any]]] = {
        "strongest_candidates": [],
        "second_tier": [],
        "blocked_insufficient_evidence": [],
    }
    if chair is None or chair.chair_verdict is None:
        return buckets
    for tier, entry in chair.chair_verdict.entries():
        company = pack.company_by_id(entry.company_ref)
        buckets[tier].append(
            {
                "company_ref": entry.company_ref,
                "discovery_candidate_id": (
                    company.discovery_candidate_id if company else None
                ),
                "report_id": company.report_id if company else None,
                "ticker": company.ticker if company else entry.ticker,
                "exchange": company.exchange if company else entry.exchange,
                "rationale": entry.rationale,
                "citation_ids": list(entry.citation_ids),
                "confidence": entry.confidence,
                # The company's own honest caveats are ALWAYS carried, in
                # addition to whatever the chair added, so a mock-provenance
                # company can never be presented as clean.
                "caveats": _dedupe(
                    [*(company.caveats if company else []), *entry.caveats],
                    limit=10,
                ),
            }
        )
    return buckets


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def run_field_review_council(
    pack: FieldReviewPack,
    client: LLMClient,
    *,
    cfg: Settings | None = None,
    field_review_run_id: str | None = None,
    logger: logging.Logger | None = None,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], Awaitable[Any]] | None = None,
    rng: random.Random | None = None,
    pacer: TokenBudgetPacer | None = None,
) -> FieldReviewResult:
    """Run every field-review agent over the pack and return the result.

    ``clock`` / ``sleeper`` / ``rng`` are injectable so tests can drive the
    budget and backoff deterministically (a fake clock advanced by a fake
    sleeper) without real waiting.
    """
    cfg = cfg or default_settings
    log = logger or _logger
    clock = clock or time.monotonic
    sleeper = sleeper or asyncio.sleep
    rng = rng or random.Random()
    run_started = clock()
    # Phase 32A TPM slice: per-run usage accounting + the process-shared
    # provider pacer (None when ``llm_council_tpm_capacity`` is 0 — default).
    tracker = CouncilUsageTracker()
    if pacer is None:
        pacer = get_shared_pacer(
            client.provider_name,
            client.deployment_name,
            cfg.llm_council_tpm_capacity,
        )

    evidence_ids = pack.evidence_ids()
    company_ids = pack.company_ids()
    pack_json = pack.model_dump_json()
    # Corrective (post-#99/#100): the run's own structured gap state, so the
    # citation checker's gap-attribution grounding check can tell a genuine
    # cause from an invented one.
    known_gaps = pack.known_gaps

    result = FieldReviewResult(
        council_version=cfg.llm_field_review_council_version,
        llm_used=True,
        provider=client.provider_name,
        model=client.model_name,
        deployment=client.deployment_name,
        pack_version=pack.pack_version,
        item_count=pack.item_count,
        company_count=pack.company_count,
    )

    log_event(
        log,
        "field_review_council_started",
        field_review_run_id=field_review_run_id,
        provider=client.provider_name,
        model=client.model_name,
        council_version=cfg.llm_field_review_council_version,
        item_count=pack.item_count,
        company_count=pack.company_count,
    )

    if cfg.llm_field_review_council_retry_enabled:
        await _run_with_retries(
            pack_json=pack_json,
            evidence_ids=evidence_ids,
            company_ids=company_ids,
            result=result,
            client=client,
            cfg=cfg,
            log=log,
            field_review_run_id=field_review_run_id,
            clock=clock,
            sleeper=sleeper,
            rng=rng,
            known_gaps=known_gaps,
            pacer=pacer,
            tracker=tracker,
        )
    else:
        await _run_single_pass(
            pack_json=pack_json,
            evidence_ids=evidence_ids,
            company_ids=company_ids,
            result=result,
            client=client,
            cfg=cfg,
            log=log,
            field_review_run_id=field_review_run_id,
            known_gaps=known_gaps,
            pacer=pacer,
            tracker=tracker,
        )

    result.recount()
    result.safety_valid = not _is_safety_quarantine(result.warnings)

    chair = next(
        (a for a in result.agents if a.agent_name == AGENT_FIELD_CHAIR), None
    )

    # When the retry bundle is on and the LLM field chair STILL did not complete,
    # attach a DETERMINISTIC, non-consensus field-chair synthesis so the review
    # has an honest explanation to render instead of three silently-empty
    # priority buckets (the Slice 6D defect this fixes) — without inventing a
    # ranking, a placement, or a recommendation. The FAILED LLM chair entry is
    # KEPT in ``agents`` untouched (so the counts + warnings still show the
    # council is visibly partial) and is excluded from the fallback's own
    # completed/failed tallies; the fallback is attached separately and never
    # flips ``human_review_required`` / ``publication_ready``. Mirrors
    # ``discovery_council`` (Slice 6A) and ``council`` (Slice 4).
    chair_for_verdict = chair
    if cfg.llm_field_review_council_retry_enabled and (
        chair is None or chair.status != STATUS_COMPLETED
    ):
        fallback = _deterministic_field_chair_fallback(
            result.agents, FIELD_REVIEW_AGENT_ORDER
        )
        # Defense-in-depth: the fallback goes through the SAME safety/citation
        # gate every real agent output does, even though it is machine-built.
        sanitized_fallback, _fb_issues = check_and_sanitize(
            fallback, evidence_ids, company_ids, is_chair=True, known_gaps=known_gaps
        )
        result.deterministic_field_chair = sanitized_fallback.to_dict()
        result.chair_fallback_used = True
        chair_for_verdict = sanitized_fallback
        log_event(
            log,
            "field_review_chair_fallback",
            level=logging.WARNING,
            field_review_run_id=field_review_run_id,
            provider=client.provider_name,
            council_version=cfg.llm_field_review_council_version,
            agents_completed=result.agents_completed,
            agents_failed=result.agents_failed,
        )

    verdict = (
        chair_for_verdict.chair_verdict if chair_for_verdict is not None else None
    )
    quality = verdict.field_quality if verdict is not None else None
    if quality not in ALLOWED_FIELD_QUALITY:
        # The chair did not (or could not — e.g. it failed) set a valid label.
        # Fall back to an honest field_quality that is always in the allowed set.
        quality = DEFAULT_FIELD_QUALITY if result.agents_completed else "failed"
    result.field_quality = quality

    buckets = _aggregate_chair(pack, chair_for_verdict)
    result.strongest_candidates = buckets["strongest_candidates"][:_MAX_AGG_LIST]
    result.second_tier = buckets["second_tier"][:_MAX_AGG_LIST]
    result.blocked_insufficient_evidence = buckets["blocked_insufficient_evidence"][
        :_MAX_AGG_LIST
    ]
    result.field_uncertainties = _dedupe(
        list(verdict.field_uncertainties) if verdict is not None else []
    )
    result.evidence_gaps = _dedupe(
        [g for a in result.agents for g in a.evidence_gaps]
    )
    result.next_research_tasks = _dedupe(
        [t for a in result.agents for t in a.next_research_tasks]
    )

    # Phase 32A TPM slice — failure-vs-judgement semantics + run accounting
    # (mirrors ``council.run_council`` / ``discovery_council``).
    if chair is not None and chair.status == STATUS_COMPLETED:
        result.chair_synthesis_basis = "llm_chair"
    elif result.chair_fallback_used:
        result.chair_synthesis_basis = "deterministic_fallback"
    result.chair_attempts = tracker.attempts_for(AGENT_FIELD_CHAIR)
    if chair is None or chair.status != STATUS_COMPLETED:
        result.chair_error_type = _chair_failure_reason(
            result.chair_attempts, tracker.last_error_for(AGENT_FIELD_CHAIR)
        )
    result.token_usage = tracker.usage_metadata()

    log_event(
        log,
        "field_review_council_completed",
        field_review_run_id=field_review_run_id,
        provider=client.provider_name,
        model=client.model_name,
        council_version=cfg.llm_field_review_council_version,
        item_count=pack.item_count,
        company_count=pack.company_count,
        agents_completed=result.agents_completed,
        agents_failed=result.agents_failed,
        field_quality=result.field_quality,
        safety_valid=result.safety_valid,
        chair_fallback_used=result.chair_fallback_used,
        chair_attempts=result.chair_attempts,
        chair_synthesis_basis=result.chair_synthesis_basis,
        chair_error_type=result.chair_error_type,
        strongest_count=len(result.strongest_candidates),
        second_tier_count=len(result.second_tier),
        blocked_count=len(result.blocked_insufficient_evidence),
        elapsed_ms=int((clock() - run_started) * 1000),
        **tracker.summary_fields(),
    )
    return result


async def maybe_run_field_review_council(
    *,
    pack: FieldReviewPack,
    field_review_run_id: str | None = None,
    cfg: Settings | None = None,
    client: LLMClient | None = None,
    logger: logging.Logger | None = None,
) -> FieldReviewResult:
    """Resolve a client and run the council over an already-built pack.

    Returns ``FieldReviewResult.disabled()`` (llm_used=False) when the field
    review is off or no provider resolves. Never raises: an unexpected failure
    degrades to the disabled result.
    """
    cfg = cfg or default_settings
    log = logger or _logger
    resolved = client or get_field_review_llm_client(cfg)
    if resolved is None:
        return FieldReviewResult.disabled()
    try:
        return await run_field_review_council(
            pack,
            resolved,
            cfg=cfg,
            field_review_run_id=field_review_run_id,
            logger=log,
        )
    except Exception as exc:  # noqa: BLE001 - never let the council crash the job
        log_event(
            log,
            "field_review_council_failed",
            level=logging.ERROR,
            field_review_run_id=field_review_run_id,
            exception_type=type(exc).__name__,
        )
        return FieldReviewResult.disabled()
