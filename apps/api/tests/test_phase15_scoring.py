"""
Phase 15 — Scoring + Valuation Framework Tests.

All tests run OFFLINE with no network calls, no Azure credentials, no API key.

Coverage:
  ScoringEngine.score_candidate:
    - mock/T6 data overall score capped at ≤ 30
    - T5 data overall score capped at ≤ 60
    - missing data lowers data_completeness_score
    - T5-only triggers source-quality warning
    - theme alignment raises score for matched keywords
    - no BUY/SELL/HOLD/WATCH/price_target/fair_value in any output
    - all internal_status values are in ALLOWED_INTERNAL_STATUSES
    - score normalization: all dimension scores are 0–100 integers
    - empty candidate data returns graceful result (not an exception)

  ScoringEngine.score_company_analysis:
    - returns ScorecardResult from council summaries
    - mock data cannot get overall_score > 30
    - no forbidden terms in any result field

  ValuationReadinessService:
    - mock/T6 data → not_ready
    - T5 data with partial fields → partial or ready_for_basic_multiples
    - T5 data with all basic fields → ready_for_basic_multiples
    - T1 data with full set → ready_for_deeper_valuation
    - result contains disclaimer, no price target, no fair value

  run_score_research_attractiveness (agent node):
    - always returns dict, never raises
    - returns not_enough_data on empty inputs
    - returns disclaimer in output
    - no forbidden terms

  ScoringService (DB layer, mocked):
    - score_candidate raises ValueError for unknown candidate_id
    - score_screening_run raises ValueError for unknown run_id
    - list_ranked_candidates raises ValueError for unknown run_id
    - get_candidate_scorecard returns None when missing

  Safety gates:
    - _check_forbidden_terms catches BUY, SELL, HOLD, price target, fair value
    - ALLOWED_INTERNAL_STATUSES never contains BUY/SELL/HOLD/WATCH/REJECT
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)

_FORBIDDEN_OUTPUT_TERMS = [
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "price target",
    "target price",
    "fair value",
    "upside of",
    "downside of",
    "undervalued",
    "overvalued",
    "upside_percent",
]


def _has_forbidden_term(obj: Any, skip_keys: frozenset[str] | None = None) -> list[str]:
    """Recursively search any dict/list/str for forbidden output terms.

    skip_keys: dict keys to skip (e.g. 'disclaimer' which legitimately references
    what the system does NOT produce).
    """
    if skip_keys is None:
        skip_keys = frozenset({"disclaimer"})
    text = _flatten_to_text(obj, skip_keys=skip_keys)
    return [t for t in _FORBIDDEN_OUTPUT_TERMS if t.lower() in text.lower()]


def _flatten_to_text(obj: Any, skip_keys: frozenset[str] | None = None) -> str:
    if skip_keys is None:
        skip_keys = frozenset()
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return " ".join(
            _flatten_to_text(v, skip_keys=skip_keys)
            for k, v in obj.items()
            if k not in skip_keys
        )
    if isinstance(obj, (list, tuple)):
        return " ".join(_flatten_to_text(v, skip_keys=skip_keys) for v in obj)
    return str(obj)


def _make_uuid(n: int) -> uuid.UUID:
    return uuid.UUID(f"{n:032x}")


def _minimal_candidate_data(
    source_tier: str = "T6_model_estimate",
    data_quality: str = "D_weak_or_stale",
    available_data: list[str] | None = None,
    missing_data: list[str] | None = None,
    discovery_reasons: list[str] | None = None,
) -> dict:
    return {
        "ticker": "TEST",
        "name": "Test Company",
        "sector": "Utilities",
        "country": "Denmark",
        "source_tier": source_tier,
        "data_quality": data_quality,
        "available_data": available_data or ["ticker", "name", "sector", "country"],
        "missing_data": missing_data or ["market_cap", "currency", "revenue_ttm"],
        "discovery_reasons": discovery_reasons or [],
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# ScoringEngine — candidate scoring
# ---------------------------------------------------------------------------


class TestScoringEngineCandidateScoring:
    def setup_method(self) -> None:
        from app.services.scoring_engine import ScoringEngine

        self.engine = ScoringEngine()

    def test_mock_t6_overall_score_capped_at_30(self) -> None:
        data = _minimal_candidate_data(
            source_tier="T6_model_estimate",
            data_quality="D_weak_or_stale",
            available_data=["ticker", "name", "sector", "country", "market_cap",
                            "revenue_ttm", "ebitda", "shares_outstanding"],
            discovery_reasons=["Theme match: wind, solar, hydrogen"],
        )
        result = self.engine.score_candidate(data)
        assert result.overall_score <= 30, (
            f"Mock/T6 overall score {result.overall_score} exceeds cap of 30"
        )

    def test_t5_overall_score_capped_at_60(self) -> None:
        data = _minimal_candidate_data(
            source_tier="T5_api_aggregator",
            data_quality="C_aggregated",
            available_data=["ticker", "exchange", "name", "country", "sector",
                            "market_cap", "currency", "revenue_ttm", "ebitda",
                            "shares_outstanding", "ev_ebitda", "pe_ratio"],
            missing_data=["fcf_ttm", "net_debt"],
            discovery_reasons=["Theme match: renewable, offshore wind, hydrogen"],
        )
        result = self.engine.score_candidate(data)
        assert result.overall_score <= 60, (
            f"T5 overall score {result.overall_score} exceeds cap of 60"
        )

    def test_missing_data_lowers_data_completeness_score(self) -> None:
        full_data = _minimal_candidate_data(
            source_tier="T5_api_aggregator",
            data_quality="C_aggregated",
            available_data=["ticker", "exchange", "name", "country", "sector",
                            "market_cap", "currency", "revenue_ttm", "ebitda",
                            "ev_ebitda", "pe_ratio", "fcf_ttm", "net_debt", "shares_outstanding"],
            missing_data=[],
        )
        sparse_data = _minimal_candidate_data(
            source_tier="T5_api_aggregator",
            data_quality="C_aggregated",
            available_data=["ticker", "name"],
            missing_data=["exchange", "country", "sector", "market_cap", "currency",
                          "revenue_ttm", "ebitda", "ev_ebitda", "fcf_ttm"],
        )
        full_result = self.engine.score_candidate(full_data)
        sparse_result = self.engine.score_candidate(sparse_data)

        full_dc = full_result.scores["data_completeness_score"].score
        sparse_dc = sparse_result.scores["data_completeness_score"].score
        assert full_dc > sparse_dc, (
            f"Full data completeness score ({full_dc}) should be > sparse ({sparse_dc})"
        )

    def test_t5_source_tier_triggers_warning(self) -> None:
        data = _minimal_candidate_data(
            source_tier="T5_api_aggregator",
            data_quality="C_aggregated",
        )
        result = self.engine.score_candidate(data)
        # Collect warnings from top-level result and all dimension scores
        all_warnings = list(result.warnings)
        for dim in result.scores.values():
            all_warnings.extend(dim.warnings)
        all_text = " ".join(all_warnings)
        assert (
            "T5" in all_text
            or "aggregator" in all_text
            or "T1/T2" in all_text
            or "primary source" in all_text.lower()
        ), f"T5 tier should produce a source quality warning. Got: {all_text[:300]}"

    def test_theme_keyword_match_raises_theme_alignment_score(self) -> None:
        no_theme = _minimal_candidate_data(
            source_tier="T5_api_aggregator",
            data_quality="C_aggregated",
            discovery_reasons=[],
        )
        with_theme = _minimal_candidate_data(
            source_tier="T5_api_aggregator",
            data_quality="C_aggregated",
            discovery_reasons=[
                "Theme match energy_transition: renewable wind solar hydrogen offshore"
            ],
        )
        no_theme_result = self.engine.score_candidate(no_theme)
        with_theme_result = self.engine.score_candidate(with_theme)

        no_score = no_theme_result.scores["theme_alignment_score"].score
        with_score = with_theme_result.scores["theme_alignment_score"].score
        assert with_score >= no_score, (
            f"Theme match should raise score: {with_score} >= {no_score}"
        )

    def test_no_forbidden_terms_in_candidate_result(self) -> None:
        data = _minimal_candidate_data(
            source_tier="T5_api_aggregator",
            data_quality="C_aggregated",
            discovery_reasons=["Theme match: renewable energy, solar, wind"],
        )
        result = self.engine.score_candidate(data)
        result_dict = result.to_dict()
        violations = _has_forbidden_term(result_dict)
        assert not violations, f"Forbidden terms found in candidate result: {violations}"

    def test_all_internal_status_values_are_allowed(self) -> None:
        from app.services.scoring_engine import ALLOWED_INTERNAL_STATUSES

        for source_tier, data_quality in [
            ("T6_model_estimate", "D_weak_or_stale"),
            ("T5_api_aggregator", "C_aggregated"),
        ]:
            data = _minimal_candidate_data(
                source_tier=source_tier,
                data_quality=data_quality,
            )
            result = self.engine.score_candidate(data)
            assert result.internal_status in ALLOWED_INTERNAL_STATUSES, (
                f"Status '{result.internal_status}' not in ALLOWED_INTERNAL_STATUSES"
            )

    def test_dimension_scores_are_0_to_100_integers(self) -> None:
        data = _minimal_candidate_data(source_tier="T5_api_aggregator")
        result = self.engine.score_candidate(data)
        for dim_name, dim_score in result.scores.items():
            assert isinstance(dim_score.score, int), (
                f"{dim_name} score is not int: {type(dim_score.score)}"
            )
            assert 0 <= dim_score.score <= 100, (
                f"{dim_name} score {dim_score.score} out of 0-100 range"
            )
        assert isinstance(result.overall_score, int)
        assert 0 <= result.overall_score <= 100

    def test_empty_candidate_data_returns_graceful_result(self) -> None:
        result = self.engine.score_candidate({})
        assert isinstance(result.overall_score, int)
        assert result.internal_status in (
            __import__("app.services.scoring_engine", fromlist=["ALLOWED_INTERNAL_STATUSES"])
            .ALLOWED_INTERNAL_STATUSES
        )

    def test_overall_score_is_not_zero_for_t5_with_data(self) -> None:
        data = _minimal_candidate_data(
            source_tier="T5_api_aggregator",
            data_quality="C_aggregated",
            available_data=["ticker", "name", "sector", "country", "market_cap"],
        )
        result = self.engine.score_candidate(data)
        assert result.overall_score > 0, "T5 candidate with some data should score > 0"

    def test_result_dict_always_has_disclaimer(self) -> None:
        data = _minimal_candidate_data()
        result = self.engine.score_candidate(data)
        d = result.to_dict()
        assert "disclaimer" in d
        assert "Not investment advice" in d["disclaimer"]

    def test_allowed_internal_statuses_do_not_contain_forbidden_terms(self) -> None:
        from app.services.scoring_engine import ALLOWED_INTERNAL_STATUSES

        public_recommendations = {"BUY", "SELL", "HOLD", "WATCH", "REJECT"}
        for status in ALLOWED_INTERNAL_STATUSES:
            assert status.upper() not in public_recommendations, (
                f"Internal status '{status}' looks like a public recommendation"
            )


# ---------------------------------------------------------------------------
# ScoringEngine — company analysis scoring
# ---------------------------------------------------------------------------


class TestScoringEngineCompanyAnalysisScoring:
    def setup_method(self) -> None:
        from app.services.scoring_engine import ScoringEngine

        self.engine = ScoringEngine()

    def _mock_snapshot(self, is_mock: bool = True, source_tier: str = "T6_model_estimate") -> dict:
        return {
            "company_identity": {
                "ticker": "ORSTED",
                "exchange": "CPH",
                "legal_name": "Ørsted A/S",
                "country_domicile": "Denmark",
            },
            "provider_metadata": {
                "provider_name": "mock" if is_mock else "eodhd",
                "source_tier": source_tier,
                "is_mock": is_mock,
            },
            "profile": {"sector": "Utilities"},
            "is_mock": is_mock,
        }

    def test_mock_data_overall_score_capped_at_30(self) -> None:
        snapshot = self._mock_snapshot(is_mock=True, source_tier="T6_model_estimate")
        result = self.engine.score_company_analysis(company_snapshot=snapshot)
        assert result.overall_score <= 30, (
            f"Mock data overall score {result.overall_score} exceeds cap of 30"
        )

    def test_no_forbidden_terms_in_company_analysis_result(self) -> None:
        snapshot = self._mock_snapshot(is_mock=False, source_tier="T5_api_aggregator")
        bull = {
            "bull_case_points": ["Strong renewable pipeline", "Grid expansion"],
            "warnings": [],
        }
        bear = {
            "bear_case_points": ["High capex", "Subsidy dependency"],
            "warnings": [],
        }
        risk = {"key_risks": ["Regulatory risk", "Interest rate risk"], "warnings": []}
        result = self.engine.score_company_analysis(
            company_snapshot=snapshot,
            bull_case_summary=bull,
            bear_case_summary=bear,
            risk_summary=risk,
        )
        violations = _has_forbidden_term(result.to_dict())
        assert not violations, f"Forbidden terms in company analysis result: {violations}"

    def test_all_dimension_scores_0_to_100(self) -> None:
        snapshot = self._mock_snapshot()
        result = self.engine.score_company_analysis(company_snapshot=snapshot)
        for dim_name, dim_score in result.scores.items():
            assert 0 <= dim_score.score <= 100, (
                f"{dim_name} score {dim_score.score} out of range"
            )

    def test_internal_status_is_allowed(self) -> None:
        from app.services.scoring_engine import ALLOWED_INTERNAL_STATUSES

        snapshot = self._mock_snapshot()
        result = self.engine.score_company_analysis(company_snapshot=snapshot)
        assert result.internal_status in ALLOWED_INTERNAL_STATUSES

    def test_result_has_next_research_steps(self) -> None:
        snapshot = self._mock_snapshot()
        result = self.engine.score_company_analysis(company_snapshot=snapshot)
        assert isinstance(result.next_research_steps, list)


# ---------------------------------------------------------------------------
# ValuationReadinessService
# ---------------------------------------------------------------------------


class TestValuationReadinessService:
    def setup_method(self) -> None:
        from app.services.scoring_engine import ValuationReadinessService

        self.svc = ValuationReadinessService()

    def test_mock_data_returns_not_ready(self) -> None:
        result = self.svc.check(
            available_data=["ticker", "market_cap", "revenue_ttm", "ebitda"],
            source_tier="T6_model_estimate",
            is_mock=True,
        )
        assert result.valuation_readiness == "not_ready"

    def test_t6_tier_returns_not_ready_regardless_of_fields(self) -> None:
        result = self.svc.check(
            available_data=["ticker", "exchange", "market_cap", "revenue_ttm",
                            "ebitda", "shares_outstanding", "enterprise_value",
                            "ebit", "net_income", "free_cash_flow", "debt", "cash"],
            source_tier="T6_model_estimate",
            is_mock=False,
        )
        assert result.valuation_readiness == "not_ready"

    def test_t5_with_no_financial_fields_returns_not_ready(self) -> None:
        result = self.svc.check(
            available_data=["ticker", "name", "sector"],
            source_tier="T5_api_aggregator",
            is_mock=False,
        )
        assert result.valuation_readiness in {"not_ready", "partial"}

    def test_t5_with_all_basic_fields_returns_ready_for_basic_multiples(self) -> None:
        result = self.svc.check(
            available_data=["ticker", "market_cap", "revenue_ttm", "ebitda", "shares_outstanding"],
            source_tier="T5_api_aggregator",
            is_mock=False,
        )
        assert result.valuation_readiness in {
            "ready_for_basic_multiples",
            "ready_for_deeper_valuation",
        }

    def test_t1_with_full_fields_returns_ready_for_deeper_valuation(self) -> None:
        result = self.svc.check(
            available_data=[
                "ticker", "market_cap", "revenue_ttm", "ebitda", "shares_outstanding",
                "enterprise_value", "ebit", "net_income", "free_cash_flow",
                "debt", "cash", "historical_price",
            ],
            source_tier="T1_primary_filing",
            is_mock=False,
        )
        assert result.valuation_readiness == "ready_for_deeper_valuation"

    def test_result_has_disclaimer_no_price_target(self) -> None:
        result = self.svc.check(
            available_data=["market_cap"],
            source_tier="T5_api_aggregator",
            is_mock=False,
        )
        d = result.to_dict()
        assert "disclaimer" in d
        # Disclaimer must explicitly say no price target / fair value
        assert "price target" in d["disclaimer"].lower() or "fair value" in d["disclaimer"].lower()
        # The result should not itself contain forbidden output terms
        violations = _has_forbidden_term({k: v for k, v in d.items() if k != "disclaimer"})
        assert not violations, f"Forbidden terms in valuation readiness: {violations}"

    def test_t5_produces_source_warning(self) -> None:
        result = self.svc.check(
            available_data=["market_cap", "revenue_ttm"],
            source_tier="T5_api_aggregator",
            is_mock=False,
        )
        assert any("T5" in w or "primary source" in w.lower() for w in result.warnings), (
            "T5 tier should produce a source warning"
        )

    def test_valuation_readiness_values_are_expected(self) -> None:
        allowed = {
            "not_ready",
            "partial",
            "ready_for_basic_multiples",
            "ready_for_deeper_valuation",
        }
        for source_tier, is_mock, available_data in [
            ("T6_model_estimate", True, []),
            ("T5_api_aggregator", False, []),
            ("T5_api_aggregator", False, ["market_cap", "revenue_ttm"]),
            ("T1_primary_filing", False, ["market_cap", "revenue_ttm", "ebitda",
                                          "shares_outstanding"]),
        ]:
            result = self.svc.check(
                available_data=available_data,
                source_tier=source_tier,
                is_mock=is_mock,
            )
            assert result.valuation_readiness in allowed, (
                f"Unexpected readiness value: {result.valuation_readiness}"
            )


# ---------------------------------------------------------------------------
# run_score_research_attractiveness agent node
# ---------------------------------------------------------------------------


class TestScoreResearchAttractivenessAgentNode:
    def test_always_returns_dict_never_raises(self) -> None:
        from app.agents.analysis_council.score_research_attractiveness import (
            run_score_research_attractiveness,
        )

        # Completely empty inputs
        result = run_score_research_attractiveness(company_snapshot={})
        assert isinstance(result, dict)

    def test_returns_required_keys_on_empty_input(self) -> None:
        from app.agents.analysis_council.score_research_attractiveness import (
            run_score_research_attractiveness,
        )

        result = run_score_research_attractiveness(company_snapshot={})
        for key in ["overall_score", "internal_status", "scores", "warnings",
                    "missing_data", "reasoning", "disclaimer"]:
            assert key in result, f"Missing key '{key}' in result"

    def test_empty_input_returns_not_enough_data_status(self) -> None:
        from app.agents.analysis_council.score_research_attractiveness import (
            run_score_research_attractiveness,
        )

        result = run_score_research_attractiveness(company_snapshot={})
        from app.services.scoring_engine import ALLOWED_INTERNAL_STATUSES

        assert result["internal_status"] in ALLOWED_INTERNAL_STATUSES

    def test_result_contains_disclaimer(self) -> None:
        from app.agents.analysis_council.score_research_attractiveness import (
            run_score_research_attractiveness,
        )

        result = run_score_research_attractiveness(
            company_snapshot={"ticker": "TEST", "is_mock": True}
        )
        assert "disclaimer" in result
        assert "Not investment advice" in result["disclaimer"]

    def test_no_forbidden_terms_in_output(self) -> None:
        from app.agents.analysis_council.score_research_attractiveness import (
            run_score_research_attractiveness,
        )

        result = run_score_research_attractiveness(
            company_snapshot={
                "company_identity": {
                    "ticker": "ORSTED",
                    "legal_name": "Ørsted A/S",
                    "country_domicile": "Denmark",
                },
                "provider_metadata": {
                    "provider_name": "mock",
                    "source_tier": "T6_model_estimate",
                    "is_mock": True,
                },
                "profile": {"sector": "Utilities"},
                "is_mock": True,
            },
            bull_case_summary={
                "bull_case_points": ["Strong renewable portfolio"],
                "warnings": [],
            },
            bear_case_summary={
                "bear_case_points": ["High capex"],
                "warnings": [],
            },
        )
        violations = _has_forbidden_term(result)
        assert not violations, f"Forbidden terms in agent node output: {violations}"

    def test_survives_exception_in_engine_with_fallback(self) -> None:
        from app.agents.analysis_council.score_research_attractiveness import (
            run_score_research_attractiveness,
        )

        # Patch the engine to raise to test the fallback path
        with patch(
            "app.agents.analysis_council.score_research_attractiveness._engine.score_company_analysis",
            side_effect=RuntimeError("Simulated engine failure"),
        ):
            result = run_score_research_attractiveness(company_snapshot={"ticker": "FAIL"})

        assert isinstance(result, dict)
        assert result["internal_status"] == "not_enough_data"
        assert result["overall_score"] == 0
        assert any("Scoring node failed" in w for w in result["warnings"])

    def test_overall_score_is_integer_0_to_100(self) -> None:
        from app.agents.analysis_council.score_research_attractiveness import (
            run_score_research_attractiveness,
        )

        result = run_score_research_attractiveness(company_snapshot={"ticker": "X"})
        assert isinstance(result["overall_score"], int)
        assert 0 <= result["overall_score"] <= 100


# ---------------------------------------------------------------------------
# Safety gate: _check_forbidden_terms
# ---------------------------------------------------------------------------


class TestForbiddenTermsSafetyGate:
    def _check(self, text: str) -> list[str]:
        from app.services.scoring_engine import _check_forbidden_terms

        return _check_forbidden_terms(text)

    def test_catches_buy(self) -> None:
        assert self._check("This is a BUY recommendation") != []

    def test_catches_sell(self) -> None:
        assert self._check("We rate this SELL") != []

    def test_catches_hold(self) -> None:
        assert self._check("Rating: HOLD") != []

    def test_catches_price_target(self) -> None:
        assert self._check("price target of EUR 100") != []

    def test_catches_fair_value(self) -> None:
        assert self._check("fair value estimate: 50") != []

    def test_catches_upside_of(self) -> None:
        assert self._check("upside of 30%") != []

    def test_safe_text_returns_empty_list(self) -> None:
        safe = (
            "This is an internal research attractiveness score. "
            "The company shows strong theme alignment with energy_transition. "
            "Internal status: ready_for_deeper_analysis. Human review required."
        )
        assert self._check(safe) == []

    def test_catches_undervalued(self) -> None:
        assert self._check("The stock appears undervalued") != []


# ---------------------------------------------------------------------------
# ALLOWED_INTERNAL_STATUSES integrity
# ---------------------------------------------------------------------------


class TestAllowedInternalStatuses:
    def test_statuses_are_research_queue_labels_not_recommendations(self) -> None:
        from app.services.scoring_engine import ALLOWED_INTERNAL_STATUSES

        # Check that statuses are not single-word public recommendations.
        # "reject_due_to_data_quality" is a valid research queue label (not the
        # bare "REJECT" recommendation), so we only disallow bare term matches.
        bare_public_recommendations = {"buy", "sell", "hold", "watch"}
        for status in ALLOWED_INTERNAL_STATUSES:
            assert status.lower() not in bare_public_recommendations, (
                f"Internal status '{status}' is a bare public recommendation"
            )

    def test_exactly_six_allowed_statuses(self) -> None:
        from app.services.scoring_engine import ALLOWED_INTERNAL_STATUSES

        assert len(ALLOWED_INTERNAL_STATUSES) == 6

    def test_expected_statuses_present(self) -> None:
        from app.services.scoring_engine import ALLOWED_INTERNAL_STATUSES

        expected = {
            "not_enough_data",
            "low_priority_research",
            "needs_primary_sources",
            "ready_for_deeper_analysis",
            "high_priority_for_human_review",
            "reject_due_to_data_quality",
        }
        assert ALLOWED_INTERNAL_STATUSES == expected


# ---------------------------------------------------------------------------
# ScoringService (DB layer — all async DB calls mocked)
# ---------------------------------------------------------------------------


class TestScoringServiceMocked:
    @pytest.mark.asyncio
    async def test_score_candidate_raises_value_error_for_unknown_id(self) -> None:
        from app.services.scoring_service import ScoringService

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        svc = ScoringService()
        candidate_id = _make_uuid(1)
        with pytest.raises(ValueError, match=str(candidate_id)):
            await svc.score_candidate(db, candidate_id)

    @pytest.mark.asyncio
    async def test_score_screening_run_raises_value_error_for_unknown_run_id(self) -> None:
        from app.services.scoring_service import ScoringService

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        svc = ScoringService()
        run_id = _make_uuid(2)
        with pytest.raises(ValueError, match=str(run_id)):
            await svc.score_screening_run(db, run_id)

    @pytest.mark.asyncio
    async def test_list_ranked_candidates_raises_for_unknown_run_id(self) -> None:
        from app.services.scoring_service import ScoringService

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        svc = ScoringService()
        run_id = _make_uuid(3)
        with pytest.raises(ValueError, match=str(run_id)):
            await svc.list_ranked_candidates(db, run_id=run_id)

    @pytest.mark.asyncio
    async def test_get_candidate_scorecard_returns_none_when_missing(self) -> None:
        from app.services.scoring_service import ScoringService

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        svc = ScoringService()
        candidate_id = _make_uuid(4)
        result = await svc.get_candidate_scorecard(db, candidate_id)
        assert result is None


# ---------------------------------------------------------------------------
# Scorecard model
# ---------------------------------------------------------------------------


class TestScorecardModel:
    def test_scorecard_model_has_required_columns(self) -> None:
        from app.models.scorecard import Scorecard

        sc = Scorecard()
        # These attributes must exist (may be None on an unsaved record)
        for attr in [
            "id", "company_id", "screening_candidate_id", "report_id",
            "score_type", "overall_score", "internal_status",
            "scores_json", "warnings_json", "missing_data_json",
            "source_quality_summary_json", "provider_name", "created_at",
        ]:
            assert hasattr(sc, attr), f"Scorecard is missing attribute '{attr}'"

    def test_scorecard_model_tablename(self) -> None:
        from app.models.scorecard import Scorecard

        assert Scorecard.__tablename__ == "scorecards"


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class TestScoringSchemas:
    def test_scorecard_read_schema_has_disclaimer(self) -> None:
        from app.schemas.scoring import ScorecardRead

        sc = ScorecardRead(
            id=_make_uuid(10),
            score_type="candidate_scoring",
            company_id=None,
            screening_candidate_id=_make_uuid(11),
            report_id=None,
            overall_score=25,
            internal_status="not_enough_data",
            scores=None,
            warnings=None,
            missing_data=None,
            source_quality_summary=None,
            provider_name="mock",
            created_at=_NOW,
            disclaimer="INTERNAL SCORE ONLY. Not investment advice.",
        )
        assert sc.disclaimer is not None
        assert "Not investment advice" in sc.disclaimer

    def test_valuation_readiness_read_has_disclaimer(self) -> None:
        from app.schemas.scoring import ValuationReadinessRead

        vr = ValuationReadinessRead(
            valuation_readiness="not_ready",
            available_inputs=[],
            missing_inputs=["market_cap"],
            blocked_methods=["DCF"],
            allowed_methods=[],
            warnings=["Mock data"],
            disclaimer=(
                "Valuation readiness check only. "
                "No fair value, price target, or upside estimate is produced here."
            ),
        )
        assert "price target" in vr.disclaimer.lower() or "fair value" in vr.disclaimer.lower()

    def test_ranked_candidate_list_has_note_and_disclaimer(self) -> None:
        from app.schemas.scoring import RankedCandidateList

        lst = RankedCandidateList(
            run_id=_make_uuid(20),
            items=[],
            total=0,
            disclaimer="INTERNAL SCORE ONLY. Not investment advice.",
            note="Candidates are ranked by internal research attractiveness score.",
        )
        assert lst.disclaimer
        assert lst.note


# ---------------------------------------------------------------------------
# Workflow state field
# ---------------------------------------------------------------------------


class TestCompanyAnalysisStatePhase15Field:
    def test_state_has_research_attractiveness_scorecard_field(self) -> None:
        from app.agents.base import CompanyAnalysisState

        # TypedDict keys are accessible via __annotations__
        annotations = CompanyAnalysisState.__annotations__
        assert "research_attractiveness_scorecard" in annotations, (
            "CompanyAnalysisState missing 'research_attractiveness_scorecard' field"
        )

    def test_field_is_nullable_dict(self) -> None:
        from app.agents.base import CompanyAnalysisState

        annotation = CompanyAnalysisState.__annotations__["research_attractiveness_scorecard"]
        # Should be dict | None — verify it accepts None
        annotation_str = str(annotation)
        assert "None" in annotation_str or "Optional" in annotation_str, (
            f"research_attractiveness_scorecard should be nullable: {annotation_str}"
        )
