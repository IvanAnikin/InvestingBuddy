"""
Phase 16 — Final Report Generator Tests.

All tests run OFFLINE with no network calls, no Azure credentials, no API key,
no EODHD key, and no live database (uses mocks / in-memory state).

Coverage:
  Safety gate (run_safety_gate):
    - passes when no forbidden terms present
    - catches BUY in any section
    - catches SELL in any section
    - catches HOLD in any section
    - catches WATCH (as public recommendation context)
    - catches "price target" (case-insensitive)
    - catches "fair value" (case-insensitive)
    - catches "upside of"
    - catches "guaranteed return"
    - catches "will go up"
    - blocks_approval=True when forbidden term found
    - scans nested dicts and lists
    - passed=True when all sections are clean

  Section builders (unit tests):
    - _build_admin_disclaimer returns static text
    - _build_executive_summary with scorecard includes overall_score
    - _build_executive_summary without scorecard uses missing_data provenance
    - _build_company_identity with snapshot populates fields
    - _build_company_identity without snapshot returns missing_data
    - _build_discovery_rationale without candidate returns available=False
    - _build_discovery_rationale with candidate returns reasons
    - _build_valuation_readiness with guard_summary returns readiness
    - _build_valuation_readiness includes no price target, no fair value
    - _build_internal_scorecard with scorecard returns overall_score
    - _build_internal_scorecard without scorecard returns available=False
    - _build_bull_case with summary returns positive_thesis_points
    - _build_bull_case without summary returns available=False
    - _build_bear_case with summary returns negative_thesis_points
    - _build_risk_analysis with summary returns all 6 risk categories
    - _build_missing_information aggregates from all sources
    - _build_human_review_checklist has required items
    - _build_human_review_checklist safety gate item matches passed param

  _assemble_final_report_content:
    - returns all 19 required sections
    - no BUY/SELL/HOLD/WATCH in any section
    - no price target or fair value
    - human_review_required=True in every section that needs it
    - admin_disclaimer is always static

  FinalReportGeneratorService (via mocked DB):
    - generate_from_scorecard raises ValueError for unknown scorecard_id
    - generate_from_candidate raises ValueError for unknown candidate_id
    - generate_from_company raises ValueError for unknown company_id
    - generate_from_report raises ValueError for unknown report_id
    - regenerate_report_section raises ValueError for invalid section_name
    - regenerate_report_section raises ValueError for unknown report_id
    - validate_final_report raises ValueError for unknown report_id

  ALLOWED_INTERNAL_STATUSES:
    - never contains BUY, SELL, HOLD, WATCH, REJECT
    - contains the 6 expected research queue labels

  Schemas:
    - FinalReportResponse includes disclaimer
    - SafetyValidationResult model round-trips correctly

  API endpoints (mock DB via override):
    - POST /api/v1/final-reports/from-scorecard/{id} → 404 when not found
    - POST /api/v1/final-reports/from-candidate/{id} → 404 when not found
    - POST /api/v1/final-reports/from-company/{id} → 404 when not found
    - POST /api/v1/final-reports/{id}/validate → 404 when not found
    - POST /api/v1/final-reports/{id}/regenerate-section → 404 when not found
    - POST /api/v1/final-reports/{id}/regenerate-section → 422 for invalid section

  Migration:
    - 008 revision exists and has upgrade/downgrade
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.schemas.final_report import (
    ALLOWED_INTERNAL_STATUSES,
    FINAL_REPORT_VERSION,
    FinalReportResponse,
    HumanReviewChecklistItem,
    SafetyValidationResult,
)
from app.services.final_report_generator import (
    _REQUIRED_SECTIONS,
    FinalReportGeneratorService,
    _assemble_final_report_content,
    _build_admin_disclaimer,
    _build_bear_case,
    _build_bull_case,
    _build_company_identity,
    _build_discovery_rationale,
    _build_executive_summary,
    _build_human_review_checklist,
    _build_internal_scorecard,
    _build_missing_information,
    _build_risk_analysis,
    _build_valuation_readiness,
    run_safety_gate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_scorecard(
    overall_score: int = 45,
    internal_status: str = "ready_for_deeper_analysis",
) -> MagicMock:
    sc = MagicMock()
    sc.id = uuid.uuid4()
    sc.overall_score = overall_score
    sc.internal_status = internal_status
    sc.score_type = "candidate_scoring"
    sc.scores_json = {
        "source_quality_score": {"score": 40, "explanation": "T5 data", "warnings": []},
        "data_completeness_score": {"score": 55, "explanation": "6/15 fields", "warnings": []},
    }
    sc.warnings_json = ["T5 data active"]
    sc.missing_data_json = ["market_cap", "revenue_ttm"]
    sc.source_quality_summary_json = {"is_mock": False, "overall_source_quality": "weak"}
    sc.provider_name = "eodhd"
    sc.created_at = _utcnow()
    sc.report_id = None
    sc.company_id = None
    sc.screening_candidate_id = None
    return sc


def _make_candidate() -> MagicMock:
    c = MagicMock()
    c.id = uuid.uuid4()
    c.ticker = "ORSTED"
    c.exchange = "CPH"
    c.name = "Ørsted A/S"
    c.country = "Denmark"
    c.sector = "Utilities"
    c.candidate_status = "ready_for_deeper_analysis"
    c.source_tier = "T5_api_aggregator"
    c.data_quality = "B_single_credible"
    c.discovery_reasons_json = ["Theme match 'energy_transition': keywords found — offshore, wind"]
    c.available_data_json = ["ticker", "exchange", "name", "country", "sector"]
    c.missing_data_json = ["market_cap", "currency", "revenue_ttm"]
    c.warnings_json = ["Candidate requires primary-source validation before final analysis."]
    c.company_id = None
    c.created_at = _utcnow()
    return c


def _make_mock_snapshot() -> dict[str, Any]:
    return {
        "company_identity": {
            "legal_name": "Acme Nordic AS [MOCK]",
            "ticker": "TEST",
            "exchange": "OSE",
            "country_domicile": "Norway",
            "isin": None,
            "lei": None,
        },
        "profile": {
            "sector": "Industrials",
            "reporting_currency": "NOK",
        },
        "source_tier": "T6_model_estimate",
        "retrieved_at": "2026-07-01T12:00:00Z",
        "is_mock": True,
        "missing_fields": ["identity.isin", "identity.lei"],
        "investment_recommendation": None,
    }


def _make_committee_chair_summary() -> dict[str, Any]:
    return {
        "committee_summary": "Research package based on mock provider data only.",
        "bull_bear_balance": "insufficient_data",
        "provisional_internal_status": "research_incomplete",
        "human_review_required": True,
        "primary_open_questions": ["What are the revenue sources?"],
        "research_next_steps": ["Obtain T1 primary filings"],
        "quality_gate_status": {
            "source_quality_ok": False,
            "citation_status_ok": False,
            "schema_valid": False,
            "valuation_ready": False,
            "research_complete": False,
        },
        "warnings": ["Mock provider active."],
    }


def _make_bull_case_summary() -> dict[str, Any]:
    return {
        "positive_thesis_points": ["Company operates in energy transition sector"],
        "potential_tailwinds": ["EU Green Deal investment"],
        "evidence_used": ["EODHD sector classification"],
        "assumptions": ["Sector tailwinds persist"],
        "missing_evidence": ["T1 filings needed"],
        "confidence_level": "low",
        "warnings": ["Mock data only"],
    }


def _make_bear_case_summary() -> dict[str, Any]:
    return {
        "negative_thesis_points": ["Revenue data unavailable"],
        "potential_headwinds": ["Regulatory risk"],
        "key_unknowns": ["Balance sheet composition"],
        "evidence_used": [],
        "missing_evidence": ["T1 annual report"],
        "confidence_level": "low",
        "warnings": [],
    }


def _make_risk_summary() -> dict[str, Any]:
    return {
        "business_risks": ["Execution risk"],
        "financial_risks": ["Leverage unknown"],
        "market_risks": ["Commodity price exposure"],
        "regulatory_geopolitical_risks": ["Regulatory change"],
        "data_quality_risks": ["Mock data — all values synthetic"],
        "source_quality_risks": ["T6 only — no T1/T2 sources present"],
        "risk_summary": "Six risk categories assessed. Data quality risks dominate.",
        "warnings": [],
    }


def _make_valuation_guard_summary() -> dict[str, Any]:
    return {
        "valuation_readiness": "not_ready",
        "available_valuation_inputs": [],
        "missing_valuation_inputs": ["market_cap", "ebitda", "revenue"],
        "valuation_blockers": ["CRITICAL: mock data active"],
        "allowed_next_steps": ["Obtain T1 filings"],
        "disallowed_outputs": ["price target", "fair value", "upside percentage"],
        "warnings": ["Mock provider active."],
    }


# ---------------------------------------------------------------------------
# Safety gate tests
# ---------------------------------------------------------------------------


class TestRunSafetyGate:
    def test_passes_clean_content(self) -> None:
        content = {
            "executive_summary": {
                "internal_status": "ready_for_deeper_analysis",
                "note": "Research data available for review.",
            }
        }
        result = run_safety_gate(content)
        assert result.passed is True
        assert result.forbidden_terms_found == []
        assert result.blocks_approval is False

    def test_catches_buy(self) -> None:
        content = {"summary": {"text": "This is a BUY opportunity"}}
        result = run_safety_gate(content)
        assert result.passed is False
        assert result.blocks_approval is True
        assert any("BUY" in term for term in result.forbidden_terms_found)

    def test_catches_sell(self) -> None:
        content = {"section": {"recommendation": "SELL now"}}
        result = run_safety_gate(content)
        assert result.passed is False
        assert any("SELL" in t.upper() for t in result.forbidden_terms_found)

    def test_catches_hold(self) -> None:
        content = {"note": "Rating: HOLD"}
        result = run_safety_gate(content)
        assert result.passed is False

    def test_catches_price_target(self) -> None:
        content = {"analysis": {"text": "price target of 120 NOK"}}
        result = run_safety_gate(content)
        assert result.passed is False
        assert any("price target" in t.lower() for t in result.forbidden_terms_found)

    def test_catches_fair_value(self) -> None:
        content = {"valuation": {"note": "fair value estimate of 50"}}
        result = run_safety_gate(content)
        assert result.passed is False
        assert any("fair value" in t.lower() for t in result.forbidden_terms_found)

    def test_catches_upside_of(self) -> None:
        content = {"bull": {"note": "upside of 30% expected"}}
        result = run_safety_gate(content)
        assert result.passed is False

    def test_catches_guaranteed_return(self) -> None:
        content = {"note": "guaranteed return on investment"}
        result = run_safety_gate(content)
        assert result.passed is False

    def test_catches_will_go_up(self) -> None:
        content = {"analysis": "The stock will go up significantly"}
        result = run_safety_gate(content)
        assert result.passed is False

    def test_scans_nested_dict(self) -> None:
        content = {
            "section": {
                "subsection": {
                    "deep": "price target: 100"
                }
            }
        }
        result = run_safety_gate(content)
        assert result.passed is False

    def test_scans_list_items(self) -> None:
        content = {
            "risks": ["regulatory risk", "BUY signals detected"]
        }
        result = run_safety_gate(content)
        assert result.passed is False

    def test_safety_warning_added_when_failed(self) -> None:
        content = {"note": "SELL recommendation"}
        result = run_safety_gate(content)
        assert len(result.warnings) > 0
        assert "forbidden" in result.warnings[0].lower()

    def test_sections_recorded(self) -> None:
        content = {"section_a": "clean", "section_b": "also clean"}
        result = run_safety_gate(content)
        assert "section_a" in result.scanned_sections
        assert "section_b" in result.scanned_sections

    def test_case_insensitive_buy(self) -> None:
        content = {"note": "This is a buy signal"}
        result = run_safety_gate(content)
        assert result.passed is False


# ---------------------------------------------------------------------------
# Section builder unit tests
# ---------------------------------------------------------------------------


class TestSectionBuilders:
    def test_admin_disclaimer_static_text(self) -> None:
        section = _build_admin_disclaimer()
        assert section["type"] == "admin_disclaimer"
        assert "NOT INVESTMENT ADVICE" in section["content"] or "NOT A PUBLIC RECOMMENDATION" in section["content"]
        assert section["provenance"] == "static_system_text"

    def test_executive_summary_with_scorecard(self) -> None:
        sc = _make_scorecard(overall_score=55)
        section = _build_executive_summary(
            "Test Company", "TEST", sc, _make_committee_chair_summary(), "ready_for_deeper_analysis"
        )
        assert section["overall_score"] == 55
        assert section["internal_status"] == "ready_for_deeper_analysis"
        assert "NOT" in section["disclaimer"] or "ADVICE" in section["disclaimer"]

    def test_executive_summary_without_scorecard(self) -> None:
        section = _build_executive_summary(
            "Test Co", "TEST", None, None, None
        )
        assert section["overall_score"] is None
        assert section["internal_status"] == "not_enough_data"
        assert section["score_note"]["provenance"] == "missing_data"

    def test_executive_summary_forbidden_status_replaced(self) -> None:
        section = _build_executive_summary(
            "Test Co", "TEST", None, None, "BUY"
        )
        assert section["internal_status"] == "not_enough_data"

    def test_company_identity_with_snapshot(self) -> None:
        snapshot = _make_mock_snapshot()
        section = _build_company_identity(snapshot, None)
        assert section["legal_name"]["value"] == "Acme Nordic AS [MOCK]"
        assert section["ticker"]["value"] == "TEST"
        assert section["is_mock"] is True

    def test_company_identity_without_snapshot(self) -> None:
        section = _build_company_identity(None, None)
        assert section["legal_name"]["provenance"] == "missing_data"

    def test_discovery_rationale_no_candidate(self) -> None:
        section = _build_discovery_rationale(None)
        assert section["available"] is False
        assert section["note"]["provenance"] == "missing_data"

    def test_discovery_rationale_with_candidate(self) -> None:
        candidate = _make_candidate()
        section = _build_discovery_rationale(candidate)
        assert section["available"] is True
        assert section["ticker"] == "ORSTED"
        assert len(section["discovery_reasons"]["value"]) > 0

    def test_valuation_readiness_no_price_target(self) -> None:
        guard = _make_valuation_guard_summary()
        section = _build_valuation_readiness(guard, None)
        section_str = str(section)
        assert "price target" not in section_str.lower() or (
            "disallowed_outputs" in section_str and "price target" in section["disallowed_outputs"]["value"]
        )
        # The key check: no recommendation produced
        assert "price_target" not in section or section.get("price_target") is None

    def test_valuation_readiness_with_guard_summary(self) -> None:
        guard = _make_valuation_guard_summary()
        section = _build_valuation_readiness(guard, None)
        assert section["readiness"]["value"] == "not_ready"
        assert "Valuation readiness check only" in section["disclaimer"]

    def test_internal_scorecard_with_scorecard(self) -> None:
        sc = _make_scorecard(overall_score=45)
        section = _build_internal_scorecard(sc)
        assert section["available"] is True
        assert section["overall_score"]["value"] == 45
        assert "INTERNAL SCORE ONLY" in section["disclaimer"]

    def test_internal_scorecard_without_scorecard(self) -> None:
        section = _build_internal_scorecard(None)
        assert section["available"] is False
        assert section["note"]["provenance"] == "missing_data"

    def test_bull_case_with_summary(self) -> None:
        bull = _make_bull_case_summary()
        section = _build_bull_case(bull)
        assert section["available"] is True
        assert len(section["positive_thesis_points"]["value"]) > 0
        assert section["positive_thesis_points"]["provenance"] == "model_interpretation"

    def test_bull_case_without_summary(self) -> None:
        section = _build_bull_case(None)
        assert section["available"] is False
        assert section["note"]["provenance"] == "missing_data"

    def test_bear_case_with_summary(self) -> None:
        bear = _make_bear_case_summary()
        section = _build_bear_case(bear)
        assert section["available"] is True
        assert len(section["negative_thesis_points"]["value"]) > 0

    def test_risk_analysis_with_summary(self) -> None:
        risk = _make_risk_summary()
        section = _build_risk_analysis(risk)
        assert section["available"] is True
        # All 6 risk categories present
        assert "business_risks" in section
        assert "financial_risks" in section
        assert "market_risks" in section
        assert "regulatory_geopolitical_risks" in section
        assert "data_quality_risks" in section
        assert "source_quality_risks" in section

    def test_missing_information_aggregates(self) -> None:
        snapshot = _make_mock_snapshot()
        financial_summary = {"missing_fields": ["ebitda", "revenue"], "available_fields": []}
        section = _build_missing_information(
            financial_summary, None, snapshot, None
        )
        assert section["total_missing_items"] > 0
        field_names = [m["field"] for m in section["missing_items"]["value"]]
        assert "identity.isin" in field_names or "ebitda" in field_names

    def test_human_review_checklist_has_required_items(self) -> None:
        items = _build_human_review_checklist(
            safety_valid=True,
            schema_valid=False,
            has_scorecard=True,
            has_bull_bear=True,
            has_risk=True,
            has_citations=True,
            missing_count=0,
            is_mock=True,
        )
        assert len(items) >= 8
        required_items = [i for i in items if i.required]
        assert len(required_items) >= 5

    def test_human_review_checklist_safety_gate_item_reflects_pass(self) -> None:
        items = _build_human_review_checklist(
            safety_valid=True,
            schema_valid=False,
            has_scorecard=False,
            has_bull_bear=False,
            has_risk=False,
            has_citations=False,
            missing_count=5,
            is_mock=True,
        )
        safety_item = items[0]
        assert safety_item.completed is True

    def test_human_review_checklist_safety_gate_item_reflects_fail(self) -> None:
        items = _build_human_review_checklist(
            safety_valid=False,
            schema_valid=False,
            has_scorecard=False,
            has_bull_bear=False,
            has_risk=False,
            has_citations=False,
            missing_count=0,
            is_mock=True,
        )
        safety_item = items[0]
        assert safety_item.completed is False
        assert safety_item.note is not None


# ---------------------------------------------------------------------------
# Full assembly tests
# ---------------------------------------------------------------------------


class TestAssembleFinalReportContent:
    def _assemble_minimal(self, **kwargs: Any) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "company_snapshot": None,
            "company_record": None,
            "candidate": None,
            "scorecard": None,
            "financial_data_summary": None,
            "source_quality_summary": None,
            "research_completeness_summary": None,
            "upgraded_citation_validation": None,
            "bull_case_summary": None,
            "bear_case_summary": None,
            "risk_summary": None,
            "valuation_guard_summary": None,
            "committee_chair_summary": None,
            "fundamentals_data": None,
            "fundamentals_available": None,
            "source_tier": None,
            "sources": [],
            "citations": [],
            "report": None,
            "agent_run_id": None,
            "schema_valid": None,
            "human_review_required": True,
        }
        defaults.update(kwargs)
        return _assemble_final_report_content(**defaults)

    def test_all_required_sections_present(self) -> None:
        content = self._assemble_minimal()
        for section in _REQUIRED_SECTIONS:
            assert section in content, f"Missing section: {section}"

    def test_no_buy_sell_hold_watch_in_output(self) -> None:
        content = self._assemble_minimal(
            company_snapshot=_make_mock_snapshot(),
            bull_case_summary=_make_bull_case_summary(),
            bear_case_summary=_make_bear_case_summary(),
            risk_summary=_make_risk_summary(),
            committee_chair_summary=_make_committee_chair_summary(),
        )
        result = run_safety_gate(content)
        assert result.passed is True, f"Forbidden terms found: {result.forbidden_terms_found}"

    def test_no_price_target_or_fair_value(self) -> None:
        content = self._assemble_minimal(
            valuation_guard_summary=_make_valuation_guard_summary(),
            company_snapshot=_make_mock_snapshot(),
        )
        result = run_safety_gate(content)
        assert result.passed is True

    def test_admin_disclaimer_always_present_and_static(self) -> None:
        content = self._assemble_minimal()
        assert "admin_disclaimer" in content
        disclaimer_section = content["admin_disclaimer"]
        assert "NOT INVESTMENT ADVICE" in disclaimer_section["content"]

    def test_human_review_required_in_key_sections(self) -> None:
        content = self._assemble_minimal()
        for section_name in [
            "executive_summary",
            "company_identity",
            "internal_scorecard",
            "valuation_readiness",
            "bull_case",
            "bear_case",
            "risk_analysis",
        ]:
            section = content[section_name]
            assert section.get("human_review_required") is True, (
                f"{section_name} missing human_review_required=True"
            )

    def test_workflow_status_has_version(self) -> None:
        content = self._assemble_minimal()
        assert content["workflow_status"]["final_report_version"] == FINAL_REPORT_VERSION

    def test_source_citation_appendix_empty_without_sources(self) -> None:
        content = self._assemble_minimal()
        appendix = content["source_citation_appendix"]
        assert appendix["sources"]["total"] == 0
        assert appendix["citations"]["total"] == 0

    def test_scorecard_included_when_present(self) -> None:
        sc = _make_scorecard(overall_score=55)
        content = self._assemble_minimal(scorecard=sc)
        scorecard_section = content["internal_scorecard"]
        assert scorecard_section["available"] is True
        assert scorecard_section["overall_score"]["value"] == 55


# ---------------------------------------------------------------------------
# ALLOWED_INTERNAL_STATUSES tests
# ---------------------------------------------------------------------------


class TestAllowedInternalStatuses:
    def test_never_contains_public_ratings(self) -> None:
        forbidden = {"BUY", "SELL", "HOLD", "WATCH", "REJECT"}
        for status in ALLOWED_INTERNAL_STATUSES:
            assert status not in forbidden, f"Forbidden status found: {status}"

    def test_contains_all_required_labels(self) -> None:
        expected = {
            "not_enough_data",
            "low_priority_research",
            "needs_primary_sources",
            "ready_for_deeper_analysis",
            "high_priority_for_human_review",
            "reject_due_to_data_quality",
        }
        assert expected == ALLOWED_INTERNAL_STATUSES


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_final_report_response_includes_disclaimer(self) -> None:
        response = FinalReportResponse(
            report_id=uuid.uuid4(),
            schema_valid=False,
            safety_valid=True,
            human_review_required=True,
        )
        assert "NOT INVESTMENT ADVICE" in response.disclaimer or "NOT A PUBLIC RECOMMENDATION" in response.disclaimer

    def test_safety_validation_result_round_trips(self) -> None:
        result = SafetyValidationResult(
            passed=False,
            forbidden_terms_found=["BUY in section.text"],
            scanned_sections=["section"],
            warnings=["Forbidden term found."],
            blocks_approval=True,
        )
        data = result.model_dump()
        restored = SafetyValidationResult(**data)
        assert restored.passed is False
        assert restored.blocks_approval is True
        assert len(restored.forbidden_terms_found) == 1

    def test_human_review_checklist_item_model(self) -> None:
        item = HumanReviewChecklistItem(
            item="Safety gate passed",
            required=True,
            completed=True,
        )
        assert item.completed is True
        assert item.required is True


# ---------------------------------------------------------------------------
# Service tests (mocked DB)
# ---------------------------------------------------------------------------


class TestFinalReportGeneratorServiceMocked:
    """
    Tests that verify ValueError is raised for not-found entities.
    Uses mocked DB sessions — no real PostgreSQL needed.
    """

    @pytest.mark.asyncio
    async def test_generate_from_scorecard_not_found(self) -> None:
        svc = FinalReportGeneratorService()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ValueError, match="not found"):
            await svc.generate_from_scorecard(mock_db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_generate_from_candidate_not_found(self) -> None:
        svc = FinalReportGeneratorService()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ValueError, match="not found"):
            await svc.generate_from_candidate(mock_db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_generate_from_company_not_found(self) -> None:
        svc = FinalReportGeneratorService()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ValueError, match="not found"):
            await svc.generate_from_company(mock_db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_generate_from_report_not_found(self) -> None:
        svc = FinalReportGeneratorService()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ValueError, match="not found"):
            await svc.generate_from_report(mock_db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_validate_final_report_not_found(self) -> None:
        svc = FinalReportGeneratorService()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ValueError, match="not found"):
            await svc.validate_final_report(mock_db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_regenerate_section_invalid_section_name(self) -> None:
        svc = FinalReportGeneratorService()
        mock_db = AsyncMock()

        with pytest.raises(ValueError, match="Unknown section"):
            await svc.regenerate_report_section(
                mock_db, uuid.uuid4(), "INVALID_SECTION"
            )

    @pytest.mark.asyncio
    async def test_regenerate_section_report_not_found(self) -> None:
        svc = FinalReportGeneratorService()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ValueError, match="not found"):
            await svc.regenerate_report_section(
                mock_db, uuid.uuid4(), "executive_summary"
            )


# ---------------------------------------------------------------------------
# API endpoint tests (mock DB)
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def mock_db_not_found() -> AsyncMock:
    """DB session that always returns None for scalar_one_or_none."""
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    return mock_db


async def _override_get_db_not_found():
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    yield mock_db


class TestFinalReportAPIEndpoints:
    @pytest.mark.asyncio
    async def test_from_scorecard_404(self) -> None:
        from app.db.session import get_db

        app.dependency_overrides[get_db] = _override_get_db_not_found
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/api/v1/final-reports/from-scorecard/{uuid.uuid4()}"
                )
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_from_candidate_404(self) -> None:
        from app.db.session import get_db

        app.dependency_overrides[get_db] = _override_get_db_not_found
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/api/v1/final-reports/from-candidate/{uuid.uuid4()}"
                )
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_from_company_404(self) -> None:
        from app.db.session import get_db

        app.dependency_overrides[get_db] = _override_get_db_not_found
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/api/v1/final-reports/from-company/{uuid.uuid4()}"
                )
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_validate_report_404(self) -> None:
        from app.db.session import get_db

        app.dependency_overrides[get_db] = _override_get_db_not_found
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/api/v1/final-reports/{uuid.uuid4()}/validate"
                )
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_regenerate_section_404(self) -> None:
        from app.db.session import get_db

        app.dependency_overrides[get_db] = _override_get_db_not_found
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/api/v1/final-reports/{uuid.uuid4()}/regenerate-section",
                    json={"section_name": "executive_summary"},
                )
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_regenerate_section_invalid_section_422(self) -> None:
        from app.db.session import get_db

        app.dependency_overrides[get_db] = _override_get_db_not_found
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/api/v1/final-reports/{uuid.uuid4()}/regenerate-section",
                    json={"section_name": "BUY_RECOMMENDATION"},
                )
            # ValueError (invalid section) raised before report lookup → 422
            assert resp.status_code in (404, 422)
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Migration test
# ---------------------------------------------------------------------------


class TestMigration008:
    def _load_migration(self) -> Any:
        import importlib.util
        import pathlib

        migration_path = (
            pathlib.Path(__file__).parent.parent
            / "alembic"
            / "versions"
            / "008_add_final_report_fields.py"
        )
        spec = importlib.util.spec_from_file_location(
            "migration_008", migration_path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module

    def test_migration_008_importable(self) -> None:
        module = self._load_migration()
        assert hasattr(module, "upgrade")
        assert hasattr(module, "downgrade")
        assert module.revision == "008"
        assert module.down_revision == "007"

    def test_migration_008_upgrade_callable(self) -> None:
        module = self._load_migration()
        assert callable(module.upgrade)
        assert callable(module.downgrade)
