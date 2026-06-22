"""
Financial data provider smoke-test endpoints.

These are DEVELOPMENT / INTERNAL endpoints for verifying provider wiring.
They are NOT production user-facing endpoints.
They do NOT produce real investment advice.
They use the configured provider (default: mock) — no real API calls in CI.

Endpoints:
  GET /api/v1/financial-data/providers             List all registered providers
  GET /api/v1/financial-data/mock/company/{ticker} Company profile via mock provider
  GET /api/v1/financial-data/mock/prices/{ticker}  Price history via mock provider
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    PriceHistoryData,
)
from app.integrations.financial_data_service import FinancialDataService

router = APIRouter(
    prefix="/financial-data",
    tags=["financial-data (dev/smoke-test)"],
)


@router.get(
    "/providers",
    summary="[DEV] List all registered financial data providers",
    description=(
        "Development endpoint. Returns metadata for all registered providers "
        "including their source tier, capabilities, and current status. "
        "No network calls are made."
    ),
)
async def list_providers() -> list[dict]:
    svc = FinancialDataService()
    return svc.list_providers()


@router.get(
    "/mock/company/{ticker}",
    response_model=CompanyProfileData,
    summary="[DEV] Fetch company profile from mock provider",
    description=(
        "Development smoke-test endpoint. Returns deterministic demo data "
        "from MockFinancialDataProvider. Data is marked is_mock=True and is "
        "NOT real financial information. Not investment advice."
    ),
)
async def mock_company_profile(
    ticker: str,
    exchange: str | None = Query(default=None, description="Exchange code (optional)"),
) -> CompanyProfileData:
    svc = FinancialDataService(provider_name="mock")
    try:
        return await svc.get_company_profile(ticker=ticker.upper(), exchange=exchange)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.get(
    "/mock/prices/{ticker}",
    response_model=PriceHistoryData,
    summary="[DEV] Fetch price history from mock provider",
    description=(
        "Development smoke-test endpoint. Returns deterministic demo price "
        "series from MockFinancialDataProvider. Data is marked is_mock=True "
        "and is NOT real market data. Not investment advice."
    ),
)
async def mock_price_history(
    ticker: str,
    exchange: str | None = Query(default=None, description="Exchange code (optional)"),
    start_date: str | None = Query(default=None, description="Start date YYYY-MM-DD"),
    end_date: str | None = Query(default=None, description="End date YYYY-MM-DD"),
) -> PriceHistoryData:
    svc = FinancialDataService(provider_name="mock")
    try:
        return await svc.get_price_history(
            ticker=ticker.upper(),
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
