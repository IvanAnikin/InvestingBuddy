"""
Financial data provider smoke-test and diagnostic endpoints.

These are DEVELOPMENT / INTERNAL endpoints for verifying provider wiring.
They are NOT production user-facing endpoints.
They do NOT produce real investment advice.

Phase 4 endpoints (mock only — no external calls):
  GET /api/v1/financial-data/providers
  GET /api/v1/financial-data/mock/company/{ticker}
  GET /api/v1/financial-data/mock/prices/{ticker}

Phase 5 endpoints (live free providers — require network access):
  GET /api/v1/financial-data/stooq/prices/{ticker}     Live Stooq price history (T5)
  GET /api/v1/financial-data/gleif/entity/{lei_or_name} GLEIF entity lookup (T2)
  GET /api/v1/financial-data/sec-edgar/company/{cik}   SEC EDGAR company by CIK (T2)

Phase 5 endpoints make real external HTTP calls. Do not expose them to end users.
Do not use responses as investment advice.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    PriceHistoryData,
)
from app.integrations.financial_data_service import FinancialDataService
from app.integrations.providers.gleif_provider import GleifProvider
from app.integrations.providers.sec_edgar_provider import SecEdgarProvider
from app.integrations.providers.stooq_provider import StooqProvider

router = APIRouter(
    prefix="/financial-data",
    tags=["financial-data (dev/smoke-test)"],
)


# ---------------------------------------------------------------------------
# Phase 4 — mock provider endpoints (no external calls)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Phase 5 — live free provider endpoints (make real external HTTP calls)
# ---------------------------------------------------------------------------


@router.get(
    "/stooq/prices/{ticker}",
    response_model=PriceHistoryData,
    summary="[DEV] Fetch live price history from Stooq (T5 — free, no API key)",
    description=(
        "[LIVE — makes external HTTP call to stooq.com] "
        "Returns historical OHLCV price data for the given ticker. "
        "Source tier: T5_api_aggregator. No API key required. "
        "Data is NOT investment advice. Dev/diagnostic use only."
    ),
)
async def stooq_price_history(
    ticker: str,
    exchange: str | None = Query(
        default=None,
        description=(
            "Exchange code used to build the Stooq symbol suffix "
            "(e.g. NASDAQ→US, XETRA→DE, LSE→UK). Defaults to US."
        ),
    ),
    start_date: str | None = Query(default=None, description="Start date YYYY-MM-DD"),
    end_date: str | None = Query(default=None, description="End date YYYY-MM-DD"),
) -> PriceHistoryData:
    provider = StooqProvider()
    try:
        return await provider.get_price_history(
            ticker=ticker.upper(),
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Stooq request failed: {exc}",
        ) from exc


@router.get(
    "/gleif/entity/{lei_or_name}",
    response_model=CompanyProfileData,
    summary="[DEV] Lookup legal entity in GLEIF registry (T2 — free, no API key)",
    description=(
        "[LIVE — makes external HTTP call to api.gleif.org] "
        "Looks up a legal entity by LEI code (20-character alphanumeric) or "
        "by legal name (name search). Source tier: T2_regulator_or_gov. "
        "Data quality: A_verified. No API key required. "
        "Not investment advice. Dev/diagnostic use only."
    ),
)
async def gleif_entity_lookup(
    lei_or_name: str,
) -> CompanyProfileData:
    provider = GleifProvider()
    try:
        return await provider.get_company_profile(lei_or_name)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GLEIF request failed: {exc}",
        ) from exc


@router.get(
    "/sec-edgar/company/{cik}",
    response_model=CompanyProfileData,
    summary="[DEV] Fetch company profile from SEC EDGAR by CIK (T2 — free, no API key)",
    description=(
        "[LIVE — makes external HTTP call to data.sec.gov] "
        "Fetches company identity and filing metadata from the SEC EDGAR "
        "submissions endpoint using the company's CIK (Central Index Key). "
        "Source tier: T2_regulator_or_gov. Data quality: A_verified. "
        "No API key required. Not investment advice. Dev/diagnostic use only."
    ),
)
async def sec_edgar_company_by_cik(
    cik: str,
) -> CompanyProfileData:
    if not cik.strip().isdigit():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"CIK must be a numeric string (e.g. '320193' for Apple Inc.). "
                f"Got: '{cik}'. Find CIKs at https://www.sec.gov/cgi-bin/browse-edgar"
            ),
        )
    provider = SecEdgarProvider()
    try:
        return await provider.get_company_by_cik(cik.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SEC EDGAR request failed: {exc}",
        ) from exc
