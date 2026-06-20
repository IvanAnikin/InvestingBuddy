"""
Tests for company API endpoints.

Service layer is mocked so no database is needed.
"""

import uuid
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

from app.models.company import Company

_GET_BY_TICKER = "app.api.v1.companies.company_service.get_company_by_ticker"
_CREATE = "app.api.v1.companies.company_service.create_company"
_LIST = "app.api.v1.companies.company_service.list_companies"
_COUNT = "app.api.v1.companies.company_service.count_companies"
_GET = "app.api.v1.companies.company_service.get_company"

# ---------------------------------------------------------------------------
# POST /api/v1/companies
# ---------------------------------------------------------------------------


async def test_create_company_returns_201(
    client: AsyncClient, sample_company: Company
) -> None:
    with (
        patch(_GET_BY_TICKER, new_callable=AsyncMock, return_value=None),
        patch(_CREATE, new_callable=AsyncMock, return_value=sample_company),
    ):
        response = await client.post(
            "/api/v1/companies",
            json={"ticker": "VOW3", "exchange": "XETRA", "name": "Volkswagen AG"},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["ticker"] == "VOW3"
    assert data["exchange"] == "XETRA"
    assert data["name"] == "Volkswagen AG"
    assert data["status"] == "new"
    assert "id" in data


async def test_create_company_duplicate_returns_409(
    client: AsyncClient, sample_company: Company
) -> None:
    with patch(_GET_BY_TICKER, new_callable=AsyncMock, return_value=sample_company):
        response = await client.post(
            "/api/v1/companies",
            json={"ticker": "VOW3", "exchange": "XETRA", "name": "Volkswagen AG"},
        )

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


async def test_create_company_missing_required_fields(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/companies",
        json={"ticker": "VOW3"},  # missing exchange and name
    )
    assert response.status_code == 422


async def test_create_company_with_optional_fields(
    client: AsyncClient, sample_company: Company
) -> None:
    with (
        patch(_GET_BY_TICKER, new_callable=AsyncMock, return_value=None),
        patch(_CREATE, new_callable=AsyncMock, return_value=sample_company),
    ):
        response = await client.post(
            "/api/v1/companies",
            json={
                "ticker": "VOW3",
                "exchange": "XETRA",
                "name": "Volkswagen AG",
                "country": "Germany",
                "sector": "Automotive",
                "market_cap": 60000000000.0,
                "currency": "EUR",
            },
        )

    assert response.status_code == 201


# ---------------------------------------------------------------------------
# GET /api/v1/companies
# ---------------------------------------------------------------------------


async def test_list_companies_returns_200(
    client: AsyncClient, sample_company: Company
) -> None:
    with (
        patch(_LIST, new_callable=AsyncMock, return_value=[sample_company]),
        patch(_COUNT, new_callable=AsyncMock, return_value=1),
    ):
        response = await client.get("/api/v1/companies")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["ticker"] == "VOW3"


async def test_list_companies_empty(client: AsyncClient) -> None:
    with (
        patch(_LIST, new_callable=AsyncMock, return_value=[]),
        patch(_COUNT, new_callable=AsyncMock, return_value=0),
    ):
        response = await client.get("/api/v1/companies")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


# ---------------------------------------------------------------------------
# GET /api/v1/companies/{id}
# ---------------------------------------------------------------------------


async def test_get_company_by_id_returns_200(
    client: AsyncClient, sample_company: Company, company_id: uuid.UUID
) -> None:
    with patch(_GET, new_callable=AsyncMock, return_value=sample_company):
        response = await client.get(f"/api/v1/companies/{company_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(company_id)
    assert data["ticker"] == "VOW3"


async def test_get_company_not_found_returns_404(
    client: AsyncClient, company_id: uuid.UUID
) -> None:
    with patch(_GET, new_callable=AsyncMock, return_value=None):
        response = await client.get(f"/api/v1/companies/{company_id}")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_get_company_invalid_uuid_returns_422(client: AsyncClient) -> None:
    response = await client.get("/api/v1/companies/not-a-uuid")
    assert response.status_code == 422
