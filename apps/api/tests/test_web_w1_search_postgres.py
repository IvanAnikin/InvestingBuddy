"""Open-web W1 on real PostgreSQL — migration 042 and the provenance write path.

The static checks on the migration file always run. Everything touching a database is
skipped unless ``V3_TEST_POSTGRES_URL`` points at a PostgreSQL already at head (CI sets
it against ``postgres:16``). SQLite runs with foreign keys off, so the ``ON DELETE SET
NULL`` lineage and the real JSONB/UUID types are only proven here.

The round trip (``upgrade → downgrade 041 → upgrade head``) runs Alembic in a subprocess
against that database, exactly as an operator would.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = API_ROOT / "alembic" / "versions" / "042_add_web_search_provenance.py"
POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
FIXTURES = Path(__file__).parent / "fixtures" / "web"

requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 042"
)

NEW_TABLES = ("web_search_queries", "web_search_results", "web_fetch_attempts")


def _module():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("_m042", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sync_url() -> str:
    return POSTGRES_URL.replace("+asyncpg", "+psycopg")


def _alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": POSTGRES_URL}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    # Only the return code is asserted: the output can echo the database URL.
    failed = proc.returncode != 0
    assert failed is False, f"alembic {' '.join(args)} failed (output withheld)"


def _tables() -> set[str]:
    engine = sa.create_engine(_sync_url())
    try:
        return set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()


class TestTheMigrationIsAdditive:
    def test_it_follows_041(self) -> None:
        m = _module()
        assert (m.revision, m.down_revision) == ("042", "041")

    def test_it_owns_exactly_three_new_tables(self) -> None:
        assert _module().TABLES == NEW_TABLES

    def test_upgrade_alters_and_drops_nothing(self) -> None:
        source = MIGRATION.read_text()
        upgrade = source[source.index("def upgrade()") : source.index("def downgrade()")]
        for forbidden in ("drop_", "alter_column", "add_column", "execute(", "rename"):
            assert forbidden not in upgrade, forbidden

    def test_no_index_is_unique(self) -> None:
        assert "unique=True" not in MIGRATION.read_text()

    def test_the_orm_and_the_migration_agree_on_columns(self) -> None:
        from app.models.web_research import WebFetchAttempt, WebSearchQuery, WebSearchResult

        source = MIGRATION.read_text()
        for model in (WebSearchQuery, WebSearchResult, WebFetchAttempt):
            for column in model.__table__.columns:
                assert f'"{column.name}"' in source, f"{model.__tablename__}.{column.name}"


@requires_postgres
class TestOnRealPostgres:
    def test_upgrade_downgrade_upgrade_round_trip(self) -> None:
        _alembic("upgrade", "head")
        assert set(NEW_TABLES) <= _tables()
        _alembic("downgrade", "041")
        assert not (set(NEW_TABLES) & _tables()), "downgrade must drop exactly the new tables"
        assert {"research_jobs", "discovery_runs", "companies", "agent_runs"} <= _tables()
        _alembic("upgrade", "head")
        assert set(NEW_TABLES) <= _tables()

    def test_indexes_and_set_null_lineage_at_head(self) -> None:
        engine = sa.create_engine(_sync_url())
        try:
            inspector = sa.inspect(engine)
            indexes = {i["name"]: i for i in inspector.get_indexes("web_search_queries")}
            assert indexes["ix_web_search_queries_cache_key"]["column_names"] == [
                "request_hash",
                "provider",
                "created_at",
            ]
            assert "ix_web_search_queries_research_job_id" in indexes
            assert "ix_web_search_queries_discovery_run_id" in indexes
            result_idx = {i["name"] for i in inspector.get_indexes("web_search_results")}
            assert "ix_web_search_results_query_rank" in result_idx
            fks = {
                fk["referred_table"]: fk["options"].get("ondelete")
                for fk in inspector.get_foreign_keys("web_search_queries")
            }
            assert fks == {
                "research_jobs": "SET NULL",
                "discovery_runs": "SET NULL",
                "agent_runs": "SET NULL",
                "companies": "SET NULL",
                "web_search_queries": "SET NULL",  # served_from_query_id (review C5)
            }
        finally:
            engine.dispose()

    async def test_a_run_writes_provenance_and_survives_its_job(self) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.integrations.search.fake import FakeWebSearchProvider
        from app.models.research_job import ResearchJob
        from app.models.web_research import WebSearchQuery, WebSearchResult
        from app.services.providers.contracts import QueryFamily, SearchRequest
        from app.services.web_research.search import STATE_OK, SearchContext, run_searches

        cfg = SimpleNamespace(
            app_env="test",
            v3_web_search_enabled=True,
            v3_web_search_provider="fake",
            v3_web_search_max_queries_per_day=10_000,
            v3_run_max_web_searches=0,
        )
        engine = create_async_engine(POSTGRES_URL, future=True)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        job_id = uuid.uuid4()
        # A unique query per test run so the 24h cache on a shared database cannot
        # turn this run's network call into a cache serve.
        marker = uuid.uuid4().hex[:8]
        query = f"power transformer manufacturer listed company Europe {marker}"
        fake = FakeWebSearchProvider.from_fixture_dir(FIXTURES)
        fake.fixtures[query.casefold()] = fake.fixtures[
            "power transformer manufacturer listed company europe"
        ]
        try:
            async with maker() as s:
                s.add(
                    ResearchJob(
                        id=job_id,
                        job_type="company_research",
                        idempotency_key=f"w1-pg-{job_id}",
                        status="running",
                    )
                )
                await s.flush()
                result = await run_searches(
                    s,
                    [SearchRequest(query=query, family=QueryFamily.ENTITY)],
                    SearchContext(research_job_id=job_id),
                    provider=fake,
                    cfg=cfg,
                )
                await s.commit()
                assert result.state == STATE_OK
                query_id = result.outcomes[0].query_id

                again = await run_searches(
                    s,
                    [SearchRequest(query=query, family=QueryFamily.ENTITY)],
                    SearchContext(research_job_id=job_id),
                    provider=fake,
                    cfg=cfg,
                )
                await s.commit()
                assert again.outcomes[0].from_cache
                assert len(fake.requests) == 1

            async with maker() as s:
                row = await s.get(WebSearchQuery, query_id)
                assert row is not None and row.executed and row.network_call_count == 1
                assert row.filters_json["requested"]["topic"] == "general"
                count = await s.scalar(
                    sa.select(sa.func.count())
                    .select_from(WebSearchResult)
                    .where(WebSearchResult.query_id == query_id)
                )
                assert count == 4
                await s.execute(sa.delete(ResearchJob).where(ResearchJob.id == job_id))
                await s.commit()

            async with maker() as s:
                row = await s.get(WebSearchQuery, query_id)
                assert row is not None, "deleting a job never deletes its search audit"
                assert row.research_job_id is None
        finally:
            async with maker() as s:
                await s.execute(
                    sa.delete(WebSearchQuery).where(WebSearchQuery.query_text.contains(marker))
                )
                await s.execute(sa.delete(ResearchJob).where(ResearchJob.id == job_id))
                await s.commit()
            await engine.dispose()

    async def test_provenance_survives_the_callers_rollback(self) -> None:
        """Review C2: with its own session factory, a stage that fails and rolls back
        does not take the record of the calls it already made with it."""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.integrations.search.fake import FakeWebSearchProvider
        from app.models.web_research import WebSearchQuery
        from app.services.providers.contracts import QueryFamily, SearchRequest
        from app.services.web_research.search import SearchContext, run_searches

        cfg = SimpleNamespace(
            app_env="test",
            v3_web_search_enabled=True,
            v3_web_search_provider="fake",
            v3_web_search_max_queries_per_day=10_000,
            v3_run_max_web_searches=0,
        )
        engine = create_async_engine(POSTGRES_URL, future=True)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        marker = uuid.uuid4().hex[:8]
        try:
            async with maker() as caller:
                result = await run_searches(
                    caller,
                    [SearchRequest(query=f"grid transformer {marker}", family=QueryFamily.ENTITY)],
                    SearchContext(stage="c2"),
                    provider=FakeWebSearchProvider.from_fixture_dir(FIXTURES),
                    cfg=cfg,
                    persist_session_factory=maker,
                )
                await caller.rollback()
            async with maker() as s:
                row = await s.get(WebSearchQuery, result.outcomes[0].query_id)
                assert row is not None and row.executed and row.network_call_count == 1
        finally:
            async with maker() as s:
                await s.execute(
                    sa.delete(WebSearchQuery).where(WebSearchQuery.query_text.contains(marker))
                )
                await s.commit()
            await engine.dispose()
