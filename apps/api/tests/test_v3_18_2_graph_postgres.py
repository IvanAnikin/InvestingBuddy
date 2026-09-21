"""V3.18.2 on real PostgreSQL, and the schema-readiness guard.

The unit suite runs on SQLite, where JSONB is JSON and foreign keys are off. The graph is
JSONB-heavy (contracts, acquisition logs, dependency lists) and hangs off `research_runs`
by foreign key, so the round trip is asserted on the real engine too — the V3.13 lesson:
green SQLite tests once hid an outage that took every report with it.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.ledger import ResearchQuestion
from app.services import schema_readiness
from app.services.director.planner import persist_plan, plan_research
from app.services.ledger import store as ledger

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 041"
)
MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / (
    "041_add_research_question_graph.py"
)

_CFG = SimpleNamespace(
    v3_agent_tools_enabled=True,
    v3_deepseek_search_enabled=True,
    v3_filings_tool_enabled=True,
    v3_corpus_enabled=True,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _migration_columns() -> set[tuple[str, str]]:
    spec = importlib.util.spec_from_file_location("_m041", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {(table, name) for table, name, _type in module._COLUMNS}


class TestReadiness:
    def test_the_guard_checks_only_columns_the_migration_adds(self) -> None:
        declared = {
            (table, column)
            for table, columns in schema_readiness.MIGRATION_041_COLUMNS.items()
            for column in columns
        }
        assert declared <= _migration_columns()

    async def test_a_database_at_head_is_ready(self) -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as session:
            schema_readiness.reset_cache()
            assert (await schema_readiness.migration_041_readiness(session)).ready
        await engine.dispose()

    async def test_a_database_without_041_is_not_ready_and_says_what_is_missing(self) -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE research_gaps DROP COLUMN knowledge_state"))
        async with async_sessionmaker(engine)() as session:
            schema_readiness.reset_cache()
            readiness = await schema_readiness.migration_041_readiness(session)
            assert not readiness.ready
            assert "research_gaps.knowledge_state" in readiness.missing
        await engine.dispose()

    async def test_the_pipeline_degrades_rather_than_failing(self, monkeypatch) -> None:
        """Code deployed before a human applies the migration must cost the V3 run, with
        the reason named — never the report."""
        from app.services.pipeline import v3_pipeline

        async def not_ready(_session, **_kw):  # noqa: ANN001, ANN202
            return schema_readiness.Readiness(False, ("research_runs.thesis_json",))

        monkeypatch.setattr(schema_readiness, "migration_041_readiness", not_ready)
        company = SimpleNamespace(id=uuid.uuid4(), ticker="ANY", exchange="US", name="Any")
        outcome = await v3_pipeline.run_v3_research(object(), company, cfg=SimpleNamespace())
        assert outcome.error == "schema_not_ready"
        assert any("migration 041" in reason for reason in outcome.degraded)
        assert outcome.research_run_id is None


@requires_postgres
class TestTheGraphOnPostgres:
    async def test_a_plan_round_trips_with_its_jsonb_intact(self) -> None:
        engine = create_async_engine(POSTGRES_URL, future=True)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as session:
                schema_readiness.reset_cache()
                assert (await schema_readiness.migration_041_readiness(session)).ready
                run = await ledger.open_run(session, mode="deep")
                plan = await plan_research(subject="ANY:US", mode="deep", cfg=_CFG)
                await persist_plan(session, run, plan)
                await ledger.update_question_graph_state(
                    session,
                    run,
                    "industry_economics",
                    contract_status="partial",
                    contract_detail={"missing": ["needs an independent source"], "items": 1},
                    acquisition_steps=[{"rung": "platform_tools", "round": 0}],
                    unresolved_reason=ledger.UNRESOLVED_CONTRACT_UNMET,
                )
                await ledger.update_question_graph_state(
                    session,
                    run,
                    "industry_economics",
                    acquisition_steps=[{"rung": "external_search", "round": 1}],
                )
                await session.commit()
                run_id = run.id
            async with maker() as session:
                row = (
                    await session.execute(
                        select(ResearchQuestion).where(
                            ResearchQuestion.research_run_id == run_id,
                            ResearchQuestion.question_key == "industry_economics",
                        )
                    )
                ).scalar_one()
                assert row.domain == "industry_economics"
                assert row.owner_role == "industry_analyst"
                assert row.evidence_contract_json["allow_external"] is True
                assert row.contract_detail_json["missing"] == ["needs an independent source"]
                assert [s["rung"] for s in row.acquisition_log_json] == [
                    "platform_tools", "external_search",
                ], "the log is appended across rounds, never replaced"
                assert row.unresolved_reason == "contract_unmet"
        finally:
            await engine.dispose()

    async def test_an_unknown_unresolved_reason_is_refused(self) -> None:
        engine = create_async_engine(POSTGRES_URL, future=True)
        try:
            async with async_sessionmaker(engine)() as session:
                run = await ledger.open_run(session, mode="quick")
                with pytest.raises(ValueError, match="unresolved reason"):
                    await ledger.update_question_graph_state(
                        session, run, "x", unresolved_reason="we gave up"
                    )
                await session.rollback()
        finally:
            await engine.dispose()
