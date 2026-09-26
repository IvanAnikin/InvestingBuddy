"""V3.19.5 — the discovery council may say only what is verified.

MUTATION GUARD (query attribute leakage): a council sentence that calls unverified
candidates "small-cap" or "growing" — because the USER asked for small, growing companies
— is removed before anyone reads it; a sentence about what was REQUESTED stands.
"""

from __future__ import annotations

import json

import pytest

from app.services.discovery.attribute_guard import CandidateAttributes, check_sentence, guard_review
from app.services.llm.discovery_evidence_pack import build_discovery_evidence_pack
from app.services.llm.discovery_prompts import system_prompt_for
from app.services.llm.discovery_schemas import AGENT_RUN_COORDINATOR

CANDIDATES = [
    {"candidate_id": "id-lvmh", "ticker": "MC",
     "verified_attributes": {"size_bucket": "mega_cap", "growth_status": "mixed"}},
    {"candidate_id": "id-mex", "ticker": "MEX",
     "verified_attributes": {"size_bucket": "small_cap", "growth_status": "established"}},
    {"candidate_id": "id-unk", "ticker": "UNK", "verified_attributes": {}},
]
ATTRS = CandidateAttributes(CANDIDATES)


@pytest.mark.parametrize(
    "sentence",
    [
        "All eight are small-cap European luxury companies.",
        "These small-cap names offer growth.",
        "C1 is a small-cap luxury house.",
        "MC is a growing small cap.",
        "C3 is a verified small-cap.",
        "All candidates meet the thesis's small-cap filter.",
        "The cohort consists of growing luxury companies.",
    ],
)
def test_unverified_attribute_claims_are_removed(sentence):
    assert check_sentence(sentence, ATTRS, None) is not None


@pytest.mark.parametrize(
    "sentence",
    [
        "The user requested small-cap companies.",
        "The thesis asked for growing companies; only 1 of 3 has verified growth.",
        "C2 is a small-cap with verified organic growth of 8%.",
        "Growth is not established for C3.",
        "C1 is far outside the small-cap band requested.",
        "MC is a mega-cap group.",
        "The luxury sector has pricing power.",
    ],
)
def test_supported_or_qualified_sentences_stand(sentence):
    assert check_sentence(sentence, ATTRS, None) is None


def test_a_candidate_note_is_judged_against_its_own_candidate():
    note = {"candidate_ref": "C3", "ticker": "UNK",
            "rationale": "A small-cap growing house. Strong brand heat."}
    out = guard_review({"agent_outputs": {"a": {"candidate_notes": [note]}}}, CANDIDATES)
    rationale = out["agent_outputs"]["a"]["candidate_notes"][0]["rationale"]
    assert "small-cap" not in rationale and "Strong brand heat." in rationale
    assert out["attribute_guard"]["removed_count"] == 1


def test_removed_list_items_are_dropped_and_recorded():
    bucket = [{"candidate_ref": "C1", "ticker": "MC",
               "upside_drivers": ["As a small-cap it has room to grow.", "Pricing power."]}]
    out = guard_review({"candidates_to_research_next": bucket}, CANDIDATES)
    drivers = out["candidates_to_research_next"][0]["upside_drivers"]
    assert drivers == ["Pricing power."]
    assert out["attribute_guard"]["removed"][0]["where"].startswith("candidates_to_research_next")


def test_pack_labels_requested_constraints_and_carries_verified_attributes():
    pack = build_discovery_evidence_pack(
        run={
            "run_id": "r", "mode": "thesis", "thesis_text": "small cap growing luxury",
            "requested_constraints": [
                {"key": "size", "requested": ["small_cap"], "excluded": [], "hardness": "hard"}
            ],
            "discovery_funnel": {"raw_leads": 20, "returned": 3},
        },
        candidates=[{
            "candidate_id": "id-mex", "ticker": "MEX", "momentum_score": 90.0,
            "momentum_label": "positive_momentum_candidate",
            "verified_attributes": {"size_bucket": "small_cap"},
            "constraint_status": {"size": "pass", "growth": "unknown"},
            "eligibility": "eligible_unverified",
        }],
    )
    fact = next(f for f in pack.run_facts if f.label == "requested_constraints")
    assert "NOT A PROPERTY OF ANY CANDIDATE" in fact.detail
    candidate = pack.candidates[0]
    assert candidate.verified_attributes == {"size_bucket": "small_cap"}
    assert candidate.constraint_status["growth"] == "unknown"
    dumped = json.dumps(pack.model_dump())
    # No momentum next to a business-growth question.
    assert "momentum" not in dumped


def test_prompts_carry_the_requested_vs_verified_contract():
    prompt = system_prompt_for(AGENT_RUN_COORDINATOR)
    assert "REQUESTED vs VERIFIED" in prompt
    assert "Price movement is never growth" in prompt


def test_finalisation_applies_the_guard_before_storage():
    from types import SimpleNamespace

    from app.models.discovery import DiscoveryRun
    from app.services import market_discovery_service as mds

    review = {
        "agent_outputs": {"chair": {"summary": "All candidates are small-cap growth names."}},
        "safety_valid": True,
    }
    result = SimpleNamespace(
        to_storage_dict=lambda created_at=None: dict(review), llm_used=True,
        agents_completed=8, agents_failed=0, _guard_candidates=CANDIDATES,
    )
    run = DiscoveryRun(config_json={})
    envelope = mds._finalize_council_review(run, result)
    stored = envelope["review"]
    assert "small-cap" not in stored["agent_outputs"]["chair"]["summary"]
    assert stored["attribute_guard"]["removed_count"] == 1


def test_candidate_evidence_never_carries_the_requested_size():
    """MUTATION GUARD: a candidate without verified attributes gets none from the query."""
    from app.models.discovery import DiscoveryCandidate
    from app.services import market_discovery_service as mds

    candidate = DiscoveryCandidate(ticker="UNK", exchange="PA", thesis_match_json={
        "v319": {"verified_attributes": {}, "constraint_results": [
            {"key": "size", "status": "unknown", "requested": ["small_cap"]}]}})
    evidence = mds._candidate_to_evidence_dict(candidate)
    assert evidence["verified_attributes"] == {}
    assert evidence["constraint_status"] == {"size": "unknown"}


async def test_deep_research_tests_size_against_the_verified_market_cap():
    """A EUR-listed candidate's verified cap reaches the V3.18 size_fit (no longer
    'unknown because non-USD'), and the intent's size request is what is tested."""
    import contextlib
    import uuid
    from types import SimpleNamespace

    from app.services.director.thesis import resolve_thesis, size_fit

    cand_id, run_id = uuid.uuid4(), uuid.uuid4()
    candidate = SimpleNamespace(
        id=cand_id, discovery_run_id=run_id, exchange="PA", market_cap_mln=None,
        score_explanation=None, created_at=None,
        thesis_match_json={"theme": "luxury_goods", "v319": {"verified_attributes": {
            "market_cap_usd": 31_500_000_000.0, "market_cap": {"as_of": "2026-09-20"}}}},
    )
    run = SimpleNamespace(
        thesis_text="small cap growing european luxury companies", config_json={},
        parsed_thesis_json={"themes": ["luxury_goods"], "size_hints": ["small_cap"],
                            "discovery_intent": {"constraints": [
                                {"key": "size", "requested": ["small_cap"]}]}},
    )

    class _Session:
        @contextlib.asynccontextmanager
        async def begin_nested(self):
            yield

        async def get(self, model, key):
            return candidate if key == cand_id else run

    context = await resolve_thesis(_Session(), discovery_candidate_id=cand_id)
    assert context.market_cap_usd == 31_500_000_000.0
    fit = size_fit(context, context.market_cap_usd)
    assert fit["fits"] is False


# ── review follow-ups ─────────────────────────────────────────────────────── #


@pytest.mark.parametrize(
    "sentence",
    [
        "C3 is a small-cap with no net debt.",
        "C3, a fast-growing house, lacks filings coverage.",
        "C3 is a small cap that exceeds expectations.",
        "C3 is a small‑cap jeweller.",
        "C3 is a small capitalisation stock.",
        "C3 is a smaller luxury brand.",
        "Growth-oriented C3 is expanding.",
        "unk is a small-cap.",
    ],
)
def test_more_leaks_are_removed(sentence):
    assert check_sentence(sentence, ATTRS, None) is not None


@pytest.mark.parametrize(
    "sentence",
    [
        "Large caps dominate the sector.",
        "The sector is growing.",
        "C3 operates in a growing market.",
        "C3 benefits from growing Chinese demand.",
        "C3 compares with mid-cap peers.",
        "C3 is not a small cap.",
        "C3's small-cap status is not verified.",
    ],
)
def test_generic_or_qualified_sentences_stand(sentence):
    assert check_sentence(sentence, ATTRS, None) is None


def test_separators_are_preserved_when_nothing_is_removed():
    text = "Para one.\n\nPara two.\n- bullet"
    out = guard_review({"agent_outputs": {"chair": {"summary": text}}}, CANDIDATES)
    assert out["agent_outputs"]["chair"]["summary"] == text


def test_research_next_on_an_unverified_candidate_is_labelled():
    candidates = [*CANDIDATES[:2], {**CANDIDATES[2], "eligibility": "eligible_unverified",
                                    "unknown_constraints": ["size"]}]
    out = guard_review({"candidates_to_research_next": [{"candidate_ref": "C3",
                                                         "rationale": "Strong brand."}]},
                       candidates)
    assert out["candidates_to_research_next"][0]["unverified_constraints"] == ["size"]


def test_a_leads_why_that_states_an_unverified_attribute_is_withheld():
    from app.services.discovery import constraints as c
    from app.services.discovery.identity import IdentityOutcome
    from app.services.discovery.leads import CompanyLead
    from app.services.discovery.pipeline import CandidateRecord

    lead = CompanyLead(name="X SA", ticker="XSA", exchange_raw="PA", country="France",
                       listing_source_url=None, evidence_url=None,
                       why="a fast-growing small-cap jeweller", source="external_search")
    identity = IdentityOutcome(lead=lead, status="verified", ticker="XSA", exchange="PA",
                               name="X SA")
    results = [c.verify_listing({"identity_status": "verified", "listing_source":
                                 {"url": "https://x.example", "tier": "issuer"}})]
    record = CandidateRecord(identity, results, c.decide_eligibility(results), None,
                             {"discovery_source": "external_search", "why": lead.why})
    payload = record.to_dict()
    assert payload["provenance"]["why"] is None
    assert "without" in payload["provenance"]["why_withheld"]
