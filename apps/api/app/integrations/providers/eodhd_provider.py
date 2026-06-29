"""
EodhdProvider — placeholder for EODHD Fundamentals Data Feed.

Source tier: T5_api_aggregator

EODHD is a paid structured data aggregator. It aggregates fundamentals from
underlying sources (many of which are T1/T2 filings) but the tier stays T5
unless the agent independently verified the underlying filing.
See eodhd_mapping.json for field-level source guidance.

This placeholder makes NO live network calls. Attempting to call any method
raises NotImplementedError until the implementation is wired and a valid
EODHD_API_KEY is configured.

API key required: Yes — EODHD_API_KEY environment variable.
The key must NEVER be hardcoded. It is loaded from env / Azure Key Vault only.
Tests must NEVER require a real EODHD_API_KEY (use MockFinancialDataProvider).

Provider mapping reference:
  packages/research-contracts/real_asset_equity/v1/eodhd_mapping.json
"""

from __future__ import annotations

import os

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    FinancialDataProvider,
    FundamentalsData,
    PriceHistoryData,
    ProviderCapability,
    ProviderStatus,
    SourceTier,
)

_EODHD_BASE_URL = os.environ.get("EODHD_BASE_URL", "https://eodhd.com/api")


class EodhdProvider(FinancialDataProvider):
    """
    EODHD structured data aggregator — placeholder only.

    Source tier: T5_api_aggregator (always; see module docstring)
    Requires: EODHD_API_KEY environment variable (not loaded here — deferred
              to implementation phase to keep imports safe for tests)
    Provider mapping: packages/research-contracts/real_asset_equity/v1/eodhd_mapping.json

    Supported capabilities (when implemented):
      company_profile, price_history, fundamentals, insider_transactions,
      news, screener
    """

    @property
    def provider_name(self) -> str:
        return "eodhd"

    @property
    def source_tier(self) -> SourceTier:
        return SourceTier.T5_api_aggregator

    def get_supported_capabilities(self) -> list[ProviderCapability]:
        return [
            ProviderCapability.company_profile,
            ProviderCapability.price_history,
            ProviderCapability.fundamentals,
            ProviderCapability.insider_transactions,
            ProviderCapability.news,
            ProviderCapability.screener,
        ]

    def get_provider_status(self) -> ProviderStatus:
        api_key = os.environ.get("EODHD_API_KEY", "")
        if not api_key:
            return ProviderStatus.not_configured
        return ProviderStatus.not_implemented

    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        raise NotImplementedError(
            "EodhdProvider.get_company_profile is not yet implemented. "
            f"Planned endpoint: {_EODHD_BASE_URL}/fundamentals/{{TICKER}}.{{EXCHANGE}} "
            "with fields from eodhd_mapping.json → identity section. "
            "Requires EODHD_API_KEY. Use MockFinancialDataProvider in tests."
        )

    async def get_price_history(
        self,
        ticker: str,
        exchange: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> PriceHistoryData:
        raise NotImplementedError(
            "EodhdProvider.get_price_history is not yet implemented. "
            f"Planned endpoint: {_EODHD_BASE_URL}/eod/{{TICKER}}.{{EXCHANGE}} "
            "Requires EODHD_API_KEY. Use MockFinancialDataProvider in tests."
        )

    async def get_fundamentals(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> FundamentalsData:
        raise NotImplementedError(
            "EodhdProvider.get_fundamentals is not yet implemented. "
            f"Planned endpoint: {_EODHD_BASE_URL}/fundamentals/{{TICKER}}.{{EXCHANGE}} "
            "See eodhd_mapping.json for field-level path guidance. "
            "Requires EODHD_API_KEY. Use MockFinancialDataProvider in tests."
        )
