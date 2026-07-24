"""
Phase 25 — Real Market Candidate Discovery tests.

All tests run OFFLINE: the per-ticker signal extractor and the analysis workflow
are injected/patched, so no provider snapshot, trend signal, catalyst discovery,
GDELT/news, press-release, or SEC call ever hits the network.

Coverage groups:
  - Universe / run creation
  - Run processing (non-blocking failures, status, counts, warnings)
  - Deterministic scoring
  - Signal extraction (state -> signal mapping)
  - API endpoints
  - Safety (no BUY/SELL/HOLD/WATCH, no target/fair value/upside/downside,
    human_review_required, is_public False, safety scan)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.discovery import (
    ALLOWED_CANDIDATE_LABELS,
    DiscoveryCandidate,
    DiscoveryRun,
)
from app.schemas.market_discovery import (
    DiscoveryCandidateDetail,
    DiscoveryCandidateRead,
    DiscoveryRunRead,
)
from app.services import market_discovery_service as mds
from app.services.discovery_scoring_service import score_signal
from app.services.discovery_signal_extractor import (
    ExtractedSignal,
    map_state_to_signal,
)

_NOW = datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# Forbidden terms (candidate labels / explanations must never contain these)
# ---------------------------------------------------------------------------

_FORBIDDEN = [
    "buy",
    "sell",
    "hold",
    "watch",
    "price target",
    "target price",
    "fair value",
    "intrinsic value",
    "upside",
    "downside",
    "undervalued",
    "overvalued",
    "recommendation",
]


# ---------------------------------------------------------------------------
# Signal builders
# ---------------------------------------------------------------------------


def _strong_signal(ticker: str = "AAPL") -> dict:
    return {
        "ticker": ticker,
        "exchange": "US",
        "provider_name": "free_real",
        "is_mock": False,
        "provider_failed": False,
        "error": None,
        "identity": {
            "legal_name": f"{ticker} Inc.",
            "company_name": f"{ticker} Inc.",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "country": "US",
            "lei": "HWUPKR0MPOU8FGXBT394",
            "website": "https://example.com",
        },
        "trend": {
            "momentum_label": "positive_momentum_candidate",
            "return_1m": 5.0,
            "return_3m": 12.0,
            "return_6m": 22.0,
            "pct_above_ma50": 8.0,
            "pct_above_ma200": 15.0,
            "has_price_history": True,
        },
        "fundamentals": {
            "available": True,
            "stale": False,
            "latest_annual_fy": "FY2024",
            "revenue_mln": 383285.0,
            "revenue_growth_yoy_pct": 2.0,
            "net_income_mln": 96995.0,
            "free_cash_flow_mln": 99584.0,
            "total_debt_mln": 111000.0,
            "cash_mln": 61000.0,
        },
        "market": {
            "latest_close": 190.0,
            "week52_high": 199.0,
            "week52_low": 164.0,
            "market_cap_mln": 3000000.0,
            "enterprise_value_mln": 3050000.0,
            "pe_ratio": 31.0,
        },
        "catalyst": {
            "coverage_status": "strong",
            "total_events": 6,
            "positive_count": 3,
            "high_strength_count": 2,
            "primary_or_regulator_event_count": 3,
            "aggregator_only_count": 0,
            "press_release_event_count": 2,
            "news_event_count": 1,
            "filing_event_count": 3,
            "latest_event_date": "2026-07-10",
            "warnings": [],
            "missing_sources": [],
        },
        "source_quality": {
            "overall": "strong",
            "strong_sources_count": 3,
            "weak_sources_count": 0,
            "aggregator_only_count": 0,
            "source_tiers": {"T1_primary_filing": 2, "T2_regulator_or_gov": 3},
        },
        "completeness": {
            "missing_fields": ["fundamentals.ebitda_mln"],
            "missing_info_count": 1,
            "blocking_gap_count": 0,
        },
        "warnings": [],
    }


def _weak_signal(ticker: str = "ZZZ") -> dict:
    return {
        "ticker": ticker,
        "exchange": "US",
        "provider_name": "free_real",
        "is_mock": False,
        "provider_failed": False,
        "error": None,
        "identity": {"legal_name": None, "company_name": ticker, "sector": None},
        "trend": {
            "momentum_label": "negative_momentum",
            "return_1m": -5.0,
            "return_3m": -8.0,
            "return_6m": -12.0,
            "has_price_history": True,
        },
        "fundamentals": {"available": False, "stale": False},
        "market": {},
        "catalyst": {
            "coverage_status": "filings_only",
            "total_events": 1,
            "filing_event_count": 1,
            "aggregator_only_count": 0,
            "warnings": ["limited coverage"],
            "missing_sources": ["company_press_release"],
        },
        "source_quality": {"overall": "weak", "aggregator_only_count": 1},
        "completeness": {
            "missing_fields": ["a", "b", "c"],
            "missing_info_count": 9,
            "blocking_gap_count": 1,
        },
        "warnings": ["thin data"],
    }


def _mock_signal(ticker: str = "MCK") -> dict:
    return {
        "ticker": ticker,
        "exchange": "US",
        "provider_name": "mock",
        "is_mock": True,
        "provider_failed": False,
        "error": None,
        "identity": {"company_name": ticker},
        "trend": {"has_price_history": False},
        "fundamentals": {"available": False},
        "market": {},
        "catalyst": {"total_events": 0},
        "source_quality": {"overall": "insufficient"},
        "completeness": {"missing_fields": [], "missing_info_count": 0, "blocking_gap_count": 0},
        "warnings": [],
    }


def _extracted(signal: dict, *, status: str = "ok", with_report: bool = True) -> ExtractedSignal:
    return ExtractedSignal(
        ticker=signal["ticker"],
        exchange=signal["exchange"],
        provider_name=signal["provider_name"],
        signal=signal,
        status=status,
        error=signal.get("error"),
        analysis_report_id=str(uuid.uuid4()) if with_report else None,
        agent_run_id=str(uuid.uuid4()) if with_report else None,
        schema_valid=False,
        safety_valid=True,
    )


def _fake_extractor(mapping: dict[str, dict], *, fail: set[str] | None = None):
    fail = fail or set()

    async def _extract(db, *, ticker, exchange, provider_name, lookback_days):
        if ticker in fail:
            sig = _weak_signal(ticker)
            sig["provider_failed"] = True
            sig["error"] = "provider unavailable"
            return ExtractedSignal(
                ticker=ticker,
                exchange=exchange,
                provider_name=provider_name,
                signal=sig,
                status="failed",
                error="provider unavailable",
            )
        sig = mapping.get(ticker, _weak_signal(ticker))
        sig = dict(sig)
        sig["ticker"] = ticker
        return _extracted(sig)

    return _extract


def _mock_session_with_capture() -> tuple[AsyncMock, list]:
    added: list = []
    db = AsyncMock()
    db.add = MagicMock(side_effect=lambda o: added.append(o))
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db, added


def _payload(**over):
    from app.schemas.market_discovery import DiscoveryRunCreate

    base = {"universe_source": "manual_tickers", "tickers": ["AAPL", "MSFT"], "exchange": "US"}
    base.update(over)
    return DiscoveryRunCreate(**base)


# ===========================================================================
# Universe / run creation
# ===========================================================================


def test_01_curated_seed_universe_resolves() -> None:
    from app.schemas.market_discovery import DiscoveryRunCreate

    universe = mds.resolve_universe(DiscoveryRunCreate(universe_source="curated_seed"))
    assert len(universe) >= 1
    assert all(u["exchange"] == "US" for u in universe)


def test_02_manual_tickers_universe_resolves() -> None:
    universe = mds.resolve_universe(_payload(tickers=["aapl", "msft", "nvda"]))
    assert [u["ticker"] for u in universe] == ["AAPL", "MSFT", "NVDA"]


def test_03_enforces_max_universe_size() -> None:
    big = [f"T{i}" for i in range(50)]
    with pytest.raises(ValueError, match="exceeds the configured maximum"):
        mds.resolve_universe(_payload(tickers=big))


def test_04_rejects_empty_universe() -> None:
    with pytest.raises(ValueError, match="empty"):
        mds.resolve_universe(_payload(tickers=[]))


def test_05_normalizes_tickers_uppercase_and_dedup() -> None:
    universe = mds.resolve_universe(_payload(tickers=["aapl", "AAPL", " msft "]))
    assert [u["ticker"] for u in universe] == ["AAPL", "MSFT"]


def test_06_defaults_exchange_when_omitted() -> None:
    from app.schemas.market_discovery import DiscoveryRunCreate

    universe = mds.resolve_universe(
        DiscoveryRunCreate(universe_source="manual_tickers", tickers=["AAPL"])
    )
    assert universe[0]["exchange"] == "US"


@pytest.mark.asyncio
async def test_07_run_stores_config_and_human_review_required() -> None:
    db, _ = _mock_session_with_capture()
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL")})
    run = await mds.create_discovery_run(db, _payload(tickers=["AAPL"]), extractor=fake)
    assert run.human_review_required is True
    assert run.config_json["provider_name"] == "free_real"
    assert run.safety_notes["not_investment_advice"] is True


# ===========================================================================
# Run processing
# ===========================================================================


@pytest.mark.asyncio
async def test_08_processes_universe_and_creates_candidates() -> None:
    db, added = _mock_session_with_capture()
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL"), "MSFT": _strong_signal("MSFT")})
    run = await mds.create_discovery_run(db, _payload(tickers=["AAPL", "MSFT"]), extractor=fake)
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    assert run.candidate_count == 2
    assert len(cands) == 2


@pytest.mark.asyncio
async def test_09_per_ticker_failure_does_not_fail_run() -> None:
    db, added = _mock_session_with_capture()
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL")}, fail={"MSFT"})
    run = await mds.create_discovery_run(db, _payload(tickers=["AAPL", "MSFT"]), extractor=fake)
    assert run.status == "completed_with_warnings"
    assert run.error_count == 1
    assert run.candidate_count == 2  # partial candidate still stored


@pytest.mark.asyncio
async def test_10_status_completed_with_warnings_on_partial_failure() -> None:
    db, _ = _mock_session_with_capture()
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL")}, fail={"MSFT"})
    run = await mds.create_discovery_run(db, _payload(tickers=["AAPL", "MSFT"]), extractor=fake)
    assert run.status == "completed_with_warnings"


@pytest.mark.asyncio
async def test_11_counts_update_correctly() -> None:
    db, _ = _mock_session_with_capture()
    fake = _fake_extractor(
        {"AAPL": _strong_signal("AAPL"), "MSFT": _strong_signal("MSFT")}, fail={"NVDA"}
    )
    run = await mds.create_discovery_run(
        db, _payload(tickers=["AAPL", "MSFT", "NVDA"]), extractor=fake
    )
    assert run.processed_count == 3
    assert run.candidate_count == 3
    assert run.error_count == 1


@pytest.mark.asyncio
async def test_12_warnings_persist() -> None:
    db, _ = _mock_session_with_capture()
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL")}, fail={"MSFT"})
    run = await mds.create_discovery_run(db, _payload(tickers=["AAPL", "MSFT"]), extractor=fake)
    assert run.warnings
    assert any("MSFT" in w for w in run.warnings)


@pytest.mark.asyncio
async def test_12b_all_failed_marks_run_failed() -> None:
    db, _ = _mock_session_with_capture()
    fake = _fake_extractor({}, fail={"AAPL", "MSFT"})
    run = await mds.create_discovery_run(db, _payload(tickers=["AAPL", "MSFT"]), extractor=fake)
    assert run.status == "failed"


# ===========================================================================
# Scoring
# ===========================================================================


def test_13_positive_momentum_increases_momentum_score() -> None:
    pos = score_signal(_strong_signal())
    neg = score_signal(_weak_signal())
    assert pos["momentum_score"] > neg["momentum_score"]


def test_14_strong_catalyst_coverage_increases_catalyst_score() -> None:
    strong = score_signal(_strong_signal())
    weak = score_signal(_weak_signal())
    assert strong["catalyst_score"] > weak["catalyst_score"]


def test_15_primary_regulator_evidence_increases_source_quality() -> None:
    high = _strong_signal()
    low = _strong_signal()
    low["source_quality"] = {"overall": "weak", "aggregator_only_count": 3, "source_tiers": {}}
    assert score_signal(high)["source_quality_score"] > score_signal(low)["source_quality_score"]


def test_16_missing_fundamentals_reduce_scores() -> None:
    have = _strong_signal()
    lack = _strong_signal()
    lack["fundamentals"] = {"available": False}
    lack["market"] = {}
    assert score_signal(lack)["fundamentals_score"] < score_signal(have)["fundamentals_score"]


def test_17_mock_data_flagged_and_lowers_grade() -> None:
    res = score_signal(_mock_signal())
    assert res["candidate_score_grade"] == "data_insufficient"
    assert "data_sparse" in res["labels"]


def test_18_candidate_score_within_0_100() -> None:
    for sig in (_strong_signal(), _weak_signal(), _mock_signal()):
        res = score_signal(sig)
        assert 0.0 <= res["candidate_score"] <= 100.0
        for key in (
            "momentum_score",
            "fundamentals_score",
            "catalyst_score",
            "source_quality_score",
            "data_completeness_score",
        ):
            assert 0.0 <= res[key] <= 100.0


async def test_19_ranking_orders_by_score_desc() -> None:
    db, added = _mock_session_with_capture()
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL"), "ZZZ": _weak_signal("ZZZ")})
    await mds.create_discovery_run(db, _payload(tickers=["ZZZ", "AAPL"]), extractor=fake)
    cands = sorted(
        [o for o in added if isinstance(o, DiscoveryCandidate)], key=lambda c: c.rank
    )
    assert cands[0].ticker == "AAPL"
    assert cands[0].candidate_score >= cands[1].candidate_score


def test_20_grade_maps_correctly() -> None:
    assert score_signal(_strong_signal())["candidate_score_grade"] == "high_internal_interest"
    assert score_signal(_mock_signal())["candidate_score_grade"] == "data_insufficient"


def test_21_data_sparse_grade_for_insufficient_data() -> None:
    res = score_signal(_mock_signal())
    assert res["candidate_score_grade"] == "data_insufficient"
    assert "research_incomplete" in res["labels"]


# ===========================================================================
# Signal extraction (state -> signal)
# ===========================================================================


def _final_state() -> dict:
    return {
        "status": "completed",
        "is_mock": False,
        "company_name": "Apple Inc.",
        "fundamentals_available": True,
        "draft_report_id": str(uuid.uuid4()),
        "agent_run_id": str(uuid.uuid4()),
        "schema_valid": False,
        "provider_warnings": ["price provider fallback"],
        "research_team_warnings": [],
        "company_snapshot": {
            "company_identity": {
                "legal_name": "Apple Inc.",
                "country_domicile": "US",
                "lei": "HWUPKR0MPOU8FGXBT394",
            },
            "profile": {
                "sector": "Technology",
                "industry": "Consumer Electronics",
                "website": "https://apple.com",
            },
            "fundamentals_summary": {
                "revenue_usd_m": 383285.0,
                "net_income_usd_m": 96995.0,
                "free_cash_flow_usd_m": 99584.0,
                "total_debt_usd_m": 111000.0,
                "cash_and_equivalents_usd_m": 61000.0,
                "revenue_yoy_growth_pct": 2.0,
                "fiscal_year": 2024,
            },
            "market_metrics_summary": {
                "latest_close": 190.0,
                "week52_high": 199.0,
                "week52_low": 164.0,
                "market_cap_mln": 3000000.0,
                "enterprise_value_mln": 3050000.0,
                "pe_ratio": 31.0,
            },
            "trend_signal_summary": {
                "momentum_label": "positive_momentum_candidate",
                "return_1m": 5.0,
                "return_3m": 12.0,
                "return_6m": 22.0,
                "pct_above_ma50": 8.0,
                "pct_above_ma200": 15.0,
            },
            "missing_fields": ["fundamentals.ebitda_mln"],
        },
        "source_quality_summary": {
            "overall_source_quality": "medium",
            "strong_sources": ["SEC EDGAR"],
            "weak_sources": [],
            "aggregator_only_claims": [],
        },
        "research_completeness_summary": {
            "blocking_gaps": [],
            "missing_required_fields": ["thesis", "valuation"],
        },
        "catalyst_discovery": {
            "coverage_quality": "adequate",
            "warnings": ["one feed stale"],
            "missing_sources": [],
            "summary": {
                "total_events": 4,
                "positive_count": 2,
                "high_strength_count": 1,
                "primary_or_regulator_event_count": 2,
                "aggregator_only_count": 0,
                "press_release_event_count": 1,
                "news_event_count": 1,
                "filing_event_count": 2,
                "latest_event_date": "2026-07-11",
            },
        },
    }


def test_22_captures_trend_signal_fields() -> None:
    sig = map_state_to_signal(_final_state(), ticker="AAPL", exchange="US", provider_name="free_real")
    assert sig["trend"]["momentum_label"] == "positive_momentum_candidate"
    assert sig["trend"]["return_3m"] == 12.0
    assert sig["trend"]["has_price_history"] is True


def test_23_captures_catalyst_counts() -> None:
    sig = map_state_to_signal(_final_state(), ticker="AAPL", exchange="US", provider_name="free_real")
    assert sig["catalyst"]["total_events"] == 4
    assert sig["catalyst"]["press_release_event_count"] == 1
    assert sig["catalyst"]["filing_event_count"] == 2


def test_24_captures_latest_catalyst_date() -> None:
    sig = map_state_to_signal(_final_state(), ticker="AAPL", exchange="US", provider_name="free_real")
    assert sig["catalyst"]["latest_event_date"] == "2026-07-11"


def test_25_captures_source_tier_summary() -> None:
    sig = map_state_to_signal(_final_state(), ticker="AAPL", exchange="US", provider_name="free_real")
    tiers = sig["source_quality"]["source_tiers"]
    assert "T2_regulator_or_gov" in tiers
    assert "T1_primary_filing" in tiers  # from press release event


def test_26_captures_sec_fundamentals() -> None:
    sig = map_state_to_signal(_final_state(), ticker="AAPL", exchange="US", provider_name="free_real")
    assert sig["fundamentals"]["revenue_mln"] == 383285.0
    assert sig["fundamentals"]["net_income_mln"] == 96995.0
    assert sig["fundamentals"]["available"] is True


def test_27_captures_derived_market_metrics() -> None:
    sig = map_state_to_signal(_final_state(), ticker="AAPL", exchange="US", provider_name="free_real")
    assert sig["market"]["market_cap_mln"] == 3000000.0
    assert sig["market"]["pe_ratio"] == 31.0


def test_28_captures_warnings_and_missing_fields() -> None:
    sig = map_state_to_signal(_final_state(), ticker="AAPL", exchange="US", provider_name="free_real")
    assert sig["completeness"]["missing_fields"] == ["fundamentals.ebitda_mln"]
    assert any("stale" in w for w in sig["warnings"])
    assert sig["completeness"]["blocking_gap_count"] == 0


@pytest.mark.asyncio
async def test_28b_extract_signal_uses_injected_runner() -> None:
    from app.services import discovery_signal_extractor as ext

    async def fake_runner(db, **kwargs):
        return _final_state()

    company = MagicMock(id=uuid.uuid4())
    with patch.object(
        ext.company_service, "get_company_by_ticker", AsyncMock(return_value=company)
    ):
        res = await ext.extract_signal(
            AsyncMock(),
            ticker="AAPL",
            exchange="US",
            provider_name="free_real",
            run_analysis=fake_runner,
        )
    assert res.status == "ok"
    assert res.analysis_report_id is not None
    assert res.signal["fundamentals"]["available"] is True


# ===========================================================================
# API
# ===========================================================================


def _run_obj(**over) -> DiscoveryRun:
    run = DiscoveryRun(
        id=over.get("id", uuid.uuid4()),
        status=over.get("status", "completed"),
        provider_name="free_real",
        universe_source="curated_seed",
        universe_count=2,
        requested_tickers=["AAPL", "MSFT"],
        processed_count=2,
        candidate_count=2,
        error_count=0,
        lookback_days=90,
        warnings=[],
        config_json={"provider_name": "free_real"},
        safety_notes={"internal_only": True},
        created_by=None,
        human_review_required=True,
        started_at=_NOW,
        completed_at=_NOW,
    )
    run.created_at = _NOW
    run.updated_at = _NOW
    for k, v in over.items():
        setattr(run, k, v)
    return run


def _candidate_obj(**over) -> DiscoveryCandidate:
    c = DiscoveryCandidate(
        id=over.get("id", uuid.uuid4()),
        discovery_run_id=over.get("discovery_run_id", uuid.uuid4()),
        ticker=over.get("ticker", "AAPL"),
        exchange="US",
        company_name="Apple Inc.",
        legal_name="Apple Inc.",
        sector="Technology",
        industry="Consumer Electronics",
        country="US",
        lei="HWUPKR0MPOU8FGXBT394",
        website="https://apple.com",
        candidate_score=over.get("candidate_score", 88.0),
        candidate_score_grade="high_internal_interest",
        rank=over.get("rank", 1),
        momentum_score=80.0,
        fundamentals_score=90.0,
        catalyst_score=85.0,
        source_quality_score=88.0,
        data_completeness_score=95.0,
        risk_penalty_score=0.0,
        labels_json=["internal_research_candidate", "needs_human_review"],
        score_explanation="Internal prioritization score only.",
        momentum_label="positive_momentum_candidate",
        return_1m=5.0,
        return_3m=12.0,
        return_6m=22.0,
        pct_above_ma50=8.0,
        pct_above_ma200=15.0,
        catalyst_coverage_status="strong",
        latest_catalyst_date=None,
        positive_catalyst_count=3,
        high_strength_catalyst_count=2,
        press_release_event_count=2,
        news_event_count=1,
        filing_event_count=3,
        primary_or_regulator_event_count=3,
        aggregator_only_event_count=0,
        latest_close=190.0,
        market_cap_mln=3000000.0,
        enterprise_value_mln=3050000.0,
        pe_ratio=31.0,
        revenue_mln=383285.0,
        revenue_growth_yoy_pct=2.0,
        net_income_mln=96995.0,
        free_cash_flow_mln=99584.0,
        total_debt_mln=111000.0,
        cash_mln=61000.0,
        latest_annual_fy="FY2024",
        source_quality="strong",
        missing_info_count=1,
        blocking_gap_count=0,
        source_tiers_json={"T2_regulator_or_gov": 3},
        warnings_json=[],
        missing_sources_json=[],
        missing_fields_json=["fundamentals.ebitda_mln"],
        raw_signal_json={"provider_name": "free_real"},
        snapshot_json=None,
        analysis_report_id=None,
        agent_run_id=None,
        human_review_required=True,
        is_public=False,
        safety_valid=True,
        schema_valid=False,
        safety_notes=None,
    )
    c.created_at = _NOW
    c.updated_at = _NOW
    for k, v in over.items():
        setattr(c, k, v)
    return c


@pytest.mark.asyncio
async def test_29_post_run_returns_run(client) -> None:
    # Phase 25.1: POST now creates a pending run and returns immediately; the
    # background task is scheduled (patched to a no-op here).
    run = _run_obj(status="pending", processed_count=0, candidate_count=0)
    with patch.object(
        mds, "create_pending_run", AsyncMock(return_value=run)
    ), patch.object(mds, "process_discovery_run_task", AsyncMock()):
        res = await client.post(
            "/api/v1/market-discovery/runs",
            json={"universe_source": "curated_seed"},
        )
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "pending"
    assert body["human_review_required"] is True
    assert body["processed_count"] == 0
    assert "progress_pct" in body
    assert body.get("message")


@pytest.mark.asyncio
async def test_29b_post_run_rejects_oversized_universe(client) -> None:
    with patch.object(
        mds,
        "create_pending_run",
        AsyncMock(side_effect=ValueError("universe size 50 exceeds the configured maximum")),
    ), patch.object(mds, "process_discovery_run_task", AsyncMock()) as task:
        res = await client.post(
            "/api/v1/market-discovery/runs",
            json={"universe_source": "manual_tickers", "tickers": ["A"]},
        )
    assert res.status_code == 422
    assert "exceeds" in res.json()["detail"]
    # No background work is scheduled when the universe is rejected.
    task.assert_not_called()


@pytest.mark.asyncio
async def test_30_get_runs_lists(client) -> None:
    with patch.object(mds, "list_runs", AsyncMock(return_value=([_run_obj()], 1))):
        res = await client.get("/api/v1/market-discovery/runs")
    assert res.status_code == 200
    assert res.json()["total"] == 1


@pytest.mark.asyncio
async def test_31_get_run_detail(client) -> None:
    run = _run_obj()
    with patch.object(mds, "get_run", AsyncMock(return_value=run)):
        res = await client.get(f"/api/v1/market-discovery/runs/{run.id}")
    assert res.status_code == 200
    assert res.json()["candidate_count"] == 2


@pytest.mark.asyncio
async def test_31b_get_run_not_found(client) -> None:
    with patch.object(mds, "get_run", AsyncMock(return_value=None)):
        res = await client.get(f"/api/v1/market-discovery/runs/{uuid.uuid4()}")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_32_get_candidates_sort_by_score(client) -> None:
    run = _run_obj()
    cands = [_candidate_obj(ticker="AAPL", candidate_score=90.0, rank=1)]
    with patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
        mds, "list_candidates", AsyncMock(return_value=(cands, 1))
    ) as lc:
        res = await client.get(
            f"/api/v1/market-discovery/runs/{run.id}/candidates?sort=candidate_score"
        )
    assert res.status_code == 200
    assert lc.await_args.kwargs["sort"] == "candidate_score"
    assert res.json()["candidates"][0]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_33_get_candidates_supports_filters(client) -> None:
    run = _run_obj()
    with patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
        mds, "list_candidates", AsyncMock(return_value=([], 0))
    ) as lc:
        res = await client.get(
            f"/api/v1/market-discovery/runs/{run.id}/candidates"
            "?grade=high_internal_interest&score_min=50&sector=Technology&has_press_releases=true"
        )
    assert res.status_code == 200
    kwargs = lc.await_args.kwargs
    assert kwargs["grade"] == "high_internal_interest"
    assert kwargs["score_min"] == 50
    assert kwargs["sector"] == "Technology"
    assert kwargs["has_press_releases"] is True


@pytest.mark.asyncio
async def test_34_get_candidate_detail_breakdown(client) -> None:
    cand = _candidate_obj()
    with patch.object(mds, "get_candidate", AsyncMock(return_value=cand)):
        res = await client.get(f"/api/v1/market-discovery/candidates/{cand.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["momentum_score"] == 80.0
    assert body["revenue_mln"] == 383285.0


@pytest.mark.asyncio
async def test_35_run_analysis_from_candidate(client) -> None:
    cand = _candidate_obj()
    report_id = uuid.uuid4()
    run_id = uuid.uuid4()
    result = {
        "candidate_id": cand.id,
        "ticker": "AAPL",
        "status": "completed",
        "analysis_report_id": report_id,
        "agent_run_id": run_id,
        "provider_name": "free_real",
    }
    with patch.object(mds, "run_candidate_analysis", AsyncMock(return_value=result)):
        res = await client.post(
            f"/api/v1/market-discovery/candidates/{cand.id}/run-analysis"
        )
    assert res.status_code == 200
    body = res.json()
    assert body["analysis_report_id"] == str(report_id)
    assert body["human_review_required"] is True


@pytest.mark.asyncio
async def test_36_run_analysis_stores_report_id() -> None:
    """Phase 28A.1 — the candidate links to the FINAL report, not the legacy
    workflow draft. The deterministic draft id is retained separately."""
    from app.schemas.final_report import FinalReportResponse

    db, _ = _mock_session_with_capture()
    candidate = _candidate_obj(raw_signal_json={"provider_name": "free_real"})
    legacy_draft_id = str(uuid.uuid4())
    agent_run_id = str(uuid.uuid4())
    final_report_id = uuid.uuid4()

    async def fake_runner(db_, **kwargs):
        return {
            "status": "completed",
            "draft_report_id": legacy_draft_id,
            "agent_run_id": agent_run_id,
        }

    async def fake_generate(db_, **kwargs):
        return FinalReportResponse(
            report_id=final_report_id,
            schema_valid=True,
            safety_valid=True,
            llm_used=False,
        )

    company = MagicMock(id=uuid.uuid4(), name="Apple Inc.", ticker="AAPL", exchange="US")
    with patch.object(
        mds, "_load_final_report_inputs", AsyncMock(return_value=(None, [], []))
    ), patch.object(mds, "get_candidate", AsyncMock(return_value=candidate)), patch.object(
        mds, "ensure_company", AsyncMock(return_value=company)
    ):
        result = await mds.run_candidate_analysis(
            db,
            candidate.id,
            run_analysis=fake_runner,
            generate_final_report=fake_generate,
        )
    # Candidate now links to the final report, not the legacy draft.
    assert result["analysis_report_id"] == final_report_id
    assert candidate.analysis_report_id == final_report_id
    # The deterministic draft is retained for audit.
    assert str(result["legacy_draft_report_id"]) == legacy_draft_id
    assert result["report"].report_kind == "final"


@pytest.mark.asyncio
async def test_37_api_never_exposes_recommendation_fields(client) -> None:
    cand = _candidate_obj()
    with patch.object(mds, "get_candidate", AsyncMock(return_value=cand)):
        res = await client.get(f"/api/v1/market-discovery/candidates/{cand.id}")
    body_keys = " ".join(res.json().keys()).lower()
    for term in ("recommendation", "rating", "target", "fair_value", "upside", "downside"):
        assert term not in body_keys


# ===========================================================================
# Safety
# ===========================================================================


def test_38_candidate_labels_have_no_forbidden_terms() -> None:
    for sig in (_strong_signal(), _weak_signal(), _mock_signal()):
        res = score_signal(sig)
        for label in res["labels"]:
            assert label in ALLOWED_CANDIDATE_LABELS
            low = label.lower()
            for term in _FORBIDDEN:
                # word-boundary safe: labels are snake_case tokens
                assert term not in low.replace("_", " ").split(), (label, term)


def test_39_explanations_have_no_forbidden_terms() -> None:
    for sig in (_strong_signal(), _weak_signal(), _mock_signal()):
        res = score_signal(sig)
        assert mds.scan_forbidden_terms(res["explanation"]) == []


async def test_40_human_review_required_on_every_candidate() -> None:
    db, added = _mock_session_with_capture()
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL"), "ZZZ": _weak_signal("ZZZ")})
    await mds.create_discovery_run(db, _payload(tickers=["AAPL", "ZZZ"]), extractor=fake)
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    assert cands and all(c.human_review_required for c in cands)


async def test_41_is_public_false_on_every_candidate() -> None:
    db, added = _mock_session_with_capture()
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL")})
    await mds.create_discovery_run(db, _payload(tickers=["AAPL"]), extractor=fake)
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    assert cands and all(c.is_public is False for c in cands)


def test_42_safety_scan_passes_clean_candidate_payload() -> None:
    res = score_signal(_strong_signal())
    payload = {"labels": res["labels"], "score_explanation": res["explanation"]}
    assert mds.scan_candidate_safety(payload) == []


def test_42b_safety_scan_flags_forbidden_text() -> None:
    payload = {
        "labels": ["internal_research_candidate"],
        "score_explanation": "strong buy with upside of 30%",
    }
    violations = mds.scan_candidate_safety(payload)
    assert "strong buy" in violations
    # Phrase semantics: bare "upside" is legitimate research language ("the
    # upside case"), so the gate matches "upside of" / "upside to" instead.
    assert "upside of" in violations


async def test_43_schema_invalid_does_not_block_candidate_creation() -> None:
    db, added = _mock_session_with_capture()
    # extractor reports schema_valid=False (expected at this phase)
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL")})
    run = await mds.create_discovery_run(db, _payload(tickers=["AAPL"]), extractor=fake)
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    assert run.candidate_count == 1
    assert cands[0].schema_valid is False


async def test_44_safety_valid_preserved_when_available() -> None:
    db, added = _mock_session_with_capture()
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL")})
    await mds.create_discovery_run(db, _payload(tickers=["AAPL"]), extractor=fake)
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    assert cands[0].safety_valid is True


def test_45_candidate_read_schema_has_no_recommendation_fields() -> None:
    fields = set(DiscoveryCandidateRead.model_fields.keys()) | set(
        DiscoveryCandidateDetail.model_fields.keys()
    )
    joined = " ".join(fields).lower()
    for term in ("recommendation", "rating", "buy", "sell", "target", "fair_value", "upside"):
        assert term not in joined


def test_46_run_read_schema_validates_from_orm() -> None:
    run = _run_obj()
    dto = DiscoveryRunRead.model_validate(run)
    assert dto.human_review_required is True
    assert "NOT INVESTMENT ADVICE" in dto.disclaimer
