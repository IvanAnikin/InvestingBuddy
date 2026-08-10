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
    STATUS_FAILED,
    FieldReviewAgentOutput,
    FieldReviewPack,
    FieldReviewResult,
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
) -> tuple[FieldReviewAgentOutput, list[str], Exception | None]:
    """Run ONE attempt for an agent. Never raises.

    Returns ``(output, issues, exc)``. On success ``output`` is the sanitized
    agent output and ``exc`` is None (``output.status`` may still be ``failed``
    if the safety gate quarantined it — a PERMANENT outcome). On an ``LLMError``
    ``output`` is the failed placeholder and ``exc`` is the (possibly transient)
    exception.
    """
    system, user = _messages_for(agent_name, pack_json, result)
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
        placeholder = FieldReviewAgentOutput(
            agent_name=agent_name,
            status=STATUS_FAILED,
            summary="[Agent did not complete: provider error or timeout.]",
            safety_notes=[f"Agent failed ({type(exc).__name__})."],
        )
        return placeholder, [f"{agent_name}: {type(exc).__name__}"], exc
    output = _coerce_output(agent_name, raw)
    sanitized, issues = check_and_sanitize(
        output,
        evidence_ids,
        company_ids,
        is_chair=agent_name == AGENT_FIELD_CHAIR,
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
) -> tuple[FieldReviewAgentOutput, list[str], Exception | None, int]:
    """``_run_agent_attempt`` plus a wall-clock duration_ms for logging."""
    started = time.perf_counter()
    output, issues, exc = await _run_agent_attempt(
        agent_name, pack_json, evidence_ids, company_ids, result, client, cfg
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
) -> None:
    """Emit the safe completed/failed telemetry for one attempt (no prompts)."""
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
            agent_name, pack_json, evidence_ids, company_ids, result, client, cfg
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
) -> None:
    """The retry-OFF path: one attempt per agent, no retries."""
    for agent_name in FIELD_REVIEW_AGENT_ORDER:
        output, issues, exc, duration_ms = await _timed_attempt(
            agent_name, pack_json, evidence_ids, company_ids, result, client, cfg
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
            agent_name, pack_json, evidence_ids, company_ids, result, client, cfg
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
        )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


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

    evidence_ids = pack.evidence_ids()
    company_ids = pack.company_ids()
    pack_json = pack.model_dump_json()

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
        )

    result.recount()
    result.safety_valid = not _is_safety_quarantine(result.warnings)

    chair = next(
        (a for a in result.agents if a.agent_name == AGENT_FIELD_CHAIR), None
    )
    verdict = chair.chair_verdict if chair is not None else None
    quality = verdict.field_quality if verdict is not None else None
    if quality not in ALLOWED_FIELD_QUALITY:
        # The chair did not (or could not — e.g. it failed) set a valid label.
        # Fall back to an honest field_quality that is always in the allowed set.
        quality = DEFAULT_FIELD_QUALITY if result.agents_completed else "failed"
    result.field_quality = quality

    buckets = _aggregate_chair(pack, chair)
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
        strongest_count=len(result.strongest_candidates),
        second_tier_count=len(result.second_tier),
        blocked_count=len(result.blocked_insufficient_evidence),
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
