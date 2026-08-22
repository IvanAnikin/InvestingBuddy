"""
Product readiness — ASYNC "Run Full Analysis" job + per-candidate report lineage.

Real manual-QA defect this covers:

  The admin clicked "Run Full Analysis" for the NVDA discovery candidate and the
  browser returned **HTTP 504** after ~230s (the Azure App Service gateway
  ceiling), even though the backend completed successfully and persisted a good
  final report. A retry then started a SECOND expensive 8-agent council run for
  the same candidate.

These tests prove:
  * the POST returns 202 immediately with a ``pending`` job envelope,
  * the expensive work happens in a background task with its OWN session,
  * a second click while a job is in flight does NOT start a duplicate run,
  * a completed job is returned as-is unless ``force=true``,
  * a crashed/failed worker always persists a terminal envelope (never stuck
    in ``running``),
  * the job envelope resolves to the report generated for THIS candidate —
    never a global-latest / cross-candidate lookup.

All offline — no network, no credentials, no LLM calls.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.market_discovery import ReportLinkSummary
from app.services import market_discovery_service as mds
from tests.test_phase25_market_candidate_discovery import _candidate_obj

# asyncio_mode = "auto" (see pyproject.toml) — async tests need no marker.


def _db() -> AsyncMock:
    db = AsyncMock(spec=AsyncSession)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Job lifecycle
# ---------------------------------------------------------------------------


async def test_start_writes_pending_envelope_and_schedules() -> None:
    cand = _candidate_obj(raw_signal_json={"provider_name": "free_real"})
    cand.analysis_report_id = None
    db = _db()

    envelope, scheduled = await mds.start_candidate_analysis(db, cand)

    assert scheduled is True
    assert envelope["status"] == "pending"
    assert envelope["started_at"]
    assert envelope["provider_name"] == "free_real"
    # Persisted on the candidate under the additive JSONB key (no migration).
    assert cand.raw_signal_json[mds.ANALYSIS_JOB_STORAGE_KEY]["status"] == "pending"
    db.commit.assert_awaited()


async def test_second_click_while_running_does_not_duplicate() -> None:
    """A double-click must never launch a second (expensive) council job."""
    cand = _candidate_obj(
        raw_signal_json={
            "provider_name": "free_real",
            mds.ANALYSIS_JOB_STORAGE_KEY: {
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        }
    )
    cand.analysis_report_id = None
    db = _db()

    envelope, scheduled = await mds.start_candidate_analysis(db, cand)

    assert scheduled is False
    assert envelope["status"] == "running"
    db.commit.assert_not_awaited()


async def test_completed_job_is_returned_without_rerun_unless_forced() -> None:
    report_id = uuid.uuid4()
    stored = {
        "status": "completed",
        "started_at": "2026-08-22T12:00:00+00:00",
        "analysis_report_id": str(report_id),
    }
    cand = _candidate_obj(
        raw_signal_json={"provider_name": "free_real", mds.ANALYSIS_JOB_STORAGE_KEY: stored}
    )
    db = _db()

    envelope, scheduled = await mds.start_candidate_analysis(db, cand)
    assert scheduled is False
    assert envelope["analysis_report_id"] == str(report_id)

    # force=True is the ONLY way to pay for a second council run.
    envelope2, scheduled2 = await mds.start_candidate_analysis(db, cand, force=True)
    assert scheduled2 is True
    assert envelope2["status"] == "pending"


async def test_stale_running_job_is_restartable() -> None:
    """A worker that died mid-run must not block the candidate forever."""
    stale = (
        datetime.now(timezone.utc)
        - timedelta(minutes=mds._ANALYSIS_STALE_RUNNING_MINUTES + 5)
    ).isoformat()
    cand = _candidate_obj(
        raw_signal_json={
            "provider_name": "free_real",
            mds.ANALYSIS_JOB_STORAGE_KEY: {"status": "running", "started_at": stale},
        }
    )
    cand.analysis_report_id = None
    db = _db()

    _, scheduled = await mds.start_candidate_analysis(db, cand)
    assert scheduled is True


async def test_legacy_candidate_without_envelope_reads_as_completed() -> None:
    """A candidate analysed BEFORE the async migration still renders honestly."""
    report_id = uuid.uuid4()
    cand = _candidate_obj(raw_signal_json={"provider_name": "free_real"})
    cand.analysis_report_id = report_id
    cand.updated_at = datetime.now(timezone.utc)

    envelope = mds.get_analysis_job_envelope(cand)
    assert envelope is not None
    assert envelope["status"] == "completed"
    assert envelope["analysis_report_id"] == str(report_id)


async def test_no_job_ever_run_returns_none() -> None:
    cand = _candidate_obj(raw_signal_json={"provider_name": "free_real"})
    cand.analysis_report_id = None
    assert mds.get_analysis_job_envelope(cand) is None


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


class _FakeSessionFactory:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        session = self._session

        class _Ctx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


async def test_worker_persists_completed_envelope_with_this_candidates_report() -> None:
    cand = _candidate_obj(raw_signal_json={"provider_name": "free_real"})
    cand.analysis_report_id = None
    session = _db()
    report_id = uuid.uuid4()
    agent_run_id = uuid.uuid4()
    legacy_id = uuid.uuid4()

    result = {
        "candidate_id": cand.id,
        "ticker": cand.ticker,
        "status": "completed",
        "analysis_report_id": report_id,
        "agent_run_id": agent_run_id,
        "provider_name": "free_real",
        "report": ReportLinkSummary(
            report_id=report_id, report_kind="final", llm_used=True
        ),
        "legacy_draft_report_id": legacy_id,
        "warnings": [],
    }

    with patch.object(mds, "get_candidate", AsyncMock(return_value=cand)), patch.object(
        mds, "run_candidate_analysis", AsyncMock(return_value=result)
    ):
        await mds.process_candidate_analysis_by_id(
            cand.id, session_factory=_FakeSessionFactory(session)
        )

    envelope = cand.raw_signal_json[mds.ANALYSIS_JOB_STORAGE_KEY]
    assert envelope["status"] == "completed"
    assert envelope["analysis_report_id"] == str(report_id)
    assert envelope["legacy_draft_report_id"] == str(legacy_id)
    assert envelope["report"]["report_kind"] == "final"
    assert envelope["completed_at"]


async def test_worker_marks_completed_with_warnings_on_degraded_run() -> None:
    cand = _candidate_obj(raw_signal_json={"provider_name": "free_real"})
    cand.analysis_report_id = None
    session = _db()
    report_id = uuid.uuid4()
    result = {
        "candidate_id": cand.id,
        "ticker": cand.ticker,
        "status": "completed",
        "analysis_report_id": report_id,
        "agent_run_id": uuid.uuid4(),
        "provider_name": "free_real",
        "report": ReportLinkSummary(report_id=report_id, report_kind="legacy"),
        "legacy_draft_report_id": report_id,
        "warnings": ["final_report_generation_failed"],
    }
    with patch.object(mds, "get_candidate", AsyncMock(return_value=cand)), patch.object(
        mds, "run_candidate_analysis", AsyncMock(return_value=result)
    ):
        await mds.process_candidate_analysis_by_id(
            cand.id, session_factory=_FakeSessionFactory(session)
        )
    envelope = cand.raw_signal_json[mds.ANALYSIS_JOB_STORAGE_KEY]
    assert envelope["status"] == "completed_with_warnings"
    assert envelope["warnings"] == ["final_report_generation_failed"]


async def test_worker_crash_persists_failed_envelope_never_stuck_running() -> None:
    cand = _candidate_obj(raw_signal_json={"provider_name": "free_real"})
    cand.analysis_report_id = None
    session = _db()

    with patch.object(mds, "get_candidate", AsyncMock(return_value=cand)), patch.object(
        mds, "run_candidate_analysis", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        await mds.process_candidate_analysis_by_id(
            cand.id, session_factory=_FakeSessionFactory(session)
        )

    envelope = cand.raw_signal_json[mds.ANALYSIS_JOB_STORAGE_KEY]
    assert envelope["status"] == "failed"
    assert envelope["error"] == "internal_error"
    assert envelope["completed_at"]


async def test_worker_never_clobbers_a_completed_result_with_a_failure() -> None:
    report_id = uuid.uuid4()
    cand = _candidate_obj(
        raw_signal_json={
            mds.ANALYSIS_JOB_STORAGE_KEY: {
                "status": "completed",
                "analysis_report_id": str(report_id),
            }
        }
    )
    session = _db()
    await mds._mark_analysis_job_failed(session, cand, reason="internal_error")
    assert (
        cand.raw_signal_json[mds.ANALYSIS_JOB_STORAGE_KEY]["analysis_report_id"]
        == str(report_id)
    )
    assert cand.raw_signal_json[mds.ANALYSIS_JOB_STORAGE_KEY]["status"] == "completed"


# ---------------------------------------------------------------------------
# API contract
# ---------------------------------------------------------------------------


async def test_post_returns_202_and_schedules_background_task(client) -> None:
    cand = _candidate_obj(raw_signal_json={"provider_name": "free_real"})
    envelope = {"status": "pending", "started_at": "2026-08-22T12:00:00+00:00"}
    with patch.object(mds, "get_candidate", AsyncMock(return_value=cand)), patch.object(
        mds, "start_candidate_analysis", AsyncMock(return_value=(envelope, True))
    ), patch.object(mds, "process_candidate_analysis_task", AsyncMock()) as task:
        res = await client.post(
            f"/api/v1/market-discovery/candidates/{cand.id}/run-analysis"
        )
    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "pending"
    assert "background" in body["message"].lower()
    task.assert_awaited_once()


async def test_post_while_in_flight_does_not_schedule_a_second_task(client) -> None:
    cand = _candidate_obj(raw_signal_json={"provider_name": "free_real"})
    envelope = {"status": "running", "started_at": "2026-08-22T12:00:00+00:00"}
    with patch.object(mds, "get_candidate", AsyncMock(return_value=cand)), patch.object(
        mds, "start_candidate_analysis", AsyncMock(return_value=(envelope, False))
    ), patch.object(mds, "process_candidate_analysis_task", AsyncMock()) as task:
        res = await client.post(
            f"/api/v1/market-discovery/candidates/{cand.id}/run-analysis"
        )
    assert res.status_code == 202
    assert res.json()["status"] == "running"
    assert "already in progress" in res.json()["message"]
    task.assert_not_awaited()


async def test_post_unknown_candidate_is_404(client) -> None:
    with patch.object(mds, "get_candidate", AsyncMock(return_value=None)):
        res = await client.post(
            f"/api/v1/market-discovery/candidates/{uuid.uuid4()}/run-analysis"
        )
    assert res.status_code == 404


async def test_get_analysis_job_returns_this_candidates_report(client) -> None:
    """Lineage: the job endpoint resolves the report produced for THIS candidate,
    never a globally-newest report belonging to another candidate."""
    report_id = uuid.uuid4()
    other_report_id = uuid.uuid4()
    cand = _candidate_obj(
        raw_signal_json={
            mds.ANALYSIS_JOB_STORAGE_KEY: {
                "status": "completed",
                "analysis_report_id": str(report_id),
                "provider_name": "free_real",
                "report": {"report_id": str(report_id), "report_kind": "final",
                           "llm_used": True},
            }
        }
    )
    with patch.object(mds, "get_candidate", AsyncMock(return_value=cand)):
        res = await client.get(
            f"/api/v1/market-discovery/candidates/{cand.id}/analysis-job"
        )
    assert res.status_code == 200
    body = res.json()
    assert body["analysis_report_id"] == str(report_id)
    assert body["analysis_report_id"] != str(other_report_id)
    assert body["report"]["report_kind"] == "final"


async def test_get_analysis_job_404_when_never_run(client) -> None:
    cand = _candidate_obj(raw_signal_json={"provider_name": "free_real"})
    cand.analysis_report_id = None
    with patch.object(mds, "get_candidate", AsyncMock(return_value=cand)):
        res = await client.get(
            f"/api/v1/market-discovery/candidates/{cand.id}/analysis-job"
        )
    assert res.status_code == 404


async def test_job_status_is_never_an_investment_action(client) -> None:
    """Job lifecycle vocabulary must stay free of rating language."""
    from app.services import safety_terms

    for status in ("pending", "running", "completed", "completed_with_warnings", "failed"):
        cand = _candidate_obj(
            raw_signal_json={mds.ANALYSIS_JOB_STORAGE_KEY: {"status": status}}
        )
        with patch.object(mds, "get_candidate", AsyncMock(return_value=cand)):
            res = await client.get(
                f"/api/v1/market-discovery/candidates/{cand.id}/analysis-job"
            )
        assert res.status_code == 200
        # The disclaimer deliberately NAMES the prohibited outputs to deny them.
        assert not safety_terms.scan_value(
            res.json(), exempt_keys=frozenset({"disclaimer"})
        )
