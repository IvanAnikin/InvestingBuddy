"""V3.19.11 — defects READ in the live critical-materials council review (run 4853103c).

1. "Size mismatch with user request" was said of CLF, whose size was UNKNOWN (a $3.7bn
   float floor proves nothing for a micro/small/mid request) — a size verdict slipped
   past a guard that only knew band words, via the requested-language exemption.
2. "The run returned 10 candidates …, mostly micro to small caps" stood as generic
   prose: "10 candidates" / "mostly" were not recognised as the cohort.
3. The three companies discovery found and verified from official sources (Rainbow
   Rare Earths, Pensana, Eramet) were REJECTED for having no research yet.
4. The guard's inputs lacked eligibility, so its "still unverified" note never fired,
   and the API response model dropped the guard's record entirely.
"""

from __future__ import annotations

import uuid

import pytest

from app.schemas.market_discovery import DiscoveryCouncilReviewResponse
from app.services.discovery.attribute_guard import (
    GAP_NOT_REJECTION_NOTE,
    GAP_NOT_REJECTION_NOTE_UNVERIFIED,
    CandidateAttributes,
    check_sentence,
    guard_review,
)


def _cand(ticker, *, size=None, bucket=None, eligibility="eligible", research=False,
          unknown=()):
    return {
        "candidate_id": f"id-{ticker}", "ticker": ticker,
        "verified_attributes": {"size_bucket": bucket} if bucket else {},
        "constraint_status": {"size": size} if size else {},
        "eligibility": eligibility, "unknown_constraints": list(unknown),
        "has_current_research": research,
    }


CANDS = [
    _cand("RBW", size="pass", bucket="micro_cap"),
    _cand("PRE", size="pass", bucket="micro_cap"),
    _cand("ERA", size="pass", bucket="small_cap"),
    _cand("CLF", size="unknown", research=True, unknown=("size",)),
    _cand("FCX", size="fail", research=True, eligibility="included_with_mismatch"),
]
ATTRS = CandidateAttributes(CANDS)
CLF = ATTRS.lookup("CLF")
FCX = ATTRS.lookup("FCX")


def test_size_mismatch_needs_a_failed_size_constraint():
    sentence = "Size mismatch with user request"
    assert check_sentence(sentence, ATTRS, CLF) is not None  # size unknown
    assert check_sentence(sentence, ATTRS, FCX) is None  # size verified to FAIL
    assert check_sentence("CLF looks too large for the brief.", ATTRS, None) is not None
    assert check_sentence("CLF is larger than requested.", ATTRS, None) is not None
    # A negated match IS a mismatch claim.
    assert check_sentence("CLF does not fit the requested size.", ATTRS, None) is not None
    assert check_sentence("FCX does not fit the requested size.", ATTRS, None) is None
    # Saying the verdict is NOT established is exactly right, and stands.
    assert check_sentence("A size mismatch for CLF is not verified.", ATTRS, None) is None


def test_size_match_needs_a_passed_size_constraint():
    assert check_sentence("PRE fits the requested size.", ATTRS, None) is None
    assert check_sentence("CLF fits the requested size.", ATTRS, None) is not None


def test_run_level_band_claims_are_cohort_claims():
    sentence = ("The run returned 10 candidates focused on critical and rare earth "
                "materials in North America and Europe, mostly micro to small caps.")
    assert check_sentence(sentence, ATTRS, None) is not None
    # Counting verified attributes is exactly what the contract asks for.
    assert check_sentence("3 of 5 candidates have a verified micro or small cap.", ATTRS,
                          None) is None


def test_research_less_verified_candidates_are_insufficient_data_not_rejected():
    review = {
        "candidates_to_reject": [
            {"candidate_ref": "C1", "ticker": "RBW", "rationale": "no sourced fundamentals"},
            {"candidate_ref": "C2", "ticker": "PRE", "rationale": "no filings"},
            # Researched and excluded-by-mismatch: the council may still reject it.
            {"candidate_ref": "C5", "ticker": "FCX", "rationale": "size mismatch"},
        ],
        "candidates_insufficient_data": [],
    }
    out = guard_review(review, CANDS)
    assert [e["ticker"] for e in out["candidates_to_reject"]] == ["FCX"]
    moved = out["candidates_insufficient_data"]
    assert [e["ticker"] for e in moved] == ["RBW", "PRE"]
    assert all(e["placement_note"] == GAP_NOT_REJECTION_NOTE for e in moved)
    assert all(e["council_placement"] == "reject_for_now" for e in moved)
    assert moved[0]["rationale"] == "no sourced fundamentals"  # council's words kept
    assert out["attribute_guard"]["reclassified"] == [
        {"ticker": "RBW", "candidate_ref": "C1", "from": "candidates_to_reject",
         "to": "candidates_insufficient_data"},
        {"ticker": "PRE", "candidate_ref": "C2", "from": "candidates_to_reject",
         "to": "candidates_insufficient_data"},
    ]


def test_a_researched_candidate_may_still_be_rejected():
    cands = [_cand("SCCO", size="unknown", research=True)]
    review = {"candidates_to_reject": [{"ticker": "SCCO", "rationale": "weak margins"}]}
    out = guard_review(review, cands)
    assert [e["ticker"] for e in out["candidates_to_reject"]] == ["SCCO"]


def test_unverified_constraints_note_and_guard_record_reach_the_api():
    cands = [_cand("UHR", eligibility="eligible_unverified", unknown=("growth", "size"))]
    review = {"candidates_to_monitor": [{"ticker": "UHR", "rationale": "legacy only"}],
              "candidates_to_reject": []}
    stored = guard_review(review, cands)
    response = DiscoveryCouncilReviewResponse.from_envelope(
        uuid.uuid4(), {"status": "completed", "review": stored})
    body = response.model_dump()
    assert body["candidates_to_monitor"][0]["unverified_constraints"] == ["growth", "size"]
    assert body["attribute_guard"]["version"] == 2


RBW = ATTRS.lookup("RBW")


@pytest.mark.parametrize("sentence", [
    # "too small" about something other than the company's size is risk prose (rule 7).
    "Cash balance too small to fund the Phalaborwa build.",
    "Market share is too small to matter.",
    "The evidence set is too small to judge.",
    "The sample is too small a base for conclusions.",
    # About peers, not the candidate.
    "Peers show a size mismatch with the request.",
    # About what was asked for.
    "The user requested small caps; most candidates have unknown size.",
    "Mostly, the thesis asked for small caps.",
    "The user asked for small caps; 10 candidates were returned.",
])
def test_legitimate_prose_is_kept(sentence):
    assert check_sentence(sentence, ATTRS, RBW) is None


def test_a_named_candidate_among_peers_is_still_checked():
    assert check_sentence("Peers such as CLF are too large for the brief.", ATTRS,
                          RBW) is not None


def test_eligible_unverified_gets_an_honest_note_and_nothing_is_listed_twice():
    cands = [_cand("UHR", eligibility="eligible_unverified", unknown=("size",))]
    review = {
        "candidates_to_reject": [{"ticker": "UHR", "rationale": "no fundamentals"}],
        "candidates_insufficient_data": [{"ticker": "UHR", "rationale": "gaps"}],
    }
    out = guard_review(review, cands)
    assert out["candidates_to_reject"] == []
    [entry] = out["candidates_insufficient_data"]
    assert entry["placement_note"] == GAP_NOT_REJECTION_NOTE_UNVERIFIED
    assert "checked against official sources" not in entry["placement_note"]


def test_legacy_only_research_does_not_count_as_current():
    """The council is never shown legacy content, so it cannot reject on it."""
    cands = [_cand("KER", research=False)]
    out = guard_review({"candidates_to_reject": [{"ticker": "KER"}]}, cands)
    assert out["candidates_to_reject"] == []
