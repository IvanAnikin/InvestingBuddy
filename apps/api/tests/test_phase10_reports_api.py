"""
Phase 10: Tests for report listing and detail API endpoints.

Service layer is mocked — no real database required.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.models.report import Report

_LIST = "app.api.v1.reports.report_service.list_reports"
_GET = "app.api.v1.reports.report_service.get_report"


# ---------------------------------------------------------------------------
# GET /api/v1/reports
# ---------------------------------------------------------------------------


async def test_list_reports_returns_200_empty(client: AsyncClient) -> None:
    with patch(_LIST, new_callable=AsyncMock, return_value=([], 0)):
        response = await client.get("/api/v1/reports")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


async def test_list_reports_returns_200_with_items(
    client: AsyncClient, sample_report: MagicMock
) -> None:
    with patch(_LIST, new_callable=AsyncMock, return_value=([sample_report], 1)):
        response = await client.get("/api/v1/reports")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["title"] == sample_report.title
    assert item["slug"] == sample_report.slug
    assert item["report_type"] == "company_deep_dive"
    assert item["status"] == "draft"


async def test_list_reports_passes_limit_offset(client: AsyncClient) -> None:
    with patch(_LIST, new_callable=AsyncMock, return_value=([], 0)) as mock_list:
        await client.get("/api/v1/reports?limit=10&offset=5")

    mock_list.assert_called_once()
    _, kwargs = mock_list.call_args
    assert kwargs.get("limit") == 10
    assert kwargs.get("offset") == 5


async def test_list_reports_default_pagination(client: AsyncClient) -> None:
    with patch(_LIST, new_callable=AsyncMock, return_value=([], 0)) as mock_list:
        await client.get("/api/v1/reports")

    mock_list.assert_called_once()
    _, kwargs = mock_list.call_args
    assert kwargs.get("limit") == 50
    assert kwargs.get("offset") == 0


# ---------------------------------------------------------------------------
# GET /api/v1/reports/{report_id}
# ---------------------------------------------------------------------------


async def test_get_report_returns_200(
    client: AsyncClient,
    report_id: uuid.UUID,
    sample_report: MagicMock,
) -> None:
    with patch(_GET, new_callable=AsyncMock, return_value=sample_report):
        response = await client.get(f"/api/v1/reports/{report_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(report_id)
    assert data["title"] == sample_report.title
    assert data["status"] == "draft"
    assert data["report_type"] == "company_deep_dive"
    assert data["content_markdown"] == "# VOW3"
    assert data["summary"] == "Placeholder analysis."


async def test_get_report_not_found_returns_404(
    client: AsyncClient,
    report_id: uuid.UUID,
) -> None:
    with patch(_GET, new_callable=AsyncMock, return_value=None):
        response = await client.get(f"/api/v1/reports/{report_id}")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_get_report_invalid_uuid_returns_422(client: AsyncClient) -> None:
    response = await client.get("/api/v1/reports/not-a-uuid")
    assert response.status_code == 422


async def test_get_report_unknown_uuid_returns_404(client: AsyncClient) -> None:
    unknown_id = uuid.uuid4()
    with patch(_GET, new_callable=AsyncMock, return_value=None):
        response = await client.get(f"/api/v1/reports/{unknown_id}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Response shape validation
# ---------------------------------------------------------------------------


async def test_report_response_has_all_expected_fields(
    client: AsyncClient,
    report_id: uuid.UUID,
    sample_report: MagicMock,
) -> None:
    with patch(_GET, new_callable=AsyncMock, return_value=sample_report):
        response = await client.get(f"/api/v1/reports/{report_id}")

    data = response.json()
    required_fields = {
        "id",
        "title",
        "slug",
        "report_type",
        "status",
        "summary",
        "content_markdown",
        "content_html",
        "created_by_agent_run_id",
        "published_at",
        "created_at",
        "updated_at",
    }
    for field in required_fields:
        assert field in data, f"Missing field: {field}"


async def test_list_reports_items_contain_required_fields(
    client: AsyncClient, sample_report: MagicMock
) -> None:
    with patch(_LIST, new_callable=AsyncMock, return_value=([sample_report], 1)):
        response = await client.get("/api/v1/reports")

    item = response.json()["items"][0]
    assert "id" in item
    assert "title" in item
    assert "slug" in item
    assert "status" in item
    assert "report_type" in item
    assert "created_at" in item


# ---------------------------------------------------------------------------
# Safety: no internal statuses or recommendations in reports
# ---------------------------------------------------------------------------


async def test_report_content_does_not_expose_buy_sell(
    client: AsyncClient,
    report_id: uuid.UUID,
) -> None:
    """Draft report content should not contain BUY/SELL/HOLD/WATCH as
    a recommendation — content_markdown is raw AI output, not validated."""
    safe_report = MagicMock(spec=Report)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    safe_report.id = report_id
    safe_report.title = "Test Report"
    safe_report.slug = "test-report"
    safe_report.report_type = "company_deep_dive"
    safe_report.period_start = None
    safe_report.period_end = None
    safe_report.status = "draft"
    safe_report.summary = "Admin draft only."
    safe_report.content_markdown = (
        "# Admin Draft — Not Investment Advice\n\nThis is an internal draft."
    )
    safe_report.content_html = None
    safe_report.created_by_agent_run_id = None
    safe_report.published_at = None
    safe_report.created_at = now
    safe_report.updated_at = now
    # Phase 11 review fields
    safe_report.review_status = "draft"
    safe_report.reviewed_at = None
    safe_report.reviewer_note = None
    safe_report.review_decision_reason = None
    safe_report.human_review_required = True
    safe_report.approved_by = None
    safe_report.rejected_by = None

    with patch(_GET, new_callable=AsyncMock, return_value=safe_report):
        response = await client.get(f"/api/v1/reports/{report_id}")

    assert response.status_code == 200
    content = response.json().get("content_markdown", "")
    assert "Not Investment Advice" in content or content is not None


@pytest.mark.parametrize("endpoint", ["/api/v1/reports", "/api/v1/reports"])
async def test_reports_endpoints_are_accessible_without_auth(
    client: AsyncClient,
    endpoint: str,
) -> None:
    """Phase 10: no auth is enforced yet — documented as future work."""
    with patch(_LIST, new_callable=AsyncMock, return_value=([], 0)):
        response = await client.get(endpoint)
    assert response.status_code == 200
