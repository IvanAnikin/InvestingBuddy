"""
Phase 19.1 — Free Real Data Provider Stack.

All tests run OFFLINE — no network calls, no API keys.
Live/integration tests are in test_integration_live_providers.py (opt-in only).

Coverage:
  EodhdPriceOnlyProvider:
    - No key → not_configured status
    - get_fundamentals raises NotImplementedError
    - price-only warning in metadata
    - get_company_profile returns stub without calling /fundamentals
    - get_price_history with mocked HTTP response

  SecEdgarFundamentalsProvider (parse_company_facts):
    - Revenue parsed correctly from fixture
    - Net income parsed correctly
    - EPS basic + diluted parsed correctly
    - All balance sheet and cash flow items parsed
    - Missing concept produces warning, not exception
    - Most recent annual entry is selected (not quarterly)
    - Dollar values scaled to millions
    - Source tier is T2_regulator_or_gov for all datapoints
    - Data quality is B_single_credible for all datapoints
    - Empty facts dict produces all-warnings, no crash
    - CIK resolution from pre-loaded index

  TrendSignalEngine:
    - Insufficient price history (<30 points) → insufficient_price_history label
    - Positive return signals → positive_momentum_candidate
    - Negative return signals → negative_momentum
    - Mixed signals → neutral_momentum
    - 1M/3M/6M returns computed correctly
    - MA50 and MA200 deviations computed correctly
    - Relative strength computed when benchmark provided
    - No BUY/SELL/HOLD/WATCH in any label or field

  FreeRealSnapshot composer:
    - is_mock=False when real price_data provided
    - is_mock=True when both sources are None
    - Warnings present when fundamentals missing
    - Warnings present when price data missing
    - Trend signals computed from price data
    - to_dict() serializes without error
    - to_dict() contains no BUY/SELL/HOLD/WATCH/price_target/fair_value

  NewsCatalystProvider:
    - NullNewsCatalystProvider returns empty events with warning
    - SecEdgar8KProvider: _parse_8k_filings parses fixture correctly
    - SecEdgar8KProvider: no CIK → returns warning, not crash
    - SecEdgar8KProvider: 8-K events extracted; non-8K forms excluded
    - CatalystEvent source_tier is T2_regulator_or_gov

  FinancialDataService registry:
    - "eodhd_price_only" resolves to EodhdPriceOnlyProvider
    - "free_real" resolves to FreeRealProvider
    - "eodhd_free_real" resolves to EodhdFreeRealProvider
    - "sec_edgar_fundamentals" resolves to SecEdgarFundamentalsProvider
    - all new providers instantiate without error

  Forbidden terms:
    - No BUY/SELL/HOLD/WATCH in any output
    - No "price target" or "fair value" in any output
    - No "upside" or "downside" percentage in any output
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures"

_FORBIDDEN_TERMS = {
    "BUY", "SELL", "HOLD", "WATCH",
    "price target", "fair value", "upside", "downside",
    "SHORTLIST", "REJECT",
}


def load_fixture(name: str) -> dict:
    with open(FIXTURE_DIR / name) as f:
        return json.load(f)


# ============================================================================
# Helper: build synthetic price history
# ============================================================================


def _make_price_points(n: int, start_price: float = 100.0, drift: float = 0.001):
    """Generate n synthetic price points for testing."""
    from datetime import date, timedelta

    from app.integrations.financial_data_provider import (
        PriceHistoryData,
        PricePoint,
        ProviderResponseMetadata,
        ProviderStatus,
        SourceTier,
    )

    points = []
    price = start_price
    base_date = date(2024, 1, 2)
    for i in range(n):
        price = price * (1 + drift)
        d = base_date + timedelta(days=i)
        points.append(PricePoint(
            date=d.isoformat(),
            open=price * 0.99,
            high=price * 1.01,
            low=price * 0.98,
            close=price,
            volume=1_000_000,
            adjusted_close=price,
        ))
    meta = ProviderResponseMetadata(
        provider_name="mock_price",
        source_tier=SourceTier.T5_api_aggregator,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
    )
    return PriceHistoryData(
        ticker="TEST",
        exchange="NYSE",
        currency="USD",
        price_points=points,
        source_url="http://mock",
        meta=meta,
    )


# ============================================================================
# EodhdPriceOnlyProvider tests
# ============================================================================


class TestEodhdPriceOnlyProvider:
    def test_no_key_returns_not_configured(self):
        from app.integrations.providers.eodhd_price_only_provider import EodhdPriceOnlyProvider
        env = {k: v for k, v in os.environ.items() if k != "EODHD_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            provider = EodhdPriceOnlyProvider()
        from app.integrations.financial_data_provider import ProviderStatus
        assert provider.get_provider_status() == ProviderStatus.not_configured

    def test_key_present_returns_ok(self):
        from app.integrations.financial_data_provider import ProviderStatus
        from app.integrations.providers.eodhd_price_only_provider import EodhdPriceOnlyProvider
        with patch.dict(os.environ, {"EODHD_API_KEY": "testkey123"}, clear=False):
            provider = EodhdPriceOnlyProvider()
        assert provider.get_provider_status() == ProviderStatus.ok

    def test_get_fundamentals_raises_not_implemented(self):
        from app.integrations.providers.eodhd_price_only_provider import EodhdPriceOnlyProvider
        provider = EodhdPriceOnlyProvider()
        with pytest.raises(NotImplementedError) as exc_info:
            asyncio.run(
                provider.get_fundamentals("AAPL")
            )
        msg = str(exc_info.value).lower()
        assert "paid subscription" in msg or "not provide fundamentals" in msg

    def test_get_company_profile_returns_stub_without_network(self):
        from app.integrations.providers.eodhd_price_only_provider import (
            EodhdPriceOnlyProvider,
        )
        provider = EodhdPriceOnlyProvider()
        profile = asyncio.run(provider.get_company_profile("AAPL", "NASDAQ"))
        assert profile.ticker == "AAPL"
        assert profile.meta.note is not None
        assert "fundamentals unavailable" in profile.meta.note
        assert profile.meta.is_mock is False

    def test_price_only_warning_contains_required_text(self):
        from app.integrations.providers.eodhd_price_only_provider import PRICE_ONLY_WARNING
        assert "fundamentals unavailable" in PRICE_ONLY_WARNING
        assert "free plan" in PRICE_ONLY_WARNING.lower() or "free" in PRICE_ONLY_WARNING

    def test_provider_name(self):
        from app.integrations.providers.eodhd_price_only_provider import EodhdPriceOnlyProvider
        assert EodhdPriceOnlyProvider().provider_name == "eodhd_price_only"

    def test_capabilities_only_price_history(self):
        from app.integrations.financial_data_provider import ProviderCapability
        from app.integrations.providers.eodhd_price_only_provider import EodhdPriceOnlyProvider
        caps = EodhdPriceOnlyProvider().get_supported_capabilities()
        assert ProviderCapability.price_history in caps
        assert ProviderCapability.fundamentals not in caps

    def test_get_price_history_parses_json_response(self):
        from app.integrations.providers.eodhd_price_only_provider import EodhdPriceOnlyProvider
        eod_payload = load_fixture("eodhd_eod_aapl.json")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = eod_payload
        mock_resp.raise_for_status.return_value = None

        async def _mock_get(url, params=None):
            return mock_resp

        provider = EodhdPriceOnlyProvider()
        provider._api_key = "testkey"

        async def run():
            with patch.object(provider, "_get_json", new=AsyncMock(return_value=eod_payload)):
                return await provider.get_price_history("AAPL", "US")

        result = asyncio.run(run())
        assert result.ticker == "AAPL"
        assert len(result.price_points) > 0
        assert result.meta.is_mock is False
        assert result.meta.source_tier.value == "T5_api_aggregator"
        assert "fundamentals unavailable" in (result.meta.note or "")


# ============================================================================
# SEC EDGAR fundamentals parser tests
# ============================================================================


class TestSecEdgarFundamentalsParser:
    @pytest.fixture
    def aapl_facts(self) -> dict:
        return load_fixture("sec_companyfacts_aapl.json")

    def test_revenue_parsed_from_fixture(self, aapl_facts):
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        dps, warnings = parse_company_facts(aapl_facts, "AAPL", "320193")
        rev = next((dp for dp in dps if dp.field_name == "sec_edgar.revenue"), None)
        assert rev is not None
        # 2023 revenue: 383_285_000_000 → 383285.0 million
        assert abs(rev.value - 383285.0) < 0.1
        assert rev.unit == "USD_m"
        # FundamentalDataPoint uses use_enum_values=True → source_tier is stored as str
        assert rev.source_tier == "T2_regulator_or_gov"

    def test_net_income_parsed(self, aapl_facts):
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        dps, _ = parse_company_facts(aapl_facts, "AAPL", "320193")
        ni = next((dp for dp in dps if dp.field_name == "sec_edgar.net_income"), None)
        assert ni is not None
        assert abs(ni.value - 96995.0) < 0.1

    def test_eps_basic_parsed(self, aapl_facts):
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        dps, _ = parse_company_facts(aapl_facts, "AAPL", "320193")
        eps = next((dp for dp in dps if dp.field_name == "sec_edgar.eps_basic"), None)
        assert eps is not None
        assert abs(eps.value - 6.16) < 0.01
        assert eps.unit == "USD"

    def test_eps_diluted_parsed(self, aapl_facts):
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        dps, _ = parse_company_facts(aapl_facts, "AAPL", "320193")
        eps = next((dp for dp in dps if dp.field_name == "sec_edgar.eps_diluted"), None)
        assert eps is not None
        assert abs(eps.value - 6.13) < 0.01

    def test_total_assets_parsed(self, aapl_facts):
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        dps, _ = parse_company_facts(aapl_facts, "AAPL", "320193")
        dp = next((d for d in dps if d.field_name == "sec_edgar.total_assets"), None)
        assert dp is not None
        assert abs(dp.value - 352583.0) < 0.1

    def test_total_liabilities_parsed(self, aapl_facts):
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        dps, _ = parse_company_facts(aapl_facts, "AAPL", "320193")
        dp = next((d for d in dps if d.field_name == "sec_edgar.total_liabilities"), None)
        assert dp is not None
        assert abs(dp.value - 290437.0) < 0.1

    def test_shareholders_equity_parsed(self, aapl_facts):
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        dps, _ = parse_company_facts(aapl_facts, "AAPL", "320193")
        dp = next((d for d in dps if d.field_name == "sec_edgar.shareholders_equity"), None)
        assert dp is not None
        assert abs(dp.value - 62146.0) < 0.1

    def test_operating_cash_flow_parsed(self, aapl_facts):
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        dps, _ = parse_company_facts(aapl_facts, "AAPL", "320193")
        dp = next((d for d in dps if d.field_name == "sec_edgar.operating_cash_flow"), None)
        assert dp is not None
        assert abs(dp.value - 110543.0) < 0.1

    def test_long_term_debt_parsed(self, aapl_facts):
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        dps, _ = parse_company_facts(aapl_facts, "AAPL", "320193")
        dp = next((d for d in dps if d.field_name == "sec_edgar.long_term_debt"), None)
        assert dp is not None
        assert abs(dp.value - 95281.0) < 0.1

    def test_short_term_debt_parsed(self, aapl_facts):
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        dps, _ = parse_company_facts(aapl_facts, "AAPL", "320193")
        dp = next((d for d in dps if d.field_name == "sec_edgar.short_term_debt"), None)
        assert dp is not None
        assert abs(dp.value - 15812.0) < 0.1

    def test_most_recent_annual_selected(self, aapl_facts):
        """Revenue has 3 annual entries; most recent (2023) must be selected."""
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        dps, _ = parse_company_facts(aapl_facts, "AAPL", "320193")
        rev = next((dp for dp in dps if dp.field_name == "sec_edgar.revenue"), None)
        assert rev is not None
        assert "2023" in rev.as_of

    def test_missing_concept_produces_warning(self):
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        empty_facts = {"facts": {"us-gaap": {}}}
        dps, warnings = parse_company_facts(empty_facts, "TEST", "999999")
        assert len(dps) == 0
        assert len(warnings) > 0
        assert any("revenue" in w.lower() for w in warnings)
        assert any("not found" in w for w in warnings)

    def test_all_datapoints_are_t2_source_tier(self, aapl_facts):
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        dps, _ = parse_company_facts(aapl_facts, "AAPL", "320193")
        # use_enum_values=True → source_tier stored as string
        for dp in dps:
            assert dp.source_tier == "T2_regulator_or_gov", (
                f"Expected T2 for {dp.field_name}, got {dp.source_tier}"
            )

    def test_all_datapoints_are_b_single_credible(self, aapl_facts):
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        dps, _ = parse_company_facts(aapl_facts, "AAPL", "320193")
        # use_enum_values=True → data_quality stored as string
        for dp in dps:
            assert dp.data_quality == "B_single_credible"

    def test_cik_resolution_from_index(self):
        from app.integrations.providers.sec_edgar_fundamentals import SecEdgarFundamentalsProvider
        tickers_index = load_fixture("sec_tickers_mini.json")
        provider = SecEdgarFundamentalsProvider()
        provider._load_cik_index_sync(tickers_index)
        # Phase 27.1A: the cache is keyed by (ticker, exchange). The SEC index
        # is US-registrant data, so it loads against the US venue and cannot
        # answer for the same ticker on a foreign exchange.
        assert provider._cik_cache[("AAPL", "US")] == "320193"
        assert provider._cik_cache[("MSFT", "US")] == "789019"
        assert provider._cik_cache[("GOOGL", "US")] == "1652044"


# ============================================================================
# TrendSignalEngine tests
# ============================================================================


class TestTrendSignalEngine:
    def test_insufficient_history_below_30_points(self):
        from app.integrations.trend_signal_engine import LABEL_INSUFFICIENT, compute_trend_signals
        price_data = _make_price_points(15)
        result = compute_trend_signals(price_data)
        assert result.momentum_label == LABEL_INSUFFICIENT
        assert result.return_1m is None
        assert len(result.data_warnings) > 0

    def test_positive_momentum_with_rising_prices(self):
        from app.integrations.trend_signal_engine import LABEL_POSITIVE, compute_trend_signals
        # drift=0.003 → strongly rising prices over 250 days
        price_data = _make_price_points(250, drift=0.003)
        result = compute_trend_signals(price_data)
        assert result.momentum_label == LABEL_POSITIVE

    def test_negative_momentum_with_falling_prices(self):
        from app.integrations.trend_signal_engine import LABEL_NEGATIVE, compute_trend_signals
        # drift=-0.003 → strongly falling prices
        price_data = _make_price_points(250, drift=-0.003)
        result = compute_trend_signals(price_data)
        assert result.momentum_label == LABEL_NEGATIVE

    def test_returns_are_computed(self):
        from app.integrations.trend_signal_engine import compute_trend_signals
        price_data = _make_price_points(250, drift=0.001)
        result = compute_trend_signals(price_data)
        assert result.return_1m is not None
        assert result.return_3m is not None
        assert result.return_6m is not None

    def test_ma50_deviation_computed(self):
        from app.integrations.trend_signal_engine import compute_trend_signals
        price_data = _make_price_points(250, drift=0.001)
        result = compute_trend_signals(price_data)
        assert result.pct_above_ma50 is not None

    def test_ma200_deviation_computed(self):
        from app.integrations.trend_signal_engine import compute_trend_signals
        price_data = _make_price_points(250, drift=0.001)
        result = compute_trend_signals(price_data)
        assert result.pct_above_ma200 is not None

    def test_source_tier_is_t6(self):
        from app.integrations.trend_signal_engine import compute_trend_signals
        price_data = _make_price_points(100, drift=0.001)
        result = compute_trend_signals(price_data)
        assert result.source_tier == "T6_model_estimate"

    def test_no_forbidden_terms_in_labels(self):
        from app.integrations.trend_signal_engine import (
            LABEL_INSUFFICIENT,
            LABEL_NEGATIVE,
            LABEL_NEUTRAL,
            LABEL_POSITIVE,
        )
        all_labels = [LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_NEUTRAL, LABEL_INSUFFICIENT]
        for label in all_labels:
            for term in ["BUY", "SELL", "HOLD", "WATCH"]:
                assert term not in label.upper(), (
                    f"Forbidden term '{term}' found in label '{label}'"
                )

    def test_relative_strength_with_benchmark(self):
        from app.integrations.trend_signal_engine import compute_trend_signals
        asset_prices = _make_price_points(250, drift=0.003)
        bench_prices = _make_price_points(250, drift=0.001)
        result = compute_trend_signals(asset_prices, benchmark_prices=bench_prices)
        assert result.relative_strength is not None
        assert result.relative_strength > 1.0  # asset outperforms benchmark

    def test_relative_strength_none_without_benchmark(self):
        from app.integrations.trend_signal_engine import compute_trend_signals
        price_data = _make_price_points(250, drift=0.001)
        result = compute_trend_signals(price_data)
        assert result.relative_strength is None

    def test_computed_at_is_set(self):
        from app.integrations.trend_signal_engine import compute_trend_signals
        price_data = _make_price_points(50, drift=0.0)
        result = compute_trend_signals(price_data)
        assert result.computed_at != ""


# ============================================================================
# FreeRealSnapshot composer tests
# ============================================================================


class TestFreeRealSnapshotComposer:
    @pytest.fixture
    def identity(self):
        from app.integrations.free_real_snapshot import CompanyIdentity
        return CompanyIdentity(
            ticker="AAPL",
            legal_name="Apple Inc.",
            exchange="NASDAQ",
            country_domicile="US",
            sector="Technology",
            sec_cik="320193",
        )

    @pytest.fixture
    def real_price_data(self):
        return _make_price_points(250, drift=0.001)

    @pytest.fixture
    def real_fundamentals_data(self):
        from app.integrations.financial_data_provider import (
            FundamentalsData,
            ProviderResponseMetadata,
            ProviderStatus,
            SourceTier,
        )
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        aapl_facts = load_fixture("sec_companyfacts_aapl.json")
        dps, _ = parse_company_facts(aapl_facts, "AAPL", "320193")
        meta = ProviderResponseMetadata(
            provider_name="sec_edgar_fundamentals",
            source_tier=SourceTier.T2_regulator_or_gov,
            retrieved_at=datetime.now(timezone.utc),
            is_mock=False,
            status=ProviderStatus.ok,
        )
        return FundamentalsData(ticker="AAPL", exchange="NASDAQ", datapoints=dps, meta=meta)

    def test_is_mock_false_when_real_price_data(self, identity, real_price_data):
        from app.integrations.free_real_snapshot import compose_free_real_snapshot
        snapshot = asyncio.run(
            compose_free_real_snapshot(identity, price_data=real_price_data)
        )
        assert snapshot.is_mock is False

    def test_is_mock_false_when_real_fundamentals(self, identity, real_fundamentals_data):
        from app.integrations.free_real_snapshot import compose_free_real_snapshot
        snapshot = asyncio.run(
            compose_free_real_snapshot(identity, fundamentals_data=real_fundamentals_data)
        )
        assert snapshot.is_mock is False

    def test_is_mock_true_when_no_data(self, identity):
        from app.integrations.free_real_snapshot import compose_free_real_snapshot
        snapshot = asyncio.run(
            compose_free_real_snapshot(identity)
        )
        assert snapshot.is_mock is True

    def test_warning_when_no_price_data(self, identity):
        from app.integrations.free_real_snapshot import compose_free_real_snapshot
        snapshot = asyncio.run(
            compose_free_real_snapshot(identity)
        )
        assert any("price" in w.lower() for w in snapshot.warnings)

    def test_warning_when_no_fundamentals(self, identity, real_price_data):
        from app.integrations.free_real_snapshot import compose_free_real_snapshot
        snapshot = asyncio.run(
            compose_free_real_snapshot(identity, price_data=real_price_data)
        )
        assert any("cik" in w.lower() or "fundamental" in w.lower() or "sec" in w.lower()
                   for w in snapshot.warnings)

    def test_trend_signals_computed_from_price_data(self, identity, real_price_data):
        from app.integrations.free_real_snapshot import compose_free_real_snapshot
        snapshot = asyncio.run(
            compose_free_real_snapshot(identity, price_data=real_price_data)
        )
        assert snapshot.trend_signals is not None
        assert snapshot.trend_signals.momentum_label is not None

    def test_trend_signals_none_without_price(self, identity):
        from app.integrations.free_real_snapshot import compose_free_real_snapshot
        snapshot = asyncio.run(
            compose_free_real_snapshot(identity)
        )
        assert snapshot.trend_signals is None

    def test_to_dict_serializes(self, identity, real_price_data, real_fundamentals_data):
        from app.integrations.free_real_snapshot import compose_free_real_snapshot
        snapshot = asyncio.run(
            compose_free_real_snapshot(
                identity, price_data=real_price_data, fundamentals_data=real_fundamentals_data
            )
        )
        d = snapshot.to_dict()
        assert isinstance(d, dict)
        assert d["ticker"] == "AAPL"
        assert d["is_mock"] is False
        assert "price_history" in d
        assert "fundamentals" in d
        assert "trend_signals" in d

    def test_to_dict_no_forbidden_terms(self, identity, real_price_data, real_fundamentals_data):
        from app.integrations.free_real_snapshot import compose_free_real_snapshot
        snapshot = asyncio.run(
            compose_free_real_snapshot(
                identity, price_data=real_price_data, fundamentals_data=real_fundamentals_data
            )
        )
        d_str = json.dumps(snapshot.to_dict())
        # Use word-boundary matching: "HOLD" inside "stockholders" is not a recommendation
        for term in _FORBIDDEN_TERMS:
            assert not re.search(rf"\b{term}\b", d_str), (
                f"Forbidden standalone term '{term}' found in snapshot.to_dict() output"
            )

    def test_composed_at_is_set(self, identity):
        from app.integrations.free_real_snapshot import compose_free_real_snapshot
        snapshot = asyncio.run(
            compose_free_real_snapshot(identity)
        )
        assert snapshot.composed_at != ""

    def test_fundamentals_datapoints_in_to_dict(
        self, identity, real_price_data, real_fundamentals_data
    ):
        from app.integrations.free_real_snapshot import compose_free_real_snapshot
        snapshot = asyncio.run(
            compose_free_real_snapshot(
                identity, price_data=real_price_data, fundamentals_data=real_fundamentals_data
            )
        )
        d = snapshot.to_dict()
        fund = d["fundamentals"]
        assert fund is not None
        assert fund["num_datapoints"] > 0
        assert isinstance(fund["datapoints"], list)
        rev_dp = next((x for x in fund["datapoints"] if x["field_name"] == "sec_edgar.revenue"), None)
        assert rev_dp is not None
        assert abs(rev_dp["value"] - 383285.0) < 0.1


# ============================================================================
# NewsCatalystProvider tests
# ============================================================================


class TestNewsCatalystProvider:
    def test_null_provider_returns_empty_with_warning(self):
        from app.integrations.news_catalyst_provider import NullNewsCatalystProvider
        provider = NullNewsCatalystProvider()
        result = asyncio.run(
            provider.get_recent_events("AAPL")
        )
        assert result.events == []
        assert len(result.warnings) > 0
        assert any("no news provider" in w.lower() for w in result.warnings)

    def test_null_provider_name(self):
        from app.integrations.news_catalyst_provider import NullNewsCatalystProvider
        assert NullNewsCatalystProvider().provider_name == "null_news"

    def test_sec_8k_provider_name(self):
        from app.integrations.news_catalyst_provider import SecEdgar8KProvider
        assert SecEdgar8KProvider().provider_name == "sec_edgar_8k"

    def test_parse_8k_filings_extracts_8k_events(self):
        from app.integrations.news_catalyst_provider import _parse_8k_filings
        submissions = {
            "filings": {
                "recent": {
                    "form": ["8-K", "10-K", "8-K", "DEF 14A"],
                    "filingDate": ["2024-01-15", "2024-01-10", "2023-12-01", "2023-11-20"],
                    "primaryDocument": ["doc1.htm", "doc2.htm", "doc3.htm", "doc4.htm"],
                    "accessionNumber": [
                        "0000320193-24-000001",
                        "0000320193-24-000002",
                        "0000320193-23-000099",
                        "0000320193-23-000098",
                    ],
                    "reportDate": ["2024-01-15", "2024-01-09", "2023-11-30", "2023-11-19"],
                }
            }
        }
        events = _parse_8k_filings(submissions, "AAPL", "0000320193", max_events=10)
        # Only 8-K forms, not 10-K or DEF 14A
        assert len(events) == 2
        for event in events:
            assert event.event_type in ("8-K", "8-K/A")
            assert event.source_tier == "T2_regulator_or_gov"
            assert event.is_mock is False

    def test_parse_8k_max_events_respected(self):
        from app.integrations.news_catalyst_provider import _parse_8k_filings
        submissions = {
            "filings": {
                "recent": {
                    "form": ["8-K"] * 20,
                    "filingDate": ["2024-01-15"] * 20,
                    "primaryDocument": ["doc.htm"] * 20,
                    "accessionNumber": [f"0000320193-24-{str(i).zfill(6)}" for i in range(20)],
                    "reportDate": ["2024-01-14"] * 20,
                }
            }
        }
        events = _parse_8k_filings(submissions, "AAPL", "0000320193", max_events=5)
        assert len(events) == 5

    def test_sec_8k_no_cik_returns_warning(self):
        from app.integrations.news_catalyst_provider import SecEdgar8KProvider
        provider = SecEdgar8KProvider()
        result = asyncio.run(
            provider.get_recent_events("AAPL", cik=None)
        )
        assert result.events == []
        assert any("cik" in w.lower() for w in result.warnings)

    def test_catalyst_event_source_tier(self):
        from app.integrations.news_catalyst_provider import _parse_8k_filings
        submissions = {
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "filingDate": ["2024-01-15"],
                    "primaryDocument": ["doc.htm"],
                    "accessionNumber": ["0000320193-24-000001"],
                    "reportDate": ["2024-01-14"],
                }
            }
        }
        events = _parse_8k_filings(submissions, "AAPL", "0000320193", max_events=1)
        assert events[0].source_tier == "T2_regulator_or_gov"

    def test_get_provider_factory_null(self):
        from app.integrations.news_catalyst_provider import (
            NullNewsCatalystProvider,
            get_news_catalyst_provider,
        )
        env = {k: v for k, v in os.environ.items() if k != "NEWS_CATALYST_PROVIDER"}
        with patch.dict(os.environ, env, clear=True):
            provider = get_news_catalyst_provider()
        assert isinstance(provider, NullNewsCatalystProvider)

    def test_get_provider_factory_sec_8k(self):
        from app.integrations.news_catalyst_provider import (
            SecEdgar8KProvider,
            get_news_catalyst_provider,
        )
        provider = get_news_catalyst_provider("sec_8k")
        assert isinstance(provider, SecEdgar8KProvider)


# ============================================================================
# FinancialDataService registry tests
# ============================================================================


class TestFinancialDataServiceRegistry:
    def test_eodhd_price_only_registered(self):
        from app.integrations.financial_data_service import get_provider
        from app.integrations.providers.eodhd_price_only_provider import EodhdPriceOnlyProvider
        provider = get_provider("eodhd_price_only")
        assert isinstance(provider, EodhdPriceOnlyProvider)

    def test_free_real_registered(self):
        from app.integrations.financial_data_service import get_provider
        from app.integrations.providers.free_real_provider import FreeRealProvider
        provider = get_provider("free_real")
        assert isinstance(provider, FreeRealProvider)

    def test_eodhd_free_real_registered(self):
        from app.integrations.financial_data_service import get_provider
        from app.integrations.providers.free_real_provider import EodhdFreeRealProvider
        provider = get_provider("eodhd_free_real")
        assert isinstance(provider, EodhdFreeRealProvider)

    def test_sec_edgar_fundamentals_registered(self):
        from app.integrations.financial_data_service import get_provider
        from app.integrations.providers.sec_edgar_fundamentals import SecEdgarFundamentalsProvider
        provider = get_provider("sec_edgar_fundamentals")
        assert isinstance(provider, SecEdgarFundamentalsProvider)

    def test_unknown_provider_raises(self):
        from app.integrations.financial_data_service import get_provider
        with pytest.raises(ValueError, match="Unknown financial data provider"):
            get_provider("not_a_real_provider_xyz")

    def test_all_new_providers_instantiate(self):
        from app.integrations.financial_data_service import _REGISTRY
        for name in ("eodhd_price_only", "free_real", "eodhd_free_real", "sec_edgar_fundamentals"):
            cls = _REGISTRY.get(name)
            assert cls is not None, f"Provider '{name}' not in _REGISTRY"
            instance = cls()
            assert instance.provider_name is not None

    def test_list_providers_includes_new_providers(self):
        from app.integrations.financial_data_service import FinancialDataService
        svc = FinancialDataService("mock")
        providers = svc.list_providers()
        names = {p["name"] for p in providers}
        assert "free_real" in names
        assert "eodhd_price_only" in names
        assert "eodhd_free_real" in names
        assert "sec_edgar_fundamentals" in names


# ============================================================================
# Forbidden terms across all new module outputs
# ============================================================================


class TestForbiddenTermsAbsent:
    """Verify that no module in this phase outputs investment recommendation terms."""

    def _all_strings_in(self, obj) -> list[str]:
        """Recursively collect all string values in a dict/list."""
        strings = []
        if isinstance(obj, str):
            strings.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                strings.extend(self._all_strings_in(v))
        elif isinstance(obj, list):
            for item in obj:
                strings.extend(self._all_strings_in(item))
        return strings

    def test_trend_signal_labels_forbidden_terms_absent(self):
        from app.integrations.trend_signal_engine import (
            LABEL_INSUFFICIENT,
            LABEL_NEGATIVE,
            LABEL_NEUTRAL,
            LABEL_POSITIVE,
        )
        for term in ["BUY", "SELL", "HOLD", "WATCH"]:
            # The term should not appear as a label value (comments don't count here but
            # label constants must not contain the term)
            for label in [LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_NEUTRAL, LABEL_INSUFFICIENT]:
                assert term not in label.upper()

    def test_eodhd_price_only_metadata_no_forbidden_terms(self):
        from app.integrations.providers.eodhd_price_only_provider import PRICE_ONLY_WARNING
        for term in ["BUY", "SELL", "HOLD", "WATCH"]:
            assert term not in PRICE_ONLY_WARNING.upper()

    def test_sec_edgar_datapoints_note_no_forbidden_terms(self):
        from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
        aapl_facts = load_fixture("sec_companyfacts_aapl.json")
        dps, warnings = parse_company_facts(aapl_facts, "AAPL", "320193")
        all_text = " ".join(
            f"{dp.note or ''} {dp.field_name}" for dp in dps
        ) + " ".join(warnings)
        # Use word-boundary match: "HOLD" inside "stockholders" or "LongTermDebt" is OK
        for term in ["BUY", "SELL", "HOLD", "WATCH"]:
            assert not re.search(rf"\b{term}\b", all_text.upper()), (
                f"Forbidden standalone term '{term}' found in SEC datapoint text"
            )
