"""V3.18.10 — ask the corpus a query, grade a dimension on what names it, keep the period.

Three defects, all read off SCCO's fourth live run and all diagnosed against the
production database rather than guessed at:

1. **The corpus was asked in prose.** Every question's first corpus search sent the
   analyst question verbatim. The index uses the ``simple`` configuration, which keeps
   ``which``/``does``/``the`` as ordinary lexemes, so the question's function words
   decided the ranking. The run then reported "No revenue or profit figures by product,
   segment or geography appear anywhere in the evidence" about a corpus that held
   ``Net sales in 2025 reached a record high of $13,420.0 million``.
2. **A thesis dimension was graded on findings that were not about it.** The
   "semiconductor supply chains" dimension came back ``evidenced`` from a finding about
   the filing's exhibit index, and "What would change the thesis" reported that nothing
   was unestablished.
3. **Every finding built on the subject's own statements carried no period.** The tool
   stamps ``"period": "FY2025"`` on each line; the harvester read ``period_key`` and
   nothing else, so the report's FY2025 revenue, margins and cash conversion all
   reached the page with an empty period.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services.agents.investigator import (
    CORPUS_TOP_K,
    ExternalSearchBudget,
    LLMInvestigator,
    QuestionContext,
    _corpus_arguments,
    _harvest,
    _period_key_of,
)
from app.services.corpus.query_terms import STOPWORDS, keyword_query
from app.services.director.base_model import base_question
from app.services.director.loop import corpus_intents_in
from app.services.pipeline import professional_research as pr
from app.services.playbooks.schema import planned_from
from tests.test_v3_18_8_professional_research import _f, _inputs, _q, _section


class TestAQueryIsNotAQuestion:
    """``keyword_query`` — pure, total, and it never invents a term."""

    def test_the_live_question_loses_its_function_words_and_keeps_its_subject(self) -> None:
        # The exact text that returned six paragraphs of narrative in production.
        query = keyword_query(
            "Which commodities and materials does the company produce and sell, and "
            "what share of revenue and of production does each represent in the latest "
            "reported period? Give the figures and the period."
        )
        assert query == "commodities materials produce sell share revenue production"
        for word in ("which", "does", "the", "company", "period", "figures"):
            assert word not in query.lower().split()

    def test_no_term_is_invented(self) -> None:
        text = "How sensitive is operating income to the copper price?"
        source = {w.strip("?.,").lower() for w in text.split()}
        assert all(term.lower() in source for term in keyword_query(text).split())

    def test_identifiers_and_figures_survive(self) -> None:
        query = keyword_query("What did the FY2025 10-K say about mRNA-1273 and 23,000 tons?")
        assert "FY2025" in query and "10-K" in query and "mRNA-1273" in query
        assert "23" in query.split() and "000" in query.split()

    def test_order_is_kept_and_repeats_are_dropped(self) -> None:
        assert keyword_query("copper Copper molybdenum copper") == "copper molybdenum"

    def test_terms_are_capped(self) -> None:
        assert len(keyword_query(" ".join(f"term{i}" for i in range(50))).split()) == 14

    def test_a_question_of_nothing_but_stopwords_reduces_to_nothing(self) -> None:
        assert keyword_query("What has the issuer disclosed most recently?") == ""

    def test_an_entity_suffix_is_not_a_search_term(self) -> None:
        # In a corpus already filtered to one company, "Corp" matches its every page.
        assert keyword_query("Southern Copper Corp. net sales") == "Southern Copper net sales"

    def test_the_stopword_list_never_swallows_subject_matter(self) -> None:
        for term in ("copper", "molybdenum", "reserves", "smelter", "capex", "dividends",
                     "production", "revenue", "segment", "tailings", "offtake",
                     # Each of these was in a generic stopword list and had to come out:
                     # a pronoun in English, the subject matter of a filing.
                     "mine", "mines", "value", "values", "level", "levels", "state",
                     "list", "lists", "reportable"):
            assert term not in STOPWORDS


class TestTheCorpusIsAskedItsOwnIntent:
    def test_the_first_search_intent_becomes_the_query(self) -> None:
        question = planned_from(base_question("business_model"), origin="director")
        question = _with(question, search_intents=(
            "{company} net sales by product {commodity} revenue share",))
        args = _corpus_arguments(
            question, uuid.uuid4(), {"company": "Southern Copper Corp.", "commodity": "copper"}
        )
        assert args is not None
        assert args["query"] == "Southern Copper net sales product revenue share"
        assert args["top_k"] == CORPUS_TOP_K

    def test_without_an_intent_the_questions_own_terms_are_used(self) -> None:
        question = _with(
            planned_from(base_question("business_model"), origin="director"),
            search_intents=(),
            text="Which commodities does the company sell, and in what share of revenue?",
        )
        args = _corpus_arguments(question, uuid.uuid4(), {})
        assert args is not None
        assert args["query"] == "commodities sell share revenue"

    def test_a_question_with_no_distinctive_term_still_searches(self) -> None:
        """A weak query finds less than a good one. No query finds nothing at all."""
        text = "What has the issuer disclosed most recently, and for which period?"
        question = _with(
            planned_from(base_question("recent_disclosure"), origin="director"),
            search_intents=(), text=text,
        )
        args = _corpus_arguments(question, uuid.uuid4(), {})
        assert args is not None and args["query"] == text

    def test_a_placeholder_with_no_value_is_not_sent(self) -> None:
        question = _with(
            planned_from(base_question("business_model"), origin="director"),
            search_intents=("{company} {commodity} reserves",),
        )
        args = _corpus_arguments(question, uuid.uuid4(), {"company": "Acme", "commodity": None})
        assert args is not None and args["query"] == "Acme reserves"


class TestTheLadderNeverAsksTheSameThingTwice:
    async def test_the_platform_rung_takes_intent_one_and_the_corpus_rung_takes_the_rest(
        self,
    ) -> None:
        session = _Session()
        question = _with(
            planned_from(base_question("business_model"), origin="director"),
            search_intents=("{company} sales by product", "{company} smelter refinery",
                            "{company} mine production tons"),
        )
        worker = _investigator(session)
        _e, _u, steps = await worker._acquire(
            "business_analyst", _role("business_analyst"), question, 50,
            context=QuestionContext(), round_index=0,
        )
        queries = [args["query"] for tool, args in session.calls
                   if tool == "search_company_corpus"]
        # Three distinct queries, in the playbook's own order — the first from the
        # platform rung, the next two from the corpus rung, and none repeated.
        assert queries == ["Southern Copper sales product",
                           "Southern Copper smelter refinery",
                           "Southern Copper mine production tons"]
        assert len(set(queries)) == len(queries)
        platform = next(s for s in steps if s["rung"] == "platform_tools")
        assert platform["corpus_intents"] == 1

    async def test_a_repeat_round_continues_past_every_intent_already_asked(self) -> None:
        session = _Session()
        question = _with(
            planned_from(base_question("business_model"), origin="director"),
            search_intents=("{company} alpha", "{company} beta", "{company} gamma",
                            "{company} delta"),
        )
        worker = _investigator(session)
        # The loop's own accounting: one from the platform rung, two from the corpus rung.
        done = corpus_intents_in(
            [{"rung": "platform_tools", "corpus_intents": 1},
             {"rung": "corpus_by_intent", "queries": ["x", "y"]}]
        )
        assert done == 3
        await worker._acquire(
            "business_analyst", _role("business_analyst"), question, 50,
            context=QuestionContext(corpus_intents_done=done, rounds_attempted=1),
            round_index=1,
        )
        queries = [args["query"] for tool, args in session.calls
                   if tool == "search_company_corpus"]
        assert queries == ["Southern Copper delta"]

    def test_the_accounting_counts_both_rungs(self) -> None:
        assert corpus_intents_in([]) == 0
        assert corpus_intents_in([{"rung": "platform_tools"}]) == 0
        assert corpus_intents_in(
            [{"rung": "platform_tools", "corpus_intents": 1},
             {"rung": "external_search", "query": "q"},
             {"rung": "corpus_by_intent", "queries": ["a"]}]
        ) == 2


class TestATableRowIsRenderedAsWhatItSays:
    """A spacer cell is not content, and a repeat is not a second mention."""

    def test_a_colspan_header_is_counted_once(self) -> None:
        from app.services.corpus.chunking import _row_text

        row = ["\u200b", "Three Months Ended", "Three Months Ended", "Three Months Ended",
               "\u200b", "2025", "2025", "", "2024"]
        assert _row_text(row) == "Three Months Ended | 2025 | 2024"

    def test_a_grid_of_spacer_cells_renders_as_nothing(self) -> None:
        from app.services.corpus.chunking import _row_text

        assert _row_text(["\u200b", "\xa0", "  ", "\ufeff"]) == ""

    def test_real_figures_are_untouched(self) -> None:
        from app.services.corpus.chunking import _row_text

        assert _row_text(["Net sales", "13,420.0", "11,433.4", "13,420.0"]) == (
            "Net sales | 13,420.0 | 11,433.4 | 13,420.0"
        )

    def test_an_empty_row_never_reaches_the_rendering(self) -> None:
        from types import SimpleNamespace

        from app.services.corpus.chunking import render_table

        table = SimpleNamespace(
            column_periods=("FY2025",),
            rows=[["\u200b", "\u200b"], ["Net sales", "13,420.0"]],
        )
        assert render_table(table, max_chars=500) == (
            "Periods: FY2025\nNet sales | 13,420.0"
        )


class TestADimensionIsGradedOnWhatNamesIt:
    def test_a_finding_that_never_names_the_dimension_does_not_evidence_it(self) -> None:
        """The live defect: an exhibit-list finding graded 'semiconductors' evidenced."""
        report = pr.assemble(_inputs(
            questions=[*_QUESTIONS_WITH_SEMIS],
            findings=[_f("x", "thesis_fit", "thesis_fit__semiconductors",
                         "The exhibit list names technical report summaries for five mines.")],
        ))
        thesis = _section(report, "thesis_fit")
        semis = next(d for d in thesis["dimensions"] if d["dimension"] == "semiconductors")
        assert semis["status"] == "not_established"
        assert semis["finding_labels"] == []
        assert semis["findings_not_naming_the_dimension"] == ["F1"]
        assert "do not name semiconductors" in semis["note"]
        # It is still a finding: shown, labelled, citable.
        assert [f["label"] for f in thesis["findings"]] == ["F1"]

    def test_what_would_change_the_thesis_then_says_the_dimension_is_unestablished(
        self,
    ) -> None:
        report = pr.assemble(_inputs(
            questions=[*_QUESTIONS_WITH_SEMIS],
            findings=[_f("x", "thesis_fit", "thesis_fit__semiconductors",
                         "The exhibit list names technical report summaries.")],
        ))
        change = _section(report, "what_would_change_the_thesis")
        assert change["unestablished_thesis_dimensions"] == ["semiconductors"]

    def test_a_finding_that_names_the_dimension_evidences_it(self) -> None:
        report = pr.assemble(_inputs(
            questions=[*_QUESTIONS_WITH_SEMIS],
            findings=[_f("x", "thesis_fit", "thesis_fit__semiconductors",
                         "Copper foil for semiconductor packaging is 4% of shipments.")],
        ))
        semis = next(d for d in _section(report, "thesis_fit")["dimensions"]
                     if d["dimension"] == "semiconductors")
        assert semis["status"] == "evidenced"
        assert semis["finding_labels"] == ["F1"]
        assert "findings_not_naming_the_dimension" not in semis


class TestAFindingKeepsItsPeriod:
    def test_a_tools_period_is_the_evidences_period(self) -> None:
        assert _period_key_of({"period": "FY2025"}) == "FY2025"
        assert _period_key_of({"period": "2026-Q2"}) == "2026-Q2"
        assert _period_key_of({"period": "2026-07"}) == "2026-07"

    def test_an_explicit_period_key_still_wins(self) -> None:
        assert _period_key_of({"period_key": "FY2024", "period": "FY2025"}) == "FY2024"

    def test_a_period_described_in_prose_is_not_a_period_key(self) -> None:
        assert _period_key_of({"period": "the three months ended June 30"}) is None
        assert _period_key_of({"period": "latest"}) is None
        assert _period_key_of({}) is None

    def test_the_statements_tool_now_yields_dated_evidence(self) -> None:
        """The shape ``get_sec_statements`` actually returns, harvested."""
        items = _harvest(
            "get_sec_statements",
            {"items": [
                {"id": "secfin:SCCO:FY2025:revenue", "line": "Net sales", "value": 13420.0,
                 "period": "FY2025", "scope": "group"},
            ]},
            False,
        )
        assert [i.period_key for i in items] == ["FY2025"]
        assert [i.scope_key for i in items] == ["group"]


_QUESTIONS_WITH_SEMIS = [
    _q("thesis_fit__semiconductors", "thesis_fit"),
]


def _with(question: Any, **over: Any) -> Any:
    import dataclasses

    return dataclasses.replace(question, **over)


@dataclass
class _Result:
    ok: bool = True
    payload: dict[str, Any] = field(default_factory=dict)
    contains_untrusted_content: bool = False
    outcome: str = "ok"


@dataclass
class _Session:
    """Scripted results that never satisfy a contract, so every rung is reached."""

    calls: list[tuple[str, dict]] = field(default_factory=list)

    async def call(self, tool: str, arguments: dict, task_ref: str = "") -> _Result:
        self.calls.append((tool, arguments))
        return _Result(payload={"items": []})


def _investigator(session: _Session) -> LLMInvestigator:
    return LLMInvestigator(
        session=session,
        company_id=uuid.uuid4(),
        ticker="SCCO",
        exchange="US",
        client=None,
        company_name="Southern Copper Corp",
        industry="Metals & Mining",
        external_budget=ExternalSearchBudget(limit=0),
    )


def _role(role_id: str):  # noqa: ANN202
    from app.services.director.roles import role_for

    return role_for(role_id)
