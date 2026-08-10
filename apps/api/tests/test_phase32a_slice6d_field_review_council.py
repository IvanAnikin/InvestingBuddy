"""
Phase 32A Slice 6D — Deep Field Review council orchestration.

All tests run with the deterministic FAKE field-review client only — no network,
no credentials. They cover the agent flow, bounded retry recovery, safety
quarantine of forbidden rating language, citation-id validation, and the chair
verdict's shape/safety across a full round trip (including adversarial output).
"""

from __future__ import annotations

import json
import random
import uuid
from typing import Any

import pytest

from app.core.config import Settings
from app.models.field_review import FieldReviewRun
from app.schemas.field_review import FieldReviewResponse
from app.services import safety_terms
from app.services.llm.fake_field_review_client import FakeFieldReviewLLMClient
from app.services.llm.field_review_citation_checker import check_and_sanitize
from app.services.llm.field_review_council import (
    field_review_council_enabled,
    get_field_review_llm_client,
    maybe_run_field_review_council,
    run_field_review_council,
)
from app.services.llm.field_review_schemas import (
    AGENT_COMPARATIVE_FINANCIAL_QUALITY,
    AGENT_FIELD_CHAIR,
    AGENT_FIELD_RED_TEAM,
    ALLOWED_FIELD_QUALITY,
    FIELD_REVIEW_AGENT_ORDER,
    STATUS_COMPLETED,
    STATUS_FAILED,
    FieldChairVerdict,
    FieldCompanyNote,
    FieldNote,
    FieldPriorityEntry,
    FieldReviewAgentOutput,
    FieldReviewCompanySummary,
    FieldReviewPack,
    FieldRunContext,
    FieldRunFact,
)

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
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _cfg(enabled: bool = True, **over: Any) -> Settings:
    base: dict[str, Any] = {
        "llm_council_enabled": enabled,
        "llm_field_review_council_enabled": enabled,
        "llm_provider_council": "fake",
        # Retries off by default in these tests so the plain flow is exercised;
        # the retry tests opt in explicitly.
        "llm_field_review_council_retry_enabled": False,
    }
    base.update(over)
    return Settings(**base)


def _pack(n: int = 3) -> FieldReviewPack:
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
                data_provenance="real" if i else "mock",
                caveats=[] if i else ["data_provenance=mock"],
            )
            for i in range(n)
        ],
        known_gaps=["A known, honest gap."],
    )


class _FakeClock:
    """A deterministic monotonic clock advanced only by the fake sleeper."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _sleeper_for(clock: _FakeClock):
    async def _sleep(seconds: float) -> None:
        clock.now += seconds

    return _sleep


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def test_client_requires_both_flags() -> None:
    assert get_field_review_llm_client(_cfg(True)) is not None
    assert (
        get_field_review_llm_client(
            _cfg(True, llm_field_review_council_enabled=False)
        )
        is None
    )
    assert (
        get_field_review_llm_client(_cfg(True, llm_council_enabled=False)) is None
    )


def test_enabled_helper_requires_both_flags() -> None:
    assert field_review_council_enabled(_cfg(True)) is True
    assert field_review_council_enabled(_cfg(False)) is False
    assert (
        field_review_council_enabled(_cfg(True, llm_council_enabled=False)) is False
    )


def test_fake_client_is_used_only_for_the_fake_provider() -> None:
    client = get_field_review_llm_client(_cfg(True))
    assert isinstance(client, FakeFieldReviewLLMClient)
    assert client.is_fake is True


@pytest.mark.anyio
async def test_disabled_returns_llm_not_used_and_never_fabricates() -> None:
    result = await maybe_run_field_review_council(pack=_pack(), cfg=_cfg(False))
    assert result.llm_used is False
    assert result.agents == []
    assert result.strongest_candidates == []


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_full_success_runs_all_eight_agents_in_order() -> None:
    result = await run_field_review_council(
        _pack(), FakeFieldReviewLLMClient(), cfg=_cfg()
    )
    assert [a.agent_name for a in result.agents] == list(FIELD_REVIEW_AGENT_ORDER)
    assert result.agents_completed == 8
    assert result.agents_failed == 0
    assert result.llm_used is True
    assert result.provider == "fake"
    assert result.company_count == 3


@pytest.mark.anyio
async def test_chair_verdict_places_every_company_into_exactly_one_bucket() -> None:
    result = await run_field_review_council(
        _pack(3), FakeFieldReviewLLMClient(), cfg=_cfg()
    )
    placed = [
        e["company_ref"]
        for bucket in (
            result.strongest_candidates,
            result.second_tier,
            result.blocked_insufficient_evidence,
        )
        for e in bucket
    ]
    assert sorted(placed) == ["F1", "F2", "F3"]
    assert len(placed) == len(set(placed))
    assert result.field_quality in ALLOWED_FIELD_QUALITY


@pytest.mark.anyio
async def test_priority_entries_carry_identity_from_the_pack_not_the_model() -> None:
    pack = _pack(2)
    result = await run_field_review_council(
        pack, FakeFieldReviewLLMClient(), cfg=_cfg()
    )
    all_entries = (
        result.strongest_candidates
        + result.second_tier
        + result.blocked_insufficient_evidence
    )
    by_ref = {e["company_ref"]: e for e in all_entries}
    assert by_ref["F1"]["ticker"] == "TCK1"
    assert by_ref["F1"]["report_id"] == pack.companies[0].report_id
    # Every entry cites at least its own company id.
    assert all(e["citation_ids"] for e in all_entries)


@pytest.mark.anyio
async def test_company_caveats_are_always_carried_into_the_chair_entry() -> None:
    """A mock-provenance company can never be presented as clean, even if the
    model returned no caveats for it."""
    result = await run_field_review_council(
        _pack(3), FakeFieldReviewLLMClient(), cfg=_cfg()
    )
    all_entries = (
        result.strongest_candidates
        + result.second_tier
        + result.blocked_insufficient_evidence
    )
    f1 = next(e for e in all_entries if e["company_ref"] == "F1")
    assert "data_provenance=mock" in f1["caveats"]


@pytest.mark.anyio
async def test_storage_dict_is_internal_only_and_never_publishable() -> None:
    result = await run_field_review_council(
        _pack(), FakeFieldReviewLLMClient(), cfg=_cfg()
    )
    stored = result.to_storage_dict(created_at="2026-08-10T00:00:00Z")
    assert stored["type"] == "deep_field_review"
    assert stored["human_review_required"] is True
    assert stored["publication_ready"] is False
    assert "NOT investment advice" in stored["disclaimer"]
    # Deployment is never persisted (it can name an internal Azure resource).
    assert "deployment" not in stored


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_timeout_fails_only_that_council_not_the_review() -> None:
    result = await run_field_review_council(
        _pack(), FakeFieldReviewLLMClient(mode="timeout"), cfg=_cfg()
    )
    assert result.agents_failed == 8
    assert result.agents_completed == 0
    assert result.field_quality == "failed"
    assert result.strongest_candidates == []


@pytest.mark.anyio
async def test_malformed_json_is_repaired_once() -> None:
    result = await run_field_review_council(
        _pack(), FakeFieldReviewLLMClient(mode="invalid_json_once"), cfg=_cfg()
    )
    assert result.agents_completed == 8


@pytest.mark.anyio
async def test_permanently_malformed_json_fails_the_agent_without_crashing() -> None:
    result = await run_field_review_council(
        _pack(), FakeFieldReviewLLMClient(mode="invalid_json_always"), cfg=_cfg()
    )
    assert result.agents_failed == 8
    assert result.agents_completed == 0


# ---------------------------------------------------------------------------
# Bounded retry
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("error", ["rate_limit", "server", "timeout"])
async def test_a_transient_failure_recovers_in_the_bounded_retry_pass(
    error: str,
) -> None:
    clock = _FakeClock()
    client = FakeFieldReviewLLMClient(
        transient_agents={AGENT_COMPARATIVE_FINANCIAL_QUALITY},
        transient_failures=1,
        transient_error=error,
    )
    result = await run_field_review_council(
        _pack(),
        client,
        cfg=_cfg(llm_field_review_council_retry_enabled=True),
        clock=clock,
        sleeper=_sleeper_for(clock),
        rng=random.Random(1234),
    )
    recovered = next(
        a for a in result.agents if a.agent_name == AGENT_COMPARATIVE_FINANCIAL_QUALITY
    )
    assert recovered.status == STATUS_COMPLETED
    assert result.agents_completed == 8
    assert client.attempts[AGENT_COMPARATIVE_FINANCIAL_QUALITY] == 2


@pytest.mark.anyio
async def test_retries_are_bounded_by_the_attempt_cap() -> None:
    """A permanently-transient agent stops after the configured attempts."""
    clock = _FakeClock()
    client = FakeFieldReviewLLMClient(
        transient_agents={AGENT_COMPARATIVE_FINANCIAL_QUALITY},
        transient_failures=99,
    )
    result = await run_field_review_council(
        _pack(),
        client,
        cfg=_cfg(
            llm_field_review_council_retry_enabled=True,
            llm_field_review_council_critical_max_retries=2,
        ),
        clock=clock,
        sleeper=_sleeper_for(clock),
        rng=random.Random(1234),
    )
    failed = next(
        a for a in result.agents if a.agent_name == AGENT_COMPARATIVE_FINANCIAL_QUALITY
    )
    assert failed.status == STATUS_FAILED
    # 1 initial attempt + at most 2 retries — strictly bounded.
    assert client.attempts[AGENT_COMPARATIVE_FINANCIAL_QUALITY] == 3


@pytest.mark.anyio
async def test_retries_are_bounded_by_the_total_wall_budget() -> None:
    """A tiny budget means the retry pass is skipped, not run forever."""
    clock = _FakeClock()
    client = FakeFieldReviewLLMClient(
        transient_agents={AGENT_COMPARATIVE_FINANCIAL_QUALITY},
        transient_failures=99,
    )
    result = await run_field_review_council(
        _pack(),
        client,
        cfg=_cfg(
            llm_field_review_council_retry_enabled=True,
            llm_field_review_council_total_budget_seconds=0.0,
            llm_field_review_council_critical_reserve_seconds=0.0,
        ),
        clock=clock,
        sleeper=_sleeper_for(clock),
        rng=random.Random(1234),
    )
    # Budget exhausted before ANY agent could start.
    assert result.agents_completed == 0
    assert result.agents_failed == 8
    assert client.attempts == {}
    assert any("budget_exhausted" in w for w in result.warnings)


@pytest.mark.anyio
async def test_a_quarantined_retry_outcome_is_permanent_and_not_retried() -> None:
    """A safety quarantine is a PERMANENT outcome — never retried into passing."""
    clock = _FakeClock()
    client = FakeFieldReviewLLMClient(
        transient_agents={AGENT_COMPARATIVE_FINANCIAL_QUALITY},
        transient_failures=1,
        forbidden_agents={AGENT_COMPARATIVE_FINANCIAL_QUALITY},
    )
    result = await run_field_review_council(
        _pack(),
        client,
        cfg=_cfg(llm_field_review_council_retry_enabled=True),
        clock=clock,
        sleeper=_sleeper_for(clock),
        rng=random.Random(1234),
    )
    agent = next(
        a for a in result.agents if a.agent_name == AGENT_COMPARATIVE_FINANCIAL_QUALITY
    )
    assert agent.status == STATUS_FAILED
    # 1 transient failure + 1 retry that quarantined -> stop. No further attempts.
    assert client.attempts[AGENT_COMPARATIVE_FINANCIAL_QUALITY] == 2
    assert result.safety_valid is False


# ---------------------------------------------------------------------------
# Safety quarantine
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_forbidden_rating_language_quarantines_the_agent_entirely() -> None:
    result = await run_field_review_council(
        _pack(),
        FakeFieldReviewLLMClient(
            forbidden_agents={AGENT_COMPARATIVE_FINANCIAL_QUALITY}
        ),
        cfg=_cfg(),
    )
    agent = next(
        a for a in result.agents if a.agent_name == AGENT_COMPARATIVE_FINANCIAL_QUALITY
    )
    assert agent.status == STATUS_FAILED
    # NOT sanitized-and-passed: everything from the agent is withheld.
    assert agent.company_notes == []
    assert agent.field_notes == []
    assert "Output withheld" in agent.summary
    # The quarantine note names the TIER, never the forbidden term itself.
    assert agent.safety_notes and "Quarantined" in agent.safety_notes[0]
    assert not any(t in agent.safety_notes[0] for t in ("BUY", "price target"))
    assert result.safety_valid is False


@pytest.mark.anyio
async def test_a_quarantined_chair_produces_no_priority_buckets() -> None:
    result = await run_field_review_council(
        _pack(), FakeFieldReviewLLMClient(forbidden_agents={AGENT_FIELD_CHAIR}), cfg=_cfg()
    )
    assert result.strongest_candidates == []
    assert result.second_tier == []
    assert result.blocked_insufficient_evidence == []
    assert result.field_quality in ALLOWED_FIELD_QUALITY


@pytest.mark.anyio
async def test_full_round_trip_output_contains_no_forbidden_language() -> None:
    """Even when EVERY agent is adversarially prompted to emit rating language,
    nothing forbidden survives into the stored payload."""
    result = await run_field_review_council(
        _pack(),
        FakeFieldReviewLLMClient(forbidden_agents=set(FIELD_REVIEW_AGENT_ORDER)),
        cfg=_cfg(),
    )
    stored = result.to_storage_dict()
    hits = safety_terms.scan_value(stored)
    assert hits == [], [h.term for h in hits]
    blob = json.dumps(stored)
    for term in FORBIDDEN_SUBSTRINGS:
        assert term not in blob, term
    assert result.safety_valid is False


@pytest.mark.anyio
async def test_clean_round_trip_output_is_also_forbidden_free() -> None:
    result = await run_field_review_council(
        _pack(), FakeFieldReviewLLMClient(), cfg=_cfg()
    )
    stored = result.to_storage_dict()
    assert safety_terms.scan_value(stored) == []
    blob = json.dumps(stored)
    for term in FORBIDDEN_SUBSTRINGS:
        assert term not in blob, term
    assert result.safety_valid is True


# ---------------------------------------------------------------------------
# Citation integrity
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_citation_ids_outside_the_pack_are_dropped() -> None:
    result = await run_field_review_council(
        _pack(),
        FakeFieldReviewLLMClient(
            bad_citation_agents={AGENT_COMPARATIVE_FINANCIAL_QUALITY}
        ),
        cfg=_cfg(),
    )
    agent = next(
        a for a in result.agents if a.agent_name == AGENT_COMPARATIVE_FINANCIAL_QUALITY
    )
    for note in agent.company_notes:
        assert all(cid in {"R1", "F1", "F2", "F3"} for cid in note.citation_ids)
    for note in agent.field_notes:
        assert all(cid in {"R1", "F1", "F2", "F3"} for cid in note.citation_ids)
    assert any("not present in the field pack" in w for w in result.warnings)


@pytest.mark.anyio
async def test_a_chair_entry_with_an_unknown_company_ref_is_dropped() -> None:
    """The chair may not invent a company that is not in the pack."""
    pack = _pack(2)
    output = FieldReviewAgentOutput(
        agent_name=AGENT_FIELD_CHAIR,
        chair_verdict=FieldChairVerdict(
            strongest_candidates=[
                FieldPriorityEntry(
                    company_ref="F1", rationale="Grounded.", citation_ids=["F1"]
                ),
                FieldPriorityEntry(
                    company_ref="F99", rationale="Invented.", citation_ids=["F99"]
                ),
            ],
            field_quality="adequate",
        ),
    )
    cleaned, issues = check_and_sanitize(
        output, pack.evidence_ids(), pack.company_ids(), is_chair=True
    )
    assert cleaned.chair_verdict is not None
    refs = [e.company_ref for e in cleaned.chair_verdict.strongest_candidates]
    assert refs == ["F1"]
    assert any("unknown company_ref" in i for i in issues)


def test_a_company_cannot_be_placed_in_two_buckets() -> None:
    pack = _pack(2)
    output = FieldReviewAgentOutput(
        agent_name=AGENT_FIELD_CHAIR,
        chair_verdict=FieldChairVerdict(
            strongest_candidates=[
                FieldPriorityEntry(company_ref="F1", citation_ids=["F1"])
            ],
            second_tier=[FieldPriorityEntry(company_ref="F1", citation_ids=["F1"])],
            field_quality="adequate",
        ),
    )
    cleaned, issues = check_and_sanitize(
        output, pack.evidence_ids(), pack.company_ids(), is_chair=True
    )
    assert cleaned.chair_verdict is not None
    assert len(cleaned.chair_verdict.strongest_candidates) == 1
    assert cleaned.chair_verdict.second_tier == []
    assert any("duplicate placement" in i for i in issues)


def test_an_uncited_material_claim_is_moved_to_unsupported_claims() -> None:
    pack = _pack(2)
    output = FieldReviewAgentOutput(
        agent_name=AGENT_COMPARATIVE_FINANCIAL_QUALITY,
        field_notes=[
            FieldNote(claim="Both companies are equally well evidenced.", citation_ids=[])
        ],
    )
    cleaned, issues = check_and_sanitize(
        output, pack.evidence_ids(), pack.company_ids()
    )
    assert cleaned.field_notes == []
    assert "Both companies are equally well evidenced." in cleaned.unsupported_claims
    assert any("un-cited material field claim" in i for i in issues)


def test_an_invalid_field_quality_is_coerced_to_the_safe_default() -> None:
    pack = _pack(1)
    output = FieldReviewAgentOutput(
        agent_name=AGENT_FIELD_CHAIR,
        chair_verdict=FieldChairVerdict(field_quality="excellent_strong_conviction"),
    )
    cleaned, issues = check_and_sanitize(
        output, pack.evidence_ids(), pack.company_ids(), is_chair=True
    )
    assert cleaned.chair_verdict is not None
    assert cleaned.chair_verdict.field_quality in ALLOWED_FIELD_QUALITY
    assert any("field_quality not in the allowed set" in i for i in issues)


def test_only_the_chair_may_carry_a_verdict() -> None:
    pack = _pack(1)
    output = FieldReviewAgentOutput(
        agent_name=AGENT_FIELD_RED_TEAM,
        chair_verdict=FieldChairVerdict(field_quality="strong"),
    )
    cleaned, _ = check_and_sanitize(output, pack.evidence_ids(), pack.company_ids())
    assert cleaned.chair_verdict is None


def test_an_invalid_confidence_is_coerced() -> None:
    pack = _pack(1)
    output = FieldReviewAgentOutput(
        agent_name=AGENT_COMPARATIVE_FINANCIAL_QUALITY,
        company_notes=[
            FieldCompanyNote(
                company_ref="F1",
                rationale="A grounded comparative note.",
                citation_ids=["F1"],
                confidence="extremely_high",
            )
        ],
    )
    cleaned, _ = check_and_sanitize(output, pack.evidence_ids(), pack.company_ids())
    assert cleaned.company_notes[0].confidence == "low"


def test_a_missing_chair_verdict_becomes_an_honest_empty_verdict() -> None:
    pack = _pack(1)
    output = FieldReviewAgentOutput(agent_name=AGENT_FIELD_CHAIR)
    cleaned, issues = check_and_sanitize(
        output, pack.evidence_ids(), pack.company_ids(), is_chair=True
    )
    assert cleaned.chair_verdict is not None
    assert cleaned.chair_verdict.strongest_candidates == []
    assert cleaned.chair_verdict.field_quality == "thin"
    assert any("no chair_verdict was returned" in i for i in issues)


# ---------------------------------------------------------------------------
# Deterministic field-chair fallback
# ---------------------------------------------------------------------------


async def _run_with_failing_chair(
    *, also_failing: set[str] | None = None, companies: int = 3
):
    """Run the council with the field chair failing through retry exhaustion."""
    clock = _FakeClock()
    client = FakeFieldReviewLLMClient(
        transient_agents={AGENT_FIELD_CHAIR, *(also_failing or set())},
        transient_failures=99,
        transient_error="rate_limit",
    )
    result = await run_field_review_council(
        _pack(companies),
        client,
        cfg=_cfg(
            llm_field_review_council_retry_enabled=True,
            llm_field_review_council_critical_max_retries=1,
            llm_field_review_council_max_retries=1,
        ),
        clock=clock,
        sleeper=_sleeper_for(clock),
        rng=random.Random(1234),
    )
    return result, client


@pytest.mark.anyio
async def test_a_failed_field_chair_gets_a_deterministic_fallback() -> None:
    """The Slice 6D defect: a failed chair used to leave ALL THREE buckets
    silently empty with no explanation anywhere in the payload."""
    result, client = await _run_with_failing_chair()

    # The chair really did exhaust its retries (1 initial + 1 retry).
    assert client.attempts[AGENT_FIELD_CHAIR] == 2

    # The ORIGINAL failed LLM chair entry is still honestly visible.
    llm_chair = next(a for a in result.agents if a.agent_name == AGENT_FIELD_CHAIR)
    assert llm_chair.status == STATUS_FAILED
    assert llm_chair.chair_verdict is None
    assert len(result.agents) == len(FIELD_REVIEW_AGENT_ORDER)
    assert result.agents_completed == 7
    assert result.agents_failed == 1

    # The fallback is an ADDITIONAL field, not a replacement.
    assert result.chair_fallback_used is True
    fallback = result.deterministic_field_chair
    assert isinstance(fallback, dict)
    assert fallback["agent_name"] == AGENT_FIELD_CHAIR
    assert fallback["status"] == STATUS_COMPLETED
    assert "Deterministic field chair summary" in fallback["summary"]
    assert "7 of 7 comparative agents completed" in fallback["summary"]
    assert fallback["safety_notes"]

    # No fabricated ranking: every bucket stays empty and the label is honest.
    assert result.strongest_candidates == []
    assert result.second_tier == []
    assert result.blocked_insufficient_evidence == []
    assert result.field_quality == "failed"
    assert result.field_uncertainties
    joined = " ".join(result.field_uncertainties)
    assert "field chair did not complete" in joined
    assert "NO comparative ranking" in joined
    assert "Human review" in joined


@pytest.mark.anyio
async def test_the_fallback_names_the_agents_that_did_and_did_not_complete() -> None:
    result, _ = await _run_with_failing_chair(
        also_failing={AGENT_COMPARATIVE_FINANCIAL_QUALITY}
    )
    fallback = result.deterministic_field_chair
    assert isinstance(fallback, dict)
    summary = fallback["summary"]
    assert "6 of 7 comparative agents completed" in summary
    # The still-usable agents are named ...
    assert AGENT_FIELD_RED_TEAM in summary
    # ... and so is the one that did not complete, honestly.
    assert f"Did not complete: {AGENT_COMPARATIVE_FINANCIAL_QUALITY}" in summary
    # The chair itself is never counted among the agents it is summarizing.
    assert "Completed: " in summary
    completed_part = summary.split("Completed: ")[1].split(". Did not complete")[0]
    assert AGENT_FIELD_CHAIR not in completed_part

    joined = " ".join(result.field_uncertainties)
    assert AGENT_COMPARATIVE_FINANCIAL_QUALITY in joined
    assert AGENT_FIELD_RED_TEAM in joined


@pytest.mark.anyio
async def test_the_fallback_places_no_company_in_any_bucket() -> None:
    """An empty ``blocked_insufficient_evidence`` is deliberate: the CHAIR
    failing says nothing about any company's own evidence."""
    result, _ = await _run_with_failing_chair(companies=3)
    fallback = result.deterministic_field_chair
    assert isinstance(fallback, dict)
    verdict = fallback["chair_verdict"]
    assert verdict["strongest_candidates"] == []
    assert verdict["second_tier"] == []
    assert verdict["blocked_insufficient_evidence"] == []
    assert verdict["field_quality"] == "failed"
    assert result.tier_by_company_ref() == {}


@pytest.mark.anyio
async def test_the_fallback_text_passes_the_safety_scanner() -> None:
    result, _ = await _run_with_failing_chair()
    stored = result.to_storage_dict()
    assert stored["chair_fallback_used"] is True
    assert stored["deterministic_field_chair"] is not None
    hits = safety_terms.scan_value(stored["deterministic_field_chair"])
    assert hits == [], [h.term for h in hits]
    blob = json.dumps(stored["deterministic_field_chair"])
    for term in FORBIDDEN_SUBSTRINGS:
        assert term not in blob, term
    # And the whole stored payload stays clean too.
    assert safety_terms.scan_value(stored) == []


@pytest.mark.anyio
async def test_a_completed_chair_never_triggers_the_fallback() -> None:
    """Regression guard: the healthy path is unchanged."""
    clock = _FakeClock()
    result = await run_field_review_council(
        _pack(3),
        FakeFieldReviewLLMClient(),
        cfg=_cfg(llm_field_review_council_retry_enabled=True),
        clock=clock,
        sleeper=_sleeper_for(clock),
        rng=random.Random(1234),
    )
    assert result.chair_fallback_used is False
    assert result.deterministic_field_chair is None
    assert result.agents_completed == 8
    placed = (
        len(result.strongest_candidates)
        + len(result.second_tier)
        + len(result.blocked_insufficient_evidence)
    )
    assert placed == 3
    assert result.field_quality in ALLOWED_FIELD_QUALITY
    assert result.field_quality != "failed"

    stored = result.to_storage_dict()
    assert stored["chair_fallback_used"] is False
    assert stored["deterministic_field_chair"] is None


@pytest.mark.anyio
async def test_the_fallback_survives_the_stored_payload_to_api_response() -> None:
    """Guards the field-preservation class of bug: ``from_row`` lists every
    field EXPLICITLY, so a new key must be read there or it is silently lost."""
    result, _ = await _run_with_failing_chair()
    stored = result.to_storage_dict(created_at="2026-08-10T00:00:00+00:00")

    discovery_run_id = uuid.uuid4()
    row = FieldReviewRun(
        id=uuid.uuid4(),
        discovery_run_id=discovery_run_id,
        status="completed_with_warnings",
        included_candidate_count=3,
        missing_candidate_count=0,
        llm_used=True,
        council_version="v1",
        provider="fake",
        model="fake-field-review-model",
        agents_completed=result.agents_completed,
        agents_failed=result.agents_failed,
        field_quality=result.field_quality,
        safety_valid=result.safety_valid,
        review_json=stored,
        warnings_json=list(result.warnings),
        human_review_required=True,
    )
    resp = FieldReviewResponse.from_row(discovery_run_id, row)

    assert resp.chair_fallback_used is True
    assert resp.deterministic_field_chair is not None
    assert "Deterministic field chair summary" in str(
        resp.deterministic_field_chair.get("summary")
    )
    # The failed LLM chair is still visible to the admin UI alongside it.
    assert resp.agent_outputs[AGENT_FIELD_CHAIR]["status"] == STATUS_FAILED
    # And the buckets are honestly empty, with the reason now explained.
    assert resp.strongest_candidates == []
    assert resp.second_tier == []
    assert resp.blocked_insufficient_evidence == []
    assert resp.field_quality == "failed"
    assert resp.field_uncertainties


def test_a_review_without_a_fallback_round_trips_as_not_used() -> None:
    discovery_run_id = uuid.uuid4()
    row = FieldReviewRun(
        id=uuid.uuid4(),
        discovery_run_id=discovery_run_id,
        status="completed",
        llm_used=True,
        review_json={"field_quality": "adequate"},
    )
    resp = FieldReviewResponse.from_row(discovery_run_id, row)
    assert resp.chair_fallback_used is False
    assert resp.deterministic_field_chair is None


# ---------------------------------------------------------------------------
# Safe logging
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_logs_never_contain_prompts_or_pack_text(caplog) -> None:
    caplog.set_level("INFO")
    await run_field_review_council(_pack(), FakeFieldReviewLLMClient(), cfg=_cfg())
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "DEEP FIELD REVIEW PACK" not in blob
    assert "HARD RULES" not in blob
    assert "Deterministic fake comparative summary" not in blob
    assert "field_review_council_completed" in blob
