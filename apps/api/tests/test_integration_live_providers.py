"""
Integration tests — live network calls to free financial data providers.

These tests make REAL HTTP requests to external services.
They are:
  - Marked with @pytest.mark.integration
  - Skipped automatically unless ENABLE_INTEGRATION_TESTS=true is set in .env
  - NEVER run in CI (CI must always run offline)
  - Safe to run locally to verify real provider connectivity

Run manually:
  cd apps/api
  ENABLE_INTEGRATION_TESTS=true pytest tests/test_integration_live_providers.py -v -m integration

Manual integration test command (from project root):
  cd apps/api && source .venv/bin/activate && \
  ENABLE_INTEGRATION_TESTS=true pytest tests/test_integration_live_providers.py \
  -v -m integration --tb=short

Providers tested:
  - Stooq: live OHLCV price CSV (https://stooq.com)
  - GLEIF: live LEI lookup (https://api.gleif.org)
  - SEC EDGAR: live company submissions (https://data.sec.gov)

No API keys required for any of these tests.
"""

from __future__ import annotations

import os

import pytest

from app.integrations.financial_data_provider import (
    DataQuality,
    PriceHistoryData,
    ProviderStatus,
    SourceTier,
)
from app.integrations.providers.gleif_provider import GleifProvider
from app.integrations.providers.sec_edgar_provider import SecEdgarProvider
from app.integrations.providers.stooq_provider import StooqProvider

# ---------------------------------------------------------------------------
# Skip guard — integration tests are opt-in only
# ---------------------------------------------------------------------------

_INTEGRATION_ENABLED = os.environ.get("ENABLE_INTEGRATION_TESTS", "false").lower() == "true"

skip_unless_integration = pytest.mark.skipif(
    not _INTEGRATION_ENABLED,
    reason=(
        "Set ENABLE_INTEGRATION_TESTS=true to run live provider tests. "
        "These tests make real network calls and must NOT run in CI."
    ),
)


# ---------------------------------------------------------------------------
# Stooq — live price history
# ---------------------------------------------------------------------------


@skip_unless_integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_stooq_live_aapl_price_history() -> None:
    """Live Stooq: fetch Apple US price history.

    NOTE: Stooq serves a JavaScript browser-verification challenge page for automated
    requests without a real browser session/cookies. This test may fail in environments
    that cannot complete the JS challenge (e.g. headless servers). This is a known
    Stooq anti-bot limitation, not a code bug. Offline fixture tests in
    test_phase5_live_providers.py verify the parsing logic independently.
    """
    provider = StooqProvider()
    result = await provider.get_price_history(
        ticker="AAPL",
        exchange="NASDAQ",
        start_date="2026-01-02",
        end_date="2026-01-10",
    )
    assert isinstance(result, PriceHistoryData)
    assert result.ticker == "AAPL"
    assert len(result.price_points) > 0
    assert result.meta.is_mock is False
    assert result.meta.source_tier == SourceTier.T5_api_aggregator
    for pt in result.price_points:
        assert pt.close > 0
        assert pt.date


@skip_unless_integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_stooq_live_vow3_xetra_price_history() -> None:
    """Live Stooq: fetch Volkswagen AG (XETRA) price history.

    NOTE: Subject to the same Stooq anti-bot limitation as test_stooq_live_aapl_price_history.
    """
    provider = StooqProvider()
    result = await provider.get_price_history(
        ticker="VOW3",
        exchange="XETRA",
        start_date="2026-01-02",
        end_date="2026-01-10",
    )
    assert isinstance(result, PriceHistoryData)
    assert result.ticker == "VOW3"
    assert len(result.price_points) > 0
    assert result.meta.is_mock is False


@skip_unless_integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_stooq_live_unknown_ticker_raises_value_error() -> None:
    """Live Stooq: unknown ticker returns no data → ValueError."""
    provider = StooqProvider()
    with pytest.raises(ValueError, match="no price data"):
        await provider.get_price_history(ticker="ZZZZZZ_FAKE_TICKER_XYZ", exchange="NASDAQ")


@skip_unless_integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_stooq_live_source_record_ready() -> None:
    """Live Stooq: source record attrs are returned before fetch."""
    provider = StooqProvider()
    attrs = provider.build_source_record_for_prices("AAPL", "NASDAQ")
    assert attrs.source_type == "financial_data_api"
    assert attrs.credibility_score == 0.55
    assert "stooq" in (attrs.url or "").lower()


# ---------------------------------------------------------------------------
# GLEIF — live LEI lookup
# ---------------------------------------------------------------------------


@skip_unless_integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_gleif_live_apple_by_lei() -> None:
    """Live GLEIF: fetch Apple Inc. by known LEI."""
    provider = GleifProvider()
    # Apple Inc. LEI — publicly verifiable at https://search.gleif.org
    profile = await provider.get_by_lei("HWUPKR0MPOU8FGXBT394")
    assert profile.legal_name == "Apple Inc."
    assert profile.lei == "HWUPKR0MPOU8FGXBT394"
    assert profile.country_domicile == "US"
    assert profile.meta.is_mock is False
    assert profile.meta.source_tier == SourceTier.T2_regulator_or_gov
    assert profile.data_quality == DataQuality.A_verified.value


@skip_unless_integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_gleif_live_search_by_name() -> None:
    """Live GLEIF: search Apple Inc. by legal name."""
    provider = GleifProvider()
    results = await provider.search_by_name("Apple Inc.", page_size=3)
    assert len(results) > 0
    names = [r.legal_name for r in results]
    assert any("Apple" in name for name in names)


@skip_unless_integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_gleif_live_invalid_lei_raises_value_error() -> None:
    """Live GLEIF: invalid LEI returns 404 → ValueError."""
    provider = GleifProvider()
    with pytest.raises(ValueError, match="not found in GLEIF registry"):
        await provider.get_by_lei("ZZZZZZZZZZZZZZZZZZ12")


@skip_unless_integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_gleif_live_provider_status_ok() -> None:
    """Live GLEIF: provider status is ok (no key needed)."""
    assert GleifProvider().get_provider_status() == ProviderStatus.ok


# ---------------------------------------------------------------------------
# SEC EDGAR — live company submissions
# ---------------------------------------------------------------------------


@skip_unless_integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_sec_edgar_live_apple_by_cik() -> None:
    """Live SEC EDGAR: fetch Apple Inc. by CIK 320193.

    NOTE: The live SEC EDGAR submissions endpoint for Apple returns an empty string
    for the 'website' field (not the investor-facing URL). We normalise '' → None.
    """
    provider = SecEdgarProvider()
    profile = await provider.get_company_by_cik("320193")
    assert profile.legal_name == "Apple Inc."
    assert profile.country_domicile == "US"
    # website may be None (empty in EDGAR) or a URL — both are valid
    assert profile.website is None or profile.website.startswith("http")
    assert profile.meta.is_mock is False
    assert profile.meta.source_tier == SourceTier.T2_regulator_or_gov
    assert profile.data_quality == DataQuality.A_verified.value


@skip_unless_integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_sec_edgar_live_apple_tickers_include_aapl() -> None:
    """Live SEC EDGAR: Apple submissions include AAPL ticker."""
    import httpx

    from app.integrations.providers.sec_edgar_provider import _pad_cik, _parse_edgar_submissions
    cik = "320193"
    padded = _pad_cik(cik)
    url = f"https://data.sec.gov/submissions/CIK{padded}.json"
    async with httpx.AsyncClient(
        headers={"User-Agent": "InvestingBuddy-Research-Platform/1.0 (contact: research@investingbuddy.com)"},
        timeout=20.0,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    parsed = _parse_edgar_submissions(data, cik)
    assert "AAPL" in parsed["tickers"]


@skip_unless_integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_sec_edgar_live_unknown_cik_raises_value_error() -> None:
    """Live SEC EDGAR: unknown CIK returns 404 → ValueError."""
    provider = SecEdgarProvider()
    with pytest.raises(ValueError, match="not found in SEC EDGAR"):
        await provider.get_company_by_cik("9999999999")


@skip_unless_integration
@pytest.mark.integration
@pytest.mark.asyncio
async def test_sec_edgar_live_provider_status_ok() -> None:
    """Live SEC EDGAR: provider status is ok (no key needed)."""
    assert SecEdgarProvider().get_provider_status() == ProviderStatus.ok
