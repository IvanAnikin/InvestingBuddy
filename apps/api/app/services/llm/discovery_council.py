"""
Run-level LLM discovery council orchestrator — Phase 28B.

Runs the eight run-level council agents in order over a bounded run evidence
pack, enforces citations + safety on every agent's output, aggregates the
discovery chair's decision, and returns a ``DiscoveryCouncilResult`` with honest
run metadata. A single agent failing (timeout, malformed JSON, provider error)
is isolated: that agent is marked ``failed`` and the review still returns.

Gating: the discovery council runs only when BOTH ``llm_council_enabled`` (the
shared client gate) and ``llm_discovery_council_enabled`` are true and a usable
provider resolves. Otherwise ``maybe_run_discovery_council`` returns a disabled
result (``llm_used=False``) — the deterministic path.

Logging is structured and safe (Phase 27.1D): it records ids, provider/model
names, statuses, counts and durations — never prompts, completions, evidence
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
from app.services.llm import discovery_prompts as prompts
from app.services.llm import retry_engine
from app.services.llm.client import (
    LLMClient,
    LLMError,
    LLMRateLimitError,
    get_llm_client,
)
from app.services.llm.discovery_citation_checker import check_and_sanitize
from app.services.llm.discovery_evidence_pack import build_discovery_evidence_pack
from app.services.llm.discovery_schemas import (
    AGENT_DISCOVERY_CHAIR,
    ALLOWED_RUN_QUALITY,
    CRITICAL_ALWAYS,
    DEFAULT_RUN_QUALITY,
    DISCOVERY_COUNCIL_AGENT_ORDER,
    RESERVED_AGENTS,
    STATUS_COMPLETED,
    STATUS_FAILED,
    DiscoveryCouncilAgentOutput,
    DiscoveryCouncilResult,
    DiscoveryEvidencePack,
)
from app.services.llm.token_pacer import (
    CouncilUsageTracker,
    TokenBudgetPacer,
    estimate_request_tokens,
    get_shared_pacer,
)
from app.services.sources.connectors.event_reference import event_spec_for
from app.services.sources.event_evidence import (
    ThemeEventEvidence,
    collect_theme_event_evidence,
)
from app.services.sources.macro_evidence import (
    ThemeMacroEvidence,
    collect_theme_macro_evidence,
)
from app.services.sources.registry import build_registry, registry_gap_messages
from app.services.sources.taxonomy import ProviderType

__all__ = [
    "get_discovery_llm_client",
    "discovery_max_output_tokens",
    "run_discovery_council",
    "maybe_run_discovery_council",
]

_logger = logging.getLogger("app.services.llm.discovery_council")

_MAX_AGG_LIST = 30

# internal_action -> DiscoveryCouncilResult list attribute.
_ACTION_TO_FIELD = {
    "research_next": "candidates_to_research_next",
    "monitor_for_evidence": "candidates_to_monitor",
    "reject_for_now": "candidates_to_reject",
    "insufficient_data": "candidates_insufficient_data",
}


def get_discovery_llm_client(settings: Settings | None = None) -> LLMClient | None:
    """Resolve a discovery-council client, or None when disabled/unavailable.

    Reuses ``get_llm_client`` for the shared flag/provider/credential logic (so
    the ``llm_council_enabled`` gate and the real Azure/OpenAI clients are shared
    with 28A), then adds the ``llm_discovery_council_enabled`` gate and swaps the
    28A fake client for the discovery-shaped fake client. Never raises.
    """
    cfg = settings or default_settings
    if not cfg.llm_discovery_council_enabled:
        return None
    base = get_llm_client(cfg)
    if base is None:
        return None
    if base.is_fake:
        from app.services.llm.fake_discovery_client import FakeDiscoveryLLMClient

        return FakeDiscoveryLLMClient()
    return base


def discovery_max_output_tokens(candidate_count: int, cfg: Settings | None = None) -> int:
    """The output-token budget for ONE discovery-council agent call.

    ``min(cap, base + per_candidate * candidate_count)``. The discovery council's
    JSON contract carries one ``candidate_notes`` entry per candidate, so its
    output grows with the candidate count — a flat budget truncates the reply
    mid-object on multi-candidate runs, which surfaces as a PERMANENT
    ``LLMJsonError`` (never retried, and the one-shot repair reuses the same
    budget). Computed ONCE per council run from the evidence pack and threaded
    down, so every agent (and every retry) in a run uses the same budget.

    Negative/garbage counts are floored at 0, so a zero-candidate pack gets
    exactly ``base``. The cap is a HARD ceiling and always wins.
    """
    cfg = cfg or default_settings
    count = max(0, int(candidate_count))
    scaled = cfg.llm_discovery_max_output_tokens_base + (
        cfg.llm_discovery_max_output_tokens_per_candidate * count
    )
    return min(cfg.llm_discovery_max_output_tokens_cap, scaled)


def _coerce_output(agent_name: str, raw: dict[str, Any]) -> DiscoveryCouncilAgentOutput:
    """Validate the model's dict into the agent output, tolerating drift.

    ``agent_name`` is always forced to the expected value — never trusted from
    the model — so an agent cannot impersonate another in the merged review.
    """
    payload = dict(raw) if isinstance(raw, dict) else {}
    payload["agent_name"] = agent_name
    try:
        return DiscoveryCouncilAgentOutput.model_validate(payload)
    except Exception:  # noqa: BLE001 - any validation drift becomes a failed agent
        return DiscoveryCouncilAgentOutput(
            agent_name=agent_name,
            status=STATUS_FAILED,
            summary="[Agent output could not be parsed into the required schema.]",
            safety_notes=["Malformed structured output rejected."],
        )


def _prior_summaries(outputs: list[DiscoveryCouncilAgentOutput]) -> str:
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


def _aggregate_chair(
    pack: DiscoveryEvidencePack, chair: DiscoveryCouncilAgentOutput | None
) -> dict[str, list[dict[str, Any]]]:
    """Group the chair's candidate notes into the internal-action buckets."""
    buckets: dict[str, list[dict[str, Any]]] = {
        field: [] for field in _ACTION_TO_FIELD.values()
    }
    if chair is None:
        return buckets
    for note in chair.candidate_notes:
        cand = pack.candidate_by_id(note.candidate_ref) if note.candidate_ref else None
        entry = {
            "candidate_ref": note.candidate_ref,
            "candidate_id": cand.candidate_id if cand else None,
            "ticker": note.ticker or (cand.ticker if cand else None),
            "exchange": note.exchange or (cand.exchange if cand else None),
            "rationale": note.rationale,
            "confidence": note.confidence,
        }
        field = _ACTION_TO_FIELD.get(note.internal_action)
        if field:
            buckets[field].append(entry)
    return buckets


# ---------------------------------------------------------------------------
# Phase 32A Slice 6A — single-agent attempt + retry orchestration
#
# Mirrors ``council.py``'s Slice-4 adapter section exactly (same helper names,
# same shapes) so the two councils' reliability code stays easy to compare.
# ---------------------------------------------------------------------------


def _messages_for(
    agent_name: str, evidence_json: str, result: DiscoveryCouncilResult
) -> tuple[str, str]:
    """Build (system, user) for one agent from the CURRENT council state.

    The discovery chair's user message is rebuilt from the current (possibly
    recovered) prior summaries every time it is called, so a chair retry
    synthesizes over agents that recovered in the retry pass.
    """
    if agent_name == AGENT_DISCOVERY_CHAIR:
        system = prompts.discovery_chair_system_prompt()
        user = prompts.build_user_message(
            evidence_json, _prior_summaries(result.agents)
        )
    else:
        system = prompts.system_prompt_for(agent_name)
        user = prompts.build_user_message(evidence_json)
    return system, user


async def _run_agent_attempt(
    agent_name: str,
    evidence_json: str,
    evidence_ids: set[str],
    candidate_ids: set[str],
    result: DiscoveryCouncilResult,
    client: LLMClient,
    cfg: Settings,
    max_tokens: int,
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> tuple[DiscoveryCouncilAgentOutput, list[str], Exception | None]:
    """Run ONE attempt for an agent. Never raises.

    Returns ``(output, issues, exc)``. On success ``output`` is the sanitized
    agent output and ``exc`` is None (``output.status`` may still be ``failed``
    if the safety gate quarantined it — a PERMANENT outcome). On an ``LLMError``
    ``output`` is the failed placeholder and ``exc`` is the (possibly transient)
    exception. This is the single-agent primitive BOTH the OFF path and the ON
    (retry) path call.

    ``max_tokens`` is the run's candidate-count-scaled output budget (see
    ``discovery_max_output_tokens``), computed once per run and passed in — it is
    NOT ``cfg.llm_max_output_tokens`` (that flat value is the COMPANY council's).
    ``known_gaps`` (the run's ``DiscoveryEvidencePack.known_gaps``) enables the
    gap-attribution grounding check (corrective, post-#99/#100).
    """
    is_chair = agent_name == AGENT_DISCOVERY_CHAIR
    system, user = _messages_for(agent_name, evidence_json, result)
    # Phase 32A TPM slice: advisory provider-aware pacing shared with the other
    # two councils (same deployment, same window); the chair draws on its
    # reserved slice.
    lease = None
    paced_wait = 0.0
    if pacer is not None:
        lease = await pacer.acquire(
            estimate_request_tokens(system, user, max_tokens),
            reserve_tokens=cfg.llm_council_chair_token_reserve,
            use_reserve=is_chair,
            max_wait_seconds=cfg.llm_council_pacing_max_wait_seconds,
        )
        paced_wait = lease.waited_seconds
    try:
        raw = await client.complete_json(
            system,
            user,
            max_tokens=max_tokens,
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
        placeholder = DiscoveryCouncilAgentOutput(
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
        output, evidence_ids, candidate_ids, is_chair=is_chair, known_gaps=known_gaps
    )
    return sanitized, issues, None


async def _timed_attempt(
    agent_name: str,
    evidence_json: str,
    evidence_ids: set[str],
    candidate_ids: set[str],
    result: DiscoveryCouncilResult,
    client: LLMClient,
    cfg: Settings,
    max_tokens: int,
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> tuple[DiscoveryCouncilAgentOutput, list[str], Exception | None, int]:
    """``_run_agent_attempt`` plus a wall-clock duration_ms for logging."""
    started = time.perf_counter()
    output, issues, exc = await _run_agent_attempt(
        agent_name,
        evidence_json,
        evidence_ids,
        candidate_ids,
        result,
        client,
        cfg,
        max_tokens,
        known_gaps,
        pacer,
        tracker,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    return output, issues, exc, duration_ms


def _log_agent_outcome(
    log: logging.Logger,
    agent_name: str,
    output: DiscoveryCouncilAgentOutput,
    exc: Exception | None,
    duration_ms: int,
    *,
    cfg: Settings,
    client: LLMClient,
    run_id: str | None,
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
            "discovery_council_agent_failed",
            level=logging.WARNING,
            run_id=run_id,
            agent_name=agent_name,
            provider=client.provider_name,
            council_version=cfg.llm_discovery_council_version,
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
            "discovery_council_agent_failed",
            level=logging.WARNING,
            run_id=run_id,
            agent_name=agent_name,
            provider=client.provider_name,
            council_version=cfg.llm_discovery_council_version,
            duration_ms=duration_ms,
            status=STATUS_FAILED,
            reason="quarantined_or_unparsed",
            attempt=attempt,
            **usage_fields,
        )
    else:
        log_event(
            log,
            "discovery_council_agent_completed",
            run_id=run_id,
            agent_name=agent_name,
            provider=client.provider_name,
            council_version=cfg.llm_discovery_council_version,
            duration_ms=duration_ms,
            status=output.status,
            candidate_note_count=len(output.candidate_notes),
            attempt=attempt,
            **usage_fields,
        )


def _budget_exhausted_output(agent_name: str) -> DiscoveryCouncilAgentOutput:
    """A failed placeholder for an agent that could not START before the deadline.

    Thin discovery-specific adapter (Phase 32A Slice 6A) over
    ``retry_engine.build_budget_exhausted_output``.
    """
    return retry_engine.build_budget_exhausted_output(
        agent_name, DiscoveryCouncilAgentOutput, failed_status=STATUS_FAILED
    )


def _retry_priority_order() -> list[str]:
    """Order transiently-failed agents are retried in (chair last).

    Unlike the company council, the discovery-council critical set is fixed
    (not evidence-dependent — see ``discovery_schemas.CRITICAL_ALWAYS``), so the
    natural run order already places the two RESERVED agents (run_red_team,
    discovery_chair) last.
    """
    return list(DISCOVERY_COUNCIL_AGENT_ORDER)


def _replace_agent(
    result: DiscoveryCouncilResult,
    agent_name: str,
    output: DiscoveryCouncilAgentOutput,
    issues: list[str],
) -> None:
    """Replace a failed placeholder IN PLACE (never append) and refresh warnings.

    Mirrors ``council._replace_agent``: exactly one entry per agent name is
    preserved and the recovered agent leaves no stale failure warning.
    """
    for i, existing in enumerate(result.agents):
        if existing.agent_name == agent_name:
            result.agents[i] = output
            break
    prefix = f"{agent_name}: "
    result.warnings = [w for w in result.warnings if not w.startswith(prefix)]
    result.warnings.extend(issues)


def _deterministic_chair_fallback(
    agents: list[DiscoveryCouncilAgentOutput], order: tuple[str, ...]
) -> DiscoveryCouncilAgentOutput:
    """A deterministic, non-consensus discovery-chair summary.

    Built only from ALREADY-VALIDATED stored council outputs. It NEVER
    fabricates a consensus, a recommendation, or a candidate action: the label
    is the honest ``run_quality="failed"`` (already in ``ALLOWED_RUN_QUALITY``)
    and ``candidate_notes``/``run_notes`` are empty (so it carries no
    citations and buckets no candidate into research_next / monitor / reject /
    insufficient_data on the fallback's behalf). The wording deliberately
    avoids the forbidden safety substrings (e.g. "price target", "fair value")
    so it survives ``check_and_sanitize``.

    Thin discovery-specific adapter (Phase 32A Slice 6A) over
    ``retry_engine.build_deterministic_synthesis``.
    """
    synthesis = retry_engine.build_deterministic_synthesis(
        agents,
        order,
        AGENT_DISCOVERY_CHAIR,
        completed_status=STATUS_COMPLETED,
        failed_status=STATUS_FAILED,
        chair_role_label="discovery chair",
    )
    return DiscoveryCouncilAgentOutput(
        agent_name=AGENT_DISCOVERY_CHAIR,
        status=STATUS_COMPLETED,
        summary=synthesis.summary,
        candidate_notes=[],
        run_notes=[],
        evidence_gaps=[],
        unsupported_claims=[],
        safety_notes=[synthesis.safety_note],
        next_source_tasks=[],
        run_quality="failed",
    )


def _finalize_aggregates(
    result: DiscoveryCouncilResult,
) -> tuple[bool, list[str], list[str]]:
    """Derive (safety_valid, evidence_gaps, next_source_tasks) from the FINAL
    ``result.agents`` state — after any retries have replaced a recovered
    placeholder in place. Equivalent to (and replaces) the pre-Slice-6A inline
    per-attempt accumulation: a quarantine warning is permanent (a quarantined
    agent's ``exc`` is always ``None``, so it is never entered into the
    transient-failure retry pass — see ``retry_engine.run_with_retries``), and
    every stored agent's own ``evidence_gaps``/``next_source_tasks`` already
    reflect its FINAL (possibly recovered) output.
    """
    safety_valid = not _is_safety_quarantine(result.warnings)
    all_gaps = [g for a in result.agents for g in a.evidence_gaps]
    all_tasks = [t for a in result.agents for t in a.next_source_tasks]
    return safety_valid, _dedupe(all_gaps), _dedupe(all_tasks)


async def _run_offline_pass(
    *,
    evidence_json: str,
    evidence_ids: set[str],
    candidate_ids: set[str],
    result: DiscoveryCouncilResult,
    client: LLMClient,
    cfg: Settings,
    max_tokens: int,
    log: logging.Logger,
    run_id: str | None,
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> None:
    """The OFF path: one attempt per agent, no retries — byte-identical to
    pre-Slice-6A."""
    for agent_name in DISCOVERY_COUNCIL_AGENT_ORDER:
        output, issues, exc, duration_ms = await _timed_attempt(
            agent_name,
            evidence_json,
            evidence_ids,
            candidate_ids,
            result,
            client,
            cfg,
            max_tokens,
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
            run_id=run_id,
            tracker=tracker,
        )


def _make_attempt(
    evidence_json: str,
    evidence_ids: set[str],
    candidate_ids: set[str],
    result: DiscoveryCouncilResult,
    client: LLMClient,
    cfg: Settings,
    max_tokens: int,
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> retry_engine.AttemptFn:
    """Bind the discovery-specific single-attempt primitive for the retry engine."""

    async def _attempt(agent_name: str) -> retry_engine.AttemptResult:
        return await _timed_attempt(
            agent_name,
            evidence_json,
            evidence_ids,
            candidate_ids,
            result,
            client,
            cfg,
            max_tokens,
            known_gaps,
            pacer,
            tracker,
        )

    return _attempt


def _make_replace_agent(
    result: DiscoveryCouncilResult,
) -> retry_engine.ReplaceAgentFn:
    """Bind ``_replace_agent`` to a specific ``result`` for the retry engine."""

    def _replace(agent_name: str, output: Any, issues: list[str]) -> None:
        _replace_agent(result, agent_name, output, issues)

    return _replace


def _make_status_of(result: DiscoveryCouncilResult) -> retry_engine.StatusOfFn:
    """The current status of an already-attempted agent, or ``None``."""

    def _status_of(agent_name: str) -> str | None:
        entry = next((a for a in result.agents if a.agent_name == agent_name), None)
        return entry.status if entry is not None else None

    return _status_of


def _make_log_outcome(
    log: logging.Logger,
    cfg: Settings,
    client: LLMClient,
    run_id: str | None,
    tracker: CouncilUsageTracker | None = None,
) -> retry_engine.LogOutcomeFn:
    """Bind ``_log_agent_outcome`` to the run's fixed logging context."""

    def _log_outcome(
        agent_name: str,
        output: Any,
        exc: Exception | None,
        duration_ms: int,
        attempt_number: int | None,
    ) -> None:
        _log_agent_outcome(
            log,
            agent_name,
            output,
            exc,
            duration_ms,
            cfg=cfg,
            client=client,
            run_id=run_id,
            attempt=attempt_number,
            tracker=tracker,
        )

    return _log_outcome


async def _run_council_with_retries(
    *,
    evidence_json: str,
    evidence_ids: set[str],
    candidate_ids: set[str],
    result: DiscoveryCouncilResult,
    client: LLMClient,
    cfg: Settings,
    max_tokens: int,
    log: logging.Logger,
    run_id: str | None,
    clock: Callable[[], float],
    sleeper: Callable[[float], Awaitable[Any]],
    rng: random.Random,
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> None:
    """The ON path: initial pass under a deadline + a priority retry pass.

    Thin discovery-specific adapter (Phase 32A Slice 6A) over
    ``retry_engine.run_with_retries``: supplies the discovery council's FIXED
    agent order / critical-agent set / retry-priority order, and the
    ``result`` mutation/lookup callbacks the generic engine needs.

    It also opts INTO the engine's optional initial-pass pacing
    (``llm_discovery_council_initial_pass_delay_seconds``): eight back-to-back
    large requests to one Azure deployment are what trip the provider's
    short-window rate limits. The other two councils leave the engine's default
    (``0.0`` — no pacing) untouched.
    """
    await retry_engine.run_with_retries(
        agent_order=DISCOVERY_COUNCIL_AGENT_ORDER,
        critical=CRITICAL_ALWAYS,
        priority_order=_retry_priority_order(),
        reserved=RESERVED_AGENTS,
        attempt=_make_attempt(
            evidence_json,
            evidence_ids,
            candidate_ids,
            result,
            client,
            cfg,
            max_tokens,
            known_gaps,
            pacer,
            tracker,
        ),
        append_output=result.agents.append,
        extend_warnings=result.warnings.extend,
        replace_agent=_make_replace_agent(result),
        status_of=_make_status_of(result),
        log_outcome=_make_log_outcome(log, cfg, client, run_id, tracker),
        budget_exhausted_output=_budget_exhausted_output,
        log=log,
        report_id=None,
        ticker=run_id,
        provider=client.provider_name,
        council_version=cfg.llm_discovery_council_version,
        clock=clock,
        sleeper=sleeper,
        rng=rng,
        total_budget_seconds=cfg.llm_discovery_council_retry_total_budget_seconds,
        critical_reserve_seconds=cfg.llm_discovery_council_retry_critical_reserve_seconds,
        max_retries=cfg.llm_discovery_council_retry_max_retries,
        critical_max_retries=cfg.llm_discovery_council_retry_critical_max_retries,
        base_backoff_seconds=cfg.llm_discovery_council_retry_base_backoff_seconds,
        max_backoff_seconds=cfg.llm_discovery_council_retry_max_backoff_seconds,
        max_retry_after_seconds=cfg.llm_discovery_council_retry_max_retry_after_seconds,
        completed_status=STATUS_COMPLETED,
        failed_status=STATUS_FAILED,
        initial_pass_delay_seconds=(
            cfg.llm_discovery_council_initial_pass_delay_seconds
        ),
    )


async def run_discovery_council(
    evidence_pack: DiscoveryEvidencePack,
    client: LLMClient,
    *,
    cfg: Settings | None = None,
    run_id: str | None = None,
    logger: logging.Logger | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    rng: random.Random | None = None,
    pacer: TokenBudgetPacer | None = None,
) -> DiscoveryCouncilResult:
    """Run every discovery-council agent over the pack and return the result.

    When ``cfg.llm_discovery_council_retry_enabled`` is False (default) the OFF
    path runs: one attempt per agent, no retries, no chair fallback —
    behaviorally identical to pre-Slice-6A. When True, an initial pass plus a
    bounded, priority-ordered retry pass runs under a total wall-time budget
    (materially more generous than the company council's, since the discovery
    council is an async background job — see ``llm_discovery_council_retry_*``
    in ``config.py``), and a deterministic discovery-chair fallback is attached
    if the LLM chair still fails.

    The per-agent OUTPUT-token budget is computed once from the pack's candidate
    count (``discovery_max_output_tokens``) and used for every agent attempt in
    the run, because this council's JSON contract grows with the candidate count.
    It is independent of the company council's flat ``llm_max_output_tokens``.

    ``clock`` / ``sleeper`` / ``rng`` are injectable so tests can drive the
    budget and backoff deterministically (a fake clock advanced by a fake
    sleeper) — mirrors ``council.run_council``.
    """
    cfg = cfg or default_settings
    log = logger or _logger
    rng = rng if rng is not None else random.Random()
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
    evidence_ids = evidence_pack.evidence_ids()
    candidate_ids = evidence_pack.candidate_ids()
    evidence_json = evidence_pack.model_dump_json()
    # Corrective (post-#99/#100): the run's own structured gap state, so the
    # citation checker's gap-attribution grounding check can tell a genuine
    # cause from an invented one.
    known_gaps = evidence_pack.known_gaps
    # Computed ONCE per run from the pack, then threaded down to every agent
    # attempt (initial pass AND retries) exactly like evidence_json/evidence_ids.
    max_tokens = discovery_max_output_tokens(evidence_pack.candidate_count, cfg)

    result = DiscoveryCouncilResult(
        council_version=cfg.llm_discovery_council_version,
        llm_used=True,
        provider=client.provider_name,
        model=client.model_name,
        deployment=client.deployment_name,
        evidence_pack_version=evidence_pack.evidence_pack_version,
        evidence_item_count=evidence_pack.item_count,
        candidate_count=evidence_pack.candidate_count,
    )

    log_event(
        log,
        "discovery_council_started",
        run_id=run_id,
        provider=client.provider_name,
        model=client.model_name,
        council_version=cfg.llm_discovery_council_version,
        evidence_item_count=evidence_pack.item_count,
        candidate_count=evidence_pack.candidate_count,
        max_output_tokens=max_tokens,
    )

    if cfg.llm_discovery_council_retry_enabled:
        await _run_council_with_retries(
            evidence_json=evidence_json,
            evidence_ids=evidence_ids,
            candidate_ids=candidate_ids,
            result=result,
            client=client,
            cfg=cfg,
            max_tokens=max_tokens,
            log=log,
            run_id=run_id,
            clock=clock,
            sleeper=sleeper,
            rng=rng,
            known_gaps=known_gaps,
            pacer=pacer,
            tracker=tracker,
        )
    else:
        await _run_offline_pass(
            evidence_json=evidence_json,
            evidence_ids=evidence_ids,
            candidate_ids=candidate_ids,
            result=result,
            client=client,
            cfg=cfg,
            max_tokens=max_tokens,
            log=log,
            run_id=run_id,
            known_gaps=known_gaps,
            pacer=pacer,
            tracker=tracker,
        )

    result.recount()

    chair = next(
        (a for a in result.agents if a.agent_name == AGENT_DISCOVERY_CHAIR), None
    )
    chair_quality = chair.run_quality if chair else None
    if chair_quality not in ALLOWED_RUN_QUALITY:
        # The chair did not (or could not — e.g. it failed) set a valid label.
        # Fall back to an honest run_quality that is always in the allowed set:
        # "failed" when no agent completed, else the neutral default.
        chair_quality = (
            DEFAULT_RUN_QUALITY if result.agents_completed else "failed"
        )
    result.run_quality = chair_quality
    buckets = _aggregate_chair(evidence_pack, chair)
    for field, entries in buckets.items():
        setattr(result, field, entries[:_MAX_AGG_LIST])
    safety_valid, evidence_gaps, next_source_tasks = _finalize_aggregates(result)
    result.evidence_gaps = evidence_gaps
    result.next_source_tasks = next_source_tasks
    result.safety_valid = safety_valid

    # Phase 32A Slice 6A: when the retry bundle is on and the LLM discovery
    # chair still did not complete, attach a DETERMINISTIC, non-consensus
    # discovery-chair summary so the run has an honest synthesis to render —
    # without inventing a consensus, a candidate action, or a recommendation.
    # The failed LLM chair entry is KEPT in ``agents`` (so the counts +
    # warnings show the council is visibly partial); the fallback is attached
    # separately and is excluded from the completed/failed recount tallies. It
    # never flips ``human_review_required`` / ``publication_ready``.
    if cfg.llm_discovery_council_retry_enabled and (
        chair is None or chair.status != STATUS_COMPLETED
    ):
        fallback = _deterministic_chair_fallback(
            result.agents, DISCOVERY_COUNCIL_AGENT_ORDER
        )
        # Defense-in-depth: run the fallback through the same safety/citation gate.
        sanitized_fallback, _fb_issues = check_and_sanitize(
            fallback, evidence_ids, candidate_ids, is_chair=True, known_gaps=known_gaps
        )
        result.deterministic_chair = sanitized_fallback
        result.chair_fallback_used = True
        result.run_quality = sanitized_fallback.run_quality
        log_event(
            log,
            "discovery_council_chair_fallback",
            level=logging.WARNING,
            run_id=run_id,
            provider=client.provider_name,
            council_version=cfg.llm_discovery_council_version,
            run_quality=sanitized_fallback.run_quality,
            agents_completed=result.agents_completed,
            agents_failed=result.agents_failed,
        )

    # Phase 32A TPM slice — failure-vs-judgement semantics + run accounting
    # (mirrors ``council.run_council``).
    if chair is not None and chair.status == STATUS_COMPLETED:
        result.chair_synthesis_basis = "llm_chair"
    elif result.chair_fallback_used:
        result.chair_synthesis_basis = "deterministic_fallback"
    result.chair_attempts = tracker.attempts_for(AGENT_DISCOVERY_CHAIR)
    if chair is None or chair.status != STATUS_COMPLETED:
        result.chair_error_type = tracker.last_error_for(AGENT_DISCOVERY_CHAIR)
    result.token_usage = tracker.usage_metadata()

    log_event(
        log,
        "discovery_council_completed",
        run_id=run_id,
        provider=client.provider_name,
        model=client.model_name,
        council_version=cfg.llm_discovery_council_version,
        evidence_item_count=evidence_pack.item_count,
        candidate_count=evidence_pack.candidate_count,
        agents_completed=result.agents_completed,
        agents_failed=result.agents_failed,
        run_quality=result.run_quality,
        safety_valid=result.safety_valid,
        chair_attempts=result.chair_attempts,
        chair_fallback_used=result.chair_fallback_used or None,
        chair_synthesis_basis=result.chair_synthesis_basis,
        chair_error_type=result.chair_error_type,
        elapsed_ms=int((clock() - run_started) * 1000),
        **tracker.summary_fields(),
    )
    return result


def _run_theme_region(run: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extract the macro theme + region for a run from its parsed thesis / config.

    Theme = parsed_thesis theme (else first of parsed themes, else the raw thesis
    text); region = config region (else parsed region). Both may be None (a plain
    ticker run has no theme) — the macro collector then stays quiet.
    """
    parsed = run.get("parsed_thesis")
    parsed = parsed if isinstance(parsed, dict) else {}
    config = run.get("config")
    config = config if isinstance(config, dict) else {}

    theme = parsed.get("theme")
    if not theme:
        themes = parsed.get("themes")
        if isinstance(themes, list) and themes:
            theme = themes[0]
    if not theme:
        theme = run.get("thesis_text")
    region = config.get("region") or parsed.get("region")
    return (str(theme) if theme else None, str(region) if region else None)


def _macro_discovery_facts(macro: ThemeMacroEvidence) -> list[dict[str, str]]:
    """Turn macro references into bounded, honest run-fact dicts for the pack.

    Each dict is ``{"label", "detail"}`` where the detail names the source + its
    official landing URL and states, honestly, that it is thesis-level macro
    CONTEXT only: which indicators the dataset covers, with NO figures / index
    levels / dates fetched or fabricated, and that it is neither a candidate nor a
    recommendation.
    """
    facts: list[dict[str, str]] = []
    for it in macro.evidence_items:
        name = it.source_name or it.source_id
        url = it.url or ""
        detail = (
            f"{name} — official macro statistics source reference (T2). "
            "Thesis-level macro CONTEXT only: which indicators this dataset "
            "publishes; no figures, index levels, or dates are fetched or "
            "fabricated. Not a candidate and not a trading signal. "
            f"{url}"
        ).strip()
        facts.append({"label": "macro_context", "detail": detail})
    return facts


def _event_provider_type(source_id: str) -> ProviderType | None:
    """The provider_type (procurement / patents / permits) for an event source."""
    spec = event_spec_for(source_id)
    return spec.provider_type if spec else None


def _event_discovery_facts(events: ThemeEventEvidence) -> list[dict[str, str]]:
    """Turn event references into bounded, honest run-fact dicts.

    The event analog of ``_macro_discovery_facts``. Each dict is
    ``{"label", "detail"}`` where the detail names the venue + its official
    landing URL and states, honestly, that it is a WEAK thesis-level
    research-priority CONTEXT signal only, neither a candidate, a catalyst, nor a
    trading signal. The kind-specific wording is driven by the source's
    ``provider_type`` so the label is honest for each event kind:

      * procurement / tender → "procurement / tender venue reference" (no specific
        award / contractor / amount / contract number / date);
      * patents → "patent office / index venue reference" (no specific patent /
        inventor / assignee / date, no legal / ownership conclusion);
      * permits → "permit / regulatory-event venue reference" (no specific docket /
        permit number / applicant / date, no regulatory-outcome conclusion).

    Procurement wording is intentionally byte-identical to Phase 29D.1; only the
    patent and permit labels are corrected here (the cosmetic 29D.2/29D.3 tidy).
    """
    facts: list[dict[str, str]] = []
    for it in events.evidence_items:
        name = it.source_name or it.source_id
        url = it.url or ""
        provider = _event_provider_type(it.source_id)
        if provider == ProviderType.patents:
            tier_short = (it.content_source_tier or "").split("_", 1)[0] or "T2"
            detail = (
                f"{name} — official public patent office / index venue reference "
                f"({tier_short}). WEAK thesis-level research-priority CONTEXT "
                "only: which patent filings this venue publishes; no specific "
                "patent number, inventor, assignee, or date is fetched or "
                "fabricated, and no legal or ownership conclusion is drawn. Not a "
                "candidate, not a catalyst, and not a trading signal. "
                f"{url}"
            ).strip()
        elif provider == ProviderType.permits:
            detail = (
                f"{name} — official public permit / regulatory-event venue "
                "reference (T2). WEAK thesis-level research-priority CONTEXT only: "
                "which permit / docket categories this venue publishes; no "
                "specific docket, permit number, applicant, or date is fetched or "
                "fabricated, and no regulatory-outcome conclusion is drawn. Not a "
                "candidate, not a catalyst, and not a trading signal. "
                f"{url}"
            ).strip()
        else:
            detail = (
                f"{name} — official public procurement / tender venue reference (T2). "
                "WEAK thesis-level research-priority CONTEXT only: which tenders / "
                "awards this venue publishes; no specific award, contractor, amount, "
                "contract number, or date is fetched or fabricated. Not a candidate, "
                "not a catalyst, and not a trading signal. "
                f"{url}"
            ).strip()
        facts.append({"label": "event_context", "detail": detail})
    return facts


async def maybe_run_discovery_council(
    *,
    run: dict[str, Any],
    candidates: list[dict[str, Any]],
    run_id: str | None = None,
    cfg: Settings | None = None,
    client: LLMClient | None = None,
    logger: logging.Logger | None = None,
) -> DiscoveryCouncilResult:
    """Resolve a client, build the run evidence pack, and run the council.

    Returns ``DiscoveryCouncilResult.disabled()`` (llm_used=False) when the
    discovery council is off or no provider resolves. Never raises: an unexpected
    failure degrades to the disabled result.
    """
    cfg = cfg or default_settings
    log = logger or _logger
    resolved = client or get_discovery_llm_client(cfg)
    if resolved is None:
        return DiscoveryCouncilResult.disabled()

    try:
        # Phase 29A: surface planned-source coverage gaps to the council.
        extra_known_gaps = registry_gap_messages(build_registry(cfg))
        # Phase 29C.1: when the macro layer is on, collect bounded reference-only
        # macro sources for the run's theme/region and thread them into the pack
        # as extra run facts (R#, so they are citeable) plus honest gaps. Dark by
        # default (flag off → the collector is not called and the pack is
        # byte-identical to Phase 29A/29B). Macro is thesis-level CONTEXT — never
        # a candidate and never a recommendation.
        macro_facts: list[dict[str, Any]] | None = None
        if cfg.source_macro_enabled:
            theme, region = _run_theme_region(run)
            macro = await collect_theme_macro_evidence(theme, region, cfg)
            if macro.evidence_items:
                macro_facts = _macro_discovery_facts(macro)
            extra_known_gaps = extra_known_gaps + macro.gap_messages()

        # Phase 29D.1: when the EVENT layer is on, collect bounded reference-only
        # procurement / tender sources for the run's theme/region and thread them
        # into the pack as further run facts (R#, so they are citeable) plus honest
        # gaps. Dark by default and independent of the macro flag (event off → the
        # collector is not called and the pack is byte-identical). Events are WEAK
        # thesis-level research-priority CONTEXT — never a candidate, never a
        # recommendation.
        event_facts: list[dict[str, Any]] | None = None
        if cfg.source_event_enabled:
            theme, region = _run_theme_region(run)
            events = await collect_theme_event_evidence(theme, region, cfg)
            if events.evidence_items:
                event_facts = _event_discovery_facts(events)
            extra_known_gaps = extra_known_gaps + events.gap_messages()

        pack = build_discovery_evidence_pack(
            run=run,
            candidates=candidates,
            max_candidates=cfg.llm_discovery_council_max_candidates,
            extra_known_gaps=extra_known_gaps,
            macro_evidence=macro_facts,
            event_evidence=event_facts,
        )
        log_event(
            log,
            "discovery_council_evidence_built",
            run_id=run_id,
            evidence_pack_version=pack.evidence_pack_version,
            evidence_item_count=pack.item_count,
            candidate_count=pack.candidate_count,
            known_gap_count=len(pack.known_gaps),
            macro_reference_count=len(macro_facts or []),
            event_reference_count=len(event_facts or []),
        )
        return await run_discovery_council(
            pack,
            resolved,
            cfg=cfg,
            run_id=run_id,
            logger=log,
        )
    except Exception as exc:  # noqa: BLE001 - never let the council crash a request
        log_event(
            log,
            "discovery_council_failed",
            level=logging.ERROR,
            run_id=run_id,
            exception_type=type(exc).__name__,
        )
        return DiscoveryCouncilResult.disabled()
