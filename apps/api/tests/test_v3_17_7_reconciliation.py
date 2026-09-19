"""V3.17.7 — a lost terminal notification must still be repaired.

THE HOLE THIS FILE EXISTS FOR
=============================
``worker.notify_terminal`` calls the escalation observer straight after the job's
terminal write commits, and it deliberately swallows anything the observer raises —
a listener must never undo a finished job.

That safety is also a trap. The observer runs in its own session after its own commit,
so a transient database error there is discarded with the exception, and the job will
never run again to notify a second time. The decision stays OPEN for ever and
``ux_research_decisions_one_open`` locks the company out of every future decision.

So the load-bearing test here is
:meth:`TestRecoveryAfterALostNotification.test_a_swallowed_observer_failure_is_repaired`:
it makes the immediate observer fail exactly as production would, proves the decision is
still open, then runs the REAL production sweep and proves it healed — including that the
evidence and improvement payloads were written and that a replay is inert.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.research_decision import OPEN_STATUSES
from app.services.escalation import store

pytestmark = pytest.mark.asyncio


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):  # noqa: ANN001, ANN202, ARG001
    return "JSON"


@pytest.fixture
def flags(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    import app.core.config as config

    for name, value in (
        ("v3_research_escalation_enabled", True),
        ("v3_durable_jobs_enabled", True),
        ("v3_escalation_max_rounds", 2),
        ("v3_escalation_max_decisions_per_run", 5),
        ("v3_escalation_cost_cap_usd", 0.0),
    ):
        monkeypatch.setattr(config.settings, name, value, raising=False)
    yield


@pytest.fixture
async def engine_and_factory():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, factory
    await engine.dispose()


@pytest.fixture
async def session(engine_and_factory):  # noqa: ANN001, ANN201
    _e, factory = engine_and_factory
    async with factory() as s:
        yield s


@pytest.fixture(autouse=True)
def _isolate_hooks():  # noqa: ANN202
    from app.services.jobs import worker

    obs, sweeps = list(worker._terminal_observers), list(worker._sweep_hooks)
    yield
    worker._terminal_observers[:] = obs
    worker._sweep_hooks[:] = sweeps


async def _company(session) -> uuid.UUID:  # noqa: ANN001
    from app.models.company import Company

    c = Company(
        id=uuid.uuid4(),
        ticker=f"T{uuid.uuid4().hex[:6].upper()}",
        exchange="US",
        name="Reconcile Test Co",
        status="new",
    )
    session.add(c)
    await session.flush()
    return c.id


async def _job(session, status: str) -> uuid.UUID:  # noqa: ANN001
    """A real research_jobs row in the given status."""
    from app.models.research_job import ResearchJob

    job = ResearchJob(
        id=uuid.uuid4(),
        job_type="company_research",
        idempotency_key=f"k-{uuid.uuid4().hex[:10]}",
        status=status,
        attempt=1,
        max_attempts=3,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(job)
    await session.flush()
    return job.id


async def _decision_on_job(session, job_id: uuid.UUID):  # noqa: ANN001
    decision = await store.create_decision(
        session,
        company_id=await _company(session),
        discovery_run_id=uuid.uuid4(),
        discovery_candidate_id=None,
        source="discovery_council",
        decision="research_next",
        reason="test",
        max_rounds=2,
    )
    decision.last_job_id = job_id
    await session.flush()
    await session.commit()
    return decision


def _naive(dt):  # noqa: ANN001, ANN202
    """SQLite hands back a naive datetime where the ORM wrote an aware one.

    Same instant, different tzinfo, and comparing them raises. Normalising here keeps
    the assertions about WHETHER a row was rewritten, which is what they are for.
    """
    return dt.replace(tzinfo=None) if dt is not None and dt.tzinfo else dt


def _patch_factory(monkeypatch: pytest.MonkeyPatch, factory) -> None:  # noqa: ANN001
    """Point BOTH session sources at the test database.

    The sweep opens its own session via ``job_hook._session_factory``, and
    ``recover_stranded_decisions`` enqueues through ``JobStore()``, which resolves
    ``app.db.session.async_session_factory`` lazily and owns a SEPARATE session by
    design. Patching only the first leaves the enqueue talking to the real app database,
    where it raises — and the sweep swallows that, so the test would pass while the path
    under test never ran.
    """
    import app.db.session as db_session
    import app.services.escalation.job_hook as hook

    monkeypatch.setattr(hook, "_session_factory", lambda: factory)
    monkeypatch.setattr(db_session, "async_session_factory", factory, raising=False)


# --------------------------------------------------------------------------- #


class TestRecoveryAfterALostNotification:
    async def test_a_swallowed_observer_failure_is_repaired(
        self, engine_and_factory, session, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        """The whole point: the fast path fails, the durable path heals it."""
        from app.services.jobs import worker as worker_mod

        _e, factory = engine_and_factory
        job_id = await _job(session, "completed")
        decision = await _decision_on_job(session, job_id)

        # 1. The immediate observer fails exactly as a dropped connection would, and
        #    notify_terminal swallows it — by design.
        async def _broken(job, *, completed, dead_lettered):  # noqa: ANN001, ARG001
            raise RuntimeError("transient session failure")

        worker_mod.clear_terminal_observers()
        worker_mod.register_terminal_observer(_broken)
        await worker_mod.notify_terminal(
            type("J", (), {"id": job_id})(), completed=True, dead_lettered=False
        )

        # 2. The decision is stranded — this is the production failure mode.
        await session.refresh(decision)
        assert decision.status in OPEN_STATUSES
        assert decision.terminal_reason is None

        # 3. The REAL production sweep, reached the way the worker reaches it.
        from app.services.escalation.job_hook import reconcile_missed_rounds

        _patch_factory(monkeypatch, factory)
        worker_mod.clear_sweep_hooks()
        worker_mod.register_sweep_hook(reconcile_missed_rounds)
        await worker_mod.run_sweeps()

        # 4. Healed, with the payloads a reader needs.
        await session.refresh(decision)
        assert decision.status not in OPEN_STATUSES, (
            "a lost notification left the decision open - the company is locked out"
        )
        assert decision.terminal_reason == "exhausted_no_improvement"
        assert decision.evidence_after_json is not None
        assert decision.improvement_json is not None
        healed_at = _naive(decision.updated_at)

        # 5. Replay is inert.
        await worker_mod.run_sweeps()
        await session.refresh(decision)
        assert _naive(decision.updated_at) == healed_at, (
            "a second sweep rewrote a closed decision"
        )

    async def test_a_dead_lettered_job_is_repaired_as_not_completed(
        self, engine_and_factory, session, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        from app.services.escalation.controller import reconcile_terminal_decisions

        _e, factory = engine_and_factory
        job_id = await _job(session, "dead_letter")
        decision = await _decision_on_job(session, job_id)

        async with factory() as s:
            await reconcile_terminal_decisions(s)
            await s.commit()

        await session.refresh(decision)
        assert decision.status not in OPEN_STATUSES
        assert decision.terminal_reason != "exhausted_no_improvement", (
            "a permanent failure was recorded as a finished investigation"
        )

    async def test_a_retryable_job_is_never_finalised(
        self, engine_and_factory, session, flags
    ) -> None:  # noqa: ANN001
        """A transient failure returns the job to `pending`; work is still owed."""
        from app.services.escalation.controller import reconcile_terminal_decisions

        _e, factory = engine_and_factory
        job_id = await _job(session, "pending")
        decision = await _decision_on_job(session, job_id)

        async with factory() as s:
            repaired = await reconcile_terminal_decisions(s)
            await s.commit()

        await session.refresh(decision)
        assert repaired == []
        assert decision.status in OPEN_STATUSES
        assert decision.terminal_reason is None

    async def test_a_running_job_is_never_finalised(
        self, engine_and_factory, session, flags
    ) -> None:  # noqa: ANN001
        from app.services.escalation.controller import reconcile_terminal_decisions

        _e, factory = engine_and_factory
        decision = await _decision_on_job(session, await _job(session, "running"))

        async with factory() as s:
            assert await reconcile_terminal_decisions(s) == []

        await session.refresh(decision)
        assert decision.status in OPEN_STATUSES

    async def test_an_already_closed_decision_is_ignored(
        self, engine_and_factory, session, flags
    ) -> None:  # noqa: ANN001
        from app.services.escalation.controller import (
            complete_round,
            reconcile_terminal_decisions,
        )

        _e, factory = engine_and_factory
        decision = await _decision_on_job(session, await _job(session, "completed"))
        await complete_round(session, decision, job_completed=True)
        await session.commit()
        closed_at = _naive(decision.updated_at)

        async with factory() as s:
            assert await reconcile_terminal_decisions(s) == []

        await session.refresh(decision)
        assert _naive(decision.updated_at) == closed_at

    async def test_ordinary_company_research_is_untouched(
        self, engine_and_factory, session, flags
    ) -> None:  # noqa: ANN001
        """A terminal job with no decision pointing at it must be invisible here."""
        from app.services.escalation.controller import reconcile_terminal_decisions

        _e, factory = engine_and_factory
        await _job(session, "completed")
        await session.commit()

        async with factory() as s:
            assert await reconcile_terminal_decisions(s) == []

    async def test_the_sweep_is_bounded(
        self, engine_and_factory, session, flags
    ) -> None:  # noqa: ANN001
        """A backlog is drained over passes, never in one unbounded query."""
        from app.services.escalation.controller import reconcile_terminal_decisions

        _e, factory = engine_and_factory
        for _ in range(5):
            await _decision_on_job(session, await _job(session, "completed"))

        async with factory() as s:
            repaired = await reconcile_terminal_decisions(s, limit=2)
            await s.commit()
        assert len(repaired) == 2


class TestTheOtherStranding:
    """`last_job_id IS NULL` — the decision exists and its job never landed.

    A different failure from a lost notification, with the same consequence: the decision
    is OPEN, nothing will ever advance it, and `ux_research_decisions_one_open` locks the
    company out. `recover_stranded_decisions` was written for it in V3.17.4 and, like
    `complete_round`, had no production caller until this sweep gained one.

    WHAT THIS TEST DOES AND DOES NOT PROVE
    --------------------------------------
    It proves the sweep CALLS it, which is the part that was missing. It does not re-prove
    what that function does — `test_v3_17_3_escalation_controller.py` already covers the
    enqueue, the idempotency and the no-company case against the same schema.

    The behaviour is deliberately not re-tested end to end here: `recover_stranded_decisions`
    enqueues through `JobStore`, which owns a SEPARATE session by design, and the
    in-memory-SQLite harness shares one connection across sessions — so a failure there
    invalidates the connection and the next statement reports "no such table", masking the
    real error rather than showing it. The PostgreSQL file is where that path is honest.
    """

    async def test_the_sweep_also_repairs_decisions_with_no_job(
        self, engine_and_factory, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        from app.services.jobs import worker as worker_mod

        _e, factory = engine_and_factory
        called: list[str] = []

        async def _fake_recover(session, **kw):  # noqa: ANN001, ANN003, ARG001
            called.append("recover")
            return []

        async def _fake_reconcile(session, **kw):  # noqa: ANN001, ANN003, ARG001
            called.append("reconcile")
            return []

        import app.services.escalation.controller as controller

        monkeypatch.setattr(controller, "recover_stranded_decisions", _fake_recover)
        monkeypatch.setattr(controller, "reconcile_terminal_decisions", _fake_reconcile)

        from app.services.escalation.job_hook import reconcile_missed_rounds

        _patch_factory(monkeypatch, factory)
        worker_mod.clear_sweep_hooks()
        worker_mod.register_sweep_hook(reconcile_missed_rounds)
        await worker_mod.run_sweeps()

        assert "recover" in called, (
            "the sweep never calls recover_stranded_decisions - a decision whose job "
            "never landed stays open for ever and locks its company out"
        )
        assert "reconcile" in called
        assert called.index("recover") < called.index("reconcile"), (
            "recovery must run before reconciliation so one sweep does not leave a "
            "decision obviously half-repaired"
        )


class TestTheSweepHasAProductionCaller:
    async def test_the_worker_sweeps_BEFORE_it_claims_any_job(self) -> None:
        """Order matters, and the ordering is what pins the startup call.

        A worker may be replacing one that died between a job's terminal write and its
        notification. Every such decision is OPEN and holding a company's lock, so the
        repair must happen before this process starts taking new work — otherwise the
        first thing the new worker does is add to the backlog it has not yet drained.

        Asserting only "a sweep ran" would NOT pin this: the in-loop call fires on the
        first iteration too. The event order is the assertion.
        """
        import asyncio

        from app.services.jobs import worker as worker_mod
        from app.services.jobs.worker import HandlerRegistry, ResearchWorker

        events: list[str] = []

        async def _sweep() -> None:
            events.append("sweep")

        worker_mod.clear_sweep_hooks()
        worker_mod.register_sweep_hook(_sweep)

        class _RecordingStore:
            async def claim_next(self, **kw):  # noqa: ANN001, ANN003, ANN202, ARG002
                events.append("claim")
                return None

        w = ResearchWorker(
            _RecordingStore(),
            handlers=HandlerRegistry(),
            owner="sweep-order",
            poll_interval_seconds=0.01,
            sweep_interval_seconds=3600,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(w.run_forever(stop=stop))
        await asyncio.sleep(0.15)
        stop.set()
        await task

        assert events, "the worker neither swept nor claimed"
        assert events[0] == "sweep", (
            f"the worker claimed before repairing; order was {events[:3]}. A decision "
            "stranded by a lost notification stays locked while new work is taken."
        )

    async def test_the_sweep_repeats_on_its_cadence(self) -> None:
        """A worker that swept only at startup would repair, then stop repairing.

        The observer can drop a notification at any time, not only around a restart, so
        a long-lived worker must keep asking. Asserting "a sweep ran" would NOT pin this
        — the startup call satisfies that on its own. Repetition is the assertion.
        """
        import asyncio

        from app.services.jobs import worker as worker_mod
        from app.services.jobs.worker import HandlerRegistry, ResearchWorker

        ran: list[int] = []

        async def _sweep() -> None:
            ran.append(1)

        worker_mod.clear_sweep_hooks()
        worker_mod.register_sweep_hook(_sweep)

        class _EmptyStore:
            async def claim_next(self, **kw):  # noqa: ANN001, ANN003, ANN202, ARG002
                return None

        w = ResearchWorker(
            _EmptyStore(),
            handlers=HandlerRegistry(),
            owner="sweep-repeat",
            poll_interval_seconds=0.01,
            sweep_interval_seconds=0.05,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(w.run_forever(stop=stop))
        await asyncio.sleep(0.35)
        stop.set()
        await task

        assert len(ran) >= 3, (
            f"the sweep ran {len(ran)} time(s) in 0.35s at a 0.05s cadence - it is not "
            "repeating, so a notification lost after startup is never repaired"
        )

    async def test_sweeps_do_not_run_on_every_poll(self) -> None:
        """A repair path on a 2-second cadence is a hot loop, not a repair path."""
        import asyncio

        from app.services.jobs import worker as worker_mod
        from app.services.jobs.worker import HandlerRegistry, ResearchWorker

        ran: list[int] = []

        async def _sweep() -> None:
            ran.append(1)

        worker_mod.clear_sweep_hooks()
        worker_mod.register_sweep_hook(_sweep)

        class _EmptyStore:
            async def claim_next(self, **kw):  # noqa: ANN001, ANN003, ANN202, ARG002
                return None

        w = ResearchWorker(
            _EmptyStore(),
            handlers=HandlerRegistry(),
            owner="sweep-cadence",
            poll_interval_seconds=0.001,
            sweep_interval_seconds=3600,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(w.run_forever(stop=stop))
        await asyncio.sleep(0.2)
        stop.set()
        await task

        assert len(ran) == 1, f"sweep ran {len(ran)} times in 0.2s; cadence is not honoured"

    async def test_a_failing_sweep_does_not_kill_the_worker(self) -> None:
        from app.services.jobs import worker as worker_mod

        async def _boom() -> None:
            raise RuntimeError("sweep exploded")

        worker_mod.clear_sweep_hooks()
        worker_mod.register_sweep_hook(_boom)
        await worker_mod.run_sweeps()  # must not raise

    async def test_importing_handlers_registers_the_sweep(self) -> None:
        """The same wiring assertion as the observer, for the repair path."""
        import sys

        import app.services.escalation as escalation_pkg
        from app.services.jobs import worker

        worker.clear_sweep_hooks()
        for name in ("app.services.jobs.handlers", "app.services.escalation.job_hook"):
            sys.modules.pop(name, None)
        if hasattr(escalation_pkg, "job_hook"):
            delattr(escalation_pkg, "job_hook")

        import app.services.jobs.handlers  # noqa: F401

        names = [getattr(f, "__name__", "") for f in worker._sweep_hooks]
        assert "reconcile_missed_rounds" in names
