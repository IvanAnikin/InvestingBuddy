"""
EodhdPriceOnlyProvider — EODHD free-plan compatible provider.

Uses only the /eod (end-of-day price) endpoint, which is available on EODHD
free API keys. The /fundamentals endpoint requires a paid EODHD subscription
and is intentionally NOT called here.

Source tier: T5_api_aggregator
API key:     Required (EODHD_API_KEY) — but free plan works for /eod
Plan limit:  Free plan allows /eod; /fundamentals returns HTTP 403

If EODHD_API_KEY is absent:
  get_provider_status() → not_configured
  get_price_history()   → raises EodhdPriceOnlyError

get_company_profile():
  Returns a minimal stub from DB identity (ticker + exchange only).
  Does NOT call EODHD /fundamentals.
  Always adds warning: "EODHD free plan price-only mode; fundamentals unavailable."

get_fundamentals():
  Always raises NotImplementedError — use SecEdgarFundamentalsProvider for US
  companies, or leave fundamentals absent with a warning.

Symbol format: same as EodhdProvider — "{TICKER}.{EXCHANGE_SUFFIX}" (e.g. AAPL.US).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    DataQuality,
    FinancialDataProvider,
    FundamentalsData,
    PriceHistoryData,
    PricePoint,
    ProviderCapability,
    ProviderResponseMetadata,
    ProviderStatus,
    SourceTier,
)

# Reuse exchange→suffix mapping from the full EODHD provider
from app.integrations.providers.eodhd_provider import (
    EodhdAuthError,
    EodhdNotFoundError,
    EodhdRateLimitError,
    _eodhd_symbol,
    _safe_float,
    _safe_int,
)

PRICE_ONLY_WARNING = (
    "EODHD free plan price-only mode; fundamentals unavailable. "
    "Use SecEdgarFundamentalsProvider for US company fundamental data."
)


class EodhdPriceOnlyError(Exception):
    """Raised when EodhdPriceOnlyProvider cannot fulfil a request."""


class EodhdPriceOnlyProvider(FinancialDataProvider):
    """
    EODHD provider restricted to price history (/eod endpoint only).

    Compatible with EODHD free API keys. Designed as the price leg of the
    free_real compound provider stack:
      - Price data: EodhdPriceOnlyProvider  (T5_api_aggregator)
      - Fundamentals: SecEdgarFundamentalsProvider (T2_regulator_or_gov)

    Source tier: T5_api_aggregator
    """

    _TIMEOUT = 30.0

    def __init__(self) -> None:
        self._api_key: str = os.environ.get("EODHD_API_KEY", "")
        self._base_url: str = os.environ.get(
            "EODHD_BASE_URL", "https://eodhd.com/api"
        ).rstrip("/")

    @property
    def provider_name(self) -> str:
        return "eodhd_price_only"

    @property
    def source_tier(self) -> SourceTier:
        return SourceTier.T5_api_aggregator

    def get_supported_capabilities(self) -> list[ProviderCapability]:
        return [ProviderCapability.price_history]

    def get_provider_status(self) -> ProviderStatus:
        if not self._api_key:
            return ProviderStatus.not_configured
        return ProviderStatus.ok

    def _require_key(self) -> None:
        if not self._api_key:
            raise EodhdPriceOnlyError(
                "EODHD_API_KEY is not configured. "
                "Set EODHD_API_KEY in the environment or Azure Key Vault."
            )

    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        """
        Return a minimal stub company profile without calling EODHD /fundamentals.

        This avoids the HTTP 403 that the free plan returns for /fundamentals.
        The stub carries ticker and exchange from the call arguments — enrich
        using DB company identity or SecEdgarProvider separately.
        """
        meta = ProviderResponseMetadata(
            provider_name=self.provider_name,
            source_tier=self.source_tier,
            retrieved_at=datetime.now(timezone.utc),
            is_mock=False,
            status=ProviderStatus.ok,
            note=PRICE_ONLY_WARNING,
        )
        return CompanyProfileData(
            ticker=ticker.upper(),
            exchange=exchange,
            legal_name=ticker.upper(),
            country_domicile=None,
            reporting_currency=None,
            fiscal_year_end=None,
            sector=None,
            industry=None,
            description=None,
            website=None,
            isin=None,
            ipo_date=None,
            source_url=(
                "https://eodhd.com/financial-apis/api-for-historical-data-and-volumes/"
                f"?s={_eodhd_symbol(ticker, exchange)}"
            ),
            data_quality=DataQuality.D_weak_or_stale,
            meta=meta,
        )

    async def get_price_history(
        self,
        ticker: str,
        exchange: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> PriceHistoryData:
        """
        Fetch OHLCV price history from the EODHD /eod endpoint.

        Works on EODHD free plan. Returns T5_api_aggregator price data with
        is_mock=False. Adds PRICE_ONLY_WARNING to the metadata note.
        """
        self._require_key()
        symbol = _eodhd_symbol(ticker, exchange)
        url = f"{self._base_url}/eod/{symbol}"
        params: dict[str, str] = {
            "api_token": self._api_key,
            "fmt": "json",
            "order": "a",
        }
        if start_date:
            params["from"] = start_date
        if end_date:
            params["to"] = end_date

        raw = await self._get_json(url, params=params)

        if not isinstance(raw, list):
            raise EodhdPriceOnlyError(
                f"EODHD /eod returned unexpected format for {symbol}: {type(raw)}"
            )

        price_points = [
            PricePoint(
                date=row["date"],
                open=_safe_float(row.get("open")),
                high=_safe_float(row.get("high")),
                low=_safe_float(row.get("low")),
                close=float(row["close"]),
                volume=_safe_int(row.get("volume")),
                adjusted_close=_safe_float(row.get("adjusted_close")),
            )
            for row in raw
            if "date" in row and "close" in row
        ]

        meta = ProviderResponseMetadata(
            provider_name=self.provider_name,
            source_tier=self.source_tier,
            retrieved_at=datetime.now(timezone.utc),
            is_mock=False,
            status=ProviderStatus.ok,
            note=PRICE_ONLY_WARNING,
        )

        return PriceHistoryData(
            ticker=ticker.upper(),
            exchange=exchange,
            currency="USD",
            price_points=price_points,
            source_url=f"{self._base_url}/eod/{symbol}",
            data_quality=(
                DataQuality.B_single_credible if price_points else DataQuality.D_weak_or_stale
            ),
            meta=meta,
        )

    async def get_fundamentals(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> FundamentalsData:
        raise NotImplementedError(
            "EodhdPriceOnlyProvider does not provide fundamentals — EODHD /fundamentals "
            "requires a paid subscription. Use SecEdgarFundamentalsProvider for U.S. "
            "companies (free, T2_regulator_or_gov), or upgrade to a paid EODHD plan."
        )

    async def _get_json(
        self,
        url: str,
        params: dict[str, str] | None = None,
    ) -> list | dict:
        async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
            try:
                response = await client.get(url, params=params)
            except httpx.ConnectError as exc:
                raise EodhdPriceOnlyError(
                    f"Cannot connect to EODHD API at {self._base_url}: {exc}"
                ) from exc
            except httpx.TimeoutException as exc:
                raise EodhdPriceOnlyError(
                    f"EODHD API timed out after {self._TIMEOUT}s: {exc}"
                ) from exc

        if response.status_code in (401, 403):
            raise EodhdAuthError(
                f"EODHD /eod returned HTTP {response.status_code}. "
                "Check EODHD_API_KEY — even free-plan keys should work for /eod."
            )
        if response.status_code == 404:
            raise EodhdNotFoundError(
                f"EODHD: symbol not found (HTTP 404) for {url}. "
                "Check ticker and exchange suffix format."
            )
        if response.status_code == 429:
            raise EodhdRateLimitError(
                "EODHD API rate limit exceeded (HTTP 429). Retry after a delay."
            )
        if response.status_code >= 400:
            raise EodhdPriceOnlyError(
                f"EODHD API error HTTP {response.status_code} for {url}: "
                f"{response.text[:200]}"
            )

        try:
            return response.json()
        except Exception as exc:
            raise EodhdPriceOnlyError(
                f"EODHD returned non-JSON response for {url}: {response.text[:200]}"
            ) from exc
