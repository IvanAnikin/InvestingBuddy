"""
Phase 32A — Deep Field Review chair output budget scales with field size.

WHY: live staging (2026-08-23) ran a real 7-company Deep Field Review. Seven of
eight agents completed; the CHAIR failed with ``LLMJsonError``. Root cause: the
field-review council passed the company council's FLAT
``llm_max_output_tokens`` (1200) for every agent regardless of how many
companies were being compared, while the discovery council already scaled its
budget with candidate count. The chair emits the per-company ``company_notes``
AND a three-bucket ``chair_verdict`` in which every company appears once, so it
is the first to overflow — and an unparseable reply is PERMANENT (never
retried; the one-shot repair reuses the same budget).

Everything here uses the deterministic fake field-review client — no network,
no credentials, no real Azure dependency.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.core.config import Settings
from app.core.config import settings as app_settings
from app.services.llm.client import LLMJsonError
from app.services.llm.fake_field_review_client import FakeFieldReviewLLMClient
from app.services.llm.field_review_council import (
    field_review_max_output_tokens,
    run_field_review_council,
)
from app.services.llm.field_review_schemas import (
    AGENT_COMPARATIVE_FINANCIAL_QUALITY,
    AGENT_FIELD_CHAIR,
    FIELD_REVIEW_AGENT_ORDER,
    STATUS_COMPLETED,
    STATUS_FAILED,
    FieldReviewCompanySummary,
    FieldReviewPack,
    FieldRunContext,
    FieldRunFact,
)
from app.services.llm.token_pacer import TokenBudgetPacer, estimate_request_tokens


def _cfg(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "llm_council_enabled": True,
        "llm_field_review_council_enabled": True,
        "llm_provider_council": "fake",
        "llm_field_review_council_retry_enabled": False,
    }
    base.update(over)
    return Settings(**base)


def _pack(n: int) -> FieldReviewPack:
    return FieldReviewPack(
        run=FieldRunContext(
            discovery_run_id=str(uuid.uuid4()),
            mode="thesis",
            status="completed",
            candidate_count=n,
            analyzed_candidate_count=n,
            included_company_count=n,
        ),
        run_facts=[FieldRunFact(id="R1", label="run_shape", detail="Run detail.")],
        companies=[
            FieldReviewCompanySummary(
                id=f"F{i + 1}",
                discovery_candidate_id=str(uuid.uuid4()),
                report_id=str(uuid.uuid4()),
                ticker=f"TCK{i + 1}",
                exchange="US",
                company_name=f"Company {i + 1}",
                data_provenance="real",
            )
            for i in range(n)
        ],
        known_gaps=["A known, honest gap."],
    )


# ---------------------------------------------------------------------------
# The scaling function itself
# ---------------------------------------------------------------------------
def test_budget_scales_and_chair_always_exceeds_its_peers() -> None:
    for n in (0, 2, 3, 7, 12):
        agent = field_review_max_output_tokens(n)
        chair = field_review_max_output_tokens(n, is_chair=True)
        assert chair > agent, f"chair must exceed a peer agent at n={n}"
        assert agent >= app_settings.llm_field_review_max_output_tokens_base
    # Strictly increasing in company count (below the cap).
    assert (
        field_review_max_output_tokens(2, is_chair=True)
        < field_review_max_output_tokens(3, is_chair=True)
        < field_review_max_output_tokens(7, is_chair=True)
        < field_review_max_output_tokens(12, is_chair=True)
    )


def test_budget_is_hard_capped_and_floors_garbage_counts() -> None:
    cap = app_settings.llm_field_review_max_output_tokens_cap
    # D: growth is bounded — an absurd count cannot allocate unbounded tokens.
    assert field_review_max_output_tokens(10_000, is_chair=True) == cap
    assert field_review_max_output_tokens(10_000) == cap
    # Negative/garbage floors at zero companies, i.e. exactly ``base``.
    assert field_review_max_output_tokens(-5) == (
        app_settings.llm_field_review_max_output_tokens_base
    )


def test_max_supported_company_count_is_not_clipped_by_the_cap() -> None:
    """D: the configured maximum field size must fit WITHOUT hitting the cap.

    The cap exists to stop a raised company cap from making one call unbounded,
    not to silently truncate the supported maximum.
    """
    max_companies = app_settings.llm_field_review_council_max_companies
    chair = field_review_max_output_tokens(max_companies, is_chair=True)
    assert chair < app_settings.llm_field_review_max_output_tokens_cap


def test_seven_company_chair_budget_exceeds_the_old_flat_value() -> None:
    """C: the exact live-failure shape now gets materially more room."""
    old_flat = app_settings.llm_max_output_tokens  # 1200, the failing value
    chair = field_review_max_output_tokens(7, is_chair=True)
    assert chair > old_flat
    assert chair >= 4 * old_flat


def test_prompt_contract_bounds_per_company_output() -> None:
    """The budget can only work if the CONTRACT bounds per-company output.

    Live regression (2026-08-23): with the rationale unbounded, richer packs
    made agents write proportionally more and SEVEN of eight truncated even on
    a scaled budget. The discovery council bounds its rationale for exactly
    this reason; the field review must too, or no cap is ever "enough".
    """
    from app.services.llm import field_review_prompts as prompts

    contract = prompts.JSON_CONTRACT
    assert '"rationale": "<=200 chars' in contract
    assert '"claim": "<=200 chars"' in contract
    assert "<=600 chars" in contract  # summary bound retained
    chair_prompt = prompts.field_chair_system_prompt()
    # The chair's verdict entries carry the same bound.
    assert chair_prompt.count("<=200 chars") >= 2
    assert "field_uncertainties" in chair_prompt


def test_chair_is_told_not_to_duplicate_per_company_output() -> None:
    """The chair must not describe every company twice.

    It emits ``chair_verdict`` (every company, exactly one bucket) AND could
    emit ``company_notes`` for the same companies. Only the verdict drives the
    persisted buckets, so duplicating doubled the chair's per-company output
    and truncated the verdict — the payload that actually matters.
    """
    from app.services.llm import field_review_prompts as prompts

    chair_prompt = prompts.field_chair_system_prompt()
    assert '"company_notes" EMPTY' in chair_prompt
    # Peer agents are NOT told to skip company_notes — that is their whole job.
    peer = prompts.system_prompt_for(AGENT_COMPARATIVE_FINANCIAL_QUALITY)
    assert '"company_notes" EMPTY' not in peer


# ---------------------------------------------------------------------------
# A / B / C — end-to-end through the council, valid JSON at every field size
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("n", [2, 3, 7])
async def test_council_completes_with_valid_chair_json_at_each_field_size(
    n: int,
) -> None:
    client = FakeFieldReviewLLMClient()
    result = await run_field_review_council(_pack(n), client, cfg=_cfg())

    assert result.agents_completed == len(FIELD_REVIEW_AGENT_ORDER)
    assert result.agents_failed == 0
    chair = next(a for a in result.agents if a.agent_name == AGENT_FIELD_CHAIR)
    assert chair.status == STATUS_COMPLETED
    assert chair.chair_verdict is not None
    assert result.chair_fallback_used is False
    assert result.chair_synthesis_basis == "llm_chair"

    # The SCALED budget actually reached the provider call, and the chair's is
    # strictly larger than a peer agent's in the same run.
    chair_budget = client.max_tokens_seen[AGENT_FIELD_CHAIR]
    peer_budget = next(
        v for k, v in client.max_tokens_seen.items() if k != AGENT_FIELD_CHAIR
    )
    assert chair_budget == field_review_max_output_tokens(n, is_chair=True)
    assert peer_budget == field_review_max_output_tokens(n)
    assert chair_budget > peer_budget
    # B/C: bigger field => bigger chair budget than the 2-company minimum.
    if n > 2:
        assert chair_budget > field_review_max_output_tokens(2, is_chair=True)


async def test_small_review_uses_the_minimum_end_of_the_curve() -> None:
    """A: a 2-company review stays near the floor — no wasteful allocation."""
    client = FakeFieldReviewLLMClient()
    await run_field_review_council(_pack(2), client, cfg=_cfg())
    chair_budget = client.max_tokens_seen[AGENT_FIELD_CHAIR]
    assert chair_budget == field_review_max_output_tokens(2, is_chair=True)
    assert chair_budget < field_review_max_output_tokens(
        app_settings.llm_field_review_council_max_companies, is_chair=True
    )


# ---------------------------------------------------------------------------
# E — the pacer must admit the ENLARGED chair request, not under-count it
# ---------------------------------------------------------------------------
async def test_pacer_estimate_includes_the_dynamic_chair_budget() -> None:
    """E: admission accounting must use the same scaled budget as the call.

    Under-counting the chair would re-introduce exactly the 429 starvation the
    TPM slice removed.
    """
    n = 7
    pacer = TokenBudgetPacer(capacity_tpm=60_000)
    client = FakeFieldReviewLLMClient()
    result = await run_field_review_council(
        _pack(n), client, cfg=_cfg(llm_council_tpm_capacity=60_000), pacer=pacer
    )
    assert result.agents_completed == len(FIELD_REVIEW_AGENT_ORDER)

    chair_budget = field_review_max_output_tokens(n, is_chair=True)
    # The window must have been charged at least the chair's own output budget
    # (prompt tokens and the other seven agents only add to this).
    assert pacer.used_in_window() > chair_budget
    # And the estimator is genuinely budget-sensitive.
    assert estimate_request_tokens("sys", "user", chair_budget) > (
        estimate_request_tokens("sys", "user", app_settings.llm_max_output_tokens)
    )


def test_estimate_request_tokens_tracks_the_output_budget() -> None:
    small = estimate_request_tokens("s", "u", field_review_max_output_tokens(2, is_chair=True))
    large = estimate_request_tokens("s", "u", field_review_max_output_tokens(12, is_chair=True))
    assert large - small == (
        field_review_max_output_tokens(12, is_chair=True)
        - field_review_max_output_tokens(2, is_chair=True)
    )


# ---------------------------------------------------------------------------
# F — a still-truncated reply is explicit, never an evidence judgement
# ---------------------------------------------------------------------------
async def test_truncated_chair_is_explicit_and_never_an_evidence_judgement() -> None:
    client = FakeFieldReviewLLMClient(truncate_agents={AGENT_FIELD_CHAIR})
    result = await run_field_review_council(
        _pack(7), client, cfg=_cfg(llm_field_review_council_retry_enabled=True)
    )

    chair = next(a for a in result.agents if a.agent_name == AGENT_FIELD_CHAIR)
    assert chair.status == STATUS_FAILED
    # The deterministic fallback stood in, and says so.
    assert result.chair_fallback_used is True
    assert result.chair_synthesis_basis == "deterministic_fallback"
    # The reason names TRUNCATION, not a generic parse failure — so a too-small
    # budget is diagnosable rather than looking like model misbehaviour.
    assert result.chair_error_type == "LLMJsonError_truncated"
    assert any("truncated" in w for w in result.warnings)
    # Field quality is the honest failure default, and the three priority
    # buckets stay EMPTY — a truncated reply never becomes a ranking.
    assert result.field_quality == "failed"
    assert result.strongest_candidates == []
    assert result.second_tier == []
    assert result.blocked_insufficient_evidence == []
    # Safety gates unchanged.
    stored = result.to_storage_dict()
    assert stored["human_review_required"] is True
    assert stored["publication_ready"] is False


async def test_truncation_is_permanent_not_retried_forever() -> None:
    """A truncated reply is PERMANENT: the repair reuses the same budget.

    Bounded behaviour matters — this must not spin.
    """
    client = FakeFieldReviewLLMClient(truncate_agents={AGENT_FIELD_CHAIR})
    result = await run_field_review_council(
        _pack(3), client, cfg=_cfg(llm_field_review_council_retry_enabled=True)
    )
    # One initial attempt only; LLMJsonError is not a transient error.
    assert client.attempts[AGENT_FIELD_CHAIR] == 1
    assert result.chair_error_type == "LLMJsonError_truncated"


def test_truncation_flag_defaults_off_for_ordinary_json_errors() -> None:
    assert LLMJsonError("bad json").truncated is False
    assert LLMJsonError("cut off", truncated=True).truncated is True


# ===========================================================================
# The real client must APPLY the per-call budget — with the RIGHT parameter
# ---------------------------------------------------------------------------
# Two live regressions, in order:
#  1. ``_complete_raw`` accepted ``max_tokens`` and never forwarded it, so every
#     real call used the CONSTRUCTION-time default (1200) and every count-aware
#     output budget in the codebase — this slice's and the discovery council's —
#     was inert against the real provider.
#  2. Forwarding it as ``max_tokens`` then broke EVERY call: langchain-openai
#     already translates the constructor's ``max_tokens`` into
#     ``max_completion_tokens``, so the payload carried both and Azure returned
#     HTTP 400 ``invalid_parameter_combination`` (verified live).
#
# The fake client honours whatever it is given, which is why neither defect
# showed up in unit tests. These exercise the REAL invocation path with a stub
# model object — no credentials, no network.
# ===========================================================================
class _StubChatModel:
    """Captures the kwargs a langchain chat model would receive."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))

        class _Result:
            content = '{"agent_name": "x", "status": "completed"}'
            usage_metadata = {
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            }
            response_metadata = {"finish_reason": "stop"}

        return _Result()


async def test_per_call_budget_uses_max_completion_tokens_only() -> None:
    """Both halves of the contract, in one assertion pair.

    Sending ``max_tokens`` alongside the constructor-derived
    ``max_completion_tokens`` is what produced HTTP 400 on every call.
    """
    from app.services.llm.azure_openai_client import _ainvoke_chat

    stub = _StubChatModel()
    await _ainvoke_chat(stub, "sys", "user", 30, max_tokens=8400)
    sent = stub.calls[-1]
    assert sent.get("max_completion_tokens") == 8400, (
        "the per-call output budget must reach the provider, or every "
        "count-aware budget in the codebase is silently inert"
    )
    assert "max_tokens" not in sent, (
        "sending max_tokens together with max_completion_tokens is rejected by "
        "Azure with invalid_parameter_combination"
    )


async def test_no_per_call_budget_leaves_the_constructor_default() -> None:
    from app.services.llm.azure_openai_client import _ainvoke_chat

    for value in (None, 0):
        stub = _StubChatModel()
        await _ainvoke_chat(stub, "sys", "user", 30, max_tokens=value)
        assert stub.calls[-1] == {}


async def test_real_client_complete_raw_passes_its_budget_through() -> None:
    """End of the chain: ``_complete_raw(max_tokens=N)`` must reach ainvoke."""
    from app.services.llm.azure_openai_client import AzureOpenAILLMClient

    client = AzureOpenAILLMClient.__new__(AzureOpenAILLMClient)  # no credentials
    stub = _StubChatModel()
    client._llm = stub  # type: ignore[attr-defined]
    await client._complete_raw(
        "sys", "user", max_tokens=6040, temperature=0.1, timeout=30
    )
    assert stub.calls[-1]["max_completion_tokens"] == 6040
