"""ADR-095 — one search-status producer, deterministic conflicts, one ledger view.

Company names appear here as test fixtures only; no app code names an issuer.
"""

from __future__ import annotations

import gc
import itertools
import re
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.services.agents.chair import LLMChair
from app.services.council_v2.inputs import ChallengeRef, CouncilInput, DisagreementRef
from app.services.ledger import ledger_invariants as li
from app.services.ledger import report_invariants as ri
from app.services.ledger import search_status as ss
from app.services.ledger.canonical_state import (
    LedgerTally,
    build_canonical_state,
    conflict_state,
    period_bounds,
)
from app.services.pipeline import gap_reconciliation as gr
from app.services.pipeline import professional_research as pr

API = Path(__file__).resolve().parents[1]
WEB = API.parent / "web" / "src"


# ── Fixtures ────────────────────────────────────────────────────────────────── #


def _web_context(*, planned: int = 32, executed: int = 0, failed: int = 32,
                 not_issued: int = 0, from_cache: int = 0,
                 followup: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "provider": "tavily",
        "state": "web_search_unavailable" if executed == 0 else "ok",
        "queries": {"planned": planned, "executed": executed, "failed": failed,
                    "not_issued": not_issued, "from_cache": from_cache},
        "results": {"seen": 0 if executed == 0 else 10 * executed},
    }
    if followup is not None:
        ctx["followup"] = {"rounds": followup, "queries": []}
    return ctx


def _no_credit_run() -> dict[str, Any]:
    """CFR3's shape: no search credit left; every stage and follow-up query failed."""
    return {
        "web_context": _web_context(followup=[
            {"kind": "gap", "round": 0, "queries": ["a"] * 6, "executed": 0, "failed": 6,
             "not_issued": 0, "candidates": 0},
            {"kind": "challenge", "round": 1, "queries": ["b"] * 3, "executed": 0,
             "failed": 3, "not_issued": 0, "candidates": 0},
        ]),
        "consumption": {
            "web_search_calls": 1, "tavily_credits": 0.0,
            "web_stage": {"web_search_calls": 41, "tavily_credits": 0.0,
                          "unreported": ["tavily_credits"]},
        },
    }


def _finding(fid: str, statement: str, *, period: str | None = "FY2025",
             scope: str | None = "group", kinds: tuple[str, ...] = ("issuer_filing",),
             **extra: Any) -> dict[str, Any]:
    return {"finding_id": fid, "statement": statement, "period_key": period,
            "scope_key": scope, "source_kinds": list(kinds), **extra}


def _facts(*findings: dict[str, Any]) -> list[gr.FindingFacts]:
    out = [gr.finding_facts_from_payload(f) for f in findings]
    return [f for f in out if f is not None]


A = "aaaaaaaa-0000-4000-8000-000000000001"
B = "bbbbbbbb-0000-4000-8000-000000000002"
C = "cccccccc-0000-4000-8000-000000000003"


# ── 1. One search-status producer ───────────────────────────────────────────── #


class TestSearchStatus:
    def test_a_run_with_no_credit_says_every_search_failed(self) -> None:
        status = ss.build_search_status(_no_credit_run())
        stage = status["classes"]["web_stage"]
        assert stage["planned"] == 32 and stage["executed"] == 32
        assert stage["succeeded"] == 0 and stage["failed"] == 32
        web = status["web"]
        assert (web["executed"], web["succeeded"], web["failed"], web["outcome_unknown"]) == (
            42, 0, 41, 1)
        assert status["violations"] == []
        line = ss.web_status_line(status)
        assert line is not None
        assert line["total"] == 42 and line["succeeded"] == 0 and line["failed"] == 41

    def test_counts_are_consistent_by_construction(self) -> None:
        for planned, executed, failed, not_issued, cache in itertools.product(
                (0, 5, 30), (0, 3, 20), (0, 2), (0, 4), (0, 2)):
            status = ss.build_search_status({"web_context": _web_context(
                planned=planned, executed=executed, failed=failed, not_issued=not_issued,
                from_cache=min(cache, executed))})
            for block in status["classes"].values():
                if block is None:
                    continue
                assert block["executed"] <= block["attempted"] <= block["planned"]
                assert (block["succeeded"] + block["failed"] + block["outcome_unknown"]
                        == block["executed"])
            assert [v for v in status["violations"]
                    if v["code"] != ss.SEARCH_SUCCEEDED_WITHOUT_CREDIT] == []

    def test_zero_reported_credits_with_a_network_success_is_a_violation(self) -> None:
        payload = {"web_context": _web_context(planned=5, executed=5, failed=0),
                   "consumption": {"web_stage": {"tavily_credits": 0.0, "unreported": []}}}
        codes = [v["code"] for v in ss.build_search_status(payload)["violations"]]
        assert codes == [ss.SEARCH_SUCCEEDED_WITHOUT_CREDIT]

    def test_cache_serves_cost_no_credit(self) -> None:
        payload = {"web_context": _web_context(planned=5, executed=5, failed=0,
                                               from_cache=5),
                   "consumption": {"web_stage": {"tavily_credits": 0.0, "unreported": []}}}
        assert ss.build_search_status(payload)["violations"] == []

    def test_unreported_credits_are_not_read_as_zero(self) -> None:
        payload = {"web_context": _web_context(planned=5, executed=5, failed=0),
                   "consumption": {"web_stage": {"tavily_credits": 0.0,
                                                 "unreported": ["tavily_credits"]}}}
        assert ss.build_search_status(payload)["violations"] == []

    def test_a_bad_block_reports_its_invariants(self) -> None:
        bad = {"classes": {"web_stage": {
            "planned": 1, "attempted": 2, "executed": 3, "succeeded": 1, "failed": 0,
            "outcome_unknown": 0}}}
        codes = {v["code"] for v in ss.check_search_status(bad)}
        assert codes == {ss.SEARCH_EXECUTED_EXCEEDS_ATTEMPTED,
                         ss.SEARCH_ATTEMPTED_EXCEEDS_PLANNED, ss.SEARCH_OUTCOMES_DO_NOT_SUM}

    def test_budget_cut_queries_are_planned_not_attempted(self) -> None:
        status = ss.build_search_status({"web_context": _web_context(
            planned=36, executed=20, failed=0, not_issued=2)})
        stage = status["classes"]["web_stage"]
        assert (stage["attempted"], stage["not_attempted"]) == (22, 14)

    def test_corpus_and_gap_queue_are_their_own_classes(self) -> None:
        payload = {
            "web_context": _web_context(planned=2, executed=2, failed=0),
            "question_graph": [{"domain": "financial_capacity", "acquisition_log": [
                {"rung": "corpus_by_intent", "queries": ["a", "b"]},
                {"rung": "primary_document_reread", "tool_calls": 3},
                {"rung": "external_search", "query": "x", "fetched": 1},
            ]}],
            "gap_resolution": {"counts": {"queries_planned": 9, "queries_succeeded": 6,
                                          "queries_failed": 1, "queries_skipped": 2}},
        }
        status = ss.build_search_status(payload)
        assert status["classes"]["corpus"]["executed"] == 5
        queue = status["classes"]["gap_queue"]
        assert (queue["planned"], queue["executed"], queue["failed"]) == (9, 7, 1)
        assert status["web"]["executed"] == 2  # never a corpus query
        assert status["by_domain"]["financial_capacity"]["external_searches"] == 1

    def test_canonical_status_line_is_the_block(self) -> None:
        state = build_canonical_state(_no_credit_run(), as_of="2026-11-09")
        assert state["searches"]["web"]["executed"] == 42
        assert state["status"]["web_searches"]["total"] == 42
        assert state["status"]["web_searches"]["succeeded"] == 0

    def test_professional_web_block_reads_the_producer(self) -> None:
        context = _no_credit_run()["web_context"]
        block = pr.web_research_block(context, [], {})
        assert block["searches_run"] == 41 and block["searches_planned"] == 41
        assert block["searches_succeeded"] == 0 and block["searches_failed"] == 41
        assert pr.domain_cost is ss.domain_cost

    def test_a_stale_report_count_is_recorded_against_the_block(self) -> None:
        payload = _no_credit_run()
        payload["professional_research"] = {"sections": [
            {"key": "evidence_quality_and_gaps",
             "web_research": {"searches_run": 0, "searches_planned": 32}}]}
        state = build_canonical_state(payload, as_of="2026-11-09")
        result = ri.enforce(payload, state)
        rules = [v.rule for v in result.violations]
        assert rules.count(ri.RULE_SEARCH_COUNT) == 2

    def test_no_renderer_reads_a_search_count_from_anywhere_else(self) -> None:
        """Grep-style pin: the raw search counters are read by the producer only."""
        ledger_dir = API / "app" / "services" / "ledger"
        for path in ledger_dir.glob("*.py"):
            if path.name in ("search_status.py", "report_invariants.py"):
                continue
            text = path.read_text()
            assert '"web_search_calls"' not in text, path.name
            assert 'queries.get("executed")' not in text, path.name
        prof = (API / "app" / "services" / "pipeline" / "professional_research.py").read_text()
        # The only read of the stage's own counter is the pre-ADR-095 fallback.
        assert prof.count('queries.get("executed")') == 1
        web = (WEB / "components" / "research" / "v3Research.ts").read_text()
        assert "canonical.searches.web_stage_total" in web
        status_fn = web[web.index("export function canonicalStatus"):]
        status_fn = status_fn[: status_fn.index("\n}\n")]
        assert "consumption" not in status_fn and "web_context" not in status_fn


# ── 2. Deterministic conflicts ──────────────────────────────────────────────── #


class TestDeterministicConflicts:
    def test_two_findings_stating_different_revenue_for_one_period_conflict(self) -> None:
        conflicts = gr.deterministic_conflicts(_facts(
            _finding(A, "FY2025 Group revenue was USD 37,442m."),
            _finding(B, "FY2025 Group revenue was USD 38,500m."),
        ))
        assert len(conflicts) == 1
        c = conflicts[0]
        assert (c.metric, c.period, c.scope) == ("metric:revenue", "FY2025", "group")
        assert {c.value_a, c.value_b} == {"USD 37,442,000,000", "USD 38,500,000,000"}

    def test_rounding_and_restatement_in_another_unit_are_not_conflicts(self) -> None:
        assert gr.deterministic_conflicts(_facts(
            _finding(A, "FY2025 Group revenue was USD 37,442m."),
            _finding(B, "FY2025 Group revenue was USD 37.4bn."),
        )) == []

    def test_a_figure_read_at_two_scales_is_not_a_conflict(self) -> None:
        """PYPL3 replay: "revenue 33172.0 USD m" parses without its "m"."""
        assert gr.deterministic_conflicts(_facts(
            _finding(A, "FY2025 Group operating margin 18.28%, from operating profit "
                        "6065.0 / revenue 33172.0 USD m."),
            _finding(B, "Group revenue was $33,172 million in FY2025."),
        )) == []

    def test_an_amount_that_belongs_to_another_quantity_is_not_read(self) -> None:
        """JPM replay: the amount after "operating cash flow / net income)" is net
        income's."""
        assert gr.deterministic_conflicts(_facts(
            _finding(A, "FY2025 Group cash conversion was -259.1% (operating cash flow / "
                        "net income), against net income of 57,048 USD millions."),
            _finding(B, "FY2025 Group operating cash flow was -147,782 USD millions."),
        )) == []

    @pytest.mark.parametrize("b", [
        _finding(B, "Q3 2026 Group revenue was USD 9,000m.", period="2026-Q3"),
        _finding(B, "FY2025 Aerospace revenue was USD 15,000m.", scope="segment:aerospace"),
        _finding(B, "FY2025 revenue was USD 15,000m.", scope=None),
        _finding(B, "FY2025 Group revenue was USD 38,500m.", verification_status="withdrawn"),
        _finding(B, "FY2025 Group revenue guidance is USD 38,500m.",
                 statement_kind="guidance"),
        _finding(B, "Iluka (a peer, not the company) FY2025 revenue was USD 38,500m."),
    ])
    def test_another_period_scope_kind_or_entity_is_never_paired(self, b) -> None:  # noqa: ANN001
        assert gr.deterministic_conflicts(_facts(
            _finding(A, "FY2025 Group revenue was USD 37,442m."), b)) == []

    def test_a_quarter_key_over_year_to_date_words_is_never_paired(self) -> None:
        assert gr.deterministic_conflicts(_facts(
            _finding(A, "Q3 2026 Group revenue was USD 10,300m.", period="2026-Q3"),
            _finding(B, "Group revenue for the nine months ended 30 September 2026 was "
                        "USD 34,000m.", period="2026-Q3"),
        )) == []

    def test_a_denial_is_no_value_conflict(self) -> None:
        """HON4 / CLLS7 replay: "not spent on capex" and "no cost of revenue" agree with
        the figures they were paired against."""
        facts = _facts(
            _finding(A, "FY2025 capex absorbed 15.39% of operating cash flow, so roughly "
                        "five-sixths of operating cash was not spent on capex."),
            _finding(B, "FY2025 Group free cash flow was USD 5,422m (operating cash flow "
                        "6,408m less capex 986m)."),
            _finding(C, "Cellectis reported no cost of revenue for FY2025, so a gross "
                        "margin cannot be computed."),
        )
        assert gr.deterministic_conflicts(facts) == []

    def test_conflict_state_prefers_the_reconciliation_record(self) -> None:
        record = {"gap_reconciliation": {"conflicts": [
            {"finding_a_id": A, "finding_b_id": B, "metric": "metric:revenue",
             "disagreement_id": C}]}}
        state = conflict_state(record)
        assert state["source"] == "reconciliation"
        assert state["count"] == 1 and state["ids"] == [C]

    def test_conflict_state_recomputes_on_an_older_payload(self) -> None:
        state = conflict_state({"findings": [
            _finding(A, "FY2025 Group revenue was USD 37,442m."),
            _finding(B, "FY2025 Group revenue was USD 38,500m.")]})
        assert state["source"] == "payload_findings"
        assert state["count"] == 1 and state["ids"] is None

    def test_the_canonical_unresolved_count_is_the_conflict_count(self) -> None:
        tally = LedgerTally(findings=3, verified=0, primary_source=3, gaps_open=0,
                            disagreements=18, unresolved_disagreement_ids=tuple(
                                str(uuid.uuid4()) for _ in range(18)))
        state = build_canonical_state({"gap_reconciliation": {"conflicts": []}},
                                      tally=tally, as_of="2026-11-09")
        assert state["disagreements"]["unresolved"] == 0
        assert state["disagreements"]["ledger_unresolved"] == 18
        assert state["conflicts"]["count"] == 0


class TestChairNarration:
    def _state(self, conflicts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return build_canonical_state(
            {"gap_reconciliation": {"conflicts": conflicts or []},
             "findings": [_finding(A, "x"), _finding(B, "y")]},
            as_of="2026-11-09")

    @pytest.mark.parametrize("sentence", [
        "There are unresolved conflicts in the evidence regarding capital expenditure.",
        "There are unresolved disagreements in the evidence regarding revenue.",
        "However, conflicting findings exist on this point.",
        "A disagreement between the findings on net debt remains.",
        "The findings conflict on net debt.",
    ])
    def test_an_unbacked_conflict_is_withheld(self, sentence: str) -> None:
        hit = ri.check_sentence(sentence, self._state(), site=ri.SITE_CHAIR)
        assert hit is not None and hit.rule == ri.RULE_UNBACKED_DISAGREEMENT

    @pytest.mark.parametrize("sentence", [
        "Unresolved conflicts in the region weigh on demand.",
        "Analysts disagree on the outlook.",
        "There are no conflicting findings on revenue.",
        "No unresolved disagreements were recorded.",
    ])
    def test_the_world_and_negations_are_kept(self, sentence: str) -> None:
        assert ri.check_sentence(sentence, self._state(), site=ri.SITE_CHAIR) is None

    def test_a_listed_conflict_may_be_narrated(self) -> None:
        state = self._state([{"finding_a_id": A, "finding_b_id": B,
                              "disagreement_id": C}])
        assert ri.check_sentence(
            "There is an unresolved disagreement between findings on revenue.", state,
            site=ri.SITE_CHAIR) is None

    def test_a_count_is_repaired_to_the_conflict_count(self) -> None:
        hit = ri.check_sentence("The ledger holds 18 unresolved disagreements.",
                                self._state(), site=ri.SITE_CHAIR)
        assert hit is not None and hit.rule == ri.RULE_CONFLICT_COUNT
        assert hit.replacement == "The ledger holds 0 unresolved disagreements."

    def test_the_deterministic_sentence_states_the_conflict_count(self) -> None:
        hit = ri.check_sentence(
            "Deterministic synthesis: 0 finding(s), 0 verified, 0 open gap(s), 18 "
            "unresolved disagreement(s).", self._state(), site=ri.SITE_CHAIR)
        assert hit is not None and hit.replacement is not None
        assert "0 unresolved disagreement(s)" in hit.replacement

    def test_professional_prose_is_held_to_the_conflict_list(self) -> None:
        hit = ri.check_sentence("Conflicting findings exist on the net cash position.",
                                self._state(), site=ri.SITE_REPORT_PROSE)
        assert hit is not None and hit.rule == ri.RULE_UNBACKED_DISAGREEMENT
        # A record text (a finding's own statement) is not.
        assert ri.check_sentence("Conflicting findings exist on the net cash position.",
                                 self._state(), site=ri.SITE_RECORD) is None

    def test_chair_ids_are_dropped_when_no_conflict_was_computed(self) -> None:
        payload = {"findings": [_finding(A, "x")],
                   "chair": {"synthesis": "ok", "unresolved_disagreement_ids": [C]}}
        state = build_canonical_state(payload, as_of="2026-11-09")
        out = ri.enforce(payload, state).v3
        assert out["chair"]["unresolved_disagreement_ids"] == []
        assert out["chair"]["dropped_disagreement_ids"] == [C]

    def test_the_chair_prompt_lists_conflicts_and_challenges_apart(self) -> None:
        council = CouncilInput(
            research_run_id=uuid.uuid4(), convened=True,
            unresolved_challenges=(ChallengeRef(uuid.UUID(A), "period_mismatch",
                                                "that is an interim figure"),),
        )
        prompt = LLMChair()._prompt(council)
        assert "UNRESOLVED DISAGREEMENTS: none computed between findings." in prompt
        assert "UNRESOLVED RED TEAM CHALLENGES" in prompt
        assert "that is an interim figure" in prompt
        council = CouncilInput(
            research_run_id=uuid.uuid4(), convened=True,
            disagreements=(DisagreementRef(uuid.UUID(C), uuid.UUID(A), uuid.UUID(B),
                                           "value", "two revenue figures", "unresolved",
                                           None),))
        prompt = LLMChair()._prompt(council)
        assert "UNRESOLVED DISAGREEMENTS — you must surface these:" in prompt
        assert "none computed" not in prompt


# ── 3. One ledger view and its impossible combinations ─────────────────────── #


class TestOneLedger:
    def test_year_to_date_keys_have_bounds(self) -> None:
        assert [d.isoformat() for d in period_bounds("2026-9M")] == ["2026-01-01",
                                                                     "2026-09-30"]
        assert [d.isoformat() for d in period_bounds("2027-9M", fiscal_year_end_month=3)
                ] == ["2026-04-01", "2026-12-31"]

    def test_the_latest_period_reads_a_year_to_date_finding(self) -> None:
        payload = {"findings": [
            _finding(A, "Revenue for the nine months ended 30 September 2026 was USD 1m.",
                     period="2026-9M", source_published_at="2026-10-20"),
            _finding(B, "FY2025 revenue USD 1m.", source_published_at="2026-02-01")]}
        state = build_canonical_state(payload, as_of="2026-11-09")
        assert state["documents"]["latest_reporting_period"] == "2026-9M"
        assert li.impossible_combinations(payload, state) == []

    def test_document_not_parsed_makes_the_evidence_incomplete(self) -> None:
        payload = {"findings": [_finding(A, "FY2025 revenue USD 1m.")],
                   "gap_resolution": {"counts": {"by_result": {"DOCUMENT_NOT_PARSED": 2}}}}
        state = build_canonical_state(payload, as_of="2026-11-09")
        assert state["evidence"]["status"] != "adequate"
        assert any("partly parsed" in r for r in state["evidence"]["reasons"])

    def test_the_view_owns_every_count(self) -> None:
        state = build_canonical_state(_no_credit_run(), as_of="2026-11-09")
        for key in ("findings", "gaps", "conflicts", "disagreements", "searches",
                    "documents", "evidence", "chair", "status"):
            assert key in state
        assert {"label", "cited_finding_ids"} <= set(state["chair"])

    def test_a_consistent_report_has_no_impossible_combination(self) -> None:
        payload = _no_credit_run()
        payload["findings"] = [_finding(A, "FY2025 revenue USD 1m.")]
        payload["chair"] = {"synthesis": "Revenue was stated for FY2025.",
                            "key_points": [{"text": "Revenue.", "finding_id": A}],
                            "unresolved_disagreement_ids": []}
        state = build_canonical_state(payload, as_of="2026-11-09")
        out = ri.enforce(payload, state)
        assert li.impossible_combinations(out.v3, state) == []

    @pytest.mark.parametrize("mutate,code", [
        (lambda p, s: p["chair"].update(synthesis="There are no findings to report."),
         li.CHAIR_NO_FINDINGS),
        (lambda p, s: p["chair"].update(
            synthesis="Deterministic synthesis: 0 finding(s), 0 verified, 0 open gap(s), 0 "
                      "unresolved disagreement(s)."), li.CHAIR_NO_FINDINGS),
        (lambda p, s: s["gaps"].update(closed=5, total=3), li.GAPS_CLOSED_EXCEED),
        (lambda p, s: s["disagreements"].update(unresolved=2, unresolved_ids=[C]),
         li.CONFLICT_COUNT_IDS),
        (lambda p, s: p["chair"].update(unresolved_disagreement_ids=[C]),
         li.CONFLICT_COUNT_IDS),
        (lambda p, s: p["chair"].update(
            synthesis="There are unresolved conflicts in the evidence on revenue."),
         li.CONFLICT_UNBACKED),
        (lambda p, s: s["status"]["web_searches"].update(total=7), li.SEARCHES_DIFFER),
        (lambda p, s: p.update(professional_research={"sections": [
            {"web_research": {"searches_run": 3}}]}), li.SEARCHES_DIFFER),
        (lambda p, s: s["documents"].update(latest_reporting_period="FY2024",
                                            latest_reporting_period_end="2024-12-31"),
         li.LATEST_PERIOD_OLDER),
        (lambda p, s: s.update(gap_resolution={"counts": {"by_result": {
            "DOCUMENT_NOT_PARSED": 1}}}), li.EVIDENCE_NOT_PARSED),
        (lambda p, s: p["chair"]["key_points"].append({"text": "x", "finding_id": C}),
         li.CHAIR_UNKNOWN_FINDING),
    ])
    def test_each_impossible_combination_is_reported(self, mutate, code) -> None:  # noqa: ANN001
        payload = _no_credit_run()
        payload["findings"] = [_finding(A, "FY2025 revenue USD 1m.",
                                        source_published_at="2026-02-01")]
        payload["chair"] = {"synthesis": "Revenue was stated for FY2025.",
                            "key_points": [{"text": "Revenue.", "finding_id": A}],
                            "unresolved_disagreement_ids": []}
        state = build_canonical_state(payload, as_of="2026-11-09")
        state["evidence"]["status"] = "adequate"
        mutate(payload, state)
        codes = {item["code"] for item in li.impossible_combinations(payload, state)}
        assert code in codes

    def test_the_pipeline_records_the_check(self) -> None:
        source = (API / "app" / "services" / "pipeline" / "v3_pipeline.py").read_text()
        assert 'out["state_invariants"]["impossible_combinations"]' in source


# ── Linear time ─────────────────────────────────────────────────────────────── #


def test_the_conflict_detector_and_checker_are_quick_on_degenerate_input() -> None:
    big = "FY2025 Group revenue was USD 1m " + "revenue was " * 2000
    started = time.perf_counter()
    gr.deterministic_conflicts(_facts(_finding(A, big), _finding(B, big)))
    gr._metric_amount(big.lower(), "metric:revenue")  # noqa: SLF001
    li.impossible_combinations({"chair": {"synthesis": "no findings " * 3000}},
                               {"findings": {"total": 1}})
    # ~1.3s unloaded, almost all in research_fields' clause scan of a 24k-char finding
    # (pre-existing); the bound catches a quadratic blow-up, not machine load.
    assert time.perf_counter() - started < 5.0


@pytest.mark.parametrize("pattern", [
    gr._YTD_WORDS_RE, gr._QUARTER_WORDS_RE, gr._METRIC_LINK_RE, ri._CONFLICT_COUNT_RE,
    ri._LEDGER_DISAGREEMENT_RE, ri._NEGATED_DISAGREEMENT_RE, li._NO_FINDINGS_RE,
    li._UNRESOLVED_COUNT_RE,
    # ADR-095 review.
    gr._QUALIFIED_MEASURE_RE, gr._GEO_ABBREVIATION_RE, gr._PREFIX_BOUNDARY_RE,
    gr._PREFIX_TOKEN_RE, gr._PREFIX_WORD_RE, gr._SUB_ITEM_RE, gr._DISCLOSURE_DENIAL_RE,
    ri._RED_TEAM_DISPUTE_RE,
    ri._SEARCHES_RAN_RE,
], ids=lambda p: p.pattern[:30])
def test_new_patterns_are_linear(pattern: re.Pattern[str]) -> None:
    """Review item 6 — linear by RATIO, with the collector paused: the time on a text
    four times longer stays within a linear bound of the shorter one's, so a loaded
    machine (or a collector pass over the suite's heap) cannot fail it, while a
    quadratic pattern (16x) still does."""
    units = (" ", "unresolved ", "no ", "9 ", "(", "was ", "U.S. ", "by segment ")

    def cost(text: str) -> float:
        best = float("inf")
        for _ in range(3):
            started = time.perf_counter()
            for _m in pattern.finditer(text):
                pass
            pattern.fullmatch(text)
            best = min(best, time.perf_counter() - started)
        return best

    was_enabled = gc.isenabled()  # restore, never force on
    gc.disable()
    try:
        for unit in units:
            short = cost(unit * 1500)
            long = cost(unit * 6000)
            # Linear is 4x; quadratic is 16x. 12x leaves room for cache effects.
            assert long < 12 * short + 0.05, (unit, short, long)
            assert long < 0.5
    finally:
        if was_enabled:
            gc.enable()
