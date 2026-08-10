"""
Phase 32A Slice 6D — Deep Field Review API endpoints.

  POST /api/v1/discovery-runs/{run_id}/field-review
  GET  /api/v1/discovery-runs/{run_id}/field-review

Two layers:
  * router-level tests with a mocked service (404 / 409 / 422 / idempotent start
    / force re-run / lifecycle statuses);
  * one end-to-end test against a real in-memory SQLite DB proving CROSS-RUN
    ISOLATION through the HTTP layer — a field review for run A never returns
    run B's data, even when both runs contain the same company.

No network, no credentials; the deterministic FAKE client only.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import document_ingestion_attempt as _dia  # noqa: F401
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import field_review as _field_review  # noqa: F401
from app.models import report as _report_model  # noqa: F401
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.models.field_review import FieldReviewRun
from app.models.report import Report
from app.services import field_review_service as svc
from app.services.field_review_service import (
    FieldReviewDisabledError,
    InsufficientAnalyzedCandidatesError,
)
from app.services.llm.fake_field_review_client import FakeFieldReviewLLMClient

FIELD_REVIEW_PATH = "/api/v1/discovery-runs/{run_id}/field-review"

FORBIDDEN_SUBSTRINGS = (
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "price target",
    "target price",
    "fair value",
    "intrinsic value",
    "upside of",
    "downside of",
    "undervalued",
    "overvalued",
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


# ---------------------------------------------------------------------------
# Router-level factories
# ---------------------------------------------------------------------------


def _orm_run() -> DiscoveryRun:
    return DiscoveryRun(
        id=uuid.uuid4(), status="completed", mode="ticker", candidate_count=3
    )


def _review_row(
    run_id: uuid.UUID,
    *,
    status: str = "completed",
    review: dict[str, Any] | None = None,
    error: str | None = None,
) -> FieldReviewRun:
    now = datetime.now(timezone.utc)
    return FieldReviewRun(
        id=uuid.uuid4(),
        discovery_run_id=run_id,
        status=status,
        included_candidate_count=2,
        missing_candidate_count=1,
        llm_used=status in {"completed", "completed_with_warnings"},
        council_version="v1",
        provider="fake",
        model="fake-field-review-model",
        agents_completed=8 if status == "completed" else 0,
        agents_failed=0,
        field_quality="adequate" if status == "completed" else None,
        safety_valid=True,
        review_json=review,
        warnings_json=[],
        error=error,
        human_review_required=True,
        started_at=now,
        completed_at=now if status not in {"pending", "running"} else None,
    )


def _stored_review() -> dict[str, Any]:
    return {
        "type": "deep_field_review",
        "llm_used": True,
        "council_version": "v1",
        "pack_version": "v1",
        "item_count": 3,
        "company_count": 2,
        "agents_completed": 8,
        "agents_failed": 0,
        "agents_skipped": 0,
        "field_quality": "adequate",
        "strongest_candidates": [
            {
                "company_ref": "F1",
                "ticker": "AAA",
                "exchange": "US",
                "rationale": "Best evidenced of the compared analyses.",
                "citation_ids": ["F1"],
                "confidence": "low",
                "caveats": [],
            }
        ],
        "second_tier": [],
        "blocked_insufficient_evidence": [],
        "field_uncertainties": ["Evidence depth differs across the field."],
        "evidence_gaps": [],
        "next_research_tasks": [],
        "agent_outputs": {},
        "warnings": [],
        "safety_valid": True,
        "human_review_required": True,
        "publication_ready": False,
        "created_at": "2026-08-10T00:00:00+00:00",
    }


# ===========================================================================
# Router-level: not found
# ===========================================================================


@pytest.mark.asyncio
async def test_post_unknown_run_returns_404(client, mock_db) -> None:
    run_id = uuid.uuid4()
    with patch.object(
        svc, "start_field_review", AsyncMock()
    ) as start, patch(
        "app.api.v1.field_review.discovery_svc.get_run", AsyncMock(return_value=None)
    ):
        resp = await client.post(FIELD_REVIEW_PATH.format(run_id=run_id))
    assert resp.status_code == 404
    start.assert_not_called()


@pytest.mark.asyncio
async def test_get_unknown_run_returns_404(client, mock_db) -> None:
    run_id = uuid.uuid4()
    with patch(
        "app.api.v1.field_review.discovery_svc.get_run", AsyncMock(return_value=None)
    ):
        resp = await client.get(FIELD_REVIEW_PATH.format(run_id=run_id))
    assert resp.status_code == 404


# ===========================================================================
# Router-level: disabled (409) and insufficient candidates (422)
# ===========================================================================


@pytest.mark.asyncio
async def test_post_when_disabled_and_no_prior_review_returns_409(
    client, mock_db
) -> None:
    run = _orm_run()
    task = AsyncMock()
    with patch(
        "app.api.v1.field_review.discovery_svc.get_run", AsyncMock(return_value=run)
    ), patch.object(
        svc,
        "start_field_review",
        AsyncMock(side_effect=FieldReviewDisabledError("Deep Field Review is disabled.")),
    ), patch.object(svc, "process_field_review_task", task):
        resp = await client.post(FIELD_REVIEW_PATH.format(run_id=run.id))
    assert resp.status_code == 409
    assert "disabled" in resp.json()["detail"].lower()
    task.assert_not_called()


@pytest.mark.asyncio
async def test_post_with_too_few_analyses_returns_422_with_the_breakdown(
    client, mock_db
) -> None:
    run = _orm_run()
    missing = [
        {
            "discovery_candidate_id": str(uuid.uuid4()),
            "report_id": None,
            "ticker": "BBB",
            "exchange": "US",
            "exclusion_reason": "no_analysis_run",
        },
        {
            "discovery_candidate_id": str(uuid.uuid4()),
            "report_id": str(uuid.uuid4()),
            "ticker": "CCC",
            "exchange": "US",
            "exclusion_reason": "draft_only",
        },
    ]
    exc = InsufficientAnalyzedCandidatesError(
        "Insufficient analyzed candidates for a Deep Field Review "
        "(need >=2, found 1).",
        included=1,
        required=2,
        missing=missing,
    )
    task = AsyncMock()
    with patch(
        "app.api.v1.field_review.discovery_svc.get_run", AsyncMock(return_value=run)
    ), patch.object(
        svc, "start_field_review", AsyncMock(side_effect=exc)
    ), patch.object(svc, "process_field_review_task", task):
        resp = await client.post(FIELD_REVIEW_PATH.format(run_id=run.id))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "need >=2, found 1" in detail["message"]
    assert detail["included_candidate_count"] == 1
    assert detail["required_candidate_count"] == 2
    # The 422 still lists WHICH candidates exist and why each is not comparable.
    assert {m["ticker"] for m in detail["missing_candidates"]} == {"BBB", "CCC"}
    assert {m["exclusion_reason"] for m in detail["missing_candidates"]} == {
        "no_analysis_run",
        "draft_only",
    }
    task.assert_not_called()


# ===========================================================================
# Router-level: job lifecycle
# ===========================================================================


@pytest.mark.asyncio
async def test_post_schedules_exactly_one_background_task(client, mock_db) -> None:
    run = _orm_run()
    row = _review_row(run.id, status="pending")
    task = AsyncMock()
    with patch(
        "app.api.v1.field_review.discovery_svc.get_run", AsyncMock(return_value=run)
    ), patch.object(
        svc, "start_field_review", AsyncMock(return_value=(row, True))
    ), patch.object(
        svc, "get_candidate_summaries", AsyncMock(return_value=[])
    ), patch.object(svc, "process_field_review_task", task):
        resp = await client.post(FIELD_REVIEW_PATH.format(run_id=run.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["message"] == "Deep Field Review started."
    assert body["review_available"] is False
    task.assert_awaited_once_with(str(row.id))


@pytest.mark.asyncio
async def test_repeat_post_while_running_does_not_start_a_second_job(
    client, mock_db
) -> None:
    run = _orm_run()
    row = _review_row(run.id, status="running")
    task = AsyncMock()
    with patch(
        "app.api.v1.field_review.discovery_svc.get_run", AsyncMock(return_value=run)
    ), patch.object(
        svc, "start_field_review", AsyncMock(return_value=(row, False))
    ), patch.object(
        svc, "get_candidate_summaries", AsyncMock(return_value=[])
    ), patch.object(svc, "process_field_review_task", task):
        resp = await client.post(FIELD_REVIEW_PATH.format(run_id=run.id))
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    assert resp.json()["message"] == "Deep Field Review already in progress."
    task.assert_not_called()


@pytest.mark.asyncio
async def test_post_returns_the_existing_completed_review_without_force(
    client, mock_db
) -> None:
    run = _orm_run()
    row = _review_row(run.id, review=_stored_review())
    task = AsyncMock()
    with patch(
        "app.api.v1.field_review.discovery_svc.get_run", AsyncMock(return_value=run)
    ), patch.object(
        svc, "start_field_review", AsyncMock(return_value=(row, False))
    ) as start, patch.object(
        svc, "get_candidate_summaries", AsyncMock(return_value=[])
    ), patch.object(svc, "process_field_review_task", task):
        resp = await client.post(FIELD_REVIEW_PATH.format(run_id=run.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["review_available"] is True
    assert body["message"] == "Returning the existing Deep Field Review."
    assert start.await_args.kwargs["force"] is False
    task.assert_not_called()


@pytest.mark.asyncio
async def test_post_with_force_reruns_and_schedules(client, mock_db) -> None:
    run = _orm_run()
    row = _review_row(run.id, status="pending")
    task = AsyncMock()
    with patch(
        "app.api.v1.field_review.discovery_svc.get_run", AsyncMock(return_value=run)
    ), patch.object(
        svc, "start_field_review", AsyncMock(return_value=(row, True))
    ) as start, patch.object(
        svc, "get_candidate_summaries", AsyncMock(return_value=[])
    ), patch.object(svc, "process_field_review_task", task):
        resp = await client.post(
            FIELD_REVIEW_PATH.format(run_id=run.id) + "?force=true"
        )
    assert resp.status_code == 200
    assert start.await_args.kwargs["force"] is True
    task.assert_awaited_once_with(str(row.id))


# ===========================================================================
# Router-level: GET
# ===========================================================================


@pytest.mark.asyncio
async def test_get_absent_review_while_enabled_returns_404(client, mock_db) -> None:
    run = _orm_run()
    with patch(
        "app.api.v1.field_review.discovery_svc.get_run", AsyncMock(return_value=run)
    ), patch.object(
        svc, "get_latest_field_review", AsyncMock(return_value=None)
    ), patch.object(svc, "field_review_enabled", lambda *a, **k: True):
        resp = await client.get(FIELD_REVIEW_PATH.format(run_id=run.id))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_absent_review_while_disabled_returns_a_disabled_state(
    client, mock_db
) -> None:
    run = _orm_run()
    with patch(
        "app.api.v1.field_review.discovery_svc.get_run", AsyncMock(return_value=run)
    ), patch.object(
        svc, "get_latest_field_review", AsyncMock(return_value=None)
    ), patch.object(svc, "field_review_enabled", lambda *a, **k: False):
        resp = await client.get(FIELD_REVIEW_PATH.format(run_id=run.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "disabled"
    assert body["review_available"] is False
    assert body["llm_used"] is False


@pytest.mark.asyncio
async def test_get_completed_review_spreads_the_priority_buckets(
    client, mock_db
) -> None:
    run = _orm_run()
    row = _review_row(run.id, review=_stored_review())
    with patch(
        "app.api.v1.field_review.discovery_svc.get_run", AsyncMock(return_value=run)
    ), patch.object(
        svc, "get_latest_field_review", AsyncMock(return_value=row)
    ), patch.object(svc, "get_candidate_summaries", AsyncMock(return_value=[])):
        resp = await client.get(FIELD_REVIEW_PATH.format(run_id=run.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["review_available"] is True
    assert body["field_quality"] == "adequate"
    assert body["strongest_candidates"][0]["ticker"] == "AAA"
    assert body["human_review_required"] is True
    assert body["publication_ready"] is False
    # No rating/valuation vocabulary anywhere in the response.
    blob = json.dumps(body)
    for term in FORBIDDEN_SUBSTRINGS:
        assert term not in blob, term


@pytest.mark.asyncio
async def test_get_insufficient_candidates_is_an_explicit_terminal_state(
    client, mock_db
) -> None:
    run = _orm_run()
    row = _review_row(
        run.id,
        status="insufficient_candidates",
        error="insufficient_analyzed_candidates",
    )
    with patch(
        "app.api.v1.field_review.discovery_svc.get_run", AsyncMock(return_value=run)
    ), patch.object(
        svc, "get_latest_field_review", AsyncMock(return_value=row)
    ), patch.object(svc, "get_candidate_summaries", AsyncMock(return_value=[])):
        resp = await client.get(FIELD_REVIEW_PATH.format(run_id=run.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "insufficient_candidates"
    assert body["review_available"] is False
    assert body["error"] == "insufficient_analyzed_candidates"


@pytest.mark.asyncio
async def test_get_failed_review_surfaces_a_safe_reason_code(client, mock_db) -> None:
    run = _orm_run()
    row = _review_row(run.id, status="failed", error="provider_unavailable")
    with patch(
        "app.api.v1.field_review.discovery_svc.get_run", AsyncMock(return_value=run)
    ), patch.object(
        svc, "get_latest_field_review", AsyncMock(return_value=row)
    ), patch.object(svc, "get_candidate_summaries", AsyncMock(return_value=[])):
        resp = await client.get(FIELD_REVIEW_PATH.format(run_id=run.id))
    assert resp.status_code == 200
    assert resp.json()["error"] == "provider_unavailable"


# ===========================================================================
# End-to-end against a real DB: cross-run isolation through HTTP
# ===========================================================================


def _cfg(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "llm_council_enabled": True,
        "llm_field_review_council_enabled": True,
        "llm_provider_council": "fake",
        "llm_field_review_council_retry_enabled": False,
        "field_review_min_candidates": 2,
    }
    base.update(over)
    return Settings(**base)


def _sections() -> dict[str, Any]:
    return {
        "company_identity": {"type": "company_identity", "ticker": {"value": "X"}},
        "financial_snapshot": {
            "type": "financial_snapshot",
            "latest_close": {"value": 10.0, "provenance": "sourced_fact"},
        },
    }


def _final_report() -> Report:
    return Report(
        id=uuid.uuid4(),
        title="Report",
        slug=f"r-{uuid.uuid4().hex[:10]}",
        report_type="company_deep_dive",
        status="draft",
        final_report_version="1.0.0",
        content_markdown="```json\n" + json.dumps(_sections()) + "\n```",
        schema_validation_json={"schema_valid": True},
        source_summary_json={"data_provenance": "real", "llm_council": {}},
    )


@pytest.mark.asyncio
async def test_end_to_end_field_review_never_leaks_another_runs_data() -> None:
    """Runs A and B each contain the SAME ticker with DIFFERENT reports. The API
    result for run A must reference only run A's candidates and reports."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    run_a = DiscoveryRun(
        id=uuid.uuid4(), status="completed", mode="ticker", candidate_count=2
    )
    run_b = DiscoveryRun(
        id=uuid.uuid4(), status="completed", mode="ticker", candidate_count=2
    )
    a_same, a_other = _final_report(), _final_report()
    b_same, b_other = _final_report(), _final_report()
    async with factory() as seed:
        seed.add_all([run_a, run_b, a_same, a_other, b_same, b_other])
        seed.add_all(
            [
                DiscoveryCandidate(
                    id=uuid.uuid4(),
                    discovery_run_id=run_a.id,
                    ticker="SAME",
                    exchange="US",
                    rank=1,
                    analysis_report_id=a_same.id,
                ),
                DiscoveryCandidate(
                    id=uuid.uuid4(),
                    discovery_run_id=run_a.id,
                    ticker="ONLYA",
                    exchange="US",
                    rank=2,
                    analysis_report_id=a_other.id,
                ),
                DiscoveryCandidate(
                    id=uuid.uuid4(),
                    discovery_run_id=run_b.id,
                    ticker="SAME",
                    exchange="US",
                    rank=1,
                    analysis_report_id=b_same.id,
                ),
                DiscoveryCandidate(
                    id=uuid.uuid4(),
                    discovery_run_id=run_b.id,
                    ticker="ONLYB",
                    exchange="US",
                    rank=2,
                    analysis_report_id=b_other.id,
                ),
            ]
        )
        await seed.commit()

    async def _override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            with patch.object(svc, "process_field_review_task", AsyncMock()), patch(
                "app.services.field_review_service.settings", _cfg()
            ):
                started = await http.post(
                    FIELD_REVIEW_PATH.format(run_id=run_a.id)
                )
            assert started.status_code == 200
            review_run_id = uuid.UUID(started.json()["field_review_run_id"])

            # Drive the background worker explicitly (deterministic fake client).
            await svc.process_field_review_by_id(
                review_run_id,
                session_factory=factory,
                cfg=_cfg(),
                client=FakeFieldReviewLLMClient(),
            )

            resp = await http.get(FIELD_REVIEW_PATH.format(run_id=run_a.id))

        # Run B has no review of its own — one run's job never satisfies
        # another's. Checked before the engine is disposed.
        async with factory() as check:
            assert await svc.get_latest_field_review(check, run_b.id) is None
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["included_candidate_count"] == 2

    tickers = {c["ticker"] for c in body["candidates"]}
    assert tickers == {"SAME", "ONLYA"}
    assert "ONLYB" not in tickers

    report_ids = {c["report_id"] for c in body["candidates"]}
    assert report_ids == {str(a_same.id), str(a_other.id)}
    assert str(b_same.id) not in report_ids
    assert str(b_other.id) not in report_ids

    blob = json.dumps(body)
    for term in FORBIDDEN_SUBSTRINGS:
        assert term not in blob, term
