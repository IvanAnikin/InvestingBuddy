"""
Tests for citation API endpoints:
  POST /api/v1/reports/{report_id}/citations
  GET  /api/v1/reports/{report_id}/citations
  POST /api/v1/reports/{report_id}/validate-citations
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient

from app.models.source import Citation
from app.schemas.source import CitationValidationResult

_GET_REPORT = "app.api.v1.citations.report_service.get_report"
_GET_SOURCE = "app.api.v1.citations.source_service.get_source"
_CREATE_CITATION = "app.api.v1.citations.citation_service.create_citation"
_LIST_CITATIONS = "app.api.v1.citations.citation_service.list_citations_for_report"
_COUNT_CITATIONS = "app.api.v1.citations.citation_service.count_citations_for_report"
_VALIDATE = "app.api.v1.citations.citation_service.validate_citations_for_draft"


# ---------------------------------------------------------------------------
# POST /api/v1/reports/{report_id}/citations
# ---------------------------------------------------------------------------


async def test_create_citation_returns_201(
    client: AsyncClient,
    sample_report: MagicMock,
    sample_source: MagicMock,
    sample_citation: Citation,
    report_id: uuid.UUID,
    source_id: uuid.UUID,
) -> None:
    with (
        patch(_GET_REPORT, new_callable=AsyncMock, return_value=sample_report),
        patch(_GET_SOURCE, new_callable=AsyncMock, return_value=sample_source),
        patch(_CREATE_CITATION, new_callable=AsyncMock, return_value=sample_citation),
    ):
        response = await client.post(
            f"/api/v1/reports/{report_id}/citations",
            json={"source_id": str(source_id), "claim_text": "thesis"},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["claim_text"] == "thesis"
    assert data["source_id"] == str(source_id)
    assert data["report_id"] == str(report_id)


async def test_create_citation_report_not_found_returns_404(
    client: AsyncClient, report_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    with patch(_GET_REPORT, new_callable=AsyncMock, return_value=None):
        response = await client.post(
            f"/api/v1/reports/{report_id}/citations",
            json={"source_id": str(source_id)},
        )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_create_citation_source_not_found_returns_422(
    client: AsyncClient,
    sample_report: MagicMock,
    report_id: uuid.UUID,
    source_id: uuid.UUID,
) -> None:
    with (
        patch(_GET_REPORT, new_callable=AsyncMock, return_value=sample_report),
        patch(_GET_SOURCE, new_callable=AsyncMock, return_value=None),
    ):
        response = await client.post(
            f"/api/v1/reports/{report_id}/citations",
            json={"source_id": str(source_id)},
        )

    assert response.status_code == 422
    assert "not found" in response.json()["detail"].lower()


async def test_create_citation_missing_source_id_returns_422(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    response = await client.post(
        f"/api/v1/reports/{report_id}/citations",
        json={},  # source_id is required
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/reports/{report_id}/citations
# ---------------------------------------------------------------------------


async def test_list_citations_returns_200(
    client: AsyncClient,
    sample_report: MagicMock,
    sample_citation: Citation,
    report_id: uuid.UUID,
) -> None:
    with (
        patch(_GET_REPORT, new_callable=AsyncMock, return_value=sample_report),
        patch(_LIST_CITATIONS, new_callable=AsyncMock, return_value=[sample_citation]),
        patch(_COUNT_CITATIONS, new_callable=AsyncMock, return_value=1),
    ):
        response = await client.get(f"/api/v1/reports/{report_id}/citations")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["claim_text"] == "thesis"


async def test_list_citations_empty(
    client: AsyncClient, sample_report: MagicMock, report_id: uuid.UUID
) -> None:
    with (
        patch(_GET_REPORT, new_callable=AsyncMock, return_value=sample_report),
        patch(_LIST_CITATIONS, new_callable=AsyncMock, return_value=[]),
        patch(_COUNT_CITATIONS, new_callable=AsyncMock, return_value=0),
    ):
        response = await client.get(f"/api/v1/reports/{report_id}/citations")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


async def test_list_citations_report_not_found_returns_404(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    with patch(_GET_REPORT, new_callable=AsyncMock, return_value=None):
        response = await client.get(f"/api/v1/reports/{report_id}/citations")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/reports/{report_id}/validate-citations
# ---------------------------------------------------------------------------


async def test_validate_citations_returns_200(
    client: AsyncClient,
    sample_report: MagicMock,
    sample_citation: Citation,
    report_id: uuid.UUID,
) -> None:
    validation_result = CitationValidationResult(
        status="warnings",
        total_claims=1,
        cited_claims=0,
        missing_citations=[],
        approved_claims=[],
        warnings=["[PLACEHOLDER] Analysis output is marked is_placeholder=true."],
    )

    with (
        patch(_GET_REPORT, new_callable=AsyncMock, return_value=sample_report),
        patch(_LIST_CITATIONS, new_callable=AsyncMock, return_value=[sample_citation]),
        patch(_VALIDATE, return_value=validation_result),
    ):
        response = await client.post(f"/api/v1/reports/{report_id}/validate-citations")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "warnings"
    assert "warnings" in data


async def test_validate_citations_report_not_found_returns_404(
    client: AsyncClient, report_id: uuid.UUID
) -> None:
    with patch(_GET_REPORT, new_callable=AsyncMock, return_value=None):
        response = await client.post(
            f"/api/v1/reports/{report_id}/validate-citations"
        )

    assert response.status_code == 404
