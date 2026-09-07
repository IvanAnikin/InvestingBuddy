"""The durable job contract — V3.0 Slice 1.

WHY THIS MODULE EXISTS
======================
V2 job *state* is durable. V2 job *execution* is not: the work runs in the API
process via FastAPI ``BackgroundTasks`` at six call sites, so an App Service
recycle kills every in-flight run. ``research_job`` says so in its own docstring
and does the honest thing with it — a job whose owning process is gone reads as
``interrupted`` rather than pretending to still be running.

This module is the first half of fixing that. It is the *contract*: which
transitions are legal, when a job may be claimed, how long a claim lasts, what
happens after a failure, and how a reader-facing status is derived. It has no
database access, no network, no clock of its own beyond what a caller passes in,
and no knowledge of a broker. That is deliberate — the rules are the part worth
testing exhaustively, and they are only testable exhaustively if nothing else is
in the way.

WHAT "DURABLE" WILL MEAN, AND WHAT IT ALREADY MEANS
===================================================
Slice 1 changes no entry point. Nothing calls this yet. What it establishes is
the shape that Slice 2 (worker loop) and Slice 3 (routing ``/company-research``
through it) build on, so those slices are small and reviewable instead of one
enormous "make jobs durable" branch.

THE VOCABULARY IS V2'S, ON PURPOSE
==================================
A queue wants words like ``queued`` / ``claimed`` / ``succeeded``. This module
does not use them. ``research_job`` already owns a status vocabulary that the web
app polls, that the admin console renders and that a body of tests asserts, and
forking it would fork the polling contract for no gain. So the V2 statuses are
imported, never redefined, and V3 adds exactly two terminal states that genuinely
did not exist before: ``dead_letter`` and ``cancelled``.

``tests/test_v3_durable_job_contract.py`` asserts the superset relationship, so a
future edit to either module cannot silently let the two drift apart.

LEASING MAKES ``interrupted`` PROVABLE
======================================
This is the real upgrade, and it is worth being precise about.

V2 derives ``interrupted`` two ways: elapsed time past a derived worst case, or —
much better — the observation that a job started before this process booted and
therefore cannot be running in it. The second is a certainty rather than a
heuristic, but it only holds at exactly one gunicorn worker, so
``research_job.is_orphaned`` disables itself at two or more. That is correct, and
it is also a ceiling on the deployment.

A lease removes the ceiling. A worker claims a job until ``lease_expires_at`` and
heartbeats to extend it. An expired lease is not a guess about elapsed time — it
is the owner having stopped saying it was alive, which is the thing we actually
wanted to know all along. It holds for any number of workers, on any host, and it
makes reclaim safe: whoever wins the compare-and-set owns the job, and there is
exactly one winner.

``interrupted`` stays DERIVED and is never stored, for the same reason V2 gives:
a stored status needs a writer that is running, which is precisely what is absent
in the case it describes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.research_job import (
    HAS_RESULT,
    IN_FLIGHT,
    STATUS_COMPLETED,
    STATUS_COMPLETED_WITH_WARNINGS,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PENDING,
    STATUS_RUNNING,
)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: A job that exhausted its attempts. Distinct from ``failed`` because the two
#: call for different human responses: a ``failed`` job hit a permanent error and
#: re-running it will fail the same way, while a ``dead_letter`` job hit
#: transient errors ``max_attempts`` times and may well succeed later. Overloading
#: ``failed`` for both would erase exactly the distinction an operator needs.
STATUS_DEAD_LETTER = "dead_letter"

#: A job cancelled at a task boundary. Cooperative — see ``cancel_requested``.
STATUS_CANCELLED = "cancelled"

#: V3 additions, kept separate so the superset relationship with V2 is explicit
#: rather than something a reader has to reconstruct by diffing two frozensets.
V3_ONLY_STATUSES: frozenset[str] = frozenset({STATUS_DEAD_LETTER, STATUS_CANCELLED})

#: Every stored status. ``interrupted`` is absent BY CONSTRUCTION: it is derived
#: at read time and must never be written. See the module docstring.
STORED_STATUSES: frozenset[str] = (
    frozenset(
        {
            STATUS_PENDING,
            STATUS_RUNNING,
            STATUS_COMPLETED,
            STATUS_COMPLETED_WITH_WARNINGS,
            STATUS_FAILED,
        }
    )
    | V3_ONLY_STATUSES
)

#: Terminal for V3 = V2's terminal set plus the two new ends.
TERMINAL: frozenset[str] = (
    HAS_RESULT | frozenset({STATUS_FAILED}) | V3_ONLY_STATUSES
)

#: Legal transitions. Anything not listed here is a bug, and
#: :func:`assert_transition` refuses it loudly rather than letting a job reach an
#: impossible state that a later reader would have to interpret.
_LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_PENDING: frozenset({STATUS_RUNNING, STATUS_CANCELLED}),
    # ``running -> running`` is legal: it is a lease RECLAIM by a new owner after
    # the previous one stopped heartbeating, which is a real and expected event
    # rather than a no-op.
    STATUS_RUNNING: frozenset(
        {
            STATUS_RUNNING,
            STATUS_PENDING,  # transient failure, scheduled for another attempt
            STATUS_COMPLETED,
            STATUS_COMPLETED_WITH_WARNINGS,
            STATUS_FAILED,
            STATUS_DEAD_LETTER,
            STATUS_CANCELLED,
        }
    ),
    STATUS_COMPLETED: frozenset(),
    STATUS_COMPLETED_WITH_WARNINGS: frozenset(),
    STATUS_FAILED: frozenset(),
    STATUS_DEAD_LETTER: frozenset(),
    STATUS_CANCELLED: frozenset(),
}


class IllegalTransition(RuntimeError):
    """A transition the contract does not permit."""


def is_terminal(status: str) -> bool:
    return status in TERMINAL


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in _LEGAL_TRANSITIONS.get(from_status, frozenset())


def assert_transition(from_status: str, to_status: str) -> None:
    """Raise unless ``from_status -> to_status`` is legal."""
    if not can_transition(from_status, to_status):
        raise IllegalTransition(f"{from_status!r} -> {to_status!r} is not permitted")


# ---------------------------------------------------------------------------
# Leasing
# ---------------------------------------------------------------------------

#: How long a claim lasts before another worker may reclaim it.
#:
#: Must comfortably exceed the heartbeat interval — a worker that is alive and
#: working has to be able to renew before expiry, or healthy jobs get stolen
#: mid-run. It must ALSO stay well under the abandoned-job threshold
#: ``research_job.stale_after_minutes`` (currently ≥55 min), because a lease is
#: the FASTER of the two detectors and a lease longer than the elapsed-time rule
#: would make it pointless.
DEFAULT_LEASE_SECONDS = 120

#: How often a running worker renews its lease. One third of the lease, so two
#: consecutive missed heartbeats still leave room for a third to land.
DEFAULT_HEARTBEAT_SECONDS = 40

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE_SECONDS = 5.0
DEFAULT_BACKOFF_MAX_SECONDS = 300.0


def lease_expiry(now: datetime, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> datetime:
    """When a claim taken at ``now`` expires."""
    return _aware(now) + timedelta(seconds=lease_seconds)


def is_lease_expired(lease_expires_at: datetime | None, now: datetime) -> bool:
    """True when a claim has lapsed.

    A ``running`` job with NO lease is treated as expired. That is the V2 legacy
    case — a job written by the old ``BackgroundTasks`` path has no lease because
    nothing ever took one — and calling it expired is right: nothing is holding
    it, so it is reclaimable.
    """
    if lease_expires_at is None:
        return True
    return _aware(lease_expires_at) <= _aware(now)


def backoff_seconds(
    attempt: int,
    *,
    base: float = DEFAULT_BACKOFF_BASE_SECONDS,
    maximum: float = DEFAULT_BACKOFF_MAX_SECONDS,
) -> float:
    """Exponential backoff for ``attempt`` (1-based), capped at ``maximum``.

    No jitter here on purpose: this is a pure function and jitter would make it
    untestable. A caller that runs many workers should add jitter at the call
    site, where it can also be disabled in tests.
    """
    if attempt < 1:
        return 0.0
    # ``base * 2**(attempt-1)``, computed so a large attempt cannot overflow.
    exponent = min(attempt - 1, 32)
    return min(float(base) * float(2**exponent), float(maximum))


# ---------------------------------------------------------------------------
# The job snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobView:
    """An immutable snapshot of one job, decoupled from the ORM.

    The contract reasons over this rather than over a SQLAlchemy instance so the
    rules can be tested with no database, no session and no event loop — and so a
    future change of persistence does not touch the rules.
    """

    id: str
    job_type: str
    status: str
    attempt: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    available_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested: bool = False
    stage: str | None = None
    error_class: str | None = None
    error_message: str | None = None
    dead_letter_reason: str | None = None

    #: Lineage, carried so a reader can be answered from one snapshot. No RULE
    #: in this module reasons over these — they are here because the alternative
    #: is a second round trip for every poll, not because the contract needs them.
    company_id: str | None = None
    agent_run_id: str | None = None
    result_type: str | None = None
    result_ref: str | None = None


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------


def attempts_exhausted(job: JobView) -> bool:
    """True when this job has already had every attempt it is entitled to."""
    return job.attempt >= job.max_attempts


def is_abandoned(job: JobView, now: datetime) -> bool:
    """True when a ``running`` job's owner is gone AND it has no attempts left.

    This is the case nothing else can see. A failure that a worker *observes* is
    reported through :func:`fail`, which dead-letters once attempts run out. But
    a worker that is killed — OOM, ``SIGKILL``, an App Service recycle — reports
    nothing at all, so a job that reliably kills whichever worker takes it never
    reaches :func:`fail` and therefore never reaches ``dead_letter``.

    Without this predicate such a job is reclaimed forever, one dead worker at a
    time, with ``attempt`` climbing past ``max_attempts`` and nothing ever
    stopping it. That is the exact shape of an outage that keeps restarting
    itself, which is the thing counting a lost attempt was supposed to prevent —
    the counter existed, the bound on it did not.
    """
    return (
        job.status == STATUS_RUNNING
        and is_lease_expired(job.lease_expires_at, now)
        and attempts_exhausted(job)
    )


def is_claimable(job: JobView, now: datetime) -> bool:
    """True when a worker may take this job.

    Four independent reasons a job is NOT claimable, and each matters:

    * it is terminal — there is nothing left to do;
    * it is not yet due (``available_at`` in the future) — this is how retry
      backoff is expressed, so ignoring it would turn a backoff into a hot loop;
    * it is ``running`` under a live lease — someone else owns it;
    * it is abandoned with no attempts left (:func:`is_abandoned`) — handing it
      to a fifth worker to be killed by is not recovery.

    A cancel request does not block a claim. The claim is what lets a worker
    observe the request and move the job to ``cancelled`` at a task boundary; a
    pending job nobody may claim would sit in ``pending`` forever wearing a
    cancellation nobody acted on.

    The attempt bound is applied ONLY to the reclaim branch. A ``pending`` job
    with exhausted attempts cannot arise from this contract — :func:`fail`
    dead-letters instead of scheduling one — so refusing it here would only ever
    block a deliberate operator reset, which is a legitimate thing to do.
    """
    if is_terminal(job.status):
        return False
    if job.available_at is not None and _aware(job.available_at) > _aware(now):
        return False
    if job.status == STATUS_RUNNING:
        return is_lease_expired(job.lease_expires_at, now) and not attempts_exhausted(
            job
        )
    return job.status == STATUS_PENDING


def claim(
    job: JobView,
    *,
    owner: str,
    now: datetime,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> JobView:
    """Take ownership of ``job``. Raises unless it is claimable.

    Increments ``attempt`` on every claim, INCLUDING a reclaim of a job whose
    previous owner died. That is intentional: an attempt that was started and
    lost is an attempt spent. Not counting it would let a job that reliably kills
    its worker be retried forever, which is the exact shape of an outage that
    keeps restarting itself.
    """
    if not is_claimable(job, now):
        raise IllegalTransition(
            f"job {job.id} is not claimable (status={job.status!r}, "
            f"lease_expires_at={job.lease_expires_at!r})"
        )
    assert_transition(job.status, STATUS_RUNNING)
    now = _aware(now)
    return replace(
        job,
        status=STATUS_RUNNING,
        attempt=job.attempt + 1,
        lease_owner=owner,
        lease_expires_at=lease_expiry(now, lease_seconds=lease_seconds),
        last_heartbeat_at=now,
        started_at=job.started_at or now,
    )


def heartbeat(
    job: JobView,
    *,
    owner: str,
    now: datetime,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    stage: str | None = None,
) -> JobView:
    """Extend the lease. Only the current owner may.

    Refusing a heartbeat from a non-owner is what makes reclaim safe. Once a
    lease lapses and another worker takes the job, the original worker — which
    may still be alive and mid-request — must not be able to extend a lease it no
    longer holds, or two workers would believe they own the same job.
    """
    if job.status != STATUS_RUNNING:
        raise IllegalTransition(f"cannot heartbeat a job in {job.status!r}")
    if job.lease_owner != owner:
        raise IllegalTransition(
            f"job {job.id} is owned by {job.lease_owner!r}, not {owner!r}"
        )
    now = _aware(now)
    return replace(
        job,
        lease_expires_at=lease_expiry(now, lease_seconds=lease_seconds),
        last_heartbeat_at=now,
        stage=stage if stage is not None else job.stage,
    )


# ---------------------------------------------------------------------------
# Completion and failure
# ---------------------------------------------------------------------------


def complete(
    job: JobView, *, now: datetime, with_warnings: bool = False
) -> JobView:
    """Finish successfully, releasing the lease."""
    to_status = STATUS_COMPLETED_WITH_WARNINGS if with_warnings else STATUS_COMPLETED
    assert_transition(job.status, to_status)
    return replace(
        job,
        status=to_status,
        finished_at=_aware(now),
        lease_owner=None,
        lease_expires_at=None,
    )


def cancel(job: JobView, *, now: datetime) -> JobView:
    """Move a job to ``cancelled`` at a task boundary."""
    assert_transition(job.status, STATUS_CANCELLED)
    return replace(
        job,
        status=STATUS_CANCELLED,
        finished_at=_aware(now),
        lease_owner=None,
        lease_expires_at=None,
    )


@dataclass(frozen=True)
class FailureOutcome:
    """What the contract decided to do about one failure."""

    job: JobView
    will_retry: bool
    retry_at: datetime | None
    reason: str


def fail(
    job: JobView,
    *,
    now: datetime,
    transient: bool,
    error_class: str | None = None,
    error_message: str | None = None,
    base_backoff: float = DEFAULT_BACKOFF_BASE_SECONDS,
    max_backoff: float = DEFAULT_BACKOFF_MAX_SECONDS,
) -> FailureOutcome:
    """Decide what a failure means: retry, give up, or dead-letter.

    The transient/permanent split is the same judgement the council already
    makes (``llm.client.is_transient_llm_error``): a rate limit, a provider 5xx
    or a timeout may be retried; malformed output, missing credentials and every
    other error are permanent and retrying them only spends budget to reproduce
    the same result.

    A permanent failure goes straight to ``failed`` with attempts left on the
    clock, because attempts are not the resource being protected — the point is
    not to burn a research budget re-running something that cannot work.
    """
    now = _aware(now)
    base = replace(
        job,
        error_class=error_class,
        error_message=error_message,
        lease_owner=None,
        lease_expires_at=None,
    )

    if not transient:
        assert_transition(job.status, STATUS_FAILED)
        return FailureOutcome(
            job=replace(base, status=STATUS_FAILED, finished_at=now),
            will_retry=False,
            retry_at=None,
            reason="permanent error",
        )

    if job.attempt >= job.max_attempts:
        assert_transition(job.status, STATUS_DEAD_LETTER)
        reason = (
            f"exhausted {job.max_attempts} attempts; last error: "
            f"{error_class or 'unknown'}"
        )
        return FailureOutcome(
            job=replace(
                base,
                status=STATUS_DEAD_LETTER,
                finished_at=now,
                dead_letter_reason=reason,
            ),
            will_retry=False,
            retry_at=None,
            reason=reason,
        )

    delay = backoff_seconds(job.attempt, base=base_backoff, maximum=max_backoff)
    retry_at = now + timedelta(seconds=delay)
    assert_transition(job.status, STATUS_PENDING)
    return FailureOutcome(
        job=replace(base, status=STATUS_PENDING, available_at=retry_at),
        will_retry=True,
        retry_at=retry_at,
        reason=f"transient error; attempt {job.attempt}/{job.max_attempts}",
    )


def abandon(job: JobView, *, now: datetime) -> FailureOutcome:
    """Dead-letter a job whose owner died with no attempts left.

    Separate from :func:`fail` because the two say different things and an
    operator needs the difference: ``fail`` reports an error somebody *caught*,
    while this reports that nobody was left to catch anything. Collapsing them
    would label a series of killed workers as "last error: unknown", which reads
    as a missing detail rather than as the diagnosis it actually is.
    """
    if not is_abandoned(job, now):
        raise IllegalTransition(
            f"job {job.id} is not abandoned (status={job.status!r}, "
            f"attempt={job.attempt}/{job.max_attempts})"
        )
    assert_transition(job.status, STATUS_DEAD_LETTER)
    reason = (
        f"the worker owning this job stopped reporting on all "
        f"{job.max_attempts} attempts; no error was ever reported, which is "
        f"what a killed worker looks like"
    )
    return FailureOutcome(
        job=replace(
            job,
            status=STATUS_DEAD_LETTER,
            finished_at=_aware(now),
            lease_owner=None,
            lease_expires_at=None,
            dead_letter_reason=reason,
        ),
        will_retry=False,
        retry_at=None,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Reader-facing view
# ---------------------------------------------------------------------------


def derive_status(job: JobView, now: datetime) -> str:
    """The status a HUMAN should see. Never written back.

    A ``running`` job whose lease has lapsed has lost its owner — but that alone
    does not tell a reader what to do, and the two cases call for opposite
    things:

    * **attempts remain** — another worker will reclaim it and finish it, and the
      reader needs to do nothing except keep waiting. It reads as ``pending``,
      because that is exactly what it is: queued, awaiting a worker. Calling it
      ``interrupted`` here would tell someone their run had stopped and that
      re-running was up to them, moments before it resumed on its own — and the
      UI treats ``interrupted`` as terminal, so it would also stop polling a job
      that was about to produce a report.

    * **attempts exhausted** — nothing will pick it up (``is_claimable`` refuses
      it and the next claim scan retires it to ``dead_letter``). Nobody is
      coming, so ``interrupted`` is the true word and the reader does have to
      act.

    V2 could not make this distinction because V2 had nothing that reclaimed;
    every lapsed job was the second case. The rule is unchanged for that case and
    the derivation is still exactly that — derived, never stored, because a
    stored status needs a writer that is running.
    """
    if job.status == STATUS_RUNNING and is_lease_expired(job.lease_expires_at, now):
        return STATUS_INTERRUPTED if attempts_exhausted(job) else STATUS_PENDING
    return job.status


def describe(job: JobView, now: datetime) -> dict[str, Any]:
    """The reader-facing envelope, with interruption made explicit.

    Does not mutate the job: the dead worker's last write stays exactly as it
    left it, so the record of what it was doing survives, and a job still running
    under another live owner is never stolen from it.
    """
    status = derive_status(job, now)
    out: dict[str, Any] = {
        "id": job.id,
        "job_type": job.job_type,
        "status": status,
        "stage": job.stage,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
    }
    if status == STATUS_INTERRUPTED:
        out["recoverable"] = True
        out["interrupted_reason"] = (
            "The worker that owned this job stopped reporting progress, so its "
            "lease lapsed — most likely a restart. Nothing already saved was "
            "lost, and re-running is safe."
        )
    if status == STATUS_DEAD_LETTER:
        out["recoverable"] = True
        out["dead_letter_reason"] = job.dead_letter_reason
    if job.error_message:
        out["error"] = job.error_message
    if job.cancel_requested and status in IN_FLIGHT:
        out["cancel_requested"] = True
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _aware(dt: datetime) -> datetime:
    """Treat a naive datetime as UTC rather than raising or comparing wrongly."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return _aware(dt).isoformat() if dt is not None else None
