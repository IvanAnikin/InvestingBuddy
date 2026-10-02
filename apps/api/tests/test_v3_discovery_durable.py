"""W6a — Discovery on the durable worker.

The product requirement: *a user starts Discovery, leaves the page, returns later
and sees the same run; a page reload must not cancel work.* These tests pin the
properties that make it true, against a real (file-backed SQLite) database and the
REAL worker loop — a mocked session would let the resume cursor and the guarded
writes pass while being wrong.

  * flag on  -> a ``discovery_research`` job is enqueued and no BackgroundTask runs;
  * flag off -> today's BackgroundTask path, byte for byte;
  * the handler completes the run;
  * a crash after k tickers -> the retry RESUMES: no duplicate candidate, the
    counts continue, the done tickers are never extracted again;
  * a retry of a run left ``running`` with a fresh ``started_at`` still processes
    (the 30-minute BackgroundTasks guard would have made it a silent no-op);
  * a terminal run is a no-op;
  * cancellation -> run ``cancelled``; dead letter / permanent failure -> run
    ``failed`` via the terminal observer; what no observer sees -> the sweep;
  * a lost lease stops the scan without a terminal write;
  * a double enqueue joins the existing job;
  * the heartbeat keeps the lease alive during a slow extraction.

Offline: the signal extractor is a fake; nothing touches the network or an LLM.
The PostgreSQL variant is ``test_v3_discovery_durable_postgres.py``.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import research_job as _research_job  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import screening as _screening  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.models.research_job import ResearchJob
from app.services import market_discovery_service as mds
from app.services.jobs import discovery_research_job as djob
from app.services.jobs import job_contract as contract
from app.services.jobs import worker as worker_mod
from app.services.jobs.job_store import JobStore
from app.services.jobs.worker import HandlerRegistry, LeaseLostError, ResearchWorker

_NOW = datetime.now(timezone.utc)
TICKERS = ["AAA", "BBB", "CCC", "DDD", "EEE"]


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _sqlite_wal(engine):  # noqa: ANN001
    """Reader/writer coexistence: the heartbeat writes while the handler writes."""
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=OFF")
        cursor.close()


@pytest.fixture
async def engine(tmp_path):  # noqa: ANN001, ANN201
    # File-backed, not :memory: — two sessions are genuinely concurrent here (see
    # test_v3_worker_executor.py for the full reasoning).
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/w6a.db",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    _sqlite_wal(eng)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def factory(engine):  # noqa: ANN001, ANN201
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _wire_session_factory(factory, monkeypatch):  # noqa: ANN001
    """Every call-time session lookup resolves to the test database."""
    import app.db.session as session_mod

    monkeypatch.setattr(session_mod, "async_session_factory", factory)
    monkeypatch.setattr(mds, "async_session_factory", factory)


@pytest.fixture(autouse=True)
def _only_our_observer():
    """Observers are process-global; keep exactly the one under test."""
    saved = list(worker_mod._terminal_observers)
    worker_mod.clear_terminal_observers()
    worker_mod.register_terminal_observer(djob.reconcile_discovery_run)
    yield
    worker_mod._terminal_observers[:] = saved


@pytest.fixture
def durable_on(monkeypatch):  # noqa: ANN001, ANN201
    from app.core.config import settings

    monkeypatch.setattr(settings, "v3_durable_jobs_enabled", True)
    monkeypatch.setattr(settings, "v3_discovery_durable_enabled", True)
    return settings


@pytest.fixture
def store(factory):  # noqa: ANN001, ANN201
    return JobStore(factory, lease_seconds=120, max_attempts=3)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _signal(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "exchange": "US",
        "provider_name": "free_real",
        "is_mock": False,
        "provider_failed": False,
        "error": None,
        "identity": {"company_name": f"{ticker} Inc.", "sector": "Technology"},
        "trend": {"momentum_label": "positive_momentum_candidate", "return_3m": 5.0},
        "fundamentals": {"available": True, "revenue_mln": 100.0},
        "market": {"latest_close": 10.0, "market_cap_mln": 500.0},
        "catalyst": {"coverage_status": "strong", "total_events": 2},
        "source_quality": {"overall": "strong"},
        "completeness": {"missing_fields": [], "missing_info_count": 0},
        "warnings": [],
    }


class FakeExtractor:
    """Records every call; can raise or stall on a chosen ticker."""

    def __init__(self, *, on_call=None):  # noqa: ANN001
        self.calls: list[str] = []
        self.on_call = on_call

    async def __call__(self, db, *, ticker, exchange, provider_name, lookback_days, **_):  # noqa: ANN001, ANN003, ANN204
        self.calls.append(ticker)
        if self.on_call is not None:
            await self.on_call(ticker)
        return mds.ExtractedSignal(
            ticker=ticker,
            exchange=exchange,
            provider_name=provider_name,
            signal=_signal(ticker),
            status="ok",
            error=None,
            analysis_report_id=None,
            agent_run_id=None,
            schema_valid=False,
            safety_valid=True,
        )


@pytest.fixture
def extractor(monkeypatch) -> FakeExtractor:  # noqa: ANN001
    fake = FakeExtractor()
    monkeypatch.setattr(mds, "extract_signal", fake)
    return fake


async def _make_run(factory, tickers=TICKERS, *, status="pending", **over) -> uuid.UUID:  # noqa: ANN001
    run = DiscoveryRun(
        id=uuid.uuid4(),
        status=status,
        provider_name="free_real",
        universe_source="manual_tickers",
        universe_count=len(tickers),
        requested_tickers=list(tickers),
        processed_count=0,
        candidate_count=0,
        error_count=0,
        lookback_days=90,
        warnings=[],
        config_json={"provider_name": "free_real", "exchange": "US", "lookback_days": 90},
        safety_notes={"internal_only": True},
        human_review_required=True,
    )
    for k, v in over.items():
        setattr(run, k, v)
    async with factory() as s:
        s.add(run)
        await s.commit()
    return run.id


async def _run(factory, run_id) -> DiscoveryRun:  # noqa: ANN001
    async with factory() as s:
        return (
            await s.execute(
                sa.select(DiscoveryRun)
                .where(DiscoveryRun.id == run_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()


async def _candidate_tickers(factory, run_id) -> list[str]:  # noqa: ANN001
    async with factory() as s:
        rows = await s.execute(
            sa.select(DiscoveryCandidate.ticker).where(
                DiscoveryCandidate.discovery_run_id == run_id
            )
        )
        return sorted(r[0] for r in rows)


async def _job_row(factory, job_id) -> ResearchJob:  # noqa: ANN001
    async with factory() as s:
        return (
            await s.execute(
                sa.select(ResearchJob)
                .where(ResearchJob.id == uuid.UUID(str(job_id)))
                .execution_options(populate_existing=True)
            )
        ).scalar_one()


async def _make_due_now(factory, job_id) -> None:  # noqa: ANN001
    """Stand in for the retry backoff elapsing. No sleeping in tests."""
    async with factory() as s:
        await s.execute(
            sa.update(ResearchJob)
            .where(ResearchJob.id == uuid.UUID(str(job_id)))
            .values(available_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await s.commit()


async def _expire_lease(factory, job_id) -> None:  # noqa: ANN001
    """Stand in for a killed worker's lease lapsing."""
    async with factory() as s:
        await s.execute(
            sa.update(ResearchJob)
            .where(ResearchJob.id == uuid.UUID(str(job_id)))
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await s.commit()


def _worker(store, *, owner="w1", **kw) -> ResearchWorker:  # noqa: ANN001
    reg = HandlerRegistry()
    reg.register(djob.JOB_TYPE, djob.run_discovery_research_job)
    return ResearchWorker(
        store,
        handlers=reg,
        owner=owner,
        lease_seconds=kw.pop("lease_seconds", 120),
        heartbeat_seconds=kw.pop("heartbeat_seconds", 30),
        poll_interval_seconds=0.01,
        **kw,
    )


async def _wait_until(predicate, *, timeout: float = 10.0) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached within the timeout")
        await asyncio.sleep(0.005)


# ===========================================================================
# Flags and the endpoints
# ===========================================================================


def _api_run(run_id: uuid.UUID, *, mode: str = "ticker") -> DiscoveryRun:
    run = DiscoveryRun(
        id=run_id,
        status="pending",
        mode=mode,
        provider_name="free_real",
        universe_source="manual_tickers" if mode == "ticker" else "thesis_generated",
        universe_count=2,
        requested_tickers=["AAA", "BBB"],
        processed_count=0,
        candidate_count=0,
        error_count=0,
        lookback_days=90,
        warnings=[],
        config_json={},
        safety_notes={},
        human_review_required=True,
    )
    run.created_at = _NOW
    run.updated_at = _NOW
    return run


class TestFlags:
    def test_both_flags_are_required(self, monkeypatch):  # noqa: ANN001
        from app.core.config import settings

        monkeypatch.setattr(settings, "v3_durable_jobs_enabled", True)
        monkeypatch.setattr(settings, "v3_discovery_durable_enabled", False)
        assert djob.durable_enabled() is False
        monkeypatch.setattr(settings, "v3_durable_jobs_enabled", False)
        monkeypatch.setattr(settings, "v3_discovery_durable_enabled", True)
        assert djob.durable_enabled() is False
        monkeypatch.setattr(settings, "v3_durable_jobs_enabled", True)
        assert djob.durable_enabled() is True

    def test_discovery_flag_defaults_off(self):
        from app.core.config import Settings

        assert Settings.model_fields["v3_discovery_durable_enabled"].default is False

    def test_the_handler_is_registered_by_the_handlers_module(self):
        """Unconditionally — an enqueued job must drain after the flag is turned off."""
        import app.services.jobs.handlers  # noqa: F401

        assert djob.JOB_TYPE in worker_mod.registry


class TestEndpoints:
    async def test_flag_on_enqueues_and_schedules_no_background_task(
        self, client, durable_on, factory  # noqa: ANN001
    ):
        run = _api_run(uuid.uuid4())
        with patch.object(mds, "create_pending_run", AsyncMock(return_value=run)), patch.object(
            mds, "process_discovery_run_task", AsyncMock()
        ) as task:
            res = await client.post(
                "/api/v1/market-discovery/runs", json={"universe_source": "curated_seed"}
            )
        assert res.status_code == 201
        body = res.json()
        assert body["status"] == "pending"
        task.assert_not_awaited()
        async with factory() as s:
            jobs = (await s.execute(sa.select(ResearchJob))).scalars().all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.job_type == "discovery_research"
        assert job.idempotency_key.startswith(f"discovery_research:{run.id}")
        assert job.payload_json == {"discovery_run_id": str(run.id)}
        assert job.company_id is None
        assert body["job"] == {
            "job_id": str(job.id),
            "job_status": "pending",
            "attempt": 0,
            "max_attempts": job.max_attempts,
        }

    async def test_thesis_run_flag_on_enqueues(self, client, durable_on, factory):  # noqa: ANN001
        run = _api_run(uuid.uuid4(), mode="thesis")
        with patch.object(
            mds, "create_pending_thesis_run", AsyncMock(return_value=run)
        ), patch.object(mds, "process_discovery_run_task", AsyncMock()) as task:
            res = await client.post(
                "/api/v1/market-discovery/thesis-runs",
                json={"thesis_text": "European luxury goods companies"},
            )
        assert res.status_code == 201, res.text
        task.assert_not_awaited()
        async with factory() as s:
            keys = (await s.execute(sa.select(ResearchJob.idempotency_key))).scalars().all()
        assert [k.split("#")[0] for k in keys] == [f"discovery_research:{run.id}"]

    @pytest.mark.parametrize(
        ("master", "discovery"), [(False, False), (True, False), (False, True)]
    )
    async def test_flag_off_keeps_the_background_task(
        self, client, monkeypatch, factory, master, discovery  # noqa: ANN001
    ):
        from app.core.config import settings

        monkeypatch.setattr(settings, "v3_durable_jobs_enabled", master)
        monkeypatch.setattr(settings, "v3_discovery_durable_enabled", discovery)
        run = _api_run(uuid.uuid4())
        with patch.object(mds, "create_pending_run", AsyncMock(return_value=run)), patch.object(
            mds, "process_discovery_run_task", AsyncMock()
        ) as task:
            res = await client.post(
                "/api/v1/market-discovery/runs", json={"universe_source": "curated_seed"}
            )
        assert res.status_code == 201
        assert res.json()["job"] is None
        task.assert_awaited_once_with(str(run.id))
        async with factory() as s:
            assert (await s.execute(sa.select(sa.func.count(ResearchJob.id)))).scalar() == 0

    async def test_enqueue_failure_falls_back_to_the_background_task(
        self, client, durable_on, caplog  # noqa: ANN001
    ):
        run = _api_run(uuid.uuid4())
        with patch.object(mds, "create_pending_run", AsyncMock(return_value=run)), patch.object(
            mds, "process_discovery_run_task", AsyncMock()
        ) as task, patch.object(djob, "submit", AsyncMock(side_effect=OSError("db down"))):
            res = await client.post(
                "/api/v1/market-discovery/runs", json={"universe_source": "curated_seed"}
            )
        assert res.status_code == 201
        assert res.json()["status"] == "pending"
        assert res.json()["job"] is None
        task.assert_awaited_once_with(str(run.id))
        assert "discovery_durable_enqueue_failed" in caplog.text

    async def test_get_run_carries_the_job_block_when_durable(
        self, client, durable_on, store  # noqa: ANN001
    ):
        run = _api_run(uuid.uuid4())
        view, _ = await djob.submit(run, store=store)
        with patch.object(mds, "get_run", AsyncMock(return_value=run)):
            res = await client.get(f"/api/v1/market-discovery/runs/{run.id}")
        assert res.status_code == 200
        assert res.json()["job"]["job_id"] == view.id
        assert res.json()["job"]["job_status"] == "pending"
        assert "lease_owner" not in res.json()["job"]

    async def test_get_run_with_durable_off_never_reads_the_job_store(
        self, client, monkeypatch  # noqa: ANN001
    ):
        from app.core.config import settings

        monkeypatch.setattr(settings, "v3_durable_jobs_enabled", False)
        run = _api_run(uuid.uuid4())
        with patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
            JobStore, "latest_for_base_key", AsyncMock(side_effect=AssertionError("read"))
        ):
            res = await client.get(f"/api/v1/market-discovery/runs/{run.id}")
        assert res.status_code == 200
        assert res.json()["job"] is None


# ===========================================================================
# Submission
# ===========================================================================


class TestSubmit:
    async def test_a_double_enqueue_joins_the_existing_job(self, factory, store):  # noqa: ANN001
        run_id = await _make_run(factory)
        run = await _run(factory, run_id)
        first, created_1 = await djob.submit(run, store=store)
        second, created_2 = await djob.submit(run, store=store)
        assert created_1 is True
        assert created_2 is False
        assert second.id == first.id
        async with factory() as s:
            assert (await s.execute(sa.select(sa.func.count(ResearchJob.id)))).scalar() == 1


# ===========================================================================
# The handler
# ===========================================================================


class TestHandler:
    async def test_the_handler_completes_the_run(self, factory, store, extractor):  # noqa: ANN001
        run_id = await _make_run(factory)
        view, _ = await djob.submit(await _run(factory, run_id), store=store)

        assert await _worker(store).run_once() is True

        run = await _run(factory, run_id)
        assert run.status == "completed"
        assert run.processed_count == 5
        assert run.candidate_count == 5
        assert await _candidate_tickers(factory, run_id) == TICKERS
        assert extractor.calls == TICKERS
        job = await _job_row(factory, view.id)
        assert job.status == contract.STATUS_COMPLETED
        assert job.result_type == "discovery_run"
        assert job.result_ref == run_id
        assert job.stage == mds.STAGE_SCANNING

    async def test_a_terminal_run_is_a_successful_no_op(self, factory, store, extractor):  # noqa: ANN001
        run_id = await _make_run(factory, status="completed")
        view, _ = await djob.submit(await _run(factory, run_id), store=store)

        await _worker(store).run_once()

        assert extractor.calls == []
        assert (await _run(factory, run_id)).status == "completed"
        assert (await _job_row(factory, view.id)).status == contract.STATUS_COMPLETED

    async def test_a_running_run_with_a_fresh_started_at_is_still_processed(
        self, factory, store, extractor  # noqa: ANN001
    ):
        """The 30-minute guard would make this retry a silent no-op."""
        run_id = await _make_run(
            factory, status="running", started_at=datetime.now(timezone.utc)
        )
        await djob.submit(await _run(factory, run_id), store=store)

        await _worker(store).run_once()

        run = await _run(factory, run_id)
        assert run.status == "completed"
        assert extractor.calls == TICKERS

    async def test_the_v2_path_still_honours_the_running_guard(self, factory, extractor):  # noqa: ANN001
        """owned_by_lease is the ONLY thing that bypasses it."""
        run_id = await _make_run(
            factory, status="running", started_at=datetime.now(timezone.utc)
        )
        await mds.process_discovery_run_by_id(run_id, session_factory=factory)
        assert (await _run(factory, run_id)).status == "running"
        assert extractor.calls == []

    async def test_a_missing_run_fails_permanently(self, factory, store):  # noqa: ANN001
        view, _ = await store.enqueue(
            job_type=djob.JOB_TYPE,
            idempotency_key=djob.dedup_key(uuid.uuid4()),
            payload={"discovery_run_id": str(uuid.uuid4())},
        )
        await _worker(store).run_once()
        job = await _job_row(factory, view.id)
        assert job.status == contract.STATUS_FAILED
        assert job.error_class == "DiscoveryRunMissing"


# ===========================================================================
# Resume
# ===========================================================================


class TestResume:
    async def test_a_transient_crash_after_k_tickers_resumes_without_duplicates(
        self, factory, store, extractor, monkeypatch  # noqa: ANN001
    ):
        """A transient error escaping the loop (a dropped DB connection, say) after
        two tickers. The retry must continue at ticker three."""
        run_id = await _make_run(factory)
        view, _ = await djob.submit(await _run(factory, run_id), store=store)

        real_score = mds.score_signal
        state = {"armed": True}

        def flaky_score(signal):  # noqa: ANN001, ANN202
            if state["armed"] and signal["ticker"] == "CCC":
                raise ConnectionError("connection reset")
            return real_score(signal)

        monkeypatch.setattr(mds, "score_signal", flaky_score)

        await _worker(store).run_once()
        job = await _job_row(factory, view.id)
        assert job.status == contract.STATUS_PENDING, "a transient error must retry"
        mid = await _run(factory, run_id)
        assert mid.status == "running", "a retryable crash must not finish the run"
        assert mid.processed_count == 2
        assert await _candidate_tickers(factory, run_id) == ["AAA", "BBB"]

        state["armed"] = False
        await _make_due_now(factory, view.id)
        await _worker(store, owner="w2").run_once()

        run = await _run(factory, run_id)
        assert run.status == "completed"
        assert await _candidate_tickers(factory, run_id) == TICKERS, "duplicate or lost candidate"
        assert run.processed_count == 5
        assert run.candidate_count == 5
        assert run.error_count == 0
        # AAA and BBB were extracted ONCE; CCC twice (it never committed).
        assert extractor.calls == ["AAA", "BBB", "CCC", "CCC", "DDD", "EEE"]
        job = await _job_row(factory, view.id)
        assert job.status == contract.STATUS_COMPLETED
        assert job.attempt == 2

    async def test_a_killed_worker_is_reclaimed_and_the_scan_resumes(
        self, factory, store, monkeypatch  # noqa: ANN001
    ):
        """The process dies mid-ticker (a recycle). Nothing is written; the lease
        lapses; the next worker reclaims and continues."""
        run_id = await _make_run(factory)
        view, _ = await djob.submit(await _run(factory, run_id), store=store)

        state = {"kill": True}

        async def die_on_ddd(ticker: str) -> None:
            if state["kill"] and ticker == "DDD":
                raise asyncio.CancelledError()

        fake = FakeExtractor(on_call=die_on_ddd)
        monkeypatch.setattr(mds, "extract_signal", fake)

        with pytest.raises(asyncio.CancelledError):
            await _worker(store).run_once()
        assert (await _job_row(factory, view.id)).status == contract.STATUS_RUNNING
        assert (await _run(factory, run_id)).processed_count == 3

        state["kill"] = False
        await _expire_lease(factory, view.id)
        await _worker(store, owner="w2").run_once()

        run = await _run(factory, run_id)
        assert run.status == "completed"
        assert await _candidate_tickers(factory, run_id) == TICKERS
        assert run.processed_count == 5 and run.candidate_count == 5
        assert fake.calls == ["AAA", "BBB", "CCC", "DDD", "DDD", "EEE"]
        assert (await _job_row(factory, view.id)).attempt == 2

    async def test_an_extraction_error_before_the_crash_is_not_rerun_or_recounted(
        self, factory, store, monkeypatch  # noqa: ANN001
    ):
        """A ticker whose extraction RAISED left no candidate — but it was processed.
        A ticker-set resume would run it again and count its error twice."""
        run_id = await _make_run(factory)
        view, _ = await djob.submit(await _run(factory, run_id), store=store)

        state = {"kill": True}

        async def behave(ticker: str) -> None:
            if ticker == "AAA":
                raise RuntimeError("provider exploded")
            if state["kill"] and ticker == "CCC":
                raise asyncio.CancelledError()

        fake = FakeExtractor(on_call=behave)
        monkeypatch.setattr(mds, "extract_signal", fake)

        with pytest.raises(asyncio.CancelledError):
            await _worker(store).run_once()
        state["kill"] = False
        await _expire_lease(factory, view.id)
        await _worker(store, owner="w2").run_once()

        run = await _run(factory, run_id)
        assert fake.calls.count("AAA") == 1
        assert run.processed_count == 5
        assert run.error_count == 1
        assert run.candidate_count == 4
        assert run.status == "completed_with_warnings"
        assert await _candidate_tickers(factory, run_id) == ["BBB", "CCC", "DDD", "EEE"]

    async def test_resumed_candidates_are_ranked_with_the_new_ones(
        self, factory, store, monkeypatch  # noqa: ANN001
    ):
        run_id = await _make_run(factory)
        view, _ = await djob.submit(await _run(factory, run_id), store=store)
        state = {"kill": True}

        async def die(ticker: str) -> None:
            if state["kill"] and ticker == "CCC":
                raise asyncio.CancelledError()

        monkeypatch.setattr(mds, "extract_signal", FakeExtractor(on_call=die))
        with pytest.raises(asyncio.CancelledError):
            await _worker(store).run_once()
        state["kill"] = False
        await _expire_lease(factory, view.id)
        await _worker(store, owner="w2").run_once()

        async with factory() as s:
            ranks = sorted(
                (
                    await s.execute(
                        sa.select(DiscoveryCandidate.rank).where(
                            DiscoveryCandidate.discovery_run_id == run_id
                        )
                    )
                ).scalars()
            )
        assert ranks == [1, 2, 3, 4, 5]


# ===========================================================================
# Lease loss and cancellation
# ===========================================================================


class TestStops:
    async def test_lease_loss_stops_the_scan_without_a_terminal_write(
        self, factory, extractor  # noqa: ANN001
    ):
        """``progress`` sits OUTSIDE the per-ticker try: a lost lease must stop the
        scan, not be recorded as one ticker's extraction error."""
        run_id = await _make_run(factory)
        calls = {"n": 0}

        async def progress(stage):  # noqa: ANN001, ANN202
            calls["n"] += 1
            if calls["n"] == 3:
                raise LeaseLostError("gone")

        async with factory() as s:
            run = await mds.get_run(s, run_id)
            with pytest.raises(LeaseLostError):
                await mds.process_run(s, run, owned_by_lease=True, progress=progress)

        run = await _run(factory, run_id)
        assert run.status == "running"
        assert run.completed_at is None
        assert run.processed_count == 2
        assert run.error_count == 0
        assert extractor.calls == ["AAA", "BBB"]

    async def test_cancel_at_a_ticker_boundary_cancels_the_run(
        self, factory, store, monkeypatch  # noqa: ANN001
    ):
        run_id = await _make_run(factory)
        view, _ = await djob.submit(await _run(factory, run_id), store=store)

        async def cancel_on_bbb(ticker: str) -> None:
            if ticker == "BBB":
                await store.request_cancel(view.id)

        fake = FakeExtractor(on_call=cancel_on_bbb)
        monkeypatch.setattr(mds, "extract_signal", fake)

        await _worker(store).run_once()

        run = await _run(factory, run_id)
        assert run.status == "cancelled"
        assert run.completed_at is not None
        assert any("cancelled" in w for w in run.warnings)
        assert fake.calls == ["AAA", "BBB"]
        assert await _candidate_tickers(factory, run_id) == ["AAA", "BBB"]
        assert (await _job_row(factory, view.id)).status == contract.STATUS_CANCELLED

    async def test_a_pending_job_cancelled_outright_is_reconciled_by_the_sweep(
        self, factory, store, durable_on  # noqa: ANN001
    ):
        run_id = await _make_run(factory)
        view, _ = await djob.submit(await _run(factory, run_id), store=store)
        await store.request_cancel(view.id)
        assert (await _run(factory, run_id)).status == "pending"

        await djob.reconcile_orphaned_discovery_runs()

        assert (await _run(factory, run_id)).status == "cancelled"


# ===========================================================================
# Terminal failures
# ===========================================================================


class TestTerminalFailures:
    async def test_dead_letter_marks_the_run_failed_via_the_observer(
        self, factory, monkeypatch  # noqa: ANN001
    ):
        store = JobStore(factory, lease_seconds=120, max_attempts=2)
        run_id = await _make_run(factory)
        view, _ = await djob.submit(await _run(factory, run_id), store=store)

        def always_down(signal):  # noqa: ANN001, ANN202
            raise ConnectionError("provider down")

        monkeypatch.setattr(mds, "score_signal", always_down)
        monkeypatch.setattr(mds, "extract_signal", FakeExtractor())

        await _worker(store).run_once()
        assert (await _run(factory, run_id)).status == "running", "retry pending"
        await _make_due_now(factory, view.id)
        await _worker(store, owner="w2").run_once()

        job = await _job_row(factory, view.id)
        assert job.status == contract.STATUS_DEAD_LETTER
        run = await _run(factory, run_id)
        assert run.status == "failed"
        assert run.completed_at is not None
        assert any("gave up after 2 attempts" in w for w in run.warnings)

    async def test_a_permanent_failure_marks_the_run_failed(self, factory, store, monkeypatch):  # noqa: ANN001
        run_id = await _make_run(factory)
        view, _ = await djob.submit(await _run(factory, run_id), store=store)

        def bug(signal):  # noqa: ANN001, ANN202
            raise ValueError("a bug")

        monkeypatch.setattr(mds, "score_signal", bug)
        monkeypatch.setattr(mds, "extract_signal", FakeExtractor())

        await _worker(store).run_once()

        assert (await _job_row(factory, view.id)).status == contract.STATUS_FAILED
        run = await _run(factory, run_id)
        assert run.status == "failed"
        assert any("permanent error (ValueError)" in w for w in run.warnings)

    async def test_an_abandoned_job_is_reconciled_by_the_sweep(
        self, factory, monkeypatch, durable_on  # noqa: ANN001
    ):
        """Killed on every attempt: dead-lettered on the CLAIM path, which notifies
        no observer. Without the sweep the run reads ``running`` for ever."""
        store = JobStore(factory, lease_seconds=120, max_attempts=1)
        run_id = await _make_run(factory)
        view, _ = await djob.submit(await _run(factory, run_id), store=store)

        async def die(ticker: str) -> None:
            raise asyncio.CancelledError()

        monkeypatch.setattr(mds, "extract_signal", FakeExtractor(on_call=die))
        with pytest.raises(asyncio.CancelledError):
            await _worker(store).run_once()
        await _expire_lease(factory, view.id)
        assert await _worker(store, owner="w2").run_once() is False  # retired, not run
        assert (await _job_row(factory, view.id)).status == contract.STATUS_DEAD_LETTER
        assert (await _run(factory, run_id)).status == "running"

        await djob.reconcile_orphaned_discovery_runs()

        run = await _run(factory, run_id)
        assert run.status == "failed"
        assert any("gave up after 1 attempts" in w for w in run.warnings)

    async def test_the_sweep_is_inert_with_durable_jobs_off(self, factory, store, monkeypatch):  # noqa: ANN001
        """Registered by the API import in every process: with durable jobs off it
        must not even open a session (a worker's startup sweep would pay for it)."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "v3_durable_jobs_enabled", False)
        run_id = await _make_run(factory)
        view, _ = await djob.submit(await _run(factory, run_id), store=store)
        await store.request_cancel(view.id)
        with patch.object(djob, "_session_factory", side_effect=AssertionError("opened")):
            await djob.reconcile_orphaned_discovery_runs()
        assert (await _run(factory, run_id)).status == "pending"

    async def test_the_observer_and_sweep_never_touch_a_finished_run(
        self, factory, store, extractor  # noqa: ANN001
    ):
        run_id = await _make_run(factory)
        view, _ = await djob.submit(await _run(factory, run_id), store=store)
        await _worker(store).run_once()
        assert (await _run(factory, run_id)).status == "completed"

        dead = contract.JobView(
            id=view.id, job_type=djob.JOB_TYPE, status=contract.STATUS_DEAD_LETTER, attempt=3
        )
        assert await djob.reconcile_run_with_job(dead, run_id) is False
        assert (await _run(factory, run_id)).status == "completed"

    async def test_the_observer_ignores_other_job_types(self, factory):  # noqa: ANN001
        run_id = await _make_run(factory)
        other = contract.JobView(
            id=str(uuid.uuid4()), job_type="company_research", status=contract.STATUS_FAILED
        )
        await djob.reconcile_discovery_run(other, completed=False, dead_lettered=False)
        assert (await _run(factory, run_id)).status == "pending"


# ===========================================================================
# Liveness
# ===========================================================================


class TestHeartbeat:
    async def test_the_heartbeat_continues_during_a_slow_extraction(
        self, factory, monkeypatch  # noqa: ANN001
    ):
        store = JobStore(factory, lease_seconds=1, max_attempts=3)
        run_id = await _make_run(factory, tickers=["SLOW"])
        view, _ = await djob.submit(await _run(factory, run_id), store=store)
        w = _worker(store, lease_seconds=1, heartbeat_seconds=0.05)
        seen: dict = {}

        async def slow(ticker: str) -> None:
            start = w.heartbeats_sent
            seen["lease_before"] = (await _job_row(factory, view.id)).lease_expires_at
            # Longer than the 1 s lease: only a live heartbeat keeps the job ours.
            await _wait_until(lambda: w.heartbeats_sent >= start + 3)
            await asyncio.sleep(1.2)
            seen["beats"] = w.heartbeats_sent - start
            seen["lease_after"] = (await _job_row(factory, view.id)).lease_expires_at

        monkeypatch.setattr(mds, "extract_signal", FakeExtractor(on_call=slow))

        await w.run_once()

        assert seen["beats"] >= 3
        assert seen["lease_after"] > seen["lease_before"], "the lease was never renewed"
        assert w.lease_lost is False
        assert (await _run(factory, run_id)).status == "completed"
        assert (await _job_row(factory, view.id)).status == contract.STATUS_COMPLETED
