"""
Phase 32A Slice 6A — LLM discovery-council reliability under Azure rate limits.

Every test runs with the deterministic FAKE discovery client (no network, no
credentials) plus a FAKE clock / FAKE sleeper / fixed-seed RNG so retries are
instant and fully deterministic. This mirrors ``app.services.llm.council``'s
Slice-4 reliability bundle (``test_phase32a_slice4_council_reliability.py``)
but for the run-level discovery council, reproducing the real staging incident
that motivated this slice: an async discovery-council run where only 1/8
agents completed (candidate_prioritization succeeded; run_coordinator hit a
PERMANENT ``LLMJsonError``; the other 6 hit ``LLMRateLimitError`` with zero
retries) while the company council, already covered by Slice 4, got 8/8 in the
same session.

Coverage matches the Slice-6A spec matrix:
  - Transient recovery (429 then success, timeout then success)
  - Permanent-error isolation (LLMJsonError never retried)
  - Retry exhaustion (agent stays failed after max retries)
  - Selective retry (successful agents attempted exactly once)
  - Critical-reserve budget protects run_red_team + discovery_chair
  - Deterministic discovery-chair fallback (honest, safety-clean, non-hiding)
  - Partial councils (8/8, 5/8, near-total 1/8 recovering to materially more,
    complete provider outage)
  - Flag OFF: byte-identical to the pre-Slice-6A single-attempt behaviour
"""

from __future__ import annotations

import random
from typing import Any

from app.core.config import settings as app_settings
from app.services import safety_terms
from app.services.llm.client import (
    LLMJsonError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    LLMUnavailableError,
    is_transient_llm_error,
)
from app.services.llm.discovery_council import run_discovery_council
from app.services.llm.discovery_evidence_pack import build_discovery_evidence_pack
from app.services.llm.discovery_schemas import (
    AGENT_CANDIDATE_PRIORITIZATION,
    AGENT_DISCOVERY_CHAIR,
    AGENT_DIVERSITY_ANTI_CONVERGENCE,
    AGENT_EVIDENCE_SUFFICIENCY,
    AGENT_NOVELTY_COVERAGE,
    AGENT_RISK_GATEKEEPER,
    AGENT_RUN_COORDINATOR,
    AGENT_RUN_RED_TEAM,
    CRITICAL_ALWAYS,
    DISCOVERY_COUNCIL_AGENT_ORDER,
    RESERVED_AGENTS,
    STATUS_COMPLETED,
    STATUS_FAILED,
    DiscoveryCouncilResult,
    DiscoveryEvidencePack,
)
from app.services.llm.fake_discovery_client import FakeDiscoveryLLMClient

FORBIDDEN_SUBSTRINGS = (
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "price target",
    "target price",
    "fair value",
    "intrinsic value",
    "upside of",
    "downside of",
    "undervalued",
    "overvalued",
)


# ---------------------------------------------------------------------------
# Deterministic clock / sleeper (duplicated from test_phase32a_slice4 — not a
# shared/importable module today; kept identical for behaviour parity).
# ---------------------------------------------------------------------------
class FakeClock:
    """A monotonic clock advanced ONLY by the fake sleeper (never real time)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSleeper:
    """Records every requested sleep and advances the shared fake clock."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._clock.advance(seconds)


def _fixed_rng() -> random.Random:
    return random.Random(1234)


# ---------------------------------------------------------------------------
# Evidence-pack builder (a small US-ticker run — mirrors test_phase28b's
# ``_us_semis`` fixture shape).
# ---------------------------------------------------------------------------
def _cand(ticker: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
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
        "momentum_score": 40,
        "catalyst_score": 10,
        "fundamentals_score": 20,
        "source_quality_score": 30,
        "data_completeness_score": 25,
        "risk_penalty_score": 5,
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
        "catalyst_coverage_status": "limited",
        "momentum_label": "neutral",
        "positive_catalyst_count": 1,
        "high_strength_catalyst_count": 0,
        "filing_event_count": 1,
        "news_event_count": 0,
        "press_release_event_count": 0,
        "safety_valid": True,
        "human_review_required": True,
        "is_public": False,
        "warnings": [],
    }
    base.update(over)
    return base


def _run_dict(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "run-6a-test",
        "mode": "ticker",
        "status": "completed",
        "thesis_text": None,
        "parsed_thesis": None,
        "config": {},
        "provider": "free_real",
        "lookback_days": 90,
        "universe_count": 4,
        "candidate_count": 4,
        "error_count": 0,
        "warnings": [],
    }
    base.update(over)
    return base


def _us_pack(max_candidates: int = 25) -> DiscoveryEvidencePack:
    tickers = ["AMAT", "LRCX", "KLAC", "TER"]
    cands = [_cand(t) for t in tickers]
    run = _run_dict(candidate_count=len(cands))
    return build_discovery_evidence_pack(
        run=run, candidates=cands, max_candidates=max_candidates
    )


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def _retry_on(monkeypatch) -> None:
    """Enable the Slice-6A discovery retry bundle with default budgets."""
    monkeypatch.setattr(app_settings, "llm_discovery_council_retry_enabled", True)


async def _run(
    pack: DiscoveryEvidencePack,
    client: FakeDiscoveryLLMClient,
    *,
    clock: FakeClock | None = None,
    sleeper: FakeSleeper | None = None,
    rng: random.Random | None = None,
    **kwargs: Any,
) -> DiscoveryCouncilResult:
    clock = clock or FakeClock()
    sleeper = sleeper or FakeSleeper(clock)
    return await run_discovery_council(
        pack,
        client,
        clock=clock,
        sleeper=sleeper,
        rng=rng or _fixed_rng(),
        **kwargs,
    )


def _status(result: DiscoveryCouncilResult) -> dict[str, str]:
    return {a.agent_name: a.status for a in result.agents}


# ===========================================================================
# 0. Config defaults
# ===========================================================================
def test_config_defaults_present_and_off() -> None:
    assert app_settings.llm_discovery_council_retry_enabled is False
    assert app_settings.llm_discovery_council_retry_max_retries == 2
    assert app_settings.llm_discovery_council_retry_critical_max_retries == 3
    assert app_settings.llm_discovery_council_retry_base_backoff_seconds == 1.0
    assert app_settings.llm_discovery_council_retry_max_backoff_seconds == 20.0
    assert app_settings.llm_discovery_council_retry_max_retry_after_seconds == 30.0
    # Materially higher than the company council's 150s/45s: the discovery
    # council is an async background job, not bound by the inline gateway.
    assert app_settings.llm_discovery_council_retry_total_budget_seconds == 300.0
    assert app_settings.llm_discovery_council_retry_critical_reserve_seconds == 60.0
    assert app_settings.llm_discovery_council_retry_total_budget_seconds > (
        app_settings.llm_council_total_budget_seconds
    )
    assert app_settings.llm_discovery_council_retry_critical_reserve_seconds > (
        app_settings.llm_council_critical_reserve_seconds
    )


def test_critical_and_reserved_sets() -> None:
    assert CRITICAL_ALWAYS == {
        AGENT_RUN_COORDINATOR,
        AGENT_RISK_GATEKEEPER,
        AGENT_RUN_RED_TEAM,
        AGENT_DISCOVERY_CHAIR,
    }
    assert RESERVED_AGENTS == {AGENT_RUN_RED_TEAM, AGENT_DISCOVERY_CHAIR}
    assert RESERVED_AGENTS <= CRITICAL_ALWAYS


# ===========================================================================
# 1. Transient recovery
# ===========================================================================
async def test_429_then_success(monkeypatch) -> None:
    _retry_on(monkeypatch)
    fake = FakeDiscoveryLLMClient(
        agent_failures={
            AGENT_CANDIDATE_PRIORITIZATION: [LLMRateLimitError(retry_after=None)]
        }
    )
    result = await _run(_us_pack(), fake)
    assert _status(result)[AGENT_CANDIDATE_PRIORITIZATION] == STATUS_COMPLETED
    assert result.agents_completed == len(DISCOVERY_COUNCIL_AGENT_ORDER)
    assert fake.calls[AGENT_CANDIDATE_PRIORITIZATION] == 2  # initial + one retry


async def test_timeout_then_success(monkeypatch) -> None:
    _retry_on(monkeypatch)
    fake = FakeDiscoveryLLMClient(
        agent_failures={AGENT_NOVELTY_COVERAGE: [LLMTimeoutError("t")]}
    )
    result = await _run(_us_pack(), fake)
    assert _status(result)[AGENT_NOVELTY_COVERAGE] == STATUS_COMPLETED
    assert result.agents_failed == 0


async def test_5xx_then_success(monkeypatch) -> None:
    _retry_on(monkeypatch)
    fake = FakeDiscoveryLLMClient(
        agent_failures={AGENT_EVIDENCE_SUFFICIENCY: [LLMServerError("5xx")]}
    )
    result = await _run(_us_pack(), fake)
    assert _status(result)[AGENT_EVIDENCE_SUFFICIENCY] == STATUS_COMPLETED
    assert result.agents_failed == 0


# ===========================================================================
# 2. LLMJsonError -> permanent failure, NOT retried
# ===========================================================================
def test_llm_json_error_is_permanent_classification() -> None:
    # The shared classification (Slice 4) — reused unchanged by the discovery
    # council. LLMJsonError is explicitly PERMANENT, never retried.
    assert is_transient_llm_error(LLMJsonError("bad")) is False
    assert is_transient_llm_error(LLMRateLimitError()) is True
    assert is_transient_llm_error(LLMServerError("x")) is True
    assert is_transient_llm_error(LLMTimeoutError("x")) is True


async def test_llm_json_error_not_retried_flag_on(monkeypatch) -> None:
    _retry_on(monkeypatch)
    # A long queue would prove retries never consumed more than one entry.
    fake = FakeDiscoveryLLMClient(
        agent_failures={AGENT_RUN_COORDINATOR: [LLMJsonError("bad"), LLMJsonError("bad")]}
    )
    result = await _run(_us_pack(), fake)
    assert _status(result)[AGENT_RUN_COORDINATOR] == STATUS_FAILED
    assert fake.calls[AGENT_RUN_COORDINATOR] == 1  # single attempt only, never retried
    # The rest of the council still completes.
    assert result.agents_completed == len(DISCOVERY_COUNCIL_AGENT_ORDER) - 1


# ===========================================================================
# 3. Retry exhaustion
# ===========================================================================
async def test_retry_exhaustion_stays_failed(monkeypatch) -> None:
    _retry_on(monkeypatch)
    # An OPTIONAL agent gets max_retries extra attempts; a longer failure queue
    # exhausts them and the agent stays failed (isolated, no crash).
    queue = [LLMTimeoutError("t")] * 6
    fake = FakeDiscoveryLLMClient(agent_failures={AGENT_NOVELTY_COVERAGE: queue})
    result = await _run(_us_pack(), fake)
    assert _status(result)[AGENT_NOVELTY_COVERAGE] == STATUS_FAILED
    assert fake.calls[AGENT_NOVELTY_COVERAGE] == (
        1 + app_settings.llm_discovery_council_retry_max_retries
    )
    assert result.agents_completed == len(DISCOVERY_COUNCIL_AGENT_ORDER) - 1


async def test_permanent_error_not_retried_generic(monkeypatch) -> None:
    _retry_on(monkeypatch)
    fake = FakeDiscoveryLLMClient(
        agent_failures={AGENT_DIVERSITY_ANTI_CONVERGENCE: [LLMUnavailableError("x")]}
    )
    result = await _run(_us_pack(), fake)
    assert _status(result)[AGENT_DIVERSITY_ANTI_CONVERGENCE] == STATUS_FAILED
    assert fake.calls[AGENT_DIVERSITY_ANTI_CONVERGENCE] == 1


# ===========================================================================
# 4. Selective retry — successful agents attempted exactly once
# ===========================================================================
async def test_selective_retry_successful_agents_called_once(monkeypatch) -> None:
    _retry_on(monkeypatch)
    fake = FakeDiscoveryLLMClient(
        agent_failures={AGENT_CANDIDATE_PRIORITIZATION: [LLMTimeoutError("t")]}
    )
    result = await _run(_us_pack(), fake)
    for agent in DISCOVERY_COUNCIL_AGENT_ORDER:
        if agent == AGENT_CANDIDATE_PRIORITIZATION:
            assert fake.calls[agent] == 2
        else:
            assert fake.calls[agent] == 1
    assert result.agents_completed == len(DISCOVERY_COUNCIL_AGENT_ORDER)


# ===========================================================================
# 5. Critical-reserve budget protects run_red_team + discovery_chair
# ===========================================================================
async def test_reserved_budget_protects_red_team_and_chair(monkeypatch) -> None:
    _retry_on(monkeypatch)
    monkeypatch.setattr(app_settings, "llm_discovery_council_retry_total_budget_seconds", 100.0)
    monkeypatch.setattr(
        app_settings, "llm_discovery_council_retry_critical_reserve_seconds", 40.0
    )
    monkeypatch.setattr(
        app_settings, "llm_discovery_council_retry_max_retry_after_seconds", 40.0
    )
    # Deterministic waits via retry-after (no jitter): each retry sleeps 30s.
    ra = LLMRateLimitError
    fake = FakeDiscoveryLLMClient(
        agent_failures={
            AGENT_NOVELTY_COVERAGE: [ra(retry_after=30.0)] * 3,
            AGENT_DIVERSITY_ANTI_CONVERGENCE: [ra(retry_after=30.0)] * 3,
            AGENT_RUN_RED_TEAM: [ra(retry_after=30.0)],
            AGENT_DISCOVERY_CHAIR: [ra(retry_after=30.0)],
        }
    )
    clock = FakeClock(1000.0)
    sleeper = FakeSleeper(clock)
    result = await _run(_us_pack(), fake, clock=clock, sleeper=sleeper)
    st = _status(result)
    # Reserved agents were protected and completed.
    assert st[AGENT_RUN_RED_TEAM] == STATUS_COMPLETED
    assert st[AGENT_DISCOVERY_CHAIR] == STATUS_COMPLETED
    assert fake.calls[AGENT_RUN_RED_TEAM] == 2  # initial + one protected retry
    # A non-reserved agent was starved by the drained budget (never re-attempted).
    assert fake.calls[AGENT_DIVERSITY_ANTI_CONVERGENCE] == 1
    assert st[AGENT_DIVERSITY_ANTI_CONVERGENCE] == STATUS_FAILED
    assert result.chair_fallback_used is False


# ===========================================================================
# 6. Deterministic discovery-chair fallback
# ===========================================================================
async def test_deterministic_chair_fallback_on_chair_exhaustion(monkeypatch) -> None:
    _retry_on(monkeypatch)
    fake = FakeDiscoveryLLMClient(
        agent_failures={AGENT_DISCOVERY_CHAIR: [LLMTimeoutError("t")] * 8}
    )
    result = await _run(_us_pack(), fake)
    chair = next(a for a in result.agents if a.agent_name == AGENT_DISCOVERY_CHAIR)
    # The failed LLM chair entry is KEPT (visibly partial) — the fallback is a
    # SEPARATE field, never hiding the failure.
    assert chair.status == STATUS_FAILED
    assert result.chair_fallback_used is True
    assert result.deterministic_chair is not None
    fb = result.deterministic_chair
    assert fb.status == STATUS_COMPLETED
    assert fb.run_quality == "failed"
    assert result.run_quality == "failed"
    # States partial completion explicitly + names completed/failed agents.
    for agent in DISCOVERY_COUNCIL_AGENT_ORDER:
        if agent == AGENT_DISCOVERY_CHAIR:
            continue
        assert agent in fb.summary
    assert "did not complete" in fb.summary.lower() or "completed" in fb.summary.lower()
    # No fabricated consensus / candidate bucketing on the fallback's behalf.
    assert fb.candidate_notes == []
    assert fb.run_notes == []
    # No forbidden recommendation/valuation language anywhere.
    assert safety_terms.scan_value(fb.model_dump()) == []
    text = str(fb.model_dump())
    for token in FORBIDDEN_SUBSTRINGS:
        assert token not in text
    # Chair critical -> 1 initial + critical_max_retries attempts.
    assert fake.calls[AGENT_DISCOVERY_CHAIR] == (
        1 + app_settings.llm_discovery_council_retry_critical_max_retries
    )


async def test_chair_fallback_surfaced_in_storage_dict(monkeypatch) -> None:
    _retry_on(monkeypatch)
    fake = FakeDiscoveryLLMClient(
        agent_failures={AGENT_DISCOVERY_CHAIR: [LLMTimeoutError("t")] * 8}
    )
    result = await _run(_us_pack(), fake)
    storage = result.to_storage_dict()
    assert storage["chair_fallback_used"] is True
    assert storage["deterministic_discovery_chair"]["agent_name"] == AGENT_DISCOVERY_CHAIR
    assert storage["run_quality"] == "failed"
    assert storage["human_review_required"] is True
    assert storage["publication_ready"] is False


# ===========================================================================
# 7. Partial councils matrix
# ===========================================================================
async def test_full_8_of_8(monkeypatch) -> None:
    _retry_on(monkeypatch)
    result = await _run(_us_pack(), FakeDiscoveryLLMClient())
    assert result.agents_completed == 8
    assert result.agents_failed == 0
    assert result.chair_fallback_used is False
    assert safety_terms.scan_value(result.to_storage_dict()) == []


async def test_partial_5_of_8(monkeypatch) -> None:
    _retry_on(monkeypatch)
    # Three agents fail permanently (never retried); the chair still completes
    # over the remaining agents' summaries.
    fake = FakeDiscoveryLLMClient(
        agent_failures={
            AGENT_NOVELTY_COVERAGE: [LLMUnavailableError("x")],
            AGENT_DIVERSITY_ANTI_CONVERGENCE: [LLMUnavailableError("x")],
            AGENT_EVIDENCE_SUFFICIENCY: [LLMUnavailableError("x")],
        }
    )
    result = await _run(_us_pack(), fake)
    assert result.agents_completed == 5
    assert result.agents_failed == 3
    assert len(result.agents) == 8
    assert result.chair_fallback_used is False
    assert _status(result)[AGENT_DISCOVERY_CHAIR] == STATUS_COMPLETED
    assert safety_terms.scan_value(result.to_storage_dict()) == []


async def test_near_total_1_of_8_recovers_materially(monkeypatch) -> None:
    """Mirrors the real staging incident: 1/8 succeed initially (candidate_
    prioritization), run_coordinator hits a PERMANENT LLMJsonError, and the
    other 6 hit a transient LLMRateLimitError with (pre-Slice-6A) zero
    retries. With retries enabled, the 6 transiently-failed agents recover —
    materially better than 1/8.
    """
    _retry_on(monkeypatch)
    ra = LLMRateLimitError
    fake = FakeDiscoveryLLMClient(
        agent_failures={
            AGENT_RUN_COORDINATOR: [LLMJsonError("bad")],  # permanent
            AGENT_NOVELTY_COVERAGE: [ra(retry_after=1.0)],
            AGENT_DIVERSITY_ANTI_CONVERGENCE: [ra(retry_after=1.0)],
            AGENT_EVIDENCE_SUFFICIENCY: [ra(retry_after=1.0)],
            AGENT_RISK_GATEKEEPER: [ra(retry_after=1.0)],
            AGENT_RUN_RED_TEAM: [ra(retry_after=1.0)],
            AGENT_DISCOVERY_CHAIR: [ra(retry_after=1.0)],
            # AGENT_CANDIDATE_PRIORITIZATION succeeds first try (no entry).
        }
    )
    result = await _run(_us_pack(), fake)
    # Only the permanently-failed run_coordinator stays failed.
    assert result.agents_failed == 1
    assert _status(result)[AGENT_RUN_COORDINATOR] == STATUS_FAILED
    assert fake.calls[AGENT_RUN_COORDINATOR] == 1
    # Materially better than the pre-retry 1/8 -> 7/8 recovered.
    assert result.agents_completed == 7
    assert result.agents_completed > 1
    assert result.chair_fallback_used is False


async def test_complete_provider_outage(monkeypatch) -> None:
    """All 8 agents permanently fail every attempt -> the run still terminates
    cleanly with an honest deterministic fallback and run_quality='failed',
    never a crash or an infinite loop."""
    _retry_on(monkeypatch)
    fake = FakeDiscoveryLLMClient(
        agent_failures={
            a: [LLMUnavailableError("down")] for a in DISCOVERY_COUNCIL_AGENT_ORDER
        }
    )
    result = await _run(_us_pack(), fake)
    assert result.agents_failed == 8
    assert result.agents_completed == 0
    # Permanent errors are never retried.
    assert all(fake.calls[a] == 1 for a in DISCOVERY_COUNCIL_AGENT_ORDER)
    assert result.chair_fallback_used is True
    assert result.run_quality == "failed"
    assert result.deterministic_chair is not None
    assert result.deterministic_chair.candidate_notes == []
    assert safety_terms.scan_value(result.to_storage_dict()) == []
    # Honest empty buckets — nothing fabricated when nothing completed.
    assert result.candidates_to_research_next == []
    assert result.candidates_to_monitor == []


# ===========================================================================
# 8. Flag OFF — byte-identical to pre-Slice-6A behaviour
# ===========================================================================
async def test_off_single_attempt_no_retry() -> None:
    # Flag OFF (default): a transient failure is NOT retried.
    fake = FakeDiscoveryLLMClient(
        agent_failures={AGENT_CANDIDATE_PRIORITIZATION: [LLMTimeoutError("t")]}
    )
    result = await _run(_us_pack(), fake)
    assert _status(result)[AGENT_CANDIDATE_PRIORITIZATION] == STATUS_FAILED
    assert fake.calls[AGENT_CANDIDATE_PRIORITIZATION] == 1
    assert result.chair_fallback_used is False


async def test_off_chair_failure_no_fallback() -> None:
    fake = FakeDiscoveryLLMClient(
        agent_failures={AGENT_DISCOVERY_CHAIR: [LLMTimeoutError("t")]}
    )
    result = await _run(_us_pack(), fake)
    assert _status(result)[AGENT_DISCOVERY_CHAIR] == STATUS_FAILED
    assert result.chair_fallback_used is False
    assert result.deterministic_chair is None
    storage = result.to_storage_dict()
    assert "chair_fallback_used" not in storage
    assert "deterministic_discovery_chair" not in storage


async def test_off_path_byte_identical_to_baseline(monkeypatch) -> None:
    # A clean run with the flag OFF must serialize identically whether or not
    # the ON-path machinery exists in the code — the OFF path never touches
    # the retry engine.
    off = await _run(_us_pack(), FakeDiscoveryLLMClient())
    monkeypatch.setattr(app_settings, "llm_discovery_council_retry_enabled", True)
    on = await _run(_us_pack(), FakeDiscoveryLLMClient())
    assert off.to_storage_dict() == on.to_storage_dict()
    assert off.chair_fallback_used is False and on.chair_fallback_used is False


async def test_off_path_matches_pre_slice6a_call_shape() -> None:
    # No clock/sleeper/rng injected -> the OFF path never calls the sleeper
    # (single pass, no retries), proving it is unaffected by the new engine.
    fake = FakeDiscoveryLLMClient(
        agent_failures={AGENT_NOVELTY_COVERAGE: [LLMRateLimitError(retry_after=5.0)]}
    )
    result = await run_discovery_council(_us_pack(), fake, run_id="r-off")
    assert _status(result)[AGENT_NOVELTY_COVERAGE] == STATUS_FAILED
    assert fake.calls[AGENT_NOVELTY_COVERAGE] == 1
    assert result.agents_completed == 7


def test_shared_engine_preserves_company_council_wording_byte_for_byte() -> None:
    # PR-review caught a wording drift: build_deterministic_synthesis's
    # default chair_role_label reused for BOTH prose slots produced
    # "Deterministic committee chair summary ..." where the pre-Slice-6A
    # council.py literal was the asymmetric "Deterministic committee
    # summary (LLM committee chair unavailable) ...". council.py now passes
    # summary_noun="committee" explicitly to restore this exactly. Pin it so
    # it can never silently drift again.
    from app.services.llm import retry_engine

    class _Agent:
        def __init__(self, agent_name: str, status: str) -> None:
            self.agent_name = agent_name
            self.status = status

    agents = [_Agent("financial_analyst", "completed"), _Agent("business_moat", "failed")]
    order = ("financial_analyst", "business_moat", "committee_chair")

    company = retry_engine.build_deterministic_synthesis(
        agents,
        order,
        "committee_chair",
        completed_status="completed",
        failed_status="failed",
        summary_noun="committee",
    )
    assert company.summary.startswith(
        "Deterministic committee summary (LLM committee chair unavailable). "
    )

    discovery = retry_engine.build_deterministic_synthesis(
        agents,
        order,
        "discovery_chair",
        completed_status="completed",
        failed_status="failed",
        chair_role_label="discovery chair",
    )
    assert discovery.summary.startswith(
        "Deterministic discovery chair summary (LLM discovery chair unavailable). "
    )
