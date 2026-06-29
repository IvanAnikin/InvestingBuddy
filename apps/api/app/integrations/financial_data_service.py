"""
FinancialDataService — provider registry and selector.

Reads FINANCIAL_DATA_PROVIDER from config (default: "mock").
Exposes provider capabilities, company profile, and price history retrieval.
Fails safely with a ValueError when an unknown provider name is requested.

No real API key is required unless FINANCIAL_DATA_PROVIDER is set to a live
provider (e.g. "eodhd"). Tests always use the default "mock" provider.
"""

from __future__ import annotations

from app.core.config import settings
from app.integrations.financial_data_provider import (
    CompanyProfileData,
    FinancialDataProvider,
    PriceHistoryData,
    ProviderCapability,
    ProviderStatus,
)
from app.integrations.providers.eodhd_provider import EodhdProvider
from app.integrations.providers.gleif_provider import GleifProvider
from app.integrations.providers.mock_provider import MockFinancialDataProvider
from app.integrations.providers.openbb_provider import OpenBBProvider
from app.integrations.providers.sec_edgar_provider import SecEdgarProvider
from app.integrations.providers.stooq_provider import StooqProvider

_REGISTRY: dict[str, type[FinancialDataProvider]] = {
    "mock": MockFinancialDataProvider,
    "eodhd": EodhdProvider,
    "sec_edgar": SecEdgarProvider,
    "stooq": StooqProvider,
    "openbb": OpenBBProvider,
    "gleif": GleifProvider,
}


def get_provider(name: str | None = None) -> FinancialDataProvider:
    """
    Resolve a provider by name. Defaults to FINANCIAL_DATA_PROVIDER config value.
    Raises ValueError for unknown provider names.
    """
    provider_name = name or settings.financial_data_provider
    cls = _REGISTRY.get(provider_name)
    if cls is None:
        known = ", ".join(sorted(_REGISTRY.keys()))
        raise ValueError(
            f"Unknown financial data provider: '{provider_name}'. "
            f"Known providers: {known}"
        )
    return cls()


class FinancialDataService:
    """
    Service layer over the provider registry.

    Instantiated per-request or as a singleton; the provider is resolved
    at construction time based on config. Use provider_name="mock" in tests.
    """

    def __init__(self, provider_name: str | None = None) -> None:
        self._provider = get_provider(provider_name)

    @property
    def provider(self) -> FinancialDataProvider:
        return self._provider

    def list_providers(self) -> list[dict]:
        """Return metadata for all registered providers (no network calls)."""
        result = []
        for name, cls in _REGISTRY.items():
            instance = cls()
            result.append(
                {
                    "name": name,
                    "source_tier": instance.source_tier.value,
                    "capabilities": [c.value for c in instance.get_supported_capabilities()],
                    "status": instance.get_provider_status().value,
                }
            )
        return result

    def get_capabilities(self) -> list[ProviderCapability]:
        return self._provider.get_supported_capabilities()

    def get_status(self) -> ProviderStatus:
        return self._provider.get_provider_status()

    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        return await self._provider.get_company_profile(ticker, exchange)

    async def get_price_history(
        self,
        ticker: str,
        exchange: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> PriceHistoryData:
        return await self._provider.get_price_history(
            ticker, exchange, start_date, end_date
        )
