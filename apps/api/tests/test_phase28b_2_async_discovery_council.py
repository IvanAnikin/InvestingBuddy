"""
Phase 28B.2 — async discovery-council execution.

The run-level LLM discovery council review is now started asynchronously: the
POST endpoint returns a ``pending`` job immediately and a FastAPI BackgroundTask
runs the (sequential, per-agent-isolated) council in a fresh DB session, storing
a status envelope under ``discovery_runs.config_json["discovery_council"]`` — no
schema migration. GET returns the current job status / completed review and stays
readable after the council flags are turned off.

All tests use the deterministic FAKE discovery client only — no network, no
credentials, no migration.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.schemas.market_discovery import DiscoveryCouncilReviewResponse
from app.services import market_discovery_service as mds
from app.services.llm.discovery_schemas import DISCOVERY_COUNCIL_AGENT_ORDER
from app.services.llm.fake_discovery_client import FakeDiscoveryLLMClient

COUNCIL = mds.COUNCIL_STORAGE_KEY

FORBIDDEN_SUBSTRINGS = (
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "price target",
    "fair value",
    "intrinsic value",
    "upside of",
    "downside of",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(enabled: bool = True, max_candidates: int = 25) -> Settings:
    return Settings(
        llm_council_enabled=enabled,
        llm_discovery_council_enabled=enabled,
        llm_provider_council="fake",
        llm_discovery_council_max_candidates=max_candidates,
    )


def _orm_run(**over: Any) -> DiscoveryRun:
    run = DiscoveryRun(
        id=over.pop("id", uuid.uuid4()),
        status=over.pop("status", "completed"),
        provider_name="free_real",
        mode=over.pop("mode", "thesis"),
        universe_source="thesis_generated",
        universe_count=5,
        candidate_count=over.pop("candidate_count", 2),
        error_count=0,
        lookback_days=90,
        warnings=["sparse data"],
        config_json=over.pop("config_json", {"region": "Europe", "exchange": "US"}),
        thesis_text="European luxury watch producers",
        parsed_thesis_json={"theme": "luxury_goods", "region": "Europe"},
        human_review_required=True,
    )
    for k, v in over.items():
        setattr(run, k, v)
    return run


def _orm_candidate(run_id: uuid.UUID, ticker: str, **over: Any) -> DiscoveryCandidate:
    c = DiscoveryCandidate(
        id=uuid.uuid4(),
        discovery_run_id=run_id,
        ticker=ticker,
        exchange=over.pop("exchange", "SW"),
        company_name=over.pop("company_name", f"{ticker} AG"),
        country=over.pop("country", "Switzerland"),
        sector="Consumer Discretionary",
        candidate_score=30.0,
        combined_internal_score=28.0,
        thesis_relevance_score=80.0,
        source_quality="limited",
        missing_info_count=3,
        raw_signal_json={
            "data_coverage": {"sec_eligible": False, "fundamentals_source": "not_sourced"}
        },
        safety_valid=True,
        human_review_required=True,
        is_public=False,
        warnings_json=[],
    )
    for k, v in over.items():
        setattr(c, k, v)
    return c


class _FakeSession:
    """A minimal async-session stand-in: only ``commit``/``refresh`` are used
    (queries are patched at the service level)."""

    def __init__(self) -> None:
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _factory():
    return lambda: _FakeSession()


def _envelope(status: str, **over: Any) -> dict[str, Any]:
    env = mds._new_council_envelope(status=status)
    env.update(over)
    return env


# ===========================================================================
# start_discovery_council_review — job lifecycle (no duplicate jobs)
# ===========================================================================


@pytest.mark.asyncio
async def test_start_returns_pending_and_schedules() -> None:
    run = _orm_run()
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    envelope, scheduled = await mds.start_discovery_council_review(
        db, run, cfg=_cfg()
    )
    assert scheduled is True
    assert envelope["status"] == "pending"
    assert envelope["review"] is None
    assert run.config_json[COUNCIL]["status"] == "pending"
    assert run.config_json["region"] == "Europe"  # existing config preserved
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_start_duplicate_while_running_no_second_job(caplog) -> None:
    run = _orm_run(config_json={"region": "Europe", COUNCIL: _envelope("running")})
    db = AsyncMock()
    with caplog.at_level(
        logging.INFO, logger="app.services.market_discovery_service"
    ):
        envelope, scheduled = await mds.start_discovery_council_review(
            db, run, cfg=_cfg()
        )
    assert scheduled is False
    assert envelope["status"] == "running"
    assert "discovery_council_job_duplicate" in caplog.text
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_completed_returns_existing_without_force() -> None:
    completed = _envelope("completed", review={"llm_used": True, "run_quality": "thin"})
    run = _orm_run(config_json={"region": "Europe", COUNCIL: completed})
    db = AsyncMock()
    envelope, scheduled = await mds.start_discovery_council_review(
        db, run, cfg=_cfg()
    )
    assert scheduled is False
    assert envelope["status"] == "completed"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_completed_force_reschedules() -> None:
    completed = _envelope("completed", review={"llm_used": True})
    run = _orm_run(config_json={"region": "Europe", COUNCIL: completed})
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    envelope, scheduled = await mds.start_discovery_council_review(
        db, run, force=True, cfg=_cfg()
    )
    assert scheduled is True
    assert envelope["status"] == "pending"


@pytest.mark.asyncio
async def test_start_disabled_no_review_raises() -> None:
    run = _orm_run(config_json={"region": "Europe"})
    db = AsyncMock()
    with pytest.raises(mds.DiscoveryCouncilDisabledError):
        await mds.start_discovery_council_review(db, run, cfg=_cfg(enabled=False))


@pytest.mark.asyncio
async def test_start_disabled_with_completed_returns_existing() -> None:
    """A stored completed review remains returnable even when flags are off."""
    completed = _envelope("completed", review={"llm_used": True, "run_quality": "thin"})
    run = _orm_run(config_json={"region": "Europe", COUNCIL: completed})
    db = AsyncMock()
    envelope, scheduled = await mds.start_discovery_council_review(
        db, run, cfg=_cfg(enabled=False)
    )
    assert scheduled is False
    assert envelope["status"] == "completed"
    assert envelope["review"]["run_quality"] == "thin"


# ===========================================================================
# Background worker — process_discovery_council_by_id
# ===========================================================================


@pytest.mark.asyncio
async def test_background_stores_completed_review() -> None:
    run = _orm_run(config_json={"region": "Europe", COUNCIL: _envelope("pending")})
    cands = [_orm_candidate(run.id, "UHR"), _orm_candidate(run.id, "CFR")]
    with patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
        mds, "list_candidates", AsyncMock(return_value=(cands, len(cands)))
    ):
        await mds.process_discovery_council_by_id(
            run.id,
            session_factory=_factory(),
            cfg=_cfg(),
            client=FakeDiscoveryLLMClient(),
        )
    env = run.config_json[COUNCIL]
    assert env["status"] == "completed"
    assert env["llm_used"] is True
    assert env["agents_completed"] == len(DISCOVERY_COUNCIL_AGENT_ORDER)
    assert env["agents_failed"] == 0
    assert env["safety_valid"] is True
    assert env["review"]["llm_used"] is True
    assert env["completed_at"] is not None


@pytest.mark.asyncio
async def test_background_partial_failures_completed_with_warnings() -> None:
    run = _orm_run(config_json={"region": "Europe", COUNCIL: _envelope("pending")})
    cands = [_orm_candidate(run.id, "UHR")]
    with patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
        mds, "list_candidates", AsyncMock(return_value=(cands, 1))
    ):
        await mds.process_discovery_council_by_id(
            run.id,
            session_factory=_factory(),
            cfg=_cfg(),
            client=FakeDiscoveryLLMClient(forbidden_agents={"run_red_team"}),
        )
    env = run.config_json[COUNCIL]
    assert env["status"] == "completed_with_warnings"
    assert env["safety_valid"] is False
    assert env["review"] is not None  # partial review still stored + readable


@pytest.mark.asyncio
async def test_background_total_failure_marks_failed() -> None:
    run = _orm_run(config_json={"region": "Europe", COUNCIL: _envelope("pending")})
    cands = [_orm_candidate(run.id, "UHR")]
    with patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
        mds, "list_candidates", AsyncMock(return_value=(cands, 1))
    ):
        await mds.process_discovery_council_by_id(
            run.id,
            session_factory=_factory(),
            cfg=_cfg(),
            client=FakeDiscoveryLLMClient(mode="timeout"),
        )
    env = run.config_json[COUNCIL]
    assert env["status"] == "failed"
    assert env["error"] == "no_agents_completed"
    assert env["agents_completed"] == 0
    # The thin review is still stored so the failure counts are inspectable.
    assert env["review"] is not None


@pytest.mark.asyncio
async def test_background_disabled_midflight_marks_failed() -> None:
    run = _orm_run(config_json={"region": "Europe", COUNCIL: _envelope("pending")})
    with patch.object(mds, "get_run", AsyncMock(return_value=run)):
        await mds.process_discovery_council_by_id(
            run.id,
            session_factory=_factory(),
            cfg=_cfg(enabled=False),
        )
    env = run.config_json[COUNCIL]
    assert env["status"] == "failed"
    assert env["error"] == "disabled"


@pytest.mark.asyncio
async def test_background_unexpected_crash_marks_failed() -> None:
    run = _orm_run(config_json={"region": "Europe", COUNCIL: _envelope("pending")})
    with patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
        mds, "list_candidates", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        await mds.process_discovery_council_by_id(
            run.id,
            session_factory=_factory(),
            cfg=_cfg(),
            client=FakeDiscoveryLLMClient(),
        )
    env = run.config_json[COUNCIL]
    assert env["status"] == "failed"
    assert env["error"] == "internal_error"


@pytest.mark.asyncio
async def test_background_never_clobbers_a_completed_review() -> None:
    """A crash after a review exists must not overwrite the good review."""
    good = _envelope(
        "completed",
        review={"llm_used": True, "run_quality": "adequate"},
        completed_at="2026-07-24T00:00:00+00:00",
    )
    run = _orm_run(config_json={"region": "Europe", COUNCIL: good})
    with patch.object(mds, "get_run", AsyncMock(return_value=run)):
        await mds._mark_council_failed_fresh(
            _factory(), run.id, reason="internal_error"
        )
    assert run.config_json[COUNCIL]["status"] == "completed"


# ===========================================================================
# Task entry point never raises
# ===========================================================================


@pytest.mark.asyncio
async def test_task_entrypoint_swallows_errors() -> None:
    # An invalid UUID must not surface — the entry point swallows everything.
    await mds.process_discovery_council_task("not-a-uuid")


# ===========================================================================
# get_council_envelope — legacy normalisation + review readability
# ===========================================================================


def test_get_council_envelope_normalizes_legacy_raw_review() -> None:
    legacy = {
        "type": "llm_discovery_council_review",
        "llm_used": True,
        "run_quality": "thin",
        "safety_valid": True,
        "created_at": "2026-07-23T00:00:00+00:00",
    }
    run = _orm_run(config_json={"region": "Europe", COUNCIL: legacy})
    env = mds.get_council_envelope(run)
    assert env is not None
    assert env["status"] == "completed"
    assert env["review"] == legacy
    assert env["completed_at"] == "2026-07-23T00:00:00+00:00"


def test_get_council_envelope_absent_is_none() -> None:
    run = _orm_run(config_json={"region": "Europe"})
    assert mds.get_council_envelope(run) is None


def test_completed_review_readable_when_flags_off() -> None:
    completed = _envelope(
        "completed",
        llm_used=True,
        agents_completed=8,
        agents_failed=0,
        safety_valid=True,
        review={
            "llm_used": True,
            "run_quality": "adequate",
            "safety_valid": True,
            "human_review_required": True,
            "publication_ready": False,
        },
    )
    run = _orm_run(config_json={"region": "Europe", COUNCIL: completed})
    # Reading does not depend on the flags at all.
    env = mds.get_council_envelope(run)
    resp = DiscoveryCouncilReviewResponse.from_envelope(run.id, env)
    assert resp.status == "completed"
    assert resp.review_available is True
    assert resp.run_quality == "adequate"
    assert resp.human_review_required is True
    assert resp.publication_ready is False


def test_chair_fallback_fields_survive_from_storage() -> None:
    """Phase 32A Slice 6A: chair_fallback_used/deterministic_discovery_chair
    must reach the API response, not be silently dropped as unknown kwargs.
    """
    stored = {
        "llm_used": True,
        "run_quality": "failed",
        "safety_valid": True,
        "chair_fallback_used": True,
        "deterministic_discovery_chair": {
            "agent_name": "discovery_chair",
            "status": "completed",
            "summary": (
                "Deterministic discovery chair summary (LLM discovery chair "
                "unavailable). 3 of 7 non-chair council agents completed."
            ),
            "safety_notes": ["no recommendation, no valuation conclusion"],
        },
    }
    run_id = uuid.uuid4()
    resp = DiscoveryCouncilReviewResponse.from_storage(run_id, stored)
    assert resp.chair_fallback_used is True
    assert resp.deterministic_discovery_chair is not None
    assert resp.deterministic_discovery_chair["agent_name"] == "discovery_chair"
    assert "unavailable" in resp.deterministic_discovery_chair["summary"]


def test_chair_fallback_fields_survive_from_envelope() -> None:
    """Same guarantee via the async job-envelope path used by GET."""
    review = {
        "llm_used": True,
        "run_quality": "failed",
        "safety_valid": True,
        "chair_fallback_used": True,
        "deterministic_discovery_chair": {
            "agent_name": "discovery_chair",
            "status": "completed",
            "summary": "Deterministic discovery chair summary.",
        },
    }
    completed = _envelope(
        "completed",
        llm_used=True,
        agents_completed=3,
        agents_failed=4,
        safety_valid=True,
        review=review,
    )
    run = _orm_run(config_json={"region": "Europe", COUNCIL: completed})
    env = mds.get_council_envelope(run)
    resp = DiscoveryCouncilReviewResponse.from_envelope(run.id, env)
    assert resp.chair_fallback_used is True
    assert resp.deterministic_discovery_chair == review["deterministic_discovery_chair"]


def test_chair_fallback_fields_default_false_when_absent() -> None:
    """The OFF/no-fallback path stays default (byte-identical for consumers)."""
    resp = DiscoveryCouncilReviewResponse.from_storage(
        uuid.uuid4(), {"llm_used": True, "run_quality": "adequate"}
    )
    assert resp.chair_fallback_used is False
    assert resp.deterministic_discovery_chair is None


# ===========================================================================
# Response envelope shaping
# ===========================================================================


def test_from_envelope_pending_has_no_review() -> None:
    resp = DiscoveryCouncilReviewResponse.from_envelope(
        uuid.uuid4(), _envelope("pending", started_at="2026-07-24T00:00:00+00:00")
    )
    assert resp.status == "pending"
    assert resp.review_available is False
    assert resp.llm_used is False
    assert resp.human_review_required is True
    assert resp.publication_ready is False


def test_from_envelope_failed_carries_error() -> None:
    resp = DiscoveryCouncilReviewResponse.from_envelope(
        uuid.uuid4(),
        _envelope("failed", error="no_agents_completed", agents_failed=8),
    )
    assert resp.status == "failed"
    assert resp.review_available is False
    assert resp.error == "no_agents_completed"


def test_disabled_response_shape() -> None:
    resp = DiscoveryCouncilReviewResponse.disabled_response(uuid.uuid4())
    assert resp.status == "disabled"
    assert resp.review_available is False
    assert resp.llm_used is False


# ===========================================================================
# API — async endpoints
# ===========================================================================


@pytest.mark.asyncio
async def test_api_post_duplicate_while_running_does_not_schedule(
    client, mock_db
) -> None:
    run = _orm_run(config_json={"region": "Europe", COUNCIL: _envelope("running")})
    task = AsyncMock()
    with patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
        mds, "process_discovery_council_task", task
    ):
        resp = await client.post(
            f"/api/v1/market-discovery/runs/{run.id}/council-review"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["message"] == "Discovery council review already in progress."
    task.assert_not_called()


@pytest.mark.asyncio
async def test_api_get_running_status(client, mock_db) -> None:
    run = _orm_run(
        config_json={
            "region": "Europe",
            COUNCIL: _envelope("running", started_at="2026-07-24T00:00:00+00:00"),
        }
    )
    with patch.object(mds, "get_run", AsyncMock(return_value=run)):
        resp = await client.get(
            f"/api/v1/market-discovery/runs/{run.id}/council-review"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["review_available"] is False


@pytest.mark.asyncio
async def test_api_get_completed_review(client, mock_db) -> None:
    completed = _envelope(
        "completed",
        llm_used=True,
        agents_completed=8,
        agents_failed=0,
        safety_valid=True,
        review={"llm_used": True, "run_quality": "adequate", "safety_valid": True},
    )
    run = _orm_run(config_json={"region": "Europe", COUNCIL: completed})
    with patch.object(mds, "get_run", AsyncMock(return_value=run)):
        resp = await client.get(
            f"/api/v1/market-discovery/runs/{run.id}/council-review"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["review_available"] is True
    assert body["run_quality"] == "adequate"


@pytest.mark.asyncio
async def test_api_get_failed_status(client, mock_db) -> None:
    run = _orm_run(
        config_json={
            "region": "Europe",
            COUNCIL: _envelope("failed", error="no_agents_completed"),
        }
    )
    with patch.object(mds, "get_run", AsyncMock(return_value=run)):
        resp = await client.get(
            f"/api/v1/market-discovery/runs/{run.id}/council-review"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error"] == "no_agents_completed"


@pytest.mark.asyncio
async def test_api_get_absent_enabled_returns_404(client, mock_db) -> None:
    run = _orm_run(config_json={"region": "Europe"})
    with patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
        mds, "discovery_council_enabled", lambda cfg=None: True
    ):
        resp = await client.get(
            f"/api/v1/market-discovery/runs/{run.id}/council-review"
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_post_force_query_param_supported() -> None:
    from app.main import app

    schema = app.openapi()
    post = schema["paths"][
        "/api/v1/market-discovery/runs/{run_id}/council-review"
    ]["post"]
    params = {p["name"] for p in post.get("parameters", [])}
    assert "force" in params


def test_no_publish_route_added() -> None:
    from app.main import app

    schema = app.openapi()
    md_paths = [p for p in schema["paths"] if "market-discovery" in p]
    assert not any("publish" in p for p in md_paths)


# ===========================================================================
# Logging + safety
# ===========================================================================


@pytest.mark.asyncio
async def test_job_events_logged_without_secrets(caplog) -> None:
    run = _orm_run(config_json={"region": "Europe", COUNCIL: _envelope("pending")})
    cands = [_orm_candidate(run.id, "UHR")]
    with caplog.at_level(
        logging.INFO, logger="app.services.market_discovery_service"
    ), patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
        mds, "list_candidates", AsyncMock(return_value=(cands, 1))
    ):
        await mds.process_discovery_council_by_id(
            run.id,
            session_factory=_factory(),
            cfg=_cfg(),
            client=FakeDiscoveryLLMClient(),
        )
    text = caplog.text
    assert "discovery_council_job_started" in text
    assert "discovery_council_job_completed" in text
    lower = text.lower()
    for banned in ("api_key", "authorization", "basic ", "you are the", "hard rules"):
        assert banned not in lower, f"log leaked {banned!r}"


@pytest.mark.asyncio
async def test_stored_envelope_has_no_forbidden_language() -> None:
    import json

    run = _orm_run(config_json={"region": "Europe", COUNCIL: _envelope("pending")})
    cands = [_orm_candidate(run.id, "UHR"), _orm_candidate(run.id, "CFR")]
    with patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
        mds, "list_candidates", AsyncMock(return_value=(cands, len(cands)))
    ):
        await mds.process_discovery_council_by_id(
            run.id,
            session_factory=_factory(),
            cfg=_cfg(),
            client=FakeDiscoveryLLMClient(),
        )
    blob = json.dumps(run.config_json[COUNCIL])
    for term in FORBIDDEN_SUBSTRINGS:
        assert term not in blob, f"forbidden term {term!r} leaked into stored envelope"
