"""
Phase 14 — Company Discovery / Screener Tests.

All tests run OFFLINE with no network calls and no API key.

Coverage:
  - ScreeningUniverse: schema creation, theme validation, forbidden themes
  - ScreeningRunCreate: schema creation, market cap range validation
  - ScreeningCandidate: schema creation, status validation
  - CompanyScreener: mock universe filtering (theme, sector, region, exchange, keyword)
  - CompanyScreener: EODHD fixture-backed search results
  - CompanyScreener: source tier stays T5 for EODHD results
  - CompanyScreener: T5 mandatory warning present
  - CompanyScreener: market cap filter rejection
  - CompanyScreener: max_candidates limit respected
  - CompanyScreener: no BUY/SELL/HOLD/WATCH/price_target/fair_value produced
  - CompanyDiscoveryService: create_universe persists record
  - CompanyDiscoveryService: run_screening (mock provider, EODHD fixture)
  - CompanyDiscoveryService: run_screening completes with summary
  - CompanyDiscoveryService: run_screening fails gracefully
  - CompanyDiscoveryService: ambiguous identifier handling
  - CompanyDiscoveryService: missing data handling
  - CompanyDiscoveryService: candidate promotion creates company record
  - CompanyDiscoveryService: candidate promotion reuses existing company
  - CompanyDiscoveryService: promote rejected candidate raises ValueError
  - CompanyDiscoveryService: promote error candidate raises ValueError
  - API: POST /api/v1/discovery/universes
  - API: GET /api/v1/discovery/universes
  - API: POST /api/v1/discovery/runs (universe not found → 404)
  - API: GET /api/v1/discovery/runs
  - API: GET /api/v1/discovery/runs/{id}
  - API: GET /api/v1/discovery/runs/{id}/candidates
  - API: POST /api/v1/discovery/candidates/{id}/promote
  - Safety: no recommendation in any response field
  - Safety: no price_target in any response field
  - Safety: no fair_value in any response field
  - Safety: no upside_percent in any response field
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)


def _make_uuid(n: int) -> uuid.UUID:
    return uuid.UUID(f"{n:032x}")


def _universe_mock(
    uid: uuid.UUID,
    name: str = "Test Universe",
    theme: str = "energy_transition",
    provider_name: str = "mock",
    region: str | None = "Europe",
    exchange: str | None = None,
    sector_filter: str | None = None,
) -> MagicMock:
    m = MagicMock()
    m.id = uid
    m.name = name
    m.description = None
    m.region = region
    m.exchange = exchange
    m.sector_filter = sector_filter
    m.theme = theme
    m.provider_name = provider_name
    m.created_at = _NOW
    return m


def _run_mock(
    run_id: uuid.UUID,
    universe_id: uuid.UUID,
    status: str = "completed",
    provider_name: str = "mock",
) -> MagicMock:
    m = MagicMock()
    m.id = run_id
    m.universe_id = universe_id
    m.status = status
    m.provider_name = provider_name
    m.started_at = _NOW
    m.completed_at = _NOW
    m.parameters_json = {"max_candidates": 10}
    m.summary_json = {
        "total_candidates": 3,
        "status_counts": {"candidate_found": 3},
        "note": "Internal research funnel only.",
    }
    m.error_message = None
    m.created_at = _NOW
    return m


def _candidate_mock(
    cid: uuid.UUID,
    run_id: uuid.UUID,
    ticker: str = "ORSTED",
    exchange: str = "CPH",
    candidate_status: str = "candidate_found",
    source_tier: str = "T6_model_estimate",
    company_id: uuid.UUID | None = None,
) -> MagicMock:
    m = MagicMock()
    m.id = cid
    m.screening_run_id = run_id
    m.company_id = company_id
    m.ticker = ticker
    m.exchange = exchange
    m.name = "Ørsted A/S"
    m.country = "Denmark"
    m.sector = "Utilities"
    m.provider_symbol = f"{ticker}.{exchange}"
    m.market_cap = None
    m.currency = None
    m.candidate_status = candidate_status
    m.discovery_reasons_json = ["Theme match 'energy_transition': keywords found — offshore, wind"]
    m.available_data_json = ["ticker", "exchange", "name", "country", "sector"]
    m.missing_data_json = ["market_cap", "currency", "revenue_ttm"]
    m.source_tier = source_tier
    m.data_quality = "D_weak_or_stale"
    m.warnings_json = ["Mock/synthetic data only — all values are demo placeholders."]
    m.created_at = _NOW
    return m


# ===========================================================================
# Part 1: Pydantic Schemas
# ===========================================================================


class TestScreeningUniverseCreate:
    def test_valid_creation(self) -> None:
        from app.schemas.discovery import ScreeningUniverseCreate

        u = ScreeningUniverseCreate(
            name="EU Energy",
            theme="energy_transition",
            region="Europe",
            provider_name="mock",
        )
        assert u.name == "EU Energy"
        assert u.theme == "energy_transition"

    def test_all_allowed_themes_accepted(self) -> None:
        from app.schemas.discovery import ALLOWED_THEMES, ScreeningUniverseCreate

        for theme in ALLOWED_THEMES:
            u = ScreeningUniverseCreate(name=f"Test {theme}", theme=theme)
            assert u.theme == theme

    def test_invalid_theme_rejected(self) -> None:
        import pydantic

        from app.schemas.discovery import ScreeningUniverseCreate

        with pytest.raises(pydantic.ValidationError) as exc_info:
            ScreeningUniverseCreate(name="Bad", theme="BUY")
        assert "BUY" in str(exc_info.value) or "theme" in str(exc_info.value)

    def test_none_theme_allowed(self) -> None:
        from app.schemas.discovery import ScreeningUniverseCreate

        u = ScreeningUniverseCreate(name="Open universe", theme=None)
        assert u.theme is None

    def test_forbidden_values_not_in_schema(self) -> None:

        from app.schemas.discovery import ScreeningUniverseCreate

        fields = ScreeningUniverseCreate.model_fields
        forbidden = {"BUY", "SELL", "HOLD", "WATCH", "price_target", "fair_value"}
        for field_name in fields:
            assert field_name not in forbidden


class TestScreeningRunCreate:
    def test_valid_creation(self) -> None:
        from app.schemas.discovery import ScreeningRunCreate

        uid = _make_uuid(1)
        r = ScreeningRunCreate(universe_id=uid, max_candidates=25)
        assert r.max_candidates == 25
        assert r.universe_id == uid

    def test_market_cap_range_valid(self) -> None:
        from app.schemas.discovery import ScreeningRunCreate

        r = ScreeningRunCreate(
            universe_id=_make_uuid(1),
            market_cap_min=100.0,
            market_cap_max=5000.0,
        )
        assert r.market_cap_min == 100.0

    def test_market_cap_range_inverted_raises(self) -> None:
        import pydantic

        from app.schemas.discovery import ScreeningRunCreate

        with pytest.raises(pydantic.ValidationError):
            ScreeningRunCreate(
                universe_id=_make_uuid(1),
                market_cap_min=5000.0,
                market_cap_max=100.0,
            )

    def test_max_candidates_default_50(self) -> None:
        from app.schemas.discovery import ScreeningRunCreate

        r = ScreeningRunCreate(universe_id=_make_uuid(1))
        assert r.max_candidates == 50


# ===========================================================================
# Part 2: CompanyScreener (pure logic, no DB)
# ===========================================================================


class TestCompanyScreener:
    def test_screen_mock_energy_transition(self) -> None:
        from app.services.screener import CompanyScreener

        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange=None,
            sector=None,
            theme="energy_transition",
            max_candidates=10,
            provider_name="mock",
        )
        assert len(results) > 0
        assert all(r.candidate_status in {
            "candidate_found", "needs_data", "needs_primary_sources",
            "ready_for_deeper_analysis", "rejected_by_screen", "error"
        } for r in results)

    def test_screen_max_candidates_respected(self) -> None:
        from app.services.screener import CompanyScreener

        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange=None,
            sector=None,
            theme=None,
            max_candidates=2,
            provider_name="mock",
        )
        assert len(results) <= 2

    def test_screen_source_tier_t6_for_mock(self) -> None:
        from app.services.screener import SOURCE_TIER_T6, CompanyScreener

        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange=None,
            sector=None,
            theme="energy_transition",
            max_candidates=10,
            provider_name="mock",
        )
        for r in results:
            assert r.source_tier == SOURCE_TIER_T6, (
                f"Expected T6 for mock provider, got {r.source_tier}"
            )

    def test_screen_source_tier_t5_for_eodhd_results(self) -> None:
        from app.services.screener import SOURCE_TIER_T5, CompanyScreener

        eodhd_results = [
            {
                "Code": "ORSTED",
                "Exchange": "CPH",
                "Name": "Orsted AS",
                "Country": "Denmark",
                "Type": "Common Stock",
                "Currency": "DKK",
            }
        ]
        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange=None,
            sector=None,
            theme="energy_transition",
            max_candidates=10,
            provider_name="eodhd",
            eodhd_search_results=eodhd_results,
        )
        assert len(results) == 1
        assert results[0].source_tier == SOURCE_TIER_T5

    def test_t5_warning_always_present_for_eodhd(self) -> None:
        from app.services.screener import T5_VALIDATION_WARNING, CompanyScreener

        eodhd_results = [
            {
                "Code": "RHM",
                "Exchange": "XETRA",
                "Name": "Rheinmetall AG",
                "Country": "Germany",
                "Type": "Common Stock",
                "Currency": "EUR",
            }
        ]
        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange=None,
            sector=None,
            theme="defense_security",
            max_candidates=10,
            provider_name="eodhd",
            eodhd_search_results=eodhd_results,
        )
        assert len(results) == 1
        assert T5_VALIDATION_WARNING in results[0].warnings

    def test_sector_filter(self) -> None:
        from app.services.screener import CompanyScreener

        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange=None,
            sector="Materials",
            theme=None,
            max_candidates=50,
            provider_name="mock",
        )
        for r in results:
            assert (r.sector or "").lower() == "materials"

    def test_exchange_filter(self) -> None:
        from app.services.screener import CompanyScreener

        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange="XETRA",
            sector=None,
            theme=None,
            max_candidates=50,
            provider_name="mock",
        )
        for r in results:
            assert (r.exchange or "").upper() == "XETRA"

    def test_keyword_filter(self) -> None:
        from app.services.screener import CompanyScreener

        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange=None,
            sector=None,
            theme=None,
            max_candidates=50,
            provider_name="mock",
            keyword_search="copper",
        )
        # All matches should mention copper in name or description
        assert len(results) > 0
        for r in results:
            assert r.candidate_status in {
                "candidate_found", "needs_data", "needs_primary_sources",
                "ready_for_deeper_analysis", "rejected_by_screen", "error"
            }

    def test_market_cap_min_filter_rejects(self) -> None:
        from app.services.screener import CompanyScreener

        # Test market cap filter via _build_candidate (raw mock data has explicit market_cap)
        screener = CompanyScreener()
        raw = {
            "ticker": "SMALL",
            "exchange": "OSE",
            "name": "Small Co",
            "country": "Norway",
            "sector": "Industrials",
            "description": "small industrial",
            "market_cap": 50.0,
            "currency": "NOK",
        }
        candidate = screener._build_candidate(
            raw=raw,
            theme=None,
            source_tier="T6_model_estimate",
            data_quality="D_weak_or_stale",
            market_cap_min=1000.0,
            market_cap_max=None,
        )
        assert candidate.candidate_status == "rejected_by_screen"
        assert "Market cap below minimum" in candidate.discovery_reasons[0]

    def test_market_cap_max_filter_rejects(self) -> None:
        from app.services.screener import CompanyScreener

        screener = CompanyScreener()
        raw = {
            "ticker": "LARGE",
            "exchange": "NYSE",
            "name": "Large Corp",
            "country": "United States",
            "sector": "Industrials",
            "description": "large industrial",
            "market_cap": 999999.0,
            "currency": "USD",
        }
        candidate = screener._build_candidate(
            raw=raw,
            theme=None,
            source_tier="T6_model_estimate",
            data_quality="D_weak_or_stale",
            market_cap_min=None,
            market_cap_max=5000.0,
        )
        assert candidate.candidate_status == "rejected_by_screen"
        assert "Market cap above maximum" in candidate.discovery_reasons[0]

    def test_missing_data_flagged(self) -> None:
        from app.services.screener import CompanyScreener

        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange=None,
            sector=None,
            theme="materials_mining",
            max_candidates=10,
            provider_name="mock",
        )
        for r in results:
            assert len(r.missing_data) > 0, "Missing data should be flagged"

    def test_discovery_reasons_non_empty(self) -> None:
        from app.services.screener import CompanyScreener

        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange=None,
            sector=None,
            theme="energy_transition",
            max_candidates=10,
            provider_name="mock",
        )
        for r in results:
            assert len(r.discovery_reasons) > 0

    def test_no_recommendation_in_candidates(self) -> None:
        from app.services.screener import CompanyScreener

        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange=None,
            sector=None,
            theme=None,
            max_candidates=50,
            provider_name="mock",
        )
        forbidden = {"BUY", "SELL", "HOLD", "WATCH", "price_target", "fair_value", "upside"}
        for r in results:
            assert r.candidate_status not in forbidden
            for reason in r.discovery_reasons:
                for word in forbidden:
                    assert word.lower() not in reason.lower(), (
                        f"Forbidden word '{word}' found in discovery reason: {reason}"
                    )

    def test_eodhd_non_equity_types_filtered(self) -> None:
        from app.services.screener import CompanyScreener

        eodhd_results = [
            {"Code": "SPY", "Exchange": "NYSE", "Name": "SPDR ETF", "Country": "US", "Type": "ETF"},
            {"Code": "ORSTED", "Exchange": "CPH", "Name": "Orsted", "Country": "Denmark", "Type": "Common Stock"},
        ]
        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange=None,
            sector=None,
            theme=None,
            max_candidates=10,
            provider_name="eodhd",
            eodhd_search_results=eodhd_results,
        )
        # ETF should be filtered out; only common stock
        tickers = [r.ticker for r in results]
        assert "ORSTED" in tickers
        assert "SPY" not in tickers

    def test_error_candidate_for_missing_ticker(self) -> None:
        from app.services.screener import CompanyScreener

        screener = CompanyScreener()
        candidate = screener._build_candidate(
            raw={"ticker": "", "exchange": "NYSE", "name": "No Ticker Corp"},
            theme=None,
            source_tier="T6_model_estimate",
            data_quality="D_weak_or_stale",
            market_cap_min=None,
            market_cap_max=None,
        )
        assert candidate.candidate_status == "error"

    def test_all_themes_produce_candidates(self) -> None:
        from app.services.screener import THEME_KEYWORDS, CompanyScreener

        screener = CompanyScreener()
        for theme in THEME_KEYWORDS:
            results = screener.screen(
                region=None,
                exchange=None,
                sector=None,
                theme=theme,
                max_candidates=10,
                provider_name="mock",
            )
            assert len(results) > 0, f"Expected candidates for theme '{theme}'"


# ===========================================================================
# Part 3: CompanyDiscoveryService (with mock DB)
# ===========================================================================


class TestCompanyDiscoveryService:
    @pytest.mark.asyncio
    async def test_create_universe(self) -> None:
        from app.schemas.discovery import ScreeningUniverseCreate
        from app.services import company_discovery_service

        db = AsyncMock()
        uid = _make_uuid(1)
        universe_mock = _universe_mock(uid)

        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock(side_effect=lambda obj: setattr(obj, "id", uid) or None)

        data = ScreeningUniverseCreate(
            name="EU Energy Transition",
            theme="energy_transition",
            region="Europe",
            provider_name="mock",
        )

        with patch.object(
            company_discovery_service, "ScreeningUniverse", side_effect=lambda **kw: universe_mock
        ):
            await company_discovery_service.create_universe(db, data)

        db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_run_screening_mock_provider_completes(self) -> None:
        from app.schemas.discovery import ScreeningRunCreate
        from app.services import company_discovery_service

        uid = _make_uuid(1)
        universe = _universe_mock(uid, theme="energy_transition", provider_name="mock")

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        call_count = [0]

        async def refresh_side_effect(obj: Any) -> None:
            call_count[0] += 1
            if call_count[0] == 1:
                obj.id = run_id
                obj.status = "running"
                obj.universe_id = uid
                obj.provider_name = "mock"
                obj.started_at = _NOW
                obj.completed_at = None
                obj.parameters_json = {}
                obj.summary_json = None
                obj.error_message = None
                obj.created_at = _NOW
            else:
                obj.status = "completed"
                obj.completed_at = _NOW
                obj.summary_json = {"total_candidates": 3}

        db.refresh = AsyncMock(side_effect=refresh_side_effect)

        with patch.object(
            company_discovery_service, "get_universe", AsyncMock(return_value=universe)
        ), patch.object(
            company_discovery_service, "_persist_candidates", AsyncMock(return_value=[])
        ):
            data = ScreeningRunCreate(
                universe_id=uid,
                max_candidates=10,
            )
            result = await company_discovery_service.run_screening(db, data)

        assert result.status in {"completed", "running"}

    @pytest.mark.asyncio
    async def test_run_screening_universe_not_found_raises(self) -> None:
        from app.schemas.discovery import ScreeningRunCreate
        from app.services import company_discovery_service

        db = AsyncMock()

        with patch.object(
            company_discovery_service, "get_universe", AsyncMock(return_value=None)
        ):
            with pytest.raises(ValueError, match="not found"):
                await company_discovery_service.run_screening(
                    db,
                    ScreeningRunCreate(universe_id=_make_uuid(99), max_candidates=10),
                )

    @pytest.mark.asyncio
    async def test_run_screening_with_eodhd_fixture(self) -> None:
        from app.schemas.discovery import ScreeningRunCreate
        from app.services import company_discovery_service

        uid = _make_uuid(1)
        run_id = _make_uuid(2)
        universe = _universe_mock(uid, theme="defense_security", provider_name="eodhd")

        eodhd_results = [
            {
                "Code": "RHM",
                "Exchange": "XETRA",
                "Name": "Rheinmetall AG",
                "Country": "Germany",
                "Type": "Common Stock",
                "Currency": "EUR",
            }
        ]

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        call_count = [0]

        async def refresh_side_effect(obj: Any) -> None:
            call_count[0] += 1
            if call_count[0] == 1:
                obj.id = run_id
                obj.status = "running"
                obj.universe_id = uid
                obj.provider_name = "eodhd"
                obj.started_at = _NOW
                obj.completed_at = None
                obj.parameters_json = {}
                obj.summary_json = None
                obj.error_message = None
                obj.created_at = _NOW

        db.refresh = AsyncMock(side_effect=refresh_side_effect)

        captured_candidates: list = []

        async def mock_persist(db: Any, run_id: Any, inputs: list) -> list:
            captured_candidates.extend(inputs)
            return []

        with patch.object(
            company_discovery_service, "get_universe", AsyncMock(return_value=universe)
        ), patch.object(
            company_discovery_service, "_persist_candidates", mock_persist
        ):
            data = ScreeningRunCreate(universe_id=uid, max_candidates=10)
            await company_discovery_service.run_screening(
                db, data, eodhd_search_results=eodhd_results
            )

        assert len(captured_candidates) == 1
        assert captured_candidates[0].ticker == "RHM"
        assert captured_candidates[0].source_tier == "T5_api_aggregator"

    @pytest.mark.asyncio
    async def test_run_summary_no_recommendation_fields(self) -> None:
        from app.schemas.discovery import ScreeningRunCreate
        from app.services import company_discovery_service

        uid = _make_uuid(1)
        universe = _universe_mock(uid, theme="energy_transition", provider_name="mock")

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        summaries_seen: list[dict] = []

        async def refresh_side_effect(obj: Any) -> None:
            if hasattr(obj, "summary_json") and obj.summary_json:
                summaries_seen.append(obj.summary_json)

        db.refresh = AsyncMock(side_effect=refresh_side_effect)

        with patch.object(
            company_discovery_service, "get_universe", AsyncMock(return_value=universe)
        ), patch.object(
            company_discovery_service, "_persist_candidates", AsyncMock(return_value=[])
        ):
            data = ScreeningRunCreate(universe_id=uid, max_candidates=10)
            await company_discovery_service.run_screening(db, data)

        for summary in summaries_seen:
            summary_str = json.dumps(summary).lower()
            for forbidden in ["buy", "sell", "hold", "price_target", "fair_value", "upside"]:
                assert forbidden not in summary_str, (
                    f"Forbidden word '{forbidden}' found in run summary"
                )

    @pytest.mark.asyncio
    async def test_promote_candidate_creates_company(self) -> None:
        from app.models.company import Company as RealCompany
        from app.services import company_discovery_service

        candidate_id = _make_uuid(10)
        run_id = _make_uuid(2)
        candidate = _candidate_mock(candidate_id, run_id, ticker="ORSTED", exchange="CPH")
        candidate.company_id = None

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.get = AsyncMock(return_value=None)

        new_company_id = _make_uuid(99)

        # db.execute returns no existing company on the select query
        async def execute_side_effect(query: Any) -> AsyncMock:
            result = AsyncMock()
            result.scalar_one_or_none = MagicMock(return_value=None)
            return result

        db.execute = AsyncMock(side_effect=execute_side_effect)

        # On refresh: give the company its id (first call) then no-op (second for candidate)
        refresh_calls = [0]

        async def refresh_side_effect(obj: Any) -> None:
            refresh_calls[0] += 1
            if isinstance(obj, RealCompany):
                obj.id = new_company_id

        db.refresh = AsyncMock(side_effect=refresh_side_effect)

        with patch.object(
            company_discovery_service, "get_candidate", AsyncMock(return_value=candidate)
        ):
            result = await company_discovery_service.promote_candidate_to_analysis(
                db, candidate_id
            )

        assert result.promoted is True
        assert result.company_created is True
        assert result.new_candidate_status == "ready_for_deeper_analysis"
        assert "No recommendation produced" in result.message

    @pytest.mark.asyncio
    async def test_promote_rejected_candidate_raises(self) -> None:
        from app.services import company_discovery_service

        candidate_id = _make_uuid(10)
        run_id = _make_uuid(2)
        candidate = _candidate_mock(
            candidate_id, run_id, candidate_status="rejected_by_screen"
        )

        db = AsyncMock()

        with patch.object(
            company_discovery_service, "get_candidate", AsyncMock(return_value=candidate)
        ):
            with pytest.raises(ValueError, match="rejected"):
                await company_discovery_service.promote_candidate_to_analysis(db, candidate_id)

    @pytest.mark.asyncio
    async def test_promote_error_candidate_raises(self) -> None:
        from app.services import company_discovery_service

        candidate_id = _make_uuid(10)
        run_id = _make_uuid(2)
        candidate = _candidate_mock(
            candidate_id, run_id, candidate_status="error"
        )

        db = AsyncMock()

        with patch.object(
            company_discovery_service, "get_candidate", AsyncMock(return_value=candidate)
        ):
            with pytest.raises(ValueError, match="error state"):
                await company_discovery_service.promote_candidate_to_analysis(db, candidate_id)

    @pytest.mark.asyncio
    async def test_promote_candidate_not_found_raises(self) -> None:
        from app.services import company_discovery_service

        db = AsyncMock()

        with patch.object(
            company_discovery_service, "get_candidate", AsyncMock(return_value=None)
        ):
            with pytest.raises(ValueError, match="not found"):
                await company_discovery_service.promote_candidate_to_analysis(
                    db, _make_uuid(999)
                )

    @pytest.mark.asyncio
    async def test_promote_reuses_existing_company(self) -> None:
        from app.services import company_discovery_service

        candidate_id = _make_uuid(10)
        run_id = _make_uuid(2)
        existing_company_id = _make_uuid(50)
        candidate = _candidate_mock(
            candidate_id, run_id, ticker="ABB", exchange="SWX"
        )
        candidate.company_id = None

        existing_company = MagicMock()
        existing_company.id = existing_company_id
        existing_company.ticker = "ABB"
        existing_company.exchange = "SWX"
        existing_company.name = "ABB Ltd"

        db = AsyncMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        db.get = AsyncMock(return_value=None)

        async def execute_side_effect(query: Any) -> AsyncMock:
            result = AsyncMock()
            result.scalar_one_or_none = MagicMock(return_value=existing_company)
            return result

        db.execute = AsyncMock(side_effect=execute_side_effect)

        with patch.object(
            company_discovery_service, "get_candidate", AsyncMock(return_value=candidate)
        ):
            result = await company_discovery_service.promote_candidate_to_analysis(
                db, candidate_id
            )

        assert result.promoted is True
        assert result.company_created is False
        assert result.company_id == existing_company_id


# ===========================================================================
# Part 4: API Endpoints
# ===========================================================================


@pytest.fixture
def universe_id() -> uuid.UUID:
    return _make_uuid(1)


@pytest.fixture
def run_id() -> uuid.UUID:
    return _make_uuid(2)


@pytest.fixture
def candidate_id() -> uuid.UUID:
    return _make_uuid(3)


class TestDiscoveryUniverseAPI:
    @pytest.mark.asyncio
    async def test_create_universe_201(
        self, client: AsyncMock, universe_id: uuid.UUID
    ) -> None:
        universe = _universe_mock(universe_id)
        with patch(
            "app.api.v1.discovery.company_discovery_service.create_universe",
            AsyncMock(return_value=universe),
        ):
            response = await client.post(
                "/api/v1/discovery/universes",
                json={
                    "name": "EU Energy Transition",
                    "theme": "energy_transition",
                    "region": "Europe",
                    "provider_name": "mock",
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Test Universe"
        assert body["theme"] == "energy_transition"
        # Safety: no recommendation fields
        assert "BUY" not in json.dumps(body)
        assert "price_target" not in json.dumps(body)
        assert "fair_value" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_create_universe_invalid_theme_422(
        self, client: AsyncMock
    ) -> None:
        response = await client.post(
            "/api/v1/discovery/universes",
            json={"name": "Bad", "theme": "BUY"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_list_universes_200(
        self, client: AsyncMock, universe_id: uuid.UUID
    ) -> None:
        universe = _universe_mock(universe_id)
        with patch(
            "app.api.v1.discovery.company_discovery_service.list_universes",
            AsyncMock(return_value=([universe], 1)),
        ):
            response = await client.get("/api/v1/discovery/universes")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1


class TestDiscoveryRunsAPI:
    @pytest.mark.asyncio
    async def test_create_run_201(
        self, client: AsyncMock, universe_id: uuid.UUID, run_id: uuid.UUID
    ) -> None:
        run = _run_mock(run_id, universe_id)
        with patch(
            "app.api.v1.discovery.company_discovery_service.run_screening",
            AsyncMock(return_value=run),
        ):
            response = await client.post(
                "/api/v1/discovery/runs",
                json={
                    "universe_id": str(universe_id),
                    "max_candidates": 10,
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "completed"
        assert "BUY" not in json.dumps(body)
        assert "price_target" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_create_run_universe_not_found_404(
        self, client: AsyncMock
    ) -> None:
        with patch(
            "app.api.v1.discovery.company_discovery_service.run_screening",
            AsyncMock(side_effect=ValueError("Screening universe not found")),
        ):
            response = await client.post(
                "/api/v1/discovery/runs",
                json={"universe_id": str(_make_uuid(99)), "max_candidates": 10},
            )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_runs_200(
        self, client: AsyncMock, universe_id: uuid.UUID, run_id: uuid.UUID
    ) -> None:
        run = _run_mock(run_id, universe_id)
        with patch(
            "app.api.v1.discovery.company_discovery_service.list_screening_runs",
            AsyncMock(return_value=([run], 1)),
        ):
            response = await client.get("/api/v1/discovery/runs")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1

    @pytest.mark.asyncio
    async def test_get_run_200(
        self, client: AsyncMock, universe_id: uuid.UUID, run_id: uuid.UUID
    ) -> None:
        run = _run_mock(run_id, universe_id)
        with patch(
            "app.api.v1.discovery.company_discovery_service.get_screening_run",
            AsyncMock(return_value=run),
        ):
            response = await client.get(f"/api/v1/discovery/runs/{run_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(run_id)

    @pytest.mark.asyncio
    async def test_get_run_not_found_404(self, client: AsyncMock) -> None:
        with patch(
            "app.api.v1.discovery.company_discovery_service.get_screening_run",
            AsyncMock(return_value=None),
        ):
            response = await client.get(f"/api/v1/discovery/runs/{_make_uuid(99)}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_candidates_200(
        self,
        client: AsyncMock,
        universe_id: uuid.UUID,
        run_id: uuid.UUID,
        candidate_id: uuid.UUID,
    ) -> None:
        run = _run_mock(run_id, universe_id)
        candidate = _candidate_mock(candidate_id, run_id)
        with patch(
            "app.api.v1.discovery.company_discovery_service.get_screening_run",
            AsyncMock(return_value=run),
        ), patch(
            "app.api.v1.discovery.company_discovery_service.list_candidates",
            AsyncMock(return_value=([candidate], 1)),
        ):
            response = await client.get(f"/api/v1/discovery/runs/{run_id}/candidates")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        c = body["items"][0]
        # Safety: verify no recommendation fields present in the response schema
        assert "recommendation" not in c
        assert "rating" not in c
        assert "price_target" not in c
        assert "fair_value" not in c
        assert "upside_percent" not in c
        # candidate_status must be an internal status, never a public recommendation
        assert c["candidate_status"] not in {"BUY", "SELL", "HOLD", "WATCH"}

    @pytest.mark.asyncio
    async def test_list_candidates_run_not_found_404(
        self, client: AsyncMock, run_id: uuid.UUID
    ) -> None:
        with patch(
            "app.api.v1.discovery.company_discovery_service.get_screening_run",
            AsyncMock(return_value=None),
        ):
            response = await client.get(f"/api/v1/discovery/runs/{run_id}/candidates")
        assert response.status_code == 404


class TestDiscoveryCandidatePromoteAPI:
    @pytest.mark.asyncio
    async def test_promote_candidate_200(
        self,
        client: AsyncMock,
        universe_id: uuid.UUID,
        run_id: uuid.UUID,
        candidate_id: uuid.UUID,
    ) -> None:
        company_id = _make_uuid(99)
        promote_result = MagicMock()
        promote_result.candidate_id = candidate_id
        promote_result.company_id = company_id
        promote_result.ticker = "ORSTED"
        promote_result.exchange = "CPH"
        promote_result.name = "Ørsted A/S"
        promote_result.promoted = True
        promote_result.company_created = True
        promote_result.new_candidate_status = "ready_for_deeper_analysis"
        promote_result.message = (
            "Candidate promoted. Company record created (ORSTED.CPH). "
            "Run the company-analysis workflow separately to begin deeper research. "
            "No recommendation produced. No publishing performed."
        )

        with patch(
            "app.api.v1.discovery.company_discovery_service.promote_candidate_to_analysis",
            AsyncMock(return_value=promote_result),
        ):
            response = await client.post(
                f"/api/v1/discovery/candidates/{candidate_id}/promote"
            )
        assert response.status_code == 200
        body = response.json()
        assert body["promoted"] is True
        assert body["new_candidate_status"] == "ready_for_deeper_analysis"
        assert "No recommendation produced" in body["message"]

    @pytest.mark.asyncio
    async def test_promote_candidate_not_found_404(
        self, client: AsyncMock, candidate_id: uuid.UUID
    ) -> None:
        with patch(
            "app.api.v1.discovery.company_discovery_service.promote_candidate_to_analysis",
            AsyncMock(side_effect=ValueError(f"Screening candidate {candidate_id} not found")),
        ):
            response = await client.post(
                f"/api/v1/discovery/candidates/{candidate_id}/promote"
            )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_promote_rejected_candidate_422(
        self, client: AsyncMock, candidate_id: uuid.UUID
    ) -> None:
        with patch(
            "app.api.v1.discovery.company_discovery_service.promote_candidate_to_analysis",
            AsyncMock(side_effect=ValueError("rejected by the screen and cannot be promoted")),
        ):
            response = await client.post(
                f"/api/v1/discovery/candidates/{candidate_id}/promote"
            )
        assert response.status_code == 422


# ===========================================================================
# Part 5: Safety invariant tests
# ===========================================================================


class TestDiscoverySafetyInvariants:
    """
    These tests verify that no investment recommendation, price target,
    fair value, or upside percentage is ever produced by the discovery system.
    """

    def test_candidate_status_values_do_not_include_recommendations(self) -> None:
        from app.models.screening import CANDIDATE_STATUS_VALUES

        forbidden = {"BUY", "SELL", "HOLD", "WATCH", "price_target", "fair_value"}
        for v in CANDIDATE_STATUS_VALUES:
            assert v not in forbidden, f"Forbidden status value found: {v}"

    def test_screener_never_produces_recommendation_field(self) -> None:
        from app.services.screener import CompanyScreener

        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange=None,
            sector=None,
            theme=None,
            max_candidates=100,
            provider_name="mock",
        )
        for r in results:
            assert not hasattr(r, "recommendation"), "CandidateInput must not have recommendation field"
            assert not hasattr(r, "rating"), "CandidateInput must not have rating field"
            assert not hasattr(r, "price_target"), "CandidateInput must not have price_target field"
            assert not hasattr(r, "fair_value"), "CandidateInput must not have fair_value field"
            assert not hasattr(r, "upside_percent"), "CandidateInput must not have upside_percent field"

    def test_promote_response_no_recommendation_fields(self) -> None:

        from app.schemas.discovery import PromoteCandidateResponse

        fields = PromoteCandidateResponse.model_fields
        forbidden = {"recommendation", "rating", "price_target", "fair_value", "upside_percent", "BUY", "SELL"}
        for f in fields:
            assert f not in forbidden, f"Forbidden field '{f}' found in PromoteCandidateResponse"

    def test_screening_candidate_schema_no_recommendation_fields(self) -> None:
        from app.schemas.discovery import ScreeningCandidateRead

        fields = ScreeningCandidateRead.model_fields
        forbidden = {"recommendation", "rating", "price_target", "fair_value", "upside_percent"}
        for f in fields:
            assert f not in forbidden, f"Forbidden field '{f}' found in ScreeningCandidateRead"

    def test_candidate_status_field_in_screening_candidate(self) -> None:
        from app.schemas.discovery import ScreeningCandidateRead

        assert "candidate_status" in ScreeningCandidateRead.model_fields

    def test_source_tier_t5_for_eodhd_candidates(self) -> None:
        from app.services.screener import SOURCE_TIER_T5, T5_VALIDATION_WARNING, CompanyScreener

        eodhd_results = [
            {
                "Code": "GLEN",
                "Exchange": "LSE",
                "Name": "Glencore PLC",
                "Country": "United Kingdom",
                "Type": "Common Stock",
                "Currency": "USD",
            }
        ]
        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange=None,
            sector=None,
            theme="materials_mining",
            max_candidates=5,
            provider_name="eodhd",
            eodhd_search_results=eodhd_results,
        )
        assert len(results) == 1
        assert results[0].source_tier == SOURCE_TIER_T5
        assert T5_VALIDATION_WARNING in results[0].warnings

    def test_missing_data_handling_does_not_crash(self) -> None:
        from app.services.screener import CompanyScreener

        screener = CompanyScreener()
        eodhd_results = [
            {"Code": "SPARSE", "Exchange": "STO", "Type": "Common Stock"},
        ]
        results = screener.screen(
            region=None,
            exchange=None,
            sector=None,
            theme=None,
            max_candidates=5,
            provider_name="eodhd",
            eodhd_search_results=eodhd_results,
        )
        assert len(results) == 1
        assert len(results[0].missing_data) > 0

    def test_ambiguous_identifier_handled(self) -> None:
        from app.services.screener import CompanyScreener

        # Multiple results with similar names simulate ambiguity from EODHD search
        eodhd_results = [
            {"Code": "AAPL", "Exchange": "US", "Name": "Apple Inc.", "Country": "US", "Type": "Common Stock", "Currency": "USD"},
            {"Code": "AAPL", "Exchange": "XETRA", "Name": "Apple Inc. DE", "Country": "Germany", "Type": "Common Stock", "Currency": "EUR"},
        ]
        screener = CompanyScreener()
        results = screener.screen(
            region=None,
            exchange=None,
            sector=None,
            theme=None,
            max_candidates=10,
            provider_name="eodhd",
            eodhd_search_results=eodhd_results,
        )
        # Both should be processed; the screener does not silently pick one
        assert len(results) == 2

    def test_rejected_by_screen_status_not_a_recommendation(self) -> None:
        from app.services.screener import CompanyScreener

        screener = CompanyScreener()
        candidate = screener._build_candidate(
            raw={"ticker": "TINY", "exchange": "OSE", "name": "Tiny Corp", "market_cap": 1.0},
            theme=None,
            source_tier="T6_model_estimate",
            data_quality="D_weak_or_stale",
            market_cap_min=1000.0,
            market_cap_max=None,
        )
        # rejected_by_screen is NOT a SELL — it means the screen filter excluded it
        assert candidate.candidate_status == "rejected_by_screen"
        assert candidate.candidate_status != "SELL"
        assert candidate.candidate_status != "BUY"
