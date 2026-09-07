"""The durable worker loop — V3.0 Slice 2.

WHAT THESE TESTS PIN
====================
The phase gate for V3.0 (ACCEPTANCE_AND_TEST_STRATEGY §3) is a list of
properties, and this file is where the executable ones live:

  * a job survives a worker restart — a second worker reclaims it and finishes;
  * a duplicate submit JOINS rather than duplicating an expensive run;
  * an expired lease is reclaimed by EXACTLY ONE worker;
  * attempts are bounded and dead-letter is reachable, with a reason;
  * cancellation is honoured at a task boundary and never mid-step;
  * no status vocabulary drift — ``interrupted`` is never written.

Everything runs against a real SQLite database in memory: real INSERTs, real
guarded UPDATEs, real unique-constraint violations. A mocked session would let
the compare-and-set that the whole concurrency argument rests on pass while
being wrong, because the assertion would be about what we asked the mock, not
about what the database did.

No clock dependence anywhere. Leases are expired by passing an explicit ``now``,
never by sleeping, because a test whose outcome depends on the wall clock is a
flake with a schedule.
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
from app.models.company import Company
from app.models.research_job import ResearchJob
from app.services.jobs import job_contract as contract
from app.services.jobs import worker as worker_mod
from app.services.jobs.job_store import JobStore, base_key
from app.services.jobs.worker import (
    HandlerRegistry,
    JobCancelled,
    JobContext,
    JobOutcome,
    ResearchWorker,
    is_transient_failure,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


JOB_TYPE = "test_research"


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
    # FILE-backed, deliberately not ``:memory:``. The worker heartbeats from one
    # task while the handler works in another, so two sessions are genuinely
    # concurrent — and in-memory SQLite with ``StaticPool`` shares ONE connection
    # across every session. When that connection is invalidated the pool opens a
    # fresh one, which for ``:memory:`` is an empty database ("no such table").
    # That is a harness artifact, not a defect in the code: PostgreSQL gives
    # every session its own connection. ``timeout`` lets a writer wait for
    # SQLite's single write lock rather than failing immediately.
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


@pytest.fixture
def store(factory):
    return JobStore(factory, lease_seconds=120, max_attempts=3)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _wait_until(predicate, *, timeout: float = 10.0) -> None:
    """Wait for a condition rather than for a duration.

    ``await asyncio.sleep(0.25); assert beats >= 3`` is a wall-clock assertion,
    and it behaves differently on a loaded event loop — this file's first version
    passed in isolation and failed in the full suite. Waiting for the condition
    keeps the property under test ("beats keep landing") and drops the incidental
    one ("they land within 250ms on a busy machine").
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached within the timeout")
        await asyncio.sleep(0.005)


async def _row(factory, job_id) -> ResearchJob:
    async with factory() as s:
        return (
            await s.execute(
                select(ResearchJob).where(ResearchJob.id == uuid.UUID(str(job_id)))
            )
        ).scalar_one()


# ===========================================================================
# Submission and idempotency
# ===========================================================================


class TestEnqueue:
    async def test_enqueue_commits_a_pending_job(self, store, factory):
        view, created = await store.enqueue(
            job_type=JOB_TYPE, idempotency_key="k", payload={"ticker": "PNDORA"}
        )
        assert created is True
        assert view.status == contract.STATUS_PENDING
        assert view.attempt == 0
        # Committed, not merely staged: a fresh session can see it.
        row = await _row(factory, view.id)
        assert row.job_type == JOB_TYPE
        assert row.payload_json == {"ticker": "PNDORA"}

    async def test_duplicate_submit_joins_and_starts_no_second_run(self, store):
        first, created_a = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        second, created_b = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        assert created_a is True
        assert created_b is False
        assert second.id == first.id

    async def test_only_one_row_exists_after_a_duplicate_submit(self, store, factory):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        async with factory() as s:
            rows = (await s.execute(select(ResearchJob))).scalars().all()
        assert len(rows) == 1

    async def test_a_terminal_job_does_not_block_a_re_run(self, store):
        first, _ = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now())
        await store.complete(claimed.id, owner="w1")

        second, created = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        assert created is True
        assert second.id != first.id
        assert second.status == contract.STATUS_PENDING

    async def test_generations_are_a_versioned_key_not_a_reused_one(
        self, store, factory
    ):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now())
        await store.complete(claimed.id, owner="w1")
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")

        async with factory() as s:
            keys = sorted(
                (await s.execute(select(ResearchJob.idempotency_key))).scalars().all()
            )
        assert keys == ["k#1", "k#2"]
        assert {base_key(k) for k in keys} == {"k"}

    async def test_a_lapsed_lease_is_joined_not_duplicated(self, store):
        """The deliberate improvement on V2.

        V2 let a resubmit start a fresh run once the previous one was judged
        stale, because nothing was going to recover it. Here a lapsed lease is
        RECLAIMABLE, so the resubmit joins the run that is about to be resumed
        instead of paying for a second one.
        """
        first, _ = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        await store.claim_next(owner="dead-worker", now=_now(), lease_seconds=1)

        long_after = _now() + timedelta(hours=2)
        joined, created = await store.enqueue(
            job_type=JOB_TYPE, idempotency_key="k", now=long_after
        )
        assert created is False
        assert joined.id == first.id
        # It has attempts left, so a worker will reclaim it: the reader is told
        # it is queued, not that their run stopped and it is on them to retry.
        assert contract.derive_status(joined, long_after) == contract.STATUS_PENDING

    async def test_base_key_is_recoverable_from_a_stored_key(self):
        assert base_key("company_research:abc#7") == "company_research:abc"
        assert base_key("no-generation") == "no-generation"
        # A '#' that is not a generation must not be eaten.
        assert base_key("weird#name") == "weird#name"

    async def test_a_company_fk_is_stored_when_given(self, store, factory):
        async with factory() as s:
            company = Company(name="Pandora A/S", ticker="PNDORA", exchange="CPH")
            s.add(company)
            await s.commit()
            company_id = company.id

        view, _ = await store.enqueue(
            job_type=JOB_TYPE, idempotency_key="k", company_id=company_id
        )
        row = await _row(factory, view.id)
        assert row.company_id == company_id


# ===========================================================================
# Claiming and leasing
# ===========================================================================


class TestClaiming:
    async def test_claim_takes_a_pending_job_and_spends_an_attempt(self, store):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now())
        assert claimed is not None
        assert claimed.status == contract.STATUS_RUNNING
        assert claimed.attempt == 1
        assert claimed.lease_owner == "w1"
        assert claimed.lease_expires_at is not None

    async def test_nothing_to_claim_returns_none(self, store):
        assert await store.claim_next(owner="w1", now=_now()) is None

    async def test_a_live_lease_is_not_claimable_by_anyone_else(self, store):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        await store.claim_next(owner="w1", now=_now(), lease_seconds=300)
        assert await store.claim_next(owner="w2", now=_now()) is None

    async def test_exactly_one_worker_wins_a_contended_claim(self, store):
        """The compare-and-set, exercised concurrently against a real database."""
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        now = _now()
        results = await asyncio.gather(
            *[store.claim_next(owner=f"w{i}", now=now) for i in range(8)]
        )
        winners = [r for r in results if r is not None]
        assert len(winners) == 1
        assert winners[0].attempt == 1

    async def test_an_expired_lease_is_reclaimed_exactly_once(self, store):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        await store.claim_next(owner="dead", now=_now(), lease_seconds=1)

        later = _now() + timedelta(minutes=5)
        results = await asyncio.gather(
            *[store.claim_next(owner=f"w{i}", now=later) for i in range(6)]
        )
        winners = [r for r in results if r is not None]
        assert len(winners) == 1
        # The lost attempt is spent — a job that reliably kills its worker must
        # not be retried forever.
        assert winners[0].attempt == 2

    async def test_backoff_is_a_real_delay_not_a_hot_loop(self, store):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now())
        outcome = await store.fail(
            claimed.id, owner="w1", transient=True, error_class="LLMRateLimitError"
        )
        assert outcome.will_retry is True

        # Due later, so a claim right now finds nothing.
        assert await store.claim_next(owner="w1", now=_now()) is None
        assert (
            await store.claim_next(owner="w1", now=_now() + timedelta(minutes=1))
        ) is not None

    async def test_job_types_filter_is_honoured(self, store):
        await store.enqueue(job_type="other", idempotency_key="k")
        assert (
            await store.claim_next(owner="w1", now=_now(), job_types=[JOB_TYPE])
        ) is None
        assert (
            await store.claim_next(owner="w1", now=_now(), job_types=["other"])
        ) is not None

    async def test_a_terminal_job_is_never_claimed(self, store):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now())
        await store.complete(claimed.id, owner="w1")
        assert await store.claim_next(owner="w2", now=_now()) is None


class TestHeartbeat:
    async def test_heartbeat_extends_the_lease(self, store):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now(), lease_seconds=60)
        later = _now() + timedelta(seconds=30)
        beat = await store.heartbeat(claimed.id, owner="w1", now=later, lease_seconds=60)
        assert beat is not None
        assert beat.lease_expires_at > claimed.lease_expires_at

    async def test_a_non_owner_cannot_heartbeat(self, store):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now())
        assert await store.heartbeat(claimed.id, owner="w2") is None

    async def test_the_previous_owner_loses_its_lease_after_a_reclaim(self, store):
        """The split-brain guard: a slow-but-alive worker must not resurrect."""
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now(), lease_seconds=1)
        later = _now() + timedelta(minutes=5)
        await store.claim_next(owner="w2", now=later)
        assert await store.heartbeat(claimed.id, owner="w1", now=later) is None

    async def test_heartbeat_reports_a_stage(self, store, factory):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now())
        await store.heartbeat(claimed.id, owner="w1", stage="council_analysis")
        row = await _row(factory, claimed.id)
        assert row.stage == "council_analysis"

    async def test_heartbeat_surfaces_a_cancel_request(self, store):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now())
        await store.request_cancel(claimed.id)
        beat = await store.heartbeat(claimed.id, owner="w1")
        assert beat is not None
        assert beat.cancel_requested is True


# ===========================================================================
# Outcomes
# ===========================================================================


class TestOutcomes:
    async def test_complete_records_the_result_reference(self, store, factory):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now())
        ref = uuid.uuid4()
        done = await store.complete(
            claimed.id, owner="w1", result_type="report", result_ref=ref
        )
        assert done.status == contract.STATUS_COMPLETED
        row = await _row(factory, claimed.id)
        assert row.result_type == "report"
        assert row.result_ref == ref
        assert row.lease_owner is None

    async def test_warnings_map_to_completed_with_warnings(self, store):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now())
        done = await store.complete(claimed.id, owner="w1", with_warnings=True)
        assert done.status == contract.STATUS_COMPLETED_WITH_WARNINGS

    async def test_a_non_owner_cannot_complete(self, store, factory):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now())
        assert await store.complete(claimed.id, owner="w2") is None
        row = await _row(factory, claimed.id)
        assert row.status == contract.STATUS_RUNNING

    async def test_a_permanent_failure_fails_immediately_with_attempts_left(
        self, store
    ):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now())
        outcome = await store.fail(
            claimed.id, owner="w1", transient=False, error_class="ValueError"
        )
        assert outcome.will_retry is False
        assert outcome.job.status == contract.STATUS_FAILED
        assert outcome.job.attempt < outcome.job.max_attempts

    async def test_attempts_are_bounded_and_dead_letter_is_reachable(
        self, store, factory
    ):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k", max_attempts=3)
        now = _now()
        for expected_attempt in (1, 2, 3):
            claimed = await store.claim_next(owner="w1", now=now)
            assert claimed is not None
            assert claimed.attempt == expected_attempt
            outcome = await store.fail(
                claimed.id, owner="w1", transient=True, error_class="LLMServerError"
            )
            now = now + timedelta(minutes=30)

        assert outcome.will_retry is False
        row = await _row(factory, claimed.id)
        assert row.status == contract.STATUS_DEAD_LETTER
        # Visible, not silently lost: the reason names what happened.
        assert "exhausted 3 attempts" in row.dead_letter_reason
        assert await store.claim_next(owner="w1", now=now) is None

    async def test_interrupted_is_never_stored(self, store, factory):
        """Vocabulary integrity. ``interrupted`` is a read-time derivation only."""
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k", max_attempts=1)
        claimed = await store.claim_next(owner="w1", now=_now(), lease_seconds=1)
        long_after = _now() + timedelta(hours=1)

        # Attempts exhausted, so nothing will reclaim it and ``interrupted`` is
        # the true reading.
        envelope = await store.describe(claimed.id, now=long_after)
        assert envelope["status"] == contract.STATUS_INTERRUPTED
        assert envelope["recoverable"] is True

        row = await _row(factory, claimed.id)
        assert row.status == contract.STATUS_RUNNING
        assert row.status in contract.STORED_STATUSES
        assert contract.STATUS_INTERRUPTED not in contract.STORED_STATUSES

    async def test_the_reader_envelope_never_exposes_the_lease_owner(self, store):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="secret-host:1234", now=_now())
        envelope = await store.describe(claimed.id)
        assert "secret-host" not in str(envelope)
        assert "lease_owner" not in envelope


class TestCancellation:
    async def test_a_pending_job_is_cancelled_outright(self, store, factory):
        view, _ = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        after = await store.request_cancel(view.id)
        assert after.status == contract.STATUS_CANCELLED
        # No worker will start expensive work that was already called off.
        assert await store.claim_next(owner="w1", now=_now()) is None
        row = await _row(factory, view.id)
        assert row.cancel_requested is True

    async def test_a_running_job_is_only_flagged(self, store, factory):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now())
        after = await store.request_cancel(claimed.id)
        assert after.status == contract.STATUS_RUNNING
        assert after.cancel_requested is True
        row = await _row(factory, claimed.id)
        assert row.status == contract.STATUS_RUNNING

    async def test_cancelling_a_terminal_job_is_a_no_op(self, store):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        claimed = await store.claim_next(owner="w1", now=_now())
        await store.complete(claimed.id, owner="w1")
        after = await store.request_cancel(claimed.id)
        assert after.status == contract.STATUS_COMPLETED


# ===========================================================================
# The worker loop
# ===========================================================================


def _registry(job_type: str, handler) -> HandlerRegistry:
    reg = HandlerRegistry()
    reg.register(job_type, handler)
    return reg


def _worker(store, reg, *, owner="w1", **kw) -> ResearchWorker:
    return ResearchWorker(
        store,
        handlers=reg,
        owner=owner,
        lease_seconds=kw.pop("lease_seconds", 120),
        heartbeat_seconds=kw.pop("heartbeat_seconds", 0.05),
        poll_interval_seconds=kw.pop("poll_interval_seconds", 0.01),
        **kw,
    )


class TestWorkerLoop:
    async def test_run_once_is_false_when_there_is_nothing_to_do(self, store):
        w = _worker(store, _registry(JOB_TYPE, lambda ctx: None))
        assert await w.run_once() is False

    async def test_a_handler_runs_and_the_job_completes(self, store, factory):
        seen: dict = {}
        report_id = uuid.uuid4()

        async def handler(ctx: JobContext) -> JobOutcome:
            seen["payload"] = dict(ctx.payload)
            seen["attempt"] = ctx.attempt
            return JobOutcome(result_type="report", result_ref=report_id)

        await store.enqueue(
            job_type=JOB_TYPE, idempotency_key="k", payload={"ticker": "CFR"}
        )
        w = _worker(store, _registry(JOB_TYPE, handler))
        assert await w.run_once() is True

        assert seen["payload"] == {"ticker": "CFR"}
        assert seen["attempt"] == 1
        row = await _row(factory, (await store.latest_for_base_key("k")).id)
        assert row.status == contract.STATUS_COMPLETED
        assert row.result_ref == report_id

    async def test_the_handler_never_receives_a_session_or_the_store(self, store):
        """Boundary check: a handler that can write its own outcome can lie."""
        captured: dict = {}

        async def handler(ctx: JobContext) -> JobOutcome:
            captured["fields"] = set(vars(ctx))
            return JobOutcome()

        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        await _worker(store, _registry(JOB_TYPE, handler)).run_once()
        assert captured["fields"] == {"job", "payload", "owner", "_worker"}

    async def test_an_unknown_job_type_fails_permanently(self, store, factory):
        view, _ = await store.enqueue(job_type="nobody_handles_this", idempotency_key="k")
        w = _worker(store, HandlerRegistry())
        assert await w.run_once() is True
        row = await _row(factory, view.id)
        assert row.status == contract.STATUS_FAILED
        assert row.error_class == "unknown_job_type"

    async def test_a_transient_handler_failure_is_retried(self, store, factory):
        calls = {"n": 0}

        async def handler(ctx: JobContext) -> JobOutcome:
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("provider slow")
            return JobOutcome()

        view, _ = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        w = _worker(store, _registry(JOB_TYPE, handler))
        await w.run_once()
        row = await _row(factory, view.id)
        assert row.status == contract.STATUS_PENDING
        assert row.attempt == 1

        # Fast-forward past the backoff by making the job due now.
        async with factory() as s:
            job = await s.get(ResearchJob, uuid.UUID(view.id))
            job.available_at = _now() - timedelta(seconds=1)
            await s.commit()

        await w.run_once()
        row = await _row(factory, view.id)
        assert row.status == contract.STATUS_COMPLETED
        assert calls["n"] == 2

    async def test_a_permanent_handler_failure_is_not_retried(self, store, factory):
        async def handler(ctx: JobContext) -> JobOutcome:
            raise ValueError("a bug, not a blip")

        view, _ = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        await _worker(store, _registry(JOB_TYPE, handler)).run_once()
        row = await _row(factory, view.id)
        assert row.status == contract.STATUS_FAILED
        assert row.error_class == "ValueError"

    async def test_a_failure_message_carries_only_the_exception_type(
        self, store, factory
    ):
        """Provider errors have carried credential material. Types cannot."""

        async def handler(ctx: JobContext) -> JobOutcome:
            raise ValueError("api_token=SUPERSECRET123")

        view, _ = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        await _worker(store, _registry(JOB_TYPE, handler)).run_once()
        row = await _row(factory, view.id)
        assert "SUPERSECRET123" not in (row.error_message or "")
        assert "SUPERSECRET123" not in (row.dead_letter_reason or "")
        assert row.error_message == "ValueError"

    async def test_a_job_survives_a_worker_restart(self, store, factory):
        """The V3.0 phase-gate demonstration, end to end.

        Worker A claims a job and is killed mid-run (its task is dropped, exactly
        as an App Service recycle drops one). Worker B finds the lapsed lease,
        reclaims it and finishes. Under V2 this job stayed ``running`` forever
        and the only recovery was a human pressing the button again.
        """
        entered = asyncio.Event()

        async def hangs_forever(ctx: JobContext) -> JobOutcome:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def finishes(ctx: JobContext) -> JobOutcome:
            return JobOutcome(result_type="report")

        view, _ = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")

        worker_a = _worker(
            store, _registry(JOB_TYPE, hangs_forever), owner="A", lease_seconds=1
        )
        task = asyncio.create_task(worker_a.run_once())
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()  # the recycle
        with pytest.raises(asyncio.CancelledError):
            await task

        # The row is exactly as A left it. It has attempts remaining, so the
        # reader is told it is queued for another worker — which is what happens
        # next — rather than that their run stopped.
        long_after = _now() + timedelta(minutes=10)
        assert (await store.describe(view.id, now=long_after))[
            "status"
        ] == contract.STATUS_PENDING

        reclaimed = await store.claim_next(owner="B", now=long_after)
        assert reclaimed is not None
        assert reclaimed.attempt == 2
        outcome = await finishes(None)
        await store.complete(
            reclaimed.id, owner="B", result_type=outcome.result_type
        )
        row = await _row(factory, view.id)
        assert row.status == contract.STATUS_COMPLETED

    async def test_cancellation_is_honoured_at_a_task_boundary(self, store, factory):
        at_boundary = asyncio.Event()
        released = asyncio.Event()
        steps: list[str] = []

        async def handler(ctx: JobContext) -> JobOutcome:
            steps.append("step-1")
            at_boundary.set()
            await released.wait()
            await ctx.checkpoint("council_analysis")
            steps.append("step-2")
            return JobOutcome()

        view, _ = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        w = _worker(store, _registry(JOB_TYPE, handler))
        task = asyncio.create_task(w.run_once())
        await asyncio.wait_for(at_boundary.wait(), timeout=5)
        await store.request_cancel(view.id)
        released.set()
        await asyncio.wait_for(task, timeout=5)

        row = await _row(factory, view.id)
        assert row.status == contract.STATUS_CANCELLED
        # Cooperative: step 1 completed, step 2 never started. Nothing was killed
        # between "fetched" and "persisted".
        assert steps == ["step-1"]

    async def test_a_cancel_before_execution_never_enters_the_handler(
        self, store, factory
    ):
        ran = {"yes": False}

        async def handler(ctx: JobContext) -> JobOutcome:
            ran["yes"] = True
            return JobOutcome()

        view, _ = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        # Cancel a job that is already running (so it is claimable, not cancelled
        # outright) by flagging it directly, then let a worker reclaim it.
        async with factory() as s:
            job = await s.get(ResearchJob, uuid.UUID(view.id))
            job.cancel_requested = True
            await s.commit()

        await _worker(store, _registry(JOB_TYPE, handler)).run_once()
        assert ran["yes"] is False
        row = await _row(factory, view.id)
        assert row.status == contract.STATUS_CANCELLED

    async def test_a_stage_reported_by_the_handler_is_persisted(self, store, factory):
        async def handler(ctx: JobContext) -> JobOutcome:
            await ctx.checkpoint("primary_document_ingestion")
            return JobOutcome()

        view, _ = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        await _worker(store, _registry(JOB_TYPE, handler)).run_once()
        row = await _row(factory, view.id)
        assert row.stage == "primary_document_ingestion"

    async def test_run_forever_stops_when_asked(self, store):
        async def handler(ctx: JobContext) -> JobOutcome:
            return JobOutcome()

        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        w = _worker(store, _registry(JOB_TYPE, handler))
        stop = asyncio.Event()
        task = asyncio.create_task(w.run_forever(stop=stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=5)
        assert (await store.latest_for_base_key("k")).status == (
            contract.STATUS_COMPLETED
        )

    async def test_the_loop_survives_a_store_error(self, store):
        """A loop that dies on a database blip is an outage, not a worker."""
        boom = {"n": 0}

        async def exploding_claim(**kwargs):
            boom["n"] += 1
            raise RuntimeError("database unavailable")

        w = _worker(store, HandlerRegistry(), error_backoff_seconds=0.01)
        w.store = type("S", (), {"claim_next": staticmethod(exploding_claim)})()
        stop = asyncio.Event()
        task = asyncio.create_task(w.run_forever(stop=stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=5)
        assert boom["n"] >= 1


class TestLiveness:
    async def test_the_heartbeat_keeps_a_long_job_owned(self, store, factory):
        """A run longer than its lease must not be stolen from a live worker."""
        release = asyncio.Event()

        async def slow(ctx: JobContext) -> JobOutcome:
            await release.wait()
            return JobOutcome()

        view, _ = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        w = _worker(
            store, _registry(JOB_TYPE, slow), lease_seconds=1, heartbeat_seconds=0.02
        )
        task = asyncio.create_task(w.run_once())
        await _wait_until(lambda: w.heartbeats_sent >= 3)

        # Several beats in, and the lease is still live: a second worker cannot
        # take a job whose owner is still saying it is alive.
        assert await store.claim_next(owner="thief", now=_now()) is None

        release.set()
        await asyncio.wait_for(task, timeout=5)
        row = await _row(factory, view.id)
        assert row.status == contract.STATUS_COMPLETED

    async def test_blocking_the_event_loop_stops_the_heartbeat(self, store):
        """Why CPU-heavy extraction must stay behind ``to_thread``.

        The heartbeat is the liveness proof, and it is an ``async`` task. A
        handler that occupies the loop cannot be interrupted to send one, so the
        lease lapses and the job is reclaimed. This converts "someone blocked the
        loop" from an invisible latency problem into a visible ownership loss —
        which is the point of pairing this with
        ``tests/test_ingestion_event_loop_not_blocked.py``.
        """

        async def blocks_the_loop(ctx: JobContext) -> JobOutcome:
            time.sleep(0.3)  # deliberately synchronous
            return JobOutcome()

        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        w = _worker(
            store,
            _registry(JOB_TYPE, blocks_the_loop),
            lease_seconds=1,
            heartbeat_seconds=0.02,
        )
        await w.run_once()
        assert w.heartbeats_sent == 0

    async def test_the_same_cpu_work_via_to_thread_keeps_heartbeating(self, store):
        """The correct shape: the loop stays free and the lease stays held.

        The thread holds the job open until the loop has proved it is still
        beating, so the assertion is "beats landed while CPU work was in flight"
        rather than "enough beats fitted into 300ms".
        """
        done = threading.Event()

        async def off_the_loop(ctx: JobContext) -> JobOutcome:
            await asyncio.to_thread(done.wait, 10.0)
            return JobOutcome()

        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        w = _worker(
            store,
            _registry(JOB_TYPE, off_the_loop),
            lease_seconds=1,
            heartbeat_seconds=0.02,
        )
        task = asyncio.create_task(w.run_once())
        try:
            await _wait_until(lambda: w.heartbeats_sent >= 3)
        finally:
            done.set()
        await asyncio.wait_for(task, timeout=10)
        assert w.heartbeats_sent >= 3

    async def test_a_worker_that_lost_its_lease_writes_no_result(
        self, store, factory
    ):
        """Split-brain guard: the loser of a reclaim must not overwrite the winner."""
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow(ctx: JobContext) -> JobOutcome:
            entered.set()
            await release.wait()
            return JobOutcome(result_type="stale-result")

        view, _ = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        # Heartbeat far apart so the lease genuinely lapses mid-run.
        w = _worker(
            store, _registry(JOB_TYPE, slow), owner="A", lease_seconds=1,
            heartbeat_seconds=60,
        )
        task = asyncio.create_task(w.run_once())
        await asyncio.wait_for(entered.wait(), timeout=5)

        later = _now() + timedelta(minutes=5)
        reclaimed = await store.claim_next(owner="B", now=later)
        assert reclaimed is not None
        await store.complete(reclaimed.id, owner="B", result_type="real-result")

        release.set()
        await asyncio.wait_for(task, timeout=5)

        row = await _row(factory, view.id)
        assert row.result_type == "real-result"
        assert row.status == contract.STATUS_COMPLETED

    async def test_shutdown_leaves_the_job_reclaimable_not_failed(
        self, store, factory
    ):
        """A restart must not turn an in-flight job into a permanent failure."""
        entered = asyncio.Event()

        async def hangs(ctx: JobContext) -> JobOutcome:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        view, _ = await store.enqueue(job_type=JOB_TYPE, idempotency_key="k")
        w = _worker(store, _registry(JOB_TYPE, hangs), lease_seconds=1)
        task = asyncio.create_task(w.run_once())
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        row = await _row(factory, view.id)
        assert row.status == contract.STATUS_RUNNING
        assert row.status != contract.STATUS_FAILED
        assert await store.claim_next(owner="B", now=_now() + timedelta(minutes=5))



class TestAbandonedJobRetirement:
    """A job whose worker keeps being killed must eventually stop being retried.

    ``fail`` is the only writer of ``dead_letter`` and a killed worker never
    calls it, so the reclaim path is the only place this can be noticed. It is
    noticed there rather than by a reaper process because there is no reaper
    process to run — and the only moment anyone looks at a lapsed lease is when
    a worker is looking for work.
    """

    async def _kill_the_owner(self, store, *, times: int) -> None:
        """Claim and abandon ``times`` times, never reporting a failure."""
        now = _now()
        for _ in range(times):
            claimed = await store.claim_next(owner="doomed", now=now, lease_seconds=1)
            assert claimed is not None
            now = now + timedelta(minutes=5)

    async def test_a_repeatedly_killed_job_is_dead_lettered_not_reclaimed(
        self, store, factory
    ):
        view, _ = await store.enqueue(
            job_type=JOB_TYPE, idempotency_key="k", max_attempts=3
        )
        await self._kill_the_owner(store, times=3)

        row = await _row(factory, view.id)
        assert row.attempt == 3
        assert row.status == contract.STATUS_RUNNING

        # The fourth look retires it instead of handing it to a fourth victim.
        assert await store.claim_next(owner="w4", now=_now() + timedelta(hours=1)) is None
        row = await _row(factory, view.id)
        assert row.status == contract.STATUS_DEAD_LETTER
        assert row.lease_owner is None
        assert "stopped reporting" in row.dead_letter_reason

    async def test_attempts_never_climb_past_the_bound(self, store, factory):
        view, _ = await store.enqueue(
            job_type=JOB_TYPE, idempotency_key="k", max_attempts=2
        )
        await self._kill_the_owner(store, times=2)
        for _ in range(5):
            await store.claim_next(owner="w", now=_now() + timedelta(hours=1))
        row = await _row(factory, view.id)
        assert row.attempt == 2
        assert row.status == contract.STATUS_DEAD_LETTER

    async def test_retiring_one_job_does_not_stop_the_worker_finding_another(
        self, store, factory
    ):
        """The retirement happens mid-scan, so the next candidate is still taken."""
        doomed, _ = await store.enqueue(
            job_type=JOB_TYPE, idempotency_key="doomed", max_attempts=1
        )
        await store.claim_next(owner="doomed-worker", now=_now(), lease_seconds=1)
        healthy, _ = await store.enqueue(job_type=JOB_TYPE, idempotency_key="healthy")

        claimed = await store.claim_next(owner="w2", now=_now() + timedelta(hours=1))
        assert claimed is not None
        assert claimed.id == healthy.id
        assert (await _row(factory, doomed.id)).status == contract.STATUS_DEAD_LETTER

    async def test_a_job_with_attempts_left_is_still_reclaimed(self, store, factory):
        """The bound must not break the recovery it sits inside."""
        view, _ = await store.enqueue(
            job_type=JOB_TYPE, idempotency_key="k", max_attempts=3
        )
        await self._kill_the_owner(store, times=1)
        reclaimed = await store.claim_next(owner="w2", now=_now() + timedelta(hours=1))
        assert reclaimed is not None
        assert reclaimed.attempt == 2
        assert (await _row(factory, view.id)).status == contract.STATUS_RUNNING

    async def test_only_one_worker_retires_a_contended_abandoned_job(self, store):
        await store.enqueue(job_type=JOB_TYPE, idempotency_key="k", max_attempts=1)
        await store.claim_next(owner="doomed", now=_now(), lease_seconds=1)
        later = _now() + timedelta(hours=1)
        results = await asyncio.gather(
            *[store.claim_next(owner=f"w{i}", now=later) for i in range(6)]
        )
        assert all(r is None for r in results)

    async def test_the_worker_loop_reports_a_dead_lettered_job_as_handled(
        self, store, factory
    ):
        """A retired job is not work, so the loop must not spin on it."""

        async def handler(ctx: JobContext) -> JobOutcome:
            raise AssertionError("must never be entered")

        # The worker uses the real clock, so the lease has to have lapsed in real
        # time: create and claim the job an hour ago rather than sleeping.
        past = _now() - timedelta(hours=1)
        view, _ = await store.enqueue(
            job_type=JOB_TYPE, idempotency_key="k", max_attempts=1, now=past
        )
        await store.claim_next(owner="doomed", now=past, lease_seconds=1)
        w = _worker(store, _registry(JOB_TYPE, handler))
        # Nothing claimable -> no work done, and the job is retired on the way.
        assert await w.run_once() is False
        assert (await _row(factory, view.id)).status == contract.STATUS_DEAD_LETTER


class TestFailureClassification:
    async def test_the_council_taxonomy_is_reused_not_reinvented(self):
        from app.services.llm.client import (
            LLMJsonError,
            LLMRateLimitError,
            LLMServerError,
            LLMTimeoutError,
            LLMUnavailableError,
        )

        assert is_transient_failure(LLMRateLimitError("429")) is True
        assert is_transient_failure(LLMServerError("502")) is True
        assert is_transient_failure(LLMTimeoutError("slow")) is True
        # Permanent: retrying reproduces the same result at full research cost.
        assert is_transient_failure(LLMJsonError("malformed")) is False
        assert is_transient_failure(LLMUnavailableError("no credentials")) is False

    async def test_transport_errors_are_transient(self):
        assert is_transient_failure(TimeoutError()) is True
        assert is_transient_failure(ConnectionError()) is True
        assert is_transient_failure(OSError("reset by peer")) is True

    async def test_logic_errors_are_permanent(self):
        assert is_transient_failure(ValueError("bug")) is False
        assert is_transient_failure(KeyError("missing")) is False
        assert is_transient_failure(TypeError("bug")) is False

    async def test_control_flow_signals_are_not_failures(self):
        assert is_transient_failure(JobCancelled()) is False
        assert is_transient_failure(worker_mod.LeaseLostError()) is False


class TestWorkerIdentity:
    async def test_two_workers_in_one_process_never_share_an_identity(self):
        owners = {worker_mod.default_owner_id() for _ in range(50)}
        assert len(owners) == 50

    async def test_the_registry_reports_what_it_can_handle(self):
        reg = HandlerRegistry()
        assert reg.handler_for("nope") is None

        async def handler(ctx):
            return JobOutcome()

        reg.register(JOB_TYPE, handler)
        assert reg.job_types == [JOB_TYPE]
        assert JOB_TYPE in reg
