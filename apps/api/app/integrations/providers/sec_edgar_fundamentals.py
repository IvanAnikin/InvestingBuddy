"""
SecEdgarFundamentalsProvider — XBRL fundamentals + ticker→CIK resolution.

Extends SecEdgarProvider with:
  1. Ticker→CIK resolution via the SEC public company_tickers.json index.
  2. XBRL companyfacts fundamentals from data.sec.gov/api/xbrl/companyfacts/.

Source tier: T2_regulator_or_gov
API key:     None (SEC EDGAR is a free public API).
Rate limit:  10 requests/second; User-Agent header required.

Endpoints used:
  Ticker index:   https://www.sec.gov/files/company_tickers.json
  Company facts:  https://data.sec.gov/api/xbrl/companyfacts/CIK{padded_cik}.json

us-gaap concepts mapped (annual 10-K / 20-F entries only):
  Revenue          → Revenues | RevenueFromContractWithCustomerExcludingAssessedTax
  Net income       → NetIncomeLoss
  EPS basic        → EarningsPerShareBasic
  EPS diluted      → EarningsPerShareDiluted
  Total assets     → Assets
  Total liabilities→ Liabilities
  Shareholders eq. → StockholdersEquity
                   | StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest
  Operating CF     → NetCashProvidedByUsedInOperatingActivities
  Long-term debt   → LongTermDebt
  Short-term debt  → DebtCurrent

Data quality: B_single_credible (single T2 source, uncontested).
Missing concepts produce warnings, not failures.

CI rules:
  - Tests MUST NOT make network calls.
  - Pass fixture dicts directly to parse_company_facts() for offline tests.
  - Live calls are marked @pytest.mark.integration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    DataQuality,
    FundamentalDataPoint,
    FundamentalsData,
    ProviderResponseMetadata,
    ProviderStatus,
    SourceTier,
)
from app.integrations.providers.sec_edgar_provider import (
    _EDGAR_BASE_URL,
    SecEdgarProvider,
    _pad_cik,
)

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_COMPANY_FACTS_URL = f"{_EDGAR_BASE_URL}/api/xbrl/companyfacts/CIK{{cik}}.json"
_USER_AGENT = "InvestingBuddy-Research-Platform/1.0 (contact: research@investingbuddy.com)"

# Annual form types to accept when selecting the most recent filing value
_ANNUAL_FORMS = {"10-K", "20-F", "10-K/A", "20-F/A"}

# Ordered list of us-gaap concept aliases per financial line item.
# The first concept found in the facts payload wins.
_CONCEPT_MAP: dict[str, list[str]] = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps_basic": ["EarningsPerShareBasic"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "shareholders_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "long_term_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "short_term_debt": ["DebtCurrent", "ShortTermBorrowings"],
}

_UNITS_FOR_CONCEPT: dict[str, str] = {
    "revenue": "USD_m",
    "net_income": "USD_m",
    "eps_basic": "USD",
    "eps_diluted": "USD",
    "total_assets": "USD_m",
    "total_liabilities": "USD_m",
    "shareholders_equity": "USD_m",
    "operating_cash_flow": "USD_m",
    "long_term_debt": "USD_m",
    "short_term_debt": "USD_m",
}

# Concepts whose raw values are in USD (not per-share) — divide by 1M for display
_DOLLAR_CONCEPTS = {
    "revenue", "net_income", "total_assets", "total_liabilities",
    "shareholders_equity", "operating_cash_flow", "long_term_debt", "short_term_debt",
}


def _pick_most_recent_annual(entries: list[dict]) -> dict | None:
    """
    Select the most recent annual filing entry from a list of XBRL fact entries.

    Filters to 10-K / 20-F forms, then picks the entry with the latest 'end' date.
    Returns None if no annual entries exist.
    """
    annual = [e for e in entries if e.get("form", "") in _ANNUAL_FORMS]
    if not annual:
        return None
    return max(annual, key=lambda e: e.get("end", ""))


def parse_company_facts(
    data: dict,
    ticker: str,
    cik: str,
) -> tuple[list[FundamentalDataPoint], list[str]]:
    """
    Parse a SEC EDGAR companyfacts JSON payload into FundamentalDataPoints.

    This function is pure (no network calls) so it can be unit-tested with
    fixture JSON content.

    Returns:
        (datapoints, warnings) — warnings list non-empty when concepts are absent.
    """
    warnings: list[str] = []
    datapoints: list[FundamentalDataPoint] = []

    source_url = _COMPANY_FACTS_URL.format(cik=_pad_cik(cik))
    as_of_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    source_name = f"SEC EDGAR XBRL companyfacts — {ticker.upper()} (CIK {cik})"

    us_gaap: dict[str, Any] = data.get("facts", {}).get("us-gaap", {})

    for field_name, concept_list in _CONCEPT_MAP.items():
        found = False
        for concept in concept_list:
            concept_data = us_gaap.get(concept)
            if not concept_data:
                continue

            # Most financial facts are in USD; EPS is in USD/shares
            units_key = "USD" if field_name not in ("eps_basic", "eps_diluted") else "USD/shares"
            entries = concept_data.get("units", {}).get(units_key, [])
            if not entries:
                # Try bare USD if USD/shares not found (some filers use USD for EPS)
                entries = concept_data.get("units", {}).get("USD", [])
            if not entries:
                continue

            best = _pick_most_recent_annual(entries)
            if best is None:
                continue

            raw_val = best.get("val")
            if raw_val is None:
                continue

            filing_date = best.get("filed", as_of_date)
            period_end = best.get("end", as_of_date)
            unit_str = _UNITS_FOR_CONCEPT.get(field_name)

            # Scale dollar amounts to millions
            if field_name in _DOLLAR_CONCEPTS:
                display_val = round(float(raw_val) / 1_000_000, 2)
            else:
                display_val = float(raw_val)

            datapoints.append(
                FundamentalDataPoint(
                    field_name=f"sec_edgar.{field_name}",
                    value=display_val,
                    unit=unit_str,
                    as_of=period_end,
                    source_tier=SourceTier.T2_regulator_or_gov,
                    source_name=source_name,
                    source_url=source_url,
                    data_quality=DataQuality.B_single_credible,
                    note=(
                        f"SEC EDGAR XBRL — concept: {concept}, "
                        f"form: {best.get('form', '?')}, "
                        f"filed: {filing_date}, "
                        f"period end: {period_end}. "
                        "Source tier T2_regulator_or_gov."
                    ),
                )
            )
            found = True
            break  # first matching concept wins

        if not found:
            warnings.append(
                f"SEC EDGAR: '{field_name}' not found in us-gaap facts "
                f"for {ticker.upper()} (tried: {', '.join(concept_list)}). "
                "Data may be absent or filed under a different taxonomy."
            )

    return datapoints, warnings


class SecEdgarFundamentalsProvider(SecEdgarProvider):
    """
    SEC EDGAR provider with XBRL fundamentals and ticker→CIK resolution.

    Source tier: T2_regulator_or_gov
    No API key required.

    Primary methods:
      resolve_cik(ticker)           → CIK string (cached after first call)
      get_fundamentals(ticker)      → FundamentalsData from XBRL companyfacts
      get_company_profile(ticker)   → CompanyProfileData (CIK or ticker → submissions)

    CIK cache is instance-level (per-request in async context).
    """

    def __init__(self) -> None:
        self._cik_cache: dict[str, str] = {}

    @property
    def provider_name(self) -> str:
        return "sec_edgar_fundamentals"

    async def resolve_cik(self, ticker: str) -> str:
        """
        Resolve a US stock ticker to a SEC CIK using the public company_tickers.json index.

        Performs a case-insensitive search. Caches results for the lifetime of this
        provider instance. Raises ValueError if ticker is not found.
        """
        upper = ticker.upper()
        if upper in self._cik_cache:
            return self._cik_cache[upper]

        async with httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT},
            timeout=15.0,
        ) as client:
            response = await client.get(_TICKERS_URL)
            response.raise_for_status()
            index: dict[str, dict] = response.json()

        # index keys are string ints; each value has "cik_str", "ticker", "title"
        for entry in index.values():
            if entry.get("ticker", "").upper() == upper:
                cik = str(entry["cik_str"])
                self._cik_cache[upper] = cik
                return cik

        raise ValueError(
            f"Ticker '{ticker}' not found in SEC EDGAR company ticker index. "
            "The company may not be SEC-registered, or the ticker may differ "
            "(e.g. BRK.B → BRK-B on SEC). "
            f"Check manually: https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}"
        )

    def _load_cik_index_sync(self, raw: dict) -> None:
        """Populate CIK cache from a pre-loaded index dict (used in tests)."""
        for entry in raw.values():
            ticker = entry.get("ticker", "").upper()
            if ticker:
                self._cik_cache[ticker] = str(entry["cik_str"])

    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        """
        Resolve company profile from SEC EDGAR.

        Accepts:
          - All-digit string → treated as CIK directly
          - Ticker string → resolved to CIK via company_tickers.json index
        """
        if ticker.strip().isdigit():
            return await self.get_company_by_cik(ticker.strip())
        cik = await self.resolve_cik(ticker)
        return await self.get_company_by_cik(cik)

    async def get_fundamentals(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> FundamentalsData:
        """
        Fetch XBRL fundamentals from SEC EDGAR companyfacts endpoint.

        Resolves ticker → CIK if needed, fetches companyfacts JSON, parses
        core us-gaap concepts into FundamentalDataPoint envelopes.

        Missing concepts produce warnings in the metadata note, not exceptions.
        Returns FundamentalsData with is_mock=False and source_tier=T2_regulator_or_gov.
        """
        if ticker.strip().isdigit():
            cik = ticker.strip()
        else:
            cik = await self.resolve_cik(ticker)

        padded = _pad_cik(cik)
        url = _COMPANY_FACTS_URL.format(cik=padded)

        async with httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT},
            timeout=30.0,
        ) as client:
            response = await client.get(url)
            if response.status_code == 404:
                raise ValueError(
                    f"SEC EDGAR companyfacts not found for CIK {padded} "
                    f"(ticker: {ticker}). "
                    "The company may not file XBRL data with the SEC."
                )
            response.raise_for_status()
            data = response.json()

        datapoints, warnings = parse_company_facts(data, ticker, cik)

        # Phase 19.3: layer normalized fundamentals (derived margins, FCF, total
        # debt, cash, gross/operating income, YoY growth) on top of the base 10.
        # Existing field_names from parse_company_facts win to preserve behavior.
        from app.integrations.sec_fundamentals_normalizer import normalize_company_facts

        normalized = normalize_company_facts(data, ticker, cik)
        existing_fields = {dp.field_name for dp in datapoints}
        for dp in normalized.to_datapoints():
            if dp.field_name not in existing_fields:
                datapoints.append(dp)
                existing_fields.add(dp.field_name)
        warnings.extend(normalized.warnings)

        warning_note = " | ".join(warnings) if warnings else None
        status_note = (
            f"SEC EDGAR XBRL companyfacts — {ticker.upper()} CIK {padded}. "
            f"{len(datapoints)} datapoints extracted. "
            f"Source: {url}."
        )
        if warning_note:
            status_note += f" Warnings: {warning_note}"

        meta = ProviderResponseMetadata(
            provider_name=self.provider_name,
            source_tier=SourceTier.T2_regulator_or_gov,
            retrieved_at=datetime.now(timezone.utc),
            is_mock=False,
            status=ProviderStatus.ok,
            note=status_note,
        )

        return FundamentalsData(
            ticker=ticker.upper(),
            exchange=exchange,
            datapoints=datapoints,
            meta=meta,
        )
