"""ADR-076 — a newer document the platform HOLDS reaches the recency questions.

Hypothesis C of the 2026-11-02 cross-sector QA campaign ("acquired but not used"), in
3 of 8 live reports: PYPL's Q3 EX-99.1 was read, yet "headline figures of the latest
period" ended ``not_acquired``; Lindian's Sep-2026 Appendix 5B and quarterly report were
chunked and ready, yet the report quoted the 30-Jun cash and said no quarterly report
was present. Three defects, each pinned here:

1. the headline-figures question could read only the web — never the corpus;
2. a recency question's corpus search ranked by term density alone, so a 261-chunk
   annual report outranked the newer 5B on every run;
3. an empty answer beside a held document was recorded as ``not_acquired``.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.ledger import ResearchQuestion
from app.models.research_chunk import ResearchDocumentChunk
from app.services.agent_tools.corpus_search import (
    _search_company_corpus,
    validate_search_company_corpus,
)
from app.services.agents import investigator as inv
from app.services.agents.investigator import LLMInvestigator, QuestionContext
from app.services.corpus.search.backends.memory import InMemorySearchBackend
from app.services.corpus.search.types import CorpusChunk, CorpusFilters
from app.services.director import loop as director_loop
from app.services.director.base_model import RECENCY_QUESTION_KEYS, base_question
from app.services.director.loop import GapDraft, TaskOutcome, run_investigation
from app.services.director.planner import persist_plan, plan_research
from app.services.director.roles import role_for
from app.services.ledger import store as ledger
from app.services.pipeline.professional_research import domain_cost
from app.services.playbooks.schema import planned_from

EXTERNAL = "recent_external_developments"
CORPUS = "search_company_corpus"

_CFG = SimpleNamespace(
    v3_agent_tools_enabled=True,
    v3_company_web_research_enabled=True,
    v3_web_search_enabled=True,
    v3_web_search_provider="fake",
    v3_filings_tool_enabled=True,
    v3_corpus_enabled=True,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


# ── 1. The planner: the headline-figures question may read the corpus ───────── #


async def _plan(gaps: tuple = ()):  # noqa: ANN202
    return await plan_research(
        subject="LIN:ASX", mode="standard", prior_open_gaps=gaps, cfg=_CFG
    )


class TestThePlannedQuestionsCanReadTheCorpus:
    async def test_the_headline_figures_question_has_corpus_access_and_intents(self) -> None:
        plan = await _plan()
        question = next(q for q in plan.questions if q.key == EXTERNAL)
        assert CORPUS in question.optional_tools
        assert question.search_intents
        assert any("quarter" in i for i in question.search_intents)
        assert any("cash flow" in i for i in question.search_intents)
        assert question.evidence_contract.allow_external is True

    async def test_its_web_route_is_unchanged(self) -> None:
        plan = await _plan()
        question = next(q for q in plan.questions if q.key == EXTERNAL)
        # The corpus is OPTIONAL: it never decides who answers.
        assert set(question.required_tools) == {"search_web", "fetch_public_source"}
        owners = [t.role_id for t in plan.tasks if EXTERNAL in t.question_keys]
        assert owners == ["external_research_analyst"]

    async def test_a_re_asked_headline_question_keeps_its_corpus_access(self) -> None:
        plan = await _plan(((EXTERNAL, f"No citable evidence was retrieved for '{EXTERNAL}'."),))
        question = next(q for q in plan.questions if q.key == EXTERNAL)
        assert question.origin == "prior_gap"
        assert CORPUS in question.optional_tools and question.search_intents
        assert question.evidence_contract.allow_external is True

    def test_recent_disclosure_has_real_intents(self) -> None:
        question = base_question("recent_disclosure")
        assert question is not None and len(question.search_intents) >= 2
        assert all(inv.keyword_query(i.replace("{company}", "")) for i in question.search_intents)

    def test_the_recency_keys_are_real_question_keys(self) -> None:
        from app.services.director.base_model import BASE_QUESTION_KEYS
        from app.services.director.planner import EXTERNAL_QUESTIONS

        known = BASE_QUESTION_KEYS | {key for key, _t, _tools in EXTERNAL_QUESTIONS}
        assert RECENCY_QUESTION_KEYS <= known


# ── 2. The recency pass, over a REAL tool and the memory backend ───────────── #

SUBJECT = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")
ANNUAL_DATE = date(2026, 7, 30)
FIVE_B_DATE = date(2026, 10, 26)

#: Term-dense, like an annual report's cash-flow statement and its quarterly notes.
_ANNUAL_TEXT = (
    "Annual report. Quarterly cash flow report results period ended quarterly report "
    "results release announcement cash flow quarterly activities results report "
    "period ended 30 June 2026. Paragraph {n}."
)
_FIVE_B_TEXT = (
    "Appendix 5B quarterly cash flow report. Cash and cash equivalents at end of "
    "quarter A$41.3 million. Item {n}."
)
_UNDATED_TEXT = (
    "Quarterly report results release announcement quarterly cash flow report results "
    "quarterly activities results period ended results release."
)


def _chunk(doc: str, n: int, text: str, published: date | None,
           company: uuid.UUID = SUBJECT) -> CorpusChunk:
    return CorpusChunk(
        chunk_id=f"{doc}-{n}",
        text=text.format(n=n),
        research_document_id=uuid.uuid5(uuid.NAMESPACE_URL, doc),
        research_document_version_id=uuid.uuid5(uuid.NAMESPACE_URL, doc + "/v1"),
        company_id=company,
        canonical_url=f"https://example.test/{doc}.pdf",
        title=doc,
        document_type="annual_report" if doc == "annual" else "quarterly_report",
        source_tier="T1_primary_company_source",
        published_at=published,
    )


async def _backend(*, with_undated: bool = False, with_five_b: bool = True) -> InMemorySearchBackend:
    backend = InMemorySearchBackend()
    chunks = [_chunk("annual", n, _ANNUAL_TEXT, ANNUAL_DATE) for n in range(60)]
    if with_five_b:
        chunks += [_chunk("five_b", n, _FIVE_B_TEXT, FIVE_B_DATE) for n in range(3)]
    if with_undated:
        chunks.append(_chunk("undated", 0, _UNDATED_TEXT, None))
    # Another company's newer document must neither anchor the window nor be returned.
    chunks.append(_chunk("other", 0, _FIVE_B_TEXT, date(2026, 12, 1), company=OTHER))
    await backend.index(chunks)
    return backend


@dataclass
class _Result:
    ok: bool = True
    payload: dict | None = None
    contains_untrusted_content: bool = False
    refusal_reason: str | None = None
    outcome: str = "ok"


@dataclass
class _CorpusSession:
    """The investigator's tool session, wired to the REAL corpus tool and backend."""

    backend: Any
    calls: list[tuple[str, dict]] = field(default_factory=list)

    async def call(self, tool: str, arguments: dict, task_ref: str = "") -> _Result:
        self.calls.append((tool, arguments))
        if tool != CORPUS:
            return _Result(ok=False)
        validated = validate_search_company_corpus(arguments)
        context = SimpleNamespace(search_backend=self.backend, cfg=_CFG, session=None)
        payload = await _search_company_corpus(context, validated)
        return _Result(
            payload=payload,
            contains_untrusted_content=bool(payload.get("contains_untrusted_content")),
        )


def _worker(session: _CorpusSession) -> LLMInvestigator:
    return LLMInvestigator(
        session=session,
        company_id=SUBJECT,
        ticker="LIN",
        exchange="ASX",
        client=None,
        company_name="Lindian Resources Limited",
        industry="Metals & Mining",
    )


def _recent_disclosure():  # noqa: ANN202
    return planned_from(base_question("recent_disclosure"), origin="director")


async def _acquire(question: Any, backend: Any, role_id: str = "event_analyst"):  # noqa: ANN202
    session = _CorpusSession(backend)
    evidence, used, steps = await _worker(session)._acquire(
        role_id, role_for(role_id), question, 20, context=QuestionContext(), round_index=0
    )
    return evidence, used, steps, session


class TestTheNewerReportReachesTheEvidence:
    async def test_without_the_pass_the_annual_report_crowds_the_5b_out(self) -> None:
        """The defect, reproduced: the same corpus, the same intents, a question that is
        NOT a recency question — the ordinary search never reaches the 5B."""
        question = dataclasses.replace(_recent_disclosure(), key="not_a_recency_question")
        evidence, _u, _s, _session = await _acquire(question, await _backend())
        assert evidence, "the ordinary search must still return the annual report"
        assert all(item.published_at == ANNUAL_DATE for item in evidence)

    async def test_the_5b_leads_the_recency_questions_evidence(self) -> None:
        evidence, _u, steps, session = await _acquire(_recent_disclosure(), await _backend())
        five_b = [item for item in evidence if item.published_at == FIVE_B_DATE]
        assert five_b, "the newer quarterly cash-flow report never reached the evidence"
        assert evidence[0].published_at == FIVE_B_DATE, "the newest document must lead"
        # Additive: the ordinary search still ran, and its annual-report hits remain.
        assert any(item.published_at == ANNUAL_DATE for item in evidence)
        windows = [a for t, a in session.calls if t == CORPUS and a.get("recent_window_days")]
        assert len(windows) == 1
        step = next(s for s in steps if s["rung"] == "recency_corpus")
        assert step["newest_held"] == FIVE_B_DATE.isoformat()
        assert step["published_from"] == date(2026, 9, 11).isoformat()
        assert step["items"] == len(five_b)
        # No duplicate citation, and the pass's hits are capped.
        ids = [item.citation_id for item in evidence]
        assert len(ids) == len(set(ids))
        assert len(five_b) <= inv.RECENCY_MAX_ITEMS

    async def test_the_headline_figures_question_reads_the_corpus_and_the_5b(self) -> None:
        plan = await _plan()
        question = next(q for q in plan.questions if q.key == EXTERNAL)
        evidence, _u, _steps, session = await _acquire(
            question, await _backend(), role_id="external_research_analyst"
        )
        assert any(t == CORPUS and not a.get("recent_window_days") for t, a in session.calls), (
            "the ordinary corpus search must run for the headline question"
        )
        assert any(item.published_at == FIVE_B_DATE for item in evidence)

    async def test_the_pass_spends_one_tool_call_and_counts_as_a_corpus_query(self) -> None:
        _e, _used, steps, session = await _acquire(_recent_disclosure(), await _backend())
        recency = [s for s in steps if s["rung"] == "recency_corpus"]
        assert len(recency) == 1 and recency[0]["tool_calls"] == 1
        # The step is not an intent: the next round's corpus rung does not skip one.
        assert director_loop.corpus_intents_in(recency) == 0
        assert domain_cost([("catalysts", recency)])["catalysts"]["corpus_queries"] == 1

    async def test_a_question_that_is_not_about_recency_gets_no_extra_pass(self) -> None:
        question = planned_from(base_question("material_risks"), origin="director")
        _e, _u, steps, session = await _acquire(question, await _backend(), "risk_analyst")
        assert not any(a.get("recent_window_days") for _t, a in session.calls)
        assert not any(s["rung"] == "recency_corpus" for s in steps)

    async def test_no_dated_document_means_no_window_and_no_failure(self) -> None:
        backend = InMemorySearchBackend()
        await backend.index([_chunk("undated", 0, _UNDATED_TEXT, None)])
        evidence, _u, steps, _session = await _acquire(_recent_disclosure(), backend)
        step = next(s for s in steps if s["rung"] == "recency_corpus")
        assert step["newest_held"] is None and step["items"] == 0
        # The ordinary search still read the undated document.
        assert evidence and evidence[0].published_at is None


class TestUndatedChunksAreNotDropped:
    async def test_the_ordinary_search_still_returns_an_undated_chunk(self) -> None:
        backend = await _backend(with_undated=True)
        validated = validate_search_company_corpus(
            {"query": "quarterly results release", "company_ids": [str(SUBJECT)],
             "mode": "lexical", "top_k": 12}
        )
        payload = await _search_company_corpus(
            SimpleNamespace(search_backend=backend, cfg=_CFG, session=None), validated
        )
        assert any(item["published_at"] is None for item in payload["items"])
        assert "recency_window" not in payload

    async def test_the_recency_question_keeps_the_undated_chunk_too(self) -> None:
        evidence, _u, _steps, _session = await _acquire(
            _recent_disclosure(), await _backend(with_undated=True)
        )
        assert any(item.published_at is None for item in evidence)
        assert any(item.published_at == FIVE_B_DATE for item in evidence)

    async def test_only_the_window_excludes_undated_chunks(self) -> None:
        backend = await _backend(with_undated=True)
        validated = validate_search_company_corpus(
            {"query": "quarterly results release", "company_ids": [str(SUBJECT)],
             "mode": "lexical", "top_k": 12, "recent_window_days": 45}
        )
        payload = await _search_company_corpus(
            SimpleNamespace(search_backend=backend, cfg=_CFG, session=None), validated
        )
        assert payload["items"]
        assert all(item["published_at"] == FIVE_B_DATE.isoformat() for item in payload["items"])
        assert payload["recency_window"]["newest_held"] == FIVE_B_DATE.isoformat()


class TestTheWindowArgument:
    async def test_newest_ignores_undated_and_other_companies(self) -> None:
        backend = await _backend(with_undated=True)
        newest = await backend.newest_published_at(CorpusFilters(company_ids=(SUBJECT,)))
        assert newest == FIVE_B_DATE

    @pytest.mark.parametrize("bad", [0, -3, 400, "soon"])
    def test_an_out_of_range_window_is_refused(self, bad: Any) -> None:
        with pytest.raises(ValueError):
            validate_search_company_corpus(
                {"query": "x", "company_ids": [str(SUBJECT)], "recent_window_days": bad}
            )

    def test_no_window_is_the_default(self) -> None:
        validated = validate_search_company_corpus({"query": "x", "company_ids": [str(SUBJECT)]})
        assert validated["recent_window_days"] is None


# ── 3. The loop names an unread held document honestly ─────────────────────── #


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@dataclass
class _EmptyInvestigator:
    """Every question worked, nothing citable returned."""

    async def investigate(  # noqa: ANN201
        self, *, role_id, questions, round_index, remaining_tool_calls, question_context=None  # noqa: ANN001
    ):
        outcome = TaskOutcome(tool_calls=len(questions))
        for q in questions:
            outcome.question_evidence[q.key] = []
            outcome.acquisition_steps[q.key] = [{"rung": "platform_tools", "round": round_index}]
            outcome.gaps.append(
                GapDraft(
                    gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
                    description=f"No citable evidence was retrieved for {q.key!r}.",
                    question_key=q.key,
                )
            )
        return outcome


def _held_chunk(company_id: uuid.UUID, published: date | None) -> ResearchDocumentChunk:
    return ResearchDocumentChunk(
        id=uuid.uuid4(),
        chunk_id=f"c-{uuid.uuid4().hex[:8]}",
        derivation_id=uuid.uuid4(),
        research_document_version_id=uuid.uuid4(),
        company_id=company_id,
        kind="text",
        ordinal=0,
        text="Appendix 5B quarterly cash flow report",
        char_start=0,
        char_end=38,
        published_at=published,
        indexable=True,
        indexed_at=datetime(2026, 10, 27, tzinfo=UTC),
    )


async def _reasons(session: Any, *, held: date | None | str = "none") -> dict[str, str | None]:
    company_id = uuid.uuid4()
    if held != "none":
        session.add(_held_chunk(company_id, held))  # type: ignore[arg-type]
        await session.flush()
    run = await ledger.open_run(session, mode="deep", company_id=company_id)
    plan = await plan_research(subject="LIN:ASX", mode="deep", cfg=_CFG)
    await persist_plan(session, run, plan)
    await run_investigation(
        session, run, plan, investigator=_EmptyInvestigator(), limits=plan.limits
    )
    rows = (
        await session.execute(
            select(ResearchQuestion).where(ResearchQuestion.research_run_id == run.id)
        )
    ).scalars().all()
    return {r.question_key: r.unresolved_reason for r in rows}


class TestTheUnresolvedReasonIsHonest:
    async def test_a_held_newer_document_is_acquired_not_read(self, session) -> None:
        reasons = await _reasons(session, held=FIVE_B_DATE)
        assert reasons["recent_disclosure"] == ledger.UNRESOLVED_ACQUIRED_NOT_READ
        assert reasons[EXTERNAL] == ledger.UNRESOLVED_ACQUIRED_NOT_READ
        # A question that is not about the newest disclosure keeps its reason.
        assert reasons["governance_and_control"] == ledger.UNRESOLVED_NOT_ACQUIRED

    async def test_with_nothing_held_it_is_still_not_acquired(self, session) -> None:
        reasons = await _reasons(session)
        assert reasons["recent_disclosure"] == ledger.UNRESOLVED_NOT_ACQUIRED

    async def test_an_undated_holding_proves_nothing_about_recency(self, session) -> None:
        reasons = await _reasons(session, held=None)
        assert reasons["recent_disclosure"] == ledger.UNRESOLVED_NOT_ACQUIRED

    def test_the_new_reason_is_in_the_closed_vocabulary(self) -> None:
        assert ledger.UNRESOLVED_ACQUIRED_NOT_READ in ledger.UNRESOLVED_REASONS
        assert len(ledger.UNRESOLVED_ACQUIRED_NOT_READ) <= 40  # the column's width


# -- Review round 1 (PR #312) ---------------------------------------------------------------


def test_a_stale_newest_holding_is_not_a_current_holding() -> None:
    from datetime import date, datetime, timezone

    from app.services.director.loop import CURRENT_HOLDING_MAX_AGE_DAYS, _is_current_holding

    today = date(2026, 11, 2)
    assert _is_current_holding(date(2026, 9, 30), today=today)
    assert _is_current_holding(datetime(2026, 7, 6, tzinfo=timezone.utc), today=today)
    assert not _is_current_holding(date(2024, 9, 1), today=today)
    assert not _is_current_holding(None, today=today)
    assert CURRENT_HOLDING_MAX_AGE_DAYS == 120
