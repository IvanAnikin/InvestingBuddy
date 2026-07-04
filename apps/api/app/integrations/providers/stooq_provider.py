"""
StooqProvider — live free historical price data from stooq.com.

Source tier: T5_api_aggregator
API key required: No (Stooq is a free service, no authentication).

Endpoint: https://stooq.com/q/d/l/?s={symbol}&d1={start_yyyymmdd}&d2={end_yyyymmdd}&i=d
Response: CSV with columns Date,Open,High,Low,Close,Volume

Ticker format: {ticker_lower}.{stooq_exchange_suffix}
  Examples: aapl.us, vow3.de, msft.us, kghm.pl

Live calls are made when get_price_history() is invoked directly.
In CI, tests must use MockFinancialDataProvider or parse fixture CSV via
_parse_stooq_csv() without making network calls.

Integration tests (live network) are marked @pytest.mark.integration
and only run when ENABLE_INTEGRATION_TESTS=true.
"""

from __future__ import annotations

import csv
import io
import re
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
    SourceRecordAttrs,
    SourceTier,
    build_source_record,
)

_STOOQ_BASE_URL = "https://stooq.com/q/d/l/"

# Maps common exchange identifiers to Stooq's exchange suffix.
# Stooq uses two-letter lowercase country codes in the ticker symbol.
_EXCHANGE_TO_STOOQ_SUFFIX: dict[str, str] = {
    "NASDAQ": "US",
    "NYSE": "US",
    "NYSEARCA": "US",
    "AMEX": "US",
    "US": "US",
    "XETRA": "DE",
    "FRA": "DE",
    "DE": "DE",
    "LSE": "UK",
    "LONDON": "UK",
    "UK": "UK",
    "TSX": "CA",
    "CA": "CA",
    "ASX": "AU",
    "AU": "AU",
    "JPX": "JP",
    "TSE": "JP",
    "JP": "JP",
    "HKEX": "HK",
    "HK": "HK",
    "WSE": "PL",
    "PL": "PL",
    "OSE": "NO",
    "NO": "NO",
    "STO": "SE",
    "SE": "SE",
}

_USER_AGENT = "InvestingBuddy-Research-Platform/1.0 (contact: research@investingbuddy.com)"


def _stooq_symbol(ticker: str, exchange: str | None) -> str:
    """Build a Stooq-format symbol: e.g. 'aapl.us', 'vow3.de'."""
    suffix = _EXCHANGE_TO_STOOQ_SUFFIX.get((exchange or "").upper(), "US")
    return f"{ticker.lower()}.{suffix.lower()}"


def _parse_stooq_csv(
    text: str,
    ticker: str,
    exchange: str | None,
    source_url: str,
) -> PriceHistoryData:
    """
    Parse a Stooq CSV response into PriceHistoryData.

    Raises ValueError if the CSV has no data rows (e.g. unknown ticker).
    This function is pure — no network calls — so it can be unit-tested
    directly with fixture content.
    """
    reader = csv.DictReader(io.StringIO(text.strip()))
    price_points: list[PricePoint] = []

    for row in reader:
        date_str = row.get("Date", "").strip()
        if not date_str or not re.match(r"\d{4}-\d{2}-\d{2}", date_str):
            continue

        close_str = row.get("Close", "").strip()
        if not close_str:
            continue

        try:
            close = float(close_str)
        except ValueError:
            continue

        def _float_or_none(val: str | None) -> float | None:
            if not val or not val.strip():
                return None
            try:
                return float(val.strip())
            except ValueError:
                return None

        def _int_or_none(val: str | None) -> int | None:
            if not val or not val.strip():
                return None
            try:
                return int(float(val.strip()))
            except ValueError:
                return None

        price_points.append(
            PricePoint(
                date=date_str,
                open=_float_or_none(row.get("Open")),
                high=_float_or_none(row.get("High")),
                low=_float_or_none(row.get("Low")),
                close=close,
                volume=_int_or_none(row.get("Volume")),
                adjusted_close=None,
            )
        )

    if not price_points:
        raise ValueError(
            f"Stooq returned no price data for ticker '{ticker}' "
            f"(exchange={exchange!r}). "
            "The ticker may not be available on Stooq. "
            "Check the symbol format: https://stooq.com"
        )

    meta = ProviderResponseMetadata(
        provider_name="stooq",
        source_tier=SourceTier.T5_api_aggregator,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
        note=f"Live data from stooq.com — {len(price_points)} trading days",
    )
    return PriceHistoryData(
        ticker=ticker.upper(),
        exchange=exchange,
        # Stooq CSV does not include currency; caller overrides when exchange is known
        currency="USD",
        price_points=price_points,
        source_url=source_url,
        data_quality=DataQuality.B_single_credible,
        meta=meta,
    )


class StooqProvider(FinancialDataProvider):
    """
    Stooq free historical OHLCV price data (live implementation).

    Source tier: T5_api_aggregator
    Covers: historical end-of-day prices for global exchanges
    Not suitable for: company profile, fundamentals, filings

    Live HTTP calls are made via httpx.AsyncClient.
    The User-Agent is set to identify the platform per Stooq's informal guidelines.

    For CI tests: use MockFinancialDataProvider.
    For offline parsing tests: call _parse_stooq_csv() with fixture CSV content.
    For live integration tests: mark with @pytest.mark.integration.
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
        return ProviderStatus.ok

    def build_source_record_for_prices(
        self, ticker: str, exchange: str | None
    ) -> SourceRecordAttrs:
        """Convenience method — returns prepared Source record attrs for a price fetch."""
        symbol = _stooq_symbol(ticker, exchange)
        source_url = f"{_STOOQ_BASE_URL}?s={symbol}&i=d"
        meta = ProviderResponseMetadata(
            provider_name=self.provider_name,
            source_tier=self.source_tier,
            retrieved_at=datetime.now(timezone.utc),
            is_mock=False,
            status=ProviderStatus.ok,
        )
        return build_source_record(
            meta=meta,
            source_url=source_url,
            title=f"Stooq price history — {ticker.upper()}",
            data_quality=DataQuality.B_single_credible,
        )

    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        raise NotImplementedError(
            "StooqProvider does not provide company profiles. "
            "Use SecEdgarProvider (T2) or GleifProvider (T2) for entity identity."
        )

    async def get_price_history(
        self,
        ticker: str,
        exchange: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> PriceHistoryData:
        """
        Fetch OHLCV price history from stooq.com (live network call).

        Args:
            ticker: Ticker symbol (e.g. 'AAPL', 'VOW3')
            exchange: Exchange code (e.g. 'NASDAQ', 'XETRA'). Used to build
                      the Stooq symbol suffix. Defaults to US if unknown.
            start_date: Start date as 'YYYY-MM-DD'. Defaults to 1 year ago.
            end_date: End date as 'YYYY-MM-DD'. Defaults to today.

        Returns:
            PriceHistoryData with is_mock=False and source_tier=T5_api_aggregator.

        Raises:
            ValueError: If no price data is returned (unknown ticker).
            httpx.HTTPError: On network or HTTP error from stooq.com.
        """
        symbol = _stooq_symbol(ticker, exchange)
        params: dict[str, str] = {"s": symbol, "i": "d"}
        if start_date:
            params["d1"] = start_date.replace("-", "")
        if end_date:
            params["d2"] = end_date.replace("-", "")

        source_url = f"{_STOOQ_BASE_URL}?s={symbol}&i=d"

        async with httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT},
            timeout=15.0,
            follow_redirects=True,
        ) as client:
            response = await client.get(_STOOQ_BASE_URL, params=params)
            response.raise_for_status()
            csv_text = response.text

        return _parse_stooq_csv(csv_text, ticker, exchange, source_url)

    async def get_fundamentals(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> FundamentalsData:
        raise NotImplementedError(
            "StooqProvider does not provide fundamentals. "
            "Use EodhdProvider or MockFinancialDataProvider."
        )
