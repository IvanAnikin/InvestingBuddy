"""Open-web W5 — the stage inside the V3 pipeline, and web evidence in the report.

The pipeline runs for real on SQLite with no model configured (it degrades honestly, as in
production without a key); only the network is faked, at the one seam the stage reads —
``fetch.open_web_fetch`` — and the search provider is the recorded-shape fake. What is
asserted here is wiring: order, isolation, byte-identical behaviour with the flag off, the
consumption record and the report sections.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import Settings
from app.db.base import Base
from app.integrations.search.fake import MODE_OUTAGE, FakeWebSearchProvider
from app.models.company import Company
from app.models.research_chunk import ResearchDocumentChunk
from app.models.research_document import ResearchDocumentVersion
from app.models.web_research import WebSearchQuery
from app.services.corpus.retrieval import evidence_id_for
from app.services.corpus.search.backends.memory import InMemorySearchBackend
from app.services.ledger import store as ledger
from app.services.pipeline import professional_research as pr
from app.services.pipeline import v3_pipeline as v3
from app.services.web_research import fetch as fetch_mod
from app.services.web_research import stage as st
from app.services.web_research import trust
from tests.test_web_w5_stage import FIXTURES, PAGES, FakeNet

TODAY = date(2026, 10, 4)


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


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


def _cfg(**over: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "v3_pipeline_enabled": True,
        "v3_agent_tools_enabled": True,
        "azure_openai_api_key": "",
        "azure_openai_endpoint": "",
        "deepseek_api_key": "",
        "v3_run_max_web_searches": 0,
        "v3_web_search_max_queries_per_day": 300,
        "v3_artifact_store_backend": "none",
    }
    values.update(over)
    return Settings(**values)


def _web_cfg(**over: Any) -> Settings:
    return _cfg(
        v3_company_web_research_enabled=True,
        v3_web_search_enabled=True,
        v3_web_search_provider="fake",
        v3_web_fetch_enabled=True,
        v3_corpus_enabled=True,
        v3_web_corpus_ingest_enabled=True,
        **over,
    )


async def _company(session: Any) -> Company:
    company = Company(
        id=uuid.uuid4(), ticker="VGRD", exchange="NASDAQ", name="Voltgrid Corp",
        status="new", sector="Industrials", industry="Electrical equipment",
    )
    session.add(company)
    await session.flush()
    return company


class Spies:
    """Records the order of the pipeline steps the stage sits between."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, net: FakeNet) -> None:
        self.order: list[str] = []
        self.net = net
        from app.services.corpus import indexing
        from app.services.sources.disclosures import acquisition

        real_disclosures = acquisition.ensure_core_disclosures
        real_index = indexing.ensure_company_indexed
        real_stage = st.ensure_web_context

        async def disclosures(*a: Any, **k: Any) -> Any:
            self.order.append("core_disclosures")
            return await real_disclosures(*a, **k)

        async def index(*a: Any, **k: Any) -> Any:
            self.order.append("indexing")
            return await real_index(*a, **k)

        async def stage(*a: Any, **k: Any) -> Any:
            self.order.append("web_stage")
            return await real_stage(*a, **k)

        monkeypatch.setattr(acquisition, "ensure_core_disclosures", disclosures)
        monkeypatch.setattr(indexing, "ensure_company_indexed", index)
        monkeypatch.setattr(st, "ensure_web_context", stage)
        monkeypatch.setattr(fetch_mod, "open_web_fetch", net)
        self.provider = FakeWebSearchProvider.from_fixture_dir(FIXTURES)
        monkeypatch.setattr(
            "app.integrations.search.web_search_provider_from_settings",
            lambda cfg=None: self.provider,
        )


# --------------------------------------------------------------------------- #
# Flag off: nothing changes
# --------------------------------------------------------------------------- #


class TestFlagOffIsByteIdentical:
    def test_the_outcome_serialises_without_a_web_context_key(self) -> None:
        payload = v3.V3ResearchOutcome().to_dict()
        assert "web_context" not in payload
        with_web = v3.V3ResearchOutcome(web_context={"state": "ok"}).to_dict()
        assert with_web["web_context"] == {"state": "ok"}
        assert set(with_web) - set(payload) == {"web_context"}

    async def test_no_search_no_fetch_no_row_no_key(
        self, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        net = FakeNet()
        spies = Spies(monkeypatch, net)
        company = await _company(session)
        outcome = await v3.run_v3_research(
            session, company, cfg=_cfg(v3_web_search_enabled=True, v3_web_search_provider="fake",
                                       v3_web_fetch_enabled=True)
        )
        assert outcome.error is None
        assert "web_stage" not in spies.order
        assert spies.provider.requests == [] and net.requested == []
        assert outcome.web_context == {}
        assert "web_context" not in outcome.to_dict()
        assert "web_stage" not in outcome.consumption
        assert "tavily_credits" not in outcome.consumption
        assert (await session.execute(select(func.count()).select_from(WebSearchQuery))
                ).scalar_one() == 0

    async def test_the_classification_block_moved_without_changing_the_outcome(
        self, session: Any
    ) -> None:
        """Classification, subject profile and stage detection now run before indexing so
        the stage can plan from them; they read the corpus rows, not the index."""
        company = await _company(session)
        outcome = await v3.run_v3_research(session, company, cfg=_cfg())
        assert outcome.classification["industry"].lower() == "electrical equipment"
        assert outcome.subject_profile["chunks_read"] == 0
        assert outcome.error is None

    async def test_search_enabled_alone_does_not_start_the_stage(
        self, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        net = FakeNet()
        spies = Spies(monkeypatch, net)
        company = await _company(session)
        await v3.run_v3_research(session, company, cfg=_cfg(
            v3_web_search_enabled=True, v3_web_search_provider="fake"))
        assert spies.order.count("web_stage") == 0


# --------------------------------------------------------------------------- #
# Flag on: where the stage sits and what it records
# --------------------------------------------------------------------------- #


class TestTheStageInThePipeline:
    async def test_it_runs_after_the_disclosures_and_before_indexing(
        self, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spies = Spies(monkeypatch, FakeNet())
        company = await _company(session)
        outcome = await v3.run_v3_research(
            session, company, cfg=_web_cfg(), search_backend=InMemorySearchBackend()
        )
        assert spies.order == ["core_disclosures", "web_stage", "indexing"]
        assert outcome.error is None

    async def test_the_summary_is_recorded_on_the_outcome_and_the_report_payload(
        self, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Spies(monkeypatch, FakeNet())
        company = await _company(session)
        outcome = await v3.run_v3_research(
            session, company, cfg=_web_cfg(), search_backend=InMemorySearchBackend()
        )
        web = outcome.web_context
        assert web["state"] == "ok" and web["mode"] == "standard"
        assert web["queries"]["executed"] > 0 and web["ingest"]["ingested"] >= 4
        assert web["source_classes"]
        assert outcome.to_dict()["web_context"]["state"] == "ok"
        assert json.dumps(outcome.to_dict(), default=str)  # serialisable

    async def test_web_documents_are_indexed_and_reachable_through_the_corpus(
        self, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Spies(monkeypatch, FakeNet())
        backend = InMemorySearchBackend()
        company = await _company(session)
        outcome = await v3.run_v3_research(
            session, company, cfg=_web_cfg(), search_backend=backend
        )
        assert outcome.web_context["ingest"]["ingested"] >= 4
        from app.services.corpus.search.types import CorpusFilters, CorpusQuery, SearchMode

        hits = await backend.search(
            CorpusQuery(
                text="transformer order utility framework", mode=SearchMode.LEXICAL,
                filters=CorpusFilters(company_ids=(company.id,)), top_k=5,
            )
        )
        assert hits, "the web chunks are in the same index the Investigator searches"

    async def test_the_run_consumption_includes_the_stage_units(
        self, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Spies(monkeypatch, FakeNet())
        company = await _company(session)
        outcome = await v3.run_v3_research(
            session, company, cfg=_web_cfg(), search_backend=InMemorySearchBackend()
        )
        web = outcome.consumption["web_stage"]
        model = outcome.consumption["model"]
        assert web["web_search_calls"] == outcome.web_context["queries"]["executed"] > 0
        assert model["web_search_calls"] == web["web_search_calls"], "inside the cost basis"
        assert model["url_fetch_calls"] == web["url_fetch_calls"] > 0
        assert model["bytes_downloaded"] == web["bytes_downloaded"] > 0
        assert "web_search_calls" in model["instrumented"]
        # Cost unknown stays unknown: no price book is configured.
        assert outcome.consumption["estimated_cost_usd"] is None

    async def test_an_outage_degrades_the_run_with_a_label_and_it_still_completes(
        self, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spies = Spies(monkeypatch, FakeNet())
        spies.provider.mode = MODE_OUTAGE
        company = await _company(session)
        outcome = await v3.run_v3_research(
            session, company, cfg=_web_cfg(), search_backend=InMemorySearchBackend()
        )
        assert outcome.error is None and outcome.ran, "official-source research completes"
        assert outcome.web_context["state"] == "web_search_unavailable"
        assert any("Live web search unavailable" in line for line in outcome.degraded)
        rows = (await session.execute(select(WebSearchQuery))).scalars().all()
        assert rows and not any(r.executed for r in rows), "and no row is labelled a search"
        assert (await session.execute(
            select(func.count()).select_from(ResearchDocumentVersion))).scalar_one() == 0

    async def test_an_exception_in_the_stage_does_not_fail_the_job(
        self, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Spies(monkeypatch, FakeNet())

        def boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("planner exploded")

        monkeypatch.setattr(st, "build_plan", boom)
        company = await _company(session)
        outcome = await v3.run_v3_research(
            session, company, cfg=_web_cfg(), search_backend=InMemorySearchBackend()
        )
        assert outcome.error is None and outcome.ran
        assert outcome.web_context["state"] == "web_stage_failed"
        assert any("Web research could not be completed" in line for line in outcome.degraded)

    async def test_the_investigator_search_budget_is_what_the_stage_left(
        self, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Spies(monkeypatch, FakeNet())
        company = await _company(session)
        outcome = await v3.run_v3_research(
            session, company, cfg=_web_cfg(), search_backend=InMemorySearchBackend()
        )
        executed = outcome.web_context["queries"]["executed"]
        limit = outcome.loop["external_searches"]["limit"]
        assert limit == max(0, 16 - executed), "one ceiling for the stage and the rung"


# --------------------------------------------------------------------------- #
# Report sections (spec §17.4)
# --------------------------------------------------------------------------- #


def _fv(fid: str, statement: str, domain: str, evidence: tuple[str, ...]) -> pr.FindingView:
    return pr.FindingView(finding_id=fid, statement=statement, domain=domain,
                          question_key=None, evidence_ids=evidence)


def _support(eid: str, klass: str, origin: str, published: str | None = None) -> dict[str, Any]:
    return trust.SupportItem(
        evidence_id=eid, source_class=klass, origin_key=origin, web=True,
        published_at=date.fromisoformat(published) if published else None,
    ).to_dict()


class TestAssembleMapsWebEvidenceBySourceClass:
    def _report(self, **kw: Any) -> dict[str, Any]:
        findings = [
            _fv("f1", "Voltgrid won a framework order from a regional utility.", "catalysts",
                ("ev:c:cat",)),
            _fv("f2", "Large transformer lead times reached 120 to 210 weeks.",
                "industry_economics", ("ev:c:ind", "ev:c:ind2")),
            _fv("f3", "The Texas line commissioning was postponed by two quarters.", "risks",
                ("ev:c:risk",)),
            _fv("f4", "Peers include Hitachi Energy and Siemens Energy.",
                "competitive_position", ("ev:c:comp",)),
            _fv("f5", "Revenue was reported by the filing.", "financial_capacity", ("fact-1",)),
        ]
        support = {
            "ev:c:cat": _support("ev:c:cat", "trade_publication", "tdworld.com", "2026-09-12"),
            "ev:c:ind": _support("ev:c:ind", "industry_association", "nema.org", "2026-06-30"),
            "ev:c:ind2": _support("ev:c:ind2", "trade_publication", "tdworld.com"),
            "ev:c:risk": _support("ev:c:risk", "trade_publication", "utilitydive.com"),
            "ev:c:comp": _support("ev:c:comp", "trade_publication", "power-technology.com"),
        }
        inputs = pr.ReportInputs(
            subject={"ticker": "VGRD"}, questions=[], findings=findings,
            web_support=support, **kw,
        )
        return pr.assemble(inputs)

    @staticmethod
    def _section(report: dict[str, Any], key: str) -> dict[str, Any]:
        return next(s for s in report["sections"] if s["key"] == key)

    def test_catalysts_cite_a_web_chunk_with_its_class_and_corroboration(self) -> None:
        block = self._section(self._report(), "growth_and_catalysts")["web_evidence"]
        item = block["items"][0]
        assert item["sources"][0]["evidence_id"] == "ev:c:cat"
        assert item["sources"][0]["source_class"] == "trade_publication"
        assert item["sources"][0]["source_class_label"] == "Trade publication"
        assert item["corroboration"] == "single_source"
        assert block["by_source_class"] == {"trade_publication": 1}

    def test_industry_cites_an_association_and_two_origins_corroborate(self) -> None:
        block = self._section(self._report(), "industry_and_market")["web_evidence"]
        item = block["items"][0]
        assert {s["source_class"] for s in item["sources"]} == {
            "industry_association", "trade_publication",
        }
        assert item["corroboration"] == "independently_corroborated"
        assert item["origins"] == "2 sources"

    def test_risk_and_competitive_sections_map_too(self) -> None:
        report = self._report()
        assert self._section(report, "risks_and_counter_thesis")["web_evidence"]["items"]
        assert self._section(report, "competitive_position")["web_evidence"]["items"]

    def test_key_financials_stay_filings_only(self) -> None:
        report = self._report()
        assert "web_evidence" not in self._section(report, "financial_capacity")
        assert "web_evidence" not in self._section(report, "business_model")

    def test_a_report_without_web_support_is_unchanged(self) -> None:
        inputs = pr.ReportInputs(
            subject={"ticker": "VGRD"}, questions=[],
            findings=[_fv("f1", "Plain finding statement here.", "catalysts", ("fact-9",))],
        )
        report = pr.assemble(inputs)
        for section in report["sections"]:
            assert "web_evidence" not in section
        evidence = self._section(report, "evidence_quality_and_gaps")
        assert "web_research" not in evidence

    def test_the_stored_w4_label_is_surfaced(self) -> None:
        findings = [_fv("f1", "[company says] Voltgrid is the leading supplier.", "catalysts",
                        ("ev:c:cat",))]
        report = pr.assemble(pr.ReportInputs(
            subject={}, questions=[], findings=findings,
            web_support={"ev:c:cat": _support("ev:c:cat", "company_press_release",
                                              "issuer:abc")},
        ))
        item = self._section(report, "growth_and_catalysts")["web_evidence"]["items"][0]
        assert item["statement_label"] == "company says"
        assert item["corroboration"] == "issuer_only"

    def test_evidence_quality_lists_the_sources_found_but_not_accessible(self) -> None:
        context = {
            "state": "web_search_degraded",
            "label": "Web search incomplete (4 of 6 searches ran)",
            "queries": {"planned": 6, "executed": 4},
            "ingest": {"ingested": 3},
            "fetch": {"not_retrievable": 2},
            "source_classes": {"trade_publication": 3},
            "not_retrievable": [{"domain": "utilitydive.com", "reason": "paywalled"},
                                {"domain": "nema.org", "reason": "robots_disallowed"}],
        }
        report = self._report(web_context=context)
        block = self._section(report, "evidence_quality_and_gaps")["web_research"]
        assert block["sources_found_not_accessible"] == [
            {"domain": "utilitydive.com", "reason": "paywalled"},
            {"domain": "nema.org", "reason": "robots_disallowed"},
        ]
        assert block["searches_run"] == 4 and block["searches_planned"] == 6
        assert block["web_backed_findings_by_corroboration"]["independently_corroborated"] == 1
        assert block["web_backed_findings_by_corroboration"]["single_source"] == 3

    def test_a_third_party_host_cannot_withhold_the_report(self) -> None:
        """The final safety scan is substring-based: a host named like a rating label is
        neutralised in the block, never passed through to it."""
        from app.services import safety_terms

        context = {"state": "ok", "queries": {"planned": 1, "executed": 1},
                   "not_retrievable": [{"domain": "buy-rating-now.example", "reason": "paywalled"}]}
        report = self._report(web_context=context)
        assert safety_terms.scan_value(
            self._section(report, "evidence_quality_and_gaps")["web_research"], path="x"
        ) == []


class TestTheAcceptanceRun:
    """Recorded-fixture end to end: the stage stores real documents; findings that cite
    their ``ev:c:`` chunks, recorded through the ledger the way the Investigator does,
    appear in the catalysts and industry sections with the W4 labels."""

    async def test_catalysts_and_industry_cite_web_chunks_with_labels(
        self, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.web_research.stage import WebRunContext

        net = FakeNet()
        company = await _company(session)
        provider = FakeWebSearchProvider.from_fixture_dir(FIXTURES)
        from app.services.web_research.pool import ExtractionPool

        pool = ExtractionPool(1)
        try:
            from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore

            result = await st.ensure_web_context(
                session, company, WebRunContext(mode="quick", industry="Electrical equipment"),
                cfg=_web_cfg(),
                deps=st.StageDeps(provider=provider, fetch=net, pool=pool,
                                  store=InMemoryArtifactStore(), llm_transport=None,
                                  persist_session_factory=None, today=TODAY),
            )
        finally:
            pool.shutdown()
        assert result.state == "ok"

        async def evidence_for(url_part: str) -> list[str]:
            versions = (await session.execute(
                select(ResearchDocumentVersion).where(
                    ResearchDocumentVersion.canonical_url.like(f"%{url_part}%"))
            )).scalars().all()
            assert len(versions) == 1, url_part
            chunks = (await session.execute(
                select(ResearchDocumentChunk.chunk_id).where(
                    ResearchDocumentChunk.research_document_version_id == versions[0].id)
            )).scalars().all()
            assert chunks
            return [evidence_for_chunk(c) for c in chunks][:1]

        def evidence_for_chunk(chunk_id: str) -> str:
            return evidence_id_for(chunk_id)

        cat_ids = await evidence_for("tdworld.com/grid/voltgrid-order")
        ind_ids = await evidence_for("nema.org/reports/transformer-supply-outlook")
        risk_ids = await evidence_for("utilitydive.com/news/voltgrid-texas-delay")

        run = await ledger.open_run(session, mode="standard", company_id=company.id)
        for statement, ids, domain in (
            ("Voltgrid was awarded a framework order by a regional utility.", cat_ids,
             "catalysts"),
            ("Large power transformer lead times reached 120 to 210 weeks in 2026.", ind_ids,
             "industry_economics"),
            ("Voltgrid postponed commissioning of its Texas line by two quarters.", risk_ids,
             "risks"),
        ):
            support = await trust.resolve_support(session, ids, company_id=company.id)
            assert support and all(item.web for item in support)
            await ledger.record_finding(
                session, run, statement=statement, evidence_ids=ids, domain=domain,
                support=support, source_kinds=["search_company_corpus"],
            )
        await session.flush()

        plan = SimpleNamespace(questions=[])
        loop_result = SimpleNamespace(evidence_by_question={})
        report, withheld = await v3._professional_report(
            session, run, plan, loop_result, company=company, thesis=None, thesis_size=None,
            table_payloads={}, council_convened=False, editor_client=None,
            web_context=result.summary,
        )
        assert withheld is None and report is not None
        sections = {s["key"]: s for s in report["sections"]}

        cat = sections["growth_and_catalysts"]
        assert any(set(f["evidence_ids"]) & set(cat_ids) for f in cat["findings"])
        cat_item = cat["web_evidence"]["items"][0]
        assert cat_item["sources"][0]["source_class"] == "trade_publication"
        assert cat_item["sources"][0]["evidence_id"].startswith("ev:")
        assert cat_item["corroboration"] == "single_source"

        ind = sections["industry_and_market"]
        assert any(set(f["evidence_ids"]) & set(ind_ids) for f in ind["findings"])
        ind_item = ind["web_evidence"]["items"][0]
        assert ind_item["sources"][0]["source_class"] == "industry_association"

        # The W4 claim rules labelled both, stored as a statement prefix (never a
        # deletion) and surfaced beside the class and the corroboration state.
        assert cat_item["statement_label"] == "single source"
        assert any(f["statement"].startswith("[single source] ") for f in cat["findings"])
        assert ind_item["statement_label"] == "single source estimate"
        assert ind_item["corroboration"] == "single_source"

        risk = sections["risks_and_counter_thesis"]
        assert risk["web_evidence"]["by_source_class"] == {"trade_publication": 1}
        quality = sections["evidence_quality_and_gaps"]["web_research"]
        assert quality["state"] == "ok" and quality["searches_run"] == 6

        # Key financials stay filings-only.
        assert "web_evidence" not in sections["financial_capacity"]
        assert report["disclaimer"]


class TestTheV2CatalystSectionReadsWebEvidenceAdditively:
    def _report(self, catalyst: dict[str, Any] | None = None) -> Any:
        content = {"news_catalyst_discovery": catalyst or {"available": True, "events": [],
                                                           "coverage_status": "none_found"},
                   "other": {"keep": 1}}
        markdown = "\n".join(["# DRAFT", "", "```json", json.dumps(content, indent=2), "```"])
        return SimpleNamespace(content_markdown=markdown, source_summary_json={})

    def _outcome(self, evidence: list[dict[str, Any]] | None) -> v3.V3ResearchOutcome:
        web = {"state": "ok", "catalyst_evidence": evidence} if evidence is not None else {}
        return v3.V3ResearchOutcome(web_context=web)

    def test_catalyst_documents_are_added_under_a_new_key_only(self) -> None:
        report = self._report()
        before = json.loads(report.content_markdown.split("```json")[1].split("```")[0])
        v3.attach_to_report(report, self._outcome([{
            "title": "Voltgrid wins an order", "url": "https://www.tdworld.com/x",
            "domain": "tdworld.com", "source_class": "trade_publication",
            "published_at": "2026-09-12", "origin_key": "tdworld.com",
        }]))
        after = json.loads(report.content_markdown.split("```json")[1].split("```")[0])
        section = after["news_catalyst_discovery"]
        assert section["web_catalyst_evidence"]["total"] == 1
        row = section["web_catalyst_evidence"]["value"][0]
        assert row["source_class_label"] == "Trade publication" and row["published_at"]
        # Every pre-existing key is untouched.
        for key, value in before["news_catalyst_discovery"].items():
            assert section[key] == value
        assert after["other"] == before["other"]

    def test_no_web_catalysts_means_a_byte_identical_report(self) -> None:
        for outcome in (self._outcome(None), self._outcome([])):
            report = self._report()
            original = report.content_markdown
            v3.attach_to_report(report, outcome)
            assert report.content_markdown == original

    def test_titles_pass_the_same_neutralisation_as_external_headlines(self) -> None:
        from app.services import safety_terms

        report = self._report()
        v3.attach_to_report(report, self._outcome([{
            "title": "Analysts rate it a strong buy", "url": "https://x.example/a",
            "domain": "x.example", "source_class": "unknown_web",
        }]))
        section = json.loads(
            report.content_markdown.split("```json")[1].split("```")[0]
        )["news_catalyst_discovery"]["web_catalyst_evidence"]
        assert safety_terms.scan_value(section, path="web_catalyst_evidence") == []

    def test_a_missing_catalyst_section_is_left_alone(self) -> None:
        report = SimpleNamespace(
            content_markdown="```json\n" + json.dumps({"other": 1}) + "\n```",
            source_summary_json={},
        )
        original = report.content_markdown
        v3.attach_to_report(report, self._outcome([{"title": "t", "url": "https://x.example/"}]))
        assert report.content_markdown == original


class TestSearchBackedUnits:
    def test_the_documented_page_names_exist_as_fixtures(self) -> None:
        for name in PAGES.values():
            assert (Path(FIXTURES) / name).is_file()
