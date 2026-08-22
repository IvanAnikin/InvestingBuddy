"""
Hotfix — discovery-council INITIAL-PASS pacing (rate-limit mitigation).

A failing staging discovery-council run (8 candidates, large evidence pack) used
the full 300s budget with 4 agents still failing ``LLMRateLimitError`` after
retries. Excessive parallelism was ruled out: the initial pass is already
strictly sequential (no ``asyncio.gather``). But there was zero PACING between
those sequential calls — each attempt fired the instant the previous returned,
so eight large requests hit the same Azure deployment inside a few seconds,
which is exactly what a short-window token/request-rate limit punishes.

The fix adds an OPTIONAL, bounded inter-agent delay to
``retry_engine.run_with_retries``'s initial pass. It defaults to ``0.0`` (OFF),
so the company council and the deep field-review council — which share that
engine — are completely unchanged. Only the discovery council opts in, via
``llm_discovery_council_initial_pass_delay_seconds``.

Invariants pinned here:
  - pacing applies to the ON (retry-enabled) path's initial pass only;
  - never after the LAST agent;
  - never when the wait would cross the wall-clock deadline;
  - never on the flag-OFF path (which must stay byte-identical to pre-Slice-6A);
  - the shared engine's DEFAULT is no pacing at all.

Deterministic throughout: fake clock advanced only by the fake sleeper.
"""

from __future__ import annotations

import inspect
import logging
import random
from pathlib import Path
from typing import Any

from app.core.config import settings as app_settings
from app.services.llm import retry_engine
from app.services.llm.discovery_council import run_discovery_council
from app.services.llm.discovery_evidence_pack import build_discovery_evidence_pack
from app.services.llm.discovery_schemas import (
    DISCOVERY_COUNCIL_AGENT_ORDER,
    DiscoveryCouncilResult,
    DiscoveryEvidencePack,
)
from app.services.llm.fake_discovery_client import FakeDiscoveryLLMClient

_LLM_DIR = Path(__file__).resolve().parents[1] / "app" / "services" / "llm"


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSleeper:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._clock.advance(seconds)


def _pack() -> DiscoveryEvidencePack:
    cands = [
        {
            "candidate_id": t.lower(),
            "ticker": t,
            "exchange": "US",
            "company_name": f"{t} Corp",
            "country": "United States",
            "data_coverage": {"profile_source": "curated"},
            "safety_valid": True,
            "human_review_required": True,
            "is_public": False,
            "warnings": [],
        }
        for t in ("AMAT", "LRCX", "KLAC")
    ]
    run: dict[str, Any] = {
        "run_id": "run-pacing",
        "mode": "ticker",
        "status": "completed",
        "config": {},
        "provider": "free_real",
        "universe_count": len(cands),
        "candidate_count": len(cands),
        "error_count": 0,
        "warnings": [],
    }
    return build_discovery_evidence_pack(run=run, candidates=cands, max_candidates=25)


async def _run(
    clock: FakeClock, sleeper: FakeSleeper, client: FakeDiscoveryLLMClient
) -> DiscoveryCouncilResult:
    return await run_discovery_council(
        _pack(),
        client,
        clock=clock,
        sleeper=sleeper,
        rng=random.Random(1234),
    )


# ===========================================================================
# 1. Config default
# ===========================================================================
def test_config_default_pacing_is_small_and_bounded() -> None:
    delay = app_settings.llm_discovery_council_initial_pass_delay_seconds
    assert delay == 1.5
    # Worst case it costs (agents - 1) * delay, which must stay negligible
    # against the discovery council's own wall-time budget.
    worst_case = delay * (len(DISCOVERY_COUNCIL_AGENT_ORDER) - 1)
    assert worst_case < 0.1 * app_settings.llm_discovery_council_retry_total_budget_seconds


# ===========================================================================
# 2. Pacing on the ON path's initial pass
# ===========================================================================
async def test_initial_pass_is_paced_between_agents(monkeypatch) -> None:
    monkeypatch.setattr(app_settings, "llm_discovery_council_retry_enabled", True)
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    result = await _run(clock, sleeper, FakeDiscoveryLLMClient())

    delay = app_settings.llm_discovery_council_initial_pass_delay_seconds
    # One gap BETWEEN each pair of agents — never after the last one.
    assert sleeper.calls == [delay] * (len(DISCOVERY_COUNCIL_AGENT_ORDER) - 1)
    assert clock.now == 1000.0 + delay * (len(DISCOVERY_COUNCIL_AGENT_ORDER) - 1)
    # Pacing is scheduling only: the council result is unaffected.
    assert result.agents_completed == len(DISCOVERY_COUNCIL_AGENT_ORDER)
    assert result.agents_failed == 0


async def test_pacing_can_be_disabled_by_config(monkeypatch) -> None:
    monkeypatch.setattr(app_settings, "llm_discovery_council_retry_enabled", True)
    monkeypatch.setattr(
        app_settings, "llm_discovery_council_initial_pass_delay_seconds", 0.0
    )
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    result = await _run(clock, sleeper, FakeDiscoveryLLMClient())

    assert sleeper.calls == []
    assert result.agents_completed == len(DISCOVERY_COUNCIL_AGENT_ORDER)


async def test_pacing_never_crosses_the_deadline(monkeypatch) -> None:
    """The wall-clock budget belongs to real attempts, not to pacing."""
    monkeypatch.setattr(app_settings, "llm_discovery_council_retry_enabled", True)
    monkeypatch.setattr(
        app_settings, "llm_discovery_council_retry_total_budget_seconds", 3.0
    )
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    await _run(clock, sleeper, FakeDiscoveryLLMClient())

    # deadline = 1003.0. After agent 1 the clock is 1000.0, so one 1.5s gap
    # fits (1001.5 < 1003.0); the next would land exactly on the deadline and
    # is therefore skipped, and no further gap is ever taken.
    assert sleeper.calls == [1.5]
    assert clock.now < 1003.0


# ===========================================================================
# 3. The flag-OFF path stays byte-identical (never sleeps)
# ===========================================================================
async def test_off_path_never_paces() -> None:
    assert app_settings.llm_discovery_council_retry_enabled is False
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    result = await _run(clock, sleeper, FakeDiscoveryLLMClient())

    assert sleeper.calls == []
    assert clock.now == 1000.0
    assert result.agents_completed == len(DISCOVERY_COUNCIL_AGENT_ORDER)


# ===========================================================================
# 4. The SHARED engine defaults to no pacing (company + field-review councils)
# ===========================================================================
def test_shared_engine_default_is_no_pacing() -> None:
    param = inspect.signature(retry_engine.run_with_retries).parameters[
        "initial_pass_delay_seconds"
    ]
    assert param.default == 0.0
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_pacing_opt_in_policy_per_council() -> None:
    """Which councils opt into the engine's initial-pass pacing.

    Phase 32A TPM slice: the COMPANY council now opts in too (config-driven,
    default 0.0 = off — the async job removed the reason it was excluded).
    The field-review council still does not use the engine's initial-pass
    parameter (its pacing comes from the shared token pacer instead).
    """
    company = (_LLM_DIR / "council.py").read_text(encoding="utf-8")
    assert (
        "initial_pass_delay_seconds=cfg.llm_council_initial_pass_delay_seconds"
        in company
    )
    discovery = (_LLM_DIR / "discovery_council.py").read_text(encoding="utf-8")
    assert "initial_pass_delay_seconds=" in discovery
    field_review = (_LLM_DIR / "field_review_council.py").read_text(encoding="utf-8")
    assert "initial_pass_delay_seconds" not in field_review


async def test_engine_without_the_parameter_never_sleeps_in_the_initial_pass() -> None:
    """Behavioural proof for the two councils that omit the parameter."""

    class _Out:
        def __init__(self, agent_name: str) -> None:
            self.agent_name = agent_name
            self.status = "completed"

    order = ("a", "b", "c")
    outputs: list[Any] = []
    clock = FakeClock()
    sleeper = FakeSleeper(clock)

    async def _attempt(agent_name: str) -> retry_engine.AttemptResult:
        return _Out(agent_name), [], None, 1

    await retry_engine.run_with_retries(
        agent_order=order,
        critical=frozenset(),
        priority_order=list(order),
        reserved=frozenset(),
        attempt=_attempt,
        append_output=outputs.append,
        extend_warnings=lambda issues: None,
        replace_agent=lambda name, output, issues: None,
        status_of=lambda name: "completed",
        log_outcome=lambda name, output, exc, duration_ms, attempt_number: None,
        budget_exhausted_output=lambda name: _Out(name),
        log=logging.getLogger("test.retry_engine.pacing"),
        report_id=None,
        ticker=None,
        provider="fake",
        council_version="v1",
        clock=clock,
        sleeper=sleeper,
        rng=random.Random(1234),
        total_budget_seconds=100.0,
        critical_reserve_seconds=10.0,
        max_retries=2,
        critical_max_retries=3,
        base_backoff_seconds=1.0,
        max_backoff_seconds=20.0,
        max_retry_after_seconds=30.0,
        completed_status="completed",
        failed_status="failed",
    )

    assert [o.agent_name for o in outputs] == list(order)
    assert sleeper.calls == []
    assert clock.now == 1000.0
