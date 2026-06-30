"""
Phase 13 — EODHD Real Financial Data Integration Tests.

All tests in this file run OFFLINE with no network calls and no API key.
Live integration tests are in test_integration_eodhd.py (opt-in via ENABLE_EODHD_INTEGRATION_TESTS=true).

Coverage:
  - EodhdProvider: missing API key behavior (not_configured)
  - EodhdProvider: symbol building (_eodhd_symbol)
  - EodhdProvider: company profile parsing from fixture
  - EodhdProvider: fundamentals parsing from fixture (full + sparse)
  - EodhdProvider: price history parsing from fixture
  - EodhdProvider: error handling (auth, not found, rate limit, network, bad JSON)
  - EodhdProvider: source tier is always T5_api_aggregator
  - EodhdProvider: datapoint wrappers generated correctly
  - EodhdProvider: no bare numbers in FundamentalsData datapoints
  - CompanyIdentifierResolver: structural resolve when no API key
  - CompanyIdentifierResolver: EODHD symbol format detection
  - CompanyIdentifierResolver: ambiguity detection
  - CompanyIdentifierResolver: search result scoring
  - snapshot_builder: fundamentals enrich company snapshot
  - snapshot_builder: fundamentals enrich schema draft
  - WorkflowRunResponse: Phase 13 fields present
  - FinancialDataService: get_fundamentals() delegation
"""

from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict | list:
    path = FIXTURE_DIR / name
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def aapl_fundamentals_payload() -> dict:
    return load_fixture("eodhd_fundamentals_aapl.json")


@pytest.fixture
def sparse_fundamentals_payload() -> dict:
    return load_fixture("eodhd_fundamentals_sparse.json")


@pytest.fixture
def aapl_eod_payload() -> list:
    return load_fixture("eodhd_eod_aapl.json")


@pytest.fixture
def apple_search_payload() -> list:
    return load_fixture("eodhd_search_apple.json")


# ---------------------------------------------------------------------------
# EodhdProvider: status and key checks
# ---------------------------------------------------------------------------


class TestEodhdProviderStatus:
    def test_no_key_returns_not_configured(self):
        """When EODHD_API_KEY is absent, status must be not_configured."""
        env = {k: v for k, v in os.environ.items() if k != "EODHD_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            from app.integrations.providers.eodhd_provider import EodhdProvider
            provider = EodhdProvider()
            assert provider.get_provider_status().value == "not_configured"

    def test_key_present_returns_ok(self):
        """When EODHD_API_KEY is set, status is ok."""
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key-123"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider
            provider = EodhdProvider()
            assert provider.get_provider_status().value == "ok"

    def test_provider_name(self):
        from app.integrations.providers.eodhd_provider import EodhdProvider
        assert EodhdProvider().provider_name == "eodhd"

    def test_source_tier_is_always_t5(self):
        """EODHD source tier must always be T5_api_aggregator — never inflate."""
        from app.integrations.financial_data_provider import SourceTier
        from app.integrations.providers.eodhd_provider import EodhdProvider
        provider = EodhdProvider()
        assert provider.source_tier == SourceTier.T5_api_aggregator

    def test_capabilities_list(self):
        from app.integrations.providers.eodhd_provider import EodhdProvider
        provider = EodhdProvider()
        caps = [c.value for c in provider.get_supported_capabilities()]
        assert "company_profile" in caps
        assert "price_history" in caps
        assert "fundamentals" in caps
        assert "news" in caps
        assert "screener" in caps

    @pytest.mark.asyncio
    async def test_no_key_raises_on_get_company_profile(self):
        """Without API key, all live methods must raise EodhdProviderError."""
        env = {k: v for k, v in os.environ.items() if k != "EODHD_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            from app.integrations.providers.eodhd_provider import EodhdProvider, EodhdProviderError
            provider = EodhdProvider()
            with pytest.raises(EodhdProviderError, match="EODHD_API_KEY"):
                await provider.get_company_profile("AAPL", "NASDAQ")

    @pytest.mark.asyncio
    async def test_no_key_raises_on_get_fundamentals(self):
        env = {k: v for k, v in os.environ.items() if k != "EODHD_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            from app.integrations.providers.eodhd_provider import EodhdProvider, EodhdProviderError
            provider = EodhdProvider()
            with pytest.raises(EodhdProviderError, match="EODHD_API_KEY"):
                await provider.get_fundamentals("AAPL", "NASDAQ")

    @pytest.mark.asyncio
    async def test_no_key_raises_on_get_price_history(self):
        env = {k: v for k, v in os.environ.items() if k != "EODHD_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            from app.integrations.providers.eodhd_provider import EodhdProvider, EodhdProviderError
            provider = EodhdProvider()
            with pytest.raises(EodhdProviderError, match="EODHD_API_KEY"):
                await provider.get_price_history("AAPL", "NASDAQ")


# ---------------------------------------------------------------------------
# EodhdProvider: symbol building
# ---------------------------------------------------------------------------


class TestEodhdSymbolBuilding:
    def test_ticker_only(self):
        from app.integrations.providers.eodhd_provider import _eodhd_symbol
        assert _eodhd_symbol("AAPL", None) == "AAPL"

    def test_known_exchange_mapping(self):
        from app.integrations.providers.eodhd_provider import _eodhd_symbol
        assert _eodhd_symbol("AAPL", "NASDAQ") == "AAPL.US"
        assert _eodhd_symbol("VOW3", "XETRA") == "VOW3.XETRA"
        assert _eodhd_symbol("EQNR", "OSE") == "EQNR.OL"

    def test_unknown_exchange_passthrough(self):
        from app.integrations.providers.eodhd_provider import _eodhd_symbol
        assert _eodhd_symbol("ABC", "MYCX") == "ABC.MYCX"

    def test_ticker_uppercased(self):
        from app.integrations.providers.eodhd_provider import _eodhd_symbol
        assert _eodhd_symbol("aapl", "NASDAQ") == "AAPL.US"

    def test_lse_exchange(self):
        from app.integrations.providers.eodhd_provider import _eodhd_symbol
        assert _eodhd_symbol("BP", "LSE") == "BP.LSE"


# ---------------------------------------------------------------------------
# EodhdProvider: company profile parsing
# ---------------------------------------------------------------------------


class TestEodhdCompanyProfileParsing:
    @pytest.mark.asyncio
    async def test_company_profile_from_fixture(self, aapl_fundamentals_payload):
        """Company profile is parsed correctly from EODHD fundamentals fixture."""
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.financial_data_provider import SourceTier
            from app.integrations.providers.eodhd_provider import EodhdProvider

            provider = EodhdProvider()

            # Mock the _get_json method to return the fixture
            with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = aapl_fundamentals_payload
                profile = await provider.get_company_profile("AAPL", "NASDAQ")

        assert profile.ticker == "AAPL"
        assert profile.legal_name == "Apple Inc"
        assert profile.exchange == "NASDAQ"
        assert profile.country_domicile == "USA"
        assert profile.reporting_currency == "USD"
        assert profile.fiscal_year_end == "September"
        assert profile.isin == "US0378331005"
        assert profile.sector == "Technology"
        assert profile.industry == "Consumer Electronics"
        assert "smartphones" in profile.description.lower()
        assert profile.meta.provider_name == "eodhd"
        assert profile.meta.source_tier == SourceTier.T5_api_aggregator
        assert profile.meta.is_mock is False
        # CompanyProfileData uses use_enum_values=True so data_quality is stored as a string
        dq = profile.data_quality if isinstance(profile.data_quality, str) else profile.data_quality.value
        assert dq == "B_single_credible"

    @pytest.mark.asyncio
    async def test_source_tier_is_t5_in_profile(self, aapl_fundamentals_payload):
        """Profile meta source_tier must be T5 — never promoted."""
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider
            provider = EodhdProvider()
            with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = aapl_fundamentals_payload
                profile = await provider.get_company_profile("AAPL", "NASDAQ")
        tier = profile.meta.source_tier if isinstance(profile.meta.source_tier, str) else profile.meta.source_tier.value
        assert "T5" in tier


# ---------------------------------------------------------------------------
# EodhdProvider: price history parsing
# ---------------------------------------------------------------------------


class TestEodhdPriceHistoryParsing:
    @pytest.mark.asyncio
    async def test_price_history_from_fixture(self, aapl_eod_payload):
        """Price history parsed correctly from EODHD EOD fixture."""
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider

            provider = EodhdProvider()
            with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = aapl_eod_payload
                prices = await provider.get_price_history("AAPL", "NASDAQ")

        assert len(prices.price_points) == 6
        assert prices.price_points[0].date == "2026-06-20"
        assert prices.price_points[-1].close == pytest.approx(203.97, abs=0.01)
        assert prices.price_points[0].volume == 52341000
        assert prices.meta.provider_name == "eodhd"
        assert prices.meta.is_mock is False
        dq = prices.data_quality if isinstance(prices.data_quality, str) else prices.data_quality.value
        assert dq == "B_single_credible"

    @pytest.mark.asyncio
    async def test_empty_price_history_data_quality(self):
        """Empty price list results in D_weak_or_stale quality."""
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider

            provider = EodhdProvider()
            with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = []
                prices = await provider.get_price_history("UNKNOWN", "US")

        assert prices.price_points == []
        dq = prices.data_quality if isinstance(prices.data_quality, str) else prices.data_quality.value
        assert dq == "D_weak_or_stale"

    @pytest.mark.asyncio
    async def test_price_history_invalid_format_raises(self):
        from app.integrations.providers.eodhd_provider import EodhdProvider, EodhdProviderError
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            provider = EodhdProvider()
            with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = {"error": "not a list"}
                with pytest.raises(EodhdProviderError, match="unexpected format"):
                    await provider.get_price_history("AAPL", "US")


# ---------------------------------------------------------------------------
# EodhdProvider: fundamentals parsing
# ---------------------------------------------------------------------------


class TestEodhdFundamentalsParsing:
    @pytest.mark.asyncio
    async def test_fundamentals_from_fixture_full(self, aapl_fundamentals_payload):
        """Full fundamentals fixture produces many datapoints, all wrapped correctly."""
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.financial_data_provider import SourceTier
            from app.integrations.providers.eodhd_provider import EodhdProvider

            provider = EodhdProvider()
            with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = aapl_fundamentals_payload
                fundamentals = await provider.get_fundamentals("AAPL", "NASDAQ")

        assert len(fundamentals.datapoints) > 10
        assert fundamentals.ticker == "AAPL"
        assert fundamentals.meta.provider_name == "eodhd"
        assert fundamentals.meta.source_tier == SourceTier.T5_api_aggregator
        assert fundamentals.meta.is_mock is False

    @pytest.mark.asyncio
    async def test_fundamentals_source_tier_always_t5(self, aapl_fundamentals_payload):
        """Every datapoint in fundamentals must have T5 source tier."""
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider

            provider = EodhdProvider()
            with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = aapl_fundamentals_payload
                fundamentals = await provider.get_fundamentals("AAPL", "NASDAQ")

        for dp in fundamentals.datapoints:
            tier = dp.source_tier if isinstance(dp.source_tier, str) else dp.source_tier.value
            assert tier == "T5_api_aggregator", (
                f"Datapoint {dp.field_name} has tier {tier} — must be T5_api_aggregator"
            )

    @pytest.mark.asyncio
    async def test_fundamentals_datapoints_have_no_bare_numbers(self, aapl_fundamentals_payload):
        """Every numeric datapoint must be wrapped in FundamentalDataPoint (not bare)."""
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.financial_data_provider import FundamentalDataPoint
            from app.integrations.providers.eodhd_provider import EodhdProvider

            provider = EodhdProvider()
            with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = aapl_fundamentals_payload
                fundamentals = await provider.get_fundamentals("AAPL", "NASDAQ")

        for dp in fundamentals.datapoints:
            assert isinstance(dp, FundamentalDataPoint), f"Not a FundamentalDataPoint: {dp}"
            assert dp.field_name, "datapoint.field_name must be non-empty"
            assert dp.source_tier is not None, f"datapoint {dp.field_name} missing source_tier"
            assert dp.source_name, f"datapoint {dp.field_name} missing source_name"
            assert dp.data_quality is not None, f"datapoint {dp.field_name} missing data_quality"
            assert dp.as_of, f"datapoint {dp.field_name} missing as_of date"

    @pytest.mark.asyncio
    async def test_fundamentals_key_fields_present(self, aapl_fundamentals_payload):
        """Key financial fields must be present in parsed fundamentals."""
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider

            provider = EodhdProvider()
            with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = aapl_fundamentals_payload
                fundamentals = await provider.get_fundamentals("AAPL", "NASDAQ")

        field_names = {dp.field_name for dp in fundamentals.datapoints}
        assert "highlights.market_cap_mln" in field_names
        assert "highlights.revenue_ttm_mln" in field_names
        assert "valuation.enterprise_value_mln" in field_names
        assert "valuation.ev_ebitda" in field_names
        assert "shares.outstanding_mln" in field_names
        assert "technicals.beta" in field_names

    @pytest.mark.asyncio
    async def test_fundamentals_income_statement_fields(self, aapl_fundamentals_payload):
        """Annual income statement fields are extracted from the most recent year."""
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider

            provider = EodhdProvider()
            with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = aapl_fundamentals_payload
                fundamentals = await provider.get_fundamentals("AAPL", "NASDAQ")

        field_names = {dp.field_name for dp in fundamentals.datapoints}
        assert any("income_statement" in f for f in field_names)
        assert any("total_revenue_mln" in f for f in field_names)
        assert any("net_income_mln" in f for f in field_names)

    @pytest.mark.asyncio
    async def test_fundamentals_balance_sheet_fields(self, aapl_fundamentals_payload):
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider

            provider = EodhdProvider()
            with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = aapl_fundamentals_payload
                fundamentals = await provider.get_fundamentals("AAPL", "NASDAQ")

        field_names = {dp.field_name for dp in fundamentals.datapoints}
        assert any("balance_sheet" in f for f in field_names)
        assert any("total_assets_mln" in f for f in field_names)
        assert any("ppe_net_mln" in f for f in field_names)

    @pytest.mark.asyncio
    async def test_fundamentals_cash_flow_fields(self, aapl_fundamentals_payload):
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider

            provider = EodhdProvider()
            with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = aapl_fundamentals_payload
                fundamentals = await provider.get_fundamentals("AAPL", "NASDAQ")

        field_names = {dp.field_name for dp in fundamentals.datapoints}
        assert any("cash_flow" in f for f in field_names)
        assert any("free_cash_flow_mln" in f for f in field_names)
        assert any("operating_cash_flow_mln" in f for f in field_names)

    @pytest.mark.asyncio
    async def test_fundamentals_sparse_company(self, sparse_fundamentals_payload):
        """Sparse fundamentals (mostly None) parse without error, few datapoints returned."""
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider

            provider = EodhdProvider()
            with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = sparse_fundamentals_payload
                fundamentals = await provider.get_fundamentals("SMLCO", "OL")

        # Sparse fixture has minimal data — should still return valid structure
        assert fundamentals.ticker == "SMLCO"
        assert fundamentals.meta.provider_name == "eodhd"
        # No assertions on count — some fields will be absent and skipped (None filtered)
        for dp in fundamentals.datapoints:
            assert dp.value is not None, f"None value in datapoint {dp.field_name}"

    @pytest.mark.asyncio
    async def test_raw_payload_hash_present(self, aapl_fundamentals_payload):
        """The raw payload hash datapoint must be present for deduplication."""
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider

            provider = EodhdProvider()
            with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = aapl_fundamentals_payload
                fundamentals = await provider.get_fundamentals("AAPL", "NASDAQ")

        hash_dp = next(
            (dp for dp in fundamentals.datapoints if dp.field_name == "_meta.raw_payload_hash"),
            None,
        )
        assert hash_dp is not None, "Missing _meta.raw_payload_hash datapoint"
        assert isinstance(hash_dp.value, str) and len(hash_dp.value) == 64


# ---------------------------------------------------------------------------
# EodhdProvider: error handling
# ---------------------------------------------------------------------------


class TestEodhdErrorHandling:
    @pytest.mark.asyncio
    async def test_http_401_raises_auth_error(self):

        from app.integrations.providers.eodhd_provider import EodhdAuthError, EodhdProvider

        with patch.dict(os.environ, {"EODHD_API_KEY": "bad-key"}):
            provider = EodhdProvider()

            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_resp.text = "Unauthorized"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client
                mock_client_cls.return_value.__aexit__.return_value = None
                mock_client.get.return_value = mock_resp

                with pytest.raises(EodhdAuthError):
                    await provider.get_company_profile("AAPL", "US")

    @pytest.mark.asyncio
    async def test_http_404_raises_not_found_error(self):
        from app.integrations.providers.eodhd_provider import EodhdNotFoundError, EodhdProvider

        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            provider = EodhdProvider()

            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_resp.text = "Not Found"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client
                mock_client_cls.return_value.__aexit__.return_value = None
                mock_client.get.return_value = mock_resp

                with pytest.raises(EodhdNotFoundError):
                    await provider.get_company_profile("NOTEXISTS", "US")

    @pytest.mark.asyncio
    async def test_http_429_raises_rate_limit_error(self):
        from app.integrations.providers.eodhd_provider import EodhdProvider, EodhdRateLimitError

        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            provider = EodhdProvider()

            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_resp.text = "Rate limit exceeded"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client
                mock_client_cls.return_value.__aexit__.return_value = None
                mock_client.get.return_value = mock_resp

                with pytest.raises(EodhdRateLimitError):
                    await provider.get_price_history("AAPL", "US")

    @pytest.mark.asyncio
    async def test_network_error_raises_provider_error(self):
        import httpx

        from app.integrations.providers.eodhd_provider import EodhdProvider, EodhdProviderError

        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            provider = EodhdProvider()

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client
                mock_client_cls.return_value.__aexit__.return_value = None
                mock_client.get.side_effect = httpx.ConnectError("Connection refused")

                with pytest.raises(EodhdProviderError, match="Cannot connect"):
                    await provider.get_company_profile("AAPL", "US")

    @pytest.mark.asyncio
    async def test_timeout_raises_provider_error(self):
        import httpx

        from app.integrations.providers.eodhd_provider import EodhdProvider, EodhdProviderError

        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            provider = EodhdProvider()

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client
                mock_client_cls.return_value.__aexit__.return_value = None
                mock_client.get.side_effect = httpx.TimeoutException("Timed out")

                with pytest.raises(EodhdProviderError, match="timed out"):
                    await provider.get_fundamentals("AAPL", "US")

    @pytest.mark.asyncio
    async def test_non_json_response_raises_provider_error(self):
        from app.integrations.providers.eodhd_provider import EodhdProvider, EodhdProviderError

        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            provider = EodhdProvider()

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.side_effect = ValueError("Not JSON")
            mock_resp.text = "<html>Error</html>"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client
                mock_client_cls.return_value.__aexit__.return_value = None
                mock_client.get.return_value = mock_resp

                with pytest.raises(EodhdProviderError, match="non-JSON"):
                    await provider.get_company_profile("AAPL", "US")

    @pytest.mark.asyncio
    async def test_http_500_raises_provider_error(self):
        from app.integrations.providers.eodhd_provider import EodhdProvider, EodhdProviderError

        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            provider = EodhdProvider()

            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_resp.text = "Internal Server Error"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client
                mock_client_cls.return_value.__aexit__.return_value = None
                mock_client.get.return_value = mock_resp

                with pytest.raises(EodhdProviderError, match="HTTP 500"):
                    await provider.get_fundamentals("AAPL", "US")


# ---------------------------------------------------------------------------
# CompanyIdentifierResolver: structural resolution (no API key needed)
# ---------------------------------------------------------------------------


class TestCompanyIdentifierResolverStructural:
    @pytest.mark.asyncio
    async def test_eodhd_symbol_format_detected(self):
        """EODHD symbol format (TICKER.EXCHANGE) is detected with moderate confidence."""
        env = {k: v for k, v in os.environ.items() if k != "EODHD_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            from app.services.identifier_resolver import CompanyIdentifierResolver
            resolver = CompanyIdentifierResolver()
            result = await resolver.resolve("AAPL.US")

        assert len(result.candidates) == 1
        c = result.candidates[0]
        assert c.canonical_ticker == "AAPL"
        assert c.provider_symbol == "AAPL.US"
        assert c.exchange == "US"
        assert c.provider_confidence >= 0.5

    @pytest.mark.asyncio
    async def test_ticker_only_with_exchange_hint(self):
        """Plain ticker with exchange hint resolves to EODHD symbol."""
        env = {k: v for k, v in os.environ.items() if k != "EODHD_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            from app.services.identifier_resolver import CompanyIdentifierResolver
            resolver = CompanyIdentifierResolver()
            result = await resolver.resolve("AAPL", exchange="NASDAQ")

        assert len(result.candidates) == 1
        c = result.candidates[0]
        assert c.canonical_ticker == "AAPL"
        assert c.provider_symbol == "AAPL.US"

    @pytest.mark.asyncio
    async def test_no_api_key_returns_warning(self):
        """When API key absent, resolver must warn about low-confidence structural parse."""
        env = {k: v for k, v in os.environ.items() if k != "EODHD_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            from app.services.identifier_resolver import CompanyIdentifierResolver
            resolver = CompanyIdentifierResolver()
            result = await resolver.resolve("AAPL.US")

        assert any("EODHD_API_KEY" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_company_name_without_key_returns_empty_with_warning(self):
        """Free-text name search without API key cannot resolve, returns empty with warning."""
        env = {k: v for k, v in os.environ.items() if k != "EODHD_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            from app.services.identifier_resolver import CompanyIdentifierResolver
            resolver = CompanyIdentifierResolver()
            result = await resolver.resolve("Apple Inc")

        assert len(result.candidates) == 0
        assert result.is_ambiguous is True
        assert any("name search" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_ambiguity_flag_when_no_candidates(self):
        """Zero candidates → is_ambiguous=True."""
        env = {k: v for k, v in os.environ.items() if k != "EODHD_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            from app.services.identifier_resolver import CompanyIdentifierResolver
            resolver = CompanyIdentifierResolver()
            result = await resolver.resolve("Some Unknown Company Name With Spaces")
        assert result.is_ambiguous is True


# ---------------------------------------------------------------------------
# CompanyIdentifierResolver: EODHD search (mocked)
# ---------------------------------------------------------------------------


class TestCompanyIdentifierResolverWithEodhd:
    @pytest.mark.asyncio
    async def test_single_result_not_ambiguous(self, apple_search_payload):
        """Single unambiguous search result → is_ambiguous=False."""
        single_result = [apple_search_payload[0]]  # only AAPL.US

        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider
            from app.services.identifier_resolver import CompanyIdentifierResolver

            resolver = CompanyIdentifierResolver()
            with patch.object(EodhdProvider, "search_symbol", new_callable=AsyncMock) as mock_search:
                mock_search.return_value = single_result
                result = await resolver.resolve("AAPL", exchange="NASDAQ")

        assert len(result.candidates) >= 1
        assert result.is_ambiguous is False

    @pytest.mark.asyncio
    async def test_multiple_results_may_be_ambiguous(self, apple_search_payload):
        """Multiple close-confidence results → is_ambiguous=True."""
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider
            from app.services.identifier_resolver import CompanyIdentifierResolver

            resolver = CompanyIdentifierResolver()
            with patch.object(EodhdProvider, "search_symbol", new_callable=AsyncMock) as mock_search:
                mock_search.return_value = apple_search_payload
                result = await resolver.resolve("Apple")

        # With 3 Apple results and no exchange hint, likely ambiguous
        assert len(result.candidates) >= 1
        # Warnings should mention disambiguation if ambiguous
        if result.is_ambiguous:
            assert any("disambiguate" in w.lower() or "ambiguous" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_eodhd_search_error_returns_warning(self):
        """EODHD search failure → fallback with warning, no crash."""
        from app.integrations.providers.eodhd_provider import EodhdProviderError

        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider
            from app.services.identifier_resolver import CompanyIdentifierResolver

            resolver = CompanyIdentifierResolver()
            with patch.object(EodhdProvider, "search_symbol", new_callable=AsyncMock) as mock_search:
                mock_search.side_effect = EodhdProviderError("Network error")
                result = await resolver.resolve("Apple")

        assert any("error" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_eodhd_symbol_format_bypasses_search(self):
        """EODHD format input skips search and resolves directly with high confidence."""
        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            from app.integrations.providers.eodhd_provider import EodhdProvider
            from app.services.identifier_resolver import CompanyIdentifierResolver

            resolver = CompanyIdentifierResolver()
            with patch.object(EodhdProvider, "search_symbol", new_callable=AsyncMock) as mock_search:
                result = await resolver.resolve("VOW3.XETRA")

        mock_search.assert_not_called()
        assert len(result.candidates) == 1
        assert result.candidates[0].provider_symbol == "VOW3.XETRA"


# ---------------------------------------------------------------------------
# snapshot_builder: fundamentals enrichment
# ---------------------------------------------------------------------------


class TestSnapshotBuilderFundamentals:
    def _make_mock_profile(self) -> object:
        from app.integrations.financial_data_provider import (
            CompanyProfileData,
            DataQuality,
            ProviderResponseMetadata,
            ProviderStatus,
            SourceTier,
        )
        meta = ProviderResponseMetadata(
            provider_name="eodhd",
            source_tier=SourceTier.T5_api_aggregator,
            retrieved_at=datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc),
            is_mock=False,
            status=ProviderStatus.ok,
        )
        return CompanyProfileData(
            ticker="AAPL",
            exchange="US",
            legal_name="Apple Inc",
            country_domicile="USA",
            reporting_currency="USD",
            fiscal_year_end="September",
            sector="Technology",
            data_quality=DataQuality.B_single_credible,
            meta=meta,
        )

    def _make_mock_fundamentals(self) -> object:
        from app.integrations.financial_data_provider import (
            DataQuality,
            FundamentalDataPoint,
            FundamentalsData,
            ProviderResponseMetadata,
            ProviderStatus,
            SourceTier,
        )
        meta = ProviderResponseMetadata(
            provider_name="eodhd",
            source_tier=SourceTier.T5_api_aggregator,
            retrieved_at=datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc),
            is_mock=False,
            status=ProviderStatus.ok,
        )
        datapoints = [
            FundamentalDataPoint(
                field_name="highlights.market_cap_mln",
                value=2923441.22,
                unit="USD_m",
                as_of="2026-06-29",
                source_tier=SourceTier.T5_api_aggregator,
                source_name="EODHD fundamentals — AAPL.US",
                data_quality=DataQuality.B_single_credible,
            ),
            FundamentalDataPoint(
                field_name="valuation.enterprise_value_mln",
                value=2956099.76,
                unit="USD_m",
                as_of="2026-06-29",
                source_tier=SourceTier.T5_api_aggregator,
                source_name="EODHD fundamentals — AAPL.US",
                data_quality=DataQuality.B_single_credible,
            ),
            FundamentalDataPoint(
                field_name="highlights.revenue_ttm_mln",
                value=400869.99,
                unit="USD_m",
                as_of="2026-06-29",
                source_tier=SourceTier.T5_api_aggregator,
                source_name="EODHD fundamentals — AAPL.US",
                data_quality=DataQuality.B_single_credible,
            ),
            FundamentalDataPoint(
                field_name="valuation.ev_ebitda",
                value=21.83,
                unit="x",
                as_of="2026-06-29",
                source_tier=SourceTier.T5_api_aggregator,
                source_name="EODHD fundamentals — AAPL.US",
                data_quality=DataQuality.B_single_credible,
            ),
        ]
        return FundamentalsData(ticker="AAPL", exchange="US", datapoints=datapoints, meta=meta)

    def test_snapshot_includes_fundamentals_summary(self):
        from app.workflows.snapshot_builder import build_company_snapshot
        profile = self._make_mock_profile()
        fundamentals = self._make_mock_fundamentals()
        snapshot = build_company_snapshot(profile=profile, prices=None, fundamentals=fundamentals)
        assert snapshot["fundamentals_summary"] is not None
        fs = snapshot["fundamentals_summary"]
        assert "market_cap_mln" in fs
        assert fs["market_cap_mln"] == pytest.approx(2923441.22, abs=1)
        assert fs["source_tier"] == "T5_api_aggregator"

    def test_snapshot_without_fundamentals_has_none_summary(self):
        from app.workflows.snapshot_builder import build_company_snapshot
        profile = self._make_mock_profile()
        snapshot = build_company_snapshot(profile=profile, prices=None, fundamentals=None)
        assert snapshot["fundamentals_summary"] is None

    def test_schema_draft_includes_snapshot_financials_when_fundamentals(self):
        from app.workflows.snapshot_builder import build_company_snapshot, build_schema_draft
        profile = self._make_mock_profile()
        fundamentals = self._make_mock_fundamentals()
        snapshot = build_company_snapshot(profile=profile, prices=None, fundamentals=fundamentals)
        draft = build_schema_draft(
            report_id="test-id",
            snapshot=snapshot,
            profile=profile,
            prices=None,
            fundamentals=fundamentals,
        )
        assert "_phase13_fundamentals_available" in draft
        assert draft["_phase13_fundamentals_available"] is True
        assert "snapshot_financials" in draft
        sf = draft["snapshot_financials"]
        assert "market_cap_usd_m" in sf
        # Each field must be a datapoint dict
        for field_key, dp in sf.items():
            assert isinstance(dp, dict), f"{field_key} is not a datapoint dict"
            assert "value" in dp
            assert "source_tier" in dp
            assert "data_quality" in dp
            assert "as_of" in dp
            assert dp["source_tier"] == "T5_api_aggregator"

    def test_schema_draft_without_fundamentals_no_snapshot_financials(self):
        from app.workflows.snapshot_builder import build_company_snapshot, build_schema_draft
        profile = self._make_mock_profile()
        snapshot = build_company_snapshot(profile=profile, prices=None, fundamentals=None)
        draft = build_schema_draft(
            report_id="test-id",
            snapshot=snapshot,
            profile=profile,
            prices=None,
            fundamentals=None,
        )
        assert "snapshot_financials" not in draft
        assert "_phase13_fundamentals_available" not in draft


# ---------------------------------------------------------------------------
# WorkflowRunResponse: Phase 13 fields
# ---------------------------------------------------------------------------


class TestWorkflowRunResponsePhase13Fields:
    def test_phase13_fields_present_in_schema(self):
        from app.schemas.agent import WorkflowRunResponse
        fields = WorkflowRunResponse.model_fields
        assert "fundamentals_available" in fields
        assert "fundamentals_warnings" in fields

    def test_response_defaults(self):
        import uuid

        from app.schemas.agent import WorkflowRunResponse
        r = WorkflowRunResponse(
            agent_run_id=uuid.uuid4(),
            draft_report_id=None,
            status="completed",
            summary="test",
            workflow_name="company_analysis",
        )
        assert r.fundamentals_available is None
        assert r.fundamentals_warnings == []


# ---------------------------------------------------------------------------
# FinancialDataService: get_fundamentals delegation
# ---------------------------------------------------------------------------


class TestFinancialDataServiceGetFundamentals:
    @pytest.mark.asyncio
    async def test_get_fundamentals_delegates_to_provider(self, aapl_fundamentals_payload):
        from app.integrations.financial_data_service import FinancialDataService

        with patch.dict(os.environ, {"EODHD_API_KEY": "test-key"}):
            svc = FinancialDataService(provider_name="eodhd")
            with patch.object(svc._provider, "_get_json", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = aapl_fundamentals_payload
                fundamentals = await svc.get_fundamentals("AAPL", "NASDAQ")

        assert fundamentals.ticker == "AAPL"
        assert len(fundamentals.datapoints) > 0

    @pytest.mark.asyncio
    async def test_mock_provider_fundamentals_returns_mock_data(self):
        """Mock provider returns deterministic mock fundamentals (no API key required)."""
        from app.integrations.financial_data_provider import FundamentalsData
        from app.integrations.financial_data_service import FinancialDataService

        svc = FinancialDataService(provider_name="mock")
        fundamentals = await svc.get_fundamentals("ACME")
        assert isinstance(fundamentals, FundamentalsData)
        assert fundamentals.ticker == "ACME"
        assert fundamentals.meta.is_mock is True
        assert len(fundamentals.datapoints) > 0
