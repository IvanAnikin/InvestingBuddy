"""Open-web W6b — the Discovery Council's web evidence pack and prompt contract.

What is pinned here, and why:

* the pack is BOUNDED (<= 6 items per candidate across theme relevance, catalysts and
  principal downside) and labelled with each item's source class;
* ``evidence_confidence`` is a separate field that qualifies and never ranks, and
  ``priority_basis`` keeps thesis fit, economics, catalyst relevance and size fit apart —
  the Council must not primarily say "Company A has more available data";
* a candidate with a web block carries no field-completeness figure;
* a run WITHOUT a web block is byte-identical to V3.19 (pack, prompt, aggregated output);
* the V3.19 requested-vs-verified contract and the attribute guard are untouched.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.discovery import council_pack as cp
from app.services.llm import discovery_prompts as prompts
from app.services.llm.discovery_citation_checker import check_and_sanitize
from app.services.llm.discovery_council import _aggregate_chair, run_discovery_council
from app.services.llm.discovery_evidence_pack import build_discovery_evidence_pack
from app.services.llm.discovery_schemas import (
    CandidateNote,
    DimensionAssessment,
    DiscoveryCouncilAgentOutput,
)
from app.services.llm.fake_discovery_client import FakeDiscoveryLLMClient


def m(
    evidence_id: str,
    *,
    dims: list[str],
    domain: str = "mining.com",
    source_class: str = "trade_publication",
    terms: tuple[str, ...] = ("gallium",),
    suspect: bool = False,
    passage: str = "Alpha Gallium (ASX: ALG) builds a gallium plant.",
    catalyst: tuple[str, ...] = (),
    risk: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "passage_ref": f"wp:a:{evidence_id}",
        "source_class": source_class,
        "domain": domain,
        "kind": "paragraph",
        "dimensions": dims,
        "theme_terms": list(terms),
        "catalyst_terms": list(catalyst),
        "risk_terms": list(risk),
        "passage": passage,
        "injection_suspect": suspect,
    }


def block(mentions: list[dict[str, Any]], state: str = "admitted") -> dict[str, Any]:
    return {
        "discovery_mode": "search",
        "mentions": mentions,
        "admission": {"state": state, "codes": [], "evidence_ids": []},
    }


def pack(mentions: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    out = cp.build_candidate_web_pack(block(mentions), **kw)
    assert out is not None
    return out


# --------------------------------------------------------------------------- #
# The candidate pack
# --------------------------------------------------------------------------- #


class TestThePackIsBounded:
    def test_at_most_six_items_across_the_three_dimensions(self) -> None:
        mentions = (
            [m(f"t{i}", dims=["theme_relevance"], domain=f"site{i}.com") for i in range(8)]
            + [
                m(f"c{i}", dims=["catalysts"], domain=f"cat{i}.com", catalyst=("offtake",))
                for i in range(5)
            ]
            + [
                m(f"d{i}", dims=["principal_downside"], domain=f"dn{i}.com", risk=("delay",))
                for i in range(5)
            ]
        )
        out = pack(mentions)
        assert len(out["items"]) == cp.MAX_PACK_ITEMS == 6
        by_dim = {
            d: sum(1 for i in out["items"] if i["dimension"] == d)
            for d in ("theme_relevance", "catalysts", "principal_downside")
        }
        assert by_dim == {"theme_relevance": 3, "catalysts": 2, "principal_downside": 1}
        assert out["dropped"] > 0

    def test_unused_slots_go_to_the_dimensions_that_have_items(self) -> None:
        out = pack([m(f"t{i}", dims=["theme_relevance"], domain=f"s{i}.com") for i in range(8)])
        assert len(out["items"]) == 6
        assert {i["dimension"] for i in out["items"]} == {"theme_relevance"}

    def test_at_most_three_items_per_origin(self) -> None:
        out = pack([m(f"t{i}", dims=["theme_relevance"], domain="same.com") for i in range(6)])
        assert len(out["items"]) == 3

    def test_an_injection_suspect_passage_is_excluded(self) -> None:
        out = pack(
            [
                m("bad", dims=["theme_relevance"], suspect=True),
                m("ok", dims=["theme_relevance"], domain="other.com"),
            ]
        )
        assert [i["evidence_id"] for i in out["items"]] == ["ok"]

    def test_a_theme_item_needs_an_acceptable_class_but_a_downside_may_come_from_any(self) -> None:
        out = pack(
            [
                m("w", dims=["theme_relevance"], source_class="aggregator"),
                m(
                    "d",
                    dims=["principal_downside"],
                    source_class="local_press",
                    domain="paper.com",
                    risk=("lawsuit",),
                ),
            ]
        )
        assert [(i["dimension"], i["source_class"]) for i in out["items"]] == [
            ("principal_downside", "local_press")
        ], "labelled with its class"

    def test_every_item_is_labelled_with_its_source_class_and_domain(self) -> None:
        out = pack([m("a", dims=["theme_relevance"])])
        item = out["items"][0]
        assert item["source_class"] == "trade_publication" and item["domain"] == "mining.com"

    def test_the_excerpt_is_clipped_and_neutralised(self) -> None:
        out = pack(
            [
                m(
                    "a",
                    dims=["theme_relevance"],
                    passage="Foo Ltd " + "x" * 400 + " a price target of 5 and a buy rating",
                )
            ]
        )
        excerpt = out["items"][0]["excerpt"]
        assert len(excerpt) <= cp.EXCERPT_CHARS
        out = pack(
            [
                m(
                    "a",
                    dims=["theme_relevance"],
                    passage="Foo Ltd has a price target of 5 and a BUY rating, fair value 7",
                )
            ]
        )
        low = out["items"][0]["excerpt"].lower()
        assert "price target" not in low and "fair value" not in low

    def test_no_block_means_no_pack(self) -> None:
        assert cp.build_candidate_web_pack(None) is None
        assert cp.build_candidate_web_pack({}) is None

    def test_a_labelled_lead_uses_the_search_corroboration(self) -> None:
        v3_web = {
            "discovery_mode": "curated_registry",
            "admission": {"state": "labelled"},
            "corroborated_by_search": {"mentions": [m("c", dims=["theme_relevance"])]},
        }
        out = cp.build_candidate_web_pack(v3_web)
        assert out is not None and out["items"][0]["evidence_id"] == "c"
        assert out["admission_state"] == "labelled"

    def test_the_pack_is_deterministic(self) -> None:
        mentions = [m(f"t{i}", dims=["theme_relevance"], domain=f"s{i}.com") for i in range(5)]
        assert pack(mentions) == pack(list(mentions))


class TestEvidenceConfidenceIsNotThesisRelevance:
    def test_levels(self) -> None:
        assert cp.evidence_confidence([]) == "not_established"
        one = [{"domain": "a.com", "source_class": "trade_publication"}]
        assert cp.evidence_confidence(one) == "low"
        two = one + [{"domain": "b.com", "source_class": "trade_publication"}]
        assert cp.evidence_confidence(two) == "medium"
        auth = one + [{"domain": "b.org", "source_class": "industry_association"}]
        assert cp.evidence_confidence(auth) == "high"
        assert (
            cp.evidence_confidence([{"domain": "a.gov", "source_class": "government_publication"}])
            == "medium"
        )

    def test_a_strong_thesis_fit_can_carry_low_confidence(self) -> None:
        """One trade-press passage that states three theme terms: thesis fit is
        established AND evidence confidence is low. They are different answers."""
        out = pack([m("a", dims=["theme_relevance"], terms=("gallium", "germanium", "rare earth"))])
        basis = out["priority_basis"]
        assert basis["thesis_fit"]["state"] == "established"
        assert basis["evidence_confidence"]["level"] == "low"
        assert basis["evidence_confidence"]["qualifies_the_others_never_ranks"] is True

    def test_more_sources_raise_confidence_not_thesis_fit(self) -> None:
        few = pack([m("a", dims=["theme_relevance"])])
        many = pack(
            [
                m("a", dims=["theme_relevance"], domain="a.com"),
                m(
                    "b",
                    dims=["theme_relevance"],
                    domain="b.org",
                    source_class="industry_association",
                ),
            ]
        )
        assert (
            few["priority_basis"]["thesis_fit"]["state"]
            == many["priority_basis"]["thesis_fit"]["state"]
            == "established"
        )
        assert many["priority_basis"]["evidence_confidence"]["level"] == "high"
        assert few["priority_basis"]["evidence_confidence"]["level"] == "low"

    def test_priority_basis_keeps_the_four_ranking_inputs_apart_from_confidence(self) -> None:
        out = pack(
            [m("a", dims=["theme_relevance"])],
            verified_attributes={"growth_status": "established"},
            constraint_status={"size": "pass"},
        )
        basis = out["priority_basis"]
        assert set(basis) == {
            "thesis_fit",
            "research_question_economics",
            "catalyst_relevance",
            "size_constraint_fit",
            "evidence_confidence",
        }
        assert basis["research_question_economics"]["state"] == "established"
        assert basis["size_constraint_fit"]["state"] == "pass"
        assert basis["catalyst_relevance"]["state"] == "not_established"

    def test_momentum_is_not_growth(self) -> None:
        out = pack(
            [m("a", dims=["theme_relevance"])], verified_attributes={"momentum_label": "strong"}
        )
        assert out["priority_basis"]["research_question_economics"]["state"] == "not_established"

    def test_the_pack_carries_no_field_completeness_figure(self) -> None:
        blob = json.dumps(pack([m("a", dims=["theme_relevance"])]))
        for forbidden in ("missing_info", "completeness", "blocking_gap", "field_count"):
            assert forbidden not in blob


# --------------------------------------------------------------------------- #
# The evidence pack
# --------------------------------------------------------------------------- #


def run_dict(**over: Any) -> dict[str, Any]:
    base = {
        "run_id": "r1",
        "mode": "thesis",
        "status": "completed",
        "thesis_text": "gallium producers",
        "parsed_thesis": {"theme": "mining_materials"},
        "universe_count": 2,
        "candidate_count": 2,
        "error_count": 0,
        "warnings": [],
        "requested_constraints": [],
        "discovery_funnel": {},
    }
    base.update(over)
    return base


def cand(ticker: str, **over: Any) -> dict[str, Any]:
    base = {
        "candidate_id": f"id-{ticker}",
        "ticker": ticker,
        "exchange": "AU",
        "company_name": f"{ticker} Ltd",
        "country": "Australia",
        "missing_info_count": 7,
        "blocking_gap_count": 2,
        "data_completeness_score": 0.9,
        "catalyst_score": 0.4,
        "data_coverage": {"profile_source": "x"},
    }
    base.update(over)
    return base


class TestTheEvidencePack:
    def test_a_web_candidate_gets_cited_item_ids(self) -> None:
        web = pack(
            [
                m("a", dims=["theme_relevance"]),
                m("b", dims=["catalysts"], domain="o.com", catalyst=("offtake",)),
            ]
        )
        evidence = build_discovery_evidence_pack(
            run=run_dict(), candidates=[cand("ALG", web_discovery=web), cand("XYZ")]
        )
        first, second = evidence.candidates
        assert first.web_discovery and second.web_discovery is None
        ids = [i["id"] for i in first.web_discovery["items"]]
        assert ids == ["C1.1", "C1.2"]
        assert set(ids) <= evidence.evidence_ids()
        dims = first.web_discovery["dimensions"]
        assert dims["theme_relevance"]["item_ids"] == ["C1.1"]
        assert dims["catalysts"]["item_ids"] == ["C1.2"]

    def test_a_web_candidate_carries_no_completeness_counts(self) -> None:
        web = pack([m("a", dims=["theme_relevance"])])
        evidence = build_discovery_evidence_pack(
            run=run_dict(), candidates=[cand("ALG", web_discovery=web), cand("XYZ")]
        )
        with_web, without = evidence.candidates
        assert "data_completeness_score" not in with_web.score_breakdown
        assert "missing_info_count" not in with_web.data_coverage
        assert "blocking_gap_count" not in with_web.data_coverage
        assert without.data_coverage["missing_info_count"] == 7, "V3.19 candidates unchanged"

    def test_the_run_states_its_web_search_state_as_a_cited_fact(self) -> None:
        run = run_dict(
            discovery_web={
                "state": "web_search_degraded",
                "label": "Web search incomplete (3 of 4 searches ran)",
                "queries": {"executed": 3, "planned": 4},
                "admission": {"by_state": {"admitted": 2, "labelled": 1}},
            }
        )
        evidence = build_discovery_evidence_pack(run=run, candidates=[cand("ALG")])
        fact = next(f for f in evidence.run_facts if f.label == "web_discovery")
        assert "state=web_search_degraded" in fact.detail and "3 of 4" in fact.detail
        assert "model_recall were NOT surfaced by a search" in fact.detail

    def test_a_pack_without_a_web_block_is_byte_identical_to_v319(self) -> None:
        evidence = build_discovery_evidence_pack(run=run_dict(), candidates=[cand("A"), cand("B")])
        text = evidence.model_dump_json()
        assert "web_discovery" not in text
        assert not any(
            "available" in line and "never ranks" in line for line in evidence.do_not_infer
        )
        assert all(f.label != "web_discovery" for f in evidence.run_facts)
        # the serialised candidate has exactly the V3.19 keys
        assert set(json.loads(text)["candidates"][0]) == {
            "id",
            "candidate_id",
            "ticker",
            "exchange",
            "company_name",
            "country",
            "sector",
            "industry",
            "thesis_relevance_score",
            "combined_internal_score",
            "candidate_score",
            "candidate_score_grade",
            "score_breakdown",
            "data_coverage",
            "catalyst_summary",
            "research_signals",
            "verified_attributes",
            "constraint_status",
            "eligibility",
            "discovery_provenance",
            "safety_valid",
            "human_review_required",
            "is_public",
            "warnings",
        }

    def test_do_not_infer_gains_the_ranking_rules_only_for_web_candidates(self) -> None:
        web = pack([m("a", dims=["theme_relevance"])])
        evidence = build_discovery_evidence_pack(
            run=run_dict(), candidates=[cand("ALG", web_discovery=web)]
        )
        text = " ".join(evidence.do_not_infer)
        assert "never ranks" in text and "Momentum is not growth" in text
        assert "third-party passage" in text


# --------------------------------------------------------------------------- #
# The prompt contract
# --------------------------------------------------------------------------- #


class TestThePromptContract:
    AGENTS = (
        "run_coordinator",
        "candidate_prioritization",
        "novelty_coverage",
        "diversity_anti_convergence",
        "evidence_sufficiency",
        "risk_gatekeeper",
        "run_red_team",
    )

    def test_without_a_web_block_every_prompt_is_unchanged(self) -> None:
        for agent in self.AGENTS:
            default = prompts.system_prompt_for(agent)
            assert default == prompts.system_prompt_for(agent, web_discovery=False)
            assert "WEB-DISCOVERED" not in default
            assert prompts.WEB_JSON_ADDENDUM not in default and '"dimensions": [' not in default
        assert "WEB-DISCOVERED" not in prompts.discovery_chair_system_prompt()

    def test_with_a_web_block_the_contract_is_added_to_every_agent(self) -> None:
        for agent in self.AGENTS:
            text = prompts.system_prompt_for(agent, web_discovery=True)
            assert prompts.WEB_DISCOVERY_CONTRACT in text
            assert prompts.WEB_JSON_ADDENDUM in text
        assert prompts.WEB_DISCOVERY_CONTRACT in prompts.discovery_chair_system_prompt(
            web_discovery=True
        )

    def test_the_contract_separates_evidence_confidence_from_ranking(self) -> None:
        c = prompts.WEB_DISCOVERY_CONTRACT
        for phrase in (
            "thesis_fit",
            "research_question_economics",
            "catalyst_relevance",
            "size_constraint_fit",
            "QUALIFIES a view and never ranks",
            "more available data",
            "Momentum is not growth",
            "Missing-field counts are not a ranking input",
        ):
            assert phrase in c, phrase
        for dim in (
            "theme_relevance",
            "growth_drivers",
            "profitability_cash",
            "business_quality",
            "catalysts",
            "resilience",
            "principal_downside",
        ):
            assert dim in c and dim in prompts.WEB_JSON_ADDENDUM

    def test_the_v319_requested_vs_verified_contract_is_still_there(self) -> None:
        text = prompts.system_prompt_for("candidate_prioritization", web_discovery=True)
        assert prompts.REQUESTED_VS_VERIFIED_CONTRACT in text
        assert prompts.ECONOMIC_VS_EVIDENCE_CONTRACT in text
        assert prompts.JURISDICTION_CONTRACT in text

    def test_the_contract_contains_no_recommendation_vocabulary(self) -> None:
        from app.services import safety_terms

        for text in (prompts.WEB_DISCOVERY_CONTRACT, prompts.WEB_JSON_ADDENDUM):
            assert safety_terms.scan_value(text) == []

    def test_the_marker_is_present_only_when_a_candidate_has_a_block(self) -> None:
        web = pack([m("a", dims=["theme_relevance"])])
        with_web = build_discovery_evidence_pack(
            run=run_dict(), candidates=[cand("ALG", web_discovery=web)]
        ).model_dump_json()
        without = build_discovery_evidence_pack(
            run=run_dict(), candidates=[cand("ALG")]
        ).model_dump_json()
        assert prompts.pack_has_web_discovery(with_web)
        assert not prompts.pack_has_web_discovery(without)

    async def test_the_council_sends_the_web_contract_only_for_a_web_pack(self) -> None:
        class Recording(FakeDiscoveryLLMClient):
            systems: list[str] = []

            async def _complete_raw(self, system: str, user: str, **kw: Any) -> str:
                self.systems.append(system)
                return await super()._complete_raw(system, user, **kw)

        web = pack([m("a", dims=["theme_relevance"])])
        for with_web in (True, False):
            client = Recording()
            client.systems = []
            evidence = build_discovery_evidence_pack(
                run=run_dict(),
                candidates=[cand("ALG", **({"web_discovery": web} if with_web else {}))],
            )
            await run_discovery_council(evidence, client)
            assert client.systems
            assert all(("WEB-DISCOVERED" in s) is with_web for s in client.systems)


# --------------------------------------------------------------------------- #
# The council's output
# --------------------------------------------------------------------------- #


def note(**over: Any) -> CandidateNote:
    base: dict[str, Any] = {
        "candidate_ref": "C1",
        "internal_action": "research_next",
        "rationale": "Strong thesis fit on a niche gallium recovery plan.",
        "citation_ids": ["C1"],
    }
    base.update(over)
    return CandidateNote(**base)


def agent_output(*notes: CandidateNote) -> DiscoveryCouncilAgentOutput:
    return DiscoveryCouncilAgentOutput(
        agent_name="discovery_chair", candidate_notes=list(notes), run_quality="adequate"
    )


class TestTheCouncilOutput:
    def test_dimensions_are_kept_with_valid_ids_and_confidence(self) -> None:
        out = agent_output(
            note(
                dimensions=[
                    DimensionAssessment(
                        dimension="theme_relevance",
                        assessment="gallium recovery",
                        evidence_confidence="medium",
                        citation_ids=["C1.1"],
                    ),
                    DimensionAssessment(
                        dimension="growth_drivers",
                        assessment="",
                        evidence_confidence="not_established",
                        citation_ids=[],
                    ),
                ]
            )
        )
        clean, issues = check_and_sanitize(out, {"C1", "C1.1"}, {"C1"}, is_chair=True)
        dims = clean.candidate_notes[0].dimensions
        assert [(d.dimension, d.evidence_confidence, d.citation_ids) for d in dims] == [
            ("theme_relevance", "medium", ["C1.1"]),
            ("growth_drivers", "not_established", []),
        ]
        assert issues == []

    def test_an_unknown_dimension_an_invented_id_and_a_bad_label_are_repaired(self) -> None:
        out = agent_output(
            note(
                dimensions=[
                    DimensionAssessment(
                        dimension="vibes", assessment="x", evidence_confidence="high"
                    ),
                    DimensionAssessment(
                        dimension="catalysts",
                        assessment="an offtake",
                        evidence_confidence="certain",
                        citation_ids=["C9.9"],
                    ),
                    DimensionAssessment(
                        dimension="resilience",
                        assessment="net cash",
                        evidence_confidence="high",
                        citation_ids=[],
                    ),
                ]
            )
        )
        clean, issues = check_and_sanitize(out, {"C1", "C1.1"}, {"C1"}, is_chair=True)
        dims = {d.dimension: d for d in clean.candidate_notes[0].dimensions}
        assert set(dims) == {"catalysts", "resilience"}
        assert dims["catalysts"].evidence_confidence == "not_established"
        assert dims["catalysts"].citation_ids == []
        assert dims["resilience"].evidence_confidence == "low", (
            "an uncited view is not a well-sourced one"
        )
        assert any("unknown dimension" in i for i in issues)

    def test_a_dimension_with_recommendation_language_quarantines_the_output(self) -> None:
        out = agent_output(
            note(
                dimensions=[
                    DimensionAssessment(
                        dimension="resilience",
                        assessment="a clear BUY on fair value",
                        evidence_confidence="low",
                        citation_ids=["C1.1"],
                    )
                ]
            )
        )
        clean, issues = check_and_sanitize(out, {"C1", "C1.1"}, {"C1"}, is_chair=True)
        assert clean.status == "failed" and clean.candidate_notes == []

    def test_the_chair_bucket_carries_dimensions_only_when_present(self) -> None:
        web = pack([m("a", dims=["theme_relevance"])])
        evidence = build_discovery_evidence_pack(
            run=run_dict(), candidates=[cand("ALG", web_discovery=web)]
        )
        with_dims = agent_output(
            note(
                dimensions=[
                    DimensionAssessment(
                        dimension="theme_relevance",
                        assessment="x",
                        evidence_confidence="low",
                        citation_ids=["C1.1"],
                    )
                ]
            )
        )
        buckets = _aggregate_chair(evidence, with_dims)
        assert (
            buckets["candidates_to_research_next"][0]["dimensions"][0]["dimension"]
            == "theme_relevance"
        )
        plain = _aggregate_chair(evidence, agent_output(note()))
        assert "dimensions" not in plain["candidates_to_research_next"][0]

    async def test_a_real_run_with_the_fake_client_still_completes_with_a_web_pack(self) -> None:
        web = pack([m("a", dims=["theme_relevance"])])
        evidence = build_discovery_evidence_pack(
            run=run_dict(), candidates=[cand("ALG", web_discovery=web), cand("XYZ")]
        )
        result = await run_discovery_council(evidence, FakeDiscoveryLLMClient())
        assert result.agents_completed == 8 and result.safety_valid
        assert result.run_quality in {"strong", "adequate", "thin", "failed"}
