"""
Hotfix — discovery-council output-token budget (LLMJsonError collapse).

A manual staging discovery-council run collapsed to 1/8 agents completed. Three
agents failed with a PERMANENT ``LLMJsonError`` — NOT rate limits. Root cause:
both councils shared the SAME flat ``llm_max_output_tokens`` (1200). The company
council's per-agent JSON is a fixed-size qualitative shape, but the DISCOVERY
council's JSON contract carries one ``candidate_notes`` entry PER CANDIDATE, so
its reply grows with the candidate count. On a realistic multi-candidate run the
reply was cut off mid-object; ``_extract_json`` cannot recover JSON with no
closing brace, and the one-shot repair reuses the SAME budget, so it failed
identically. ``LLMJsonError`` is permanent by design and is never retried.

The fix scales the DISCOVERY council's budget only:
    min(cap, base + per_candidate * candidate_count)
plus a complementary ``rationale`` length cap in the prompt contract, which
lowers the worst-case per-candidate cost.

Every test uses the deterministic FAKE discovery client (no network, no
credentials). The ``budget_truncated`` fake mode cuts its canned reply off
whenever the payload does not fit the ``max_tokens`` it was ACTUALLY called
with, so "fails under the old flat budget, succeeds under the new scaled one"
is deterministic arithmetic rather than a mock assertion.
"""

from __future__ import annotations

import random
from typing import Any

from app.core.config import Settings
from app.core.config import settings as app_settings
from app.services.llm import discovery_prompts as prompts
from app.services.llm.client import LLMJsonError, is_transient_llm_error
from app.services.llm.discovery_council import (
    discovery_max_output_tokens,
    run_discovery_council,
)
from app.services.llm.discovery_evidence_pack import build_discovery_evidence_pack
from app.services.llm.discovery_schemas import (
    AGENT_CANDIDATE_PRIORITIZATION,
    AGENT_DISCOVERY_CHAIR,
    AGENT_EVIDENCE_SUFFICIENCY,
    AGENT_RUN_COORDINATOR,
    DISCOVERY_COUNCIL_AGENT_ORDER,
    STATUS_COMPLETED,
    STATUS_FAILED,
    DiscoveryCouncilResult,
    DiscoveryEvidencePack,
)
from app.services.llm.fake_discovery_client import FakeDiscoveryLLMClient

# The agents the fake gives per-candidate notes to — i.e. the ones whose payload
# grows with the candidate count and therefore truncate first. In the staging
# incident it was likewise exactly 3 of the 8 agents that hit LLMJsonError.
_NOTE_AGENTS = (
    AGENT_CANDIDATE_PRIORITIZATION,
    AGENT_EVIDENCE_SUFFICIENCY,
    AGENT_DISCOVERY_CHAIR,
)


# ---------------------------------------------------------------------------
# Deterministic clock / sleeper (same shape as the Slice-6A reliability tests).
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Evidence packs
# ---------------------------------------------------------------------------
def _cand(ticker: str) -> dict[str, Any]:
    return {
        "candidate_id": ticker.lower(),
        "ticker": ticker,
        "exchange": "US",
        "company_name": f"{ticker} Corp",
        "country": "United States",
        "sector": "Technology",
        "industry": "Semiconductors",
        "thesis_relevance_score": 70.0,
        "combined_internal_score": 30.0,
        "candidate_score": 50.0,
        "candidate_score_grade": "medium_internal_interest",
        "data_coverage": {
            "profile_source": "curated",
            "fundamentals_source": "sec",
            "sec_eligible": True,
            "reason": "US issuer",
            "requires_human_research": False,
        },
        "source_quality": "adequate",
        "missing_info_count": 1,
        "blocking_gap_count": 0,
        "safety_valid": True,
        "human_review_required": True,
        "is_public": False,
        "warnings": [],
    }


def _pack(candidate_count: int, *, max_candidates: int = 25) -> DiscoveryEvidencePack:
    cands = [_cand(f"TK{i:02d}") for i in range(candidate_count)]
    run: dict[str, Any] = {
        "run_id": "run-token-budget",
        "mode": "ticker",
        "status": "completed",
        "thesis_text": None,
        "parsed_thesis": None,
        "config": {},
        "provider": "free_real",
        "lookback_days": 90,
        "universe_count": candidate_count,
        "candidate_count": candidate_count,
        "error_count": 0,
        "warnings": [],
    }
    return build_discovery_evidence_pack(
        run=run, candidates=cands, max_candidates=max_candidates
    )


async def _run(
    pack: DiscoveryEvidencePack,
    client: FakeDiscoveryLLMClient,
    **kwargs: Any,
) -> DiscoveryCouncilResult:
    clock = FakeClock()
    return await run_discovery_council(
        pack,
        client,
        clock=clock,
        sleeper=FakeSleeper(clock),
        rng=random.Random(1234),
        **kwargs,
    )


def _status(result: DiscoveryCouncilResult) -> dict[str, str]:
    return {a.agent_name: a.status for a in result.agents}


def _use_old_flat_budget(monkeypatch) -> None:
    """Pin the effective budget at the OLD flat 1200 (the pre-fix behaviour)."""
    monkeypatch.setattr(app_settings, "llm_discovery_max_output_tokens_base", 1200)
    monkeypatch.setattr(
        app_settings, "llm_discovery_max_output_tokens_per_candidate", 0
    )
    monkeypatch.setattr(app_settings, "llm_discovery_max_output_tokens_cap", 1200)


# ===========================================================================
# 1. Config defaults
# ===========================================================================
def test_config_defaults() -> None:
    assert app_settings.llm_discovery_max_output_tokens_base == 1200
    assert app_settings.llm_discovery_max_output_tokens_per_candidate == 200
    assert app_settings.llm_discovery_max_output_tokens_cap == 5000
    # The cap must comfortably clear the default candidate cap's fixed shell.
    assert (
        app_settings.llm_discovery_max_output_tokens_cap
        > app_settings.llm_discovery_max_output_tokens_base
    )


def test_company_council_budget_is_untouched() -> None:
    """This hotfix is scoped to the DISCOVERY council only."""
    assert app_settings.llm_max_output_tokens == 1200


# ===========================================================================
# 2. The budget formula scales with candidate count and is capped
# ===========================================================================
def test_budget_scales_with_candidate_count() -> None:
    cfg = Settings()
    assert discovery_max_output_tokens(0, cfg) == 1200
    assert discovery_max_output_tokens(3, cfg) == 1800
    assert discovery_max_output_tokens(8, cfg) == 2800
    # 1200 + 200*25 = 6200 -> the cap binds at the configured max_candidates.
    assert discovery_max_output_tokens(25, cfg) == 5000


def test_budget_cap_is_a_hard_ceiling() -> None:
    cfg = Settings()
    assert discovery_max_output_tokens(1000, cfg) == 5000


def test_budget_is_monotonic_and_floors_garbage_counts() -> None:
    cfg = Settings()
    values = [discovery_max_output_tokens(n, cfg) for n in range(0, 40)]
    assert values == sorted(values)
    # A negative / nonsense count can never produce a below-base budget.
    assert discovery_max_output_tokens(-5, cfg) == 1200


def test_budget_uses_default_settings_when_none_passed() -> None:
    assert discovery_max_output_tokens(8) == discovery_max_output_tokens(
        8, app_settings
    )


# ===========================================================================
# 3. The computed budget actually reaches the client
# ===========================================================================
async def test_every_agent_call_uses_the_scaled_budget() -> None:
    pack = _pack(8)
    fake = FakeDiscoveryLLMClient()
    await _run(pack, fake)
    expected = discovery_max_output_tokens(pack.candidate_count)
    assert expected == 2800
    assert set(fake.max_tokens_seen) == set(DISCOVERY_COUNCIL_AGENT_ORDER)
    for seen in fake.max_tokens_seen.values():
        assert seen == [expected]
    # ... and it is NOT the company council's flat value.
    assert expected != app_settings.llm_max_output_tokens


async def test_budget_is_computed_once_and_reused_across_retries(monkeypatch) -> None:
    from app.services.llm.client import LLMRateLimitError

    monkeypatch.setattr(app_settings, "llm_discovery_council_retry_enabled", True)
    pack = _pack(8)
    fake = FakeDiscoveryLLMClient(
        agent_failures={AGENT_RUN_COORDINATOR: [LLMRateLimitError(retry_after=None)]}
    )
    result = await _run(pack, fake)
    expected = discovery_max_output_tokens(pack.candidate_count)
    assert _status(result)[AGENT_RUN_COORDINATOR] == STATUS_COMPLETED
    assert fake.calls[AGENT_RUN_COORDINATOR] == 2
    assert fake.max_tokens_seen[AGENT_RUN_COORDINATOR] == [expected, expected]


async def test_smaller_pack_gets_a_smaller_budget() -> None:
    pack = _pack(3)
    fake = FakeDiscoveryLLMClient()
    await _run(pack, fake)
    assert fake.max_tokens_seen[AGENT_RUN_COORDINATOR] == [1800]


# ===========================================================================
# 4. The regression itself: truncation under the OLD budget, success under NEW
# ===========================================================================
async def test_verbose_run_truncates_under_the_old_flat_budget(monkeypatch) -> None:
    """8 candidates + long rationales (the pre-cap model behaviour) -> LLMJsonError.

    Only the agents whose payload grows per candidate truncate — mirroring the
    staging incident, where exactly 3 of 8 agents hit LLMJsonError while the
    aggregate-only agents completed.
    """
    _use_old_flat_budget(monkeypatch)
    fake = FakeDiscoveryLLMClient(mode="budget_truncated", rationale_chars=350)
    result = await _run(_pack(8), fake)

    statuses = _status(result)
    for agent in _NOTE_AGENTS:
        assert statuses[agent] == STATUS_FAILED, agent
        # initial attempt + the ONE in-client repair, both truncated.
        assert fake.calls[agent] == 2, agent
    assert statuses[AGENT_RUN_COORDINATOR] == STATUS_COMPLETED
    assert result.agents_failed == len(_NOTE_AGENTS)
    # The failure is the permanent JSON error, not a transient provider error.
    assert any("LLMJsonError" in w for w in result.warnings)


async def test_same_run_succeeds_under_the_new_scaled_budget() -> None:
    """Identical fake + identical pack, only the budget differs -> 8/8."""
    fake = FakeDiscoveryLLMClient(mode="budget_truncated", rationale_chars=350)
    result = await _run(_pack(8), fake)

    assert result.agents_completed == len(DISCOVERY_COUNCIL_AGENT_ORDER)
    assert result.agents_failed == 0
    for agent in DISCOVERY_COUNCIL_AGENT_ORDER:
        assert fake.calls[agent] == 1, agent  # no repair round-trip needed


async def test_large_run_with_capped_rationales_needs_the_scaled_budget(
    monkeypatch,
) -> None:
    """Even a model that RESPECTS the new <=150-char rationale cap needs the
    scaled budget once the run is large — the two fixes are complementary, not
    alternatives."""
    _use_old_flat_budget(monkeypatch)
    fake_old = FakeDiscoveryLLMClient(mode="budget_truncated", rationale_chars=150)
    old = await _run(_pack(25), fake_old)
    assert old.agents_failed == len(_NOTE_AGENTS)

    monkeypatch.undo()
    fake_new = FakeDiscoveryLLMClient(mode="budget_truncated", rationale_chars=150)
    new = await _run(_pack(25), fake_new)
    assert fake_new.max_tokens_seen[AGENT_RUN_COORDINATOR] == [5000]  # cap
    assert new.agents_failed == 0
    assert new.agents_completed == len(DISCOVERY_COUNCIL_AGENT_ORDER)


# ===========================================================================
# 5. Regression guard: LLMJsonError stays PERMANENT (never retried)
# ===========================================================================
def test_llm_json_error_is_not_transient() -> None:
    assert is_transient_llm_error(LLMJsonError("truncated")) is False


async def test_truncation_is_never_retried_by_the_retry_engine(monkeypatch) -> None:
    """With the retry bundle ON, a truncated (permanent) reply must NOT trigger
    a retry pass: exactly 2 raw calls (attempt + in-client repair) per agent."""
    monkeypatch.setattr(app_settings, "llm_discovery_council_retry_enabled", True)
    _use_old_flat_budget(monkeypatch)
    fake = FakeDiscoveryLLMClient(mode="budget_truncated", rationale_chars=350)
    result = await _run(_pack(8), fake)

    for agent in _NOTE_AGENTS:
        assert _status(result)[agent] == STATUS_FAILED, agent
        assert fake.calls[agent] == 2, agent
    # The LLM chair failed permanently -> the deterministic fallback stands in,
    # and it never claims a consensus or a candidate action.
    assert result.chair_fallback_used is True
    assert result.deterministic_chair is not None
    assert result.deterministic_chair.candidate_notes == []


# ===========================================================================
# 6. Prompt contract: explicit rationale cap + output discipline
# ===========================================================================
def test_json_contract_caps_the_rationale_length() -> None:
    assert '"rationale": "<=150 chars' in prompts.JSON_CONTRACT
    # The pre-existing summary cap is unchanged.
    assert '"summary": "<=600 chars' in prompts.JSON_CONTRACT


def test_output_discipline_is_in_every_agent_prompt() -> None:
    agent_prompts = [
        prompts.system_prompt_for(a)
        for a in DISCOVERY_COUNCIL_AGENT_ORDER
        if a != AGENT_DISCOVERY_CHAIR
    ] + [prompts.discovery_chair_system_prompt()]
    for text in agent_prompts:
        assert prompts.OUTPUT_DISCIPLINE in text
        assert "at most ONE candidate_notes entry per candidate" in text


def test_next_source_tasks_guidance_is_jurisdiction_relative_not_hardcoded() -> None:
    """The prompt must not name specific venues: agents are told to follow the
    run's OWN region/country from the evidence pack instead."""
    text = prompts.discovery_chair_system_prompt()
    assert "run's jurisdiction" in text
    assert "region / country" in text
    for venue in ("SEDAR", "ASX", "EDGAR", "FCA", "AMF", "BaFin"):
        assert venue not in prompts.OUTPUT_DISCIPLINE, venue
