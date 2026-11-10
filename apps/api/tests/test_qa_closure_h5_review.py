"""ADR-095 review (H5 NO-GO) — the reviewer's probes, as tests.

Principle: missing beats wrong. A conflict the detector misses is acceptable; a conflict
it invents (adjusted vs reported revenue, a segment vs the Group) is not. No network.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.services.agents.chair import LLMChair
from app.services.council_v2.inputs import CouncilInput
from app.services.council_v2.red_team import ChallengeRoundResult
from app.services.ledger import ledger_invariants as li
from app.services.ledger import report_invariants as ri
from app.services.ledger import search_status as ss
from app.services.ledger import store as ledger
from app.services.ledger.canonical_state import (
    build_canonical_state,
    challenge_state,
    conflict_state,
    statement_slot_conflicts,
)
from app.services.pipeline import gap_reconciliation as gr
from app.services.pipeline import v3_pipeline


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003, ANN202
    return "JSON"


AS_OF = "2026-11-10"
BASE = "FY2025 revenue was $33.2bn."


def _f(i: int, statement: str, *, period: str = "FY2025", scope: str | None = "group",
       kinds: tuple[str, ...] = ("sec_filing",)) -> gr.FindingFacts:
    facts = gr.finding_facts_from_payload({
        "finding_id": f"f{i}", "statement": statement, "scope_key": scope,
        "period_key": period, "source_kinds": list(kinds),
    })
    assert facts is not None
    return facts


# ── Item 3: the detector never sets another measure against the reported total ── #


class TestNoInventedConflict:
    @pytest.mark.parametrize("statement", [
        "FY2025 revenue from continuing operations was $29.0bn.",
        "FY2025 pro forma revenue was $29.0bn.",
        "FY2025 adjusted revenue was $29.0bn.",
        "FY2025 organic revenue was $29.0bn.",
        "FY2025 revenue excluding the divested unit was $29.0bn.",
        "FY2025 revenue at constant currency was $29.0bn.",
        "FY2025 transaction revenues were $29.0bn.",
        "FY2025 revenue grew by $1.4bn.",
        "FY2025 revenue increase of $1.4bn was driven by volume.",
        "FY2025 Americas revenue was $20.1bn.",
        "FY2025 U.S. revenue was $20.1bn.",
        "FY2025 US revenue was $20.1bn.",
        "FY2025 subscription revenue was $20.1bn.",
        "FY2025 product revenue was $20.1bn.",
        "FY2025 services revenue was $20.1bn.",
        "FY2025 underlying revenue was $29.0bn.",
        "FY2025 segment revenue was $20.1bn.",
        "FY2025 revenue, as recast for the spin-off, was $29.0bn.",
        "FY2026 revenue is expected to be $35bn.",
        "FY2025 deferred revenue was $2.1bn.",
        "FY2025 revenue and other income was $2.5bn.",
        "Cumulative revenue since launch was $5.0bn by FY2025.",
        "FY2025 revenue per employee was $1.2m.",
        "Revenue of $31.8bn in FY2024 rose 4% in FY2025.",
    ])
    def test_another_measure_is_never_a_conflict(self, statement: str) -> None:
        assert gr.deterministic_conflicts([_f(1, BASE), _f(2, statement)]) == []

    def test_a_segment_scope_is_never_paired(self) -> None:
        a = _f(1, "FY2025 revenue was $20.1bn.", scope="segment:americas")
        b = _f(2, "FY2025 revenue was $18.0bn.", scope="segment:americas")
        assert gr.deterministic_conflicts([a, b]) == []

    def test_a_us_dollar_amount_is_not_a_region(self) -> None:
        assert not gr.names_other_measure("FY2025 revenue was US$33.2bn.")
        assert not gr.names_other_measure("FY2025 revenue was USD 33.2bn.")
        assert gr.names_other_measure("FY2025 U.S. revenue was $20bn.")

    @pytest.mark.parametrize("a, b", [
        ("Revenue of $33.2 billion in FY2025.", "FY2025 revenue: $31.8 billion."),
        ("Net revenues were $33,172 million in 2025.", "Net revenues were $31,000 million in 2025."),
        ("FY2025 revenue was $33.2bn, up 4.3%.", "FY2025 revenue was $31.8bn, down 1%."),
        ("FY2025 operating cash flow was $6,408m.", "FY2025 operating cash flow was $6,100m."),
        ("FY2025 capital expenditure was USD 986m.", "FY2025 capital expenditure was USD 1,200m."),
    ])
    def test_a_reported_group_figure_stated_twice_is_still_found(self, a: str,
                                                                 b: str) -> None:
        period = "2025" if "2025." in a and "FY" not in a else "FY2025"
        found = gr.deterministic_conflicts([_f(1, a, period=period), _f(2, b, period=period)])
        assert len(found) == 1

    def test_the_issuer_name_may_stand_before_the_metric(self) -> None:
        a = _f(1, "FY2025 Group revenue was USD 33,172m.")
        b = _f(2, "Examplepay FY2025 revenue was USD 31,800m, according to a newswire.",
               kinds=("news",))
        assert gr.deterministic_conflicts([a, b]) == []  # unknown name: skipped
        found = gr.deterministic_conflicts(
            [a, b], issuer_names=("Examplepay Holdings, Inc.", "EXPY"))
        assert len(found) == 1

    def test_generic_legal_words_are_not_issuer_words(self) -> None:
        assert gr.issuer_tokens(["Example International Holdings Inc", "EXI"]) == {
            "example", "exi"}


# ── Item 2: a contradicted absence the Chair is told about is a precise one ──── #


class TestDeniedWhileStated:
    def _absences(self, denial: str, stated: str) -> tuple[list[gr.FindingFacts],
                                                           list[gr.TemporalDisagreement]]:
        facts = [_f(1, denial), _f(2, stated)]
        return facts, gr.contradicted_absences(facts)

    def test_a_denied_split_is_not_contradicted_by_the_total(self) -> None:
        facts, absences = self._absences(
            "No product, geographic or customer revenue split is disclosed for FY2025.",
            "FY2025 revenue was $33.2bn.")
        assert gr.denied_while_stated(facts, absences) == []

    @pytest.mark.parametrize("denial, stated", [
        # Replay (HON4, CLLS7): negated clauses about the company, not about disclosure.
        ("FY2025 capex intensity was 2.63% of revenue, so roughly five-sixths of "
         "operating cash was not spent on capex.",
         "FY2025 operating cash flow was USD 6,408m."),
        ("FY2025 Group operating cash flow was an outflow of USD 39,401k against capex "
         "of USD 2,360k, so no cash was generated after capital spending.",
         "Group capex was USD 2,360 thousand in FY2025."),
    ])
    def test_a_negated_statement_about_the_company_is_no_denial(self, denial: str,
                                                                 stated: str) -> None:
        facts, absences = self._absences(denial, stated)
        assert gr.denied_while_stated(facts, absences) == []

    def test_a_denied_whole_field_is_contradicted_by_its_amount(self) -> None:
        facts, absences = self._absences(
            "FY2025 revenue is not disclosed in the evidence.",
            "FY2025 revenue was $33.2bn.")
        assert absences, "precondition: the broad detector pairs them"
        assert [(d.finding_a_id, d.finding_b_id) for d in
                gr.denied_while_stated(facts, absences)] == [("f1", "f2")]


# ── Item 1: a Red Team dispute is held to the unresolved challenges ──────────── #


class TestRedTeamNarration:
    SENTENCE = "The red team disputed the net debt figure."

    def _state(self, challenges: dict[str, Any] | None) -> dict[str, Any]:
        v3: dict[str, Any] = {"findings": [{"finding_id": "a", "statement": "x"}]}
        if challenges is not None:
            v3["challenges"] = challenges
        return build_canonical_state(v3, as_of=AS_OF)

    def test_the_ledger_disagreement_pattern_no_longer_reads_a_challenge(self) -> None:
        assert ri._LEDGER_DISAGREEMENT_RE.search(self.SENTENCE) is None  # noqa: SLF001

    def test_an_unresolved_challenge_backs_the_sentence(self) -> None:
        state = self._state({"challenges": 2, "unresolved": 1, "unresolved_items": [
            {"challenge_id": "c1", "finding_id": "A", "weakness_class": "single_source",
             "text": "only one source supports the net debt figure"}]})
        assert ri.check_sentence(self.SENTENCE, state, site=ri.SITE_CHAIR) is None
        assert state["challenges"]["unresolved"] == 1
        assert state["challenges"]["ids"] == ["c1"]
        assert state["challenges"]["items"][0]["finding_id"] == "a"

    def test_no_unresolved_challenge_withholds_it(self) -> None:
        hit = ri.check_sentence(self.SENTENCE, self._state({"challenges": 2,
                                                            "unresolved": 0}),
                                site=ri.SITE_CHAIR)
        assert hit is not None and hit.rule == ri.RULE_UNBACKED_CHALLENGE

    def test_an_unknown_count_says_nothing(self) -> None:
        assert ri.check_sentence(self.SENTENCE, self._state(None), site=ri.SITE_CHAIR) is None

    def test_the_round_records_each_unresolved_challenge(self) -> None:
        result = ChallengeRoundResult(unresolved=1, unresolved_items=[
            {"challenge_id": "c1", "finding_id": "a", "weakness_class": "x", "text": "t"}])
        assert result.to_dict()["unresolved_items"][0]["text"] == "t"
        assert challenge_state({"challenges": result.to_dict()})["unresolved"] == 1

    def test_the_panel_renders_each_unresolved_challenge(self) -> None:
        from pathlib import Path

        web = Path(__file__).resolve().parents[2] / "web" / "src" / "components"
        panel = (web / "research" / "report" / "V3ResearchPanel.tsx").read_text()
        reader = (web / "research" / "v3Research.ts").read_text()
        assert 'data-testid="v3-unresolved-challenges"' in panel
        # The reader's keys are the ones canonical_state.challenge_state writes.
        item = challenge_state({"challenges": {"unresolved": 1, "unresolved_items": [
            {"challenge_id": "c", "finding_id": "a", "weakness_class": "w",
             "text": "t"}]}})["items"][0]
        for key in ("finding_id", "weakness_class", "text"):
            assert key in item and f"item.{key}" in reader
        assert "readUnresolvedChallenges(v.challenges)" in reader


# ── Item 4: statement-line conflicts sit in the one ledger ───────────────────── #


HON_LIKE_SLOT = {"conflicts": [{
    "field": "cash_and_equivalents", "period": "2025",
    "note": "The issuer's documents state this figure differently.",
    "values": [
        {"numeric_value": 12273.0, "scale": "million", "currency": "USD",
         "document_title": "10-Q", "document_date": "2026-10-22"},
        {"numeric_value": 12487.0, "scale": "million", "currency": "USD",
         "document_title": "10-K", "document_date": None},
    ],
}]}


class TestStatementSlotConflicts:
    def _v3(self) -> dict[str, Any]:
        return {"financial_statements_state": HON_LIKE_SLOT,
                "gap_reconciliation": {"conflicts": []},
                "findings": [{"finding_id": "a", "statement": "x"}]}

    def test_they_are_counted_with_their_source(self) -> None:
        state = conflict_state(self._v3())
        assert state["count"] == 1
        assert state["finding_count"] == 0
        assert state["statement_slot_count"] == 1
        assert state["ids"] == []
        assert state["items"][0]["source"] == "statement_slot"

    def test_one_value_is_no_conflict(self) -> None:
        one = {"conflicts": [{**HON_LIKE_SLOT["conflicts"][0],
                              "values": HON_LIKE_SLOT["conflicts"][0]["values"][:1]}]}
        assert statement_slot_conflicts({"financial_statements_state": one}) == []

    def test_they_back_a_narrated_conflict(self) -> None:
        state = build_canonical_state(self._v3(), as_of=AS_OF)
        assert ri.check_sentence(
            "There are unresolved conflicts in the evidence regarding cash and cash equivalents.", state,
            site=ri.SITE_CHAIR) is None
        assert li.impossible_combinations(self._v3(), state) == []

    @pytest.mark.parametrize("sentence", [
        # Replay (HON4, CFR2): a line the slot conflicts do not cover, or findings named
        # as the parties, is not backed by a disagreement between the issuer's documents.
        "There are unresolved disagreements in the evidence regarding capital "
        "expenditure, operating cash flow, and revenue data.",
        "However, unresolved disagreements exist regarding the net debt status due to "
        "conflicting findings.",
        "There are unresolved conflicts in the evidence regarding Q3 2026 cash and cash equivalents.",
    ])
    def test_a_sentence_about_another_line_is_not_backed(self, sentence: str) -> None:
        state = build_canonical_state(self._v3(), as_of=AS_OF)
        hit = ri.check_sentence(sentence, state, site=ri.SITE_CHAIR)
        assert hit is not None and hit.rule == ri.RULE_UNBACKED_DISAGREEMENT

    def test_the_chair_is_told(self) -> None:
        council = CouncilInput(research_run_id=uuid.uuid4(), convened=True)
        v3_pipeline._attach_chair_conflicts(council, SimpleNamespace(  # noqa: SLF001
            financial_statements=HON_LIKE_SLOT,
            gap_reconciliation={"absences_contradicted_precise": [
                {"finding_a_id": "a", "finding_b_id": "b", "field": "metric:revenue",
                 "reason": "absence_contradicted"}]}))
        prompt = LLMChair()._prompt(council)  # noqa: SLF001
        assert "STATEMENT-LINE CONFLICTS" in prompt
        assert "12273.0" in prompt and "12487.0" in prompt
        assert "FINDINGS DENIED BY ANOTHER FINDING" in prompt
        assert "metric:revenue: a denies; b states it" in prompt


# ── Item 7: credits, and follow-up cache serves ──────────────────────────────── #


def _search_v3(*, executed: int, from_cache: int, failed: int, calls: int,
               followup_cache: int | None = 0) -> dict[str, Any]:
    round_: dict[str, Any] = {"queries": ["q"] * 3, "executed": 3, "failed": 0,
                              "not_issued": 0, "candidates": 3}
    if followup_cache is not None:
        round_["from_cache"] = followup_cache
    return {
        "web_context": {"provider": "tavily", "queries": {
            "planned": executed + failed, "executed": executed, "failed": failed,
            "not_issued": 0, "from_cache": from_cache},
            "followup": {"rounds": [round_]}},
        "consumption": {"web_search_calls": 0,
                        "web_stage": {"web_search_calls": calls, "tavily_credits": calls}},
    }


class TestSearchCredits:
    def test_more_network_searches_than_recorded_calls_is_a_violation(self) -> None:
        status = ss.build_search_status(_search_v3(executed=10, from_cache=2, failed=1,
                                                   calls=5))
        assert {v["code"] for v in status["violations"]} == {ss.SEARCH_CALLS_EXCEED_SPEND}

    def test_cache_serves_are_not_network_calls(self) -> None:
        status = ss.build_search_status(_search_v3(executed=10, from_cache=8, failed=1,
                                                   calls=4, followup_cache=2))
        assert status["violations"] == []
        assert status["classes"]["web_followup"]["from_cache"] == 2
        assert status["web"]["from_cache"] == 10

    def test_unrecorded_follow_up_cache_is_not_checked(self) -> None:
        # A round written before the review has no from_cache: a cache serve there
        # would read as a network call, so the exact check is not made.
        status = ss.build_search_status(_search_v3(executed=10, from_cache=9, failed=0,
                                                   calls=1, followup_cache=None))
        assert status["violations"] == []

    def test_the_status_line_and_the_professional_block_say_from_cache(self) -> None:
        from app.services.pipeline import professional_research as pr

        v3 = _search_v3(executed=10, from_cache=8, failed=1, calls=4, followup_cache=2)
        line = ss.web_status_line(ss.build_search_status(v3))
        assert line is not None and line["from_cache"] == 10
        block = pr._web_stage_searches(v3["web_context"], v3["web_context"]["queries"])  # noqa: SLF001
        assert block["searches_from_cache"] == 10


# ── Item 10: the search-line repair is in the payload ────────────────────────── #


class TestSearchLineRepair:
    def test_the_payload_count_and_label_are_repaired(self) -> None:
        v3 = _search_v3(executed=10, from_cache=0, failed=1, calls=14)
        v3["professional_research"] = {"sections": [{"web_research": {
            "searches_run": 4, "searches_planned": 20,
            "label": "Web search incomplete (4 of 20 searches ran)"}}]}
        state = build_canonical_state(v3, as_of=AS_OF)
        result = ri.enforce(v3, state)
        web = result.v3["professional_research"]["sections"][0]["web_research"]
        assert web["searches_run"] == 14
        assert web["searches_planned"] == 14
        assert web["label"] == "Web search incomplete (14 of 14 searches ran)"
        assert {v.rule for v in result.violations} >= {ri.RULE_SEARCH_COUNT}
        # The input is never mutated.
        assert v3["professional_research"]["sections"][0]["web_research"][
            "searches_run"] == 4


# ── Item 9: the checker is logged — counts and codes only ────────────────────── #


class TestEnforceResearchState:
    def _run(self) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = {
            "research_run_id": "run-1",
            "findings": [{"finding_id": "a", "statement": "FY2025 revenue was $1bn."}],
            "chair": {"synthesis": "The council had no findings to synthesise.",
                      "key_points": []},
        }
        out = v3_pipeline._enforce_research_state(  # noqa: SLF001
            SimpleNamespace(created_at=None, content_markdown=""), {}, payload,
            SimpleNamespace(ledger_tally=None))
        return payload, out

    def test_the_result_is_recorded_and_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger=v3_pipeline.__name__):
            _payload, out = self._run()
        found = out["state_invariants"]["impossible_combinations"]
        assert isinstance(found, list)
        lines = [r.getMessage() for r in caplog.records
                 if r.getMessage().startswith("v3_impossible_combinations")]
        assert len(lines) == 1
        assert f"count={len(found)}" in lines[0]
        # Never a sentence of the report.
        assert "synthesise" not in lines[0]

    def test_a_checker_failure_is_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def boom(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
            raise RuntimeError("secret report text")

        monkeypatch.setattr(li, "impossible_combinations", boom)
        with caplog.at_level(logging.WARNING, logger=v3_pipeline.__name__):
            _payload, out = self._run()
        assert "impossible_combinations" not in out["state_invariants"]
        lines = [r.getMessage() for r in caplog.records]
        assert any("v3_impossible_combinations_check_failed" in m
                   and "error=RuntimeError" in m for m in lines)
        assert not any("secret report text" in m for m in lines)


# ── Item 11: a pair already recorded keeps its id in the conflict list ───────── #


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", future=True, poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


class TestExistingPairIds:
    async def test_a_recorded_pair_keeps_its_id(self, session) -> None:  # noqa: ANN001
        run = await ledger.open_run(session, mode="standard")
        a = await ledger.record_finding(session, run, statement="FY2025 revenue was $33.2bn.",
                                        evidence_ids=["ev:a"], period_key="FY2025",
                                        scope_key="group")
        b = await ledger.record_finding(session, run, statement="FY2025 revenue was $31.8bn.",
                                        evidence_ids=["ev:b"], period_key="FY2025",
                                        scope_key="group")
        held = await ledger.record_disagreement(session, run, finding_a=a, finding_b=b,
                                                nature="value", description="held")
        await session.commit()
        result = await gr.reconcile_run(session, run)
        assert result["temporal_disagreements_recorded"] == 0
        assert [c["disagreement_id"] for c in result["conflicts"]] == [str(held.id)]
