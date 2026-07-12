"""
Phase 19.2 — Real Price and Trend Workflow Integration Fix.

All tests run OFFLINE — no network calls, no API keys required.

Coverage:
  1.  free_real falls back from Stooq failure to EODHD price-only
  2.  free_real continues with SEC-only partial data if both price providers fail
  3.  eodhd_free_real never calls EODHD /fundamentals
  4.  EODHD /eod price data contributes T5_api_aggregator source metadata
  5.  SEC EDGAR contributes T2_regulator_or_gov source metadata
  6.  TrendSignalEngine contributes T6_model_estimate metadata
  7.  requested_provider_name is preserved (not overwritten by sub-provider)
  8.  contributing_providers are recorded
  9.  is_mock=False when SEC-only data exists
  10. is_mock=False when price-only data exists
  11. workflow snapshot adapter includes trend metadata after enrich_snapshot_with_free_real
  12. final report can be generated from partial real data
  13. forbidden recommendation terms are absent from all outputs
  14. human_review_required is true when safety guard triggers
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

_FORBIDDEN_TERMS = {
    "BUY", "SELL", "HOLD", "WATCH",
    "price target", "fair value", "upside", "downside",
    "SHORTLIST", "REJECT",
}

# ============================================================================
# Shared fixtures / helpers
# ============================================================================


def _make_price_points(n: int, start: float = 150.0, drift: float = 0.001):
    """Return a PriceHistoryData with n synthetic price points (is_mock=False, T5)."""
    from app.integrations.financial_data_provider import (
        DataQuality,
        PriceHistoryData,
        PricePoint,
        ProviderResponseMetadata,
        ProviderStatus,
        SourceTier,
    )

    points = []
    price = start
    base = date(2023, 7, 1)
    for i in range(n):
        price *= 1 + drift
        d = base + timedelta(days=i)
        points.append(PricePoint(date=d.isoformat(), close=price, open=price * 0.99,
                                  high=price * 1.01, low=price * 0.98, volume=1_000_000))
    meta = ProviderResponseMetadata(
        provider_name="stooq",
        source_tier=SourceTier.T5_api_aggregator,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
    )
    return PriceHistoryData(
        ticker="AAPL", exchange="NASDAQ", currency="USD",
        price_points=points, source_url="https://stooq.com",
        meta=meta, data_quality=DataQuality.B_single_credible,
    )


def _make_eodhd_price_points(n: int):
    """Return a PriceHistoryData from EODHD price-only (is_mock=False, T5)."""
    from app.integrations.financial_data_provider import (
        DataQuality,
        PriceHistoryData,
        PricePoint,
        ProviderResponseMetadata,
        ProviderStatus,
        SourceTier,
    )
    points = []
    price = 180.0
    base = date(2023, 7, 1)
    for i in range(n):
        price *= 1.001
        d = base + timedelta(days=i)
        points.append(PricePoint(date=d.isoformat(), close=price))
    meta = ProviderResponseMetadata(
        provider_name="eodhd_price_only",
        source_tier=SourceTier.T5_api_aggregator,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
        note="EODHD free plan price-only mode; fundamentals unavailable.",
    )
    return PriceHistoryData(
        ticker="AAPL", exchange="NASDAQ", currency="USD",
        price_points=points, source_url="https://eodhd.com",
        meta=meta, data_quality=DataQuality.B_single_credible,
    )


def _make_sec_fundamentals():
    """Return a FundamentalsData from SEC EDGAR (is_mock=False, T2)."""
    import json as _json
    import pathlib

    from app.integrations.financial_data_provider import (
        FundamentalsData,
        ProviderResponseMetadata,
        ProviderStatus,
        SourceTier,
    )
    from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts

    fixture_path = pathlib.Path(__file__).parent / "fixtures" / "sec_companyfacts_aapl.json"
    with open(fixture_path) as f:
        facts = _json.load(f)
    dps, _ = parse_company_facts(facts, "AAPL", "320193")
    meta = ProviderResponseMetadata(
        provider_name="sec_edgar_fundamentals",
        source_tier=SourceTier.T2_regulator_or_gov,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
    )
    return FundamentalsData(ticker="AAPL", exchange="NASDAQ", datapoints=dps, meta=meta)


def _make_empty_price():
    """Return an empty PriceHistoryData (is_mock=False but 0 points — both providers failed)."""
    from app.integrations.financial_data_provider import (
        DataQuality,
        PriceHistoryData,
        ProviderResponseMetadata,
        ProviderStatus,
        SourceTier,
    )
    meta = ProviderResponseMetadata(
        provider_name="free_real_price_fallback",
        source_tier=SourceTier.T5_api_aggregator,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.error,
        note="Stooq unavailable; EODHD fallback failed; no price data.",
    )
    return PriceHistoryData(
        ticker="AAPL", exchange="NASDAQ", currency="USD",
        price_points=[], source_url=None,
        meta=meta, data_quality=DataQuality.D_weak_or_stale,
    )


# ============================================================================
# Test 1 — free_real falls back from Stooq failure to EODHD price-only
# ============================================================================

_EODHD_PATCH = "app.integrations.providers.free_real_provider.EodhdPriceOnlyProvider"


class TestFreeRealStooqFallback:
    def test_stooq_error_triggers_eodhd_fallback(self):
        """When Stooq raises an exception, get_price_history falls back to EODHD."""
        from app.integrations.financial_data_provider import ProviderStatus
        from app.integrations.providers.free_real_provider import FreeRealProvider

        provider = FreeRealProvider()
        eodhd_result = _make_eodhd_price_points(50)

        async def run():
            with patch.object(
                provider._stooq, "get_price_history",
                new=AsyncMock(side_effect=ConnectionError("Stooq blocked by Azure")),
            ):
                with patch(_EODHD_PATCH) as MockEodhd:
                    mock_inst = MagicMock()
                    mock_inst.get_provider_status.return_value = ProviderStatus.ok
                    mock_inst.get_price_history = AsyncMock(return_value=eodhd_result)
                    MockEodhd.return_value = mock_inst
                    return await provider.get_price_history("AAPL", "NASDAQ")

        result = asyncio.run(run())
        assert result.price_points, "EODHD fallback must return price points"
        assert result.meta.provider_name == "eodhd_price_only"
        assert "Stooq price provider unavailable" in (result.meta.note or "")

    def test_stooq_empty_triggers_eodhd_fallback(self):
        """When Stooq returns 0 price points, fall back to EODHD."""
        from app.integrations.financial_data_provider import ProviderStatus
        from app.integrations.providers.free_real_provider import FreeRealProvider

        provider = FreeRealProvider()
        empty_stooq = _make_price_points(0)  # 0 points
        eodhd_result = _make_eodhd_price_points(50)

        async def run():
            with patch.object(
                provider._stooq, "get_price_history",
                new=AsyncMock(return_value=empty_stooq),
            ):
                with patch(_EODHD_PATCH) as MockEodhd:
                    mock_inst = MagicMock()
                    mock_inst.get_provider_status.return_value = ProviderStatus.ok
                    mock_inst.get_price_history = AsyncMock(return_value=eodhd_result)
                    MockEodhd.return_value = mock_inst
                    return await provider.get_price_history("AAPL", "NASDAQ")

        result = asyncio.run(run())
        assert result.price_points
        assert "0 price points" in (result.meta.note or "")


# ============================================================================
# Test 2 — free_real continues with SEC-only partial data if both price fail
# ============================================================================

class TestFreeRealBothPriceProvidersFail:
    def test_both_fail_returns_empty_price_data_not_raises(self):
        """When both Stooq and EODHD fail, return empty PriceHistoryData (not raise)."""
        from app.integrations.financial_data_provider import ProviderStatus
        from app.integrations.providers.free_real_provider import FreeRealProvider

        provider = FreeRealProvider()

        async def run():
            with patch.object(
                provider._stooq, "get_price_history",
                new=AsyncMock(side_effect=TimeoutError("timeout")),
            ):
                with patch(_EODHD_PATCH) as MockEodhd:
                    mock_inst = MagicMock()
                    mock_inst.get_provider_status.return_value = ProviderStatus.ok
                    mock_inst.get_price_history = AsyncMock(
                        side_effect=RuntimeError("EODHD also down")
                    )
                    MockEodhd.return_value = mock_inst
                    return await provider.get_price_history("AAPL", "NASDAQ")

        result = asyncio.run(run())
        # Must not raise — returns empty data
        assert result.price_points == []
        assert result.meta.provider_name == "free_real_price_fallback"
        assert "No usable price history" in (result.meta.note or "")

    def test_sec_only_snapshot_is_mock_false(self):
        """is_mock=False when SEC EDGAR fundamentals succeed even with no price data."""
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot

        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.",
                                   exchange="NASDAQ", country_domicile="US")
        fund = _make_sec_fundamentals()

        snap = asyncio.run(
            compose_free_real_snapshot(identity, price_data=None, fundamentals_data=fund)
        )
        assert snap.is_mock is False
        assert any("trend signals not computable" in w.lower() for w in snap.warnings)


# ============================================================================
# Test 3 — eodhd_free_real never calls EODHD /fundamentals
# ============================================================================

class TestEodhdFreeRealNoFundamentalsCall:
    def test_get_fundamentals_raises_not_implemented(self):
        """EodhdFreeRealProvider routes fundamentals to SEC EDGAR, never /fundamentals."""
        from app.integrations.providers.free_real_provider import EodhdFreeRealProvider
        provider = EodhdFreeRealProvider()
        # The fundamentals method should call SEC, not EODHD /fundamentals.
        # We verify by checking it's NOT the eodhd._eodhd.get_fundamentals that's called.
        # We mock the SEC provider and confirm it's called.
        sec_fund = _make_sec_fundamentals()
        async def run():
            with patch.object(provider._sec, "get_fundamentals",
                               new=AsyncMock(return_value=sec_fund)):
                with patch.object(provider._eodhd, "get_fundamentals",
                                   new=AsyncMock(side_effect=AssertionError(
                                       "EODHD /fundamentals was called — FORBIDDEN"
                                   ))):
                    return await provider.get_fundamentals("AAPL", "NASDAQ")
        result = asyncio.run(run())
        assert result.meta.provider_name == "sec_edgar_fundamentals"


# ============================================================================
# Test 4 — EODHD /eod price data contributes T5 source metadata
# ============================================================================

class TestEodhdPriceT5SourceMetadata:
    def test_eodhd_price_source_tier_is_t5(self):
        price = _make_eodhd_price_points(50)
        assert price.meta.source_tier.value == "T5_api_aggregator"

    def test_enrich_snapshot_includes_t5_price_metadata(self):
        from app.workflows.snapshot_builder import enrich_snapshot_with_free_real

        fr_dict = {
            "ticker": "AAPL",
            "is_mock": False,
            "provider_stack": "eodhd_free_real",
            "contributing_providers": ["sec_edgar_fundamentals", "eodhd_price_only"],
            "price_history": {
                "num_points": 50,
                "latest_date": "2024-01-01",
                "latest_close": 185.0,
                "earliest_date": "2023-07-01",
                "source_tier": "T5_api_aggregator",
                "provider": "eodhd_price_only",
                "is_mock": False,
            },
            "fundamentals": None,
            "trend_signals": None,
            "warnings": [],
        }
        snapshot = {
            "provider_metadata": {"provider_name": "eodhd_free_real", "is_mock": False},
            "price_history_summary": {"available": False},
            "missing_fields": ["price_history"],
        }
        result = enrich_snapshot_with_free_real(snapshot, fr_dict)
        ph = result["price_history_summary"]
        assert ph["available"] is True
        assert ph["source_tier"] == "T5_api_aggregator"
        assert ph["provider_name"] == "eodhd_price_only"
        assert "price_history" not in result.get("missing_fields", [])


# ============================================================================
# Test 5 — SEC EDGAR contributes T2 source metadata
# ============================================================================

class TestSecEdgarT2SourceMetadata:
    def test_sec_fundamentals_source_tier_is_t2(self):
        fund = _make_sec_fundamentals()
        assert fund.meta.source_tier.value == "T2_regulator_or_gov"

    def test_enrich_snapshot_includes_t2_fundamentals(self):
        from app.workflows.snapshot_builder import enrich_snapshot_with_free_real

        fr_dict = {
            "ticker": "AAPL",
            "is_mock": False,
            "provider_stack": "free_real",
            "contributing_providers": ["sec_edgar_fundamentals"],
            "price_history": None,
            "fundamentals": {
                "num_datapoints": 9,
                "source_tier": "T2_regulator_or_gov",
                "provider": "sec_edgar_fundamentals",
                "is_mock": False,
                "datapoints": [
                    {"field_name": "sec_edgar.revenue", "value": 383285.0, "unit": "USD_m",
                     "as_of": "2023-09-30", "source_tier": "T2_regulator_or_gov",
                     "data_quality": "B_single_credible"},
                ],
            },
            "trend_signals": None,
            "warnings": [],
        }
        snapshot = {
            "provider_metadata": {},
            "missing_fields": [],
        }
        result = enrich_snapshot_with_free_real(snapshot, fr_dict)
        fs = result["fundamentals_summary"]
        assert fs is not None
        assert fs["source_tier"] == "T2_regulator_or_gov"
        assert fs["revenue_usd_m"] == 383285.0


# ============================================================================
# Test 6 — TrendSignalEngine contributes T6 metadata
# ============================================================================

class TestTrendSignalT6Metadata:
    def test_trend_signals_source_tier_t6(self):
        from app.integrations.trend_signal_engine import compute_trend_signals
        price = _make_price_points(250)
        result = compute_trend_signals(price)
        assert result.source_tier == "T6_model_estimate"

    def test_enrich_snapshot_includes_t6_trend_metadata(self):
        from app.workflows.snapshot_builder import enrich_snapshot_with_free_real

        fr_dict = {
            "ticker": "AAPL",
            "is_mock": False,
            "provider_stack": "free_real",
            "contributing_providers": ["stooq", "trend_signal_engine"],
            "price_history": {
                "num_points": 250, "latest_date": "2024-01-01", "latest_close": 185.0,
                "earliest_date": "2023-01-01", "source_tier": "T5_api_aggregator",
                "provider": "stooq", "is_mock": False,
            },
            "fundamentals": None,
            "trend_signals": {
                "momentum_label": "positive_momentum_candidate",
                "return_1m": 3.5,
                "return_3m": 8.2,
                "return_6m": 15.1,
                "pct_above_ma50": 4.1,
                "pct_above_ma200": 12.3,
                "relative_strength": None,
                "source_tier": "T6_model_estimate",
                "data_warnings": [],
            },
            "warnings": [],
        }
        snapshot = {"provider_metadata": {}, "missing_fields": []}
        result = enrich_snapshot_with_free_real(snapshot, fr_dict)
        ts = result.get("trend_signal_summary")
        assert ts is not None
        assert ts["source_tier"] == "T6_model_estimate"
        assert ts["momentum_label"] == "positive_momentum_candidate"
        assert ts["return_1m"] == 3.5
        # Trend signals must NOT contain forbidden labels
        for term in ["BUY", "SELL", "HOLD", "WATCH"]:
            assert term not in ts["momentum_label"].upper()


# ============================================================================
# Test 7 — requested_provider_name is preserved
# ============================================================================

class TestRequestedProviderNamePreserved:
    def test_free_real_snapshot_preserves_provider_stack(self):
        """FreeRealSnapshot.provider_stack must be set to the requested composite name."""
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot

        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.", exchange="NASDAQ")
        price = _make_price_points(50)

        snap = asyncio.run(
            compose_free_real_snapshot(identity, price_data=price, provider_stack="free_real")
        )
        assert snap.provider_stack == "free_real"
        d = snap.to_dict()
        assert d["provider_stack"] == "free_real"

    def test_eodhd_free_real_snapshot_provider_stack(self):
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.", exchange="NASDAQ")
        price = _make_eodhd_price_points(50)
        snap = asyncio.run(
            compose_free_real_snapshot(identity, price_data=price, provider_stack="eodhd_free_real")
        )
        assert snap.provider_stack == "eodhd_free_real"


# ============================================================================
# Test 8 — contributing_providers are recorded
# ============================================================================

class TestContributingProvidersRecorded:
    def test_sec_only_contributing_providers(self):
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.")
        fund = _make_sec_fundamentals()
        snap = asyncio.run(compose_free_real_snapshot(identity, fundamentals_data=fund))
        assert "sec_edgar_fundamentals" in snap.contributing_providers

    def test_price_and_sec_contributing_providers(self):
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.")
        price = _make_price_points(250)
        fund = _make_sec_fundamentals()
        snap = asyncio.run(
            compose_free_real_snapshot(identity, price_data=price, fundamentals_data=fund)
        )
        assert "sec_edgar_fundamentals" in snap.contributing_providers
        assert "stooq" in snap.contributing_providers
        assert "trend_signal_engine" in snap.contributing_providers

    def test_contributing_providers_in_to_dict(self):
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.")
        price = _make_price_points(50)
        snap = asyncio.run(compose_free_real_snapshot(identity, price_data=price))
        d = snap.to_dict()
        assert "contributing_providers" in d
        assert isinstance(d["contributing_providers"], list)


# ============================================================================
# Test 9 — is_mock=False when SEC-only data exists
# ============================================================================

class TestIsMockFalseWithSecOnly:
    def test_is_mock_false_sec_only(self):
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.",
                                   country_domicile="US", sec_cik="320193")
        fund = _make_sec_fundamentals()
        snap = asyncio.run(compose_free_real_snapshot(identity, fundamentals_data=fund))
        assert snap.is_mock is False

    def test_is_mock_true_when_no_real_data(self):
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        identity = CompanyIdentity(ticker="FAKE", legal_name="Fake Corp.")
        snap = asyncio.run(compose_free_real_snapshot(identity))
        assert snap.is_mock is True


# ============================================================================
# Test 10 — is_mock=False when price-only data exists
# ============================================================================

class TestIsMockFalseWithPriceOnly:
    def test_is_mock_false_with_eodhd_price(self):
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.")
        price = _make_eodhd_price_points(50)
        snap = asyncio.run(compose_free_real_snapshot(identity, price_data=price))
        assert snap.is_mock is False

    def test_is_mock_false_with_stooq_price(self):
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.")
        price = _make_price_points(50)
        snap = asyncio.run(compose_free_real_snapshot(identity, price_data=price))
        assert snap.is_mock is False


# ============================================================================
# Test 11 — workflow snapshot adapter includes trend metadata after enrichment
# ============================================================================

class TestWorkflowSnapshotEnrichment:
    def test_enrich_adds_trend_summary_key(self):
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        from app.workflows.snapshot_builder import enrich_snapshot_with_free_real

        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.", exchange="NASDAQ")
        price = _make_price_points(250)
        fund = _make_sec_fundamentals()

        snap = asyncio.run(
            compose_free_real_snapshot(identity, price_data=price, fundamentals_data=fund)
        )
        base_snapshot = {
            "company_identity": {"ticker": "AAPL", "legal_name": "Apple Inc."},
            "provider_metadata": {"provider_name": "free_real", "is_mock": False},
            "price_history_summary": {"available": False},
            "missing_fields": ["price_history"],
            "is_mock": False,
        }
        enriched = enrich_snapshot_with_free_real(base_snapshot, snap.to_dict())

        assert "trend_signal_summary" in enriched
        assert enriched["trend_signal_summary"]["source_tier"] == "T6_model_estimate"
        assert enriched["trend_signal_summary"]["momentum_label"] is not None

    def test_enrich_adds_contributing_providers_to_provider_metadata(self):
        from app.workflows.snapshot_builder import enrich_snapshot_with_free_real

        fr_dict = {
            "ticker": "AAPL", "is_mock": False, "provider_stack": "free_real",
            "contributing_providers": ["sec_edgar_fundamentals", "stooq", "trend_signal_engine"],
            "price_history": None, "fundamentals": None, "trend_signals": None, "warnings": [],
        }
        snap = {"provider_metadata": {}, "missing_fields": []}
        result = enrich_snapshot_with_free_real(snap, fr_dict)
        assert result["provider_metadata"]["contributing_providers"] == [
            "sec_edgar_fundamentals", "stooq", "trend_signal_engine"
        ]

    def test_enrich_does_not_break_non_free_real_snapshot(self):
        """enrich_snapshot_with_free_real is a no-op when free_real_dict is minimal."""
        from app.workflows.snapshot_builder import enrich_snapshot_with_free_real
        snapshot = {
            "company_identity": {"ticker": "AAPL"},
            "provider_metadata": {"provider_name": "mock"},
            "missing_fields": [],
        }
        fr_dict: dict = {
            "contributing_providers": [], "provider_stack": "mock",
            "price_history": None, "fundamentals": None, "trend_signals": None, "warnings": [],
        }
        result = enrich_snapshot_with_free_real(snapshot, fr_dict)
        assert result["provider_metadata"]["contributing_providers"] == []
        assert "trend_signal_summary" not in result


# ============================================================================
# Test 12 — final report can be generated from partial real data
# ============================================================================

class TestPartialRealDataReportGeneration:
    def test_snapshot_with_sec_only_has_required_keys(self):
        """A snapshot built from SEC-only real data must have the keys the report generator needs."""
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        from app.workflows.snapshot_builder import enrich_snapshot_with_free_real

        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.",
                                   exchange="NASDAQ", country_domicile="US")
        fund = _make_sec_fundamentals()
        snap = asyncio.run(compose_free_real_snapshot(identity, fundamentals_data=fund))

        base = {
            "company_identity": {"ticker": "AAPL", "legal_name": "Apple Inc."},
            "provider_metadata": {"provider_name": "free_real", "is_mock": False},
            "price_history_summary": {"available": False},
            "fundamentals_summary": None,
            "missing_fields": ["price_history"],
            "is_mock": False,
        }
        enriched = enrich_snapshot_with_free_real(base, snap.to_dict())

        assert enriched["fundamentals_summary"] is not None
        assert enriched["fundamentals_summary"]["source_tier"] == "T2_regulator_or_gov"
        assert enriched["fundamentals_summary"]["revenue_usd_m"] is not None
        assert enriched["is_mock"] is False

    def test_snapshot_to_dict_json_serializable(self):
        """The enriched snapshot must be JSON-serializable (no datetime objects)."""
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        from app.workflows.snapshot_builder import enrich_snapshot_with_free_real

        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.")
        price = _make_price_points(100)
        snap = asyncio.run(compose_free_real_snapshot(identity, price_data=price))
        base = {
            "company_identity": {"ticker": "AAPL"},
            "provider_metadata": {},
            "missing_fields": [],
        }
        enriched = enrich_snapshot_with_free_real(base, snap.to_dict())
        # Should not raise
        s = json.dumps(enriched)
        assert "AAPL" in s


# ============================================================================
# Test 13 — forbidden recommendation terms absent from all outputs
# ============================================================================

class TestForbiddenTermsAbsent:
    def _check_no_forbidden(self, obj):
        s = json.dumps(obj) if not isinstance(obj, str) else obj
        for term in _FORBIDDEN_TERMS:
            assert not re.search(rf"\b{term}\b", s), (
                f"Forbidden term '{term}' found in output"
            )

    def test_free_real_snapshot_to_dict_no_forbidden_terms(self):
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.")
        price = _make_price_points(250)
        fund = _make_sec_fundamentals()
        snap = asyncio.run(
            compose_free_real_snapshot(identity, price_data=price, fundamentals_data=fund)
        )
        self._check_no_forbidden(snap.to_dict())

    def test_enrich_snapshot_no_forbidden_terms(self):
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        from app.workflows.snapshot_builder import enrich_snapshot_with_free_real
        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.", exchange="NASDAQ")
        price = _make_price_points(250)
        snap = asyncio.run(compose_free_real_snapshot(identity, price_data=price))
        base = {"provider_metadata": {}, "missing_fields": []}
        enriched = enrich_snapshot_with_free_real(base, snap.to_dict())
        self._check_no_forbidden(enriched)

    def test_trend_labels_no_forbidden_terms(self):
        from app.integrations.trend_signal_engine import (
            LABEL_INSUFFICIENT,
            LABEL_NEGATIVE,
            LABEL_NEUTRAL,
            LABEL_POSITIVE,
        )
        for label in [LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_NEUTRAL, LABEL_INSUFFICIENT]:
            for term in ["BUY", "SELL", "HOLD", "WATCH"]:
                assert term not in label.upper(), f"Forbidden '{term}' in label '{label}'"

    def test_stooq_fallback_warning_no_forbidden_terms(self):
        from app.integrations.providers.free_real_provider import _make_empty_price_data
        empty = _make_empty_price_data("AAPL", "NASDAQ", "Stooq blocked; EODHD unavailable.")
        self._check_no_forbidden(empty.meta.note or "")


# ============================================================================
# Test 14 — human_review_required is true when safety guard triggers
# ============================================================================

class TestHumanReviewRequired:
    def test_committee_chair_safety_guard_forces_human_review(self):
        """InvestmentCommitteeChair must set human_review_required=True."""
        from app.agents.analysis_council.investment_committee_chair import (
            run_investment_committee_chair,
        )
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        from app.workflows.snapshot_builder import enrich_snapshot_with_free_real

        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.")
        price = _make_price_points(50)
        snap = asyncio.run(compose_free_real_snapshot(identity, price_data=price))
        base = {"provider_metadata": {}, "missing_fields": []}
        enriched = enrich_snapshot_with_free_real(base, snap.to_dict())

        output = run_investment_committee_chair(
            company_snapshot=enriched,
            bull_case_summary={},
            bear_case_summary={},
            risk_summary={},
            valuation_guard_summary={"valuation_readiness": "not_ready"},
            research_completeness_summary={},
            source_quality_summary={"overall_source_quality": "insufficient"},
            upgraded_citation_validation=None,
            schema_valid=False,
        )
        assert output.human_review_required is True

    def test_free_real_snapshot_does_not_produce_recommendation(self):
        """The full free_real compose+enrich pipeline must never produce a recommendation."""
        from app.integrations.free_real_snapshot import CompanyIdentity, compose_free_real_snapshot
        from app.workflows.snapshot_builder import enrich_snapshot_with_free_real

        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.", exchange="NASDAQ")
        price = _make_price_points(250)
        fund = _make_sec_fundamentals()
        snap = asyncio.run(
            compose_free_real_snapshot(identity, price_data=price, fundamentals_data=fund)
        )
        base = {
            "company_identity": {"ticker": "AAPL"},
            "provider_metadata": {},
            "missing_fields": [],
            "investment_recommendation": None,
        }
        enriched = enrich_snapshot_with_free_real(base, snap.to_dict())
        assert enriched.get("investment_recommendation") is None
        s = json.dumps(enriched)
        for term in ["BUY", "SELL", "HOLD", "WATCH", "price target", "fair value"]:
            assert not re.search(rf"\b{term}\b", s), (
                f"Forbidden term '{term}' found in enriched snapshot"
            )
