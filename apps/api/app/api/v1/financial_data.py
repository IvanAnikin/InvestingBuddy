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
  GET /api/v1/financial-data/stooq/prices/{ticker}        Live Stooq price history (T5)
  GET /api/v1/financial-data/gleif/entity/{lei_or_name}   GLEIF entity lookup (T2)
  GET /api/v1/financial-data/sec-edgar/company/{cik}      SEC EDGAR company by CIK (T2)

Phase 13 endpoints (EODHD — paid API key required; admin/dev only):
  GET /api/v1/financial-data/eodhd/status                 EODHD provider status
  GET /api/v1/financial-data/eodhd/company/{symbol}       Company profile from EODHD
  GET /api/v1/financial-data/eodhd/fundamentals/{symbol}  Fundamentals from EODHD
  GET /api/v1/financial-data/resolve                      Resolve ticker/name to symbol

Phase 13 endpoints require EODHD_API_KEY. They make real external HTTP calls.
Do not expose them to end users. Do not use responses as investment advice.
EODHD is classified as T5_api_aggregator — never promote to T1/T2.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    FundamentalsData,
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


# ---------------------------------------------------------------------------
# Phase 13 — EODHD endpoints (paid key required; admin/dev diagnostic only)
# ---------------------------------------------------------------------------


@router.get(
    "/eodhd/status",
    summary="[DEV] EODHD provider status",
    description=(
        "Returns the current EODHD provider status. "
        "When EODHD_API_KEY is not set, returns not_configured. "
        "Does not make any network call. "
        "Source tier: T5_api_aggregator. Not investment advice."
    ),
)
async def eodhd_status() -> dict:
    from app.integrations.providers.eodhd_provider import EodhdProvider

    provider = EodhdProvider()
    return {
        "provider": "eodhd",
        "source_tier": "T5_api_aggregator",
        "status": provider.get_provider_status().value,
        "capabilities": [c.value for c in provider.get_supported_capabilities()],
        "note": (
            "EODHD is classified as T5_api_aggregator. "
            "Do not promote EODHD data to T1/T2 tier. "
            "Requires EODHD_API_KEY in environment."
        ),
    }


@router.get(
    "/eodhd/company/{symbol}",
    response_model=CompanyProfileData,
    summary="[DEV] Company profile from EODHD (T5 — requires EODHD_API_KEY)",
    description=(
        "[LIVE — makes external HTTP call to EODHD API] "
        "Fetches company identity/profile from the EODHD /fundamentals endpoint. "
        "Symbol must be in EODHD format: TICKER.EXCHANGE (e.g. AAPL.US, VOW3.XETRA). "
        "Source tier: T5_api_aggregator. Requires EODHD_API_KEY. "
        "Not investment advice. Dev/diagnostic use only."
    ),
)
async def eodhd_company_profile(
    symbol: str,
    exchange: str | None = Query(
        default=None,
        description=(
            "Optional exchange override. If symbol already contains a dot "
            "(e.g. AAPL.US), the exchange suffix in the symbol takes precedence."
        ),
    ),
) -> CompanyProfileData:
    from app.integrations.providers.eodhd_provider import (
        EodhdAuthError,
        EodhdNotFoundError,
        EodhdProviderError,
        EodhdRateLimitError,
    )

    svc = FinancialDataService(provider_name="eodhd")
    ticker = symbol.split(".")[0] if "." in symbol else symbol
    exch = symbol.split(".")[1] if "." in symbol else exchange
    try:
        return await svc.get_company_profile(ticker=ticker.upper(), exchange=exch)
    except EodhdAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except EodhdNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EodhdRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except EodhdProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"EODHD company profile error: {exc}",
        ) from exc


@router.get(
    "/eodhd/fundamentals/{symbol}",
    response_model=FundamentalsData,
    summary="[DEV] Full fundamentals from EODHD (T5 — requires EODHD_API_KEY)",
    description=(
        "[LIVE — makes external HTTP call to EODHD API] "
        "Fetches full fundamentals (Highlights, Valuation, SharesStats, "
        "Income Statement, Balance Sheet, Cash Flow) from EODHD. "
        "All values are wrapped in FundamentalDataPoint envelopes with T5 tier. "
        "Symbol must be in EODHD format: TICKER.EXCHANGE (e.g. AAPL.US). "
        "Requires EODHD_API_KEY. Not investment advice. Dev/diagnostic use only."
    ),
)
async def eodhd_fundamentals(
    symbol: str,
    exchange: str | None = Query(default=None, description="Optional exchange override"),
) -> FundamentalsData:
    from app.integrations.providers.eodhd_provider import (
        EodhdAuthError,
        EodhdNotFoundError,
        EodhdProviderError,
        EodhdRateLimitError,
    )

    svc = FinancialDataService(provider_name="eodhd")
    ticker = symbol.split(".")[0] if "." in symbol else symbol
    exch = symbol.split(".")[1] if "." in symbol else exchange
    try:
        return await svc.get_fundamentals(ticker=ticker.upper(), exchange=exch)
    except EodhdAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except EodhdNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EodhdRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except EodhdProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Fundamentals not supported by active provider: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"EODHD fundamentals error: {exc}",
        ) from exc


@router.get(
    "/resolve",
    summary="[DEV] Resolve a company name or ticker to a canonical EODHD symbol",
    description=(
        "Resolves a company name or ticker to a canonical EODHD symbol using the "
        "EODHD search API. When EODHD_API_KEY is not configured, performs structural "
        "pattern matching only (low confidence). "
        "Returns a list of candidates with confidence scores and ambiguity warnings. "
        "Do not silently accept an ambiguous result — verify the symbol before use. "
        "Not investment advice. Dev/diagnostic use only."
    ),
)
async def resolve_company(
    query: str = Query(description="Ticker, company name, or EODHD symbol (e.g. AAPL.US)"),
    exchange: str | None = Query(
        default=None,
        description="Optional exchange hint to disambiguate (e.g. NASDAQ, XETRA)",
    ),
    provider: str = Query(default="eodhd", description="Provider to use for search (eodhd)"),
) -> dict:
    from app.services.identifier_resolver import CompanyIdentifierResolver

    resolver = CompanyIdentifierResolver()
    result = await resolver.resolve(query=query, exchange=exchange, provider=provider)
    return {
        "query": result.query,
        "is_ambiguous": result.is_ambiguous,
        "warnings": result.warnings,
        "candidates": [
            {
                "canonical_ticker": c.canonical_ticker,
                "provider_symbol": c.provider_symbol,
                "exchange": c.exchange,
                "company_name": c.company_name,
                "country": c.country,
                "provider_confidence": round(c.provider_confidence, 3),
                "is_ambiguous": c.is_ambiguous,
                "source": c.source,
                "warnings": c.warnings,
            }
            for c in result.candidates
        ],
        "source_tier": "T5_api_aggregator",
        "note": (
            "EODHD resolution is T5 quality. Verify the resolved symbol against "
            "primary sources (company IR, exchange listing) before use in research."
        ),
    }
