"""Open-web W6b on real PostgreSQL — Discovery's rows, lineage FKs and crash-resume.

Set ``V3_TEST_POSTGRES_URL`` to a database at head (044). SQLite runs with foreign keys
OFF, so the lineage of the search / fetch rows (``discovery_run_id``,
``web_search_result_id``), the independent provenance session and the JSONB candidate
provenance are only proven here. These tests COMMIT (``process_run`` and the provenance
session do), so each cleans up the rows it created.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.integrations.search.fake import FakeWebSearchProvider
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.models.web_research import WebFetchAttempt, WebSearchQuery, WebSearchResult
from app.schemas.market_discovery import ThesisDiscoveryRunCreate
from app.services import market_discovery_service as mds
from app.services.discovery import directories, fx
from app.services.discovery.pipeline import run_dynamic_stage
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
from tests.test_phase27_thesis_discovery import _fake_extractor as _sqlite_extractor

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 044"
)
ART = "https://www.mining.com/web/new-gallium-producers"


@pytest.fixture(autouse=True)
def _flags(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    directories.reset_cache()
    fx._CACHE.clear()
    for name, value in {
        "v3_dynamic_discovery_enabled": True, "v3_discovery_web_search_enabled": True,
        "v3_web_search_enabled": True, "v3_web_search_provider": "fake",
        "v3_web_search_max_queries_per_day": 5000, "v3_web_fetch_enabled": True,
        "v3_corpus_enabled": True, "v3_web_corpus_ingest_enabled": False,
        "v3_artifact_store_backend": "none", "v3_run_max_web_searches": 0,
    }.items():
        monkeypatch.setattr(settings, name, value)
    yield
    directories.reset_cache()
    fx._CACHE.clear()


def _fake_extractor() -> Any:
    """The phase-27 fake, minus the made-up report / agent-run ids (their FKs are real here)."""
    from dataclasses import replace

    inner = _sqlite_extractor()

    async def _extract(db: Any, **kw: Any) -> Any:
        return replace(await inner(db, **kw), analysis_report_id=None, agent_run_id=None)

    return _extract


def _intent(run: DiscoveryRun) -> Any:
    from app.services.discovery.intent import intent_from_dict

    return intent_from_dict(run.parsed_thesis_json["discovery_intent"])


def _deps(pool: Any, provider: FakeWebSearchProvider, net: Net, **over: Any) -> ds.DiscoveryWebDeps:
    values: dict[str, Any] = {
        "provider": provider, "fetch": net, "pool": pool, "llm_transport": None,
        "today": TODAY, "now": NOW,
        # persist_session_factory left at its default: on PostgreSQL the stage gets an
        # INDEPENDENT committed provenance session.
    }
    values.update(over)
    return ds.DiscoveryWebDeps(**values)


async def _cleanup(maker: Any, run_id: uuid.UUID) -> None:
    async with maker() as s:
        query_ids = (await s.execute(
            select(WebSearchQuery.id).where(WebSearchQuery.discovery_run_id == run_id)
        )).scalars().all()
        await s.execute(delete(WebFetchAttempt).where(WebFetchAttempt.discovery_run_id == run_id))
        if query_ids:
            await s.execute(delete(WebSearchResult).where(WebSearchResult.query_id.in_(query_ids)))
        await s.execute(delete(WebSearchQuery).where(WebSearchQuery.discovery_run_id == run_id))
        await s.execute(delete(DiscoveryCandidate).where(
            DiscoveryCandidate.discovery_run_id == run_id))
        await s.execute(delete(DiscoveryRun).where(DiscoveryRun.id == run_id))
        await s.commit()


@requires_postgres
class TestDiscoveryRowsOnPostgres:
    async def _maker(self):  # noqa: ANN202
        engine = create_async_engine(POSTGRES_URL, future=True)
        return engine, async_sessionmaker(engine, expire_on_commit=False)

    async def test_process_run_writes_rows_with_real_lineage_foreign_keys(self) -> None:
        engine, maker = await self._maker()
        pool = ExtractionPool(1)
        run_id = None
        try:
            async with maker() as session:
                run = await mds.create_pending_thesis_run(
                    session, ThesisDiscoveryRunCreate(thesis_text=GALLIUM,
                                                      provider_name="free_real"))
                run_id = run.id
                provider, net = FakeWebSearchProvider(), Net({ART: GALLIUM_ARTICLE})
                serve(provider, plan_for(_intent(run)), {"entity_listed.0": [hit(ART)]})
                run = await mds.process_run(
                    session, run, extractor=_fake_extractor(),
                    discovery_fetcher=directory_fetcher,
                    discovery_web_deps=_deps(pool, provider, net),
                )
                web = run.universe_json["dynamic"]["web"]
                assert web["state"] == "ok"
            async with maker() as check:
                queries = (await check.execute(
                    select(WebSearchQuery).where(WebSearchQuery.discovery_run_id == run_id)
                )).scalars().all()
                assert len(queries) == web["queries"]["planned"] and all(q.executed for q in queries)
                assert {q.stage for q in queries} == {"discovery_web"}
                attempts = (await check.execute(
                    select(WebFetchAttempt).where(WebFetchAttempt.discovery_run_id == run_id)
                )).scalars().all()
                assert len(attempts) == 1 and attempts[0].web_search_result_id is not None
                result = await check.get(WebSearchResult, attempts[0].web_search_result_id)
                assert result is not None and result.url == ART
                assert result.disposition in {"selected", "ingested", "reused", "not_ingested"}
                alg = (await check.execute(
                    select(DiscoveryCandidate).where(
                        DiscoveryCandidate.discovery_run_id == run_id,
                        DiscoveryCandidate.ticker == "ALG")
                )).scalar_one()
                v3_web = alg.thesis_match_json["v319"]["v3_web"]
                sighting = v3_web["sightings"][0]
                # The persisted provenance resolves, row by row, to the real records.
                assert uuid.UUID(sighting["query_id"]) in {q.id for q in queries}
                assert uuid.UUID(sighting["fetch_attempt_id"]) == attempts[0].id
                assert uuid.UUID(sighting["result_id"]) == result.id
                assert v3_web["admission"]["state"] == "admitted"
        finally:
            pool.shutdown()
            if run_id is not None:
                await _cleanup(maker, run_id)
            await engine.dispose()

    async def test_a_stage_that_fails_after_paying_still_leaves_its_rows_and_a_retry_reuses_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The provenance session is independent: a crash after the searches keeps them (they
        are paid calls and feed the daily cap), and the retry issues none of them again."""
        engine, maker = await self._maker()
        pool = ExtractionPool(1)
        run_id = None
        try:
            async with maker() as session:
                run = await mds.create_pending_thesis_run(
                    session, ThesisDiscoveryRunCreate(thesis_text=GALLIUM,
                                                      provider_name="free_real"))
                run_id = run.id
                intent = _intent(run)
                provider, net = FakeWebSearchProvider(), Net({ART: GALLIUM_ARTICLE})
                serve(provider, plan_for(intent), {"entity_listed.0": [hit(ART)]})
                real_select = ds.select_discovery_results

                def boom(*a: Any, **k: Any) -> Any:
                    raise RuntimeError("selection exploded after the searches were paid")

                monkeypatch.setattr(ds, "select_discovery_results", boom)
                failed = await run_dynamic_stage(
                    session, intent=intent, run_universe={"items": []}, cfg=settings,
                    provider=None, fetcher=directory_fetcher, run_id=run_id,
                    web_deps=_deps(pool, provider, net),
                )
                assert failed.web["state"] == "web_stage_failed" and not failed.candidates
                await session.rollback()
                paid = len(provider.requests)
                assert paid > 0
            async with maker() as check:
                rows = (await check.execute(
                    select(func.count()).select_from(WebSearchQuery).where(
                        WebSearchQuery.discovery_run_id == run_id)
                )).scalar_one()
                assert rows == paid, "committed independently of the failed stage"
            monkeypatch.setattr(ds, "select_discovery_results", real_select)
            async with maker() as session:
                run = await session.get(DiscoveryRun, run_id)
                retried = await run_dynamic_stage(
                    session, intent=intent, run_universe={"items": []}, cfg=settings,
                    provider=None, fetcher=directory_fetcher, run_id=run_id,
                    web_deps=_deps(pool, provider, net),
                )
                assert len(provider.requests) == paid, "no search is issued twice"
                assert retried.web["queries"]["reused_on_resume"] == paid
                assert any(r.identity.ticker == "ALG" for r in retried.candidates)
                await session.rollback()
        finally:
            pool.shutdown()
            if run_id is not None:
                await _cleanup(maker, run_id)
            await engine.dispose()

    async def test_the_daily_platform_cap_counts_discovery_rows(self) -> None:
        from datetime import datetime, timezone

        from app.services.web_research.budget import network_calls_today

        engine, maker = await self._maker()
        run_id = None
        try:
            async with maker() as session:
                run = await mds.create_pending_thesis_run(
                    session, ThesisDiscoveryRunCreate(thesis_text=GALLIUM,
                                                      provider_name="free_real"))
                run_id = run.id
                before = await network_calls_today(session, now=datetime.now(timezone.utc))
                provider = FakeWebSearchProvider()
                out = await ds.run_discovery_web_stage(
                    session, _intent(run), ds.DiscoveryWebContext(run_id=run_id),
                    cfg=settings, deps=_deps(None, provider, Net()),
                )
                await session.commit()
                after = await network_calls_today(session, now=datetime.now(timezone.utc))
                assert after - before == out.summary["queries"]["executed"] > 0
        finally:
            if run_id is not None:
                await _cleanup(maker, run_id)
            await engine.dispose()
