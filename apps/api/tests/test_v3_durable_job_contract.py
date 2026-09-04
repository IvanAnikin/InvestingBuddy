"""V3.0 Slice 1 — the durable job contract.

These tests are deliberately pure: no database, no event loop, no network, no
wall clock. Every "now" is passed in. That is what makes it possible to assert
the rules exhaustively, and it is why the contract lives in its own module
instead of inside a service that also does I/O.

The clock discipline matters for a second reason. An earlier defect in this
repository made a test's outcome depend on how long the suite had been running;
nothing here can do that, because no function in ``job_contract`` reads the
clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import research_job
from app.services.jobs import job_contract as jc

T0 = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


def _job(**kw) -> jc.JobView:
    base = dict(
        id="job-1",
        job_type="company_research",
        status=research_job.STATUS_PENDING,
    )
    base.update(kw)
    return jc.JobView(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Vocabulary — the contract must not fork V2's status words
# ---------------------------------------------------------------------------


class TestVocabulary:
    def test_v3_stored_statuses_are_a_superset_of_v2s(self):
        """V3 may ADD terminal states. It may never rename or drop a V2 one.

        The web app polls these words and the admin console renders them. A fork
        here would fork the polling contract, so this assertion is the guard
        rail for ADR-046.
        """
        v2_stored = {
            research_job.STATUS_PENDING,
            research_job.STATUS_RUNNING,
            research_job.STATUS_COMPLETED,
            research_job.STATUS_COMPLETED_WITH_WARNINGS,
            research_job.STATUS_FAILED,
        }
        assert v2_stored <= jc.STORED_STATUSES

    def test_v3_adds_exactly_dead_letter_and_cancelled(self):
        assert jc.V3_ONLY_STATUSES == {"dead_letter", "cancelled"}

    def test_interrupted_is_never_a_stored_status(self):
        """It is derived at read time. Storing it would need a live writer."""
        assert research_job.STATUS_INTERRUPTED not in jc.STORED_STATUSES

    def test_v2_terminal_states_stay_terminal_in_v3(self):
        assert research_job.TERMINAL <= jc.TERMINAL

    def test_dead_letter_is_distinct_from_failed(self):
        """They call for different human responses, so they are different words."""
        assert jc.STATUS_DEAD_LETTER != research_job.STATUS_FAILED
        assert jc.is_terminal(jc.STATUS_DEAD_LETTER)
        assert jc.is_terminal(research_job.STATUS_FAILED)


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


class TestTransitions:
    @pytest.mark.parametrize(
        "frm,to",
        [
            ("pending", "running"),
            ("pending", "cancelled"),
            ("running", "completed"),
            ("running", "completed_with_warnings"),
            ("running", "failed"),
            ("running", "dead_letter"),
            ("running", "cancelled"),
            ("running", "pending"),
        ],
    )
    def test_legal(self, frm, to):
        assert jc.can_transition(frm, to)

    @pytest.mark.parametrize(
        "frm,to",
        [
            ("completed", "running"),
            ("failed", "pending"),
            ("dead_letter", "running"),
            ("cancelled", "running"),
            ("pending", "completed"),
            ("pending", "failed"),
        ],
    )
    def test_illegal(self, frm, to):
        assert not jc.can_transition(frm, to)
        with pytest.raises(jc.IllegalTransition):
            jc.assert_transition(frm, to)

    def test_running_to_running_is_legal_because_reclaim_is_real(self):
        """A reclaim by a new owner is running -> running, not a no-op."""
        assert jc.can_transition("running", "running")

    def test_every_terminal_state_is_a_dead_end(self):
        for status in jc.TERMINAL:
            assert not any(
                jc.can_transition(status, other) for other in jc.STORED_STATUSES
            ), f"{status} should be terminal"


# ---------------------------------------------------------------------------
# Leasing and claiming
# ---------------------------------------------------------------------------


class TestClaiming:
    def test_pending_job_is_claimable(self):
        assert jc.is_claimable(_job(), T0)

    def test_claim_takes_ownership_and_starts_the_clock(self):
        claimed = jc.claim(_job(), owner="worker-a", now=T0)
        assert claimed.status == "running"
        assert claimed.lease_owner == "worker-a"
        assert claimed.lease_expires_at == T0 + timedelta(
            seconds=jc.DEFAULT_LEASE_SECONDS
        )
        assert claimed.started_at == T0
        assert claimed.attempt == 1

    def test_a_live_lease_blocks_a_second_worker(self):
        claimed = jc.claim(_job(), owner="worker-a", now=T0)
        during = T0 + timedelta(seconds=30)
        assert not jc.is_claimable(claimed, during)
        with pytest.raises(jc.IllegalTransition):
            jc.claim(claimed, owner="worker-b", now=during)

    def test_an_expired_lease_is_reclaimable_by_another_worker(self):
        """This is the property that makes recovery automatic."""
        claimed = jc.claim(_job(), owner="worker-a", now=T0)
        after = T0 + timedelta(seconds=jc.DEFAULT_LEASE_SECONDS + 1)
        assert jc.is_claimable(claimed, after)
        reclaimed = jc.claim(claimed, owner="worker-b", now=after)
        assert reclaimed.lease_owner == "worker-b"

    def test_reclaim_consumes_an_attempt(self):
        """An attempt that was started and lost is an attempt spent.

        Otherwise a job that reliably kills its worker retries forever — an
        outage that keeps restarting itself.
        """
        claimed = jc.claim(_job(), owner="worker-a", now=T0)
        after = T0 + timedelta(seconds=jc.DEFAULT_LEASE_SECONDS + 1)
        reclaimed = jc.claim(claimed, owner="worker-b", now=after)
        assert reclaimed.attempt == 2

    def test_reclaim_preserves_the_original_start_time(self):
        claimed = jc.claim(_job(), owner="worker-a", now=T0)
        after = T0 + timedelta(seconds=jc.DEFAULT_LEASE_SECONDS + 1)
        assert jc.claim(claimed, owner="worker-b", now=after).started_at == T0

    def test_a_running_job_with_no_lease_is_reclaimable(self):
        """The V2 legacy shape: nothing ever took a lease, so nothing holds it."""
        legacy = _job(status="running", lease_expires_at=None)
        assert jc.is_claimable(legacy, T0)

    def test_backoff_delays_a_claim(self):
        later = _job(available_at=T0 + timedelta(seconds=60))
        assert not jc.is_claimable(later, T0)
        assert jc.is_claimable(later, T0 + timedelta(seconds=61))

    @pytest.mark.parametrize(
        "status", ["completed", "completed_with_warnings", "failed", "dead_letter", "cancelled"]
    )
    def test_terminal_jobs_are_never_claimable(self, status):
        assert not jc.is_claimable(_job(status=status), T0)

    def test_a_cancel_request_does_not_block_a_claim(self):
        """A worker must claim the job in order to observe the request.

        A pending job nobody may claim would sit in ``pending`` forever wearing a
        cancellation nobody acted on.
        """
        assert jc.is_claimable(_job(cancel_requested=True), T0)

    def test_naive_datetimes_are_treated_as_utc(self):
        naive = datetime(2026, 9, 4, 12, 0, 0)
        assert jc.is_claimable(_job(), naive)


class TestHeartbeat:
    def test_heartbeat_extends_the_lease(self):
        claimed = jc.claim(_job(), owner="worker-a", now=T0)
        later = T0 + timedelta(seconds=60)
        beat = jc.heartbeat(claimed, owner="worker-a", now=later)
        assert beat.lease_expires_at == later + timedelta(
            seconds=jc.DEFAULT_LEASE_SECONDS
        )
        assert beat.last_heartbeat_at == later

    def test_only_the_owner_may_heartbeat(self):
        """Refusing a non-owner is what makes reclaim safe.

        The original worker may still be alive and mid-request after its lease
        lapsed; if it could extend a lease it no longer holds, two workers would
        believe they own the same job.
        """
        claimed = jc.claim(_job(), owner="worker-a", now=T0)
        with pytest.raises(jc.IllegalTransition):
            jc.heartbeat(claimed, owner="worker-b", now=T0 + timedelta(seconds=10))

    def test_cannot_heartbeat_a_finished_job(self):
        done = jc.complete(jc.claim(_job(), owner="w", now=T0), now=T0)
        with pytest.raises(jc.IllegalTransition):
            jc.heartbeat(done, owner="w", now=T0)

    def test_heartbeat_can_report_a_stage(self):
        claimed = jc.claim(_job(), owner="w", now=T0)
        beat = jc.heartbeat(claimed, owner="w", now=T0, stage="evidence_validation")
        assert beat.stage == "evidence_validation"

    def test_heartbeat_without_a_stage_keeps_the_previous_one(self):
        claimed = jc.claim(_job(stage="data_collection"), owner="w", now=T0)
        assert jc.heartbeat(claimed, owner="w", now=T0).stage == "data_collection"

    def test_lease_outlasts_the_heartbeat_interval(self):
        """A working worker must always be able to renew before expiry."""
        assert jc.DEFAULT_HEARTBEAT_SECONDS * 2 < jc.DEFAULT_LEASE_SECONDS


# ---------------------------------------------------------------------------
# Completion, failure, retry, dead-letter
# ---------------------------------------------------------------------------


class TestCompletion:
    def test_complete_releases_the_lease(self):
        done = jc.complete(jc.claim(_job(), owner="w", now=T0), now=T0)
        assert done.status == "completed"
        assert done.lease_owner is None
        assert done.lease_expires_at is None
        assert done.finished_at == T0

    def test_complete_with_warnings_uses_v2s_word(self):
        done = jc.complete(
            jc.claim(_job(), owner="w", now=T0), now=T0, with_warnings=True
        )
        assert done.status == research_job.STATUS_COMPLETED_WITH_WARNINGS

    def test_cancel_releases_the_lease(self):
        cancelled = jc.cancel(jc.claim(_job(), owner="w", now=T0), now=T0)
        assert cancelled.status == "cancelled"
        assert cancelled.lease_owner is None


class TestFailure:
    def test_permanent_failure_fails_immediately_with_attempts_left(self):
        """Attempts are not the resource being protected.

        The point is not to spend a research budget re-running something that
        cannot work.
        """
        claimed = jc.claim(_job(max_attempts=5), owner="w", now=T0)
        out = jc.fail(claimed, now=T0, transient=False, error_class="LLMJsonError")
        assert out.job.status == "failed"
        assert out.will_retry is False
        assert out.job.attempt == 1 < out.job.max_attempts

    def test_transient_failure_schedules_a_retry(self):
        claimed = jc.claim(_job(), owner="w", now=T0)
        out = jc.fail(claimed, now=T0, transient=True, error_class="LLMRateLimitError")
        assert out.job.status == "pending"
        assert out.will_retry is True
        assert out.retry_at == T0 + timedelta(seconds=jc.DEFAULT_BACKOFF_BASE_SECONDS)
        assert out.job.available_at == out.retry_at

    def test_a_retry_is_not_claimable_before_its_backoff_elapses(self):
        claimed = jc.claim(_job(), owner="w", now=T0)
        out = jc.fail(claimed, now=T0, transient=True)
        assert not jc.is_claimable(out.job, T0)
        assert jc.is_claimable(out.job, out.retry_at + timedelta(seconds=1))

    def test_exhausted_attempts_dead_letter_rather_than_fail(self):
        job = _job(status="running", attempt=3, max_attempts=3, lease_owner="w")
        out = jc.fail(job, now=T0, transient=True, error_class="LLMServerError")
        assert out.job.status == "dead_letter"
        assert out.will_retry is False
        assert "exhausted 3 attempts" in out.job.dead_letter_reason
        assert "LLMServerError" in out.job.dead_letter_reason

    def test_failure_always_releases_the_lease(self):
        claimed = jc.claim(_job(), owner="w", now=T0)
        for transient in (True, False):
            out = jc.fail(claimed, now=T0, transient=transient)
            assert out.job.lease_owner is None
            assert out.job.lease_expires_at is None

    def test_full_lifecycle_transient_until_dead_letter(self):
        """Three claims, three transient failures, then dead-letter."""
        job = _job(max_attempts=3)
        now = T0
        for expected_attempt in (1, 2, 3):
            job = jc.claim(job, owner="w", now=now)
            assert job.attempt == expected_attempt
            out = jc.fail(job, now=now, transient=True, error_class="LLMTimeoutError")
            job = out.job
            if expected_attempt < 3:
                assert out.will_retry is True
                now = out.retry_at + timedelta(seconds=1)
        assert job.status == "dead_letter"

    def test_backoff_is_exponential_and_capped(self):
        assert jc.backoff_seconds(1, base=5.0) == 5.0
        assert jc.backoff_seconds(2, base=5.0) == 10.0
        assert jc.backoff_seconds(3, base=5.0) == 20.0
        assert jc.backoff_seconds(100, base=5.0, maximum=300.0) == 300.0

    def test_backoff_of_a_zeroth_attempt_is_zero(self):
        assert jc.backoff_seconds(0) == 0.0


# ---------------------------------------------------------------------------
# The reader-facing view
# ---------------------------------------------------------------------------


class TestDerivedStatus:
    def test_a_live_lease_reads_as_running(self):
        claimed = jc.claim(_job(), owner="w", now=T0)
        assert jc.derive_status(claimed, T0 + timedelta(seconds=10)) == "running"

    def test_a_lapsed_lease_reads_as_interrupted(self):
        """The owner stopped saying it was alive. That IS the evidence."""
        claimed = jc.claim(_job(), owner="w", now=T0)
        after = T0 + timedelta(seconds=jc.DEFAULT_LEASE_SECONDS + 1)
        assert jc.derive_status(claimed, after) == research_job.STATUS_INTERRUPTED

    def test_deriving_interrupted_does_not_mutate_the_job(self):
        """The dead worker's last write must survive for the audit trail."""
        claimed = jc.claim(_job(stage="data_collection"), owner="w", now=T0)
        after = T0 + timedelta(seconds=jc.DEFAULT_LEASE_SECONDS + 1)
        jc.describe(claimed, after)
        assert claimed.status == "running"
        assert claimed.lease_owner == "w"
        assert claimed.stage == "data_collection"

    def test_terminal_statuses_are_reported_as_stored(self):
        done = jc.complete(jc.claim(_job(), owner="w", now=T0), now=T0)
        assert jc.derive_status(done, T0 + timedelta(days=365)) == "completed"

    def test_interrupted_envelope_says_re_running_is_safe(self):
        claimed = jc.claim(_job(), owner="w", now=T0)
        after = T0 + timedelta(seconds=jc.DEFAULT_LEASE_SECONDS + 1)
        out = jc.describe(claimed, after)
        assert out["status"] == "interrupted"
        assert out["recoverable"] is True
        assert "re-running is safe" in out["interrupted_reason"]

    def test_dead_letter_envelope_carries_its_reason(self):
        job = _job(status="running", attempt=3, max_attempts=3)
        out = jc.describe(jc.fail(job, now=T0, transient=True).job, T0)
        assert out["status"] == "dead_letter"
        assert out["dead_letter_reason"]

    def test_envelope_never_leaks_a_raw_lease_owner(self):
        """Worker identity is operational, not reader-facing."""
        claimed = jc.claim(_job(), owner="worker-a-secret-host", now=T0)
        assert "worker-a-secret-host" not in str(jc.describe(claimed, T0))

    def test_cancel_request_is_surfaced_only_while_in_flight(self):
        pending = _job(cancel_requested=True)
        assert jc.describe(pending, T0).get("cancel_requested") is True
        cancelled = jc.cancel(jc.claim(pending, owner="w", now=T0), now=T0)
        assert "cancel_requested" not in jc.describe(cancelled, T0)


# ---------------------------------------------------------------------------
# Coherence with the surrounding system
# ---------------------------------------------------------------------------


class TestBudgetCoherence:
    def test_lease_is_far_shorter_than_the_elapsed_time_threshold(self):
        """The lease must be the FASTER of the two abandonment detectors.

        ``research_job.stale_after_minutes`` derives a worst-case duration from
        the council and ingestion budgets. A lease longer than that would never
        fire first, which would make leasing pointless — and it would drift
        silently the next time a budget is tuned, which is exactly how the
        gunicorn worker timeout once ended up below the ingestion budget.
        """
        stale_seconds = research_job.stale_after_minutes() * 60
        assert jc.DEFAULT_LEASE_SECONDS < stale_seconds / 10

    def test_settings_defaults_match_the_contract_defaults(self):
        from app.core.config import settings

        assert settings.v3_job_lease_seconds == jc.DEFAULT_LEASE_SECONDS
        assert settings.v3_job_heartbeat_seconds == jc.DEFAULT_HEARTBEAT_SECONDS
        assert settings.v3_job_max_attempts == jc.DEFAULT_MAX_ATTEMPTS

    def test_the_durable_path_is_off_by_default(self):
        """V2 behaviour must be byte-identical until the flag is flipped."""
        from app.core.config import Settings

        assert Settings().v3_durable_jobs_enabled is False


class TestOrmShape:
    def test_model_columns_cover_the_contract(self):
        from app.models.research_job import ResearchJob

        cols = set(ResearchJob.__table__.columns.keys())
        required = {
            "id", "job_type", "idempotency_key", "status", "attempt",
            "max_attempts", "lease_owner", "lease_expires_at",
            "last_heartbeat_at", "available_at", "cancel_requested",
            "dead_letter_reason", "started_at", "finished_at",
        }
        assert required <= cols

    def test_idempotency_key_is_unique_in_the_database(self):
        """Application-level dedup loses to a concurrent duplicate submit."""
        from app.models.research_job import ResearchJob

        names = {c.name for c in ResearchJob.__table__.constraints}
        assert "uq_research_jobs_idempotency_key" in names

    def test_the_claim_query_is_indexed(self):
        from app.models.research_job import ResearchJob

        idx = {i.name for i in ResearchJob.__table__.indexes}
        assert "ix_research_jobs_status_available_at" in idx
        assert "ix_research_jobs_lease_expires_at" in idx

    def test_lineage_foreign_keys_preserve_research_history(self):
        """SET NULL, never CASCADE — CLAUDE.md rule 15."""
        from app.models.research_job import ResearchJob

        for fk in ResearchJob.__table__.foreign_keys:
            assert fk.ondelete == "SET NULL"

    def test_interrupted_is_not_a_column(self):
        from app.models.research_job import ResearchJob

        assert "interrupted" not in ResearchJob.__table__.columns
