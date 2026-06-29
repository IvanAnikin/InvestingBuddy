"""
SecEdgarProvider — SEC EDGAR free JSON API client foundation (data.sec.gov).

Source tier: T2_regulator_or_gov
API key required: No (SEC EDGAR is a free public API with rate limits).
Rate limit: 10 requests/second (SEC Fair Access Policy). Set User-Agent.

Endpoints:
  Company submissions: https://data.sec.gov/submissions/CIK{cik_padded}.json
    - CIK must be zero-padded to 10 digits (e.g. CIK0000320193)
  Company XBRL facts:  https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json
    - Contains structured financial data from XBRL filings

Implemented:
  get_company_by_cik(cik)   → fetch company profile from submissions endpoint
  get_company_profile(ticker, exchange) → accepts CIK as ticker (all digits)
                                           or raises NotImplementedError with instructions

Not yet implemented:
  Ticker → CIK resolution (requires https://www.sec.gov/files/company_tickers.json lookup)
  XBRL facts / fundamentals (get_fundamentals)

Live calls are guarded by explicit CIK usage.
In CI, tests parse fixture JSON via _parse_edgar_submissions() without network calls.

Integration tests (live network) are marked @pytest.mark.integration
and only run when ENABLE_INTEGRATION_TESTS=true.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    DataQuality,
    FinancialDataProvider,
    FundamentalsData,
    PriceHistoryData,
    ProviderCapability,
    ProviderResponseMetadata,
    ProviderStatus,
    SourceRecordAttrs,
    SourceTier,
    build_source_record,
)

_EDGAR_BASE_URL = "https://data.sec.gov"
_SUBMISSIONS_URL = f"{_EDGAR_BASE_URL}/submissions/CIK{{cik}}.json"
_COMPANY_FACTS_URL = f"{_EDGAR_BASE_URL}/api/xbrl/companyfacts/CIK{{cik}}.json"

# SEC requires a User-Agent identifying the application and a contact email.
# See: https://www.sec.gov/os/accessing-edgar-data
_USER_AGENT = "InvestingBuddy-Research-Platform/1.0 (contact: research@investingbuddy.com)"


def _pad_cik(cik: str) -> str:
    """Zero-pad a CIK to 10 digits as required by data.sec.gov."""
    return str(int(cik)).zfill(10)


def _parse_edgar_submissions(data: dict, cik: str) -> dict:
    """
    Parse a data.sec.gov submissions JSON into a flat dict.

    This function is pure — no network calls — so it can be unit-tested
    directly with fixture JSON content.

    Returns a dict with keys: cik, name, tickers, exchanges, sic, sic_description,
    fiscal_year_end, state_of_incorporation, website, investor_website, category.
    """
    return {
        "cik": cik,
        "name": data.get("name", ""),
        "tickers": data.get("tickers", []),
        "exchanges": data.get("exchanges", []),
        "sic": data.get("sic", ""),
        "sic_description": data.get("sicDescription", ""),
        "fiscal_year_end": data.get("fiscalYearEnd", ""),
        "state_of_incorporation": data.get("stateOfIncorporation", ""),
        "state_of_incorporation_description": data.get("stateOfIncorporationDescription", ""),
        "website": data.get("website", "") or None,
        "investor_website": data.get("investorWebsite", "") or None,
        "category": data.get("category", ""),
        "ein": data.get("ein", "") or None,
        "phone": data.get("phone", "") or None,
        "entity_type": data.get("entityType", ""),
    }


def _submissions_to_profile(parsed: dict, source_url: str) -> CompanyProfileData:
    """Convert parsed SEC EDGAR submissions dict to a CompanyProfileData instance."""
    meta = ProviderResponseMetadata(
        provider_name="sec_edgar",
        source_tier=SourceTier.T2_regulator_or_gov,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
        note=(
            f"Company submissions data from SEC EDGAR (data.sec.gov). "
            f"CIK: {parsed['cik']}. "
            f"Filing category: {parsed.get('category', 'unknown')}."
        ),
    )

    tickers = parsed.get("tickers", [])
    exchanges = parsed.get("exchanges", [])
    primary_ticker = tickers[0] if tickers else parsed["cik"]
    primary_exchange = exchanges[0] if exchanges else None

    fiscal_year_end_raw = parsed.get("fiscal_year_end", "")
    fiscal_year_end = None
    if fiscal_year_end_raw and len(fiscal_year_end_raw) == 4:
        month_map = {
            "01": "January", "02": "February", "03": "March", "04": "April",
            "05": "May", "06": "June", "07": "July", "08": "August",
            "09": "September", "10": "October", "11": "November", "12": "December",
        }
        month = fiscal_year_end_raw[:2]
        fiscal_year_end = month_map.get(month, fiscal_year_end_raw)

    description_parts = []
    if parsed.get("sic_description"):
        description_parts.append(f"SIC: {parsed['sic_description']}")
    if parsed.get("state_of_incorporation_description"):
        description_parts.append(
            f"Incorporated in: {parsed['state_of_incorporation_description']}"
        )
    if parsed.get("entity_type"):
        description_parts.append(f"Entity type: {parsed['entity_type']}")

    return CompanyProfileData(
        ticker=primary_ticker,
        exchange=primary_exchange,
        legal_name=parsed["name"],
        country_domicile="US",  # SEC EDGAR only covers US-registered entities
        reporting_currency="USD",
        fiscal_year_end=fiscal_year_end,
        sector=None,
        industry=parsed.get("sic_description") or None,
        description=". ".join(description_parts) if description_parts else None,
        website=parsed.get("website"),
        isin=None,
        lei=None,
        ipo_date=None,
        source_url=source_url,
        data_quality=DataQuality.A_verified,
        meta=meta,
    )


class SecEdgarProvider(FinancialDataProvider):
    """
    SEC EDGAR regulatory filing data (live foundation implementation).

    Source tier: T2_regulator_or_gov
    Covers: US-listed companies — company profile, filing metadata, XBRL financials
    Not suitable for: non-US companies, price history

    Primary access pattern:
      profile = await provider.get_company_by_cik("320193")   # Apple Inc.

    The abstract get_company_profile(ticker, exchange) accepts a CIK as the
    ticker argument (all-digit string). Ticker→CIK resolution is not yet
    implemented; pass the CIK directly.

    Live calls require the SEC's User-Agent policy to be respected (set above).
    Rate limit: max 10 requests/second.
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
        return ProviderStatus.ok

    def build_source_record_for_company(self, cik: str) -> SourceRecordAttrs:
        """Convenience method — returns prepared Source record attrs for a CIK fetch."""
        padded = _pad_cik(cik)
        source_url = _SUBMISSIONS_URL.format(cik=padded)
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
            title=f"SEC EDGAR submissions — CIK {padded}",
            data_quality=DataQuality.A_verified,
        )

    async def get_company_by_cik(self, cik: str) -> CompanyProfileData:
        """
        Fetch company profile from the SEC EDGAR submissions endpoint (live network call).

        Args:
            cik: SEC Central Index Key as a string (e.g. '320193' or '0000320193').
                 Will be zero-padded to 10 digits automatically.

        Returns:
            CompanyProfileData with source_tier=T2_regulator_or_gov and
            data_quality=A_verified (direct from SEC regulatory filing system).

        Raises:
            ValueError: If CIK is not found (404 from data.sec.gov).
            httpx.HTTPError: On network or HTTP error.
        """
        padded = _pad_cik(cik)
        source_url = _SUBMISSIONS_URL.format(cik=padded)

        async with httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT},
            timeout=20.0,
        ) as client:
            response = await client.get(source_url)
            if response.status_code == 404:
                raise ValueError(
                    f"CIK '{cik}' (padded: {padded}) not found in SEC EDGAR. "
                    "Check the CIK number at https://www.sec.gov/cgi-bin/browse-edgar"
                )
            response.raise_for_status()
            data = response.json()

        parsed = _parse_edgar_submissions(data, cik)
        return _submissions_to_profile(parsed, source_url)

    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        """
        Resolve a company profile from SEC EDGAR.

        If ticker is all digits (a CIK), fetches from the submissions endpoint.
        Otherwise raises NotImplementedError — ticker→CIK resolution is deferred.

        For direct CIK access, prefer get_company_by_cik() for clarity.
        """
        if ticker.strip().isdigit():
            return await self.get_company_by_cik(ticker.strip())

        raise NotImplementedError(
            f"SecEdgarProvider.get_company_profile does not support ticker-based lookup yet. "
            f"Pass the SEC CIK (all-digit string) as the ticker argument, or use "
            f"get_company_by_cik(cik) directly. "
            f"Find the CIK for '{ticker}' at https://www.sec.gov/cgi-bin/browse-edgar"
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
            f"Planned: fetch XBRL company facts from "
            f"{_COMPANY_FACTS_URL.format(cik='CIK{cik}')}. "
            "Pass the CIK as the ticker argument when implemented. "
            "Use MockFinancialDataProvider in tests."
        )
