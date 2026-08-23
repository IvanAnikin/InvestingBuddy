"""
Phase 32A Slice 4 — LLM council reliability under Azure rate limits.

Every test runs with the deterministic FAKE client (no network, no credentials)
plus a FAKE clock / FAKE sleeper / fixed-seed RNG so retries are instant and
fully deterministic. Coverage matches the Slice-4 spec matrix:

  - Transient recovery (429 / timeout / 5xx / retry-after honored / exhaustion)
  - Selective retry (successful agents attempted once; only failed retried)
  - Critical prioritization + reserved budget + deterministic chair fallback
  - Partial councils (8/8, 7/8, 4/8, chair-failed, red_team-failed,
    financial_analyst-failed, all-unavailable)
  - Idempotency (one entry per agent after retries; stable counts)
  - Backward-compat (flag OFF == single attempt, no fallback, byte-identical)
  - Error-classification + provider duck-typing units + safe retry logging
  - ONE report-level integration test (partial council + flag ON) over real
    SQLite: publication_ready False, human_review_required True, schema/safety
    valid, completed-agent citations persist, failed placeholders create none.
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import settings as app_settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import backtest as _backtest  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import financial_snapshot as _financial_snapshot  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import review_event as _review_event  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import screening as _screening  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.agent_run import AgentRun
from app.models.company import Company
from app.models.report import Report
from app.models.source import Citation, Source
from app.services import final_report_generator, safety_terms
from app.services.final_report_generator import FinalReportGeneratorService
from app.services.llm import council as council_mod
from app.services.llm.azure_openai_client import (
    _classify_provider_error,
    _coerce_retry_after,
)
from app.services.llm.client import (
    LLMError,
    LLMJsonError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    LLMUnavailableError,
    is_transient_llm_error,
)
from app.services.llm.council import run_council
from app.services.llm.evidence_pack import build_evidence_pack
from app.services.llm.fake_client import FakeLLMClient
from app.services.llm.schemas import (
    AGENT_COMMITTEE_CHAIR,
    AGENT_FINANCIAL_ANALYST,
    AGENT_VALUATION_GUARD,
    COUNCIL_AGENT_ORDER,
    CRITICAL_ALWAYS,
    DEFAULT_COMMITTEE_LABEL,
    STATUS_COMPLETED,
    STATUS_FAILED,
    CouncilResult,
    EvidencePack,
    has_financial_evidence,
)

FORBIDDEN_SUBSTRINGS = (
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "price target",
    "fair value",
    "intrinsic value",
    "upside of",
    "downside of",
    "undervalued",
    "overvalued",
)


# ---------------------------------------------------------------------------
# Deterministic clock / sleeper
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
# Evidence-pack builders
# ---------------------------------------------------------------------------
def _aapl_report_content() -> dict[str, Any]:
    return {
        "company_identity": {
            "legal_name": {"value": "Apple Inc."},
            "ticker": {"value": "AAPL"},
            "exchange": {"value": "NASDAQ"},
            "country_domicile": {"value": "US"},
            "sector": {"value": "Technology"},
        },
        "financial_snapshot": {
            "source_tier": "T5_api_aggregator",
            "latest_close": {"value": 190.5, "currency": "USD"},
            "revenue_ttm_usd_m": {
                "value": 383285,
                "unit": "USD_m",
                "source_tier": "T5_api_aggregator",
            },
        },
        "data_availability_summary": {"missing_fields": {"value": ["lei", "beta"]}},
        "source_citation_appendix": {
            "sources": {
                "value": [
                    {
                        "source_type": "sec_filing",
                        "source_tier": "T2_regulator_or_gov",
                        "title": "Apple Inc. 10-K FY2023",
                        "url": "https://www.sec.gov/cgi-bin/browse-edgar?x",
                        "source_quote": "Total net sales were $383,285 million.",
                    }
                ]
            }
        },
    }


def _aapl_snapshot() -> dict[str, Any]:
    return {
        "is_mock": False,
        "source_tier": "T2_regulator_or_gov",
        "company_identity": {
            "ticker": "AAPL",
            "legal_name": "Apple Inc.",
            "exchange": "NASDAQ",
            "country_domicile": "US",
        },
        "profile": {"sector": "Technology", "industry": "Consumer Electronics"},
        "fundamentals_summary": {
            "revenue_usd_m": 383285.0,
            "net_income_usd_m": 96995.0,
            "form_type": "10-K",
            "fiscal_year": 2023,
            "fiscal_period": "FY",
            "filed_date": "2023-11-03",
            "accession_number": "0000320193-23-000106",
            "source_tier": "T2_regulator_or_gov",
            "data_quality": "A_verified",
        },
    }


def _cfr_report_content() -> dict[str, Any]:
    """Small, metadata-only non-US pack (no SEC financial statements)."""
    return {
        "company_identity": {
            "legal_name": {"value": "Compagnie Financiere Richemont SA"},
            "ticker": {"value": "CFR"},
            "exchange": {"value": "SW"},
            "country_domicile": {"value": "Switzerland"},
            "sector": {"value": "Consumer Cyclical"},
        },
        "financial_snapshot": {"source_tier": "T6_model_estimate"},
        "data_availability_summary": {
            "missing_fields": {"value": ["revenue", "ebitda", "lei"]}
        },
    }


def _cfr_snapshot() -> dict[str, Any]:
    return {
        "is_mock": False,
        "source_tier": "T6_model_estimate",
        "company_identity": {
            "ticker": "CFR",
            "legal_name": "Compagnie Financiere Richemont SA",
            "exchange": "SW",
            "country_domicile": "Switzerland",
        },
        "profile": {"sector": "Consumer Cyclical", "industry": "Luxury Goods"},
    }


def _financial_pack() -> EvidencePack:
    return build_evidence_pack(
        report_content=_aapl_report_content(), company_snapshot=_aapl_snapshot()
    )


def _small_pack() -> EvidencePack:
    return build_evidence_pack(
        report_content=_cfr_report_content(), company_snapshot=_cfr_snapshot()
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def retry_on(monkeypatch):
    """Enable the Slice-4 retry bundle with default budgets (auto-restored)."""
    monkeypatch.setattr(app_settings, "llm_council_retry_enabled", True)
    return app_settings


async def _run(
    pack: EvidencePack,
    client: FakeLLMClient,
    *,
    clock: FakeClock | None = None,
    sleeper: FakeSleeper | None = None,
    rng: random.Random | None = None,
    **kwargs,
) -> CouncilResult:
    clock = clock or FakeClock()
    sleeper = sleeper or FakeSleeper(clock)
    return await run_council(
        pack,
        client,
        clock=clock,
        sleeper=sleeper,
        rng=rng or _fixed_rng(),
        **kwargs,
    )


def _status(result: CouncilResult) -> dict[str, str]:
    return {a.agent_name: a.status for a in result.agents}


# ===========================================================================
# 0. Config defaults
# ===========================================================================
def test_config_defaults_present_and_off() -> None:
    assert app_settings.llm_council_retry_enabled is False
    assert app_settings.llm_council_max_retries == 2
    assert app_settings.llm_council_critical_max_retries == 3
    assert app_settings.llm_council_retry_base_backoff_seconds == 1.0
    # Phase 32A TPM slice: async-era budgets. The old 20/30/150/45 values were
    # sized for the removed synchronous ~230s-gateway constraint; the council
    # now runs in an async job and must span provider TPM refill windows.
    assert app_settings.llm_council_retry_max_backoff_seconds == 60.0
    assert app_settings.llm_council_retry_max_retry_after_seconds == 90.0
    assert app_settings.llm_council_total_budget_seconds == 1200.0
    assert app_settings.llm_council_critical_reserve_seconds == 400.0


# ===========================================================================
# 1. Error classification units
# ===========================================================================
def test_is_transient_classification() -> None:
    assert is_transient_llm_error(LLMRateLimitError(retry_after=5.0)) is True
    assert is_transient_llm_error(LLMServerError("5xx")) is True
    assert is_transient_llm_error(LLMTimeoutError("t")) is True
    # Permanent: never retried.
    assert is_transient_llm_error(LLMJsonError("bad")) is False
    assert is_transient_llm_error(LLMUnavailableError("no creds")) is False
    assert is_transient_llm_error(LLMError("generic")) is False


class _FakeResponse:
    def __init__(self, status_code=None, headers=None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class _FakeRateLimit(Exception):
    def __init__(self, headers=None) -> None:
        super().__init__("429")
        self.status_code = 429
        self.response = _FakeResponse(429, headers)


class _FakeServerErr(Exception):
    def __init__(self) -> None:
        super().__init__("boom")
        self.response = _FakeResponse(503, {})


class _FakeInternalServerError(Exception):
    pass


def test_provider_classification_duck_typing() -> None:
    # 429 by status -> rate limit, retry-after seconds extracted (bare number).
    rl = _classify_provider_error(_FakeRateLimit({"retry-after": "12"}))
    assert isinstance(rl, LLMRateLimitError)
    assert rl.retry_after == 12.0
    # retry-after-ms takes precedence and is converted to seconds.
    rl_ms = _classify_provider_error(_FakeRateLimit({"retry-after-ms": "2500"}))
    assert isinstance(rl_ms, LLMRateLimitError)
    assert rl_ms.retry_after == 2.5
    # An HTTP-date retry-after is non-numeric -> None (never the raw text).
    rl_date = _classify_provider_error(
        _FakeRateLimit({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
    )
    assert rl_date.retry_after is None
    # 5xx by status -> server error.
    assert isinstance(_classify_provider_error(_FakeServerErr()), LLMServerError)
    # Class-name heuristic when no status is present.
    assert isinstance(
        _classify_provider_error(_FakeInternalServerError()), LLMServerError
    )
    # Anything else -> generic permanent LLMError (type name only, no message).
    generic = _classify_provider_error(ValueError("secret-url?token=abc"))
    assert type(generic) is LLMError
    assert "token" not in str(generic)
    assert "ValueError" in str(generic)


def test_coerce_retry_after_variants() -> None:
    assert _coerce_retry_after(_FakeRateLimit({"retry-after": "30"})) == 30.0
    assert _coerce_retry_after(_FakeRateLimit({"retry-after-ms": "1500"})) == 1.5
    assert _coerce_retry_after(_FakeRateLimit({})) is None
    assert _coerce_retry_after(ValueError("nope")) is None

    class _Direct(Exception):
        retry_after = 7.5

    assert _coerce_retry_after(_Direct()) == 7.5


# ===========================================================================
# 2. has_financial_evidence
# ===========================================================================
def test_has_financial_evidence() -> None:
    assert has_financial_evidence(_financial_pack()) is True
    assert has_financial_evidence(_small_pack()) is False


def test_critical_set_depends_on_financial_evidence() -> None:
    assert council_mod._critical_agents(_financial_pack()) == (
        CRITICAL_ALWAYS | {AGENT_VALUATION_GUARD}
    )
    assert council_mod._critical_agents(_small_pack()) == CRITICAL_ALWAYS


# ===========================================================================
# 3. Transient recovery
# ===========================================================================
async def test_429_then_success(retry_on) -> None:
    fake = FakeLLMClient(
        agent_failures={AGENT_FINANCIAL_ANALYST: [LLMRateLimitError(retry_after=None)]}
    )
    result = await _run(_financial_pack(), fake)
    assert _status(result)[AGENT_FINANCIAL_ANALYST] == STATUS_COMPLETED
    assert result.agents_completed == len(COUNCIL_AGENT_ORDER)
    assert fake.calls[AGENT_FINANCIAL_ANALYST] == 2  # initial + one retry


async def test_timeout_then_success(retry_on) -> None:
    fake = FakeLLMClient(agent_failures={"business_moat": [LLMTimeoutError("t")]})
    result = await _run(_financial_pack(), fake)
    assert _status(result)["business_moat"] == STATUS_COMPLETED
    assert result.agents_failed == 0


async def test_5xx_then_success(retry_on) -> None:
    fake = FakeLLMClient(agent_failures={"catalyst": [LLMServerError("5xx")]})
    result = await _run(_financial_pack(), fake)
    assert _status(result)["catalyst"] == STATUS_COMPLETED
    assert result.agents_failed == 0


async def test_retry_after_honored_and_capped(retry_on) -> None:
    # retry_after well above the cap -> the sleeper is asked to wait the CAP.
    fake = FakeLLMClient(
        agent_failures={"catalyst": [LLMRateLimitError(retry_after=999.0)]}
    )
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    result = await _run(_financial_pack(), fake, clock=clock, sleeper=sleeper)
    assert _status(result)["catalyst"] == STATUS_COMPLETED
    cap = app_settings.llm_council_retry_max_retry_after_seconds
    assert cap in sleeper.calls  # honored retry-after was capped to the max
    assert all(s <= cap for s in sleeper.calls)


async def test_retry_exhaustion_stays_failed(retry_on) -> None:
    # An OPTIONAL agent gets max_retries extra attempts; a longer failure queue
    # exhausts them and the agent stays failed (isolated, no crash).
    queue = [LLMTimeoutError("t")] * 6
    fake = FakeLLMClient(agent_failures={"business_moat": queue})
    result = await _run(_financial_pack(), fake)
    assert _status(result)["business_moat"] == STATUS_FAILED
    # 1 initial + llm_council_max_retries attempts.
    assert fake.calls["business_moat"] == 1 + app_settings.llm_council_max_retries
    # The whole council still returns; other agents complete.
    assert result.agents_completed == len(COUNCIL_AGENT_ORDER) - 1


async def test_permanent_error_not_retried(retry_on) -> None:
    # A permanent error (bad JSON / unavailable) is never retried.
    fake = FakeLLMClient(
        agent_failures={"catalyst": [LLMUnavailableError("missing dep")]}
    )
    result = await _run(_financial_pack(), fake)
    assert _status(result)["catalyst"] == STATUS_FAILED
    assert fake.calls["catalyst"] == 1  # single attempt only


# ===========================================================================
# 4. Selective retry
# ===========================================================================
async def test_selective_retry_successful_agents_called_once(retry_on) -> None:
    fake = FakeLLMClient(
        agent_failures={AGENT_FINANCIAL_ANALYST: [LLMTimeoutError("t")]}
    )
    result = await _run(_financial_pack(), fake)
    # Only the failed agent is retried; every other agent is attempted exactly once.
    for agent in COUNCIL_AGENT_ORDER:
        if agent == AGENT_FINANCIAL_ANALYST:
            assert fake.calls[agent] == 2
        else:
            assert fake.calls[agent] == 1
    assert result.agents_completed == len(COUNCIL_AGENT_ORDER)


async def test_selective_retry_preserves_completed_outputs(retry_on) -> None:
    fake = FakeLLMClient(agent_failures={"catalyst": [LLMTimeoutError("t")]})
    result = await _run(_financial_pack(), fake)
    fa = next(a for a in result.agents if a.agent_name == AGENT_FINANCIAL_ANALYST)
    # A completed non-retried agent keeps its citation-bound key points untouched.
    assert fa.status == STATUS_COMPLETED
    assert fa.key_points  # still cited, not duplicated
    # No duplicate warnings for the recovered agent.
    catalyst_warnings = [w for w in result.warnings if w.startswith("catalyst:")]
    assert catalyst_warnings == []


# ===========================================================================
# 5. Critical prioritization, reserved budget, chair fallback
# ===========================================================================
async def test_chair_retry_synthesizes_over_recovered_agents(retry_on) -> None:
    # financial_analyst fails then recovers; the chair also fails then recovers,
    # and its rebuilt user message must include the recovered agent's summary.
    fake = FakeLLMClient(
        agent_failures={
            AGENT_FINANCIAL_ANALYST: [LLMTimeoutError("t")],
            AGENT_COMMITTEE_CHAIR: [LLMTimeoutError("t")],
        }
    )
    result = await _run(_financial_pack(), fake)
    assert _status(result)[AGENT_COMMITTEE_CHAIR] == STATUS_COMPLETED
    chair_prompts = fake.user_prompts[AGENT_COMMITTEE_CHAIR]
    assert len(chair_prompts) == 2  # initial + retry
    # Initial prompt: financial_analyst had failed, so its line is absent.
    assert "- financial_analyst:" not in chair_prompts[0]
    # Retry prompt: rebuilt over the recovered agent -> its summary line present.
    assert "- financial_analyst:" in chair_prompts[-1]
    assert result.chair_fallback_used is False


async def test_reserved_budget_protects_red_team_and_chair(retry_on, monkeypatch) -> None:
    # Non-reserved agents drain the budget past (deadline - reserve); the two
    # RESERVED agents (red_team, committee_chair) still retry and complete.
    monkeypatch.setattr(app_settings, "llm_council_total_budget_seconds", 100.0)
    monkeypatch.setattr(app_settings, "llm_council_critical_reserve_seconds", 40.0)
    monkeypatch.setattr(app_settings, "llm_council_retry_max_retry_after_seconds", 40.0)
    # Deterministic waits via retry-after (no jitter): each retry sleeps 30s.
    ra = LLMRateLimitError
    fake = FakeLLMClient(
        agent_failures={
            "business_moat": [ra(retry_after=30.0)] * 3,
            "catalyst": [ra(retry_after=30.0)] * 3,
            "red_team": [ra(retry_after=30.0)],
            AGENT_COMMITTEE_CHAIR: [ra(retry_after=30.0)],
        }
    )
    clock = FakeClock(1000.0)
    sleeper = FakeSleeper(clock)
    # Use the SMALL pack so valuation_guard stays optional and does not interfere.
    result = await _run(_small_pack(), fake, clock=clock, sleeper=sleeper)
    st = _status(result)
    # Reserved agents were protected and completed.
    assert st["red_team"] == STATUS_COMPLETED
    assert st[AGENT_COMMITTEE_CHAIR] == STATUS_COMPLETED
    assert fake.calls["red_team"] == 2  # initial + one protected retry
    # A non-reserved agent was starved by the drained budget (never re-attempted).
    assert fake.calls["catalyst"] == 1
    assert st["catalyst"] == STATUS_FAILED
    assert result.chair_fallback_used is False


async def test_optional_agent_failure_does_not_block_council(retry_on) -> None:
    fake = FakeLLMClient(agent_failures={"business_moat": [LLMTimeoutError("t")] * 6})
    result = await _run(_small_pack(), fake)
    assert _status(result)["business_moat"] == STATUS_FAILED
    # The rest of the council (incl. chair) still completes.
    assert _status(result)[AGENT_COMMITTEE_CHAIR] == STATUS_COMPLETED
    assert result.chair_fallback_used is False


async def test_deterministic_chair_fallback_on_chair_exhaustion(retry_on) -> None:
    fake = FakeLLMClient(
        agent_failures={AGENT_COMMITTEE_CHAIR: [LLMTimeoutError("t")] * 8}
    )
    result = await _run(_financial_pack(), fake)
    chair = next(a for a in result.agents if a.agent_name == AGENT_COMMITTEE_CHAIR)
    # The failed LLM chair entry is KEPT (visibly partial), fallback attached.
    assert chair.status == STATUS_FAILED
    assert result.chair_fallback_used is True
    assert result.committee_label == DEFAULT_COMMITTEE_LABEL
    assert result.deterministic_chair is not None
    assert result.deterministic_chair.committee_label == DEFAULT_COMMITTEE_LABEL
    assert result.deterministic_chair.key_points == []  # empty => no citations
    # No forbidden recommendation/valuation language anywhere.
    assert safety_terms.scan_value(result.to_report_dict()) == []
    assert safety_terms.scan_value(result.deterministic_chair.model_dump()) == []
    text = str(result.deterministic_chair.model_dump())
    for token in FORBIDDEN_SUBSTRINGS:
        assert token not in text
    # Chair critical -> 1 initial + critical_max_retries attempts.
    assert fake.calls[AGENT_COMMITTEE_CHAIR] == (
        1 + app_settings.llm_council_critical_max_retries
    )


async def test_chair_fallback_surfaced_in_serialization(retry_on) -> None:
    fake = FakeLLMClient(
        agent_failures={AGENT_COMMITTEE_CHAIR: [LLMTimeoutError("t")] * 8}
    )
    result = await _run(_financial_pack(), fake)
    report_dict = result.to_report_dict()
    metadata = result.to_metadata_dict()
    assert report_dict["chair_fallback_used"] is True
    assert report_dict["deterministic_committee_chair"]["agent_name"] == (
        AGENT_COMMITTEE_CHAIR
    )
    assert metadata["chair_fallback_used"] is True
    assert metadata["committee_label"] == DEFAULT_COMMITTEE_LABEL


# ===========================================================================
# 6. Partial councils matrix
# ===========================================================================
async def test_partial_8_of_8(retry_on) -> None:
    result = await _run(_financial_pack(), FakeLLMClient())
    assert result.agents_completed == 8
    assert result.agents_failed == 0
    assert result.chair_fallback_used is False
    assert safety_terms.scan_value(result.to_report_dict()) == []


async def test_partial_7_of_8_optional_permanent(retry_on) -> None:
    fake = FakeLLMClient(agent_failures={"business_moat": [LLMUnavailableError("x")]})
    result = await _run(_financial_pack(), fake)
    assert result.agents_completed == 7
    assert result.agents_failed == 1
    assert _status(result)["business_moat"] == STATUS_FAILED
    assert result.chair_fallback_used is False


async def test_partial_4_of_8(retry_on) -> None:
    # Four agents fail permanently (never retried); chair still completes.
    fake = FakeLLMClient(
        agent_failures={
            "business_moat": [LLMUnavailableError("x")],
            "catalyst": [LLMUnavailableError("x")],
            "risk_governance": [LLMUnavailableError("x")],
            "valuation_guard": [LLMUnavailableError("x")],
        }
    )
    result = await _run(_financial_pack(), fake)
    assert result.agents_completed == 4
    assert result.agents_failed == 4
    assert len(result.agents) == 8
    assert result.chair_fallback_used is False
    assert safety_terms.scan_value(result.to_report_dict()) == []


async def test_partial_chair_failed(retry_on) -> None:
    fake = FakeLLMClient(
        agent_failures={AGENT_COMMITTEE_CHAIR: [LLMUnavailableError("x")]}
    )
    result = await _run(_financial_pack(), fake)
    assert result.agents_completed == 7
    assert _status(result)[AGENT_COMMITTEE_CHAIR] == STATUS_FAILED
    assert result.chair_fallback_used is True
    assert result.committee_label == DEFAULT_COMMITTEE_LABEL


async def test_partial_red_team_failed(retry_on) -> None:
    fake = FakeLLMClient(agent_failures={"red_team": [LLMUnavailableError("x")]})
    result = await _run(_financial_pack(), fake)
    assert _status(result)["red_team"] == STATUS_FAILED
    assert _status(result)[AGENT_COMMITTEE_CHAIR] == STATUS_COMPLETED
    assert result.chair_fallback_used is False
    warnings = [w for w in result.warnings if w.startswith("red_team:")]
    assert warnings  # the failure is honestly recorded


async def test_partial_financial_analyst_failed(retry_on) -> None:
    # financial_analyst is CRITICAL -> exhausts critical_max_retries then fails.
    queue = [LLMTimeoutError("t")] * 8
    fake = FakeLLMClient(agent_failures={AGENT_FINANCIAL_ANALYST: queue})
    result = await _run(_financial_pack(), fake)
    assert _status(result)[AGENT_FINANCIAL_ANALYST] == STATUS_FAILED
    assert fake.calls[AGENT_FINANCIAL_ANALYST] == (
        1 + app_settings.llm_council_critical_max_retries
    )
    assert result.agents_completed == 7


async def test_partial_all_unavailable(retry_on) -> None:
    fake = FakeLLMClient(
        agent_failures={a: [LLMUnavailableError("x")] for a in COUNCIL_AGENT_ORDER}
    )
    result = await _run(_financial_pack(), fake)
    assert result.agents_failed == 8
    assert result.agents_completed == 0
    # Permanent errors are never retried.
    assert all(fake.calls[a] == 1 for a in COUNCIL_AGENT_ORDER)
    # The chair failed -> deterministic fallback fires, still safe + no rec text.
    assert result.chair_fallback_used is True
    assert result.committee_label == DEFAULT_COMMITTEE_LABEL
    assert safety_terms.scan_value(result.to_report_dict()) == []


# ===========================================================================
# 7. Idempotency
# ===========================================================================
async def test_idempotent_one_entry_per_agent_after_retries(retry_on) -> None:
    fake = FakeLLMClient(
        agent_failures={
            AGENT_FINANCIAL_ANALYST: [LLMTimeoutError("t")],
            "catalyst": [LLMServerError("5xx")],
            AGENT_COMMITTEE_CHAIR: [LLMRateLimitError(retry_after=1.0)],
        }
    )
    result = await _run(_financial_pack(), fake)
    names = [a.agent_name for a in result.agents]
    assert names == list(COUNCIL_AGENT_ORDER)  # exactly one entry per name, in order
    assert len(result.agents) == 8


async def test_repeated_runs_stable_counts(retry_on) -> None:
    def _fake():
        return FakeLLMClient(
            agent_failures={"catalyst": [LLMTimeoutError("t")]}
        )

    r1 = await _run(_financial_pack(), _fake())
    r2 = await _run(_financial_pack(), _fake())
    assert (r1.agents_completed, r1.agents_failed) == (
        r2.agents_completed,
        r2.agents_failed,
    )
    assert r1.agents_completed == 8


# ===========================================================================
# 8. Backward-compatibility (flag OFF)
# ===========================================================================
async def test_off_single_attempt_no_retry() -> None:
    # Flag OFF (default): a transient failure is NOT retried.
    fake = FakeLLMClient(agent_failures={AGENT_FINANCIAL_ANALYST: [LLMTimeoutError("t")]})
    result = await _run(_financial_pack(), fake)
    assert _status(result)[AGENT_FINANCIAL_ANALYST] == STATUS_FAILED
    assert fake.calls[AGENT_FINANCIAL_ANALYST] == 1
    assert result.chair_fallback_used is False


async def test_off_chair_failure_yields_null_label_no_fallback() -> None:
    fake = FakeLLMClient(agent_failures={AGENT_COMMITTEE_CHAIR: [LLMTimeoutError("t")]})
    result = await _run(_financial_pack(), fake)
    assert _status(result)[AGENT_COMMITTEE_CHAIR] == STATUS_FAILED
    assert result.committee_label is None
    assert result.chair_fallback_used is False
    assert result.deterministic_chair is None
    # No new serialization keys leak in the OFF path.
    assert "chair_fallback_used" not in result.to_report_dict()
    assert "chair_fallback_used" not in result.to_metadata_dict()


async def test_off_path_byte_identical_to_baseline(monkeypatch) -> None:
    # A clean run with the flag OFF must serialize identically to a run made by
    # the pre-Slice-4 code path (single pass, no new keys). We assert the ON-path
    # 8/8 result serializes identically to the OFF-path 8/8 result.
    off = await _run(_financial_pack(), FakeLLMClient())
    monkeypatch.setattr(app_settings, "llm_council_retry_enabled", True)
    on = await _run(_financial_pack(), FakeLLMClient())
    assert off.to_report_dict() == on.to_report_dict()
    assert off.to_metadata_dict() == on.to_metadata_dict()
    assert off.chair_fallback_used is False and on.chair_fallback_used is False


async def test_cfr_small_pack_8_of_8(retry_on) -> None:
    # The small metadata-only pack completes 8/8 with valuation_guard optional.
    result = await _run(_small_pack(), FakeLLMClient())
    assert result.agents_completed == 8
    assert result.chair_fallback_used is False
    assert has_financial_evidence(_small_pack()) is False


async def test_persistable_evidence_still_built_when_flag_on(retry_on, monkeypatch) -> None:
    # Slice-3 invariant: with persistence on, run_council still snapshots the pack.
    monkeypatch.setattr(app_settings, "report_citation_persistence_enabled", True)
    result = await _run(_financial_pack(), FakeLLMClient())
    assert result.persistable_evidence  # snapshot present for citation resolution


# ===========================================================================
# 9. Safe retry logging (security invariant)
# ===========================================================================
async def test_retry_logs_are_safe(retry_on, caplog) -> None:
    fake = FakeLLMClient(
        agent_failures={AGENT_FINANCIAL_ANALYST: [LLMRateLimitError(retry_after=5.0)]}
    )
    with caplog.at_level(logging.WARNING, logger="app.services.llm.council"):
        await _run(_financial_pack(), fake, ticker="AAPL", report_id="r-1")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "llm_agent_retry" in text
    assert "error_type=LLMRateLimitError" in text
    assert "attempt=1" in text
    assert "retry_after=5.0" in text  # bare numeric seconds
    # Never any prompt, evidence excerpt, completion body, or secret.
    assert "SECURITY:" not in text
    assert "HARD RULES" not in text
    assert "Deterministic fake summary" not in text
    assert "383285" not in text


# ===========================================================================
# 10. Report-level integration (partial council + flag ON, real SQLite)
# ===========================================================================
@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


async def test_report_integration_partial_council_flag_on(
    session, retry_on, monkeypatch
) -> None:
    """A partial (chair-failed -> deterministic fallback) council persists through
    a real report build with every product-safety invariant preserved."""
    monkeypatch.setattr(app_settings, "report_citation_persistence_enabled", True)

    # Run the REAL Slice-4 council (retry ON) with the chair failing PERMANENTLY
    # (LLMUnavailableError -> not retried -> deterministic fallback, no sleeps).
    async def _real_council(*args, **kwargs):
        fake = FakeLLMClient(
            agent_failures={AGENT_COMMITTEE_CHAIR: [LLMUnavailableError("down")]}
        )
        return await run_council(
            build_evidence_pack(
                report_content=_aapl_report_content(),
                company_snapshot=_aapl_snapshot(),
            ),
            fake,
            cfg=app_settings,
            report_id=kwargs.get("report_id"),
            ticker=kwargs.get("ticker"),
        )

    monkeypatch.setattr(final_report_generator, "maybe_run_council", _real_council)

    company = Company(
        id=uuid.uuid4(),
        ticker="AAPL",
        exchange="NASDAQ",
        name="Apple Inc.",
        country="US",
        sector="Technology",
        industry="Consumer Electronics",
        status="new",
    )
    session.add(company)
    run = AgentRun(
        id=uuid.uuid4(),
        workflow_name="company_analysis",
        workflow_version="1.0.0",
        status="completed",
        started_at=_utcnow(),
        trigger_type="manual",
    )
    session.add(run)
    await session.flush()
    source_report = Report(
        id=uuid.uuid4(),
        title="AAPL draft",
        slug=f"draft-{uuid.uuid4().hex[:12]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="draft",
        content_markdown="# Analysis Council Draft",
        created_by_agent_run_id=run.id,
        company_id=company.id,
        human_review_required=True,
        created_at=_utcnow(),
    )
    session.add(source_report)
    await session.commit()

    resp = await FinalReportGeneratorService().generate_from_company(session, company.id)

    # Product-safety invariants preserved.
    assert resp.publication_ready is False
    assert resp.human_review_required is True
    assert resp.schema_valid is True
    assert resp.safety_valid is True
    # The chair failed, so at least one agent is failed and the fallback fired.
    assert resp.council_agents_failed >= 1
    assert resp.committee_label == DEFAULT_COMMITTEE_LABEL

    final = (
        await session.execute(select(Report).where(Report.id == resp.report_id))
    ).scalar_one()

    # Completed-agent council citations persisted; the FAILED chair created none.
    council_cits = (
        await session.execute(
            select(Citation).where(
                Citation.report_id == final.id,
                Citation.field_path.like("council:%"),
            )
        )
    ).scalars().all()
    assert council_cits  # completed agents' cited evidence persisted
    field_paths = {c.field_path for c in council_cits}
    assert f"council:{AGENT_COMMITTEE_CHAIR}" not in field_paths
    # Every persisted council citation resolves to a real Source row.
    src_ids = {c.source_id for c in council_cits}
    sources = (
        await session.execute(select(Source).where(Source.id.in_(src_ids)))
    ).scalars().all()
    assert len(sources) == len(src_ids)

    # The deterministic fallback marker is surfaced and carries no forbidden text.
    assert final.source_summary_json["llm_council"]["chair_fallback_used"] is True
    for token in FORBIDDEN_SUBSTRINGS:
        assert token not in final.content_markdown

    # Idempotency: regenerating yields stable, non-duplicated council-citation counts.
    resp2 = await FinalReportGeneratorService().generate_from_company(session, company.id)
    council_cits2 = (
        await session.execute(
            select(Citation).where(
                Citation.report_id == resp2.report_id,
                Citation.field_path.like("council:%"),
            )
        )
    ).scalars().all()
    assert len(council_cits2) == len(council_cits)
