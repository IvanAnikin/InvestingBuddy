"""The durable job queue, exercised against real PostgreSQL. V3.17 Phase 2.

WHY THIS FILE HAD TO EXIST BEFORE ESCALATION COULD BE ACTIVATED
===============================================================
V3.17 puts *automatically created, paid* research on ``research_jobs``. Before anything
depends on that queue, the queue itself has to be proven. It was not:

* ``test_v3_durable_job_contract.py`` has 63 tests and **touches no database at all** —
  it drives pure ``JobView`` state transitions. That is precisely the "asserts only
  state-setting functions" shape that proves a transition table, not a queue.
* ``test_v3_worker_executor.py`` and ``test_v3_company_research_durable.py`` do use
  ``JobStore``, but **never against PostgreSQL**.

So the parts that only exist in real SQL had never run:

* ``claim_next`` uses ``FOR UPDATE SKIP LOCKED``. **SQLite ignores it silently**, so two
  concurrent claimers on SQLite prove nothing about two workers in production.
* ``uq_research_jobs_idempotency_key`` is a UNIQUE constraint. Its whole value is what it
  does under a race, and a race needs real transactions.
* Lease expiry and reclaim depend on server time and row locking.

This repository has been bitten by exactly this before — an FK violation that aborted a
production transaction while the SQLite suite ran green (``reference_sqlite_fks_off``).

Skipped without ``V3_TEST_POSTGRES_URL``. A guard that never runs is not a guard.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services.jobs import job_contract as contract
from app.services.jobs.job_store import JobStore

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 040",
)

#: Each test owns a unique job_type. The scratch database persists across tests and
#: `claim_next` is global by design, so a shared type makes one test claim another's job
#: — which showed up immediately as "both workers claimed the same job" when in fact they
#: had claimed two different ones.
@pytest.fixture
def job_type() -> str:
    return f"lifecycle_{uuid.uuid4().hex[:10]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
async def factory():  # noqa: ANN201
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(POSTGRES_URL, future=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def store(factory):  # noqa: ANN001, ANN201
    return JobStore(factory, lease_seconds=60, max_attempts=3)


async def _company(factory) -> uuid.UUID:  # noqa: ANN001
    from app.models.company import Company

    async with factory() as s:
        company = Company(
            id=uuid.uuid4(),
            ticker=f"T{uuid.uuid4().hex[:6].upper()}",
            exchange="US",
            name="Lifecycle Test Co",
            status="new",
        )
        s.add(company)
        await s.commit()
        return company.id


def _key() -> str:
    return f"lifecycle:{uuid.uuid4()}"


@requires_postgres
class TestEnqueueAndIdempotency:
    """(1) enqueue and (5) duplicate enqueue."""

    async def test_enqueue_creates_exactly_one_row(self, store, factory, job_type) -> None:  # noqa: ANN001
        company_id = await _company(factory)
        key = _key()

        view, created = await store.enqueue(
            job_type=job_type, idempotency_key=key, company_id=company_id
        )

        assert created is True
        assert view.status == contract.STATUS_PENDING
        again = await store.get(view.id)
        assert again is not None and again.id == view.id

    async def test_a_second_enqueue_joins_rather_than_duplicating(
        self, store, factory, job_type
    ) -> None:  # noqa: ANN001
        """A double-click must answer with the first job, not start a second paid run."""
        company_id = await _company(factory)
        key = _key()

        first, created_1 = await store.enqueue(
            job_type=job_type, idempotency_key=key, company_id=company_id
        )
        second, created_2 = await store.enqueue(
            job_type=job_type, idempotency_key=key, company_id=company_id
        )

        assert created_1 is True
        assert created_2 is False, "a second execution was created for the same key"
        assert second.id == first.id

    async def test_concurrent_enqueues_of_one_key_yield_one_job(
        self, store, factory, job_type
    ) -> None:  # noqa: ANN001
        """THE RACE. This is the test SQLite cannot run.

        Ten simultaneous submits of the same logical work. The UNIQUE constraint is what
        makes exactly one row exist; a read-then-write check in application code would
        let several through here and each one would be a paid research run.
        """
        company_id = await _company(factory)
        key = _key()

        results = await asyncio.gather(
            *[
                store.enqueue(
                    job_type=job_type, idempotency_key=key, company_id=company_id
                )
                for _ in range(10)
            ],
            return_exceptions=True,
        )
        ok = [r for r in results if not isinstance(r, Exception)]
        ids = {v.id for v, _ in ok}
        created_count = sum(1 for _, c in ok if c)

        assert len(ids) == 1, f"the same key produced {len(ids)} distinct jobs"
        assert created_count == 1, f"{created_count} callers each believed they created it"


@requires_postgres
class TestClaimLeaseAndHeartbeat:
    """(2) claim, (3) lease, (4) heartbeat."""

    async def test_claim_takes_the_lease_and_marks_it_running(
        self, store, factory, job_type
    ) -> None:  # noqa: ANN001
        company_id = await _company(factory)
        view, _ = await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )

        claimed = await store.claim_next(owner="worker-a", job_types=[job_type])

        assert claimed is not None
        assert claimed.status == contract.STATUS_RUNNING
        assert claimed.lease_owner == "worker-a"
        assert claimed.lease_expires_at is not None
        assert claimed.lease_expires_at > _utcnow()

    async def test_a_live_lease_hides_the_job_from_a_second_worker(
        self, store, factory, job_type
    ) -> None:  # noqa: ANN001
        """FOR UPDATE SKIP LOCKED, on an engine that actually implements it."""
        company_id = await _company(factory)
        await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )

        first = await store.claim_next(owner="worker-a", job_types=[job_type])
        second = await store.claim_next(owner="worker-b", job_types=[job_type])

        assert first is not None
        assert second is None or second.id != first.id

    async def test_two_workers_racing_never_claim_the_same_job(
        self, store, factory, job_type
    ) -> None:  # noqa: ANN001
        """Concurrent duplicate execution is the failure that costs money twice."""
        company_id = await _company(factory)
        await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )

        a, b = await asyncio.gather(
            store.claim_next(owner="worker-a", job_types=[job_type]),
            store.claim_next(owner="worker-b", job_types=[job_type]),
        )
        claimed = [v for v in (a, b) if v is not None]

        assert len(claimed) == 1, f"{len(claimed)} workers claimed the one job"

    async def test_heartbeat_extends_the_lease_for_the_owner(
        self, store, factory, job_type
    ) -> None:  # noqa: ANN001
        company_id = await _company(factory)
        await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )
        claimed = await store.claim_next(owner="worker-a", job_types=[job_type])
        before = claimed.lease_expires_at

        beat = await store.heartbeat(
            claimed.id, owner="worker-a", now=_utcnow() + timedelta(seconds=5)
        )

        assert beat is not None
        assert beat.lease_expires_at > before

    async def test_a_stranger_cannot_extend_someone_elses_lease(
        self, store, factory, job_type
    ) -> None:  # noqa: ANN001
        """Otherwise a dead worker's peer could keep a job un-reclaimable for ever.

        `heartbeat` RETURNS None on a lost lease rather than raising — that None is the
        signal the worker loop reads to stop, so a caller that treated it as success
        would keep running work it no longer owns.
        """
        company_id = await _company(factory)
        await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )
        claimed = await store.claim_next(owner="worker-a", job_types=[job_type])

        assert await store.heartbeat(claimed.id, owner="worker-b") is None


@requires_postgres
class TestInterruptionAndReclaim:
    """(6) worker interruption, (7) lease recovery, (8) retry accounting, (11) restart."""

    async def test_an_expired_lease_is_reclaimed_by_another_worker(
        self, store, factory, job_type
    ) -> None:  # noqa: ANN001
        """The worker stopped saying it was alive. That is recovery, not failure.

        The lease is allowed to lapse by asking the store for a claim at a later `now`
        rather than by hand-editing the row — a mutated row would be testing the edit.
        """
        company_id = await _company(factory)
        await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )
        first = await store.claim_next(owner="worker-a", job_types=[job_type])

        later = _utcnow() + timedelta(seconds=120)
        reclaimed = await store.claim_next(
            owner="worker-b", job_types=[job_type], now=later
        )

        assert reclaimed is not None
        assert reclaimed.id == first.id
        assert reclaimed.lease_owner == "worker-b"

    async def test_a_reclaim_spends_an_attempt(self, store, factory, job_type) -> None:  # noqa: ANN001
        """A job that reliably kills its worker must not retry for ever."""
        company_id = await _company(factory)
        await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )
        first = await store.claim_next(owner="worker-a", job_types=[job_type])
        later = _utcnow() + timedelta(seconds=120)
        reclaimed = await store.claim_next(
            owner="worker-b", job_types=[job_type], now=later
        )

        assert reclaimed.attempt > first.attempt

    async def test_queued_work_survives_a_restart(self, store, factory, job_type) -> None:  # noqa: ANN001
        """(11) A new JobStore — a new process — finds and claims the committed row."""
        company_id = await _company(factory)
        view, _ = await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )

        fresh = JobStore(factory, lease_seconds=60, max_attempts=3)
        claimed = await fresh.claim_next(owner="worker-after-restart", job_types=[job_type])

        assert claimed is not None
        assert claimed.id == view.id


@requires_postgres
class TestCancellationAndDeadLetter:
    """(9) cancellation, (10) dead-letter."""

    async def test_a_cancel_request_is_recorded_and_honoured(
        self, store, factory, job_type
    ) -> None:  # noqa: ANN001
        company_id = await _company(factory)
        view, _ = await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )

        cancelled = await store.request_cancel(view.id)

        # A PENDING job is cancelled OUTRIGHT by the request: there is no worker to
        # observe it, and leaving it queued would mean the next worker starts expensive
        # work that has already been called off.
        assert cancelled is not None
        assert cancelled.status == contract.STATUS_CANCELLED
        assert cancelled.lease_owner is None, "a terminal job still holds a lease"

        # ...and it is never handed to a worker afterwards.
        after = await store.claim_next(
            owner="worker-a", job_types=[job_type], now=_utcnow() + timedelta(hours=1)
        )
        assert after is None or after.id != view.id

    async def test_a_running_job_is_asked_to_stop_rather_than_killed(
        self, store, factory, job_type
    ) -> None:  # noqa: ANN001
        """Cooperative cancellation: the flag is set, the worker observes it.

        Killing a claimed job from underneath its worker would leave the research run
        half-written with nothing to reconcile it.
        """
        company_id = await _company(factory)
        await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )
        claimed = await store.claim_next(owner="worker-a", job_types=[job_type])

        requested = await store.request_cancel(claimed.id)
        assert requested.status == contract.STATUS_RUNNING, "a running job was killed"
        assert requested.cancel_requested is True

        # The worker observes the flag at a task boundary and stands down.
        stopped = await store.cancel(claimed.id, owner="worker-a")
        assert stopped.status == contract.STATUS_CANCELLED
        assert stopped.lease_owner is None

    async def test_repeated_transient_failure_ends_in_dead_letter(
        self, store, factory, job_type
    ) -> None:  # noqa: ANN001
        """A deterministic failure must stop, with a reason, rather than retry for ever."""
        company_id = await _company(factory)
        await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )

        now = _utcnow()
        final = None
        for _ in range(6):
            claimed = await store.claim_next(
                owner="worker-a", job_types=[job_type], now=now
            )
            if claimed is None:
                break
            outcome = await store.fail(
                claimed.id,
                owner="worker-a",
                error_class="ProviderTimeout",
                error_message="upstream did not answer",
                transient=True,
                now=now,
            )
            final = outcome.job
            if final.status in contract.TERMINAL:
                break
            now = now + timedelta(minutes=30)

        assert final is not None
        assert final.status == contract.STATUS_DEAD_LETTER
        assert final.dead_letter_reason, "dead-lettered with no reason recorded"

    async def test_a_dead_lettered_job_is_never_claimed_again(
        self, store, factory, job_type
    ) -> None:  # noqa: ANN001
        company_id = await _company(factory)
        view, _ = await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )
        now = _utcnow()
        for _ in range(6):
            claimed = await store.claim_next(
                owner="w", job_types=[job_type], now=now
            )
            if claimed is None:
                break
            got = (await store.fail(
                claimed.id,
                owner="w",
                error_class="Boom",
                error_message="x",
                transient=True,
                now=now,
            )).job
            if got.status in contract.TERMINAL:
                break
            now = now + timedelta(minutes=30)

        after = await store.claim_next(
            owner="w2", job_types=[job_type], now=now + timedelta(days=1)
        )
        assert after is None or after.id != view.id


@requires_postgres
class TestCompletion:
    """(12) successful completion, exactly once."""

    async def test_completion_releases_the_lease_and_is_terminal(
        self, store, factory, job_type
    ) -> None:  # noqa: ANN001
        company_id = await _company(factory)
        await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )
        claimed = await store.claim_next(owner="worker-a", job_types=[job_type])

        done = await store.complete(claimed.id, owner="worker-a")

        assert done.status == contract.STATUS_COMPLETED
        assert done.lease_owner is None
        assert done.finished_at is not None

    async def test_a_completed_job_is_not_claimable_again(
        self, store, factory, job_type
    ) -> None:  # noqa: ANN001
        """"Exactly once" is only true if the row stops being a candidate."""
        company_id = await _company(factory)
        view, _ = await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )
        claimed = await store.claim_next(owner="worker-a", job_types=[job_type])
        await store.complete(claimed.id, owner="worker-a")

        again = await store.claim_next(
            owner="worker-b", job_types=[job_type], now=_utcnow() + timedelta(hours=1)
        )

        assert again is None or again.id != view.id


# --------------------------------------------------------------------------- #
# V3.17.7 — reconciliation, on the database that actually implements the locking
# --------------------------------------------------------------------------- #


@requires_postgres
class TestReconciliationOnRealPostgres:
    """`FOR UPDATE ... SKIP LOCKED` is a no-op on SQLite.

    Every SQLite test of `reconcile_terminal_decisions` therefore proves its FILTERING
    and nothing about its CONCURRENCY. Two workers sweeping the same backlog is the
    normal case the moment a second process exists, so it is proven here or not at all.
    """

    async def _discovery_run(self, factory) -> uuid.UUID:  # noqa: ANN001
        """A REAL discovery run row.

        `research_decisions.discovery_run_id` carries a foreign key. SQLite runs this
        suite with foreign keys OFF, so an invented uuid is accepted there and rejected
        here — which is exactly what these PostgreSQL tests exist to catch, and did.
        """
        from app.models.discovery import DiscoveryRun

        async with factory() as s:
            run = DiscoveryRun(id=uuid.uuid4(), status="completed")
            s.add(run)
            await s.commit()
            return run.id

    async def _decision_on_terminal_job(self, factory, job_type: str, status: str):  # noqa: ANN001, ANN202
        from app.services.escalation import store as esc_store

        company_id = await _company(factory)
        run_id = await self._discovery_run(factory)
        store = JobStore(factory, lease_seconds=60, max_attempts=3)
        view, _ = await store.enqueue(
            job_type=job_type, idempotency_key=_key(), company_id=company_id
        )
        claimed = await store.claim_next(owner="w", job_types=[job_type])
        assert claimed is not None
        if status in (contract.STATUS_COMPLETED, contract.STATUS_COMPLETED_WITH_WARNINGS):
            await store.complete(claimed.id, owner="w")
        else:
            for _ in range(5):
                await store.fail(
                    claimed.id, owner="w", transient=False, error_class="E", error_message="E"
                )
                nxt = await store.claim_next(owner="w", job_types=[job_type])
                if nxt is None:
                    break

        async with factory() as s:
            from app.services.escalation.evidence import snapshot_evidence

            decision = await esc_store.create_decision(
                s,
                company_id=company_id,
                discovery_run_id=run_id,
                discovery_candidate_id=None,
                source="discovery_council",
                decision="research_next",
                reason="reconcile-pg",
                max_rounds=2,
                # V3.17.8: the round-0 baseline. A decision without one is refused, and
                # a round without one is closed as unmeasurable rather than reconciled.
                evidence_before=(await snapshot_evidence(s, company_id)).to_dict(),
            )
            decision.last_job_id = view.id
            await s.commit()
            return decision.id

    async def test_two_concurrent_sweeps_do_not_both_close_one_decision(
        self, factory, job_type, monkeypatch
    ):  # noqa: ANN001
        """SKIP LOCKED must make the sets disjoint, not contended."""
        import asyncio

        import app.core.config as config
        from app.models.research_decision import ResearchDecision
        from app.services.escalation.controller import reconcile_terminal_decisions

        for name, value in (
            ("v3_research_escalation_enabled", True),
            ("v3_durable_jobs_enabled", True),
            ("v3_escalation_max_rounds", 2),
            ("v3_escalation_cost_cap_usd", 0.0),
        ):
            monkeypatch.setattr(config.settings, name, value, raising=False)

        decision_id = await self._decision_on_terminal_job(
            factory, job_type, contract.STATUS_COMPLETED
        )

        async def _sweep():
            async with factory() as s:
                out = await reconcile_terminal_decisions(s)
                await s.commit()
                return out

        first, second = await asyncio.gather(_sweep(), _sweep(), return_exceptions=True)
        for r in (first, second):
            assert not isinstance(r, Exception), f"a concurrent sweep raised: {r!r}"

        claimed_by = [r for r in (first, second) if decision_id in r]
        assert len(claimed_by) == 1, (
            "the same decision was reconciled by both sweeps - SKIP LOCKED is not "
            "protecting the round, and complete_round ran twice"
        )

        async with factory() as s:
            row = await s.get(ResearchDecision, decision_id)
            assert row.terminal_reason, "the decision was left open by both sweeps"

    async def test_reconciliation_is_idempotent_across_repeated_sweeps(
        self, factory, job_type, monkeypatch
    ):  # noqa: ANN001
        import app.core.config as config
        from app.models.research_decision import ResearchDecision
        from app.services.escalation.controller import reconcile_terminal_decisions

        for name, value in (
            ("v3_research_escalation_enabled", True),
            ("v3_durable_jobs_enabled", True),
            ("v3_escalation_max_rounds", 2),
            ("v3_escalation_cost_cap_usd", 0.0),
        ):
            monkeypatch.setattr(config.settings, name, value, raising=False)

        decision_id = await self._decision_on_terminal_job(
            factory, job_type, contract.STATUS_COMPLETED
        )

        async with factory() as s:
            assert decision_id in await reconcile_terminal_decisions(s)
            await s.commit()
        async with factory() as s:
            row = await s.get(ResearchDecision, decision_id)
            first_updated = row.updated_at

        for _ in range(3):
            async with factory() as s:
                assert decision_id not in await reconcile_terminal_decisions(s)
                await s.commit()

        async with factory() as s:
            row = await s.get(ResearchDecision, decision_id)
            assert row.updated_at == first_updated, "a replayed sweep rewrote the decision"
