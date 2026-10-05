"""Open-web W7 — the web rung wired to the real follow-up, the Red Team's evidence,
escalation's improvement count and the pipeline (flag off = byte-identical).

Network: only ``fetch.open_web_fetch`` is replaced (``FakeNet`` serves fixture pages); the
search provider is the recorded-shape fake; extraction and ingestion are real.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.base import Base
from app.integrations.search.fake import FakeWebSearchProvider
from app.models.company import Company
from app.models.ledger import ResearchGap
from app.models.research_chunk import ResearchDocumentChunk
from app.models.web_research import WebSearchQuery
from app.services.agents.red_team import LLMRedTeam
from app.services.corpus.retrieval import evidence_id_for
from app.services.corpus.search.backends.memory import InMemorySearchBackend
from app.services.council_v2.inputs import FindingRef
from app.services.director import loop as lp
from app.services.director.planner import persist_plan
from app.services.escalation import evidence as ev
from app.services.ledger import store as ledger
from app.services.pipeline import v3_pipeline as v3
from app.services.web_research import fetch as fetch_mod
from app.services.web_research import followup as fu
from app.services.web_research import stage as st
from app.services.web_research import trust
from app.services.web_research.pool import ExtractionPool
from tests.test_web_w5_stage import FIXTURES, FakeNet, _cfg
from tests.test_web_w7_followup import (
    CAPACITY_QUERIES,
    CAPACITY_URL,
    MARGIN_URL,
    _company,
    _followup,
    _limits,
    _plan,
    _provider,
    _tavily,
)

TODAY = date(2026, 10, 5)


@pytest.fixture(scope="module")
def pool() -> Any:
    p = ExtractionPool(1)
    yield p
    p.shutdown()


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


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


# --------------------------------------------------------------------------- #
# A gap closed by a follow-up document, through the real loop and track B
# --------------------------------------------------------------------------- #


@dataclass
class ReadsTheCorpus:
    """Round 0 opens the gap; the follow-up round reads the document the web rung stored
    (as a specialist would, through the corpus) and states what it says."""

    session: Any
    gap_text: str
    statement: str
    source_kinds: tuple[str, ...] = ("issuer_ir",)
    contexts: list[Any] = field(default_factory=list)

    async def investigate(
        self, *, role_id, questions, round_index, remaining_tool_calls, question_context=None
    ):  # noqa: ANN001, ANN201
        key = questions[0].key
        out = lp.TaskOutcome(tool_calls=1)
        if round_index == 0:
            out.gaps.append(lp.GapDraft(
                gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE, description=self.gap_text,
                question_key=key))
            out.answered_question_keys = (key,)
            return out
        self.contexts.append(tuple((question_context or {})[key].followup_queries))
        chunk_id = (await self.session.execute(
            select(ResearchDocumentChunk.chunk_id).limit(1))).scalar_one()
        out.findings.append(lp.FindingDraft(
            statement=self.statement, evidence_ids=(evidence_id_for(chunk_id),),
            question_key=key, source_kinds=self.source_kinds))
        return out


async def _loop(session: Any, pool_: Any, provider: Any, investigator: Any, **kw: Any) -> Any:
    company = await _company(session)
    web = _followup(session, company, pool_, provider)
    run = await ledger.open_run(session, mode="standard", company_id=company.id)
    plan = _plan("q1")
    await persist_plan(session, run, plan)
    investigator_obj = investigator(session)
    result = await lp.run_investigation(
        session, run, plan, investigator=investigator_obj, limits=_limits(**kw),
        web_followup=web,
    )
    return result, run, web, investigator_obj


class TestAGapClosedByAFollowUpDocument:
    async def test_a_capacity_gap_is_answered_by_a_finding_on_the_web_document(
        self, session: Any, pool: Any
    ) -> None:
        result, run, web, inv = await _loop(
            session, pool, _provider(**CAPACITY_QUERIES),
            lambda s: ReadsTheCorpus(
                s, "Nameplate production capacity is not disclosed.",
                "Nameplate production capacity of 120,000 tpa is expected once the second "
                "line is commissioned."),
        )
        assert result.stopped_by == lp.STOPPED_ANSWERED
        assert result.stopped_by_a_limit is False
        assert web.stopped_by == lp.STOPPED_ANSWERED
        assert inv.contexts and inv.contexts[0][0] == "Voltgrid capacity expansion cost"
        record = result.web_followup
        assert record["followup_rounds"] == 1 and record["stopped_by"] == "answered"
        assert record["rounds"][0]["ingested"] == 1
        # The FINAL reconciliation (track B, persisted) agrees: the finding closes the gap.
        from app.services.pipeline import gap_reconciliation

        summary = await gap_reconciliation.reconcile_run(session, run, company_id=run.company_id)
        gap = (await session.execute(select(ResearchGap))).scalar_one()
        assert gap.status == "closed" and gap.reconciliation_status == "closed"
        assert summary["counts"].get("closed") == 1

    async def test_a_margin_gap_is_followed_up_but_never_claimed_closed(
        self, session: Any, pool: Any
    ) -> None:
        """Margin is not in track B's field vocabulary, so no finding can PROVE it closed
        (fail closed): the follow-up document is admitted and read, the gap stays open,
        and the run does not say ``answered``."""
        provider = _provider(**{
            "Voltgrid margin guidance": _tavily(
                "x", [("Voltgrid margin guidance for the coming year margin guidance",
                       MARGIN_URL, "Thu, 25 Sep 2026 08:00:00 GMT")]),
        })
        result, run, web, inv = await _loop(
            session, pool, provider,
            lambda s: ReadsTheCorpus(
                s, "Gross margin trend is not disclosed.",
                "Voltgrid guided to a gross margin outlook of 24 percent for the coming year.",
                source_kinds=("search_company_corpus",)),
        )
        assert web.rounds and web.rounds[0].ingested == 1 and inv.contexts
        assert result.stopped_by != lp.STOPPED_ANSWERED
        gap = (await session.execute(select(ResearchGap))).scalar_one()
        assert gap.status == "open", "a real gap is never hidden"
        from app.services.pipeline import gap_reconciliation

        await gap_reconciliation.reconcile_run(session, run, company_id=run.company_id)
        assert gap.status != "closed"

    async def test_a_third_party_document_only_partly_closes_a_gap(
        self, session: Any, pool: Any
    ) -> None:
        result, run, _web, _inv = await _loop(
            session, pool, _provider(**CAPACITY_QUERIES),
            lambda s: ReadsTheCorpus(
                s, "Nameplate production capacity is not disclosed.",
                "Nameplate production capacity of 120,000 tpa is expected.",
                source_kinds=("search_company_corpus",)),
        )
        assert result.stopped_by != lp.STOPPED_ANSWERED
        from app.services.pipeline import gap_reconciliation

        await gap_reconciliation.reconcile_run(session, run, company_id=run.company_id)
        gap = (await session.execute(select(ResearchGap))).scalar_one()
        assert gap.reconciliation_status == "partially_closed" and gap.status == "open"


# --------------------------------------------------------------------------- #
# The Red Team's search wave and its evidence
# --------------------------------------------------------------------------- #


def _risk_provider() -> FakeWebSearchProvider:
    return FakeWebSearchProvider.from_fixture_dir(FIXTURES)


class TestTheChallengeWave:
    async def test_the_risk_family_always_runs_and_feeds_the_red_team(
        self, session: Any, pool: Any
    ) -> None:
        company = await _company(session)
        provider = _risk_provider()
        followup = _followup(session, company, pool, provider)
        wave = await followup.challenge_wave()
        assert wave.kind == "challenge" and wave.executed >= 1
        assert await followup.challenge_wave() is wave, "once"
        rows = (await session.execute(select(WebSearchQuery))).scalars().all()
        assert rows and {r.family for r in rows} == {"risk"}
        assert any(r.query_text == "Voltgrid delay" for r in rows)
        items = await followup.risk_evidence()
        assert items, "the RISK document reached the Red Team's input"
        item = items[0]
        assert item.evidence_id.startswith("ev:")
        assert item.source_class == "trade_publication"
        assert item.published_at is not None and item.origin
        prompt = item.to_prompt()
        assert {"source_class", "origin", "published_at", "independent_origin", "text"} <= set(prompt)
        assert "http" not in json.dumps(prompt)

    async def test_the_wave_runs_whatever_the_thesis(self, session: Any, pool: Any) -> None:
        company = await _company(session)
        followup = _followup(
            session, company, pool, _provider(),
            themes=["copper"], development_stage=True,
        )
        wave = await followup.challenge_wave()
        keys = set(wave.query_keys)
        assert wave.executed == fu.CHALLENGE_QUERIES
        assert "risk.permit_problem" in keys, "project risks first for a development issuer"
        assert keys & {"risk.technology_disadvantages", "risk.industry_oversupply",
                       "risk.commodity_substitute"}, "one counter-thesis query"


class _Client:
    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply
        self.prompts: list[tuple[str, str]] = []

    async def complete_json(self, system: str, user: str, **_kw: Any) -> dict[str, Any]:
        self.prompts.append((system, user))
        return self.reply


def _finding() -> FindingRef:
    return FindingRef(
        finding_id=uuid.uuid4(), statement="Voltgrid will commission its Texas line in 2027.",
        mechanism=None, direction=None, confidence=0.7, evidence_ids=("ev:abc",),
        calculation_ids=(), period_key=None, scope_key=None, originating_role="analyst",
        verification_status="unverified",
    )


def _risk(evidence_id: str, source_class: str, origin: str) -> fu.RiskEvidence:
    item = trust.SupportItem(evidence_id, source_class, origin, date(2026, 8, 20), True)
    return fu.RiskEvidence(
        evidence_id, "The project was delayed by two quarters.", source_class,
        trust.origin_display(origin), date(2026, 8, 20),
        trust.bears_independence(item, uuid.uuid4()), item,
    )


class TestTheRedTeamReceivesRiskEvidence:
    async def test_the_prompt_carries_labelled_evidence_and_citable_ids(self) -> None:
        finding = _finding()
        evidence = [_risk("ev:r1", "trade_publication", "utilitydive.com")]
        client = _Client({"challenges": [{
            "finding_id": str(finding.finding_id), "weakness_class": "stale_evidence",
            "text": "Press reports a two-quarter delay.", "risk_evidence_ids": ["ev:r1"]}]})
        red = LLMRedTeam(client=client, risk_evidence=evidence, issuer_key=uuid.uuid4())
        out = await red.select(findings=[finding], max_challenges=3)
        system, user = client.prompts[0]
        assert "RISK EVIDENCE" in user and '"source_class": "trade_publication"' in user
        assert '"published_at": "2026-08-20"' in user and "independent_origin" in user
        assert "risk_evidence_ids" in system
        assert len(out) == 1 and out[0].basis_evidence_ids == ("ev:r1",)
        assert out[0].text.startswith("[single source]"), "one reliable source is labelled"

    async def test_a_single_low_trust_source_cannot_carry_a_challenge(self) -> None:
        finding = _finding()
        evidence = [_risk("ev:r2", "aggregator", "stockchatter.example")]
        client = _Client({"challenges": [{
            "finding_id": str(finding.finding_id), "weakness_class": "contradicted_by_evidence",
            "text": "A forum says it failed.", "risk_evidence_ids": ["ev:r2"]}]})
        red = LLMRedTeam(client=client, risk_evidence=evidence, issuer_key=uuid.uuid4())
        assert await red.select(findings=[finding], max_challenges=3) == []
        assert red.discarded_low_trust_basis == [fu.REASON_LOW_TRUST]

    async def test_two_independent_sources_do_carry_one(self) -> None:
        finding = _finding()
        evidence = [_risk("ev:r3", "major_financial_press", "reuters.com"),
                    _risk("ev:r4", "trade_publication", "utilitydive.com")]
        client = _Client({"challenges": [{
            "finding_id": str(finding.finding_id), "weakness_class": "contradicted_by_evidence",
            "text": "Two outlets report the delay.", "risk_evidence_ids": ["ev:r3", "ev:r4"]}]})
        red = LLMRedTeam(client=client, risk_evidence=evidence, issuer_key=uuid.uuid4())
        out = await red.select(findings=[finding], max_challenges=3)
        assert len(out) == 1 and out[0].basis_evidence_ids == ("ev:r3", "ev:r4")
        assert not out[0].text.startswith("[")

    async def test_an_id_the_red_team_was_never_given_is_not_a_basis(self) -> None:
        finding = _finding()
        client = _Client({"challenges": [{
            "finding_id": str(finding.finding_id), "weakness_class": "stale_evidence",
            "text": "Stale.", "risk_evidence_ids": ["ev:made-up"]}]})
        red = LLMRedTeam(client=client, risk_evidence=[_risk("ev:r1", "aggregator", "x.example")])
        out = await red.select(findings=[finding], max_challenges=3)
        assert len(out) == 1 and out[0].basis_evidence_ids == ()

    async def test_without_risk_evidence_the_prompt_is_what_it_was(self) -> None:
        finding = _finding()
        client = _Client({"challenges": []})
        await LLMRedTeam(client=client).select(findings=[finding], max_challenges=3)
        system, user = client.prompts[0]
        assert "RISK EVIDENCE" not in user and "risk_evidence_ids" not in system
        assert user.startswith("FINDINGS UNDER REVIEW:\n") and user.count("\n\n") == 0


# --------------------------------------------------------------------------- #
# Escalation
# --------------------------------------------------------------------------- #


class TestEscalationCountsWebEvidence:
    def test_a_verified_external_source_is_improvement(self) -> None:
        before = ev.EvidenceSnapshot(indexed_chunks=40)
        after = ev.EvidenceSnapshot(indexed_chunks=40, verified_leads=2)
        delta = ev.measure_evidence_delta(before, after)
        assert delta["verified_leads_added"] == 2 and delta["improved"] is True
        assert any("ev:x:" in r for r in delta["reasons"])
        assert "verified_leads_added" in ev.DECISIVE_DIMENSIONS

    def test_no_movement_is_still_not_improvement(self) -> None:
        delta = ev.measure_evidence_delta(ev.EvidenceSnapshot(), ev.EvidenceSnapshot())
        assert delta["improved"] is False and delta["verified_leads_added"] == 0

    def test_an_old_snapshot_without_the_key_reads_as_zero(self) -> None:
        assert ev.EvidenceSnapshot.from_dict({"indexed_chunks": 3}).verified_leads == 0
        assert ev.EvidenceSnapshot.persisted({}) is None

    async def test_new_web_chunks_count_as_improvement_by_the_existing_dimensions(
        self, session: Any, pool: Any
    ) -> None:
        company = await _company(session)
        backend = InMemorySearchBackend()
        before = await ev.snapshot_evidence(session, company.id)
        followup = _followup(
            session, company, pool, _provider(**CAPACITY_QUERIES), search_backend=backend
        )
        record = await followup.run_round(
            fu.select_gaps([SimpleNamespace(
                id="g1", gap_type="evidence_unavailable", status="open", closable=True,
                description="Nameplate production capacity is missing.", question_key="q1")])[0],
            round_index=0,
        )
        assert record.ingested == 1
        # ``indexed_at`` is the PostgreSQL backend's index state (the memory backend keeps
        # its index in process); stamp it as that backend does. The PostgreSQL test
        # proves the same on the real backend.
        from datetime import datetime, timezone

        for chunk in (await session.execute(select(ResearchDocumentChunk))).scalars():
            chunk.indexed_at = datetime.now(timezone.utc)
        await session.flush()
        after = await ev.snapshot_evidence(session, company.id)
        delta = ev.measure_evidence_delta(before, after)
        assert delta["indexed_chunks_added"] >= 1 and delta["searchable_documents_added"] == 1
        assert delta["improved"] is True

    async def test_a_verified_lead_row_is_counted(self, session: Any) -> None:
        from app.models.research_lead import ResearchLeadRecord

        company = await _company(session)
        assert (await ev.snapshot_evidence(session, company.id)).verified_leads == 0
        for status, evidence_id, digest in (
            ("verified", "ev:x:aaa", "h" * 64), ("verified", None, "i" * 64),
            ("pending", None, None),
        ):
            session.add(ResearchLeadRecord(
                id=uuid.uuid4(), company_id=company.id, provider="p", lead_key=uuid.uuid4().hex,
                slot_key=uuid.uuid4().hex, claim_text="c", status=status,
                fetched_content_hash=digest, promoted_evidence_id=evidence_id,
            ))
        await session.flush()
        assert (await ev.snapshot_evidence(session, company.id)).verified_leads == 1


# --------------------------------------------------------------------------- #
# The pipeline: flag off is byte-identical; flag on records the rounds
# --------------------------------------------------------------------------- #


class _Spies:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, net: FakeNet,
                 provider: FakeWebSearchProvider) -> None:
        self.provider = provider
        monkeypatch.setattr(fetch_mod, "open_web_fetch", net)
        monkeypatch.setattr(
            "app.integrations.search.web_search_provider_from_settings",
            lambda cfg=None: self.provider,
        )
        self.created: list[Any] = []
        real = fu.WebFollowup.create

        def spy(*a: Any, **k: Any) -> Any:
            built = real(*a, **k)
            self.created.append(built)
            return built

        monkeypatch.setattr(fu.WebFollowup, "create", staticmethod(spy))


def _pipeline_cfg(**over: Any) -> Any:
    values: dict[str, Any] = {
        "v3_pipeline_enabled": True,
        "v3_agent_tools_enabled": True,
        "azure_openai_api_key": "",
        "azure_openai_endpoint": "",
        "deepseek_api_key": "",
        "v3_web_search_enabled": True,
        "v3_web_search_provider": "fake",
        "v3_web_fetch_enabled": True,
        "v3_corpus_enabled": True,
        "v3_web_corpus_ingest_enabled": True,
        "v3_company_web_research_enabled": True,
    }
    values.update(over)
    return _cfg(**values)


class TestThePipeline:
    async def _company(self, session: Any) -> Company:
        company = Company(
            id=uuid.uuid4(), ticker="VGRD", exchange="NASDAQ", name="Voltgrid Corp",
            status="new", sector="Industrials", industry="Electrical equipment",
        )
        session.add(company)
        await session.flush()
        return company

    async def test_flag_off_builds_nothing_and_adds_no_key(
        self, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spies = _Spies(monkeypatch, FakeNet(), FakeWebSearchProvider.from_fixture_dir(FIXTURES))
        company = await self._company(session)
        outcome = await v3.run_v3_research(session, company, cfg=_pipeline_cfg())
        assert outcome.error is None
        assert spies.created == []
        assert "followup_rounds" not in outcome.web_context
        assert "followup" not in outcome.web_context
        assert "web_followup" not in outcome.loop
        assert "risk_evidence_items" not in outcome.challenges
        assert not any(r.stage == "followup" for r in
                       (await session.execute(select(WebSearchQuery))).scalars().all())

    async def test_flag_on_without_the_web_path_builds_nothing(
        self, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spies = _Spies(monkeypatch, FakeNet(), FakeWebSearchProvider.from_fixture_dir(FIXTURES))
        company = await self._company(session)
        outcome = await v3.run_v3_research(
            session, company,
            cfg=_pipeline_cfg(v3_web_followup_enabled=True, v3_company_web_research_enabled=False),
        )
        assert outcome.error is None and spies.created == []
        assert outcome.web_context == {}

    async def test_flag_on_runs_the_challenge_wave_and_records_the_rounds(
        self, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        net = FakeNet()
        spies = _Spies(monkeypatch, net, FakeWebSearchProvider.from_fixture_dir(FIXTURES))
        company = await self._company(session)
        outcome = await v3.run_v3_research(
            session, company, cfg=_pipeline_cfg(v3_web_followup_enabled=True), mode="standard"
        )
        assert outcome.error is None and len(spies.created) == 1
        context = outcome.web_context
        assert isinstance(context["followup_rounds"], int)
        follow = context["followup"]
        assert follow["challenge"]["kind"] == "challenge"
        assert follow["queries"] and follow["template_version"] == fu.FOLLOWUP_TEMPLATE_VERSION
        assert outcome.loop["web_followup"]["followup_rounds"] == context["followup_rounds"]
        assert outcome.loop["web_followup"]["stopped_by"] == outcome.loop["stopped_by"]
        assert "risk_evidence_items" in outcome.challenges
        assert "web_stage" in outcome.consumption
        rows = (await session.execute(select(WebSearchQuery))).scalars().all()
        assert any(r.stage == "followup" and r.family == "risk" for r in rows)
        assert "stopped_by" in follow and "http" not in json.dumps(follow)


def test_stage_summary_is_unchanged_without_the_flag() -> None:
    assert st.SUMMARY_VERSION == 1


# --------------------------------------------------------------------------- #
# The Investigator's deterministic candidate step
# --------------------------------------------------------------------------- #


class _FakeToolSession:
    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, tool: str, arguments: dict[str, Any], task_ref: str = "") -> Any:
        self.calls.append((tool, arguments))
        payload = self.replies[tool]
        return SimpleNamespace(ok=True, payload=payload, outcome="ok", refusal_reason=None,
                               contains_untrusted_content=True)


def _investigator(session: Any, fetcher: Any, budget: Any = None) -> Any:
    from app.services.agents.investigator import ExternalSearchBudget, LLMInvestigator

    return LLMInvestigator(
        session=session, company_id=uuid.uuid4(), ticker="VGRD", exchange="NASDAQ",
        company_name="Voltgrid", external_budget=budget or ExternalSearchBudget(limit=4),
        candidate_fetcher=fetcher,
    )


_ROLE = SimpleNamespace(can_acquire_with=lambda tool: True)
_QUESTION = SimpleNamespace(
    key="q1", text="What is the nameplate capacity?", search_intents=("{company} capacity",),
    evidence_contract=SimpleNamespace(allow_external=True, max_external_searches=2),
)
_CURRENT = SimpleNamespace(missing=["independent"])
_CANDIDATES = [
    {"rank": 1, "url": CAPACITY_URL, "domain": "www.power-technology.com", "title": "t"},
    {"rank": 2, "url": "https://x.example/2", "domain": "x.example", "title": "t"},
]


class TestTheInvestigatorFetchesCandidatesDeterministically:
    async def test_candidates_are_fetched_by_the_platform_and_read_back_as_corpus_evidence(
        self,
    ) -> None:
        fetched_for: list[dict[str, Any]] = []

        async def fetcher(**kw: Any) -> fu.CandidateFetchResult:
            fetched_for.append(kw)
            return fu.CandidateFetchResult(
                fetch_calls=1, ingested=1, canonical_urls=(CAPACITY_URL,)
            )

        hit = {
            "evidence_id": "ev:c:cap", "text": "Nameplate production capacity of 120,000 tpa.",
            "canonical_url": CAPACITY_URL, "source_class": "trade_publication",
            "origin_key": "power-technology.com", "published_at": "2026-09-18",
            "source_tier": "T4_quality_media",
        }
        other = {**hit, "evidence_id": "ev:c:other", "canonical_url": "https://other.example/x"}
        tools = _FakeToolSession({
            "search_web": {"leads": [], "candidates": _CANDIDATES, "provider": "fake"},
            "search_company_corpus": {"items": [hit, other]},
        })
        inv = _investigator(tools, fetcher)
        step, evidence, used = await inv._external_rung(  # noqa: SLF001
            "external_research_analyst", _ROLE, _QUESTION, 20, _context(), 1, _CURRENT
        )
        # The platform named what to fetch; the model named nothing.
        assert fetched_for == [{
            "question_key": "q1", "candidates": _CANDIDATES, "round_index": 1,
            "max_fetches": 4,
        }]
        assert [e.citation_id for e in evidence] == ["ev:c:cap"], "only the stored document"
        assert step["candidate_fetch"]["ingested"] == 1
        assert step["candidate_fetch"]["evidence_items"] == 1
        assert used == 3, "the search, the fetch and the corpus read are all counted"
        assert all(not c[1].get("url") for c in tools.calls), "no model-chosen URL anywhere"

    async def test_without_a_fetcher_candidates_are_only_listed(self) -> None:
        tools = _FakeToolSession({
            "search_web": {"leads": [], "candidates": _CANDIDATES, "provider": "fake"},
        })
        inv = _investigator(tools, None)
        step, evidence, used = await inv._external_rung(  # noqa: SLF001
            "external_research_analyst", _ROLE, _QUESTION, 20, _context(), 1, _CURRENT
        )
        assert evidence == [] and used == 1 and "candidate_fetch" not in step
        assert [c[0] for c in tools.calls] == ["search_web"]

    async def test_a_failing_fetcher_costs_the_step_not_the_run(self) -> None:
        async def fetcher(**kw: Any) -> Any:
            raise RuntimeError("boom")

        tools = _FakeToolSession({
            "search_web": {"leads": [], "candidates": _CANDIDATES, "provider": "fake"},
        })
        step, evidence, used = await _investigator(tools, fetcher)._external_rung(  # noqa: SLF001
            "external_research_analyst", _ROLE, _QUESTION, 20, _context(), 1, _CURRENT
        )
        assert evidence == [] and used == 1

    async def test_the_follow_up_rung_reads_the_corpus_by_the_platform_built_query(self) -> None:
        from app.services.agents.investigator import QuestionContext

        hit = {"evidence_id": "ev:c:cap", "text": "capacity", "canonical_url": CAPACITY_URL,
               "source_class": "trade_publication", "origin_key": "power-technology.com",
               "published_at": "2026-09-18"}
        tools = _FakeToolSession({"search_company_corpus": {"items": [hit]}})
        inv = _investigator(tools, None)
        role = SimpleNamespace(
            can_use=lambda tool: True, can_acquire_with=lambda tool: False, tools=set(),
        )
        evidence, used, steps = await inv._acquire(  # noqa: SLF001
            "financial_analyst", role, SimpleNamespace(
                key="q1", text="t", search_intents=(), evidence_contract=None),
            10, context=QuestionContext(
                rounds_attempted=1, followup_queries=("Voltgrid capacity expansion cost",)),
            round_index=1,
        )
        rung = next(s for s in steps if s["rung"] == "web_followup_corpus")
        assert len(rung["queries"]) == 1 and "capacity" in rung["queries"][0].lower()
        assert tools.calls[0][1]["exclude_suspect"] is True
        assert [e.citation_id for e in evidence] == ["ev:c:cap"] and used == 1


def _context() -> Any:
    from app.services.agents.investigator import QuestionContext

    return QuestionContext()
