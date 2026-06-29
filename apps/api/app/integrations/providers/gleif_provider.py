"""
GleifProvider — live GLEIF LEI (Legal Entity Identifier) registry lookup.

Source tier: T2_regulator_or_gov
API key required: No (GLEIF is a free public API).

GLEIF is the Global Legal Entity Identifier Foundation — a public-interest body
supervised by the Financial Stability Board. It provides the authoritative
registry for LEI codes used to identify legal entities cross-border.

Endpoints:
  Search by name: https://api.gleif.org/api/v1/lei-records?filter[entity.legalName]={name}
  Fetch by LEI:   https://api.gleif.org/api/v1/lei-records/{lei}

Usage pattern:
  provider = GleifProvider()
  # By LEI (20-character alphanumeric code):
  profile = await provider.get_company_profile("HWUPKR0MPOU8FGXBT394")
  # By name search (get_company_profile uses name search if not a LEI):
  profile = await provider.get_company_profile("Apple Inc.")
  # Or use the dedicated search method:
  results = await provider.search_by_name("Apple Inc.")

The abstract interface get_company_profile(ticker, exchange) is overridden to
accept either a LEI code or a company name as the first argument. Exchange is
ignored (GLEIF is jurisdiction-based, not exchange-based).

Live calls are made when search_by_name() or get_by_lei() are invoked.
In CI, tests must use MockFinancialDataProvider or parse fixture JSON via
_parse_gleif_record() without making network calls.

Integration tests (live network) are marked @pytest.mark.integration
and only run when ENABLE_INTEGRATION_TESTS=true.
"""

from __future__ import annotations

import re
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

_GLEIF_BASE_URL = "https://api.gleif.org/api/v1"
_LEI_PATTERN = re.compile(r"^[A-Z0-9]{18}\d{2}$")

_USER_AGENT = "InvestingBuddy-Research-Platform/1.0 (contact: research@investingbuddy.com)"


def _is_lei(value: str) -> bool:
    """Return True if the value looks like a valid LEI (20 alphanumeric chars)."""
    return bool(_LEI_PATTERN.match(value.upper()))


def _parse_gleif_record(record: dict) -> dict:
    """
    Parse a single GLEIF lei-records data item into a flat dict.

    This function is pure — no network calls — so it can be unit-tested
    directly with fixture JSON content.

    Returns a dict with keys: lei, legal_name, jurisdiction, country, city,
    entity_status, website (None — GLEIF doesn't carry website).
    """
    attrs = record.get("attributes", {})
    lei = attrs.get("lei", "")
    entity = attrs.get("entity", {})
    legal_name_obj = entity.get("legalName", {})
    legal_name = legal_name_obj.get("name", "") if isinstance(legal_name_obj, dict) else ""
    jurisdiction = entity.get("jurisdiction", "")
    status = entity.get("status", "UNKNOWN")
    legal_address = entity.get("legalAddress", {})
    country = legal_address.get("country", "")
    city = legal_address.get("city", "")
    return {
        "lei": lei,
        "legal_name": legal_name,
        "jurisdiction": jurisdiction,
        "country": country,
        "city": city,
        "entity_status": status,
    }


def _record_to_profile(parsed: dict, source_url: str) -> CompanyProfileData:
    """Convert a parsed GLEIF dict to a CompanyProfileData instance."""
    meta = ProviderResponseMetadata(
        provider_name="gleif",
        source_tier=SourceTier.T2_regulator_or_gov,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
        note="Legal entity data from GLEIF LEI registry (api.gleif.org)",
    )
    return CompanyProfileData(
        ticker=parsed["lei"],  # LEI used as the canonical identifier
        exchange=None,
        legal_name=parsed["legal_name"],
        country_domicile=parsed["country"] or None,
        reporting_currency=None,
        fiscal_year_end=None,
        sector=None,
        industry=None,
        description=(
            f"Entity status: {parsed['entity_status']}. "
            f"Jurisdiction: {parsed['jurisdiction']}"
        ),
        website=None,
        isin=None,
        lei=parsed["lei"],
        ipo_date=None,
        source_url=source_url,
        data_quality=DataQuality.A_verified,
        meta=meta,
    )


class GleifProvider(FinancialDataProvider):
    """
    GLEIF LEI registry — legal entity identity cross-reference (live implementation).

    Source tier: T2_regulator_or_gov
    Covers: LEI lookup, legal name, jurisdiction, entity status, registered address
    Not suitable for: price history, fundamentals, financial metrics

    The abstract get_company_profile(ticker, exchange) is overridden:
      - If ticker is a 20-char LEI → direct lookup via get_by_lei()
      - Otherwise → treated as company name → search via search_by_name()

    Use search_by_name() or get_by_lei() directly for clearest intent.
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
        return ProviderStatus.ok

    def build_source_record_for_entity(
        self, lei_or_name: str
    ) -> SourceRecordAttrs:
        """Convenience method — returns prepared Source record attrs for a GLEIF lookup."""
        if _is_lei(lei_or_name):
            source_url = f"{_GLEIF_BASE_URL}/lei-records/{lei_or_name.upper()}"
            title = f"GLEIF LEI record — {lei_or_name.upper()}"
        else:
            source_url = f"{_GLEIF_BASE_URL}/lei-records"
            title = f"GLEIF LEI search — {lei_or_name}"
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
            title=title,
            data_quality=DataQuality.A_verified,
        )

    async def get_by_lei(self, lei: str) -> CompanyProfileData:
        """
        Fetch a GLEIF record by its LEI code (live network call).

        Args:
            lei: 20-character Legal Entity Identifier (e.g. 'HWUPKR0MPOU8FGXBT394').

        Returns:
            CompanyProfileData with source_tier=T2_regulator_or_gov and
            data_quality=A_verified (GLEIF is the authoritative LEI source).

        Raises:
            ValueError: If the LEI is not found.
            httpx.HTTPError: On network or HTTP error.
        """
        lei = lei.upper()
        source_url = f"{_GLEIF_BASE_URL}/lei-records/{lei}"
        async with httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.api+json"},
            timeout=15.0,
        ) as client:
            response = await client.get(source_url)
            if response.status_code == 404:
                raise ValueError(f"LEI '{lei}' not found in GLEIF registry.")
            response.raise_for_status()
            data = response.json()

        # Direct LEI fetch returns a single object under "data"
        record = data.get("data", {})
        if not record:
            raise ValueError(f"LEI '{lei}' returned empty response from GLEIF.")
        parsed = _parse_gleif_record(record)
        return _record_to_profile(parsed, source_url)

    async def search_by_name(
        self, company_name: str, page_size: int = 5
    ) -> list[CompanyProfileData]:
        """
        Search GLEIF registry by legal name (live network call).

        Args:
            company_name: Legal name to search for (partial match supported).
            page_size: Max results to return (default 5).

        Returns:
            List of CompanyProfileData matches. Empty list if no results.

        Raises:
            httpx.HTTPError: On network or HTTP error.
        """
        source_url = f"{_GLEIF_BASE_URL}/lei-records"
        params = {
            "filter[entity.legalName]": company_name,
            "page[size]": str(page_size),
        }
        async with httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.api+json"},
            timeout=15.0,
        ) as client:
            response = await client.get(source_url, params=params)
            response.raise_for_status()
            data = response.json()

        records = data.get("data", [])
        results = []
        for record in records:
            parsed = _parse_gleif_record(record)
            results.append(_record_to_profile(parsed, source_url))
        return results

    async def get_company_profile(
        self,
        ticker: str,
        exchange: str | None = None,
    ) -> CompanyProfileData:
        """
        Resolve a GLEIF record using ticker_or_lei as first argument.

        If the first argument is a 20-character LEI code → direct lookup.
        Otherwise → name search; returns the top result.

        Raises:
            ValueError: If the input is a name and no results are found.
        """
        if _is_lei(ticker):
            return await self.get_by_lei(ticker)

        results = await self.search_by_name(ticker, page_size=1)
        if not results:
            raise ValueError(
                f"No GLEIF record found for '{ticker}'. "
                "Try passing a full legal name or a 20-character LEI code."
            )
        return results[0]

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
