"""Company research on the durable job contract — V3.0 Slice 3.

WHAT THESE TESTS PIN
====================
The wiring slice has one hard requirement and one interesting one.

The hard requirement is that **the flag off changes nothing**. Every V2
assertion in ``test_v2_async_company_research.py`` stays green untouched; what
is added here is the explicit statement that with the flag off no durable row is
written, the background task is still scheduled, and the reads still go to the
V2 store.

The interesting one is that **the flag on changes nothing a reader can see**
except gaining two terminal states that could not previously exist. The envelope
is composed from two records — lifecycle from ``research_jobs``, content from the
``AgentStep`` — and the lifecycle always wins, which is what makes it impossible
for a poll to report a job as running when the contract has dead-lettered it.

The research itself is a controlled fake throughout: nothing here waits minutes,
touches the network or calls an LLM.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
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
from app.models.agent_run import AgentRun, AgentStep
from app.models.company import Company
from app.models.research_job import ResearchJob
from app.schemas.company_research import CompanyResearchJobResponse
from app.services import company_research_service as svc
from app.services import research_job
from app.services.jobs import company_research_job as durable
from app.services.jobs import job_contract as contract
from app.services.jobs.job_store import JobStore
from app.services.jobs.worker import HandlerRegistry, ResearchWorker


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _sqlite_wal(engine):
    """Let a reader and a writer coexist, and fail fast instead of waiting.

    The durable path writes from two tasks at once (a heartbeat renewing the
    lease while the handler persists its work). SQLite serialises writers, so
    with the default rollback journal the heartbeat blocks every read as well —
    on a 10ms heartbeat that is constant contention, and the suite spent minutes
    inside SQLite's busy handler. WAL removes the reader/writer conflict, which
    is the shape PostgreSQL has in production anyway.
    """
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=OFF")
        cursor.close()


@pytest.fixture
async def engine(tmp_path):
    """A FILE-backed SQLite database, deliberately not ``:memory:``.

    The durable path is genuinely concurrent: a heartbeat task writes to the job
    row while the handler's own session writes the content record. In-memory
    SQLite with ``StaticPool`` shares ONE connection across every session, so
    those interleave on a single transaction — and when that connection is
    invalidated the pool opens a fresh one, which for ``:memory:`` is an empty
    database ("no such table"). That is an artifact of the harness, not of the
    code: PostgreSQL gives every session its own connection.

    A file plus a real pool reproduces the production shape. ``timeout`` lets a
    writer wait for SQLite's single write lock instead of failing immediately.
    """
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v3jobs.db",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    _sqlite_wal(eng)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _wire_session_factory(factory, monkeypatch):
    """Point every module-level session lookup at the test database.

    The durable modules deliberately resolve the factory at call time rather
    than importing it at module scope, precisely so this is possible without
    reloading anything.
    """
    import app.db.session as session_mod

    monkeypatch.setattr(session_mod, "async_session_factory", factory)
    monkeypatch.setattr(svc, "async_session_factory", factory)


@pytest.fixture
def durable_on(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "v3_durable_jobs_enabled", True)
    return settings


@pytest.fixture
def store(factory):
    return JobStore(factory, lease_seconds=120, max_attempts=3)


@pytest.fixture
async def company(factory) -> Company:
    async with factory() as s:
        c = Company(name="Pandora A/S", ticker="PNDORA", exchange="CPH")
        s.add(c)
        await s.commit()
        await s.refresh(c)
        return c


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# A controlled research pipeline
# ---------------------------------------------------------------------------


def _fake_pipeline(report_id: uuid.UUID, *, warnings=(), fail_with=None, on_run=None):
    """A stand-in for the real workflow + final-report generator."""

    async def run_analysis(db, **kwargs):
        if on_run is not None:
            await on_run()
        if fail_with is not None:
            raise fail_with
        # The graph reports its OWN node names; the stage map turns them into
        # reader-facing stages, exactly as in production.
        on_node = kwargs.get("on_node")
        if on_node is not None:
            for node in ("load_company", "fetch_provider_data", "financial_data_agent"):
                await on_node(node)
        return {"status": "completed", "draft_report_id": None, "agent_run_id": None}

    async def generate_final_report(db, **kwargs):
        class _Resp:
            def __init__(self):
                self.report_id = report_id
                self.llm_used = False
                self.schema_valid = True
                self.safety_valid = True

        return _Resp()

    return run_analysis, generate_final_report


async def _run_the_job(store, factory, *, report_id, **kw):
    """Claim and execute the one queued job with a fake pipeline."""
    run_analysis, generate_final = _fake_pipeline(report_id, **kw)
    original = svc.process_company_research_by_id

    async def patched(job_id, **kwargs):
        kwargs.setdefault("session_factory", factory)
        kwargs["run_analysis"] = run_analysis
        kwargs["generate_final_report"] = generate_final
        return await original(job_id, **kwargs)

    reg = HandlerRegistry()

    async def handler(ctx):
        import app.services.jobs.company_research_job as mod

        real_svc_fn = svc.process_company_research_by_id
        svc.process_company_research_by_id = patched  # type: ignore[assignment]
        try:
            return await mod.run_company_research_job(ctx)
        finally:
            svc.process_company_research_by_id = real_svc_fn  # type: ignore[assignment]

    reg.register(durable.JOB_TYPE, handler)
    worker = ResearchWorker(
        store,
        handlers=reg,
        owner="test-worker",
        lease_seconds=120,
        heartbeat_seconds=0.2,
        poll_interval_seconds=0.01,
    )
    return await worker.run_once()


async def _job_row(factory, job_id) -> ResearchJob:
    async with factory() as s:
        return (
            await s.execute(
                select(ResearchJob).where(ResearchJob.id == uuid.UUID(str(job_id)))
            )
        ).scalar_one()


# ===========================================================================
# The flag off: V2 is byte-for-byte unchanged
# ===========================================================================


class TestFlagOff:
    async def test_the_flag_is_off_by_default(self):
        from app.core.config import Settings

        assert Settings().v3_durable_jobs_enabled is False

    async def test_durable_enabled_is_read_at_call_time(self, monkeypatch):
        """A captured module-level boolean is how a flag becomes permanent."""
        from app.core.config import settings

        assert durable.durable_enabled() is False
        monkeypatch.setattr(settings, "v3_durable_jobs_enabled", True)
        assert durable.durable_enabled() is True

    async def test_no_durable_row_is_written_on_the_v2_path(self, factory, company):
        async with factory() as s:
            envelope, scheduled = await svc.start_company_research(s, company)
        assert scheduled is True
        async with factory() as s:
            rows = (await s.execute(select(ResearchJob))).scalars().all()
        assert rows == []
        # And the V2 record is exactly what it always was.
        assert envelope["status"] == research_job.STATUS_PENDING
        assert envelope["stage"] == research_job.STAGE_QUEUED

    async def test_the_v2_envelope_writer_is_unchanged_by_extraction(
        self, factory, company
    ):
        """``create_job_record`` was split out of the submit path, not rewritten."""
        async with factory() as s:
            direct = await svc.create_job_record(s, company)
        async with factory() as s:
            via_submit, _ = await svc.start_company_research(s, company)
        assert set(direct) == set(via_submit)
        assert direct["company"] == via_submit["company"]
        assert direct["stage"] == via_submit["stage"] == research_job.STAGE_QUEUED


# ===========================================================================
# The flag on: submission
# ===========================================================================


class TestDurableSubmission:
    async def test_submit_commits_a_durable_row_before_returning(
        self, durable_on, store, company, factory
    ):
        envelope, created = await durable.submit(company, store=store)
        assert created is True
        row = await _job_row(factory, envelope["job_id"])
        assert row.job_type == durable.JOB_TYPE
        assert row.status == contract.STATUS_PENDING
        assert row.company_id == company.id

    async def test_the_submit_does_not_do_the_work(
        self, durable_on, store, company, factory
    ):
        await durable.submit(company, store=store)
        async with factory() as s:
            runs = (await s.execute(select(AgentRun))).scalars().all()
        # Nothing has executed: no workflow record exists until a worker claims.
        assert runs == []

    async def test_a_double_submit_starts_no_second_run(
        self, durable_on, store, company, factory
    ):
        first, created_a = await durable.submit(company, store=store)
        second, created_b = await durable.submit(company, store=store)
        assert created_a is True
        assert created_b is False
        assert second["job_id"] == first["job_id"]
        async with factory() as s:
            assert len((await s.execute(select(ResearchJob))).scalars().all()) == 1

    async def test_concurrent_submits_produce_one_job(
        self, durable_on, store, company, factory
    ):
        results = await asyncio.gather(
            *[durable.submit(company, store=store) for _ in range(6)]
        )
        assert sum(1 for _, created in results if created) == 1
        async with factory() as s:
            assert len((await s.execute(select(ResearchJob))).scalars().all()) == 1

    async def test_identity_is_resolved_once_and_carried(
        self, durable_on, store, company
    ):
        envelope, _ = await durable.submit(company, store=store)
        assert envelope["company"]["id"] == str(company.id)
        assert envelope["company"]["ticker"] == "PNDORA"
        assert envelope["company"]["exchange"] == "CPH"

    async def test_the_payload_never_carries_a_credential(
        self, durable_on, store, company, factory
    ):
        envelope, _ = await durable.submit(
            company, provider_name="free_real", llm_provider="azure_openai", store=store
        )
        row = await _job_row(factory, envelope["job_id"])
        keys = set(row.payload_json or {})
        assert keys == {
            "company_id",
            "provider_name",
            "use_llm",
            "llm_provider",
            "require_schema_valid",
            "company",
        }

    async def test_the_dedup_key_is_scoped_to_the_company(self, company):
        other = uuid.uuid4()
        assert durable.dedup_key(company.id) != durable.dedup_key(other)
        assert str(company.id) in durable.dedup_key(company.id)


# ===========================================================================
# The flag on: execution
# ===========================================================================


class TestDurableExecution:
    async def test_a_worker_runs_the_job_and_links_the_report(
        self, durable_on, store, company, factory
    ):
        report_id = uuid.uuid4()
        envelope, _ = await durable.submit(company, store=store)
        assert await _run_the_job(store, factory, report_id=report_id) is True

        row = await _job_row(factory, envelope["job_id"])
        assert row.status == contract.STATUS_COMPLETED
        assert row.result_type == "report"
        assert row.result_ref == report_id
        # And the reader sees the report the run actually produced.
        final = await durable.get_envelope(uuid.UUID(envelope["job_id"]), store=store)
        assert final["analysis_report_id"] == str(report_id)
        assert final["status"] == contract.STATUS_COMPLETED

    async def test_the_content_record_is_the_same_agent_run_v2_writes(
        self, durable_on, store, company, factory
    ):
        """One audit trail, one workflow name — not a parallel notion of a job."""
        envelope, _ = await durable.submit(company, store=store)
        await _run_the_job(store, factory, report_id=uuid.uuid4())

        async with factory() as s:
            runs = (await s.execute(select(AgentRun))).scalars().all()
            steps = (await s.execute(select(AgentStep))).scalars().all()
        assert len(runs) == 1
        assert runs[0].workflow_name == svc.JOB_WORKFLOW_NAME
        assert len(steps) == 1
        assert steps[0].agent_name == svc.JOB_AGENT_NAME

        row = await _job_row(factory, envelope["job_id"])
        assert row.agent_run_id == runs[0].id

    async def test_a_retry_reuses_the_content_record(
        self, durable_on, store, company, factory
    ):
        """A retried job keeps ONE audit trail, not one AgentRun per attempt."""
        envelope, _ = await durable.submit(company, store=store)
        await _run_the_job(
            store, factory, report_id=uuid.uuid4(), fail_with=TimeoutError("slow")
        )
        row = await _job_row(factory, envelope["job_id"])
        assert row.status == contract.STATUS_PENDING
        assert row.agent_run_id is not None
        first_run_id = row.agent_run_id

        async with factory() as s:
            job = await s.get(ResearchJob, uuid.UUID(envelope["job_id"]))
            job.available_at = _now() - timedelta(seconds=1)
            await s.commit()

        await _run_the_job(store, factory, report_id=uuid.uuid4())
        row = await _job_row(factory, envelope["job_id"])
        assert row.status == contract.STATUS_COMPLETED
        assert row.agent_run_id == first_run_id
        async with factory() as s:
            assert len((await s.execute(select(AgentRun))).scalars().all()) == 1

    async def test_a_transient_failure_does_not_stamp_the_envelope_failed(
        self, durable_on, store, company, factory
    ):
        """A ``failed`` envelope beside a ``pending`` job row is a contradiction."""
        envelope, _ = await durable.submit(company, store=store)
        await _run_the_job(
            store, factory, report_id=uuid.uuid4(), fail_with=TimeoutError("slow")
        )
        row = await _job_row(factory, envelope["job_id"])
        assert row.status == contract.STATUS_PENDING

        async with factory() as session:
            inner = await svc.get_job_envelope(
                session, uuid.UUID(str(row.agent_run_id))
            )
        assert inner["status"] != research_job.STATUS_FAILED
        # And the reader is told the truth about the lifecycle, from the row.
        seen = await durable.get_envelope(uuid.UUID(envelope["job_id"]), store=store)
        assert seen["status"] == contract.STATUS_PENDING

    async def test_a_permanent_failure_fails_the_job_immediately(
        self, durable_on, store, company, factory
    ):
        envelope, _ = await durable.submit(company, store=store)
        await _run_the_job(
            store, factory, report_id=uuid.uuid4(), fail_with=ValueError("a bug")
        )
        row = await _job_row(factory, envelope["job_id"])
        assert row.status == contract.STATUS_FAILED
        assert row.attempt < row.max_attempts

    async def test_a_missing_company_is_permanent_not_retried(
        self, durable_on, store, factory
    ):
        gone = Company(id=uuid.uuid4(), name="Gone", ticker="GONE", exchange="XXX")
        envelope, _ = await durable.submit(gone, store=store)
        await _run_the_job(store, factory, report_id=uuid.uuid4())
        row = await _job_row(factory, envelope["job_id"])
        assert row.status == contract.STATUS_FAILED
        assert row.error_class == "CompanyResearchFailed"

    async def test_a_missing_content_record_is_transient(self):
        """Submission commits the row then the envelope; a claim can land between."""
        from app.services.jobs.worker import is_transient_failure

        assert is_transient_failure(svc.JobRecordMissing("x")) is True
        assert is_transient_failure(svc.CompanyResearchFailed("x")) is False

    async def test_stages_reported_by_the_workflow_reach_the_job_row(
        self, durable_on, store, company, factory
    ):
        envelope, _ = await durable.submit(company, store=store)
        await _run_the_job(store, factory, report_id=uuid.uuid4())
        row = await _job_row(factory, envelope["job_id"])
        # The LAST stage the run reported, using the existing vocabulary.
        assert row.stage in research_job.STAGE_ORDER

    async def test_the_job_survives_the_process_that_accepted_it(
        self, durable_on, store, company, factory
    ):
        """The point of the whole phase, on the real entry point.

        The submitting 'process' does no work at all and the executing one is a
        different object with a different lease owner. Under V2 the two were the
        same background task and a recycle between them lost the run.
        """
        envelope, _ = await durable.submit(company, store=store)
        row = await _job_row(factory, envelope["job_id"])
        assert row.status == contract.STATUS_PENDING  # committed, unexecuted

        assert await _run_the_job(store, factory, report_id=uuid.uuid4()) is True
        row = await _job_row(factory, envelope["job_id"])
        assert row.status == contract.STATUS_COMPLETED


# ===========================================================================
# The reader-facing contract
# ===========================================================================


class TestEnvelopeContract:
    async def test_the_durable_envelope_satisfies_the_v2_response_model(
        self, durable_on, store, company, factory
    ):
        envelope, _ = await durable.submit(company, store=store)
        resp = CompanyResearchJobResponse.from_envelope(envelope, message="hi")
        assert resp.status == research_job.STATUS_PENDING
        assert resp.stage == research_job.STAGE_QUEUED
        assert resp.company.ticker == "PNDORA"
        assert [s.key for s in resp.stages] == list(research_job.STAGE_ORDER)
        assert resp.human_review_required is True

    async def test_the_completed_envelope_carries_every_v2_field(
        self, durable_on, store, company, factory
    ):
        report_id = uuid.uuid4()
        envelope, _ = await durable.submit(company, store=store)
        await _run_the_job(store, factory, report_id=report_id)
        final = await durable.get_envelope(uuid.UUID(envelope["job_id"]), store=store)

        v2_fields = {
            "job_id", "status", "stage", "stages_completed", "started_at",
            "completed_at", "company", "provider_name", "analysis_report_id",
            "agent_run_id", "legacy_draft_report_id", "report", "workflow_status",
            "warnings", "error",
        }
        assert v2_fields <= set(final)
        resp = CompanyResearchJobResponse.from_envelope(final, message="done")
        assert resp.analysis_report_id == report_id

    async def test_the_lifecycle_wins_over_the_content_record(
        self, durable_on, store, company, factory
    ):
        """The two records cannot disagree, because one of them is not asked."""
        envelope, _ = await durable.submit(company, store=store)
        await _run_the_job(store, factory, report_id=uuid.uuid4())
        job_id = uuid.UUID(envelope["job_id"])

        # Corrupt the content record into claiming the job is still running.
        row = await _job_row(factory, job_id)
        async with factory() as s:
            step = (
                await s.execute(
                    select(AgentStep).where(
                        AgentStep.agent_run_id == row.agent_run_id
                    )
                )
            ).scalar_one()
            inner = dict(step.output_json)
            inner["status"] = research_job.STATUS_RUNNING
            step.output_json = inner
            await s.commit()

        seen = await durable.get_envelope(job_id, store=store)
        assert seen["status"] == contract.STATUS_COMPLETED

    async def test_a_lapsed_lease_with_attempts_left_reads_as_pending(
        self, durable_on, store, company, factory
    ):
        envelope, _ = await durable.submit(company, store=store)
        await store.claim_next(owner="dead", now=_now(), lease_seconds=1)
        job_id = uuid.UUID(envelope["job_id"])
        found = await store.get_with_payload(job_id)
        seen = await durable.compose_envelope(
            found[0], found[1], now=_now() + timedelta(hours=1)
        )
        # Another worker is coming. Telling the reader to re-run would be wrong,
        # and the UI treats 'interrupted' as terminal so it would stop polling.
        assert seen["status"] == research_job.STATUS_PENDING
        assert "interrupted_reason" not in seen

    async def test_a_lapsed_lease_with_no_attempts_left_reads_as_interrupted(
        self, durable_on, company, factory
    ):
        store = JobStore(factory, lease_seconds=1, max_attempts=1)
        envelope, _ = await durable.submit(company, store=store)
        await store.claim_next(owner="dead", now=_now(), lease_seconds=1)
        found = await store.get_with_payload(uuid.UUID(envelope["job_id"]))
        seen = await durable.compose_envelope(
            found[0], found[1], now=_now() + timedelta(hours=1)
        )
        assert seen["status"] == research_job.STATUS_INTERRUPTED
        assert seen["recoverable"] is True
        assert "re-running is safe" in seen["interrupted_reason"]

    async def test_a_dead_lettered_job_carries_its_reason(
        self, durable_on, company, factory
    ):
        store = JobStore(factory, lease_seconds=120, max_attempts=1)
        envelope, _ = await durable.submit(company, store=store)
        claimed = await store.claim_next(owner="w", now=_now())
        await store.fail(
            claimed.id, owner="w", transient=True, error_class="LLMServerError"
        )
        seen = await durable.get_envelope(uuid.UUID(envelope["job_id"]), store=store)
        assert seen["status"] == contract.STATUS_DEAD_LETTER
        assert seen["recoverable"] is True
        assert "exhausted 1 attempts" in seen["dead_letter_reason"]
        resp = CompanyResearchJobResponse.from_envelope(seen, message="m")
        assert resp.dead_letter_reason

    async def test_a_cancelled_job_reads_as_cancelled(
        self, durable_on, store, company
    ):
        envelope, _ = await durable.submit(company, store=store)
        await store.request_cancel(uuid.UUID(envelope["job_id"]))
        seen = await durable.get_envelope(uuid.UUID(envelope["job_id"]), store=store)
        assert seen["status"] == contract.STATUS_CANCELLED

    async def test_the_envelope_never_exposes_the_lease_owner(
        self, durable_on, store, company
    ):
        envelope, _ = await durable.submit(company, store=store)
        await store.claim_next(owner="secret-host-name:9999", now=_now())
        seen = await durable.get_envelope(uuid.UUID(envelope["job_id"]), store=store)
        assert "secret-host-name" not in str(seen)

    async def test_attempt_accounting_is_visible_to_a_reader(
        self, durable_on, store, company
    ):
        envelope, _ = await durable.submit(company, store=store)
        seen = await durable.get_envelope(uuid.UUID(envelope["job_id"]), store=store)
        assert seen["attempt"] == 0
        assert seen["max_attempts"] == 3

    async def test_latest_for_company_is_scoped_to_that_company(
        self, durable_on, store, company, factory
    ):
        async with factory() as s:
            other = Company(name="Richemont", ticker="CFR", exchange="SWX")
            s.add(other)
            await s.commit()
            await s.refresh(other)

        mine, _ = await durable.submit(company, store=store)
        await durable.submit(other, store=store)

        found = await durable.latest_envelope_for_company(company.id, store=store)
        assert found["job_id"] == mine["job_id"]
        assert found["company"]["ticker"] == "PNDORA"

    async def test_latest_for_a_company_with_no_jobs_is_none(
        self, durable_on, store
    ):
        assert await durable.latest_envelope_for_company(uuid.uuid4(), store=store) is None

    async def test_an_unknown_job_id_is_none_not_an_error(self, durable_on, store):
        assert await durable.get_envelope(uuid.uuid4(), store=store) is None


# ===========================================================================
# Event-loop isolation on the durable path
# ===========================================================================


class TestEventLoopIsolation:
    async def test_a_handler_doing_cpu_work_off_the_loop_keeps_its_lease(
        self, durable_on, store, company, factory
    ):
        """The ingestion invariant, restated for the path that now runs it.

        ``live_fetchers._parse_off_loop`` keeps document parsing on a worker
        thread. Under the durable worker that is not just a latency property: the
        heartbeat is an ``async`` task, so a parse ON the loop stops the lease
        being renewed and the job is taken away mid-run.
        """
        done = threading.Event()
        beats_seen = asyncio.Event()

        async def on_run():
            # Stand in for ``_parse_off_loop``: CPU work on a worker thread.
            await asyncio.to_thread(done.wait, 10.0)

        await durable.submit(company, store=store)
        run_analysis, generate_final = _fake_pipeline(uuid.uuid4(), on_run=on_run)

        reg = HandlerRegistry()

        async def handler(ctx):
            import app.services.jobs.company_research_job as mod

            original = svc.process_company_research_by_id

            async def patched(job_id, **kwargs):
                kwargs.setdefault("session_factory", factory)
                kwargs["run_analysis"] = run_analysis
                kwargs["generate_final_report"] = generate_final
                return await original(job_id, **kwargs)

            svc.process_company_research_by_id = patched  # type: ignore[assignment]
            try:
                return await mod.run_company_research_job(ctx)
            finally:
                svc.process_company_research_by_id = original  # type: ignore[assignment]

        reg.register(durable.JOB_TYPE, handler)
        worker = ResearchWorker(
            store,
            handlers=reg,
            owner="w",
            lease_seconds=1,
            heartbeat_seconds=0.02,
            poll_interval_seconds=0.01,
        )
        task = asyncio.create_task(worker.run_once())
        deadline = time.monotonic() + 10
        try:
            while worker.heartbeats_sent < 3:
                assert time.monotonic() < deadline, "heartbeats never landed"
                await asyncio.sleep(0.005)
            # The loop is free, so the lease held: nobody could steal the job.
            assert await store.claim_next(owner="thief", now=_now()) is None
        finally:
            done.set()
            beats_seen.set()
        await asyncio.wait_for(task, timeout=10)


# ===========================================================================
# The in-process worker lifecycle
# ===========================================================================


class TestInProcessWorker:
    async def test_the_worker_does_not_start_when_the_flag_is_off(self):
        from app.main import _start_durable_worker

        assert await _start_durable_worker() is None

    async def test_the_worker_does_not_start_when_disabled_in_process(
        self, durable_on, monkeypatch
    ):
        from app.core.config import settings
        from app.main import _start_durable_worker

        monkeypatch.setattr(settings, "v3_job_worker_in_process", False)
        assert await _start_durable_worker() is None

    async def test_the_worker_starts_and_stops_cleanly(self, durable_on, monkeypatch):
        from app.core.config import settings
        from app.main import _start_durable_worker, _stop_durable_worker

        monkeypatch.setattr(settings, "v3_job_worker_in_process", True)
        worker = await _start_durable_worker()
        assert worker is not None
        await _stop_durable_worker(worker)
        task, _ = worker
        assert task.done() or task.cancelled()

    async def test_stopping_nothing_is_safe(self):
        from app.main import _stop_durable_worker

        await _stop_durable_worker(None)

    async def test_every_registered_handler_is_importable(self):
        """A job type with no handler fails permanently — so the list must load."""
        import app.services.jobs.handlers as handlers_mod
        from app.services.jobs.worker import registry

        assert handlers_mod is not None
        assert durable.JOB_TYPE in registry


# ===========================================================================
# Over HTTP: the polling contract itself
# ===========================================================================


async def _client(factory):
    from httpx import ASGITransport, AsyncClient

    from app.db.session import get_db
    from app.main import app

    async def override():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db] = override
    return (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test"),
        app,
    )


class TestOverHttp:
    """The same assertions ``test_v2_async_company_research.py`` makes, with the
    flag on. If any of these differ, the wiring changed something a reader can
    see — which is the one thing this slice is not allowed to do."""

    async def test_post_returns_202_with_a_queued_job(
        self, durable_on, factory, company
    ):
        client, app = await _client(factory)
        try:
            res = await client.post(
                "/api/v1/company-research/jobs",
                json={"company_id": str(company.id), "provider_name": "free_real"},
            )
        finally:
            await client.aclose()
            app.dependency_overrides.clear()

        assert res.status_code == 202
        body = res.json()
        assert body["status"] == "pending"
        assert body["stage"] == "queued"
        assert body["stage_label"] == "Queued"
        assert body["company"]["ticker"] == "PNDORA"
        assert body["human_review_required"] is True
        labels = [s["label"] for s in body["stages"]]
        assert "Reading the issuer's own documents" in labels
        assert "Running the research council" in labels
        # No fabricated percentage — the pipeline cannot know how long a node takes.
        assert not any("%" in label for label in labels)

    async def test_the_submit_commits_before_it_answers(
        self, durable_on, factory, company
    ):
        client, app = await _client(factory)
        try:
            res = await client.post(
                "/api/v1/company-research/jobs",
                json={"company_id": str(company.id)},
            )
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
        job_id = res.json()["job_id"]
        # Visible from a session that had nothing to do with the request.
        row = await _job_row(factory, job_id)
        assert row.status == contract.STATUS_PENDING

    async def test_a_double_post_returns_the_same_job(
        self, durable_on, factory, company
    ):
        client, app = await _client(factory)
        try:
            first = await client.post(
                "/api/v1/company-research/jobs", json={"company_id": str(company.id)}
            )
            second = await client.post(
                "/api/v1/company-research/jobs", json={"company_id": str(company.id)}
            )
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
        assert first.json()["job_id"] == second.json()["job_id"]
        assert "already in progress" in second.json()["message"]

    async def test_get_by_job_id_and_recovery_by_company(
        self, durable_on, factory, company
    ):
        client, app = await _client(factory)
        try:
            created = await client.post(
                "/api/v1/company-research/jobs", json={"company_id": str(company.id)}
            )
            job_id = created.json()["job_id"]
            by_id = await client.get(f"/api/v1/company-research/jobs/{job_id}")
            by_company = await client.get(
                "/api/v1/company-research/jobs", params={"company_id": str(company.id)}
            )
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
        assert by_id.status_code == 200
        assert by_id.json()["job_id"] == job_id
        # The job id is not only in the browser: a reader who refreshed finds it.
        assert by_company.status_code == 200
        assert by_company.json()["job_id"] == job_id

    async def test_an_unknown_job_id_is_404(self, durable_on, factory, company):
        client, app = await _client(factory)
        try:
            res = await client.get(
                f"/api/v1/company-research/jobs/{uuid.uuid4()}"
            )
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
        assert res.status_code == 404

    async def test_a_v2_job_still_resolves_with_the_flag_on(
        self, durable_on, factory, company
    ):
        """Existing job ids remain resolvable — the compatibility guarantee.

        A V2 job's id is an ``AgentRun.id`` and a durable job's is a
        ``research_jobs.id``. Turning the flag on must not orphan the runs that
        were started before it.
        """
        async with factory() as s:
            legacy = await svc.create_job_record(s, company)
        client, app = await _client(factory)
        try:
            res = await client.get(
                f"/api/v1/company-research/jobs/{legacy['job_id']}"
            )
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
        assert res.status_code == 200
        assert res.json()["job_id"] == legacy["job_id"]
        assert res.json()["company"]["ticker"] == "PNDORA"

    async def test_an_unknown_company_is_404_not_a_started_job(
        self, durable_on, factory, company
    ):
        client, app = await _client(factory)
        try:
            res = await client.post(
                "/api/v1/company-research/jobs",
                json={"company_id": str(uuid.uuid4())},
            )
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
        assert res.status_code == 404
        async with factory() as s:
            assert (await s.execute(select(ResearchJob))).scalars().all() == []

    async def test_a_dead_lettered_job_is_reported_over_http(
        self, durable_on, factory, company
    ):
        store = JobStore(factory, lease_seconds=120, max_attempts=1)
        envelope, _ = await durable.submit(company, store=store)
        claimed = await store.claim_next(owner="w", now=_now())
        await store.fail(
            claimed.id, owner="w", transient=True, error_class="LLMServerError"
        )
        client, app = await _client(factory)
        try:
            res = await client.get(
                f"/api/v1/company-research/jobs/{envelope['job_id']}"
            )
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
        body = res.json()
        assert body["status"] == "dead_letter"
        assert body["dead_letter_reason"]
        assert body["recoverable"] is True
        assert "re-running is safe" in body["message"]
        # Still never an investment action, whatever the lifecycle did.
        assert body["human_review_required"] is True
        assert "NOT INVESTMENT ADVICE" in body["disclaimer"]

    async def test_no_background_task_is_scheduled_on_the_durable_path(
        self, durable_on, factory, company, monkeypatch
    ):
        """The API must not ALSO run the job in-process; that would be two runs."""
        called: list[str] = []

        async def spy(job_id: str) -> None:  # pragma: no cover - must not run
            called.append(job_id)

        monkeypatch.setattr(svc, "process_company_research_task", spy)
        client, app = await _client(factory)
        try:
            await client.post(
                "/api/v1/company-research/jobs", json={"company_id": str(company.id)}
            )
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
        assert called == []
