"""Item 20 on real PostgreSQL: a development-stage plan persists through the ledger.

The overlay's questions — blocking flags, required calculations, evidence contracts —
round-trip on the real engine (JSONB, foreign keys, CHECK constraints), and the producer
questions it superseded are not written as questions. Skipped without
``V3_TEST_POSTGRES_URL``.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.ledger import ResearchQuestion
from app.services.director.planner import persist_plan, plan_research
from app.services.ledger import store as ledger
from app.services.macro.commodities import BY_SLUG
from app.services.pipeline.v3_pipeline import _PlaybookAdapter
from app.services.playbooks import select as select_playbooks

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head"
)
CFG = SimpleNamespace(v3_agent_tools_enabled=True, v3_deepseek_search_enabled=True,
                      v3_filings_tool_enabled=True, v3_corpus_enabled=True,
                      v3_commodity_sources_enabled=True)


async def test_a_development_stage_plan_round_trips():
    engine = create_async_engine(POSTGRES_URL, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            selection = select_playbooks(sector="Materials", industry="Metals & Mining",
                                         signals=["development_stage_resource"])
            run = await ledger.open_run(session, mode="deep",
                                        playbook_versions=dict(selection.versions))
            plan = await plan_research(
                subject="ANY:AU", mode="deep",
                playbooks=[_PlaybookAdapter(p) for p in selection.playbooks], cfg=CFG,
                commodities=[BY_SLUG["lithium"]] if "lithium" in BY_SLUG else [])
            await persist_plan(session, run, plan)
            await session.flush()
            rows = (await session.execute(
                select(ResearchQuestion).where(ResearchQuestion.research_run_id == run.id)
            )).scalars().all()
            by_key = {r.question_key: r for r in rows}
            assert by_key["capex_and_funding"].blocking is True
            assert by_key["cash_runway_and_dilution"].domain == "financial_capacity"
            for superseded in ("unit_costs", "reserves_and_mine_life", "revenue_trajectory"):
                assert superseded not in by_key
            await session.rollback()
    finally:
        await engine.dispose()
