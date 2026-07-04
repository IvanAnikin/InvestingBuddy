"""
Phase 11: Tests for the admin report review / approve-reject workflow.

Coverage:
- POST /api/v1/admin/reports/{id}/mark-under-review
- POST /api/v1/admin/reports/{id}/approve
- POST /api/v1/admin/reports/{id}/reject
- POST /api/v1/admin/reports/{id}/needs-revision
- GET  /api/v1/admin/reports/{id}/review-events
- Status transition validation
- Note-required validation for reject/needs_revision
- Acknowledgement requirement for approve with warnings
- Audit event created for every action
- 404 for missing report
- 422 for invalid UUID
- No publish endpoint exists
- No public-recommendation content in responses
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.models.report import Report
from app.models.review_event import ReportReviewEvent

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_MARK = "app.api.v1.admin_reports.report_service.mark_under_review"
_APPROVE = "app.api.v1.admin_reports.report_service.approve_report"
_REJECT = "app.api.v1.admin_reports.report_service.reject_report"
_REVISION = "app.api.v1.admin_reports.report_service.needs_revision"
_EVENTS = "app.api.v1.admin_reports.report_service.get_review_events"


def _make_report(
    report_id: uuid.UUID,
    review_status: str = "draft",
    human_review_required: bool = True,
) -> MagicMock:
    now = datetime.now(timezone.utc)
    report = MagicMock(spec=Report)
    report.id = report_id
    report.title = "Test Draft"
    report.slug = "test-draft"
    report.report_type = "company_deep_dive"
    report.period_start = None
    report.period_end = None
    report.status = "draft"
    report.summary = "Admin draft only."
    report.content_markdown = "# Admin Draft"
    report.content_html = None
    report.created_by_agent_run_id = None
    report.published_at = None
    report.created_at = now
    report.updated_at = now
    report.review_status = review_status
    report.reviewed_at = now
    report.reviewer_note = None
    report.review_decision_reason = None
    report.human_review_required = human_review_required
    report.approved_by = None
    report.rejected_by = None
    return report


def _make_event(
    report_id: uuid.UUID,
    action: str,
    from_status: str | None,
    to_status: str,
    note: str | None = None,
    actor_label: str | None = None,
) -> MagicMock:
    event = MagicMock(spec=ReportReviewEvent)
    event.id = uuid.uuid4()
    event.report_id = report_id
    event.action = action
    event.from_status = from_status
    event.to_status = to_status
    event.note = note
    event.actor_label = actor_label
    event.created_at = datetime.now(timezone.utc)
    return event


# ===========================================================================
# mark-under-review
# ===========================================================================


async def test_mark_under_review_200(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    report = _make_report(report_id, review_status="under_review")
    event = _make_event(report_id, "mark_under_review", "draft", "under_review")

    with patch(_MARK, new_callable=AsyncMock, return_value=(report, event)):
        response = await client.post(
            f"/api/v1/admin/reports/{report_id}/mark-under-review",
            json={"actor_label": "admin"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "mark_under_review"
    assert data["to_status"] == "under_review"
    assert data["report_id"] == str(report_id)
    assert "INTERNAL ADMIN ONLY" in data["message"]


async def test_mark_under_review_not_found(client: AsyncClient) -> None:
    from fastapi import HTTPException

    with patch(
        _MARK,
        new_callable=AsyncMock,
        side_effect=HTTPException(status_code=404, detail="Report not found"),
    ):
        response = await client.post(
            f"/api/v1/admin/reports/{uuid.uuid4()}/mark-under-review",
            json={},
        )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_mark_under_review_invalid_uuid(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/admin/reports/not-a-uuid/mark-under-review", json={}
    )
    assert response.status_code == 422


async def test_mark_under_review_bad_transition(client: AsyncClient, report_id: uuid.UUID) -> None:
    from fastapi import HTTPException

    with patch(
        _MARK,
        new_callable=AsyncMock,
        side_effect=HTTPException(
            status_code=422,
            detail="Cannot apply action 'mark_under_review' when review_status is 'approved_internal'.",
        ),
    ):
        response = await client.post(
            f"/api/v1/admin/reports/{report_id}/mark-under-review",
            json={},
        )

    assert response.status_code == 422
    assert "mark_under_review" in response.json()["detail"]


# ===========================================================================
# approve
# ===========================================================================


async def test_approve_200(client: AsyncClient, report_id: uuid.UUID) -> None:
    report = _make_report(report_id, review_status="approved_internal")
    event = _make_event(
        report_id, "approve", "under_review", "approved_internal", actor_label="admin"
    )

    with patch(_APPROVE, new_callable=AsyncMock, return_value=(report, event)):
        response = await client.post(
            f"/api/v1/admin/reports/{report_id}/approve",
            json={"actor_label": "admin", "acknowledge_warnings": True},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "approve"
    assert data["to_status"] == "approved_internal"
    assert "PUBLIC PUBLISHING IS NOT IMPLEMENTED" in data["message"]


async def test_approve_not_found(client: AsyncClient) -> None:
    from fastapi import HTTPException

    with patch(
        _APPROVE,
        new_callable=AsyncMock,
        side_effect=HTTPException(status_code=404, detail="Report not found"),
    ):
        response = await client.post(
            f"/api/v1/admin/reports/{uuid.uuid4()}/approve",
            json={"acknowledge_warnings": True},
        )

    assert response.status_code == 404


async def test_approve_requires_acknowledgement_when_warnings(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    """Service raises 422 when warnings exist and acknowledge_warnings=False."""
    from fastapi import HTTPException

    with patch(
        _APPROVE,
        new_callable=AsyncMock,
        side_effect=HTTPException(
            status_code=422,
            detail="Set acknowledge_warnings=true to proceed with approval.",
        ),
    ):
        response = await client.post(
            f"/api/v1/admin/reports/{report_id}/approve",
            json={"acknowledge_warnings": False},
        )

    assert response.status_code == 422
    assert "acknowledge_warnings" in response.json()["detail"]


async def test_approve_bad_transition(client: AsyncClient, report_id: uuid.UUID) -> None:
    from fastapi import HTTPException

    with patch(
        _APPROVE,
        new_callable=AsyncMock,
        side_effect=HTTPException(
            status_code=422,
            detail="Cannot apply action 'approve' when review_status is 'draft'.",
        ),
    ):
        response = await client.post(
            f"/api/v1/admin/reports/{report_id}/approve",
            json={"acknowledge_warnings": True},
        )

    assert response.status_code == 422


async def test_approve_response_has_no_publish_wording(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    """Approve response must note that public publishing is not implemented."""
    report = _make_report(report_id, review_status="approved_internal")
    event = _make_event(report_id, "approve", "under_review", "approved_internal")

    with patch(_APPROVE, new_callable=AsyncMock, return_value=(report, event)):
        response = await client.post(
            f"/api/v1/admin/reports/{report_id}/approve",
            json={"acknowledge_warnings": True},
        )

    assert response.status_code == 200
    message = response.json()["message"]
    assert "PUBLIC PUBLISHING IS NOT IMPLEMENTED" in message
    assert "INTERNAL ADMIN ONLY" in message


# ===========================================================================
# reject
# ===========================================================================


async def test_reject_200_with_note(client: AsyncClient, report_id: uuid.UUID) -> None:
    report = _make_report(report_id, review_status="rejected_internal")
    event = _make_event(
        report_id, "reject", "under_review", "rejected_internal",
        note="Data quality insufficient."
    )

    with patch(_REJECT, new_callable=AsyncMock, return_value=(report, event)):
        response = await client.post(
            f"/api/v1/admin/reports/{report_id}/reject",
            json={"note": "Data quality insufficient.", "actor_label": "admin"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "reject"
    assert data["to_status"] == "rejected_internal"
    assert data["note"] == "Data quality insufficient."


async def test_reject_requires_note(client: AsyncClient, report_id: uuid.UUID) -> None:
    """Reject without a note must return 422 at the router level."""
    response = await client.post(
        f"/api/v1/admin/reports/{report_id}/reject",
        json={},
    )
    assert response.status_code == 422
    assert "note" in response.json()["detail"].lower()


async def test_reject_empty_note_rejected(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    response = await client.post(
        f"/api/v1/admin/reports/{report_id}/reject",
        json={"note": "   "},
    )
    assert response.status_code == 422


async def test_reject_not_found(client: AsyncClient) -> None:
    from fastapi import HTTPException

    with patch(
        _REJECT,
        new_callable=AsyncMock,
        side_effect=HTTPException(status_code=404, detail="Report not found"),
    ):
        response = await client.post(
            f"/api/v1/admin/reports/{uuid.uuid4()}/reject",
            json={"note": "Not found."},
        )

    assert response.status_code == 404


async def test_reject_audit_event_captured(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    report = _make_report(report_id, review_status="rejected_internal")
    event = _make_event(
        report_id, "reject", "under_review", "rejected_internal",
        note="Sources unverified.", actor_label="reviewer@test.com"
    )

    with patch(_REJECT, new_callable=AsyncMock, return_value=(report, event)):
        response = await client.post(
            f"/api/v1/admin/reports/{report_id}/reject",
            json={"note": "Sources unverified.", "actor_label": "reviewer@test.com"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["note"] == "Sources unverified."
    assert data["actor_label"] == "reviewer@test.com"


# ===========================================================================
# needs-revision
# ===========================================================================


async def test_needs_revision_200_with_note(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    report = _make_report(report_id, review_status="needs_revision")
    event = _make_event(
        report_id, "needs_revision", "under_review", "needs_revision",
        note="Please add SEC filing citation."
    )

    with patch(_REVISION, new_callable=AsyncMock, return_value=(report, event)):
        response = await client.post(
            f"/api/v1/admin/reports/{report_id}/needs-revision",
            json={"note": "Please add SEC filing citation."},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "needs_revision"
    assert data["to_status"] == "needs_revision"


async def test_needs_revision_requires_note(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    response = await client.post(
        f"/api/v1/admin/reports/{report_id}/needs-revision",
        json={},
    )
    assert response.status_code == 422
    assert "note" in response.json()["detail"].lower()


async def test_needs_revision_empty_note_rejected(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    response = await client.post(
        f"/api/v1/admin/reports/{report_id}/needs-revision",
        json={"note": ""},
    )
    assert response.status_code == 422


async def test_needs_revision_not_found(client: AsyncClient) -> None:
    from fastapi import HTTPException

    with patch(
        _REVISION,
        new_callable=AsyncMock,
        side_effect=HTTPException(status_code=404, detail="Report not found"),
    ):
        response = await client.post(
            f"/api/v1/admin/reports/{uuid.uuid4()}/needs-revision",
            json={"note": "Missing data."},
        )

    assert response.status_code == 404


# ===========================================================================
# review-events (audit log)
# ===========================================================================


async def test_get_review_events_empty(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    with patch(_EVENTS, new_callable=AsyncMock, return_value=[]):
        response = await client.get(
            f"/api/v1/admin/reports/{report_id}/review-events"
        )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


async def test_get_review_events_returns_event_list(
    client: AsyncClient,
    report_id: uuid.UUID,
    sample_review_event: MagicMock,
) -> None:
    with patch(_EVENTS, new_callable=AsyncMock, return_value=[sample_review_event]):
        response = await client.get(
            f"/api/v1/admin/reports/{report_id}/review-events"
        )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["action"] == "mark_under_review"
    assert item["from_status"] == "draft"
    assert item["to_status"] == "under_review"
    assert item["report_id"] == str(report_id)


async def test_get_review_events_not_found(client: AsyncClient) -> None:
    from fastapi import HTTPException

    with patch(
        _EVENTS,
        new_callable=AsyncMock,
        side_effect=HTTPException(status_code=404, detail="Report not found"),
    ):
        response = await client.get(
            f"/api/v1/admin/reports/{uuid.uuid4()}/review-events"
        )

    assert response.status_code == 404


async def test_get_review_events_invalid_uuid(client: AsyncClient) -> None:
    response = await client.get("/api/v1/admin/reports/not-a-uuid/review-events")
    assert response.status_code == 422


async def test_review_events_have_required_fields(
    client: AsyncClient,
    report_id: uuid.UUID,
    sample_review_event: MagicMock,
) -> None:
    with patch(_EVENTS, new_callable=AsyncMock, return_value=[sample_review_event]):
        response = await client.get(
            f"/api/v1/admin/reports/{report_id}/review-events"
        )

    item = response.json()["items"][0]
    required_fields = {
        "id", "report_id", "action", "from_status", "to_status",
        "note", "actor_label", "created_at"
    }
    for field in required_fields:
        assert field in item, f"Missing field: {field}"


async def test_review_event_created_after_mark_under_review(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    """Verify that the mark-under-review endpoint creates an audit event
    (confirmed by checking the response includes event metadata)."""
    report = _make_report(report_id, review_status="under_review")
    event = _make_event(report_id, "mark_under_review", "draft", "under_review",
                       actor_label="qa@test.com")

    with patch(_MARK, new_callable=AsyncMock, return_value=(report, event)):
        response = await client.post(
            f"/api/v1/admin/reports/{report_id}/mark-under-review",
            json={"actor_label": "qa@test.com"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["from_status"] == "draft"
    assert data["to_status"] == "under_review"
    assert data["actor_label"] == "qa@test.com"


# ===========================================================================
# Safety: no publish endpoint exists
# ===========================================================================


async def test_publish_endpoint_does_not_exist(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    """Verify that no public publish endpoint is exposed."""
    response = await client.post(
        f"/api/v1/admin/reports/{report_id}/publish", json={}
    )
    assert response.status_code == 404


async def test_no_buy_sell_in_review_response(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    """Review action responses must not contain BUY/SELL/HOLD/WATCH recommendations."""
    report = _make_report(report_id, review_status="under_review")
    event = _make_event(report_id, "mark_under_review", "draft", "under_review")

    with patch(_MARK, new_callable=AsyncMock, return_value=(report, event)):
        response = await client.post(
            f"/api/v1/admin/reports/{report_id}/mark-under-review",
            json={},
        )

    text = str(response.json())
    for word in ("BUY", "SELL", "HOLD", "WATCH"):
        assert word not in text, f"Response contains forbidden word: {word}"


# ===========================================================================
# Response shape: ReviewActionResponse
# ===========================================================================


@pytest.mark.parametrize(
    "endpoint,action,note,patch_target",
    [
        ("mark-under-review", "mark_under_review", None, _MARK),
        ("approve", "approve", None, _APPROVE),
        ("reject", "reject", "Must reject.", _REJECT),
        ("needs-revision", "needs_revision", "Must revise.", _REVISION),
    ],
)
async def test_review_action_response_shape(
    client: AsyncClient,
    report_id: uuid.UUID,
    endpoint: str,
    action: str,
    note: str | None,
    patch_target: str,
) -> None:
    review_status_map = {
        "mark_under_review": "under_review",
        "approve": "approved_internal",
        "reject": "rejected_internal",
        "needs_revision": "needs_revision",
    }
    to_status = review_status_map[action]
    report = _make_report(report_id, review_status=to_status)
    event = _make_event(report_id, action, "draft", to_status, note=note)

    body: dict[str, object] = {"acknowledge_warnings": True}
    if note:
        body["note"] = note

    with patch(patch_target, new_callable=AsyncMock, return_value=(report, event)):
        response = await client.post(
            f"/api/v1/admin/reports/{report_id}/{endpoint}",
            json=body,
        )

    assert response.status_code == 200
    data = response.json()
    required_fields = {
        "report_id", "action", "from_status", "to_status", "note",
        "actor_label", "message"
    }
    for field in required_fields:
        assert field in data, f"Missing field: {field}"
    assert data["action"] == action
    assert data["to_status"] == to_status
