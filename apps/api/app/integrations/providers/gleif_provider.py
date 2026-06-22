"""
GleifProvider — skeleton for the GLEIF LEI (Legal Entity Identifier) registry.

Source tier: T2_regulator_or_gov
GLEIF is the Global Legal Entity Identifier Foundation — a public-interest body
supervised by the Financial Stability Board. It provides the authoritative
registry for LEI codes used to identify legal entities cross-border.

Intended endpoint: https://api.gleif.org/api/v1/lei-records (free, no key)

No live calls are made in this skeleton. All methods raise NotImplementedError
until the implementation is wired in a future phase.

API key required: No (GLEIF is a free public API).
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


class GleifProvider(FinancialDataProvider):
    """
    GLEIF LEI registry — legal entity identity cross-reference.

    Source tier: T2_regulator_or_gov
    Covers: LEI lookup, legal name, jurisdiction, entity status
    Not suitable for: price history, fundamentals, filings
    """

    @property
    def provider_name(self) -> str:
        return "gleif"

    @property
    def source_tier(self) -> SourceTier:
        return SourceTier.T2_regulator_or_gov

    def get_supported_capabilities(self) -> list[ProviderCapability]:
        return [
            ProviderCapability.company_profile,
            ProviderCapability.lei_lookup,
        ]

    def get_provider_status(self) -> ProviderStatus:
        return ProviderStatus.not_implemented

    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        raise NotImplementedError(
            "GleifProvider.get_company_profile is not yet implemented. "
            "Planned: resolve ticker → LEI via gleif.org/api/v1/lei-records "
            "then return legal name, jurisdiction, entity status. "
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
            "GleifProvider does not provide price history. "
            "Use StooqProvider or MockFinancialDataProvider."
        )

    async def get_fundamentals(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> FundamentalsData:
        raise NotImplementedError(
            "GleifProvider does not provide fundamentals. "
            "Use EodhdProvider or MockFinancialDataProvider."
        )
