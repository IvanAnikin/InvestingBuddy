"""
FreeRealProvider — composite provider combining free data sources.

Implements FinancialDataProvider by delegating to:
  company_profile  → SecEdgarFundamentalsProvider (T2, ticker→CIK resolved)
  price_history    → StooqProvider (T5, free, no key)
  fundamentals     → SecEdgarFundamentalsProvider (T2, XBRL companyfacts)

For EODHD free-plan price + SEC fundamentals, use EodhdFreeRealProvider instead.

Source tiers:
  company_profile  T2_regulator_or_gov  (SEC EDGAR submissions)
  price_history    T5_api_aggregator    (Stooq)
  fundamentals     T2_regulator_or_gov  (SEC EDGAR XBRL)

No API key required for any call.
U.S.-listed companies only for SEC data; Stooq covers most global exchanges.

Registration key: "free_real"

CI rules:
  - Use MockFinancialDataProvider for all offline CI tests.
  - Live integration tests: @pytest.mark.integration with ENABLE_INTEGRATION_TESTS=true.
"""

from __future__ import annotations

from datetime import datetime, timezone

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
from app.integrations.providers.eodhd_price_only_provider import EodhdPriceOnlyProvider
from app.integrations.providers.sec_edgar_fundamentals import (
    SecEdgarFundamentalsProvider,
    SecExchangeNotSupportedError,
)
from app.integrations.providers.stooq_provider import StooqProvider
from app.services.exchange_registry import country_for_exchange

# ---------------------------------------------------------------------------
# Honest degradation for issuers SEC EDGAR cannot cover
# ---------------------------------------------------------------------------

# Machine-readable reasons carried in the data_coverage contract.
REASON_SEC_COVERED = "sec_covered"
REASON_EXPLICIT_CIK_MAPPING = "explicit_cik_mapping"
REASON_NON_US_NO_SEC_MAPPING = "non_us_exchange_no_sec_mapping"
REASON_TICKER_NOT_IN_SEC_INDEX = "ticker_not_in_sec_index"
REASON_PROVIDER_ERROR = "provider_error"

SOURCE_NOT_SOURCED = "not_sourced"

# ProviderStatus.not_implemented is reused rather than adding a not_supported
# member: the enum is consumed widely (financial_data_service, admin provider
# UI, tests) and a new member is a cross-cutting change not worth its risk.
# Machine-readable meaning lives in data_coverage instead.
_NOT_SOURCED_STATUS = ProviderStatus.not_implemented


def _not_sourced_note(exchange: str | None) -> str:
    """Wording for a degraded result. Must contain no forbidden language."""
    return (
        f"not_sourced: SEC EDGAR does not cover exchange '{exchange}'. "
        "Company identity and fundamentals require human research "
        "(requires_human_research)."
    )


def _not_sourced_profile(ticker: str, exchange: str | None) -> CompanyProfileData:
    """
    A profile that states what is missing instead of guessing.

    legal_name is the ticker, never a fabricated or SEC-derived name — using
    the SEC index here is exactly how BA.LSE became "THE BOEING COMPANY".
    country_domicile comes from the exchange registry, which is factual.
    """
    return CompanyProfileData(
        ticker=ticker,
        exchange=exchange,
        legal_name=ticker,
        country_domicile=country_for_exchange(exchange),
        sector=None,
        industry=None,
        website=None,
        isin=None,
        lei=None,
        data_quality=DataQuality.D_weak_or_stale,
        meta=ProviderResponseMetadata(
            provider_name="free_real_not_sourced",
            source_tier=SourceTier.T6_model_estimate,
            retrieved_at=datetime.now(timezone.utc),
            is_mock=False,
            status=_NOT_SOURCED_STATUS,
            note=_not_sourced_note(exchange),
        ),
    )


def _not_sourced_fundamentals(ticker: str, exchange: str | None) -> FundamentalsData:
    """Empty fundamentals — no datapoints beats another company's datapoints."""
    return FundamentalsData(
        ticker=ticker,
        exchange=exchange,
        datapoints=[],
        meta=ProviderResponseMetadata(
            provider_name="free_real_not_sourced",
            source_tier=SourceTier.T6_model_estimate,
            retrieved_at=datetime.now(timezone.utc),
            is_mock=False,
            status=_NOT_SOURCED_STATUS,
            note=_not_sourced_note(exchange),
        ),
    )


def build_data_coverage(
    exchange: str | None,
    *,
    profile_source: str,
    fundamentals_source: str,
    reason: str,
    price_source: str | None = None,
    has_explicit_cik_mapping: bool = False,
) -> dict[str, object]:
    """Build the data_coverage contract describing how sourced a company is."""
    from app.services.exchange_registry import is_sec_eligible

    return {
        "exchange": exchange,
        "sec_eligible": is_sec_eligible(exchange),
        "has_explicit_cik_mapping": has_explicit_cik_mapping,
        "profile_source": profile_source,
        "fundamentals_source": fundamentals_source,
        "price_source": price_source,
        "reason": reason,
        "requires_human_research": fundamentals_source == SOURCE_NOT_SOURCED,
    }


def _make_empty_price_data(ticker: str, exchange: str | None, note: str) -> PriceHistoryData:
    """Return an empty PriceHistoryData carrying fallback warnings in meta.note."""
    meta = ProviderResponseMetadata(
        provider_name="free_real_price_fallback",
        source_tier=SourceTier.T5_api_aggregator,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.error,
        note=note,
    )
    return PriceHistoryData(
        ticker=ticker,
        exchange=exchange,
        currency="USD",
        price_points=[],
        source_url=None,
        meta=meta,
        data_quality=DataQuality.D_weak_or_stale,
    )


class FreeRealProvider(FinancialDataProvider):
    """
    Composite free-data provider: Stooq prices + SEC EDGAR fundamentals.

    Suitable for U.S. equity research when no paid data subscriptions are available.
    - Price data: Stooq.com (T5, free, no API key)
    - Fundamentals: SEC EDGAR XBRL (T2, free, no API key, U.S. only)
    - Company profile: SEC EDGAR submissions (T2, free, no API key)

    For EODHD free-plan price data, use EodhdFreeRealProvider (key required).
    """

    def __init__(self) -> None:
        self._sec = SecEdgarFundamentalsProvider()
        self._stooq = StooqProvider()

    @property
    def provider_name(self) -> str:
        return "free_real"

    @property
    def source_tier(self) -> SourceTier:
        return SourceTier.T2_regulator_or_gov

    def get_supported_capabilities(self) -> list[ProviderCapability]:
        return [
            ProviderCapability.company_profile,
            ProviderCapability.price_history,
            ProviderCapability.fundamentals,
        ]

    def get_provider_status(self) -> ProviderStatus:
        return ProviderStatus.ok

    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        """
        Fetch company profile from SEC EDGAR submissions (T2).

        Resolves ticker → CIK automatically for U.S.-listed companies.

        Never raises for an unsupported exchange. workflows/company_analysis
        calls this unguarded, so a raise would abort the whole node and error
        out the candidate — one non-US name would fail an entire European
        thesis run. Degrade to an honest not_sourced profile instead.
        """
        try:
            return await self._sec.get_company_profile(ticker, exchange)
        except SecExchangeNotSupportedError:
            return _not_sourced_profile(ticker, exchange)

    async def get_price_history(
        self,
        ticker: str,
        exchange: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> PriceHistoryData:
        """
        Fetch OHLCV price history from Stooq.com (T5, free, no key).

        Non-blocking fallback chain:
          1. Try Stooq.
          2. If Stooq fails (network error, timeout, empty data, Azure block):
             add warning and try EODHD price-only if EODHD_API_KEY is configured.
          3. If both fail: return empty PriceHistoryData with warnings in meta.note.
             Callers must treat empty price_points as a non-fatal partial result.
        """
        fallback_warnings: list[str] = []

        # --- Attempt 1: Stooq ---
        try:
            result = await self._stooq.get_price_history(ticker, exchange, start_date, end_date)
            if result.price_points:
                return result
            fallback_warnings.append(
                f"Stooq returned 0 price points for {ticker}; "
                "falling back to EODHD price-only provider."
            )
        except Exception as stooq_exc:
            fallback_warnings.append(
                "Stooq price provider unavailable; falling back to EODHD price-only provider. "
                f"(Stooq error: {type(stooq_exc).__name__}: {str(stooq_exc)[:120]})"
            )

        # --- Attempt 2: EODHD price-only (requires EODHD_API_KEY) ---
        eodhd = EodhdPriceOnlyProvider()
        if eodhd.get_provider_status() == ProviderStatus.ok:
            try:
                result = await eodhd.get_price_history(ticker, exchange, start_date, end_date)
                # Prepend Stooq fallback warning to meta note so callers can surface it.
                existing_note = result.meta.note or ""
                combined = "; ".join(fallback_warnings)
                result.meta.note = f"{combined}; {existing_note}" if existing_note else combined
                return result
            except Exception as eodhd_exc:
                fallback_warnings.append(
                    "EODHD price-only fallback also failed: "
                    f"{type(eodhd_exc).__name__}: {str(eodhd_exc)[:120]}"
                )
        else:
            fallback_warnings.append(
                "EODHD_API_KEY not configured; EODHD price-only fallback skipped."
            )

        # --- Both failed: return empty with warnings (non-fatal) ---
        fallback_warnings.append(
            "No usable price history available; trend signals unavailable."
        )
        return _make_empty_price_data(ticker, exchange, "; ".join(fallback_warnings))

    async def get_fundamentals(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> FundamentalsData:
        """
        Fetch XBRL fundamentals from SEC EDGAR (T2, free, no key).

        U.S.-listed companies only. Resolves ticker → CIK automatically.
        Returns FundamentalsData with is_mock=False and source_tier=T2_regulator_or_gov.
        Missing concepts produce warnings in the metadata note rather than exceptions.

        An unsupported exchange yields empty fundamentals, not an exception and
        never another issuer's numbers.
        """
        try:
            return await self._sec.get_fundamentals(ticker, exchange)
        except SecExchangeNotSupportedError:
            return _not_sourced_fundamentals(ticker, exchange)


class EodhdFreeRealProvider(FinancialDataProvider):
    """
    Composite free-data provider: EODHD /eod prices + SEC EDGAR fundamentals.

    Requires EODHD_API_KEY (free plan is sufficient for /eod).
    SEC EDGAR is free and requires no key.

    Source tiers:
      price_history  T5_api_aggregator    (EODHD /eod)
      fundamentals   T2_regulator_or_gov  (SEC EDGAR XBRL)
      company_profile T2_regulator_or_gov (SEC EDGAR submissions)

    Registration key: "eodhd_free_real"
    """

    def __init__(self) -> None:
        from app.integrations.providers.eodhd_price_only_provider import EodhdPriceOnlyProvider
        self._eodhd = EodhdPriceOnlyProvider()
        self._sec = SecEdgarFundamentalsProvider()

    @property
    def provider_name(self) -> str:
        return "eodhd_free_real"

    @property
    def source_tier(self) -> SourceTier:
        return SourceTier.T2_regulator_or_gov

    def get_supported_capabilities(self) -> list[ProviderCapability]:
        return [
            ProviderCapability.company_profile,
            ProviderCapability.price_history,
            ProviderCapability.fundamentals,
        ]

    def get_provider_status(self) -> ProviderStatus:
        return self._eodhd.get_provider_status()

    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        """
        SEC EDGAR profile (T2). Falls back to EODHD stub if SEC resolution fails.

        SecExchangeNotSupportedError subclasses ValueError, so an unsupported
        exchange still takes the EODHD path — EODHD does cover non-US venues.
        If that path also fails we degrade to not_sourced rather than raising,
        because workflows/company_analysis calls this unguarded.
        """
        try:
            return await self._sec.get_company_profile(ticker, exchange)
        except (ValueError, NotImplementedError):
            try:
                return await self._eodhd.get_company_profile(ticker, exchange)
            except Exception:
                return _not_sourced_profile(ticker, exchange)

    async def get_price_history(
        self,
        ticker: str,
        exchange: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> PriceHistoryData:
        """EODHD /eod price history (T5, requires free EODHD_API_KEY)."""
        return await self._eodhd.get_price_history(ticker, exchange, start_date, end_date)

    async def get_fundamentals(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> FundamentalsData:
        """
        SEC EDGAR XBRL fundamentals (T2, free, no key).

        An unsupported exchange yields empty fundamentals, never another
        issuer's numbers.
        """
        try:
            return await self._sec.get_fundamentals(ticker, exchange)
        except SecExchangeNotSupportedError:
            return _not_sourced_fundamentals(ticker, exchange)
