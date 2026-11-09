"""ADR-094 — gap state derived from research: the gap queue, its results, stale periods,
inferred fields, and chair absence claims. No network: searches are fakes.

Issuer names appear in fixtures only (the generic mechanisms never name one).
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.services.ledger import report_invariants as ri
from app.services.ledger import store as ledger
from app.services.ledger.canonical_state import build_canonical_state, gap_resolution_state
from app.services.pipeline import gap_reconciliation as gr
from app.services.pipeline import gap_resolution as gres

ISSUER = ("issuer_filing",)

TEN_Q = {
    "document_type": "interim_report", "published_at": "2026-11-03", "indexed_chunks": 739,
    "table_chunks": 271, "pages": 1, "extraction_status": "extracted",
}
AR_TRUNCATED = {
    "document_type": "annual_report", "published_at": "2026-09-24", "indexed_chunks": 261,
    "table_chunks": 198, "pages": 40, "pages_total": 59, "truncated": True,
    "extraction_status": "extracted",
}


def _gap(description: str, **kw) -> gres.GapInput:  # noqa: ANN003
    return gres.GapInput(
        gap_id=kw.pop("gap_id", "g1"),
        gap_type=kw.pop("gap_type", ledger.GAP_EVIDENCE_UNAVAILABLE),
        description=description, **kw,
    )


def _hit(text: str, **kw) -> dict:  # noqa: ANN003
    return {"text": text, "evidence_id": kw.pop("evidence_id", "ev-1"),
            "document_type": "interim_report", "published_at": "2026-11-03",
            "page_start": 12, **kw}


def _search(hits_by_query: dict | None = None, default: list | None = None):  # noqa: ANN202
    calls: list[str] = []

    async def search(query: str):  # noqa: ANN202
        calls.append(query)
        return list((hits_by_query or {}).get(query, default or []))

    search.calls = calls  # type: ignore[attr-defined]
    return search


def _resolve(gaps, inventory, search, **kw):  # noqa: ANN001, ANN003, ANN202
    return asyncio.run(gres.resolve_gaps(gaps, inventory=inventory, search=search, **kw))


# ── §6 routing ──────────────────────────────────────────────────────────────── #


class TestRouting:
    def test_a_cet1_gap_enters_the_reread(self) -> None:
        plan = gres.plan_gap(_gap("No reported FY2025 CET1 ratio for the Firm in the evidence."))
        assert plan.route == gres.ROUTE_REREAD
        assert "cet1" in plan.topics
        assert plan.period == "FY2025"
        assert plan.queries and len(plan.queries) <= gres.MAX_QUERIES_PER_GAP

    def test_a_closed_or_partial_gap_is_mapped(self) -> None:
        closed = gres.plan_gap(_gap("No capex", reconciliation_status="closed"))
        partial = gres.plan_gap(_gap("No capex", reconciliation_status="partially_closed",
                                     gap_status="PARTIALLY_CLOSED"))
        assert closed.route == partial.route == gres.ROUTE_MAPPED_EXISTING

    def test_a_noncomparable_gap_is_still_reread(self) -> None:
        plan = gres.plan_gap(_gap("No FY2026 revenue figure",
                                  reconciliation_status="partially_closed",
                                  gap_status="NONCOMPARABLE"))
        assert plan.route == gres.ROUTE_REREAD

    @pytest.mark.parametrize(("text", "gap_type", "why"), [
        ("Two statements disagree", ledger.GAP_CONFLICTING_SOURCES, gres.WHY_NOT_ABSENCE),
        ("The annual report could not be fetched", ledger.GAP_SOURCE_UNREACHABLE,
         gres.WHY_SOURCE_BLOCKED),
        ("No peer or market-share data on deposits", ledger.GAP_EVIDENCE_UNAVAILABLE,
         gres.WHY_NON_ISSUER),
        ("The model returned an empty reply", ledger.GAP_EVIDENCE_UNAVAILABLE,
         gres.WHY_NO_TOPIC),
    ])
    def test_not_required_always_says_why(self, text: str, gap_type: str, why: str) -> None:
        plan = gres.plan_gap(_gap(text, gap_type=gap_type))
        assert plan.route == gres.ROUTE_NOT_REQUIRED
        assert plan.why == why

    def test_a_gap_is_read_by_its_own_words_not_its_question(self) -> None:
        topics = gres.gap_topics("No customer concentration or major customers figures")
        assert "customer_concentration" in topics and "revenue" not in topics


# ── §5 results ──────────────────────────────────────────────────────────────── #


class TestResults:
    def test_a_held_passage_is_extraction_failed_never_not_found(self) -> None:
        gap = _gap("No reported FY2025 CET1 ratio for the Firm in the evidence.")
        hit = _hit("Common Equity Tier 1 capital ratio 14.0 % 15.0 % (December 31, 2025)",
                   table_location="t1")
        (res,), _ = _resolve([gap], [TEN_Q], _search(default=[hit]))
        assert res.result == gres.RESULT_EXTRACTION_FAILED
        assert res.evidence[0]["document"] == "interim report · 2026-11-03"
        assert res.evidence[0]["evidence_id"] == "ev-1"
        d = res.to_dict()
        assert d["may_say_not_found"] is False
        assert "not found" not in d["words"].lower()
        assert "interim report · 2026-11-03" in d["words"]
        # Never the passage text (untrusted, unverified).
        assert "14.0" not in str(d)

    def test_searched_and_nothing_carries_it_is_not_found_after_search(self) -> None:
        gap = _gap("No liquidity coverage ratio in evidence.")
        (res,), queries = _resolve([gap], [TEN_Q], _search(default=[_hit("Deposits grew.")]))
        assert res.result == gres.RESULT_NOT_FOUND_AFTER_SEARCH
        assert res.to_dict()["may_say_not_found"] is True
        assert "not found in the available evidence" in res.words.lower()
        assert queries >= 1 and res.documents_searched

    def test_a_truncated_held_document_is_document_not_parsed(self) -> None:
        gap = _gap("No total debt or borrowings figure is in evidence.")
        (res,), _ = _resolve([gap], [AR_TRUNCATED], _search(default=[]))
        assert res.result == gres.RESULT_DOCUMENT_NOT_PARSED
        assert res.unparsed_documents == ["annual report · 2026-09-24, pages 41–59"]

    def test_another_period_only_is_period_mismatch(self) -> None:
        gap = _gap("No FY2026 revenue figure is provided.")
        hit = _hit("Total net revenues were $31,797 million for 2025.", period_key="2025")
        (res,), _ = _resolve([gap], [TEN_Q], _search(default=[hit]))
        assert res.result == gres.RESULT_PERIOD_MISMATCH

    def test_a_segment_only_answer_to_a_group_gap_is_scope_mismatch(self) -> None:
        gap = _gap("No group revenue for the interim period.")
        hit = _hit("Segment revenue 870,043 80", scope_type="segment")
        (res,), _ = _resolve([gap], [TEN_Q], _search(default=[hit]))
        assert res.result == gres.RESULT_SCOPE_MISMATCH

    def test_boilerplate_never_counts(self) -> None:
        gap = _gap("No CET1 ratio in evidence.")
        hit = _hit("Forward-looking statements: actual results could differ materially; "
                   "the CET1 ratio of 14% is subject to risks, uncertainties and factors.")
        (res,), _ = _resolve([gap], [TEN_Q], _search(default=[hit]))
        assert res.result == gres.RESULT_NOT_FOUND_AFTER_SEARCH

    def test_no_index_no_corpus_and_failures_are_not_run(self) -> None:
        gap = _gap("No CET1 ratio in evidence.")
        (no_corpus,), _ = _resolve([gap], None, None)
        assert no_corpus.result == gres.RESULT_NOT_RUN and no_corpus.why == gres.WHY_NO_CORPUS
        unindexed = {**TEN_Q, "indexed_chunks": 0}
        (held,), _ = _resolve([gap], [unindexed], _search())
        assert held.result == gres.RESULT_DOCUMENT_NOT_PARSED

        async def broken(query: str):  # noqa: ANN202
            raise RuntimeError("down")

        (failed,), _ = _resolve([gap], [TEN_Q], broken)
        assert failed.result == gres.RESULT_NOT_RUN and failed.why == gres.WHY_SEARCH_FAILED

    def test_identical_queries_run_once_and_the_run_cap_binds(self) -> None:
        gaps = [_gap("No CET1 ratio in evidence.", gap_id=f"g{i}") for i in range(5)]
        search = _search(default=[])
        resolutions, queries = _resolve(gaps, [TEN_Q], search)
        assert queries == len(set(search.calls)) == len(search.calls)
        capped, ran = _resolve(
            [_gap("No CET1 ratio", gap_id="a"), _gap("No LCR figure", gap_id="b")],
            [TEN_Q], _search(), max_run_queries=1,
        )
        assert ran == 1
        assert capped[1].result == gres.RESULT_NOT_RUN and capped[1].why == gres.WHY_BUDGET

    def test_every_result_has_words_and_only_one_says_not_found(self) -> None:
        for result in gres.RESULTS:
            words = gres.render_words({"result": result, "topics": ["cet1"]}).lower()
            assert words
            assert ("not found" in words) == (result in gres.ABSENCE_RESULTS)

    def test_counts(self) -> None:
        gaps = [
            _gap("No CET1 ratio in evidence.", gap_id="a"),
            _gap("No LCR figure in evidence.", gap_id="b"),
            _gap("No capex", gap_id="c", reconciliation_status="closed"),
            _gap("Two statements disagree", gap_id="d",
                 gap_type=ledger.GAP_CONFLICTING_SOURCES),
        ]
        search = _search({"CET1 common equity tier 1 capital ratio": [
            _hit("CET1 ratio 14.0%")]})
        resolutions, queries = _resolve(gaps, [TEN_Q], search)
        counts = gres.counts_of(resolutions, queries_run=queries)
        assert counts["gaps_created"] == 4
        assert counts["gaps_mapped_existing"] == 1
        assert counts["gaps_reread"] == 2
        assert counts["gaps_found"] == 2  # one located, one mapped
        assert counts["gaps_failed"] == 1
        assert counts["gaps_not_required"] == 1


# ── §9/§10 reconciliation ───────────────────────────────────────────────────── #


def _finding(fid: str, statement: str, **kw) -> gr.FindingFacts:  # noqa: ANN003
    fields, project = gr.finding_fields(statement)
    return gr.FindingFacts(
        finding_id=fid, statement=statement, fields=kw.pop("fields", fields),
        project=project, source_kinds=kw.pop("source_kinds", ISSUER),
        stated_period=kw.pop("stated_period", gr.text_period(statement)), **kw,
    )


def _rgap(description: str, **kw) -> gr.GapFacts:  # noqa: ANN003
    return gr.GapFacts(gap_id="g1", gap_type=kw.pop("gap_type", ledger.GAP_EVIDENCE_UNAVAILABLE),
                       description=description, **kw)


def _one(gap: gr.GapFacts, findings: list, **kw) -> gr.GapVerdict:  # noqa: ANN001, ANN003
    (verdict,) = gr.reconcile([gap], findings, as_of=date(2026, 11, 9), **kw)
    return verdict


class TestStalePeriods:
    def test_fy2025_never_partially_closes_an_fy2026_gap(self) -> None:
        older = _finding("f1", "Revenue was US$31.8bn for FY2025.", period_key="FY2025")
        verdict = _one(_rgap("No FY2026 full-year or guidance-period revenue figure."), [older])
        assert verdict.gap_status == gr.GAP_STATUS_NONCOMPARABLE

    def test_a_stale_finding_does_not_answer_a_gap_about_now(self) -> None:
        old = _finding("f1", "Revenue was US$30bn for FY2024.", period_key="FY2024")
        new = _finding("f2", "Segment X revenue was US$9bn in H1 2026.", period_key="2026-H1",
                       scope_key="segment:x")
        verdict = _one(_rgap("No revenue figure is in evidence."), [old, new])
        assert gr.REASON_VALUE_STALE in verdict.reasons
        assert verdict.gap_status == gr.GAP_STATUS_NONCOMPARABLE

    def test_a_finding_whose_words_and_key_disagree_closes_nothing(self) -> None:
        mislabelled = _finding("f1", "Revenue of A$257 for the year to 30 June 2025.",
                               period_key="2026")
        assert mislabelled.period_conflict
        verdict = _one(_rgap("No FY2026 revenue figure."), [mislabelled])
        assert verdict.status != ledger.RECONCILED_CLOSED
        assert verdict.gap_status == gr.GAP_STATUS_NONCOMPARABLE

    def test_a_current_finding_still_closes(self) -> None:
        current = _finding("f1", "Revenue was US$33bn for FY2026.", period_key="FY2026")
        verdict = _one(_rgap("No FY2026 revenue figure."), [current])
        assert verdict.status == ledger.RECONCILED_CLOSED

    def test_another_capex_subtype_is_noncomparable(self) -> None:
        spent = _finding("f1", "Capital expenditure of US$700m was spent in FY2025.",
                         period_key="FY2025")
        verdict = _one(_rgap("No capex figure is in evidence."), [spent])
        assert gr.REASON_SUBTYPE_DIFFERS in verdict.reasons
        assert verdict.gap_status == gr.GAP_STATUS_NONCOMPARABLE

    def test_a_stale_validated_fact_does_not_cover(self) -> None:
        facts = [gr.FactFacts("x1", "revenue", "metric:revenue", "2025", "group"),
                 gr.FactFacts("x2", "revenue", "metric:revenue", "2026-H1", "group")]
        verdict = _one(_rgap("No revenue figure is in evidence."), [], facts=facts)
        assert verdict.fact is not None and verdict.fact["fact_id"] == "x2"


# ── Chair / synthesis absence claims ─────────────────────────────────────────── #


class TestAbsenceClaims:
    def test_absence_topics(self) -> None:
        assert ri.absence_topics("There is no segment split beyond Jewellery Maisons.") == (
            "segment",)
        assert "capex" in ri.absence_topics("Revenue rose 5%, but capex was not disclosed.")
        assert "revenue" not in ri.absence_topics(
            "Revenue rose 5%, but capex was not disclosed.")
        # A statement about the company, not about disclosure.
        assert ri.absence_topics("There is no evidence of deposit outflows.") == ()

    def test_a_chair_sentence_denying_what_it_cites_is_withheld(self) -> None:
        fid = "11111111-1111-1111-1111-111111111111"
        v3 = {
            "findings": [{"finding_id": fid, "statement":
                          "Jewellery Maisons segment sales were EUR 15.3bn and Specialist "
                          "Watchmakers segment sales EUR 3.4bn in FY2026."}],
            "chair": {"synthesis": "Growth is concentrated. There is no segment split "
                                   f"beyond Jewellery Maisons (finding {fid}).",
                      "key_points": []},
        }
        state = build_canonical_state(v3, as_of=date(2026, 11, 9))
        out = ri.enforce(v3, state)
        assert "no segment split" not in out.v3["chair"]["synthesis"]
        assert any(v.rule == ri.RULE_ABSENCE_CONTRADICTED for v in out.violations)

    def test_a_chair_sentence_denying_what_the_reread_located_is_withheld(self) -> None:
        v3 = {
            "gap_resolution": {"counts": {"gaps_created": 1}, "gaps": [{
                "result": "EXTRACTION_FAILED", "topics": ["cet1"],
                "evidence": [{"document": "10-Q", "topics": ["cet1"]}]}]},
            "chair": {"synthesis": "Capital is strong. The CET1 ratio is not disclosed.",
                      "key_points": []},
        }
        state = build_canonical_state(v3, as_of=date(2026, 11, 9))
        assert state["gap_resolution"]["topics_located"] == ["cet1"]
        out = ri.enforce(v3, state)
        assert out.v3["chair"]["synthesis"] == "Capital is strong."

    def test_an_absence_of_something_not_located_is_kept(self) -> None:
        v3 = {"gap_resolution": {"gaps": [{"result": "NOT_FOUND_AFTER_SEARCH",
                                           "topics": ["lcr"]}]},
              "chair": {"synthesis": "The LCR is not disclosed.", "key_points": []}}
        state = build_canonical_state(v3, as_of=date(2026, 11, 9))
        assert ri.enforce(v3, state).v3["chair"]["synthesis"] == "The LCR is not disclosed."

    def test_canonical_state_without_the_queue(self) -> None:
        assert gap_resolution_state({}) is None


# ── Bounded regexes ─────────────────────────────────────────────────────────── #


@pytest.mark.parametrize("pattern", [
    gres._NON_ISSUER_RE, gr._HISTORY_ASKED_RE,
], ids=["non_issuer", "history_asked"])
def test_new_regexes_are_linear(pattern) -> None:  # noqa: ANN001
    # The shared harness (scale 1 vs 8, min of five, noise floor). The report-invariant
    # patterns and the new chair rule are covered by its own pattern inventory.
    from tests.test_qa_closure_ledger_state import _assert_scales_linearly

    def run(text: str) -> None:
        pattern.search(text)
        for _ in pattern.finditer(text):
            pass

    _assert_scales_linearly(run, pattern.pattern[:30])
    _assert_scales_linearly(ri.absence_topics, "absence_topics")


# ── The investigator: a required metric is always read from the held documents ── #


class TestRequiredMetricsReread:
    async def test_a_count_satisfied_cet1_question_still_reads_the_10q(self) -> None:
        """JPM3: capital_adequacy was satisfied by a SUBSIDIARY's CET1 figure, skipped
        the re-read, and its answer said the held 10-Q had no CET1 ratio."""
        from app.services.playbooks.industries import PLAYBOOKS
        from app.services.playbooks.schema import planned_from
        from tests.test_qa_closure_rerun_h1 import TEN_Q as URL
        from tests.test_qa_closure_rerun_h1 import (
            _acquire,
            _primary_calls,
            _Session,
            _worker,
        )
        from tests.test_qa_closure_rerun_h1 import _hit as h1_hit

        question = next(q for p in PLAYBOOKS for q in p.questions
                        if q.key == "capital_adequacy")
        subsidiary = ("J.P. Morgan SE (a subsidiary) reported a CET1 ratio of 20.0% at "
                      "31 December 2024 against a requirement of 10.5%.")
        firm = ("Capital ratios. Common Equity Tier 1 capital ratio 14.0 % 15.0 % "
                "Tier 1 capital ratio 15.5 % 16.4 %")
        session = _Session(
            ordinary=[h1_hit("ev:sub", subsidiary, URL), h1_hit("ev:x", subsidiary, URL)],
            primary=[h1_hit("ev:cet1", firm, URL, table=True)],
        )
        evidence, _u, steps = await _acquire(
            _worker(session), planned_from(question, origin="playbook"), "financial_analyst")
        assert _primary_calls(session), "the required metric was never read"
        step = next(s for s in steps if s["rung"] == "primary_document_reread")
        assert "cet1" in step["why_topics"]
        assert any(e.citation_id == "ev:cet1" for e in evidence)


# ── The persisted queue (SQLite ledger) ────────────────────────────────────── #


class TestPersistedQueue:
    async def test_resolutions_persist_and_reach_the_chair_input(self, monkeypatch) -> None:  # noqa: ANN001
        import uuid

        from app.services.agents.chair import LLMChair
        from app.services.council_v2 import inputs as council_inputs
        from tests.test_report_reconciliation import _seed
        from tests.test_report_reconciliation import session as _session_fixture

        agen = _session_fixture.__wrapped__()
        session = await agen.__anext__()
        try:
            seeded = await _seed(session)
            cet1 = await ledger.record_gap(
                session, seeded.run, gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
                description="No reported CET1 ratio for the Firm in the evidence.",
                question_key="capital_adequacy",
            )
            await gr.reconcile_run(session, seeded.run)
            monkeypatch.setattr(
                gres, "_corpus_search",
                lambda *a, **k: _search(default=[_hit("CET1 capital ratio 14.0%")]),
            )
            record = await gres.resolve_run(
                session, seeded.run, company_id=uuid.uuid4(), inventory=[TEN_Q],
                backend=object(), cfg=None,
            )
            assert record["counts"]["gaps_created"] == 4
            await session.refresh(cet1)
            stored = cet1.reconciliation_json["resolution"]
            assert stored["result"] == gres.RESULT_EXTRACTION_FAILED
            council = await council_inputs.assemble(session, seeded.run)
            shown = {g.description: g for g in council.gaps}
            gap = shown["No reported CET1 ratio for the Firm in the evidence."]
            assert gap.resolution_result == gres.RESULT_EXTRACTION_FAILED
            assert gap.to_dict()["resolution_words"]
            prompt = LLMChair(client=None)._prompt(council)
            assert "RESEARCH STATE EXTRACTION_FAILED" in prompt
            assert "Do not call this undisclosed" in prompt
        finally:
            await agen.aclose()
