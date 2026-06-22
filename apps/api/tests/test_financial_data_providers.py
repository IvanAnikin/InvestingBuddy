"""
Offline tests for Phase 4: Financial Data Provider Foundation.

All tests run with:
  - No Azure credentials
  - No EODHD API key
  - No OpenBB installation
  - No SEC EDGAR calls
  - No Stooq calls
  - No GLEIF calls
  - No LLM calls
  - No external network

Tests cover:
  - Provider registry listing
  - Default mock provider selection
  - Mock provider company profile
  - Mock provider price history
  - Mock provider fundamentals
  - Provider response metadata
  - Source tier correctness per provider
  - EODHD status when key is missing
  - EODHD raises NotImplementedError (not a network call)
  - Unknown provider raises ValueError
  - Provider output schema validation (Pydantic)
  - API endpoints: GET /financial-data/providers
  - API endpoints: GET /financial-data/mock/company/{ticker}
  - API endpoints: GET /financial-data/mock/prices/{ticker}
"""

from __future__ import annotations

import pytest

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    DataQuality,
    FinancialDataProvider,
    FundamentalsData,
    PriceHistoryData,
    ProviderCapability,
    ProviderResponseMetadata,
    ProviderStatus,
    SourceTier,
)
from app.integrations.financial_data_service import FinancialDataService, get_provider
from app.integrations.providers.eodhd_provider import EodhdProvider
from app.integrations.providers.gleif_provider import GleifProvider
from app.integrations.providers.mock_provider import MockFinancialDataProvider
from app.integrations.providers.openbb_provider import OpenBBProvider
from app.integrations.providers.sec_edgar_provider import SecEdgarProvider
from app.integrations.providers.stooq_provider import StooqProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_provider() -> MockFinancialDataProvider:
    return MockFinancialDataProvider()


@pytest.fixture
def service() -> FinancialDataService:
    return FinancialDataService(provider_name="mock")


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------


def test_registry_contains_all_expected_providers() -> None:
    svc = FinancialDataService()
    providers = svc.list_providers()
    names = {p["name"] for p in providers}
    assert {"mock", "eodhd", "sec_edgar", "stooq", "openbb", "gleif"} <= names


def test_list_providers_returns_required_fields() -> None:
    svc = FinancialDataService()
    providers = svc.list_providers()
    for p in providers:
        assert "name" in p
        assert "source_tier" in p
        assert "capabilities" in p
        assert "status" in p


def test_default_provider_is_mock() -> None:
    provider = get_provider()
    assert provider.provider_name == "mock"


def test_get_provider_by_name_mock() -> None:
    provider = get_provider("mock")
    assert isinstance(provider, MockFinancialDataProvider)


def test_get_provider_unknown_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown financial data provider"):
        get_provider("does_not_exist")


def test_get_provider_unknown_lists_known_in_error() -> None:
    try:
        get_provider("nonexistent_provider_xyz")
    except ValueError as exc:
        assert "mock" in str(exc)
        assert "eodhd" in str(exc)


# ---------------------------------------------------------------------------
# Mock provider — properties
# ---------------------------------------------------------------------------


def test_mock_provider_name(mock_provider: MockFinancialDataProvider) -> None:
    assert mock_provider.provider_name == "mock"


def test_mock_provider_source_tier(mock_provider: MockFinancialDataProvider) -> None:
    assert mock_provider.source_tier == SourceTier.T6_model_estimate


def test_mock_provider_status_is_ok(mock_provider: MockFinancialDataProvider) -> None:
    assert mock_provider.get_provider_status() == ProviderStatus.ok


def test_mock_provider_capabilities(mock_provider: MockFinancialDataProvider) -> None:
    caps = mock_provider.get_supported_capabilities()
    assert ProviderCapability.company_profile in caps
    assert ProviderCapability.price_history in caps
    assert ProviderCapability.fundamentals in caps


# ---------------------------------------------------------------------------
# Mock provider — company profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_company_profile_returns_data(
    mock_provider: MockFinancialDataProvider,
) -> None:
    profile = await mock_provider.get_company_profile("TEST")
    assert isinstance(profile, CompanyProfileData)


@pytest.mark.asyncio
async def test_mock_company_profile_ticker(
    mock_provider: MockFinancialDataProvider,
) -> None:
    profile = await mock_provider.get_company_profile("TEST")
    assert profile.ticker == "TEST"


@pytest.mark.asyncio
async def test_mock_company_profile_is_mock(
    mock_provider: MockFinancialDataProvider,
) -> None:
    profile = await mock_provider.get_company_profile("TEST")
    assert profile.meta.is_mock is True


@pytest.mark.asyncio
async def test_mock_company_profile_data_quality_is_weak(
    mock_provider: MockFinancialDataProvider,
) -> None:
    profile = await mock_provider.get_company_profile("TEST")
    assert profile.data_quality == DataQuality.D_weak_or_stale.value


@pytest.mark.asyncio
async def test_mock_company_profile_meta_has_provider_name(
    mock_provider: MockFinancialDataProvider,
) -> None:
    profile = await mock_provider.get_company_profile("TEST")
    assert profile.meta.provider_name == "mock"


@pytest.mark.asyncio
async def test_mock_company_profile_meta_has_retrieved_at(
    mock_provider: MockFinancialDataProvider,
) -> None:
    profile = await mock_provider.get_company_profile("TEST")
    assert profile.meta.retrieved_at is not None


@pytest.mark.asyncio
async def test_mock_company_profile_legal_name_contains_mock(
    mock_provider: MockFinancialDataProvider,
) -> None:
    profile = await mock_provider.get_company_profile("TEST")
    assert "MOCK" in profile.legal_name.upper() or "mock" in profile.legal_name.lower()


@pytest.mark.asyncio
async def test_mock_company_profile_exchange_defaults_to_ose(
    mock_provider: MockFinancialDataProvider,
) -> None:
    profile = await mock_provider.get_company_profile("TEST", exchange=None)
    assert profile.exchange == "OSE"


@pytest.mark.asyncio
async def test_mock_company_profile_exchange_custom(
    mock_provider: MockFinancialDataProvider,
) -> None:
    profile = await mock_provider.get_company_profile("TEST", exchange="XETRA")
    assert profile.exchange == "XETRA"


@pytest.mark.asyncio
async def test_mock_company_profile_description_not_investment_advice(
    mock_provider: MockFinancialDataProvider,
) -> None:
    profile = await mock_provider.get_company_profile("TEST")
    desc = profile.description or ""
    assert "not investment advice" in desc.lower() or "MOCK" in desc.upper()


# ---------------------------------------------------------------------------
# Mock provider — price history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_price_history_returns_data(
    mock_provider: MockFinancialDataProvider,
) -> None:
    history = await mock_provider.get_price_history("TEST")
    assert isinstance(history, PriceHistoryData)


@pytest.mark.asyncio
async def test_mock_price_history_has_price_points(
    mock_provider: MockFinancialDataProvider,
) -> None:
    history = await mock_provider.get_price_history("TEST")
    assert len(history.price_points) > 0


@pytest.mark.asyncio
async def test_mock_price_history_is_mock(
    mock_provider: MockFinancialDataProvider,
) -> None:
    history = await mock_provider.get_price_history("TEST")
    assert history.meta.is_mock is True


@pytest.mark.asyncio
async def test_mock_price_history_deterministic(
    mock_provider: MockFinancialDataProvider,
) -> None:
    h1 = await mock_provider.get_price_history("TEST")
    h2 = await mock_provider.get_price_history("TEST")
    assert len(h1.price_points) == len(h2.price_points)
    assert h1.price_points[0].close == h2.price_points[0].close


@pytest.mark.asyncio
async def test_mock_price_history_price_points_have_date_and_close(
    mock_provider: MockFinancialDataProvider,
) -> None:
    history = await mock_provider.get_price_history("TEST")
    for pt in history.price_points:
        assert pt.date
        assert pt.close > 0


@pytest.mark.asyncio
async def test_mock_price_history_currency(
    mock_provider: MockFinancialDataProvider,
) -> None:
    history = await mock_provider.get_price_history("TEST")
    assert history.currency == "NOK"


# ---------------------------------------------------------------------------
# Mock provider — fundamentals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_fundamentals_returns_data(
    mock_provider: MockFinancialDataProvider,
) -> None:
    fundamentals = await mock_provider.get_fundamentals("TEST")
    assert isinstance(fundamentals, FundamentalsData)


@pytest.mark.asyncio
async def test_mock_fundamentals_has_datapoints(
    mock_provider: MockFinancialDataProvider,
) -> None:
    fundamentals = await mock_provider.get_fundamentals("TEST")
    assert len(fundamentals.datapoints) > 0


@pytest.mark.asyncio
async def test_mock_fundamentals_datapoints_marked_mock(
    mock_provider: MockFinancialDataProvider,
) -> None:
    fundamentals = await mock_provider.get_fundamentals("TEST")
    for dp in fundamentals.datapoints:
        assert dp.note is not None
        assert "MOCK" in dp.note.upper()


@pytest.mark.asyncio
async def test_mock_fundamentals_is_mock_meta(
    mock_provider: MockFinancialDataProvider,
) -> None:
    fundamentals = await mock_provider.get_fundamentals("TEST")
    assert fundamentals.meta.is_mock is True


# ---------------------------------------------------------------------------
# Provider response metadata schema validation
# ---------------------------------------------------------------------------


def test_provider_response_metadata_schema() -> None:
    from datetime import datetime, timezone

    meta = ProviderResponseMetadata(
        provider_name="mock",
        source_tier=SourceTier.T6_model_estimate,
        retrieved_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        is_mock=True,
        status=ProviderStatus.ok,
        note="test",
    )
    assert meta.provider_name == "mock"
    assert meta.is_mock is True


# ---------------------------------------------------------------------------
# Source tier metadata correctness
# ---------------------------------------------------------------------------


def test_sec_edgar_source_tier() -> None:
    provider = SecEdgarProvider()
    assert provider.source_tier == SourceTier.T2_regulator_or_gov


def test_gleif_source_tier() -> None:
    provider = GleifProvider()
    assert provider.source_tier == SourceTier.T2_regulator_or_gov


def test_stooq_source_tier() -> None:
    provider = StooqProvider()
    assert provider.source_tier == SourceTier.T5_api_aggregator


def test_openbb_source_tier() -> None:
    provider = OpenBBProvider()
    assert provider.source_tier == SourceTier.T5_api_aggregator


def test_eodhd_source_tier() -> None:
    provider = EodhdProvider()
    assert provider.source_tier == SourceTier.T5_api_aggregator


# ---------------------------------------------------------------------------
# EODHD — does not make network calls
# ---------------------------------------------------------------------------


def test_eodhd_provider_status_not_configured_when_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    provider = EodhdProvider()
    assert provider.get_provider_status() == ProviderStatus.not_configured


@pytest.mark.asyncio
async def test_eodhd_get_company_profile_raises_not_implemented() -> None:
    provider = EodhdProvider()
    with pytest.raises(NotImplementedError):
        await provider.get_company_profile("TEST")


@pytest.mark.asyncio
async def test_eodhd_get_price_history_raises_not_implemented() -> None:
    provider = EodhdProvider()
    with pytest.raises(NotImplementedError):
        await provider.get_price_history("TEST")


@pytest.mark.asyncio
async def test_eodhd_get_fundamentals_raises_not_implemented() -> None:
    provider = EodhdProvider()
    with pytest.raises(NotImplementedError):
        await provider.get_fundamentals("TEST")


# ---------------------------------------------------------------------------
# Free provider skeletons — status and no network
# ---------------------------------------------------------------------------


def test_sec_edgar_status_ok() -> None:
    # Phase 5: SecEdgarProvider is now implemented (CIK submissions client).
    assert SecEdgarProvider().get_provider_status() == ProviderStatus.ok


def test_stooq_status_ok() -> None:
    # Phase 5: StooqProvider is now implemented (live CSV fetch).
    assert StooqProvider().get_provider_status() == ProviderStatus.ok


def test_openbb_status_not_implemented() -> None:
    assert OpenBBProvider().get_provider_status() == ProviderStatus.not_implemented


def test_gleif_status_ok() -> None:
    # Phase 5: GleifProvider is now implemented (live LEI lookup).
    assert GleifProvider().get_provider_status() == ProviderStatus.ok


@pytest.mark.asyncio
async def test_sec_edgar_raises_not_implemented_company_profile() -> None:
    with pytest.raises(NotImplementedError):
        await SecEdgarProvider().get_company_profile("TEST")


@pytest.mark.asyncio
async def test_stooq_get_company_profile_raises_not_implemented() -> None:
    # Phase 5: price_history is now implemented; company_profile is still unsupported.
    with pytest.raises(NotImplementedError):
        await StooqProvider().get_company_profile("TEST")


@pytest.mark.asyncio
async def test_openbb_raises_not_implemented_fundamentals() -> None:
    with pytest.raises(NotImplementedError):
        await OpenBBProvider().get_fundamentals("TEST")


@pytest.mark.asyncio
async def test_gleif_get_price_history_raises_not_implemented() -> None:
    # Phase 5: GLEIF entity lookup is implemented; price_history is not applicable.
    with pytest.raises(NotImplementedError):
        await GleifProvider().get_price_history("TEST")


# ---------------------------------------------------------------------------
# FinancialDataService
# ---------------------------------------------------------------------------


def test_service_default_provider_is_mock(service: FinancialDataService) -> None:
    assert service.provider.provider_name == "mock"


def test_service_capabilities_from_mock(service: FinancialDataService) -> None:
    caps = service.get_capabilities()
    assert ProviderCapability.company_profile in caps


def test_service_status_from_mock(service: FinancialDataService) -> None:
    assert service.get_status() == ProviderStatus.ok


def test_service_list_providers_returns_list(service: FinancialDataService) -> None:
    result = service.list_providers()
    assert isinstance(result, list)
    assert len(result) >= 6


def test_service_list_providers_mock_is_ok(service: FinancialDataService) -> None:
    result = service.list_providers()
    mock_entry = next(p for p in result if p["name"] == "mock")
    assert mock_entry["status"] == "ok"


def test_service_eodhd_entry_is_t5(service: FinancialDataService) -> None:
    result = service.list_providers()
    eodhd_entry = next(p for p in result if p["name"] == "eodhd")
    assert eodhd_entry["source_tier"] == "T5_api_aggregator"


@pytest.mark.asyncio
async def test_service_get_company_profile(service: FinancialDataService) -> None:
    profile = await service.get_company_profile("VOW3", exchange="XETRA")
    assert profile.ticker == "VOW3"
    assert profile.meta.is_mock is True


@pytest.mark.asyncio
async def test_service_get_price_history(service: FinancialDataService) -> None:
    history = await service.get_price_history("VOW3")
    assert len(history.price_points) > 0
    assert history.meta.is_mock is True


# ---------------------------------------------------------------------------
# Provider is abstract — cannot be instantiated directly
# ---------------------------------------------------------------------------


def test_financial_data_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        FinancialDataProvider()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# API endpoints — smoke tests (using mock app client)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_list_providers(client) -> None:
    resp = await client.get("/api/v1/financial-data/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    names = {p["name"] for p in body}
    assert "mock" in names
    assert "eodhd" in names


@pytest.mark.asyncio
async def test_api_mock_company_profile(client) -> None:
    resp = await client.get("/api/v1/financial-data/mock/company/TEST")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "TEST"
    assert body["meta"]["is_mock"] is True


@pytest.mark.asyncio
async def test_api_mock_company_profile_with_exchange(client) -> None:
    resp = await client.get(
        "/api/v1/financial-data/mock/company/TEST", params={"exchange": "XETRA"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exchange"] == "XETRA"


@pytest.mark.asyncio
async def test_api_mock_price_history(client) -> None:
    resp = await client.get("/api/v1/financial-data/mock/prices/TEST")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "TEST"
    assert isinstance(body["price_points"], list)
    assert len(body["price_points"]) > 0
    assert body["meta"]["is_mock"] is True


@pytest.mark.asyncio
async def test_api_mock_price_history_points_have_close(client) -> None:
    resp = await client.get("/api/v1/financial-data/mock/prices/ACME")
    assert resp.status_code == 200
    for pt in resp.json()["price_points"]:
        assert "close" in pt
        assert pt["close"] > 0


@pytest.mark.asyncio
async def test_api_providers_returns_source_tier(client) -> None:
    resp = await client.get("/api/v1/financial-data/providers")
    assert resp.status_code == 200
    for provider in resp.json():
        assert "source_tier" in provider


@pytest.mark.asyncio
async def test_api_providers_eodhd_not_configured(client) -> None:
    resp = await client.get("/api/v1/financial-data/providers")
    assert resp.status_code == 200
    providers = resp.json()
    eodhd = next(p for p in providers if p["name"] == "eodhd")
    assert eodhd["status"] in ("not_configured", "not_implemented")
