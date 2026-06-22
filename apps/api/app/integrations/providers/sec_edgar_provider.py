"""
SecEdgarProvider — skeleton for SEC EDGAR free JSON API (data.sec.gov).

Source tier: T2_regulator_or_gov
Intended endpoints:
  - https://data.sec.gov/submissions/CIK{cik}.json  (company submissions)
  - https://data.sec.gov/api/xbrl/companyfacts/{cik}.json (financials)

No live calls are made in this skeleton. All methods raise NotImplementedError
until the implementation is wired in a future phase.

API key required: No (SEC EDGAR is a free public API with rate limits).
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


class SecEdgarProvider(FinancialDataProvider):
    """
    SEC EDGAR free regulatory filing data.

    Source tier: T2_regulator_or_gov
    Covers: US-listed companies (10-K, 10-Q, 8-K, Form 4, XBRL financials)
    Not suitable for: non-US companies, price history
    """

    @property
    def provider_name(self) -> str:
        return "sec_edgar"

    @property
    def source_tier(self) -> SourceTier:
        return SourceTier.T2_regulator_or_gov

    def get_supported_capabilities(self) -> list[ProviderCapability]:
        return [
            ProviderCapability.company_profile,
            ProviderCapability.fundamentals,
            ProviderCapability.insider_transactions,
        ]

    def get_provider_status(self) -> ProviderStatus:
        return ProviderStatus.not_implemented

    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        raise NotImplementedError(
            "SecEdgarProvider.get_company_profile is not yet implemented. "
            "Planned: fetch from data.sec.gov/submissions/CIK{cik}.json. "
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
            "SecEdgarProvider does not provide price history. "
            "Use StooqProvider or MockFinancialDataProvider."
        )

    async def get_fundamentals(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> FundamentalsData:
        raise NotImplementedError(
            "SecEdgarProvider.get_fundamentals is not yet implemented. "
            "Planned: fetch XBRL company facts from "
            "data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json. "
            "Use MockFinancialDataProvider in tests."
        )
