"""
EodhdProvider — real implementation of the EODHD Fundamentals Data Feed.

Source tier: T5_api_aggregator (always — see module docstring)

EODHD is a paid structured data aggregator. Even when EODHD data originates
from a T1/T2 filing (e.g. a 10-K via SEC EDGAR), the source tier stays T5
unless the agent independently verified the underlying filing.

See packages/research-contracts/real_asset_equity/v1/eodhd_mapping.json for
the field-level source guidance used when building FundamentalDataPoint wrappers.

Credentials:
  EODHD_API_KEY  — required for any live call; loaded from env / Azure Key Vault
  EODHD_BASE_URL — override the base URL (default: https://eodhd.com/api)

CI rules:
  - Tests MUST NOT require a real EODHD_API_KEY.
  - When EODHD_API_KEY is absent, get_provider_status() returns not_configured
    and all live methods raise EodhdProviderError.
  - Use MockFinancialDataProvider in all CI tests.
  - Live integration tests must be behind ENABLE_EODHD_INTEGRATION_TESTS=true.

Symbol format: EODHD uses "{TICKER}.{EXCHANGE}" (e.g. "AAPL.US", "VOW3.XETRA").
  When exchange is not provided, the provider tries the bare ticker first, then
  appends ".US" as a fallback for US equities.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    DataQuality,
    FinancialDataProvider,
    FundamentalDataPoint,
    FundamentalsData,
    PriceHistoryData,
    PricePoint,
    ProviderCapability,
    ProviderResponseMetadata,
    ProviderStatus,
    SourceTier,
)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EodhdProviderError(Exception):
    """Raised when EODHD returns an error or is not configured."""


class EodhdNotFoundError(EodhdProviderError):
    """Raised when a ticker/symbol is not found on EODHD."""


class EodhdRateLimitError(EodhdProviderError):
    """Raised when EODHD returns HTTP 429 (rate limit exceeded)."""


class EodhdAuthError(EodhdProviderError):
    """Raised when EODHD returns HTTP 401/403 (bad or missing API key)."""


# ---------------------------------------------------------------------------
# EODHD exchange suffix mapping
# ---------------------------------------------------------------------------

_EXCHANGE_TO_SUFFIX: dict[str, str] = {
    "NASDAQ": "US",
    "NYSE": "US",
    "AMEX": "US",
    "NYSEARCA": "US",
    "BATS": "US",
    "LSE": "LSE",
    "LONDON": "LSE",
    "XETRA": "XETRA",
    "FSX": "F",
    "FRA": "F",
    "PARIS": "PA",
    "EURONEXT": "PA",
    "AMSTERDAM": "AS",
    "AMS": "AS",
    "OSE": "OL",
    "OSLO": "OL",
    "STO": "ST",
    "STOCKHOLM": "ST",
    "HEL": "HE",
    "HELSINKI": "HE",
    "CPH": "CO",
    "COPENHAGEN": "CO",
    "VIE": "VI",
    "VIENNA": "VI",
    "TSX": "TO",
    "TORONTO": "TO",
    "TSXV": "V",
    "VENTURE": "V",
    "ASX": "AU",
    "AUSTRALIA": "AU",
    "HKG": "HK",
    "HONG_KONG": "HK",
    "TSE": "TO",
    "JAPAN": "T",
    "SGX": "SG",
    "JSE": "JSE",
    "NSE": "NSE",
    "BSE": "BSE",
    "SSE": "SS",
    "SZSE": "SZ",
    "US": "US",
}


def _eodhd_symbol(ticker: str, exchange: str | None) -> str:
    """Build the EODHD-format symbol string (TICKER.EXCHANGE_SUFFIX)."""
    if exchange is None:
        return ticker.upper()
    suffix = _EXCHANGE_TO_SUFFIX.get(exchange.upper(), exchange.upper())
    return f"{ticker.upper()}.{suffix}"


# ---------------------------------------------------------------------------
# EodhdProvider
# ---------------------------------------------------------------------------


class EodhdProvider(FinancialDataProvider):
    """
    Real EODHD financial data provider.

    Fetches company profile, price history, and fundamentals from the EODHD API.
    Requires EODHD_API_KEY in the environment. When the key is absent, all live
    methods raise EodhdProviderError (status: not_configured).

    Source tier: T5_api_aggregator (always — see module docstring)
    """

    # EODHD request timeout in seconds
    _TIMEOUT = 30.0

    def __init__(self) -> None:
        self._api_key: str = os.environ.get("EODHD_API_KEY", "")
        self._base_url: str = os.environ.get(
            "EODHD_BASE_URL", "https://eodhd.com/api"
        ).rstrip("/")

    # ── Abstract interface ───────────────────────────────────────────────────

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
        if not self._api_key:
            return ProviderStatus.not_configured
        return ProviderStatus.ok

    # ── Public methods ───────────────────────────────────────────────────────

    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        """
        Fetch company identity and profile from the EODHD /fundamentals endpoint.

        Maps to eodhd_mapping.json → identity.* fields (General.* section).
        Source tier: T5_api_aggregator.
        """
        self._require_key()
        symbol = _eodhd_symbol(ticker, exchange)
        url = f"{self._base_url}/fundamentals/{symbol}"
        data = await self._get_json(url, params={"api_token": self._api_key, "filter": "General"})

        general = data if "Code" in data else data.get("General", data)
        retrieved = datetime.now(timezone.utc)

        meta = ProviderResponseMetadata(
            provider_name=self.provider_name,
            source_tier=self.source_tier,
            retrieved_at=retrieved,
            is_mock=False,
            status=ProviderStatus.ok,
            note="EODHD fundamentals — T5_api_aggregator. Do not inflate tier.",
        )

        return CompanyProfileData(
            ticker=_safe_str(general.get("Code", ticker)),
            exchange=_safe_str(general.get("Exchange", exchange)),
            legal_name=_safe_str(general.get("Name", ticker)),
            country_domicile=_safe_str(general.get("CountryName")),
            reporting_currency=_safe_str(general.get("CurrencyCode")),
            fiscal_year_end=_safe_str(general.get("FiscalYearEnd")),
            sector=_safe_str(general.get("Sector")),
            industry=_safe_str(general.get("Industry")),
            description=_safe_str(general.get("Description")),
            website=_safe_str(general.get("WebURL")),
            isin=_safe_str(general.get("ISIN")),
            ipo_date=_safe_str(general.get("IPODate")),
            source_url=f"https://eodhd.com/financial-apis/api-for-historical-data-and-volumes/?s={symbol}",
            data_quality=DataQuality.B_single_credible,
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

        Source tier: T5_api_aggregator.
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
            raise EodhdProviderError(
                f"EODHD EOD endpoint returned unexpected format for {symbol}: {type(raw)}"
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

        retrieved = datetime.now(timezone.utc)
        meta = ProviderResponseMetadata(
            provider_name=self.provider_name,
            source_tier=self.source_tier,
            retrieved_at=retrieved,
            is_mock=False,
            status=ProviderStatus.ok,
        )

        return PriceHistoryData(
            ticker=ticker.upper(),
            exchange=exchange,
            currency="USD",  # overridden when profile currency is known
            price_points=price_points,
            source_url=f"{self._base_url}/eod/{symbol}",
            data_quality=DataQuality.B_single_credible if price_points else DataQuality.D_weak_or_stale, # noqa: E501
            meta=meta,
        )

    async def get_fundamentals(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> FundamentalsData:
        """
        Fetch full fundamentals from EODHD /fundamentals endpoint.

        Extracts: Highlights, Valuation, SharesStats, Technicals, and the most
        recent annual statements (Income_Statement, Balance_Sheet, Cash_Flow).
        Each value is wrapped in a FundamentalDataPoint envelope (T5, B_single_credible).

        Source tier: T5_api_aggregator — do not promote to T1/T2 even if EODHD
        sourced the data from a filing. See eodhd_mapping.json.
        """
        self._require_key()
        symbol = _eodhd_symbol(ticker, exchange)
        url = f"{self._base_url}/fundamentals/{symbol}"
        data = await self._get_json(url, params={"api_token": self._api_key})

        retrieved = datetime.now(timezone.utc)
        as_of_date = retrieved.strftime("%Y-%m-%d")

        meta = ProviderResponseMetadata(
            provider_name=self.provider_name,
            source_tier=self.source_tier,
            retrieved_at=retrieved,
            is_mock=False,
            status=ProviderStatus.ok,
            note="EODHD fundamentals — T5_api_aggregator. See eodhd_mapping.json for field provenance.", # noqa: E501
        )

        # Compute a deterministic hash of the raw payload for deduplication.
        raw_hash = hashlib.sha256(
            json.dumps(data, sort_keys=True, default=str).encode()
        ).hexdigest()

        datapoints: list[FundamentalDataPoint] = []
        source_name = f"EODHD fundamentals — {symbol}"
        source_url = f"https://eodhd.com/financial-apis/fundamental-api/?s={symbol}"

        def _dp(
            field_name: str,
            value: Any,
            unit: str | None = None,
            note: str | None = None,
            dq: DataQuality = DataQuality.B_single_credible,
        ) -> None:
            if value is None:
                return
            datapoints.append(
                FundamentalDataPoint(
                    field_name=field_name,
                    value=value,
                    unit=unit,
                    as_of=as_of_date,
                    source_tier=self.source_tier,
                    source_name=source_name,
                    source_url=source_url,
                    data_quality=dq,
                    note=note,
                )
            )

        general = data.get("General", {})
        highlights = data.get("Highlights", {})
        valuation = data.get("Valuation", {})
        shares = data.get("SharesStats", {})
        technicals = data.get("Technicals", {})
        financials = data.get("Financials", {})

        # ── General / identity ──────────────────────────────────────────────
        _dp("general.currency_code", _safe_str(general.get("CurrencyCode")))
        _dp("general.country_iso", _safe_str(general.get("CountryISO")))
        _dp("general.ipo_date", _safe_str(general.get("IPODate")))
        _dp("general.gic_sector", _safe_str(general.get("GicSector")))
        _dp("general.gic_group", _safe_str(general.get("GicGroup")))

        # ── Highlights ──────────────────────────────────────────────────────
        market_cap = _safe_float(highlights.get("MarketCapitalization"))
        market_cap_mln = _safe_float(highlights.get("MarketCapitalizationMln"))
        if market_cap_mln is None and market_cap is not None:
            market_cap_mln = round(market_cap / 1_000_000, 2)
        _dp("highlights.market_cap_mln", market_cap_mln, unit="USD_m",
            note="Native currency — convert to USD using dated FX. eodhd_mapping.json: snapshot_financials.market_cap_usd_m") # noqa: E501
        _dp("highlights.ebitda", _mln(_safe_float(highlights.get("EBITDA"))), unit="USD_m")
        _dp("highlights.pe_ratio", _safe_float(highlights.get("PERatio")), unit="x")
        _dp("highlights.peg_ratio", _safe_float(highlights.get("PEGRatio")), unit="x")
        _dp("highlights.earnings_share", _safe_float(highlights.get("EarningsShare")), unit="USD")
        _dp("highlights.profit_margin", _safe_float(highlights.get("ProfitMargin")), unit="%")
        _dp("highlights.operating_margin_ttm", _safe_float(highlights.get("OperatingMarginTTM")), unit="%") # noqa: E501
        _dp("highlights.return_on_assets_ttm", _safe_float(highlights.get("ReturnOnAssetsTTM")), unit="%") # noqa: E501
        _dp("highlights.return_on_equity_ttm", _safe_float(highlights.get("ReturnOnEquityTTM")), unit="%") # noqa: E501
        _dp("highlights.revenue_ttm_mln", _mln(_safe_float(highlights.get("RevenueTTM"))), unit="USD_m", # noqa: E501
            note="eodhd_mapping.json: snapshot_financials.revenue_ttm_usd_m")
        _dp("highlights.revenue_per_share_ttm", _safe_float(highlights.get("RevenuePerShareTTM")), unit="USD") # noqa: E501
        _dp("highlights.quarterly_revenue_growth_yoy", _safe_float(highlights.get("QuarterlyRevenueGrowthYOY")), unit="%") # noqa: E501
        _dp("highlights.gross_profit_ttm_mln", _mln(_safe_float(highlights.get("GrossProfitTTM"))), unit="USD_m") # noqa: E501
        _dp("highlights.diluted_eps_ttm", _safe_float(highlights.get("DilutedEpsTTM")), unit="USD")
        _dp("highlights.quarterly_earnings_growth_yoy", _safe_float(highlights.get("QuarterlyEarningsGrowthYOY")), unit="%") # noqa: E501
        _dp("highlights.most_recent_quarter", _safe_str(highlights.get("MostRecentQuarter")))

        # ── Valuation ───────────────────────────────────────────────────────
        _dp("valuation.trailing_pe", _safe_float(valuation.get("TrailingPE")), unit="x")
        _dp("valuation.forward_pe", _safe_float(valuation.get("ForwardPE")), unit="x")
        _dp("valuation.price_sales_ttm", _safe_float(valuation.get("PriceSalesTTM")), unit="x")
        _dp("valuation.price_book_mrq", _safe_float(valuation.get("PriceBookMRQ")), unit="x")
        _dp("valuation.enterprise_value_mln", _mln(_safe_float(valuation.get("EnterpriseValue"))), unit="USD_m", # noqa: E501
            note="eodhd_mapping.json: snapshot_financials.enterprise_value_usd_m")
        _dp("valuation.ev_revenue", _safe_float(valuation.get("EnterpriseValueRevenue")), unit="x")
        _dp("valuation.ev_ebitda", _safe_float(valuation.get("EnterpriseValueEbitda")), unit="x",
            note="eodhd_mapping.json: snapshot_financials.ev_ebitda_x")

        # ── Shares stats ────────────────────────────────────────────────────
        _dp("shares.outstanding_mln", _mln(_safe_float(shares.get("SharesOutstanding"))), unit="M shares", # noqa: E501
            note="eodhd_mapping.json: snapshot_financials.shares_out_m")
        _dp("shares.float_mln", _mln(_safe_float(shares.get("SharesFloat"))), unit="M shares")
        _dp("shares.percent_insiders", _safe_float(shares.get("PercentInsiders")), unit="%")
        _dp("shares.percent_institutions", _safe_float(shares.get("PercentInstitutions")), unit="%")
        _dp("shares.short_ratio", _safe_float(shares.get("ShortRatio")), unit="x")
        _dp("shares.short_percent_float", _safe_float(shares.get("ShortPercentFloat")), unit="%")

        # ── Technicals ──────────────────────────────────────────────────────
        _dp("technicals.beta", _safe_float(technicals.get("Beta")), unit="x")
        _dp("technicals.52_week_high", _safe_float(technicals.get("52WeekHigh")))
        _dp("technicals.52_week_low", _safe_float(technicals.get("52WeekLow")))
        _dp("technicals.50_day_ma", _safe_float(technicals.get("50DayMA")))
        _dp("technicals.200_day_ma", _safe_float(technicals.get("200DayMA")))

        # ── Annual financial statements (most recent year) ──────────────────
        self._extract_annual_statements(datapoints, financials, as_of_date, source_name, source_url)

        # Store raw payload hash as a non-financial datapoint for deduplication
        _dp("_meta.raw_payload_hash", raw_hash, note="SHA-256 hash of the full EODHD fundamentals response payload") # noqa: E501

        return FundamentalsData(
            ticker=ticker.upper(),
            exchange=exchange,
            datapoints=datapoints,
            meta=meta,
        )

    async def search_symbol(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search EODHD for a company by name or partial ticker.

        Returns a list of result dicts with keys: Code, Exchange, Name, Type, Country.
        Does NOT deduplicate — callers must handle ambiguous results.
        """
        self._require_key()
        url = f"{self._base_url}/search/{query}"
        raw = await self._get_json(url, params={"api_token": self._api_key, "limit": str(limit)})
        if not isinstance(raw, list):
            return []
        return raw

    async def get_fundamentals_raw(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> dict[str, Any]:
        """
        Fetch the full raw EODHD fundamentals response as a dict.

        Useful for diagnostic endpoints and snapshot persistence.
        """
        self._require_key()
        symbol = _eodhd_symbol(ticker, exchange)
        url = f"{self._base_url}/fundamentals/{symbol}"
        return await self._get_json(url, params={"api_token": self._api_key})

    # ── Private helpers ──────────────────────────────────────────────────────

    def _require_key(self) -> None:
        if not self._api_key:
            raise EodhdProviderError(
                "EODHD_API_KEY is not configured. "
                "Set EODHD_API_KEY in the environment or Azure Key Vault. "
                "Use MockFinancialDataProvider (FINANCIAL_DATA_PROVIDER=mock) in CI."
            )

    async def _get_json(
        self,
        url: str,
        params: dict[str, str] | None = None,
    ) -> Any:
        """Make an authenticated GET request to EODHD and return parsed JSON."""
        async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
            try:
                response = await client.get(url, params=params)
            except httpx.ConnectError as exc:
                raise EodhdProviderError(
                    f"Cannot connect to EODHD API at {self._base_url}: {exc}"
                ) from exc
            except httpx.TimeoutException as exc:
                raise EodhdProviderError(
                    f"EODHD API request timed out after {self._TIMEOUT}s: {exc}"
                ) from exc

        if response.status_code == 401 or response.status_code == 403:
            raise EodhdAuthError(
                f"EODHD API returned HTTP {response.status_code}: invalid or missing API key. "
                "Check EODHD_API_KEY."
            )
        if response.status_code == 404:
            raise EodhdNotFoundError(
                f"EODHD: symbol not found (HTTP 404) for URL {url}. "
                "Check ticker and exchange suffix format."
            )
        if response.status_code == 429:
            raise EodhdRateLimitError(
                "EODHD API rate limit exceeded (HTTP 429). Retry after a delay."
            )
        if response.status_code >= 400:
            raise EodhdProviderError(
                f"EODHD API error HTTP {response.status_code} for {url}: {response.text[:200]}"
            )

        try:
            return response.json()
        except Exception as exc:
            raise EodhdProviderError(
                f"EODHD returned non-JSON response for {url}: {response.text[:200]}"
            ) from exc

    def _extract_annual_statements(
        self,
        datapoints: list[FundamentalDataPoint],
        financials: dict,
        as_of_date: str,
        source_name: str,
        source_url: str,
    ) -> None:
        """Extract the most recent annual statement rows from Financials.*."""

        def _dp_stmt(
            field_name: str, value: Any, unit: str | None = None, note: str | None = None
        ) -> None:
            if value is None:
                return
            datapoints.append(
                FundamentalDataPoint(
                    field_name=field_name,
                    value=value,
                    unit=unit,
                    as_of=as_of_date,
                    source_tier=self.source_tier,
                    source_name=source_name,
                    source_url=source_url,
                    data_quality=DataQuality.B_single_credible,
                    note=note,
                )
            )

        # Income Statement
        income = financials.get("Income_Statement", {})
        income_yearly = income.get("yearly", {})
        if income_yearly:
            latest_key = sorted(income_yearly.keys())[-1]
            row = income_yearly[latest_key]
            stmt_date = _safe_str(row.get("date", latest_key))
            _dp_stmt(f"income_statement.{stmt_date}.total_revenue_mln",
                     _mln(_safe_float(row.get("totalRevenue"))), unit="USD_m",
                     note="eodhd_mapping.json: financials_deep.revenue_3y")
            _dp_stmt(f"income_statement.{stmt_date}.gross_profit_mln",
                     _mln(_safe_float(row.get("grossProfit"))), unit="USD_m")
            _dp_stmt(f"income_statement.{stmt_date}.operating_income_mln",
                     _mln(_safe_float(row.get("operatingIncome"))), unit="USD_m")
            _dp_stmt(f"income_statement.{stmt_date}.net_income_mln",
                     _mln(_safe_float(row.get("netIncome"))), unit="USD_m")
            _dp_stmt(f"income_statement.{stmt_date}.ebitda_mln",
                     _mln(_safe_float(row.get("ebitda"))), unit="USD_m")
            _dp_stmt(f"income_statement.{stmt_date}.eps",
                     _safe_float(row.get("eps")), unit="USD")
            _dp_stmt(f"income_statement.{stmt_date}.eps_diluted",
                     _safe_float(row.get("epsDiluted")), unit="USD")
            _dp_stmt(f"income_statement.{stmt_date}.r_and_d_mln",
                     _mln(_safe_float(row.get("researchDevelopment"))), unit="USD_m")

        # Balance Sheet
        balance = financials.get("Balance_Sheet", {})
        balance_yearly = balance.get("yearly", {})
        if balance_yearly:
            latest_key = sorted(balance_yearly.keys())[-1]
            row = balance_yearly[latest_key]
            stmt_date = _safe_str(row.get("date", latest_key))
            _dp_stmt(f"balance_sheet.{stmt_date}.total_assets_mln",
                     _mln(_safe_float(row.get("totalAssets"))), unit="USD_m")
            _dp_stmt(f"balance_sheet.{stmt_date}.total_current_assets_mln",
                     _mln(_safe_float(row.get("totalCurrentAssets"))), unit="USD_m")
            _dp_stmt(f"balance_sheet.{stmt_date}.cash_mln",
                     _mln(_safe_float(row.get("cash"))), unit="USD_m")
            _dp_stmt(f"balance_sheet.{stmt_date}.total_liabilities_mln",
                     _mln(_safe_float(row.get("totalLiabilities"))), unit="USD_m")
            _dp_stmt(f"balance_sheet.{stmt_date}.short_long_term_debt_mln",
                     _mln(_safe_float(row.get("shortLongTermDebtTotal"))), unit="USD_m",
                     note="eodhd_mapping.json: financials_deep.debt_structure.total_debt_usd_m")
            _dp_stmt(f"balance_sheet.{stmt_date}.long_term_debt_mln",
                     _mln(_safe_float(row.get("longTermDebtTotal"))), unit="USD_m")
            _dp_stmt(f"balance_sheet.{stmt_date}.total_shareholder_equity_mln",
                     _mln(_safe_float(row.get("totalShareholderEquity"))), unit="USD_m")
            _dp_stmt(f"balance_sheet.{stmt_date}.ppe_net_mln",
                     _mln(_safe_float(row.get("propertyPlantEquipment"))), unit="USD_m",
                     note="eodhd_mapping.json: real_asset_block.ppe_net_usd_m")
            _dp_stmt(f"balance_sheet.{stmt_date}.goodwill_mln",
                     _mln(_safe_float(row.get("goodWill"))), unit="USD_m")
            _dp_stmt(f"balance_sheet.{stmt_date}.intangible_assets_mln",
                     _mln(_safe_float(row.get("intangibleAssets"))), unit="USD_m")
            _dp_stmt(f"balance_sheet.{stmt_date}.inventory_mln",
                     _mln(_safe_float(row.get("inventory"))), unit="USD_m")

        # Cash Flow
        cashflow = financials.get("Cash_Flow", {})
        cf_yearly = cashflow.get("yearly", {})
        if cf_yearly:
            latest_key = sorted(cf_yearly.keys())[-1]
            row = cf_yearly[latest_key]
            stmt_date = _safe_str(row.get("date", latest_key))
            _dp_stmt(f"cash_flow.{stmt_date}.operating_cash_flow_mln",
                     _mln(_safe_float(row.get("totalCashFromOperatingActivities"))), unit="USD_m")
            _dp_stmt(f"cash_flow.{stmt_date}.capex_mln",
                     _mln(_safe_float(row.get("capitalExpenditures"))), unit="USD_m",
                     note="May be negative (outflow). FCF = CFO + capex.")
            _dp_stmt(f"cash_flow.{stmt_date}.free_cash_flow_mln",
                     _mln(_safe_float(row.get("freeCashFlow"))), unit="USD_m",
                     note="eodhd_mapping.json: snapshot_financials.fcf_ttm_usd_m")
            _dp_stmt(f"cash_flow.{stmt_date}.investing_cash_flow_mln",
                     _mln(_safe_float(row.get("totalCashFromInvestingActivities"))), unit="USD_m")
            _dp_stmt(f"cash_flow.{stmt_date}.financing_cash_flow_mln",
                     _mln(_safe_float(row.get("totalCashFromFinancingActivities"))), unit="USD_m")


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _safe_str(val: Any) -> str | None:
    if val is None or val == "":
        return None
    s = str(val).strip()
    return s if s else None


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if (f != f) else f  # reject NaN
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _mln(val: float | None) -> float | None:
    """Convert a raw unit value to millions, rounded to 2dp."""
    if val is None:
        return None
    return round(val / 1_000_000, 2)
