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
        # ADR-076 review: anchor — 75 days back from the newest disclosure (was 45).
        assert step["published_from"] == date(2026, 8, 12).isoformat()
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
        assert step["stopped"] == "no_disclosure_anchor"  # review D3
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


def _held_chunk(
    company_id: uuid.UUID,
    published: date | None,
    *,
    source_tier: str = "T1_primary_filing",
    version_id: uuid.UUID | None = None,
) -> ResearchDocumentChunk:
    return ResearchDocumentChunk(
        id=uuid.uuid4(),
        chunk_id=f"c-{uuid.uuid4().hex[:8]}",
        derivation_id=uuid.uuid4(),
        research_document_version_id=version_id or uuid.uuid4(),
        company_id=company_id,
        source_tier=source_tier,
        kind="text",
        ordinal=0,
        text="Appendix 5B quarterly cash flow report",
        char_start=0,
        char_end=38,
        published_at=published,
        indexable=True,
        indexed_at=datetime(2026, 10, 27, tzinfo=UTC),
    )


async def _reasons(
    session: Any, *, held: date | None | str = "none", seed: Any = None
) -> dict[str, str | None]:
    company_id = uuid.uuid4()
    if held != "none":
        session.add(_held_chunk(company_id, held))  # type: ignore[arg-type]
        await session.flush()
    if seed is not None:
        await seed(session, company_id)
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


# -- ADR-076 review: anchor (live Cellectis rerun df36d6f6) ----------------------------------
#
# The recency window anchored on a Boursorama quote page dated the run day (an
# ``unknown_web`` page whose date came from its text), so it started 2026-09-18 and missed
# the H1 6-K/A filed 2026-09-11 and the 2026-09-14 pivot. Only an issuer / primary
# DISCLOSURE may anchor it now, and the window is 75 days.

from app.services.corpus.search.types import (  # noqa: E402
    DISCLOSURE_SOURCE_CLASSES,
    DISCLOSURE_SOURCE_TIERS,
    is_disclosure,
)

RUN_DAY = date(2026, 11, 2)
#: Review D1 (PR #316): exchange-host quote / news / company pages. None may anchor.
_NOT_ANCHORS = (
    "https://www.nasdaq.com/market-activity/stocks/clls",
    "https://www.nasdaq.com/articles/cellectis-stock-jumps-on-pivot",
    "https://www.londonstockexchange.com/stock/ANTO/antofagasta-plc/company-page",
    "https://www.asx.com.au/markets/company/lin",
    "https://www.otcmarkets.com/stock/XYZ/quote",
)
SIX_K_DATE = date(2026, 9, 11)  # 52 days before the run day
PIVOT_DATE = date(2026, 9, 14)
DATA_RELEASE_DATE = date(2026, 10, 27)  # 46 days after the 6-K/A

_SIX_K_TEXT = (
    "Half-year results release. Interim condensed consolidated statement of cash flows; "
    "cash and cash equivalents of $169 million at 30 June 2026. Item {n}."
)
_QUOTE_TEXT = (
    "Cours action, cotation, results release, quarterly results, cash flow report, "
    "announcement, quarter results revenue reported. Ligne {n}."
)
_PIVOT_TEXT = "Company announces strategic transformation and quarterly results release {n}."


def _doc_chunk(
    doc: str,
    n: int,
    text: str,
    published: date | None,
    *,
    tier: str,
    source_class: str | None = None,
    access_class: str | None = None,
) -> CorpusChunk:
    return dataclasses.replace(
        _chunk(doc, n, text, published),
        source_tier=tier,
        source_class=source_class,
        access_class=access_class,
        document_type="web_page" if source_class else "interim_report",
    )


def _quote_page(n: int = 0) -> CorpusChunk:
    # Unclassified web, T5: what the Boursorama page was.
    return _doc_chunk("quote", n, _QUOTE_TEXT, RUN_DAY, tier="T5_api_aggregator",
                      source_class="unknown_web")


def _six_k(n: int) -> CorpusChunk:
    # An SEC filing through the core-filing connector: T1, no web class.
    return _doc_chunk("six_k", n, _SIX_K_TEXT, SIX_K_DATE, tier="T1_primary_filing")


async def _windowed_payload(backend: Any, days: int = inv.RECENCY_WINDOW_DAYS) -> dict:
    validated = validate_search_company_corpus(
        {"query": "results release cash flow", "company_ids": [str(SUBJECT)],
         "mode": "lexical", "top_k": 12, "recent_window_days": days}
    )
    return await _search_company_corpus(
        SimpleNamespace(search_backend=backend, cfg=_CFG, session=None), validated
    )


class TestTheAnchorIsADisclosure:
    async def test_a_quote_page_dated_today_does_not_move_the_window_past_the_6k(self) -> None:
        backend = InMemorySearchBackend()
        await backend.index([_quote_page(0), _quote_page(1), _six_k(0), _six_k(1)])
        payload = await _windowed_payload(backend)
        window = payload["recency_window"]
        assert window["newest_held"] == SIX_K_DATE.isoformat()
        dates = {item["published_at"] for item in payload["items"]}
        assert SIX_K_DATE.isoformat() in dates, "the 6-K fell outside the recency window"
        # The window still searches every subject chunk in it — the quote page too.
        assert RUN_DAY.isoformat() in dates

    async def test_the_recency_question_reads_the_6k_beside_a_quote_page(self) -> None:
        backend = InMemorySearchBackend()
        await backend.index([_quote_page(), *(_six_k(n) for n in range(3))])
        evidence, _u, steps, _s = await _acquire(_recent_disclosure(), backend)
        step = next(s for s in steps if s["rung"] == "recency_corpus")
        assert step["newest_held"] == SIX_K_DATE.isoformat()
        assert any(item.published_at == SIX_K_DATE for item in evidence)

    async def test_a_minor_newer_disclosure_still_reaches_back_to_the_6k(self) -> None:
        """Cellectis's 27-Oct data release is 46 days after the 6-K/A: one day outside
        the old 45-day window, inside the 75-day one. The 14-Sep pivot is inside both."""
        backend = InMemorySearchBackend()
        await backend.index([
            _quote_page(),
            _six_k(0),
            _doc_chunk("pivot", 0, _PIVOT_TEXT, PIVOT_DATE, tier="T5_api_aggregator",
                       source_class="unknown_web"),
            _doc_chunk("data", 0, "Data release: first patient dosed {n}.", DATA_RELEASE_DATE,
                       tier="T1_primary_company_source", source_class="company_press_release",
                       access_class="public_issuer"),
        ])
        payload = await _windowed_payload(backend)
        assert payload["recency_window"]["newest_held"] == DATA_RELEASE_DATE.isoformat()
        dates = {item["published_at"] for item in payload["items"]}
        assert {SIX_K_DATE.isoformat(), PIVOT_DATE.isoformat()} <= dates
        old = await _windowed_payload(backend, days=45)
        assert SIX_K_DATE.isoformat() not in {i["published_at"] for i in old["items"]}

    async def test_no_dated_disclosure_means_no_window_even_with_a_dated_quote_page(
        self,
    ) -> None:
        backend = InMemorySearchBackend()
        await backend.index([_quote_page(), dataclasses.replace(_six_k(0), published_at=None)])
        payload = await _windowed_payload(backend)
        assert payload["items"] == []
        assert payload["recency_window"]["newest_held"] is None
        assert payload["recency_window"]["stopped"] == "no_disclosure_anchor"
        assert "disclosure" in payload["summary"]

    async def test_newer_web_pages_do_not_crowd_the_anchoring_6k_out(self) -> None:
        """Review D2 (PR #316): two web pages newer than the 6-K/A, two chunks each,
        would fill RECENCY_MAX_ITEMS (4) on date order alone. Disclosures rank first."""
        backend = InMemorySearchBackend()
        await backend.index([
            _six_k(0), _six_k(1),
            *(_doc_chunk("web_a", n, _QUOTE_TEXT, date(2026, 10, 20),
                         tier="T5_api_aggregator", source_class="unknown_web")
              for n in range(2)),
            *(_doc_chunk("web_b", n, _QUOTE_TEXT, date(2026, 10, 25),
                         tier="T4_quality_media", source_class="major_financial_press")
              for n in range(2)),
        ])
        evidence, _u, steps, _s = await _acquire(_recent_disclosure(), backend)
        step = next(s for s in steps if s["rung"] == "recency_corpus")
        assert step["newest_held"] == SIX_K_DATE.isoformat()
        assert step["items"] == inv.RECENCY_MAX_ITEMS
        assert step["disclosure_items"] == 2
        assert [e.published_at for e in evidence[:2]] == [SIX_K_DATE, SIX_K_DATE]
        # The web pages still follow, newest first.
        assert evidence[2].published_at == date(2026, 10, 25)

    async def test_a_generic_issuer_web_page_does_not_anchor_but_a_release_does(self) -> None:
        backend = InMemorySearchBackend()
        await backend.index([
            _six_k(0),
            _doc_chunk("home", 0, "Welcome {n}", RUN_DAY, tier="T1_primary_company_source",
                       source_class="company_web_page"),
        ])
        filters = CorpusFilters(company_ids=(SUBJECT,), disclosures_only=True)
        assert await backend.newest_published_at(filters) == SIX_K_DATE
        await backend.index([
            _doc_chunk("pr", 0, "Press release {n}", PIVOT_DATE,
                       tier="T1_primary_company_source", source_class="company_press_release",
                       access_class="public_issuer"),
        ])
        assert await backend.newest_published_at(filters) == PIVOT_DATE

    def test_the_disclosure_vocabulary_is_the_platforms_own(self) -> None:
        from app.services.sources.taxonomy import VALID_TIER_CODES
        from app.services.web_research.classify import CLASS_TABLE, SOURCE_CLASSES

        assert set(DISCLOSURE_SOURCE_TIERS) <= VALID_TIER_CODES
        assert set(DISCLOSURE_SOURCE_CLASSES) <= SOURCE_CLASSES
        disclosing = {
            klass for klass, (tier, access) in CLASS_TABLE.items()
            if is_disclosure(CorpusChunk(chunk_id="x", text="", source_tier=tier,
                                         source_class=klass, access_class=access))
        }
        # Review D1: only what the issuer's own verified domain yields may anchor.
        assert disclosing == {"company_press_release", "investor_presentation"}

    @pytest.mark.parametrize(
        ("tier", "klass", "expected"),
        [
            ("T1_primary_filing", None, True),            # SEC / ASX connector filing
            ("T2_regulator_or_gov", None, True),          # NSM / Euronext venue disclosure
            ("T1_primary_company_source", None, True),    # company IR connector
            ("T4_quality_media", None, False),            # local-language press connector
            ("T5_api_aggregator", "unknown_web", False),  # the quote page
            ("T2_regulator_or_gov", "statistical_agency", False),
            ("T1_primary_filing", "exchange_announcement", False),  # review D1
            ("T1_primary_filing", "regulatory_filing", False),      # ADR-079
            ("T1_primary_filing", "issuer_filing", False),
            (None, None, False),
        ],
    )
    def test_is_disclosure(self, tier: str | None, klass: str | None, expected: bool) -> None:
        chunk = CorpusChunk(chunk_id="x", text="", source_tier=tier, source_class=klass)
        assert is_disclosure(chunk) is expected

    @pytest.mark.parametrize("url", _NOT_ANCHORS)
    def test_an_exchange_or_market_page_never_anchors(self, url: str) -> None:
        """Review D1 (PR #316): every page on an exchange host is classed
        ``exchange_announcement`` at T1 — quote pages and news articles included."""
        from app.services.web_research.classify import classify_source

        c = classify_source(url)
        chunk = CorpusChunk(chunk_id="x", text="", source_tier=c.tier,
                            source_class=c.source_class, access_class=c.access_class)
        assert not is_disclosure(chunk), (url, c.source_class, c.tier)

    def test_only_the_issuers_own_release_or_presentation_anchors(self) -> None:
        from app.services.web_research.classify import classify_source

        def anchors(url: str, domains: tuple[str, ...] = ()) -> bool:
            c = classify_source(url, issuer_domains=domains)
            return is_disclosure(CorpusChunk(
                chunk_id="x", text="", source_tier=c.tier, source_class=c.source_class,
                access_class=c.access_class))

        issuer = ("cellectis.com",)
        assert anchors("https://www.cellectis.com/en/press/cellectis-reports-h1-2026", issuer)
        assert anchors("https://www.cellectis.com/investors/presentation-2026.pdf", issuer)
        assert not anchors("https://www.cellectis.com/en/about-us/", issuer)  # web page
        # The same release on a PR wire is not the issuer's verified voice.
        assert not anchors("https://www.businesswire.com/news/home/2026091400001/en/")


async def _seed_quote_page_version(
    session: Any,
    company_id: uuid.UUID,
    *,
    tier: str = "T5_api_aggregator",
    source_class: str = "unknown_web",
) -> None:
    """A Boursorama-like quote page held for the subject, dated the run day."""
    from app.models.research_document import ResearchDocumentVersion

    version = ResearchDocumentVersion(
        id=uuid.uuid4(),
        research_document_id=uuid.uuid4(),
        content_hash=uuid.uuid4().hex * 2,
        canonical_url="https://www.boursorama.com/cours/1rPSAN/",
        transport="open_web",
        source_tier=tier,
        access_class="public_web",
        published_at=date.today(),
        retrieved_at=datetime.now(UTC),
        extraction_status="extracted",
        is_current=True,
        source_class=source_class,
    )
    session.add(version)
    await session.flush()
    session.add(_held_chunk(company_id, date.today(), source_tier=tier,
                            version_id=version.id))
    await session.flush()


class TestTheLoopAnchorIsADisclosure:
    async def test_a_quote_page_is_not_a_held_current_document(self, session) -> None:
        reasons = await _reasons(session, seed=_seed_quote_page_version)
        assert reasons["recent_disclosure"] == ledger.UNRESOLVED_NOT_ACQUIRED

    async def test_a_generic_issuer_web_page_is_not_one_either(self, session) -> None:
        # T1 by its class's tier; excluded by the class itself (the version subquery).
        async def seed(s: Any, company_id: uuid.UUID) -> None:
            await _seed_quote_page_version(
                s, company_id, tier="T1_primary_company_source", source_class="company_web_page"
            )

        reasons = await _reasons(session, seed=seed)
        assert reasons["recent_disclosure"] == ledger.UNRESOLVED_NOT_ACQUIRED

    async def test_newest_held_document_date_skips_the_quote_page(self, session) -> None:
        company_id = uuid.uuid4()
        await _seed_quote_page_version(session, company_id)
        session.add(_held_chunk(company_id, SIX_K_DATE))
        await session.flush()
        run = SimpleNamespace(company_id=company_id)
        assert await director_loop.newest_held_document_date(session, run) == SIX_K_DATE


# -- The same rule on a real, migrated PostgreSQL ---------------------------------------------

import os  # noqa: E402

PG_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not PG_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head"
)


@pytest.fixture
async def pg_session():  # noqa: ANN201
    """A session on the migrated database; everything it writes is rolled back."""
    engine = create_async_engine(PG_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        try:
            yield s
        finally:
            await s.rollback()
    await engine.dispose()


async def _pg_document(
    session: Any,
    company_id: uuid.UUID,
    *,
    key: str,
    published: date | None,
    tier: str,
    source_class: str | None,
    text: str,
    chunks: int = 2,
    url: str | None = None,
    access_class: str | None = None,
) -> list[ResearchDocumentChunk]:
    from app.models.research_derivation import ResearchDocumentDerivation
    from app.models.research_document import ResearchDocument, ResearchDocumentVersion

    now = datetime.now(UTC)
    document = ResearchDocument(
        id=uuid.uuid4(), company_id=company_id, document_key=f"{key}:{uuid.uuid4().hex[:8]}",
        document_type="web_page" if source_class else "interim_report",
        first_seen_at=now, last_seen_at=now,
    )
    session.add(document)
    await session.flush()
    version = ResearchDocumentVersion(
        id=uuid.uuid4(), research_document_id=document.id,
        content_hash=uuid.uuid4().hex * 2, canonical_url=url or f"https://example.test/{key}",
        transport="open_web" if source_class else "sec_edgar", source_tier=tier,
        access_class=access_class or ("public_web" if source_class else "public_official"),
        published_at=published, retrieved_at=now, extraction_status="extracted",
        is_current=True, source_class=source_class,
    )
    session.add(version)
    await session.flush()
    derivation = ResearchDocumentDerivation(
        id=uuid.uuid4(), research_document_version_id=version.id, pipeline_version=15,
        extraction_method="html", extraction_profile="live", status="complete",
        is_active=True, pages_persisted=1, paginated=False, char_count=100,
        section_count=1, table_count=0, truncated=False,
    )
    session.add(derivation)
    await session.flush()
    rows = []
    for n in range(chunks):
        body = text.format(n=n)
        row = ResearchDocumentChunk(
            id=uuid.uuid4(), chunk_id=f"{key}-{uuid.uuid4().hex[:12]}",
            derivation_id=derivation.id, research_document_version_id=version.id,
            company_id=company_id, kind="prose", ordinal=n, text=body, char_start=0,
            char_end=len(body), source_tier=tier, published_at=published,
            access_class=access_class or ("public_web" if source_class else "public_official"),
            indexable=True, indexed_at=now,
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


@requires_postgres
class TestTheAnchorOnPostgres:
    async def test_a_quote_page_dated_today_does_not_exclude_the_6k(self, pg_session) -> None:
        from app.models.company import Company
        from app.services.corpus.search.backends.postgres import PostgresSearchBackend

        company_id = uuid.uuid4()
        pg_session.add(Company(id=company_id, ticker=f"Q{uuid.uuid4().hex[:5]}",
                               exchange="NASDAQ", name="Cellectis Test", status="new"))
        await pg_session.flush()
        today = date.today()
        six_k_date = date.fromordinal(today.toordinal() - 52)
        await _pg_document(pg_session, company_id, key="quote", published=today,
                           tier="T5_api_aggregator", source_class="unknown_web",
                           text=_QUOTE_TEXT)
        await _pg_document(pg_session, company_id, key="home", published=today,
                           tier="T1_primary_company_source", source_class="company_web_page",
                           text="Welcome to our site {n}")
        await _pg_document(pg_session, company_id, key="six_k", published=six_k_date,
                           tier="T1_primary_filing", source_class=None, text=_SIX_K_TEXT)

        backend = PostgresSearchBackend(pg_session)
        assert await backend.newest_published_at(
            CorpusFilters(company_ids=(company_id,))
        ) == today, "precondition: the quote page IS the newest dated holding"
        assert await backend.newest_published_at(
            CorpusFilters(company_ids=(company_id,), disclosures_only=True)
        ) == six_k_date

        validated = validate_search_company_corpus(
            {"query": "results release cash flow", "company_ids": [str(company_id)],
             "mode": "lexical", "top_k": 12, "recent_window_days": inv.RECENCY_WINDOW_DAYS}
        )
        payload = await _search_company_corpus(
            SimpleNamespace(search_backend=backend, cfg=_CFG, session=pg_session), validated
        )
        assert payload["recency_window"]["newest_held"] == six_k_date.isoformat()
        assert six_k_date.isoformat() in {i["published_at"] for i in payload["items"]}

        run = SimpleNamespace(company_id=company_id)
        assert await director_loop.newest_held_document_date(pg_session, run) == six_k_date

    async def test_backends_agree_on_the_anchor(self, pg_session) -> None:
        """``_filter_clauses`` and ``CorpusFilters.matches`` decide the same chunks."""
        from app.models.company import Company
        from app.services.corpus.search.backends.postgres import _filter_clauses
        from app.services.web_research.classify import CLASS_TABLE, classify_source

        company_id = uuid.uuid4()
        pg_session.add(Company(id=company_id, ticker=f"P{uuid.uuid4().hex[:5]}",
                               exchange="NASDAQ", name="Parity", status="new"))
        await pg_session.flush()
        expected: dict[str, bool] = {}
        cases: list[tuple[str, str | None, str | None]] = [
            (tier, klass, access) for klass, (tier, access) in CLASS_TABLE.items()
        ]
        # The PR-wire press release, re-labelled public_web by classify_source.
        cases.append(("T1_primary_company_source", "company_press_release", "public_web"))
        cases += [("T1_primary_filing", None, "public_official"),
                  ("T2_regulator_or_gov", None, "public_official"),
                  ("T4_quality_media", None, "public_web")]
        # Review D1: the five exchange-host pages, as classify_source classes them.
        for url in _NOT_ANCHORS:
            c = classify_source(url)
            cases.append((c.tier, c.source_class, c.access_class))
        for n, (tier, klass, access) in enumerate(cases):
            rows = await _pg_document(pg_session, company_id, key=f"{n}-{tier}-{klass}",
                                      published=RUN_DAY, tier=tier, source_class=klass,
                                      text="x {n}", chunks=1, access_class=access)
            expected[rows[0].chunk_id] = is_disclosure(
                CorpusChunk(chunk_id="x", text="", source_tier=tier, source_class=klass,
                            access_class=access)
            )
        # 2 issuer-domain web classes + the T1 and T2 connector rows.
        assert sum(expected.values()) == 4
        admitted = set((await pg_session.execute(
            select(ResearchDocumentChunk.chunk_id).where(*_filter_clauses(
                CorpusFilters(company_ids=(company_id,), disclosures_only=True)
            ))
        )).scalars())
        assert admitted == {cid for cid, ok in expected.items() if ok}

    async def test_a_6k_ingested_undated_is_dated_by_the_bridge_and_read_in_the_window(
        self, pg_session
    ) -> None:
        """CLLS2-5 + CLLS3-3 on PostgreSQL: the 6-K/A held undated is dated with its SEC
        filing date when the bridge meets it, and the window then includes it beside a
        quote page dated today."""
        from app.models.company import Company
        from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence
        from app.services.corpus.search.backends.postgres import PostgresSearchBackend

        company_id = uuid.uuid4()
        pg_session.add(Company(id=company_id, ticker=f"C{uuid.uuid4().hex[:5]}",
                               exchange="NASDAQ", name="Cellectis Test", status="new"))
        await pg_session.flush()
        today = date.today()
        filed = date.fromordinal(today.toordinal() - 52)
        await _pg_document(pg_session, company_id, key="quote", published=today,
                           tier="T5_api_aggregator", source_class="unknown_web",
                           text=_QUOTE_TEXT)
        url = ("https://www.sec.gov/Archives/edgar/data/1627281/000119312526389336/"
               "clls-ex99_1.htm")
        await _pg_document(pg_session, company_id, key="six_k", published=None,
                           tier="T1_primary_filing", source_class=None, text=_SIX_K_TEXT,
                           url=url)
        backend = PostgresSearchBackend(pg_session)
        anchor = CorpusFilters(company_ids=(company_id,), disclosures_only=True)
        assert await backend.newest_published_at(anchor) is None, "precondition: undated"

        cfg = SimpleNamespace(v3_filing_body_bridge_enabled=True, v3_corpus_enabled=True)
        result = await ensure_filing_corpus_evidence(
            pg_session, company_id=company_id, cik="1627281",
            accession="0001193125-26-389336", form="6-K/A", cfg=cfg,
            document_name="clls-ex99_1.htm", filing_date=filed.isoformat(),
        )
        assert result.state == "ready" and result.reused is True
        assert await backend.newest_published_at(anchor) == filed

        validated = validate_search_company_corpus(
            {"query": "results release cash flow", "company_ids": [str(company_id)],
             "mode": "lexical", "top_k": 12, "recent_window_days": inv.RECENCY_WINDOW_DAYS}
        )
        payload = await _search_company_corpus(
            SimpleNamespace(search_backend=backend, cfg=_CFG, session=pg_session), validated
        )
        assert payload["recency_window"]["newest_held"] == filed.isoformat()
        assert filed.isoformat() in {i["published_at"] for i in payload["items"]}
