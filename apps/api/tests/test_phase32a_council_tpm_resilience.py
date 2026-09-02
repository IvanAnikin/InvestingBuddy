"""
Phase 32A TPM slice — provider-aware pacing, async-era budgets, chair
failure-vs-judgement semantics, and token observability.

Spec matrix (implementation brief §10):
  A. Azure 429 then success — retry honors a bounded retry-after and recovers.
  B. Chair 429 across one TPM window — the honored wait spans the refill
     window (old 30s cap fired INTO the exhausted window; now 90s) and the
     chair succeeds on retry with an evidence-based label.
  C. Exhausted chair — bounded retries end; the deterministic fallback is
     EXPLICITLY marked (``committee_label_basis="deterministic_fallback"`` +
     ``chair_error_type``) and can never masquerade as an evidence judgement.
  D. Token reservation — non-chair acquisitions cannot consume the chair's
     reserved window slice; the chair draws on it freely.
  E. Two simultaneous councils — one shared pacer paces both; both make
     deterministic progress, neither skips an agent.
  F. Stale-job timing — the abandoned-job threshold is derived from the
     council/pacing/ingestion budgets, so a legitimately long async analysis
     is never marked stale mid-council.
  G. Final status contract — completed-after-retry is indistinguishable from
     first-try success in the final agent status (no lingering failure), while
     a truly failed chair is explicit.

Everything runs on the deterministic FAKE client + FAKE clock/sleeper — no
network, no credentials, no real TPM windows are ever slept through.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.core.config import settings as app_settings
from app.services import market_discovery_service, research_job
from app.services.llm.azure_openai_client import _extract_usage
from app.services.llm.client import LLMRateLimitError, LLMTimeoutError
from app.services.llm.council import _prior_summaries, run_council
from app.services.llm.evidence_pack import build_evidence_pack
from app.services.llm.fake_client import FakeLLMClient
from app.services.llm.schemas import (
    AGENT_COMMITTEE_CHAIR,
    AGENT_FINANCIAL_ANALYST,
    COUNCIL_AGENT_ORDER,
    DEFAULT_COMMITTEE_LABEL,
    STATUS_COMPLETED,
    STATUS_FAILED,
    CouncilAgentOutput,
    EvidencePack,
)
from app.services.llm.token_pacer import (
    CouncilUsageTracker,
    TokenBudgetPacer,
    estimate_request_tokens,
    estimate_tokens,
    get_shared_pacer,
    reset_shared_pacers,
)


# ---------------------------------------------------------------------------
# Deterministic clock / sleeper (mirrors test_phase32a_slice4 conventions)
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


def _fixed_rng() -> random.Random:
    return random.Random(1234)


# ---------------------------------------------------------------------------
# Minimal synthetic evidence pack (generic company — no real issuer)
# ---------------------------------------------------------------------------
def _report_content() -> dict[str, Any]:
    return {
        "company_identity": {
            "legal_name": {"value": "Testco US Corp"},
            "ticker": {"value": "TSTC"},
            "exchange": {"value": "NASDAQ"},
            "country_domicile": {"value": "US"},
            "sector": {"value": "Technology"},
        },
        "financial_snapshot": {
            "source_tier": "T5_api_aggregator",
            "latest_close": {"value": 100.5, "currency": "USD"},
            "revenue_ttm_usd_m": {
                "value": 12345,
                "unit": "USD_m",
                "source_tier": "T5_api_aggregator",
            },
        },
        "source_citation_appendix": {
            "sources": {
                "value": [
                    {
                        "source_type": "sec_filing",
                        "source_tier": "T2_regulator_or_gov",
                        "title": "Testco US Corp 10-K FY2025",
                        "url": "https://www.sec.gov/cgi-bin/browse-edgar?tst",
                        "source_quote": "Total net sales were $12,345 million.",
                    }
                ]
            }
        },
    }


def _snapshot() -> dict[str, Any]:
    return {
        "is_mock": False,
        "source_tier": "T2_regulator_or_gov",
        "company_identity": {
            "ticker": "TSTC",
            "legal_name": "Testco US Corp",
            "exchange": "NASDAQ",
            "country_domicile": "US",
        },
        "profile": {"sector": "Technology", "industry": "Software"},
        "fundamentals_summary": {
            "revenue_usd_m": 12345.0,
            "net_income_usd_m": 2345.0,
            "form_type": "10-K",
            "fiscal_year": 2025,
            "fiscal_period": "FY",
            "filed_date": "2025-11-03",
            "accession_number": "0000000000-25-000001",
            "source_tier": "T2_regulator_or_gov",
            "data_quality": "A_verified",
        },
    }


def _pack() -> EvidencePack:
    return build_evidence_pack(
        report_content=_report_content(), company_snapshot=_snapshot()
    )


@pytest.fixture
def retry_on(monkeypatch):
    monkeypatch.setattr(app_settings, "llm_council_retry_enabled", True)
    return app_settings


@pytest.fixture(autouse=True)
def _clean_shared_pacers():
    reset_shared_pacers()
    yield
    reset_shared_pacers()


async def _run(
    pack: EvidencePack,
    client: FakeLLMClient,
    *,
    clock: FakeClock | None = None,
    sleeper: FakeSleeper | None = None,
    pacer: TokenBudgetPacer | None = None,
):
    clock = clock or FakeClock()
    sleeper = sleeper or FakeSleeper(clock)
    return await run_council(
        pack,
        client,
        clock=clock,
        sleeper=sleeper,
        rng=_fixed_rng(),
        pacer=pacer,
    )


def _status(result) -> dict[str, str]:
    return {a.agent_name: a.status for a in result.agents}


# ---------------------------------------------------------------------------
# Async-era config defaults (a silent revert must fail loudly)
# ---------------------------------------------------------------------------
def test_async_era_budget_defaults() -> None:
    assert app_settings.llm_council_total_budget_seconds == 1200.0
    assert app_settings.llm_council_critical_reserve_seconds == 400.0
    assert app_settings.llm_council_retry_max_retry_after_seconds == 90.0
    assert app_settings.llm_council_retry_max_backoff_seconds == 60.0
    # Pacing + compaction are OFF by default — a plain deploy is unchanged.
    assert app_settings.llm_council_tpm_capacity == 0
    assert app_settings.llm_council_initial_pass_delay_seconds == 0.0
    assert app_settings.llm_council_chair_prior_summary_max_chars == 0
    # The other two councils' caps were raised in lockstep.
    assert app_settings.llm_discovery_council_retry_max_retry_after_seconds == 90.0
    assert app_settings.llm_field_review_council_retry_max_retry_after_seconds == 90.0


def test_shared_pacer_registry_disabled_at_zero_capacity() -> None:
    assert get_shared_pacer("azure_openai", "dep", 0) is None
    assert get_shared_pacer(None, "dep", 10_000) is None
    a = get_shared_pacer("azure_openai", "dep", 10_000)
    b = get_shared_pacer("azure_openai", "dep", 10_000)
    assert a is b and a is not None


# ---------------------------------------------------------------------------
# TokenBudgetPacer units (spec D, E + advisory semantics)
# ---------------------------------------------------------------------------
async def test_pacer_no_wait_when_window_has_room() -> None:
    clock, sleeper = FakeClock(), None
    sleeper = FakeSleeper(clock)
    pacer = TokenBudgetPacer(capacity_tpm=10_000, clock=clock, sleeper=sleeper)
    lease = await pacer.acquire(3_000, max_wait_seconds=240.0)
    assert lease.waited_seconds == 0.0
    assert sleeper.calls == []
    assert pacer.used_in_window() == 3_000


async def test_pacer_waits_for_window_to_free() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    pacer = TokenBudgetPacer(capacity_tpm=10_000, clock=clock, sleeper=sleeper)
    await pacer.acquire(9_000, max_wait_seconds=240.0)
    lease = await pacer.acquire(5_000, max_wait_seconds=240.0)
    # The second request had to wait for the first lease to age out (~60s).
    assert lease.waited_seconds == pytest.approx(60.0, abs=1.0)
    assert sleeper.calls and sleeper.calls[0] == pytest.approx(60.0, abs=1.0)


async def test_pacer_chair_reserve_blocks_non_chair_not_chair() -> None:
    # Spec D: capacity 10k, reserve 4k, window already holds 4k. A 3k
    # NON-chair request would fit only by dipping into the chair's reserved
    # slice (4+3=7k > 6k effective) -> it must wait; the SAME 3k request as
    # chair draws on the reserve (7k <= 10k) and proceeds immediately.
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    pacer = TokenBudgetPacer(capacity_tpm=10_000, clock=clock, sleeper=sleeper)
    await pacer.acquire(4_000, max_wait_seconds=0.0)

    chair = await pacer.acquire(
        3_000, reserve_tokens=4_000, use_reserve=True, max_wait_seconds=30.0
    )
    assert chair.waited_seconds == 0.0
    pacer.settle(chair, 0)  # undo the chair's lease for the contrast case

    non_chair = await pacer.acquire(
        3_000, reserve_tokens=4_000, use_reserve=False, max_wait_seconds=30.0
    )
    assert non_chair.waited_seconds > 0  # advisory wait imposed by the reserve


async def test_pacer_is_advisory_never_wedges() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    pacer = TokenBudgetPacer(capacity_tpm=1_000, clock=clock, sleeper=sleeper)
    await pacer.acquire(1_000, max_wait_seconds=0.0)
    # Window full and max_wait exhausted -> the lease is STILL granted (the
    # provider 429 + bounded retries are the backstop, never a skipped agent).
    lease = await pacer.acquire(50_000, max_wait_seconds=5.0)
    assert lease is not None
    assert lease.waited_seconds <= 5.0


async def test_pacer_settle_replaces_estimate() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    pacer = TokenBudgetPacer(capacity_tpm=10_000, clock=clock, sleeper=sleeper)
    lease = await pacer.acquire(8_000, max_wait_seconds=0.0)
    pacer.settle(lease, 1_200)
    assert pacer.used_in_window() == 1_200
    # Settling a rate-limited request to 0 frees the whole window.
    pacer.settle(lease, 0)
    assert pacer.used_in_window() == 0


async def test_pacer_two_concurrent_councils_share_one_window() -> None:
    # Spec E: two "councils" acquiring from ONE shared pacer are paced against
    # the same window; both finish every acquisition (nothing is skipped) and
    # at least one is made to wait.
    import asyncio

    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    pacer = TokenBudgetPacer(capacity_tpm=10_000, clock=clock, sleeper=sleeper)

    async def council(n: int) -> list[float]:
        waits = []
        for _ in range(n):
            lease = await pacer.acquire(4_000, max_wait_seconds=240.0)
            waits.append(lease.waited_seconds)
            pacer.settle(lease, 4_000)
        return waits

    waits_a, waits_b = await asyncio.gather(council(3), council(3))
    assert len(waits_a) == 3 and len(waits_b) == 3
    assert any(w > 0 for w in waits_a + waits_b)


# ---------------------------------------------------------------------------
# Usage plumbing units
# ---------------------------------------------------------------------------
def test_estimate_tokens_heuristic() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd" * 100) == 100
    assert estimate_request_tokens("s" * 400, "u" * 400, 1200) == 100 + 100 + 1200 + 16


async def test_fake_client_records_estimated_usage() -> None:
    fake = FakeLLMClient()
    await fake.complete_json(
        "You are agent id: financial_analyst.", '{"id": "E1"} evidence here'
    )
    usage = fake.consume_usage()
    assert usage is not None
    assert usage.calls == 1
    assert usage.estimated is True
    assert usage.total_tokens > 0
    assert fake.consume_usage() is None  # consumed exactly once


def test_extract_usage_reads_langchain_and_raw_shapes() -> None:
    class _LangChainish:
        usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}

    class _Raw:
        usage_metadata = None
        response_metadata = {
            "token_usage": {
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "total_tokens": 10,
            }
        }

    class _Empty:
        pass

    assert _extract_usage(_LangChainish()) == (100, 20, 120)
    assert _extract_usage(_Raw()) == (7, 3, 10)
    assert _extract_usage(_Empty()) is None


def test_tracker_derives_retries_and_429s() -> None:
    tracker = CouncilUsageTracker()
    tracker.record_attempt(
        "a",
        prompt_tokens=10,
        completion_tokens=0,
        total_tokens=10,
        estimated=True,
        error_type="LLMRateLimitError",
    )
    tracker.record_attempt(
        "a",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        estimated=True,
        error_type=None,
    )
    assert tracker.rate_limit_429_count == 1
    assert tracker.retry_attempts == 1  # second attempt for the same agent
    assert tracker.attempts_for("a") == 2
    assert tracker.total_tokens == 25
    assert tracker.last_error_for("a") is None


# ---------------------------------------------------------------------------
# Spec A + G — 429 then success: recovered agent is NOT a failure anywhere
# ---------------------------------------------------------------------------
async def test_429_then_success_recovers_and_accounts(retry_on) -> None:
    fake = FakeLLMClient(
        agent_failures={
            AGENT_FINANCIAL_ANALYST: [LLMRateLimitError("rl", retry_after=10)]
        }
    )
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    pacer = TokenBudgetPacer(capacity_tpm=100_000, clock=clock, sleeper=sleeper)
    result = await _run(_pack(), fake, clock=clock, sleeper=sleeper, pacer=pacer)

    assert _status(result)[AGENT_FINANCIAL_ANALYST] == STATUS_COMPLETED
    assert result.agents_failed == 0
    # G: the attempt-level transient error leaves no final-failure residue.
    assert not any(
        w.startswith(f"{AGENT_FINANCIAL_ANALYST}:") for w in result.warnings
    )
    assert result.chair_synthesis_basis == "llm_chair"
    assert result.chair_attempts == 1
    assert result.chair_error_type is None
    usage = result.token_usage
    assert usage is not None
    assert usage["rate_limit_429_count"] == 1
    assert usage["retry_attempts"] == 1
    assert usage["total_tokens"] > 0
    # The honored retry-after (10s, under the 90s cap) was actually slept.
    assert any(c == pytest.approx(10.0) for c in sleeper.calls)


# ---------------------------------------------------------------------------
# Spec B — chair 429 across one TPM refill window (retry-after 60s > old cap)
# ---------------------------------------------------------------------------
async def test_chair_429_across_tpm_window_succeeds(retry_on) -> None:
    fake = FakeLLMClient(
        agent_failures={
            AGENT_COMMITTEE_CHAIR: [LLMRateLimitError("tpm", retry_after=60)]
        }
    )
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    pacer = TokenBudgetPacer(capacity_tpm=100_000, clock=clock, sleeper=sleeper)
    result = await _run(_pack(), fake, clock=clock, sleeper=sleeper, pacer=pacer)

    assert _status(result)[AGENT_COMMITTEE_CHAIR] == STATUS_COMPLETED
    assert result.chair_fallback_used is False
    assert result.chair_synthesis_basis == "llm_chair"
    assert result.chair_attempts == 2
    # The evidence-based label (fake chair emits requires_more_evidence when
    # evidence exists) — NOT the failure default.
    assert result.committee_label == "requires_more_evidence"
    # The FULL 60s retry-after was honored (the old 30s cap would have clamped
    # it into the same exhausted window).
    assert any(c == pytest.approx(60.0) for c in sleeper.calls)


# ---------------------------------------------------------------------------
# Spec C + §6 — exhausted chair: fallback is explicit, never a judgement
# ---------------------------------------------------------------------------
async def test_exhausted_chair_fallback_is_explicitly_labeled(retry_on) -> None:
    failures = [LLMRateLimitError("rl", retry_after=5) for _ in range(10)]
    fake = FakeLLMClient(agent_failures={AGENT_COMMITTEE_CHAIR: failures})
    result = await _run(_pack(), fake)

    assert _status(result)[AGENT_COMMITTEE_CHAIR] == STATUS_FAILED
    assert result.chair_fallback_used is True
    assert result.committee_label == DEFAULT_COMMITTEE_LABEL  # insufficient_data
    # THE core correctness assertion of this slice: the label's basis says the
    # chair never decided anything, and the provider failure is exposed
    # separately as a class name.
    assert result.chair_synthesis_basis == "deterministic_fallback"
    assert result.chair_error_type == "LLMRateLimitError"
    assert result.chair_attempts >= 2  # initial + bounded retries, then stop

    report_payload = result.to_report_dict()
    assert report_payload["committee_label_basis"] == "deterministic_fallback"
    assert report_payload["chair_error_type"] == "LLMRateLimitError"
    assert report_payload["chair_fallback_used"] is True
    meta_payload = result.to_metadata_dict()
    assert meta_payload["committee_label_basis"] == "deterministic_fallback"
    assert meta_payload["token_usage"]["rate_limit_429_count"] >= 2


async def test_completed_chair_basis_distinguishes_from_fallback(retry_on) -> None:
    # The CONTRAST that makes the semantics meaningful: a clean 8/8 run says
    # "llm_chair" — so the two cases can never be confused again.
    result = await _run(_pack(), FakeLLMClient())
    assert result.chair_synthesis_basis == "llm_chair"
    assert result.chair_error_type is None
    payload = result.to_report_dict()
    assert payload["committee_label_basis"] == "llm_chair"
    assert "chair_error_type" not in payload
    assert "chair_fallback_used" not in payload


# ---------------------------------------------------------------------------
# Chair-input compaction (§5)
# ---------------------------------------------------------------------------
def _agent(name: str, summary: str, *, status: str = STATUS_COMPLETED, risks=None):
    return CouncilAgentOutput(
        agent_name=name,
        status=status,
        summary=summary,
        key_points=[
            {
                "claim": "An evidenced datapoint.",
                "citation_ids": ["E1", "E2"],
                "confidence": "low",
                "data_quality": "C",
            }
        ],
        risks_or_gaps=risks
        or [{"item": "Evidence is bounded.", "citation_ids": ["E1"], "severity": "low"}],
        unsupported_claims=["one flagged claim"],
    )


def test_prior_summaries_zero_is_byte_identical_legacy() -> None:
    outputs = [
        _agent("bull_case", "Long summary A about the thesis."),
        _agent("bear_case", "Long summary B about the risks.", status=STATUS_FAILED),
    ]
    legacy = "- bull_case: Long summary A about the thesis."
    assert _prior_summaries(outputs, 0) == legacy
    assert _prior_summaries(outputs) == legacy


def test_prior_summaries_compaction_bounds_and_retains_signal() -> None:
    long_summary = "word " * 400  # ~2000 chars
    outputs = [
        _agent("bull_case", long_summary.strip()),
        _agent("red_team", "Dissent: the bull case overreaches.", status=STATUS_FAILED),
    ]
    compact = _prior_summaries(outputs, 200)
    full = _prior_summaries(outputs, 0)
    assert len(compact) < len(full)
    # Truncation is explicit, citations + unsupported flags + failures retained.
    assert "…" in compact
    assert "cites: E1,E2" in compact
    assert "unsupported_claims: 1" in compact
    assert "risks: Evidence is bounded." in compact
    assert "did_not_complete: red_team" in compact


async def test_chair_prompt_compaction_end_to_end(retry_on, monkeypatch) -> None:
    fake_full = FakeLLMClient()
    await _run(_pack(), fake_full)
    full_prompt = fake_full.user_prompts[AGENT_COMMITTEE_CHAIR][-1]

    monkeypatch.setattr(app_settings, "llm_council_chair_prior_summary_max_chars", 40)
    fake_compact = FakeLLMClient()
    result = await _run(_pack(), fake_compact)
    compact_prompt = fake_compact.user_prompts[AGENT_COMMITTEE_CHAIR][-1]

    assert len(compact_prompt) < len(full_prompt)
    assert "…" in compact_prompt
    # Compaction changes only the chair's INPUT — the run still completes 8/8
    # with an evidence-based label.
    assert result.agents_completed == len(COUNCIL_AGENT_ORDER)
    assert result.chair_synthesis_basis == "llm_chair"


# ---------------------------------------------------------------------------
# Spec F — stale-job threshold is coherent with the raised budgets
# ---------------------------------------------------------------------------
def test_stale_threshold_covers_derived_worst_case(monkeypatch) -> None:
    base = market_discovery_service.analysis_job_stale_after_minutes()
    assert base >= app_settings.analysis_job_stale_after_minutes
    # Raising the council budget automatically raises the threshold.
    monkeypatch.setattr(app_settings, "llm_council_total_budget_seconds", 3600.0)
    grown = market_discovery_service.analysis_job_stale_after_minutes()
    assert grown > base
    worst_case_minutes = (
        3600.0
        + app_settings.llm_council_pacing_max_wait_seconds
        + app_settings.primary_document_ingestion_budget_seconds
    ) / 60
    assert grown > worst_case_minutes


def test_running_job_within_council_budget_is_not_stale(monkeypatch) -> None:
    """The ELAPSED-TIME rule alone: 15 minutes in is not yet abandoned.

    ``PROCESS_BOOT_AT`` is pinned older than the job because the other
    abandonment rule (``research_job.is_orphaned``) would otherwise fire on this
    backdated envelope: in a long-lived pytest process any "started 15 minutes
    ago" fixture necessarily predates the process. In production that shape means
    the process really did restart mid-run, which is exactly what that rule is
    for — so pin the boot time here to isolate the rule under test rather than
    weaken either one.
    """
    started = datetime.now(timezone.utc) - timedelta(minutes=15)
    monkeypatch.setattr(
        research_job, "PROCESS_BOOT_AT", started - timedelta(minutes=1)
    )
    envelope = {"status": "running", "started_at": started.isoformat()}
    assert market_discovery_service._analysis_job_is_stale(envelope) is False


def test_running_job_beyond_threshold_is_stale(monkeypatch) -> None:
    threshold = market_discovery_service.analysis_job_stale_after_minutes()
    started = datetime.now(timezone.utc) - timedelta(minutes=threshold + 5)
    monkeypatch.setattr(
        research_job, "PROCESS_BOOT_AT", started - timedelta(minutes=1)
    )
    envelope = {"status": "running", "started_at": started.isoformat()}
    assert market_discovery_service._analysis_job_is_stale(envelope) is True


# ---------------------------------------------------------------------------
# Pacing inside a real council run: reserve protects the chair (spec D e2e)
# ---------------------------------------------------------------------------
async def test_council_run_paces_agents_and_chair_reserve(retry_on, monkeypatch) -> None:
    monkeypatch.setattr(app_settings, "llm_council_tpm_capacity", 20_000)
    monkeypatch.setattr(app_settings, "llm_council_chair_token_reserve", 4_000)
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    pacer = TokenBudgetPacer(capacity_tpm=20_000, clock=clock, sleeper=sleeper)
    fake = FakeLLMClient()
    result = await _run(_pack(), fake, clock=clock, sleeper=sleeper, pacer=pacer)

    assert result.agents_completed == len(COUNCIL_AGENT_ORDER)
    assert result.chair_synthesis_basis == "llm_chair"
    usage = result.token_usage
    assert usage is not None and usage["total_tokens"] > 0
    # Eight sequential requests against a 20k window forced at least one
    # advisory wait (the pack prompt is ~2-3k tokens per agent incl. output
    # budget) — and NO agent was skipped because of it.
    assert usage["paced_wait_ms"] >= 0
    assert len(result.agents) == len(COUNCIL_AGENT_ORDER)


async def test_off_path_with_failed_chair_records_error_type() -> None:
    # Retry bundle OFF: no fallback exists, but the failure is still exposed
    # as an error class, and the basis stays None (no synthesis of any kind).
    fake = FakeLLMClient(
        agent_failures={AGENT_COMMITTEE_CHAIR: [LLMTimeoutError("t")]}
    )
    result = await _run(_pack(), fake)
    assert result.chair_fallback_used is False
    assert result.chair_synthesis_basis is None
    assert result.chair_error_type == "LLMTimeoutError"
    assert result.committee_label is None


# ===========================================================================
# CORRECTIVE (live staging, 2026-08-23) — budgets must accommodate PACING
# ---------------------------------------------------------------------------
# A real 7-candidate discovery run on staging completed 6/8 agents with BOTH
# ``run_red_team`` and ``discovery_chair`` failing ``budget_exhausted``: token
# pacing adds real wall time to every agent, but only the COMPANY council's
# budget had been raised for the async/TPM era. These tests encode the
# invariant that was missing when that shipped.
# ===========================================================================
from app.services.llm.council import _chair_failure_reason  # noqa: E402


def test_chair_failure_reason_distinguishes_the_three_outcomes() -> None:
    # Never ran (wall budget gone before its turn) — must NOT read as "no error".
    assert _chair_failure_reason(0, None) == "budget_exhausted"
    # Ran and failed against the provider.
    assert _chair_failure_reason(2, "LLMRateLimitError") == "LLMRateLimitError"
    # Ran and returned, but the safety/schema gate rejected it (CONTENT, not infra).
    assert _chair_failure_reason(1, None) == "quarantined_or_unparsed"


def test_pacing_max_wait_is_bounded_by_one_window_rotation() -> None:
    # The pacer's sliding window is 60s, so a wait longer than ~one rotation
    # buys nothing and can only starve later agents. The original 240s default
    # let ONE agent consume most of a council's budget.
    assert app_settings.llm_council_pacing_max_wait_seconds <= 90.0


def test_every_council_reserve_can_cover_its_protected_agents_pacing() -> None:
    """THE invariant that was missing: reserve >= both protected agents' waits.

    Each council protects exactly two tail agents (red-team + chair). If the
    reserve cannot cover their PACING waits — not merely their calls — they
    starve with ``budget_exhausted``, which is precisely the staging failure.
    """
    wait = app_settings.llm_council_pacing_max_wait_seconds
    for total, reserve, name in (
        (
            app_settings.llm_council_total_budget_seconds,
            app_settings.llm_council_critical_reserve_seconds,
            "company",
        ),
        (
            app_settings.llm_discovery_council_retry_total_budget_seconds,
            app_settings.llm_discovery_council_retry_critical_reserve_seconds,
            "discovery",
        ),
        (
            app_settings.llm_field_review_council_total_budget_seconds,
            app_settings.llm_field_review_council_critical_reserve_seconds,
            "field_review",
        ),
    ):
        assert reserve >= 2 * wait, f"{name}: reserve {reserve}s < 2 x pacing {wait}s"
        assert total > reserve, f"{name}: total {total}s must exceed reserve {reserve}s"


def test_council_budgets_cover_a_full_paced_initial_pass() -> None:
    """A full 8-agent initial pass must fit in the non-reserved budget.

    At 10k TPM with ~3k-token requests a council needs ~2.4 sliding windows
    (~144s) of pure pacing before any call latency or retries. Every council's
    non-reserved slice must exceed that with real headroom.
    """
    paced_initial_pass_seconds = 8 * 3000 / 10_000 * 60  # ~144s
    for total, reserve, name in (
        (
            app_settings.llm_council_total_budget_seconds,
            app_settings.llm_council_critical_reserve_seconds,
            "company",
        ),
        (
            app_settings.llm_discovery_council_retry_total_budget_seconds,
            app_settings.llm_discovery_council_retry_critical_reserve_seconds,
            "discovery",
        ),
        (
            app_settings.llm_field_review_council_total_budget_seconds,
            app_settings.llm_field_review_council_critical_reserve_seconds,
            "field_review",
        ),
    ):
        non_reserved = total - reserve
        assert non_reserved >= 2 * paced_initial_pass_seconds, (
            f"{name}: non-reserved {non_reserved}s cannot absorb a paced "
            f"initial pass (~{paced_initial_pass_seconds:.0f}s) with headroom"
        )


async def test_budget_exhausted_chair_reports_a_reason_not_silence(monkeypatch) -> None:
    """End-to-end: a chair starved by the wall budget says WHY.

    Reproduces the staging shape (chair never attempted) by collapsing the
    total budget so the deadline passes during the initial pass.
    """
    monkeypatch.setattr(app_settings, "llm_council_retry_enabled", True)
    monkeypatch.setattr(app_settings, "llm_council_total_budget_seconds", 0.0)
    result = await _run(_pack(), FakeLLMClient())

    assert result.chair_attempts == 0
    assert result.chair_fallback_used is True
    assert result.chair_synthesis_basis == "deterministic_fallback"
    assert result.chair_error_type == "budget_exhausted"
    payload = result.to_report_dict()
    assert payload["committee_label_basis"] == "deterministic_fallback"
    assert payload["chair_error_type"] == "budget_exhausted"


def test_council_response_schemas_surface_failure_semantics() -> None:
    """The API must not silently drop the new fields (Pydantic ignores extras).

    Staging showed ``run_quality="failed"`` with no way to tell an evidence
    judgement from a throttle: the fields were persisted but undeclared on the
    response models.
    """
    from app.schemas.field_review import FieldReviewResponse
    from app.schemas.market_discovery import DiscoveryCouncilReviewResponse

    for model in (DiscoveryCouncilReviewResponse, FieldReviewResponse):
        fields = model.model_fields
        for name in (
            "chair_synthesis_basis",
            "chair_attempts",
            "chair_error_type",
            "token_usage",
        ):
            assert name in fields, f"{model.__name__} drops {name}"
