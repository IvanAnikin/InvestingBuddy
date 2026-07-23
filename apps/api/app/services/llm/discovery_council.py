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

import logging
import time
from typing import Any

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.structured_logging import log_event
from app.services.llm import discovery_prompts as prompts
from app.services.llm.client import (
    LLMClient,
    LLMError,
    get_llm_client,
)
from app.services.llm.discovery_citation_checker import check_and_sanitize
from app.services.llm.discovery_evidence_pack import build_discovery_evidence_pack
from app.services.llm.discovery_schemas import (
    AGENT_DISCOVERY_CHAIR,
    DISCOVERY_COUNCIL_AGENT_ORDER,
    STATUS_FAILED,
    DiscoveryCouncilAgentOutput,
    DiscoveryCouncilResult,
    DiscoveryEvidencePack,
)

__all__ = [
    "get_discovery_llm_client",
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


async def run_discovery_council(
    evidence_pack: DiscoveryEvidencePack,
    client: LLMClient,
    *,
    cfg: Settings | None = None,
    run_id: str | None = None,
    logger: logging.Logger | None = None,
) -> DiscoveryCouncilResult:
    """Run every discovery-council agent over the pack and return the result."""
    cfg = cfg or default_settings
    log = logger or _logger
    evidence_ids = evidence_pack.evidence_ids()
    candidate_ids = evidence_pack.candidate_ids()
    evidence_json = evidence_pack.model_dump_json()

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
    )

    safety_valid = True
    all_gaps: list[str] = []
    all_tasks: list[str] = []

    for agent_name in DISCOVERY_COUNCIL_AGENT_ORDER:
        started = time.perf_counter()
        is_chair = agent_name == AGENT_DISCOVERY_CHAIR
        if is_chair:
            system = prompts.discovery_chair_system_prompt()
            user = prompts.build_user_message(evidence_json, _prior_summaries(result.agents))
        else:
            system = prompts.system_prompt_for(agent_name)
            user = prompts.build_user_message(evidence_json)

        try:
            raw = await client.complete_json(
                system,
                user,
                max_tokens=cfg.llm_max_output_tokens,
                temperature=cfg.llm_temperature,
                timeout=cfg.llm_request_timeout_seconds,
                repair_instruction=prompts.REPAIR_INSTRUCTION,
            )
            output = _coerce_output(agent_name, raw)
            sanitized, issues = check_and_sanitize(
                output, evidence_ids, candidate_ids, is_chair=is_chair
            )
            result.agents.append(sanitized)
            result.warnings.extend(issues)
            if _is_safety_quarantine(issues):
                safety_valid = False
            all_gaps.extend(sanitized.evidence_gaps)
            all_tasks.extend(sanitized.next_source_tasks)
            duration_ms = int((time.perf_counter() - started) * 1000)
            if sanitized.status == STATUS_FAILED:
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
                    status=sanitized.status,
                    candidate_note_count=len(sanitized.candidate_notes),
                )
        except LLMError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            result.agents.append(
                DiscoveryCouncilAgentOutput(
                    agent_name=agent_name,
                    status=STATUS_FAILED,
                    summary="[Agent did not complete: provider error or timeout.]",
                    safety_notes=[f"Agent failed ({type(exc).__name__})."],
                )
            )
            result.warnings.append(f"{agent_name}: {type(exc).__name__}")
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
            )

    result.recount()

    chair = next(
        (a for a in result.agents if a.agent_name == AGENT_DISCOVERY_CHAIR), None
    )
    result.run_quality = chair.run_quality if chair else None
    buckets = _aggregate_chair(evidence_pack, chair)
    for field, entries in buckets.items():
        setattr(result, field, entries[:_MAX_AGG_LIST])
    result.evidence_gaps = _dedupe(all_gaps)
    result.next_source_tasks = _dedupe(all_tasks)
    result.safety_valid = safety_valid

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
    )
    return result


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
        pack = build_discovery_evidence_pack(
            run=run,
            candidates=candidates,
            max_candidates=cfg.llm_discovery_council_max_candidates,
        )
        log_event(
            log,
            "discovery_council_evidence_built",
            run_id=run_id,
            evidence_pack_version=pack.evidence_pack_version,
            evidence_item_count=pack.item_count,
            candidate_count=pack.candidate_count,
            known_gap_count=len(pack.known_gaps),
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
