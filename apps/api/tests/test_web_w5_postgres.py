"""Open-web W5 on real PostgreSQL — the stage's writes, its provenance session and the report.

Set ``V3_TEST_POSTGRES_URL`` to a database at head (044). SQLite runs with foreign keys
OFF, so the lineage FKs of the search/fetch rows, the independent provenance session and
the ledger path are only proven here. Tests that must commit (the independent session)
clean up after themselves; the others roll back.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.integrations.search.fake import FakeWebSearchProvider
from app.models.company import Company
from app.models.research_chunk import ResearchDocumentChunk
from app.models.research_document import ResearchDocumentVersion
from app.models.web_research import WebSearchQuery, WebSearchResult
from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
from app.services.corpus.retrieval import evidence_id_for
from app.services.ledger import store as ledger
from app.services.pipeline import v3_pipeline as v3
from app.services.web_research import stage as st
from app.services.web_research import trust
from app.services.web_research.pool import ExtractionPool
from tests.test_web_w5_stage import FIXTURES, FakeNet, _cfg

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 044"
)
TODAY = date(2026, 10, 4)


def _company() -> Company:
    return Company(
        id=uuid.uuid4(), ticker="VGRD", exchange="NASDAQ", name="Voltgrid Corp", status="new"
    )


def _deps(pool: Any, **over: Any) -> st.StageDeps:
    values: dict[str, Any] = {
        "provider": FakeWebSearchProvider.from_fixture_dir(FIXTURES),
        "fetch": FakeNet(),
        "pool": pool,
        "store": InMemoryArtifactStore(),
        "llm_transport": None,
        "persist_session_factory": None,
        "today": TODAY,
    }
    values.update(over)
    return st.StageDeps(**values)


@requires_postgres
class TestTheStageOnPostgres:
    async def _maker(self):  # noqa: ANN202
        engine = create_async_engine(POSTGRES_URL, future=True)
        return engine, async_sessionmaker(engine, expire_on_commit=False)

    async def test_the_write_path_and_the_report_with_real_foreign_keys(self) -> None:
        engine, maker = await self._maker()
        pool = ExtractionPool(1)
        try:
            async with maker() as session:
                company = _company()
                session.add(company)
                await session.flush()
                result = await st.ensure_web_context(
                    session, company,
                    st.WebRunContext(mode="quick", industry="Electrical equipment"),
                    cfg=_cfg(v3_web_corpus_ingest_enabled=True), deps=_deps(pool),
                )
                assert result.state == "ok"
                assert result.summary["ingest"]["ingested"] == 5
                rows = (await session.execute(select(WebSearchResult))).scalars().all()
                assert rows and "candidate" not in {r.disposition for r in rows}
                assert {"ingested", "skipped"} <= {r.disposition for r in rows}

                # Ledger → report, with real FKs: web findings in catalysts and industry.
                async def chunk_ids(url_part: str) -> list[str]:
                    version = (await session.execute(
                        select(ResearchDocumentVersion).where(
                            ResearchDocumentVersion.canonical_url.like(f"%{url_part}%")
                        ))).scalar_one()
                    chunks = (await session.execute(
                        select(ResearchDocumentChunk.chunk_id).where(
                            ResearchDocumentChunk.research_document_version_id == version.id
                        ))).scalars().all()
                    return [evidence_id_for(chunks[0])]

                run = await ledger.open_run(session, mode="standard", company_id=company.id)
                for statement, url_part, domain in (
                    ("Voltgrid was awarded a framework order by a regional utility.",
                     "tdworld.com/grid/voltgrid-order", "catalysts"),
                    ("Large power transformer lead times reached 120 to 210 weeks in 2026.",
                     "nema.org/reports", "industry_economics"),
                ):
                    ids = await chunk_ids(url_part)
                    support = await trust.resolve_support(session, ids, company_id=company.id)
                    assert support and all(s.web for s in support)
                    await ledger.record_finding(
                        session, run, statement=statement, evidence_ids=ids, domain=domain,
                        support=support, source_kinds=["search_company_corpus"],
                    )
                await session.flush()
                from types import SimpleNamespace

                report, withheld = await v3._professional_report(
                    session, run, SimpleNamespace(questions=[]),
                    SimpleNamespace(evidence_by_question={}), company=company, thesis=None,
                    thesis_size=None, table_payloads={}, council_convened=False,
                    editor_client=None, web_context=result.summary,
                )
                assert withheld is None and report is not None
                sections = {s["key"]: s for s in report["sections"]}
                assert sections["growth_and_catalysts"]["web_evidence"]["items"]
                assert sections["industry_and_market"]["web_evidence"]["items"]
                assert sections["evidence_quality_and_gaps"]["web_research"]["state"] == "ok"
                await session.rollback()
        finally:
            pool.shutdown()
            await engine.dispose()

    async def test_the_provenance_session_is_independent_and_survives_a_failed_stage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Search rows record PAID calls and feed the platform's daily cap, so they are
        written in their own committed transaction: a stage that fails afterwards (its
        SAVEPOINT rolled back) still leaves them."""
        engine, maker = await self._maker()
        company = _company()
        async with maker() as setup:
            setup.add(company)
            await setup.commit()
        try:
            async with maker() as session:
                factory = st.persist_factory_for(session)
                assert factory is not None, "PostgreSQL gets an independent session factory"

                def boom(*a: Any, **k: Any) -> Any:
                    raise RuntimeError("selection exploded after the searches were paid")

                monkeypatch.setattr(st, "select_results", boom)
                result = await st.ensure_web_context(
                    session, company, st.WebRunContext(mode="quick", industry="Electrical equipment"),
                    cfg=_cfg(), deps=_deps(None, persist_session_factory=factory),
                )
                assert result.state == "web_stage_failed"
                assert result.units.web_search_calls == 6
                await session.rollback()
            async with maker() as check:
                rows = (await check.execute(
                    select(WebSearchQuery).where(WebSearchQuery.company_id == company.id)
                )).scalars().all()
                assert len(rows) == 6, "committed independently of the failed stage"
                assert all(r.executed for r in rows)
                assert (await check.execute(
                    select(func.sum(WebSearchQuery.network_call_count)).where(
                        WebSearchQuery.company_id == company.id)
                )).scalar_one() == 6
        finally:
            async with maker() as cleanup:
                ids = (await cleanup.execute(
                    select(WebSearchQuery.id).where(WebSearchQuery.company_id == company.id)
                )).scalars().all()
                await cleanup.execute(delete(WebSearchResult).where(
                    WebSearchResult.query_id.in_(ids)))
                await cleanup.execute(delete(WebSearchQuery).where(
                    WebSearchQuery.company_id == company.id))
                await cleanup.execute(delete(Company).where(Company.id == company.id))
                await cleanup.commit()
            await engine.dispose()

    async def test_a_failed_stage_leaves_the_session_usable_for_the_rest_of_the_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine, maker = await self._maker()
        try:
            async with maker() as session:
                company = _company()
                session.add(company)
                await session.flush()

                def boom(*a: Any, **k: Any) -> Any:
                    raise RuntimeError("planner exploded")

                monkeypatch.setattr(st, "build_plan", boom)
                result = await st.ensure_web_context(
                    session, company, st.WebRunContext(mode="quick"), cfg=_cfg(),
                    deps=_deps(None),
                )
                assert result.state == "web_stage_failed"
                # The transaction is not aborted: the V2 report's session carries on.
                assert (await session.execute(
                    select(func.count()).select_from(Company).where(Company.id == company.id)
                )).scalar_one() == 1
                await session.rollback()
        finally:
            await engine.dispose()
