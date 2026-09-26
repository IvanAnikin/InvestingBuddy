"""V3.19.4 on a real PostgreSQL: a dynamic run persists, re-reads and ranks.

SQLite with FKs off has hidden real outages before (a JSONB value that does not
serialise, a SAVEPOINT that aborts the shared transaction). This runs the whole
intent-driven run — create, dynamic stage, per-ticker scan — against PostgreSQL at head,
then reads the rows back in a FRESH session. Skipped when ``V3_TEST_POSTGRES_URL`` is unset.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.schemas.market_discovery import ThesisDiscoveryRunCreate
from app.services import market_discovery_service as mds
from tests.test_phase27_thesis_discovery import _fake_extractor
from tests.test_v3_19_4_dynamic_discovery import LUXURY, _fetch, _Provider

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head"
)


def _extractor():
    """The phase-27 fake, minus its invented report/run ids: on PostgreSQL those are real
    foreign keys, and a fake id is exactly what SQLite-with-FKs-off would have let pass."""
    import dataclasses

    inner = _fake_extractor()

    async def _extract(db, **kw):  # noqa: ANN001, ANN202
        signal = await inner(db, **kw)
        return dataclasses.replace(signal, analysis_report_id=None, agent_run_id=None)

    return _extract


@pytest.fixture
async def factory():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(POSTGRES_URL, future=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_dynamic_run_round_trips_on_postgres(factory, monkeypatch):
    from app.services.discovery import fx

    fx._CACHE.clear()
    monkeypatch.setattr(settings, "v3_dynamic_discovery_enabled", True)
    async with factory() as db:
        run = await mds.create_pending_thesis_run(
            db, ThesisDiscoveryRunCreate(thesis_text=LUXURY, provider_name="free_real")
        )
        run_id = run.id
        await mds.process_run(db, run, extractor=_extractor(),
                              discovery_provider=_Provider(), discovery_fetcher=_fetch)
    async with factory() as db:
        run = await db.get(DiscoveryRun, run_id)
        assert run.status in ("completed", "completed_with_warnings")
        stage = run.universe_json["dynamic"]
        assert stage["funnel"]["returned"] >= 1
        assert run.parsed_thesis_json["discovery_intent"]["schema"] == "discovery_intent/1"
        rows = (
            await db.execute(
                select(DiscoveryCandidate).where(DiscoveryCandidate.discovery_run_id == run_id)
            )
        ).scalars().all()
        ranked = sorted(rows, key=lambda r: r.rank or 99)
        assert ranked[0].ticker == "MEX"
        v319 = ranked[0].thesis_match_json["v319"]
        assert v319["verified_attributes"]["size_bucket"] == "small_cap"
        assert all(r.ticker != "KER" for r in rows)
        # Cleanup: this database is shared with the rest of the suite.
        for row in rows:
            await db.delete(row)
        await db.delete(run)
        await db.commit()
    fx._CACHE.clear()
