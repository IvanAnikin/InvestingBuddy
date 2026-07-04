"""
Tests for source API endpoints: POST, GET /api/v1/sources, GET /api/v1/sources/{id}.

Service layer is mocked so no database is needed.
"""

import uuid
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

from app.models.source import Source

_GET_OR_CREATE = "app.api.v1.sources.source_service.get_or_create_source"
_LIST = "app.api.v1.sources.source_service.list_sources"
_COUNT = "app.api.v1.sources.source_service.count_sources"
_GET = "app.api.v1.sources.source_service.get_source"


# ---------------------------------------------------------------------------
# POST /api/v1/sources
# ---------------------------------------------------------------------------


async def test_create_source_returns_201(
    client: AsyncClient, sample_source: Source
) -> None:
    with patch(_GET_OR_CREATE, new_callable=AsyncMock, return_value=(sample_source, True)):
        response = await client.post(
            "/api/v1/sources",
            json={
                "source_type": "placeholder",
                "title": "[PLACEHOLDER] Volkswagen AG — workflow-generated source",
                "publisher": "InvestingBuddy workflow (placeholder)",
                "credibility_score": 0.0,
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert data["source_type"] == "placeholder"
    assert "id" in data


async def test_create_source_returns_existing_on_dedup(
    client: AsyncClient, sample_source: Source
) -> None:
    # get_or_create returns (source, created=False) on dedup — still 201
    with patch(_GET_OR_CREATE, new_callable=AsyncMock, return_value=(sample_source, False)):
        response = await client.post(
            "/api/v1/sources",
            json={
                "source_type": "placeholder",
                "title": "[PLACEHOLDER] Volkswagen AG — workflow-generated source",
            },
        )

    assert response.status_code == 201
    assert response.json()["id"] == str(sample_source.id)


async def test_create_source_missing_required_fields(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/sources",
        json={"source_type": "news_article"},  # title is required
    )
    assert response.status_code == 422


async def test_create_source_credibility_score_out_of_range(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/sources",
        json={
            "source_type": "news_article",
            "title": "Test",
            "credibility_score": 1.5,  # > 1.0, invalid
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/sources
# ---------------------------------------------------------------------------


async def test_list_sources_returns_200(
    client: AsyncClient, sample_source: Source
) -> None:
    with (
        patch(_LIST, new_callable=AsyncMock, return_value=[sample_source]),
        patch(_COUNT, new_callable=AsyncMock, return_value=1),
    ):
        response = await client.get("/api/v1/sources")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["source_type"] == "placeholder"


async def test_list_sources_empty(client: AsyncClient) -> None:
    with (
        patch(_LIST, new_callable=AsyncMock, return_value=[]),
        patch(_COUNT, new_callable=AsyncMock, return_value=0),
    ):
        response = await client.get("/api/v1/sources")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


# ---------------------------------------------------------------------------
# GET /api/v1/sources/{id}
# ---------------------------------------------------------------------------


async def test_get_source_by_id_returns_200(
    client: AsyncClient, sample_source: Source, source_id: uuid.UUID
) -> None:
    with patch(_GET, new_callable=AsyncMock, return_value=sample_source):
        response = await client.get(f"/api/v1/sources/{source_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(source_id)
    assert data["source_type"] == "placeholder"


async def test_get_source_not_found_returns_404(
    client: AsyncClient, source_id: uuid.UUID
) -> None:
    with patch(_GET, new_callable=AsyncMock, return_value=None):
        response = await client.get(f"/api/v1/sources/{source_id}")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_get_source_invalid_uuid_returns_422(client: AsyncClient) -> None:
    response = await client.get("/api/v1/sources/not-a-uuid")
    assert response.status_code == 422
