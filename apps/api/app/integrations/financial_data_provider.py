"""
Abstract base class and typed output schemas for financial data providers.

Every provider output carries:
- provider_name: which provider produced the data
- source_tier: T1–T6 from the source taxonomy (docs/DATA_SOURCES.md)
- retrieved_at: UTC timestamp of retrieval
- currency: where the value is monetary
- source_url: direct URL or reference to the underlying source
- data_quality: A_verified | B_single_credible | C_inferred | D_weak_or_stale
- is_mock: True when data is synthetic demo data (MockFinancialDataProvider)

No real API keys are required to import or instantiate providers.
Only EodhdProvider and live network providers require credentials.

Source record integration (Phase 5):
  Use build_source_record(meta, source_url, title) to prepare a dict suitable
  for creating a Source database record from any provider response.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SourceTier(str, Enum):
    T1_primary_filing = "T1_primary_filing"
    T2_regulator_or_gov = "T2_regulator_or_gov"
    T3_industry_specialist = "T3_industry_specialist"
    T4_quality_media = "T4_quality_media"
    T5_api_aggregator = "T5_api_aggregator"
    T6_model_estimate = "T6_model_estimate"


class DataQuality(str, Enum):
    A_verified = "A_verified"
    B_single_credible = "B_single_credible"
    C_inferred = "C_inferred"
    D_weak_or_stale = "D_weak_or_stale"


class ProviderStatus(str, Enum):
    ok = "ok"
    not_configured = "not_configured"
    not_implemented = "not_implemented"
    error = "error"


class ProviderCapability(str, Enum):
    company_profile = "company_profile"
    price_history = "price_history"
    fundamentals = "fundamentals"
    insider_transactions = "insider_transactions"
    news = "news"
    lei_lookup = "lei_lookup"
    screener = "screener"


# ---------------------------------------------------------------------------
# Response metadata
# ---------------------------------------------------------------------------


class ProviderResponseMetadata(BaseModel):
    provider_name: str
    source_tier: SourceTier
    retrieved_at: datetime
    is_mock: bool = False
    status: ProviderStatus = ProviderStatus.ok
    note: str | None = None


# ---------------------------------------------------------------------------
# Company profile
# ---------------------------------------------------------------------------


class CompanyProfileData(BaseModel):
    ticker: str
    exchange: str | None = None
    legal_name: str
    country_domicile: str | None = None
    reporting_currency: str | None = None
    fiscal_year_end: str | None = None
    sector: str | None = None
    industry: str | None = None
    description: str | None = None
    website: str | None = None
    isin: str | None = None
    lei: str | None = None
    ipo_date: str | None = None
    source_url: str | None = None
    data_quality: DataQuality = DataQuality.B_single_credible
    meta: ProviderResponseMetadata = Field(...)

    model_config = ConfigDict(use_enum_values=True)


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------


class PricePoint(BaseModel):
    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    volume: int | None = None
    adjusted_close: float | None = None


class PriceHistoryData(BaseModel):
    ticker: str
    exchange: str | None = None
    currency: str
    price_points: list[PricePoint]
    source_url: str | None = None
    data_quality: DataQuality = DataQuality.B_single_credible
    meta: ProviderResponseMetadata = Field(...)

    model_config = ConfigDict(use_enum_values=True)


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------


class FundamentalDataPoint(BaseModel):
    """Single fundamental value wrapped in full provenance."""

    field_name: str
    value: Any
    unit: str | None = None
    as_of: str | None = None
    currency: str | None = None
    source_tier: SourceTier
    source_name: str
    source_url: str | None = None
    data_quality: DataQuality
    note: str | None = None

    model_config = ConfigDict(use_enum_values=True)


class FundamentalsData(BaseModel):
    ticker: str
    exchange: str | None = None
    datapoints: list[FundamentalDataPoint]
    meta: ProviderResponseMetadata = Field(...)


# ---------------------------------------------------------------------------
# Source record integration helpers (Phase 5)
# ---------------------------------------------------------------------------

# Maps source tier → source_type string used in the Source database table.
_TIER_TO_SOURCE_TYPE: dict[str, str] = {
    SourceTier.T1_primary_filing: "company_filing",
    SourceTier.T2_regulator_or_gov: "government_data",
    SourceTier.T3_industry_specialist: "industry_report",
    SourceTier.T4_quality_media: "news_article",
    SourceTier.T5_api_aggregator: "financial_data_api",
    SourceTier.T6_model_estimate: "model_estimate",
}

# Maps source tier → credibility score (0.0–1.0).
_TIER_TO_CREDIBILITY: dict[str, float] = {
    SourceTier.T1_primary_filing: 0.95,
    SourceTier.T2_regulator_or_gov: 0.90,
    SourceTier.T3_industry_specialist: 0.75,
    SourceTier.T4_quality_media: 0.65,
    SourceTier.T5_api_aggregator: 0.55,
    SourceTier.T6_model_estimate: 0.20,
}


class SourceRecordAttrs(BaseModel):
    """
    Prepared attributes for creating a Source database record from provider data.

    Call build_source_record() to populate this from a ProviderResponseMetadata.
    The caller is responsible for the actual DB write (source_service.create_source).
    """

    source_type: str
    title: str
    url: str | None
    publisher: str
    retrieved_at: datetime
    credibility_score: float
    provider_name: str
    source_tier: SourceTier
    data_quality: DataQuality


def build_source_record(
    meta: ProviderResponseMetadata,
    source_url: str | None = None,
    title: str | None = None,
    data_quality: DataQuality = DataQuality.B_single_credible,
) -> SourceRecordAttrs:
    """
    Prepare source record attributes from provider response metadata.

    These can be passed directly to source_service.create_source() after
    constructing a SourceCreate schema from the returned dict.
    """
    tier_value = meta.source_tier if isinstance(meta.source_tier, str) else meta.source_tier.value
    return SourceRecordAttrs(
        source_type=_TIER_TO_SOURCE_TYPE.get(tier_value, "financial_data_api"),
        title=title or f"Provider data from {meta.provider_name}",
        url=source_url,
        publisher=meta.provider_name,
        retrieved_at=meta.retrieved_at,
        credibility_score=_TIER_TO_CREDIBILITY.get(tier_value, 0.50),
        provider_name=meta.provider_name,
        source_tier=meta.source_tier,
        data_quality=data_quality,
    )


# ---------------------------------------------------------------------------
# Abstract provider interface
# ---------------------------------------------------------------------------


class FinancialDataProvider(ABC):
    """
    Abstract base for all financial data providers.

    Implementations must:
    - Never require real API keys to be imported or instantiated.
    - Set source_tier correctly per the DATA_SOURCES.md taxonomy.
    - Mark synthetic / demo data with is_mock=True.
    - Not make live network calls unless the concrete class explicitly
      documents that it does so and requires credentials.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier (e.g. 'mock', 'eodhd')."""

    @property
    @abstractmethod
    def source_tier(self) -> SourceTier:
        """Default source tier for data from this provider."""

    @abstractmethod
    def get_supported_capabilities(self) -> list[ProviderCapability]:
        """List capabilities this provider can serve."""

    @abstractmethod
    def get_provider_status(self) -> ProviderStatus:
        """Return current operational status without making a network call."""

    @abstractmethod
    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        """Fetch company profile / identity data."""

    @abstractmethod
    async def get_price_history(
        self,
        ticker: str,
        exchange: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> PriceHistoryData:
        """Fetch OHLCV price history."""

    @abstractmethod
    async def get_fundamentals(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> FundamentalsData:
        """Fetch fundamental financial data wrapped in datapoint envelopes."""
