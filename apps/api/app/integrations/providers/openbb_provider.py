"""
OpenBBProvider — skeleton for the OpenBB open-source multi-source aggregator.

Source tier: T5_api_aggregator (default; actual tier depends on underlying source)
OpenBB routes requests to multiple underlying providers (Yahoo, FMP, etc.).
The agent must record the actual underlying source tier, not T5, if it is known.

No live calls are made in this skeleton. All methods raise NotImplementedError
until openbb-platform is installed and configured.

API key required: Depends on underlying OpenBB provider; free tier available.
"""

from __future__ import annotations

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    FinancialDataProvider,
    FundamentalsData,
    PriceHistoryData,
    ProviderCapability,
    ProviderStatus,
    SourceTier,
)


class OpenBBProvider(FinancialDataProvider):
    """
    OpenBB Platform open-source multi-source aggregator.

    Source tier: T5_api_aggregator (default)
    Note: Actual tier depends on the OpenBB router's underlying provider.
          Record the real tier when the underlying source is known.
    Covers: prices, fundamentals, news (via many sub-providers)
    """

    @property
    def provider_name(self) -> str:
        return "openbb"

    @property
    def source_tier(self) -> SourceTier:
        return SourceTier.T5_api_aggregator

    def get_supported_capabilities(self) -> list[ProviderCapability]:
        return [
            ProviderCapability.company_profile,
            ProviderCapability.price_history,
            ProviderCapability.fundamentals,
            ProviderCapability.news,
        ]

    def get_provider_status(self) -> ProviderStatus:
        return ProviderStatus.not_implemented

    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        raise NotImplementedError(
            "OpenBBProvider.get_company_profile is not yet implemented. "
            "Planned: use openbb.equity.profile(). "
            "Use MockFinancialDataProvider in tests."
        )

    async def get_price_history(
        self,
        ticker: str,
        exchange: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> PriceHistoryData:
        raise NotImplementedError(
            "OpenBBProvider.get_price_history is not yet implemented. "
            "Planned: use openbb.equity.price.historical(). "
            "Use MockFinancialDataProvider in tests."
        )

    async def get_fundamentals(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> FundamentalsData:
        raise NotImplementedError(
            "OpenBBProvider.get_fundamentals is not yet implemented. "
            "Planned: use openbb.equity.fundamental.*. "
            "Use MockFinancialDataProvider in tests."
        )
