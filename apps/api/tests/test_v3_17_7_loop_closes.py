"""V3.17.7 — a terminal job must advance its research decision.

THE TEST THIS FILE EXISTS FOR
=============================
V3.17.4 implemented ``controller.complete_round`` correctly and tested it thoroughly.
Every one of those tests called it DIRECTLY. Nothing in production ever did, and the
suite stayed green for three merges while the live loop was open at one joint:

    decision created -> job enqueued -> worker runs it -> job completes -> (nothing)

Production, 2026-09-19: decision ``b5516a97`` (CFR/SW) had ``job_status =
completed_with_warnings`` while the decision itself still read ``queued``, round 0,
``terminal_reason`` NULL, ``evidence_after`` NULL, and ``updated_at`` still equal to its
creation timestamp. ``ux_research_decisions_one_open`` then forbids a second open decision
for that company, so a round that FINISHED SUCCESSFULLY locked the company out for ever.

So the load-bearing test here is :meth:`TestTheConsumer.test_a_terminal_job_advances_its_decision`.
It asserts the decision LEFT the open state — not that a function is importable, not that
an observer is registered, and emphatically not that a JSON field has a key. Registration
is exactly the kind of assertion that passes while the thing is wired to nothing, which is
the defect this file exists to prevent recurring.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.research_decision import OPEN_STATUSES
from app.services.escalation import store
from app.services.escalation.evidence import snapshot_evidence

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
    _engine, factory = engine_and_factory
    async with factory() as s:
        yield s


@pytest.fixture(autouse=True)
def _isolate_observers():  # noqa: ANN202
    """Observers are module-level state; a leak between tests is a false pass."""
    from app.services.jobs import worker

    saved = list(worker._terminal_observers)
    yield
    worker._terminal_observers[:] = saved


async def _company(session) -> uuid.UUID:  # noqa: ANN001
    from app.models.company import Company

    company = Company(
        id=uuid.uuid4(),
        ticker=f"T{uuid.uuid4().hex[:6].upper()}",
        exchange="US",
        name="Loop Close Test Co",
        status="new",
    )
    session.add(company)
    await session.flush()
    return company.id


async def _decision_with_job(session, job_id: uuid.UUID):  # noqa: ANN001
    company_id = await _company(session)
    decision = await store.create_decision(
        session,
        company_id=company_id,
        discovery_run_id=uuid.uuid4(),
        discovery_candidate_id=None,
        source="discovery_council",
        decision="research_next",
        reason="test",
        max_rounds=2,
        # V3.17.8: the round-0 baseline. Without it `complete_round` refuses to
        # measure, which is a different test (see test_v3_17_8_round_zero_baseline).
        evidence_before=(await snapshot_evidence(session, company_id)).to_dict(),
    )
    decision.last_job_id = job_id
    await session.flush()
    await session.commit()
    return decision


class _Job:
    """The minimum a terminal observer is given."""

    def __init__(self, job_id: uuid.UUID, status: str = "completed") -> None:
        self.id = job_id
        self.status = status


def _patch_factory(monkeypatch: pytest.MonkeyPatch, factory: Any) -> None:
    import app.services.escalation.job_hook as hook

    monkeypatch.setattr(hook, "_session_factory", lambda: factory)


# --------------------------------------------------------------------------- #
# The consumer test. This is the one that would have caught the defect.
# --------------------------------------------------------------------------- #


class TestTheConsumer:
    async def test_a_terminal_job_advances_its_decision(
        self, engine_and_factory, session, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        """A completed job must move its decision OUT of every open status."""
        _engine, factory = engine_and_factory
        from app.services.escalation.job_hook import advance_research_decision

        job_id = uuid.uuid4()
        decision = await _decision_with_job(session, job_id)
        assert decision.status in OPEN_STATUSES, "precondition: decision starts open"

        _patch_factory(monkeypatch, factory)
        await advance_research_decision(_Job(job_id), completed=True, dead_lettered=False)

        await session.refresh(decision)
        assert decision.status not in OPEN_STATUSES, (
            "the decision is still open after its job reached a terminal state - "
            "the company is now locked out by ux_research_decisions_one_open"
        )
        assert decision.terminal_reason == "exhausted_no_improvement"
        assert decision.evidence_after_json is not None
        assert decision.improvement_json is not None

    async def test_a_failed_job_never_reads_as_exhausted(
        self, engine_and_factory, session, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        """Infrastructure failure must not be recorded as a finished investigation."""
        _engine, factory = engine_and_factory
        from app.services.escalation.job_hook import advance_research_decision

        job_id = uuid.uuid4()
        decision = await _decision_with_job(session, job_id)

        _patch_factory(monkeypatch, factory)
        await advance_research_decision(
            _Job(job_id, status="dead_letter"), completed=False, dead_lettered=True
        )

        await session.refresh(decision)
        assert decision.terminal_reason != "exhausted_no_improvement", (
            "a provider/infrastructure failure was recorded as a finished investigation"
        )
        # The contract distinguishes a dead letter from a merely incomplete round, and
        # names the more specific one. Both are "research did not happen".
        assert decision.terminal_reason == "job_dead_lettered"

    async def test_a_job_with_no_decision_is_a_no_op(
        self, engine_and_factory, session, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        """Ordinary company research must not be disturbed by the observer."""
        _engine, factory = engine_and_factory
        from app.services.escalation.job_hook import advance_research_decision

        _patch_factory(monkeypatch, factory)
        await advance_research_decision(
            _Job(uuid.uuid4()), completed=True, dead_lettered=False
        )  # must not raise

    async def test_a_replayed_notification_does_not_move_a_closed_decision(
        self, engine_and_factory, session, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        """Only OPEN decisions are advanced, so a second notification is inert."""
        _engine, factory = engine_and_factory
        from app.services.escalation.job_hook import advance_research_decision

        job_id = uuid.uuid4()
        decision = await _decision_with_job(session, job_id)
        _patch_factory(monkeypatch, factory)

        await advance_research_decision(_Job(job_id), completed=True, dead_lettered=False)
        await session.refresh(decision)
        first_reason, first_updated = decision.terminal_reason, decision.updated_at

        await advance_research_decision(_Job(job_id), completed=True, dead_lettered=False)
        await session.refresh(decision)

        assert decision.terminal_reason == first_reason
        assert decision.updated_at == first_updated, "a replay rewrote a closed decision"


# --------------------------------------------------------------------------- #
# The producer side: the worker must actually notify on both terminal paths.
# --------------------------------------------------------------------------- #


class TestTheWorkerNotifies:
    async def test_the_seam_reports_success_and_failure_differently(self) -> None:
        """``completed`` is the distinction complete_round depends on."""
        from app.services.jobs import worker

        seen: list[tuple[bool, bool]] = []

        async def _observer(job, *, completed, dead_lettered):  # noqa: ANN001, ARG001
            seen.append((completed, dead_lettered))

        worker.register_terminal_observer(_observer)
        await worker.notify_terminal(_Job(uuid.uuid4()), completed=True, dead_lettered=False)
        await worker.notify_terminal(_Job(uuid.uuid4()), completed=False, dead_lettered=True)

        assert seen == [(True, False), (False, True)]

    async def test_a_raising_observer_cannot_undo_a_finished_job(self) -> None:
        """The terminal write already happened; an observer must never be fatal."""
        from app.services.jobs import worker

        async def _boom(job, *, completed, dead_lettered):  # noqa: ANN001, ARG001
            raise RuntimeError("observer exploded")

        worker.register_terminal_observer(_boom)
        await worker.notify_terminal(
            _Job(uuid.uuid4()), completed=True, dead_lettered=False
        )  # must not raise

    async def test_registration_is_idempotent(self) -> None:
        """Re-importing the handler module must not double-notify."""
        from app.services.jobs import worker

        calls: list[int] = []

        async def _observer(job, *, completed, dead_lettered):  # noqa: ANN001, ARG001
            calls.append(1)

        worker.register_terminal_observer(_observer)
        worker.register_terminal_observer(_observer)
        await worker.notify_terminal(_Job(uuid.uuid4()), completed=True, dead_lettered=False)

        assert len(calls) == 1


class TestTheWiring:
    async def test_importing_handlers_registers_the_escalation_observer(self) -> None:
        """A worker process that lacks this import strands every decision it touches.

        ``handlers`` must be imported from a state where ``job_hook`` is NOT already
        loaded, or the decorator re-runs by itself and the assertion passes whether or
        not ``handlers`` mentions it — which is precisely the false pass this whole file
        exists to prevent. Verified by mutation: deleting the import from ``handlers``
        fails this test.
        """
        import sys

        import app.services.escalation as escalation_pkg
        from app.services.jobs import worker

        worker.clear_terminal_observers()
        for name in (
            "app.services.jobs.handlers",
            "app.services.escalation.job_hook",
        ):
            sys.modules.pop(name, None)
        # Dropping it from sys.modules is not enough: the parent package still
        # holds `job_hook` as an attribute, and `from ... import job_hook` would
        # find that and re-execute nothing.
        if hasattr(escalation_pkg, "job_hook"):
            delattr(escalation_pkg, "job_hook")

        import app.services.jobs.handlers  # noqa: F401

        names = [getattr(f, "__name__", "") for f in worker._terminal_observers]
        assert "advance_research_decision" in names, (
            "app.services.jobs.handlers does not import the escalation terminal "
            "observer, so a real worker completes escalation jobs and strands "
            "every decision executing on them"
        )
