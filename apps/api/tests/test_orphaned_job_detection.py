"""A job whose owning process is gone is dead NOW, not in 43 more minutes.

Job execution is process-local: the work runs inside the API process that
accepted it, so a worker restart stops it. The elapsed-time rule alone could
only notice that after the derived worst case — **45 minutes** with current
budgets. So a run killed two minutes in kept reporting ``running`` for the rest
of the hour, which is how a dead job came to look like a slow one.

With exactly one gunicorn worker there is a certainty available immediately: if
the job started before this process booted, the process that was running it no
longer exists.

Both abandonment rules live in ONE predicate (``is_stale``) on purpose — it
drives what the reader is shown *and* whether a resubmit may start a fresh run.
Split them and the UI would say "re-running is safe" while the submit endpoint
refused to start one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services import research_job


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _envelope(status: str, started: datetime) -> dict:
    return {"status": status, "started_at": _iso(started)}


# --------------------------------------------------------------------------- #
# The rule itself
# --------------------------------------------------------------------------- #


def test_job_started_before_this_process_booted_is_orphaned(monkeypatch):
    boot = datetime.now(timezone.utc)
    monkeypatch.setattr(research_job, "PROCESS_BOOT_AT", boot)
    env = _envelope(research_job.STATUS_RUNNING, boot - timedelta(minutes=2))
    assert research_job.is_orphaned(env) is True
    # …and it reads as abandoned through the shared predicate too.
    assert research_job.is_stale(env) is True


def test_job_started_after_this_process_booted_is_not_orphaned(monkeypatch):
    boot = datetime.now(timezone.utc) - timedelta(minutes=30)
    monkeypatch.setattr(research_job, "PROCESS_BOOT_AT", boot)
    env = _envelope(research_job.STATUS_RUNNING, boot + timedelta(minutes=1))
    assert research_job.is_orphaned(env) is False
    # 29 minutes is still inside the 45-minute worst case.
    assert research_job.is_stale(env) is False


def test_pending_job_from_a_dead_process_is_also_orphaned(monkeypatch):
    """A job committed as ``pending`` whose process died before work began.

    The elapsed-time rule never caught this at all — it only ever considered
    ``running`` — so such a job blocked resubmission indefinitely.
    """
    boot = datetime.now(timezone.utc)
    monkeypatch.setattr(research_job, "PROCESS_BOOT_AT", boot)
    env = _envelope(research_job.STATUS_PENDING, boot - timedelta(seconds=30))
    assert research_job.is_orphaned(env) is True
    assert research_job.is_stale(env) is True


def test_terminal_jobs_are_never_orphaned(monkeypatch):
    """A completed run keeps its result after a restart — it is not resurrected."""
    boot = datetime.now(timezone.utc)
    monkeypatch.setattr(research_job, "PROCESS_BOOT_AT", boot)
    for status in (
        research_job.STATUS_COMPLETED,
        research_job.STATUS_COMPLETED_WITH_WARNINGS,
        research_job.STATUS_FAILED,
    ):
        env = _envelope(status, boot - timedelta(hours=3))
        assert research_job.is_orphaned(env) is False, status
        assert research_job.is_stale(env) is False, status


def test_missing_or_unparseable_started_at_is_never_orphaned(monkeypatch):
    """No timestamp to reason about ⇒ treat as in flight, never as abandoned."""
    monkeypatch.setattr(research_job, "PROCESS_BOOT_AT", datetime.now(timezone.utc))
    for started in (None, "", "not-a-timestamp", 12345):
        env = {"status": research_job.STATUS_RUNNING, "started_at": started}
        assert research_job.is_orphaned(env) is False, started


# --------------------------------------------------------------------------- #
# The multi-worker premise
# --------------------------------------------------------------------------- #


def test_rule_disables_itself_when_more_than_one_worker_is_deployed(monkeypatch):
    """At 2+ workers a peer may still be running the job, so boot time proves nothing.

    gunicorn respawns workers individually, so a fresh worker's boot time says
    nothing about a peer's in-flight jobs. The rule must switch itself off rather
    than declare a live job dead.
    """
    boot = datetime.now(timezone.utc)
    monkeypatch.setattr(research_job, "PROCESS_BOOT_AT", boot)
    monkeypatch.setattr(research_job, "DEPLOYED_GUNICORN_WORKERS", 2)
    env = _envelope(research_job.STATUS_RUNNING, boot - timedelta(minutes=2))
    assert research_job.is_orphaned(env) is False
    # Falls back to elapsed time, which 2 minutes does not exceed.
    assert research_job.is_stale(env) is False


def test_elapsed_time_rule_still_applies_at_multiple_workers(monkeypatch):
    boot = datetime.now(timezone.utc)
    monkeypatch.setattr(research_job, "PROCESS_BOOT_AT", boot)
    monkeypatch.setattr(research_job, "DEPLOYED_GUNICORN_WORKERS", 2)
    threshold = research_job.stale_after_minutes()
    env = _envelope(
        research_job.STATUS_RUNNING,
        datetime.now(timezone.utc) - timedelta(minutes=threshold + 5),
    )
    assert research_job.is_stale(env) is True


# --------------------------------------------------------------------------- #
# What the reader is told
# --------------------------------------------------------------------------- #


def test_describe_names_the_restart_not_a_timeout(monkeypatch):
    """An orphaned job says the process restarted — not that it ran too long.

    Reporting a timeout for a run that was killed at minute two would be a small
    lie about the reader's own run.
    """
    boot = datetime.now(timezone.utc)
    monkeypatch.setattr(research_job, "PROCESS_BOOT_AT", boot)
    env = _envelope(research_job.STATUS_RUNNING, boot - timedelta(minutes=2))

    out = research_job.describe(env)

    assert out["status"] == research_job.STATUS_INTERRUPTED
    assert out["recoverable"] is True
    reason = out["interrupted_reason"]
    assert "restarted" in reason
    assert "re-running is safe" in reason
    # Must NOT claim it exhausted the worst-case duration — it did not.
    assert "worst-case duration" not in reason


def test_describe_still_reports_a_genuine_timeout_as_a_timeout(monkeypatch):
    boot = datetime.now(timezone.utc) - timedelta(days=1)
    monkeypatch.setattr(research_job, "PROCESS_BOOT_AT", boot)
    threshold = research_job.stale_after_minutes()
    env = _envelope(
        research_job.STATUS_RUNNING,
        datetime.now(timezone.utc) - timedelta(minutes=threshold + 5),
    )

    out = research_job.describe(env)

    assert out["status"] == research_job.STATUS_INTERRUPTED
    assert "worst-case duration" in out["interrupted_reason"]


def test_describe_does_not_mutate_the_stored_envelope(monkeypatch):
    """The dead worker's last write stays exactly as it left it (audit trail)."""
    boot = datetime.now(timezone.utc)
    monkeypatch.setattr(research_job, "PROCESS_BOOT_AT", boot)
    env = _envelope(research_job.STATUS_RUNNING, boot - timedelta(minutes=2))
    before = dict(env)

    research_job.describe(env)

    assert env == before
