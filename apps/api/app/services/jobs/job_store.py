"""Durable persistence and leasing for research jobs — V3.0 Slice 2.

WHY THIS MODULE EXISTS
======================
Slice 1 established the *rules* (``job_contract``) and the *shape*
(``models.research_job``). Neither of them touches a database, which is what
made the rules exhaustively testable. This module is the other half: it is the
only place that reads or writes ``research_jobs``, and it applies the contract's
decisions rather than re-deciding anything.

The split matters. Every state change here is computed by ``job_contract`` on an
immutable :class:`~app.services.jobs.job_contract.JobView` and only then written.
So a store bug can lose a write or lose a race, but it cannot invent a
transition the contract forbids — and the contract cannot be quietly forked by a
second implementation living in SQL.

CORRECTNESS UNDER CONCURRENCY IS A COMPARE-AND-SET, NOT A LOCK
==============================================================
Claiming is the one genuinely contended operation: several workers may see the
same claimable row at the same instant and exactly one must win.

The obvious PostgreSQL answer is ``SELECT ... FOR UPDATE SKIP LOCKED``. This
module deliberately does not use it, for two reasons. It does not exist on
SQLite, which is what the offline suite runs on, so the production path would be
tested only in whatever integration coverage happened to exist. And it makes
correctness a property of the *transaction isolation* rather than of the row,
which is harder to reason about and impossible to assert in a unit test.

Instead a claim is an optimistic compare-and-set:

    UPDATE research_jobs
       SET status='running', attempt = :attempt + 1, lease_owner = :owner, ...
     WHERE id = :id AND status = :seen_status AND attempt = :seen_attempt

``attempt`` is the version counter, and every claim increments it. Two workers
that read the same row issue the same guarded UPDATE; the database serialises
them; the first changes ``attempt`` and the second's predicate no longer matches,
so it gets ``rowcount == 0`` and moves on to the next candidate. Exactly one
winner, on any dialect, with no lock held across the handler's runtime.

The same guard protects heartbeat, complete, fail and cancel — there the
predicate is ``lease_owner = :owner``, which is what stops a worker whose lease
already lapsed (and whose job another worker has since taken) from writing a
result for a job it no longer owns.

IDEMPOTENCY IS A DATABASE CONSTRAINT, AND RE-RUNS ARE A NEW GENERATION
======================================================================
``uq_research_jobs_idempotency_key`` makes a duplicate submit fail in the
database rather than in a read-then-write check that loses to a concurrent
double-click.

But a unique key alone would make a job un-re-runnable forever, which is wrong:
"analyse Pandora" is a request a user is entitled to make again next quarter. So
the key a caller supplies is a *base*, and the stored key is ``base#N`` — a
generation. :meth:`JobStore.enqueue` joins the newest generation while it is
still live and starts generation ``N+1`` once it has reached a terminal state.
The unique constraint still does the enforcing; it just enforces it per
generation.

One consequence is worth stating because it is a deliberate improvement on V2
rather than an accident. V2 let a resubmit start a fresh run once the previous
one was judged *stale*, because nothing was going to recover it. Here a lapsed
lease is not stale — it is reclaimable, and a worker will reclaim it. So a
resubmit **joins** it instead, and the caller polls the run that is about to be
resumed rather than paying for a second one.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.research_job import ResearchJob
from app.services.jobs import job_contract as contract
from app.services.jobs.job_contract import FailureOutcome, JobView

logger = logging.getLogger(__name__)

#: Separator between an idempotency base key and its generation number. Chosen
#: because it cannot appear in the generated bases below and reads as a version.
_GENERATION_SEP = "#"

#: How many generations of one base key to look back over when finding the
#: newest. Ordered by ``created_at`` descending, so 1 would very nearly do; the
#: window exists only so a pathological history cannot turn this into a scan.
_GENERATION_SCAN = 25

#: How many claimable candidates one ``claim_next`` call will try before giving
#: up. Each attempt is one guarded UPDATE, and losing every one of them means
#: other workers took them all — in which case there is nothing to do anyway.
_CLAIM_CANDIDATES = 20


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """Treat a naive datetime as UTC.

    SQLite gives back naive datetimes even for ``DateTime(timezone=True)``
    columns. Comparing one of those against an aware ``now`` raises, so every
    value that leaves a row goes through here. This is a storage-layer wart, and
    normalising it here is what keeps it out of the contract.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Row <-> view
# ---------------------------------------------------------------------------


def to_view(row: ResearchJob) -> JobView:
    """The contract's immutable snapshot of one persisted row."""
    return JobView(
        id=str(row.id),
        job_type=row.job_type,
        status=row.status,
        attempt=int(row.attempt or 0),
        max_attempts=int(row.max_attempts or contract.DEFAULT_MAX_ATTEMPTS),
        lease_owner=row.lease_owner,
        lease_expires_at=_aware(row.lease_expires_at),
        available_at=_aware(row.available_at),
        last_heartbeat_at=_aware(row.last_heartbeat_at),
        started_at=_aware(row.started_at),
        finished_at=_aware(row.finished_at),
        cancel_requested=bool(row.cancel_requested),
        stage=row.stage,
        error_class=row.error_class,
        error_message=row.error_message,
        dead_letter_reason=row.dead_letter_reason,
    )


def base_key(idempotency_key: str) -> str:
    """The base of a stored generation key. Inverse of :func:`_generation_key`."""
    head, sep, tail = idempotency_key.rpartition(_GENERATION_SEP)
    if sep and tail.isdigit():
        return head
    return idempotency_key


def _generation_key(base: str, generation: int) -> str:
    return f"{base}{_GENERATION_SEP}{generation}"


def _generation_of(idempotency_key: str) -> int:
    match = re.search(rf"{re.escape(_GENERATION_SEP)}(\d+)$", idempotency_key)
    return int(match.group(1)) if match else 1


def _like_prefix(base: str) -> str:
    """``base#`` with LIKE metacharacters escaped, for a prefix match."""
    escaped = base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}{_GENERATION_SEP}%"


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class LeaseLost(RuntimeError):
    """A write was refused because the caller no longer owns the job."""


class JobStore:
    """Durable job persistence. The ONLY reader/writer of ``research_jobs``.

    Every method opens its own short session from the factory rather than
    borrowing a caller's. A worker holding a job for minutes must not also hold a
    database session for minutes, and the API's request-scoped session is closed
    the moment its 202 is sent — so sharing either one is a defect waiting for a
    long run to expose it.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        lease_seconds: int | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self._factory = session_factory
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    # -- configuration ----------------------------------------------------

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._factory is not None:
            return self._factory
        from app.db.session import async_session_factory

        return async_session_factory

    @property
    def lease_seconds(self) -> int:
        if self._lease_seconds is not None:
            return self._lease_seconds
        from app.core.config import settings

        return int(settings.v3_job_lease_seconds)

    @property
    def max_attempts(self) -> int:
        if self._max_attempts is not None:
            return self._max_attempts
        from app.core.config import settings

        return int(settings.v3_job_max_attempts)

    # -- reads ------------------------------------------------------------

    async def get(self, job_id: uuid.UUID | str) -> JobView | None:
        """One job by id, or None."""
        async with self.session_factory() as session:
            row = await self._load(session, job_id)
            return to_view(row) if row is not None else None

    async def get_payload(self, job_id: uuid.UUID | str) -> dict[str, Any] | None:
        """The job's stored inputs. Never logged — see the module security note."""
        async with self.session_factory() as session:
            row = await self._load(session, job_id)
            if row is None:
                return None
            payload = row.payload_json
            return dict(payload) if isinstance(payload, dict) else {}

    async def describe(
        self, job_id: uuid.UUID | str, *, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """The reader-facing envelope, with ``interrupted`` derived at read time."""
        view = await self.get(job_id)
        if view is None:
            return None
        return contract.describe(view, now or _utcnow())

    async def _load(
        self, session: AsyncSession, job_id: uuid.UUID | str, *, fresh: bool = False
    ) -> ResearchJob | None:
        """One row by id.

        ``fresh=True`` bypasses the identity map. It is required after a guarded
        UPDATE: the statement wrote through the map (``synchronize_session=False``)
        and the app's session factory is built with ``expire_on_commit=False``, so
        an instance loaded earlier in the same session still holds the values we
        just replaced. Reading a job's state back with the state we overwrote is
        the kind of bug that looks like a race and is not one.
        """
        ident = job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(str(job_id))
        if fresh:
            return (
                await session.execute(
                    sa.select(ResearchJob)
                    .where(ResearchJob.id == ident)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
        return await session.get(ResearchJob, ident)

    # -- submission -------------------------------------------------------

    async def enqueue(
        self,
        *,
        job_type: str,
        idempotency_key: str,
        payload: dict[str, Any] | None = None,
        company_id: uuid.UUID | None = None,
        agent_run_id: uuid.UUID | None = None,
        max_attempts: int | None = None,
        available_at: datetime | None = None,
        now: datetime | None = None,
    ) -> tuple[JobView, bool]:
        """Create a job, or join the live one for the same base key.

        Returns ``(view, created)``. ``created`` is False when an existing,
        non-terminal generation was joined — which is the signal a caller uses to
        decide whether to schedule execution, exactly as the V2 path used
        ``scheduled``.

        The job row is committed before this returns. A caller that has a view
        has a durable job, which is the property the whole 202 contract rests on.
        """
        now = _aware(now) or _utcnow()
        base = base_key(idempotency_key)

        async with self.session_factory() as session:
            latest = await self._latest_generation(session, base)
            if latest is not None and not contract.is_terminal(latest.status):
                return to_view(latest), False
            generation = (
                _generation_of(latest.idempotency_key) + 1 if latest is not None else 1
            )
            row = ResearchJob(
                job_type=job_type,
                idempotency_key=_generation_key(base, generation),
                status=contract.STATUS_PENDING,
                payload_json=dict(payload or {}),
                company_id=company_id,
                agent_run_id=agent_run_id,
                attempt=0,
                max_attempts=int(max_attempts or self.max_attempts),
                available_at=_aware(available_at) or now,
                stage=None,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # Another submit won the race for this generation. Its row is the
                # answer to our request too — joining it is the whole point.
                await session.rollback()
                existing = await self._by_key(
                    session, _generation_key(base, generation)
                )
                if existing is None:  # pragma: no cover - defensive
                    raise
                return to_view(existing), False
            await session.refresh(row)
            return to_view(row), True

    async def _latest_generation(
        self, session: AsyncSession, base: str
    ) -> ResearchJob | None:
        rows = (
            (
                await session.execute(
                    sa.select(ResearchJob)
                    .where(
                        ResearchJob.idempotency_key.like(
                            _like_prefix(base), escape="\\"
                        )
                    )
                    .order_by(ResearchJob.created_at.desc())
                    .limit(_GENERATION_SCAN)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return None
        return max(rows, key=lambda r: _generation_of(r.idempotency_key))

    async def _by_key(self, session: AsyncSession, key: str) -> ResearchJob | None:
        return (
            await session.execute(
                sa.select(ResearchJob).where(ResearchJob.idempotency_key == key)
            )
        ).scalar_one_or_none()

    async def latest_for_base_key(self, idempotency_key: str) -> JobView | None:
        """The newest generation for a base key, terminal or not."""
        async with self.session_factory() as session:
            row = await self._latest_generation(session, base_key(idempotency_key))
            return to_view(row) if row is not None else None

    # -- claiming ---------------------------------------------------------

    async def claim_next(
        self,
        *,
        owner: str,
        job_types: list[str] | tuple[str, ...] | None = None,
        now: datetime | None = None,
        lease_seconds: int | None = None,
        candidates: int = _CLAIM_CANDIDATES,
    ) -> JobView | None:
        """Take ownership of the oldest due, unowned job. None when there is none.

        A candidate is a ``pending`` job that is due, or a ``running`` job whose
        lease has lapsed. The second case is recovery: the worker that owned it
        stopped saying it was alive, so the job is available again and its
        ``attempt`` is spent on the way past.
        """
        now = _aware(now) or _utcnow()
        lease = int(lease_seconds or self.lease_seconds)

        async with self.session_factory() as session:
            stmt = (
                sa.select(ResearchJob)
                .where(
                    ResearchJob.status.in_(
                        [contract.STATUS_PENDING, contract.STATUS_RUNNING]
                    ),
                    ResearchJob.available_at <= now,
                    sa.or_(
                        ResearchJob.status == contract.STATUS_PENDING,
                        ResearchJob.lease_expires_at.is_(None),
                        ResearchJob.lease_expires_at <= now,
                    ),
                )
                .order_by(ResearchJob.available_at.asc())
                .limit(max(1, candidates))
            )
            if job_types:
                stmt = stmt.where(ResearchJob.job_type.in_(list(job_types)))
            rows = (await session.execute(stmt)).scalars().all()

            for row in rows:
                row_id = row.id
                view = to_view(row)
                if contract.is_abandoned(view, now):
                    # Nobody else will ever write this job's outcome: its owner
                    # was killed rather than failing, so ``fail`` was never
                    # reached, and it has no attempts left. Retiring it here —
                    # on the claim path — is deliberate: there is no reaper
                    # process to run, and the only moment anyone looks at a
                    # lapsed lease is when a worker is looking for work.
                    await self._retire_abandoned(session, row_id, view, now)
                    continue
                if not contract.is_claimable(view, now):
                    continue
                claimed = contract.claim(
                    view, owner=owner, now=now, lease_seconds=lease
                )
                won = await self._compare_and_set(
                    session,
                    job_id=row_id,
                    predicate=[
                        ResearchJob.status == view.status,
                        ResearchJob.attempt == view.attempt,
                    ],
                    values={
                        "status": claimed.status,
                        "attempt": claimed.attempt,
                        "lease_owner": claimed.lease_owner,
                        "lease_expires_at": claimed.lease_expires_at,
                        "last_heartbeat_at": claimed.last_heartbeat_at,
                        "started_at": claimed.started_at,
                        "updated_at": now,
                    },
                )
                if won:
                    logger.debug(
                        "v3_job_claimed job_id=%s attempt=%s owner=%s",
                        row_id,
                        claimed.attempt,
                        owner,
                    )
                    return claimed
            return None

    async def _retire_abandoned(
        self,
        session: AsyncSession,
        row_id: uuid.UUID,
        view: JobView,
        now: datetime,
    ) -> None:
        """Dead-letter a job whose owner died with no attempts left.

        Guarded on ``attempt`` like a claim, so two workers scanning at once
        cannot both write it — and so a job that was somehow reclaimed between
        the read and the write is left alone.
        """
        outcome = contract.abandon(view, now=now)
        after = outcome.job
        won = await self._compare_and_set(
            session,
            job_id=row_id,
            predicate=[
                ResearchJob.status == contract.STATUS_RUNNING,
                ResearchJob.attempt == view.attempt,
            ],
            values={
                "status": after.status,
                "dead_letter_reason": after.dead_letter_reason,
                "finished_at": after.finished_at,
                "lease_owner": None,
                "lease_expires_at": None,
                "updated_at": now,
            },
        )
        if won:
            logger.warning(
                "v3_job_dead_lettered job_id=%s attempt=%s reason=abandoned",
                row_id,
                view.attempt,
            )

    # -- ownership-guarded writes ----------------------------------------

    async def heartbeat(
        self,
        job_id: uuid.UUID | str,
        *,
        owner: str,
        now: datetime | None = None,
        lease_seconds: int | None = None,
        stage: str | None = None,
    ) -> JobView | None:
        """Renew the lease and optionally report a stage.

        Returns the refreshed view, or **None** when the lease has been lost —
        which is the signal a worker needs to stop, because someone else owns its
        job now and two workers writing one result is worse than one worker
        stopping.
        """
        now = _aware(now) or _utcnow()
        lease = int(lease_seconds or self.lease_seconds)
        async with self.session_factory() as session:
            row = await self._load(session, job_id)
            if row is None:
                return None
            view = to_view(row)
            if view.status != contract.STATUS_RUNNING or view.lease_owner != owner:
                return None
            beat = contract.heartbeat(
                view, owner=owner, now=now, lease_seconds=lease, stage=stage
            )
            won = await self._compare_and_set(
                session,
                job_id=row.id,
                predicate=[
                    ResearchJob.status == contract.STATUS_RUNNING,
                    ResearchJob.lease_owner == owner,
                ],
                values={
                    "lease_expires_at": beat.lease_expires_at,
                    "last_heartbeat_at": beat.last_heartbeat_at,
                    "stage": beat.stage,
                    "updated_at": now,
                },
            )
            if not won:
                return None
            # Re-read rather than trusting the computed view: ``cancel_requested``
            # is written by someone else entirely, and the heartbeat is the only
            # moment a running worker is guaranteed to look at the row.
            refreshed = await self._load(session, row.id, fresh=True)
            return to_view(refreshed) if refreshed is not None else beat

    async def complete(
        self,
        job_id: uuid.UUID | str,
        *,
        owner: str,
        with_warnings: bool = False,
        result_type: str | None = None,
        result_ref: uuid.UUID | None = None,
        stage: str | None = None,
        now: datetime | None = None,
    ) -> JobView | None:
        """Finish successfully and release the lease. None when the lease is lost."""
        now = _aware(now) or _utcnow()
        async with self.session_factory() as session:
            row = await self._load(session, job_id)
            if row is None:
                return None
            view = to_view(row)
            if view.lease_owner != owner:
                return None
            done = contract.complete(view, now=now, with_warnings=with_warnings)
            values: dict[str, Any] = {
                "status": done.status,
                "finished_at": done.finished_at,
                "lease_owner": None,
                "lease_expires_at": None,
                "updated_at": now,
            }
            if result_type is not None:
                values["result_type"] = result_type
            if result_ref is not None:
                values["result_ref"] = result_ref
            if stage is not None:
                values["stage"] = stage
            won = await self._compare_and_set(
                session,
                job_id=row.id,
                predicate=[ResearchJob.lease_owner == owner],
                values=values,
            )
            return done if won else None

    async def fail(
        self,
        job_id: uuid.UUID | str,
        *,
        owner: str,
        transient: bool,
        error_class: str | None = None,
        error_message: str | None = None,
        stage: str | None = None,
        now: datetime | None = None,
    ) -> FailureOutcome | None:
        """Record a failure, letting the contract decide retry vs dead-letter."""
        now = _aware(now) or _utcnow()
        async with self.session_factory() as session:
            row = await self._load(session, job_id)
            if row is None:
                return None
            view = to_view(row)
            if view.lease_owner != owner:
                return None
            outcome = contract.fail(
                view,
                now=now,
                transient=transient,
                error_class=error_class,
                error_message=error_message,
            )
            after = outcome.job
            values: dict[str, Any] = {
                "status": after.status,
                "error_class": after.error_class,
                "error_message": after.error_message,
                "dead_letter_reason": after.dead_letter_reason,
                "finished_at": after.finished_at,
                "lease_owner": None,
                "lease_expires_at": None,
                "updated_at": now,
            }
            if after.available_at is not None:
                values["available_at"] = after.available_at
            if stage is not None:
                values["stage"] = stage
            won = await self._compare_and_set(
                session,
                job_id=row.id,
                predicate=[ResearchJob.lease_owner == owner],
                values=values,
            )
            return outcome if won else None

    async def cancel(
        self,
        job_id: uuid.UUID | str,
        *,
        owner: str,
        stage: str | None = None,
        now: datetime | None = None,
    ) -> JobView | None:
        """Move an owned job to ``cancelled`` at a task boundary."""
        now = _aware(now) or _utcnow()
        async with self.session_factory() as session:
            row = await self._load(session, job_id)
            if row is None:
                return None
            view = to_view(row)
            if view.lease_owner != owner:
                return None
            done = contract.cancel(view, now=now)
            values: dict[str, Any] = {
                "status": done.status,
                "finished_at": done.finished_at,
                "lease_owner": None,
                "lease_expires_at": None,
                "updated_at": now,
            }
            if stage is not None:
                values["stage"] = stage
            won = await self._compare_and_set(
                session,
                job_id=row.id,
                predicate=[ResearchJob.lease_owner == owner],
                values=values,
            )
            return done if won else None

    async def request_cancel(
        self, job_id: uuid.UUID | str, *, now: datetime | None = None
    ) -> JobView | None:
        """Ask a job to stop. Cooperative — a worker observes this, nothing kills it.

        A ``pending`` job is cancelled outright here: there is no worker to
        observe the request, and leaving it queued would mean the next worker
        starts expensive work that has already been called off.
        """
        now = _aware(now) or _utcnow()
        async with self.session_factory() as session:
            row = await self._load(session, job_id)
            if row is None:
                return None
            view = to_view(row)
            if contract.is_terminal(view.status):
                return view
            if view.status == contract.STATUS_PENDING:
                done = contract.cancel(view, now=now)
                won = await self._compare_and_set(
                    session,
                    job_id=row.id,
                    predicate=[ResearchJob.status == contract.STATUS_PENDING],
                    values={
                        "status": done.status,
                        "cancel_requested": True,
                        "finished_at": done.finished_at,
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "updated_at": now,
                    },
                )
                if won:
                    refreshed = await self._load(session, row.id, fresh=True)
                    return to_view(refreshed) if refreshed is not None else done
                # It started while we looked at it: fall through and flag it, so
                # the worker that took it sees the request at its next boundary.
                row = await self._load(session, row.id, fresh=True)
                if row is None:  # pragma: no cover - defensive
                    return None
                view = to_view(row)
            await self._compare_and_set(
                session,
                job_id=row.id,
                predicate=[ResearchJob.status == contract.STATUS_RUNNING],
                values={"cancel_requested": True, "updated_at": now},
            )
            refreshed = await self._load(session, row.id, fresh=True)
            return to_view(refreshed) if refreshed is not None else view

    async def link_agent_run(
        self, job_id: uuid.UUID | str, *, agent_run_id: uuid.UUID
    ) -> None:
        """Attach the audit trail once the workflow that produced it exists."""
        async with self.session_factory() as session:
            await self._compare_and_set(
                session,
                job_id=job_id,
                predicate=[],
                values={"agent_run_id": agent_run_id, "updated_at": _utcnow()},
            )

    # -- the one write primitive -----------------------------------------

    async def _compare_and_set(
        self,
        session: AsyncSession,
        *,
        job_id: uuid.UUID | str,
        predicate: list[Any],
        values: dict[str, Any],
    ) -> bool:
        """One guarded UPDATE. True when this caller won.

        Everything that mutates a job goes through here, so there is exactly one
        place where a write can happen and exactly one place a reviewer has to
        check that a guard is present.
        """
        ident = job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(str(job_id))
        # ``CursorResult`` (not ``Result``) is what a DML statement returns, and
        # ``rowcount`` — how many rows the guard actually matched — is the entire
        # answer to "did this caller win the compare-and-set".
        result = cast(
            "sa.CursorResult[Any]",
            await session.execute(
                sa.update(ResearchJob)
                .where(ResearchJob.id == ident, *predicate)
                .values(**values)
                .execution_options(synchronize_session=False)
            ),
        )
        await session.commit()
        return bool(result.rowcount)
