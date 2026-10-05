"""Open-web W7 on real PostgreSQL — the follow-up's rows, the loop's ledger path and
escalation's improvement count with the real search backend.

Set ``V3_TEST_POSTGRES_URL`` to a database at head (044). SQLite runs with foreign keys
OFF; here the lineage FKs of the search/fetch rows, the ledger (closed gap -> finding) and
the ``indexed_at`` index state are real. Every test rolls back.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.company import Company
from app.models.ledger import ResearchGap
from app.models.web_research import WebSearchQuery, WebSearchResult
from app.services.corpus.search.backends.postgres import PostgresSearchBackend
from app.services.director import loop as lp
from app.services.director.planner import persist_plan
from app.services.escalation import evidence as ev
from app.services.ledger import store as ledger
from app.services.pipeline import gap_reconciliation
from app.services.web_research import followup as fu
from app.services.web_research.pool import ExtractionPool
from tests.test_web_w7_followup import (
    CAPACITY_QUERIES,
    _followup,
    _limits,
    _plan,
    _provider,
)
from tests.test_web_w7_integration import ReadsTheCorpus

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 044"
)


@requires_postgres
class TestTheFollowUpOnPostgres:
    async def test_the_loop_closes_a_gap_through_the_real_ledger_and_the_real_index(
        self,
    ) -> None:
        engine = create_async_engine(POSTGRES_URL, future=True)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        pool = ExtractionPool(1)
        try:
            async with maker() as session:
                company = Company(
                    id=uuid.uuid4(), ticker="VGRD", exchange="NASDAQ", name="Voltgrid Corp",
                    status="new",
                )
                session.add(company)
                await session.flush()
                before = await ev.snapshot_evidence(session, company.id)
                backend = PostgresSearchBackend(session)
                web = _followup(
                    session, company, pool, _provider(**CAPACITY_QUERIES),
                    search_backend=backend,
                )
                run = await ledger.open_run(session, mode="standard", company_id=company.id)
                plan = _plan("q1")
                await persist_plan(session, run, plan)
                investigator = ReadsTheCorpus(
                    session, "Nameplate production capacity is not disclosed.",
                    "Nameplate production capacity of 120,000 tpa is expected once the "
                    "second line is commissioned.",
                )
                result = await lp.run_investigation(
                    session, run, plan, investigator=investigator, limits=_limits(),
                    web_followup=web,
                )
                assert result.stopped_by == lp.STOPPED_ANSWERED
                assert result.web_followup["followup_rounds"] == 1

                # Provenance with real FKs: executed GAP rows, results with dispositions.
                queries = (await session.execute(select(WebSearchQuery))).scalars().all()
                assert len(queries) == 2 and all(q.executed and q.family == "gap" for q in queries)
                results = (await session.execute(select(WebSearchResult))).scalars().all()
                assert {r.disposition for r in results} == {"ingested"}

                # Track B's persisted reconciliation sees the follow-up finding.
                await gap_reconciliation.reconcile_run(session, run, company_id=company.id)
                gap = (await session.execute(
                    select(ResearchGap).where(ResearchGap.research_run_id == run.id)
                )).scalar_one()
                assert gap.status == "closed" and gap.closed_by_finding_id is not None

                # Escalation: the web document is indexed (``indexed_at``) and counts.
                after = await ev.snapshot_evidence(session, company.id)
                delta = ev.measure_evidence_delta(before, after)
                assert delta["indexed_chunks_added"] >= 1
                assert delta["searchable_documents_added"] == 1 and delta["improved"] is True
                await session.rollback()
        finally:
            pool.shutdown()
            await engine.dispose()

    async def test_the_challenge_wave_and_risk_evidence_on_real_rows(self) -> None:
        engine = create_async_engine(POSTGRES_URL, future=True)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        pool = ExtractionPool(1)
        try:
            async with maker() as session:
                company = Company(
                    id=uuid.uuid4(), ticker="VGRD", exchange="NASDAQ", name="Voltgrid Corp",
                    status="new",
                )
                session.add(company)
                await session.flush()
                from app.integrations.search.fake import FakeWebSearchProvider
                from tests.test_web_w5_stage import FIXTURES

                web = _followup(
                    session, company, pool, FakeWebSearchProvider.from_fixture_dir(FIXTURES)
                )
                wave = await web.challenge_wave()
                assert wave.kind == "challenge" and wave.executed >= 1
                items = await web.risk_evidence()
                assert items and items[0].source_class
                assert fu.assess_challenge_basis([i.support for i in items], company.id).reason
                await session.rollback()
        finally:
            pool.shutdown()
            await engine.dispose()
