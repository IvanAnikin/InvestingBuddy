"""W6a — Discovery on the durable worker, against real PostgreSQL.

The SQLite suite (``test_v3_discovery_durable.py``) proves the logic. This file
proves the parts only real SQL has: the UNIQUE idempotency key under a genuine
race, real foreign keys from a candidate to its run, ``FOR UPDATE`` on the run
row in the reconciliation write, and the sweep reading ``payload_json`` as JSONB.
SQLite with FKs off has hidden a production outage in this repository before.

Skipped without ``V3_TEST_POSTGRES_URL`` (a database at head 041 or later).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.models.research_job import ResearchJob
from app.services import market_discovery_service as mds
from app.services.jobs import discovery_research_job as djob
from app.services.jobs import job_contract as contract
from app.services.jobs import worker as worker_mod
from app.services.jobs.job_store import JobStore
from app.services.jobs.worker import HandlerRegistry, ResearchWorker

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 041"
)

TICKERS = ["PGA", "PGB", "PGC", "PGD"]


@pytest.fixture
async def factory():  # noqa: ANN201
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(POSTGRES_URL, future=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _isolate(factory, monkeypatch):  # noqa: ANN001, ANN201
    """Wire the session factory, keep one observer, and clear stray queue rows.

    ``claim_next`` is global by design and the scratch database persists across
    runs, so a discovery job left non-terminal by an earlier, interrupted run
    would be claimed instead of this test's. Retiring them is safe: this is a
    test database.
    """
    import app.db.session as session_mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "v3_durable_jobs_enabled", True)
    monkeypatch.setattr(session_mod, "async_session_factory", factory)
    monkeypatch.setattr(mds, "async_session_factory", factory)
    saved = list(worker_mod._terminal_observers)
    worker_mod.clear_terminal_observers()
    worker_mod.register_terminal_observer(djob.reconcile_discovery_run)
    async with factory() as s:
        await s.execute(
            sa.update(ResearchJob)
            .where(
                ResearchJob.job_type == djob.JOB_TYPE,
                ResearchJob.status.in_(["pending", "running"]),
            )
            .values(status="cancelled", lease_owner=None, lease_expires_at=None)
        )
        await s.commit()
    yield
    worker_mod._terminal_observers[:] = saved


@pytest.fixture
def store(factory):  # noqa: ANN001, ANN201
    return JobStore(factory, lease_seconds=60, max_attempts=3)


class FakeExtractor:
    def __init__(self, *, on_call=None):  # noqa: ANN001
        self.calls: list[str] = []
        self.on_call = on_call

    async def __call__(self, db, *, ticker, exchange, provider_name, lookback_days, **_):  # noqa: ANN001, ANN003, ANN204
        self.calls.append(ticker)
        if self.on_call is not None:
            await self.on_call(ticker)
        signal = {
            "ticker": ticker,
            "exchange": exchange,
            "provider_name": provider_name,
            "identity": {"company_name": f"{ticker} plc"},
            "trend": {},
            "fundamentals": {},
            "market": {},
            "catalyst": {},
            "source_quality": {},
            "completeness": {},
            "warnings": [],
        }
        # No report / agent-run ids: on PostgreSQL those are real foreign keys.
        return mds.ExtractedSignal(
            ticker=ticker,
            exchange=exchange,
            provider_name=provider_name,
            signal=signal,
            status="ok",
            error=None,
            analysis_report_id=None,
            agent_run_id=None,
            schema_valid=False,
            safety_valid=True,
        )


async def _make_run(factory, tickers=TICKERS) -> uuid.UUID:  # noqa: ANN001
    run = DiscoveryRun(
        id=uuid.uuid4(),
        status="pending",
        provider_name="free_real",
        universe_source="manual_tickers",
        universe_count=len(tickers),
        requested_tickers=list(tickers),
        processed_count=0,
        candidate_count=0,
        error_count=0,
        lookback_days=90,
        warnings=[],
        config_json={"provider_name": "free_real", "exchange": "LSE", "lookback_days": 90},
        safety_notes={"internal_only": True},
        human_review_required=True,
    )
    async with factory() as s:
        s.add(run)
        await s.commit()
    return run.id


async def _run(factory, run_id) -> DiscoveryRun:  # noqa: ANN001
    async with factory() as s:
        return (
            await s.execute(sa.select(DiscoveryRun).where(DiscoveryRun.id == run_id))
        ).scalar_one()


async def _tickers(factory, run_id) -> list[str]:  # noqa: ANN001
    async with factory() as s:
        rows = await s.execute(
            sa.select(DiscoveryCandidate.ticker).where(
                DiscoveryCandidate.discovery_run_id == run_id
            )
        )
        return sorted(r[0] for r in rows)


async def _job(factory, job_id) -> ResearchJob:  # noqa: ANN001
    async with factory() as s:
        return (
            await s.execute(
                sa.select(ResearchJob).where(ResearchJob.id == uuid.UUID(str(job_id)))
            )
        ).scalar_one()


async def _set_job(factory, job_id, **values) -> None:  # noqa: ANN001, ANN003
    async with factory() as s:
        await s.execute(
            sa.update(ResearchJob)
            .where(ResearchJob.id == uuid.UUID(str(job_id)))
            .values(**values)
        )
        await s.commit()


def _worker(store, owner: str) -> ResearchWorker:  # noqa: ANN001
    reg = HandlerRegistry()
    reg.register(djob.JOB_TYPE, djob.run_discovery_research_job)
    return ResearchWorker(
        store,
        handlers=reg,
        owner=owner,
        job_types=[djob.JOB_TYPE],
        lease_seconds=60,
        heartbeat_seconds=30,
        poll_interval_seconds=0.01,
    )


def _past() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=1)


async def test_concurrent_submits_of_one_run_yield_one_job(factory, store):  # noqa: ANN001
    """THE RACE SQLite cannot run: ten simultaneous submits, one job row."""
    run = await _run(factory, await _make_run(factory))
    results = await asyncio.gather(*(djob.submit(run, store=store) for _ in range(10)))
    ids = {view.id for view, _ in results}
    assert len(ids) == 1
    assert sum(1 for _, created in results if created) == 1
    async with factory() as s:
        count = (
            await s.execute(
                sa.select(sa.func.count(ResearchJob.id)).where(
                    ResearchJob.idempotency_key.like(f"discovery_research:{run.id}%")
                )
            )
        ).scalar()
    assert count == 1


async def test_a_killed_worker_resumes_on_postgres_without_duplicates(
    factory, store, monkeypatch  # noqa: ANN001
):
    run_id = await _make_run(factory)
    view, _ = await djob.submit(await _run(factory, run_id), store=store)
    state = {"kill": True}

    async def die_on_pgc(ticker: str) -> None:
        if state["kill"] and ticker == "PGC":
            raise asyncio.CancelledError()

    fake = FakeExtractor(on_call=die_on_pgc)
    monkeypatch.setattr(mds, "extract_signal", fake)

    with pytest.raises(asyncio.CancelledError):
        await _worker(store, "pg-w1").run_once()
    assert (await _job(factory, view.id)).status == contract.STATUS_RUNNING

    state["kill"] = False
    await _set_job(factory, view.id, lease_expires_at=_past())
    await _worker(store, "pg-w2").run_once()

    run = await _run(factory, run_id)
    assert run.status == "completed"
    assert run.processed_count == 4 and run.candidate_count == 4
    assert await _tickers(factory, run_id) == TICKERS
    assert fake.calls == ["PGA", "PGB", "PGC", "PGC", "PGD"]
    job = await _job(factory, view.id)
    assert job.status == contract.STATUS_COMPLETED
    assert job.attempt == 2
    assert job.result_ref == run_id


async def test_dead_letter_fails_the_run_through_the_observer(factory, monkeypatch):  # noqa: ANN001
    store = JobStore(factory, lease_seconds=60, max_attempts=1)
    run_id = await _make_run(factory)
    view, _ = await djob.submit(await _run(factory, run_id), store=store)

    def down(signal):  # noqa: ANN001, ANN202
        raise ConnectionError("provider down")

    monkeypatch.setattr(mds, "score_signal", down)
    monkeypatch.setattr(mds, "extract_signal", FakeExtractor())

    await _worker(store, "pg-w1").run_once()

    assert (await _job(factory, view.id)).status == contract.STATUS_DEAD_LETTER
    run = await _run(factory, run_id)
    assert run.status == "failed"
    assert any("gave up after 1 attempts" in w for w in run.warnings)


async def test_cancel_at_a_boundary_cancels_the_run(factory, store, monkeypatch):  # noqa: ANN001
    run_id = await _make_run(factory)
    view, _ = await djob.submit(await _run(factory, run_id), store=store)

    async def cancel_on_pgb(ticker: str) -> None:
        if ticker == "PGB":
            await store.request_cancel(view.id)

    monkeypatch.setattr(mds, "extract_signal", FakeExtractor(on_call=cancel_on_pgb))
    await _worker(store, "pg-w1").run_once()

    assert (await _run(factory, run_id)).status == "cancelled"
    assert await _tickers(factory, run_id) == ["PGA", "PGB"]
    assert (await _job(factory, view.id)).status == contract.STATUS_CANCELLED


async def test_the_sweep_reads_jsonb_payloads_and_repairs_an_abandoned_run(
    factory, store  # noqa: ANN001
):
    run_id = await _make_run(factory)
    view, _ = await djob.submit(await _run(factory, run_id), store=store)
    # A worker killed on every attempt, retired on the claim path: no observer ran.
    await _set_job(
        factory,
        view.id,
        status=contract.STATUS_DEAD_LETTER,
        attempt=3,
        finished_at=datetime.now(timezone.utc),
        dead_letter_reason="abandoned",
    )
    async with factory() as s:
        await s.execute(
            sa.update(DiscoveryRun).where(DiscoveryRun.id == run_id).values(status="running")
        )
        await s.commit()

    await djob.reconcile_orphaned_discovery_runs()

    run = await _run(factory, run_id)
    assert run.status == "failed"
    assert any("gave up after 3 attempts" in w for w in run.warnings)
    # Idempotent: a second pass finds nothing open and changes nothing.
    await djob.reconcile_orphaned_discovery_runs()
    assert len((await _run(factory, run_id)).warnings) == len(run.warnings)
