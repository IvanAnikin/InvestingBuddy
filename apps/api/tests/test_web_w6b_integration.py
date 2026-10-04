"""Open-web W6b — the stage inside ``process_run``, the corpus theme tool, and the API fields.

SQLite with the real schema, the fake provider and a fake network. The point of each test
is a seam the unit tests cannot see: the run row the stage writes into, the theme documents
the stage ingests and ``search_theme_corpus`` reads back, and the additive API fields.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import Settings
from app.db.base import Base
from app.integrations.search.fake import FakeWebSearchProvider
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.schemas.market_discovery import (
    DiscoveryCandidateRead,
    DiscoveryRunRead,
    ThesisDiscoveryRunCreate,
)
from app.services import market_discovery_service as mds
from app.services.agent_tools.contracts import TOOL_SEARCH_THEME_CORPUS
from app.services.agent_tools.corpus_search import (
    _search_theme_corpus,
    validate_search_theme_corpus,
)
from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
from app.services.corpus.search.backends.memory import InMemorySearchBackend
from app.services.director.roles import INDUSTRY_ANALYST
from app.services.web_research import discovery_stage as ds
from app.services.web_research.pool import ExtractionPool
from tests.helpers.discovery_web import (
    GALLIUM,
    GALLIUM_ARTICLE,
    NOW,
    TODAY,
    Net,
    directory_fetcher,
    hit,
    plan_for,
    serve,
)
from tests.test_phase27_thesis_discovery import _fake_extractor

ART = "https://www.mining.com/web/new-gallium-producers"


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


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


@pytest.fixture(autouse=True)
def _flags(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    from app.core.config import settings
    from app.services.discovery import directories, fx

    directories.reset_cache()
    fx._CACHE.clear()
    for name, value in {
        "v3_dynamic_discovery_enabled": True,
        "v3_discovery_web_search_enabled": True,
        "v3_web_search_enabled": True,
        "v3_web_search_provider": "fake",
        "v3_web_search_max_queries_per_day": 300,
        "v3_web_fetch_enabled": True,
        "v3_corpus_enabled": True,
        "v3_web_corpus_ingest_enabled": False,
        "v3_artifact_store_backend": "none",
        "v3_run_max_web_searches": 0,
    }.items():
        monkeypatch.setattr(settings, name, value)
    yield
    directories.reset_cache()
    fx._CACHE.clear()


def cfg(**over: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "v3_discovery_web_search_enabled": True,
        "v3_web_search_enabled": True,
        "v3_web_search_provider": "fake",
        "v3_web_search_max_queries_per_day": 300,
        "v3_run_max_web_searches": 0,
        "v3_web_fetch_enabled": True,
        "v3_corpus_enabled": True,
        "v3_web_corpus_ingest_enabled": False,
        "v3_artifact_store_backend": "none",
        "v3_run_max_model_calls": 0,
        "deepseek_api_key": "",
    }
    values.update(over)
    return Settings(**values)


def web_deps(
    pool: Any, net: Net, provider: FakeWebSearchProvider, **over: Any
) -> ds.DiscoveryWebDeps:
    values: dict[str, Any] = {
        "provider": provider,
        "fetch": net,
        "pool": pool,
        "llm_transport": None,
        "persist_session_factory": None,
        "today": TODAY,
        "now": NOW,
    }
    values.update(over)
    return ds.DiscoveryWebDeps(**values)


async def thesis_run(session: Any, text: str = GALLIUM) -> DiscoveryRun:
    return await mds.create_pending_thesis_run(
        session, ThesisDiscoveryRunCreate(thesis_text=text, provider_name="free_real")
    )


# --------------------------------------------------------------------------- #
# process_run
# --------------------------------------------------------------------------- #


class TestProcessRun:
    async def test_the_web_stage_runs_before_the_scan_and_persists_provenance(
        self, session: Any, pool: Any
    ) -> None:
        provider = FakeWebSearchProvider()
        net = Net({ART: GALLIUM_ARTICLE})
        run = await thesis_run(session)
        serve(provider, plan_for(mds_intent(run)), {"entity_listed.0": [hit(ART)]})
        run = await mds.process_run(
            session,
            run,
            extractor=_fake_extractor(),
            discovery_fetcher=directory_fetcher,
            discovery_web_deps=web_deps(pool, net, provider),
        )
        dynamic = run.universe_json["dynamic"]
        assert dynamic["status"] == "completed"
        assert dynamic["web"]["state"] == "ok"
        assert dynamic["web"]["queries"]["executed"] == dynamic["web"]["queries"]["planned"]
        assert dynamic["web"]["theme_key"] == f"discovery:{run.id}"
        rows = (
            (
                await session.execute(
                    select(DiscoveryCandidate).where(DiscoveryCandidate.discovery_run_id == run.id)
                )
            )
            .scalars()
            .all()
        )
        alg = next(c for c in rows if c.ticker == "ALG")
        v319 = alg.thesis_match_json["v319"]
        assert v319["v3_web"]["discovery_mode"] == "search"
        assert v319["v3_web"]["admission"]["state"] == "admitted"
        assert v319["provenance"]["discovery_mode"] == "search"
        assert run.processed_count == len(run.universe_json["items"])
        # Search provenance rows carry the run id (the audit trail's join key).
        from app.models.web_research import WebSearchQuery

        queries = (await session.execute(select(WebSearchQuery))).scalars().all()
        assert queries and all(q.discovery_run_id == run.id for q in queries)

    async def test_the_run_reports_a_web_search_outage_and_still_completes(
        self, session: Any, pool: Any
    ) -> None:
        from app.integrations.search.fake import MODE_OUTAGE

        provider = FakeWebSearchProvider(mode=MODE_OUTAGE)
        run = await thesis_run(session)
        run = await mds.process_run(
            session,
            run,
            extractor=_fake_extractor(),
            discovery_fetcher=directory_fetcher,
            discovery_web_deps=web_deps(pool, Net(), provider),
        )
        web = run.universe_json["dynamic"]["web"]
        assert web["state"] == "web_search_unavailable"
        assert "Live web search unavailable" in web["label"]
        read = DiscoveryRunRead.model_validate(run)
        assert read.web_search_state == "web_search_unavailable"
        assert read.web_search_label.startswith("Live web search unavailable")

    async def test_flag_off_the_run_row_has_no_web_keys(
        self, session: Any, pool: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "v3_discovery_web_search_enabled", False)
        provider = FakeWebSearchProvider()
        run = await thesis_run(session)
        run = await mds.process_run(
            session,
            run,
            extractor=_fake_extractor(),
            discovery_fetcher=directory_fetcher,
            discovery_web_deps=web_deps(pool, Net(), provider),
        )
        dynamic = run.universe_json["dynamic"]
        assert "web" not in dynamic and "also_surfaced" not in dynamic
        assert provider.requests == []
        read = DiscoveryRunRead.model_validate(run)
        assert read.web_search_state is None and read.web_search_label is None


def mds_intent(run: DiscoveryRun) -> Any:
    from app.services.discovery.intent import intent_from_dict

    return intent_from_dict(run.parsed_thesis_json["discovery_intent"])


# --------------------------------------------------------------------------- #
# Theme documents and ``search_theme_corpus``
# --------------------------------------------------------------------------- #


class TestThemeCorpus:
    async def test_pages_are_ingested_as_theme_documents_the_run_can_search(
        self, session: Any, pool: Any
    ) -> None:
        from app.models.research_document import ResearchDocumentSubject

        provider = FakeWebSearchProvider()
        net = Net({ART: GALLIUM_ARTICLE})
        backend = InMemorySearchBackend()
        run = await thesis_run(session)
        serve(provider, plan_for(mds_intent(run)), {"entity_listed.0": [hit(ART)]})
        config = cfg(v3_web_corpus_ingest_enabled=True)
        from app.services.discovery.pipeline import run_dynamic_stage

        stage = await run_dynamic_stage(
            session,
            intent=mds_intent(run),
            run_universe={"items": []},
            cfg=config,
            provider=None,
            fetcher=directory_fetcher,
            run_id=run.id,
            web_deps=web_deps(
                pool, net, provider, store=InMemoryArtifactStore(), search_backend=backend
            ),
        )
        theme_key = stage.web["theme_key"]
        subjects = (await session.execute(select(ResearchDocumentSubject))).scalars().all()
        assert [(s.relation, s.theme_key) for s in subjects] == [("theme", theme_key)]
        assert stage.web["pages"]["ingested"] == 1
        alg = next(r for r in stage.candidates if r.identity.ticker == "ALG")
        evidence_id = alg.web["admission"]["evidence_ids"][0]
        assert not evidence_id.startswith("wp:"), "a stored page's passage is a corpus chunk id"

        args = validate_search_theme_corpus({"query": "gallium recovery plant offtake"})
        found = await _search_theme_corpus(
            SimpleNamespace(
                session=session, cfg=config, search_backend=backend, theme_key=theme_key
            ),
            args,
        )
        assert found["theme_scoped"] and found["items"]
        assert found["items"][0]["source_class"] == "trade_publication"
        other = await _search_theme_corpus(
            SimpleNamespace(
                session=session,
                cfg=config,
                search_backend=backend,
                theme_key="discovery:" + str(uuid.uuid4()),
            ),
            args,
        )
        assert other["items"] == [], "another run's theme is not readable"
        none = await _search_theme_corpus(
            SimpleNamespace(session=session, cfg=config, search_backend=backend, theme_key=None),
            args,
        )
        assert none["refusal"] == "no_theme_scope_for_this_run"

    async def test_an_unstored_page_still_gives_a_passage_ref_as_evidence(
        self, session: Any, pool: Any
    ) -> None:
        provider = FakeWebSearchProvider()
        net = Net({ART: GALLIUM_ARTICLE})
        run = await thesis_run(session)
        serve(provider, plan_for(mds_intent(run)), {"entity_listed.0": [hit(ART)]})
        from app.services.discovery.pipeline import run_dynamic_stage

        stage = await run_dynamic_stage(
            session,
            intent=mds_intent(run),
            run_universe={"items": []},
            cfg=cfg(),
            provider=None,
            fetcher=directory_fetcher,
            run_id=run.id,
            web_deps=web_deps(pool, net, provider),
        )
        alg = next(r for r in stage.candidates if r.identity.ticker == "ALG")
        assert alg.web["admission"]["evidence_ids"][0].startswith("wp:")

    async def test_the_theme_key_resolves_only_for_a_run_whose_web_stage_ran(
        self, session: Any, pool: Any
    ) -> None:
        run = await thesis_run(session)
        cand = DiscoveryCandidate(
            id=uuid.uuid4(),
            discovery_run_id=run.id,
            ticker="ALG",
            exchange="AU",
            company_name="Alpha Gallium Limited",
            human_review_required=True,
            is_public=False,
            candidate_score=0.0,
            positive_catalyst_count=0,
            high_strength_catalyst_count=0,
            press_release_event_count=0,
            news_event_count=0,
            filing_event_count=0,
            primary_or_regulator_event_count=0,
            aggregator_only_event_count=0,
        )
        session.add(cand)
        await session.flush()
        assert await ds.theme_key_for_candidate(session, cand.id) is None, "no web stage yet"
        run.universe_json = {
            "dynamic": {"status": "completed", "web": {"state": "ok", "theme_key": "discovery:x"}}
        }
        await session.flush()
        assert await ds.theme_key_for_candidate(session, cand.id) == "discovery:x"
        assert await ds.theme_key_for_candidate(session, str(cand.id)) == "discovery:x"
        run.universe_json = {
            "dynamic": {"web": {"state": "web_search_unavailable", "theme_key": "discovery:x"}}
        }
        await session.flush()
        assert await ds.theme_key_for_candidate(session, cand.id) is None
        assert await ds.theme_key_for_candidate(session, uuid.uuid4()) is None
        assert await ds.theme_key_for_candidate(session, "not-a-uuid") is None

    async def test_the_pipeline_forwards_the_theme_key_to_the_research(
        self, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import settings
        from app.services import company_research_service as crs

        run = await thesis_run(session)
        run.universe_json = {"dynamic": {"web": {"state": "ok", "theme_key": "discovery:k"}}}
        cand = DiscoveryCandidate(
            id=uuid.uuid4(),
            discovery_run_id=run.id,
            ticker="ALG",
            exchange="AU",
            human_review_required=True,
            is_public=False,
            candidate_score=0.0,
            positive_catalyst_count=0,
            high_strength_catalyst_count=0,
            press_release_event_count=0,
            news_event_count=0,
            filing_event_count=0,
            primary_or_regulator_event_count=0,
            aggregator_only_event_count=0,
        )
        session.add(cand)
        await session.flush()
        seen: dict[str, Any] = {}

        async def fake_run(session_: Any, company: Any, **kw: Any) -> Any:
            seen.update(kw)
            raise RuntimeError("stop here")

        monkeypatch.setattr(settings, "v3_pipeline_enabled", True)
        import app.services.pipeline.v3_pipeline as v3

        monkeypatch.setattr(v3, "run_v3_research", fake_run)
        await crs._run_v3_pipeline(
            session,
            company=SimpleNamespace(id=uuid.uuid4()),
            report_id=uuid.uuid4(),
            discovery_candidate_id=cand.id,
        )
        assert seen["theme_key"] == "discovery:k"
        seen.clear()
        await crs._run_v3_pipeline(
            session, company=SimpleNamespace(id=uuid.uuid4()), report_id=uuid.uuid4()
        )
        assert seen["theme_key"] is None, "a company run has no theme scope"

    def test_the_industry_role_holds_the_theme_tool(self) -> None:
        assert INDUSTRY_ANALYST.can_use(TOOL_SEARCH_THEME_CORPUS)
        assert INDUSTRY_ANALYST.can_use("search_company_corpus")


# --------------------------------------------------------------------------- #
# API fields
# --------------------------------------------------------------------------- #


class TestApiFields:
    def test_candidate_read_exposes_the_mode_and_the_admission(self) -> None:
        read = DiscoveryCandidateRead.model_construct(
            thesis_match_json={
                "v319": {
                    "provenance": {"discovery_mode": "search"},
                    "v3_web": {"admission": {"state": "admitted", "codes": []}},
                }
            }
        )
        assert read.discovery_mode == "search"
        assert read.admission == {"state": "admitted", "codes": []}

    def test_a_curated_candidate_has_no_mode_and_a_v319_one_has_no_admission(self) -> None:
        read = DiscoveryCandidateRead.model_construct(
            thesis_match_json={"v319": {"provenance": {"discovery_mode": None}}}
        )
        assert read.discovery_mode is None and read.admission is None
        assert DiscoveryCandidateRead.model_construct(thesis_match_json=None).admission is None

    def test_the_fields_are_in_the_serialised_response(self) -> None:
        read = DiscoveryCandidateRead.model_construct(
            thesis_match_json={"v319": {"provenance": {"discovery_mode": "model_recall"}}}
        )
        dumped = read.model_dump()
        assert dumped["discovery_mode"] == "model_recall" and "admission" in dumped
