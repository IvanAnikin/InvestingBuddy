"""
StooqProvider — skeleton for Stooq free historical price data.

Source tier: T5_api_aggregator
Intended endpoint: https://stooq.com/q/d/l/?s={ticker}&i=d (CSV download)

No live calls are made in this skeleton. All methods raise NotImplementedError
until the implementation is wired in a future phase.

API key required: No (Stooq is a free service, no authentication).
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


class StooqProvider(FinancialDataProvider):
    """
    Stooq free historical OHLCV price data.

    Source tier: T5_api_aggregator
    Covers: historical end-of-day prices for global exchanges
    Not suitable for: company profile, fundamentals, filings
    """

    @property
    def provider_name(self) -> str:
        return "stooq"

    @property
    def source_tier(self) -> SourceTier:
        return SourceTier.T5_api_aggregator

    def get_supported_capabilities(self) -> list[ProviderCapability]:
        return [ProviderCapability.price_history]

    def get_provider_status(self) -> ProviderStatus:
        return ProviderStatus.not_implemented

    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        raise NotImplementedError(
            "StooqProvider does not provide company profiles. "
            "Use SecEdgarProvider or MockFinancialDataProvider."
        )

    async def get_price_history(
        self,
        ticker: str,
        exchange: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> PriceHistoryData:
        raise NotImplementedError(
            "StooqProvider.get_price_history is not yet implemented. "
            "Planned: fetch CSV from stooq.com/q/d/l/?s={ticker}&i=d "
            "and parse into PriceHistoryData. "
            "Use MockFinancialDataProvider in tests."
        )

    async def get_fundamentals(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> FundamentalsData:
        raise NotImplementedError(
            "StooqProvider does not provide fundamentals. "
            "Use EodhdProvider or MockFinancialDataProvider."
        )
