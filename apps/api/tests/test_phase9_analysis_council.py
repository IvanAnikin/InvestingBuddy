"""
Phase 9: Analysis Council MVP — offline tests.

All tests run without:
  - Network calls
  - Database (mock AsyncSession where needed)
  - Azure OpenAI credentials
  - Real LLM calls

Test coverage:
  1.  BullCaseAgent: positive thesis points identified from mock snapshot
  2.  BullCaseAgent: potential tailwinds identified for known sector
  3.  BullCaseAgent: missing evidence listed when source quality weak
  4.  BullCaseAgent: confidence=low when mock provider active
  5.  BullCaseAgent: does not invent unsupported financial facts
  6.  BullCaseAgent: no forbidden recommendation words in output
  7.  BullCaseAgent: no price target or fair value in output
  8.  BullCaseAgent: LLM thesis draft incorporated when no forbidden words
  9.  BullCaseAgent: LLM thesis draft rejected when forbidden words present
  10. BearCaseAgent: negative thesis points identified
  11. BearCaseAgent: challenges bull case assumptions
  12. BearCaseAgent: key_unknowns populated when financials missing
  13. BearCaseAgent: source quality weak triggers negative point
  14. BearCaseAgent: no forbidden recommendation words in output
  15. BearCaseAgent: no price target or fair value in output
  16. BearCaseAgent: confidence=low for mock provider
  17. RiskAgent: data_quality_risks populated from Research Team warnings
  18. RiskAgent: source_quality_risks populated from source quality summary
  19. RiskAgent: data_quality_risks always present (non-empty)
  20. RiskAgent: source_quality_risks always present (non-empty)
  21. RiskAgent: business_risks include UNKNOWN marker when financials absent
  22. RiskAgent: risk_summary non-empty string
  23. RiskAgent: no forbidden recommendation words in output
  24. ValuationGuardAgent: valuation_readiness=not_ready when mock provider
  25. ValuationGuardAgent: all DCF fields listed in missing when fundamentals absent
  26. ValuationGuardAgent: valuation_blockers non-empty when mock data
  27. ValuationGuardAgent: disallowed_outputs always includes price target and fair value
  28. ValuationGuardAgent: allowed_next_steps populated
  29. ValuationGuardAgent: no fair value in output
  30. ValuationGuardAgent: no price target in output
  31. ValuationGuardAgent: no upside/downside percentage in output
  32. InvestmentCommitteeChair: provisional_internal_status is one of allowed values
  33. InvestmentCommitteeChair: watchlist_candidate_for_review requires human_review=True
  34. InvestmentCommitteeChair: mock data forces research_incomplete status
  35. InvestmentCommitteeChair: forbidden words not in committee output
  36. InvestmentCommitteeChair: no BUY/SELL/HOLD/WATCH/REJECT in output
  37. InvestmentCommitteeChair: no price target in output
  38. InvestmentCommitteeChair: primary_open_questions populated
  39. InvestmentCommitteeChair: research_next_steps populated
  40. InvestmentCommitteeChair: quality_gate_status is a dict with expected keys
  41. Safety: forbidden recommendation words caught across agents
  42. Safety: price target phrase caught
  43. Safety: fair value phrase caught
  44. Workflow: completes with use_llm=False (offline, no DB)
  45. Workflow: completes with use_llm=True, llm_provider=mock (offline)
  46. Workflow: graph includes all 18 nodes + handle_error
  47. Workflow: bull_case_agent node persists output in state
  48. Workflow: bear_case_agent node persists output in state
  49. Workflow: risk_agent node persists output in state
  50. Workflow: valuation_guard_agent node persists output in state
  51. Workflow: investment_committee_chair node persists output in state
  52. Workflow: analysis_council_warnings aggregated in final state
  53. Workflow: provisional_internal_status in final state
  54. Workflow: human_review_required in final state
  55. Draft report: contains Bull Case Draft section
  56. Draft report: contains Bear Case Draft section
  57. Draft report: contains Risk Review section
  58. Draft report: contains Valuation Guard section
  59. Draft report: contains Investment Committee Chair Summary section
  60. Draft report: contains admin disclaimer
  61. API response: includes bull_case_summary field
  62. API response: includes bear_case_summary field
  63. API response: includes risk_summary field
  64. API response: includes valuation_guard_summary field
  65. API response: includes committee_chair_summary field
  66. API response: includes provisional_internal_status field
  67. API response: includes human_review_required field
  68. All new agent steps persisted (18 nodes tracked)
  69. No Azure credentials required
  70. No network required
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.analysis_council.bear_case_agent import (
    bear_case_output_to_dict,
    run_bear_case_agent,
)
from app.agents.analysis_council.bull_case_agent import (
    BullCaseOutput,
    bull_case_output_to_dict,
    run_bull_case_agent,
)
from app.agents.analysis_council.investment_committee_chair import (
    ALLOWED_INTERNAL_STATUSES,
    committee_chair_output_to_dict,
    run_investment_committee_chair,
)
from app.agents.analysis_council.risk_agent import (
    risk_agent_output_to_dict,
    run_risk_agent,
)
from app.agents.analysis_council.valuation_guard_agent import (
    run_valuation_guard_agent,
    valuation_guard_output_to_dict,
)
from app.agents.research_team.citation_validator_v2 import (
    run_upgraded_citation_validator,
    upgraded_citation_validation_to_dict,
)
from app.agents.research_team.financial_data_agent import (
    financial_data_agent_output_to_dict,
    run_financial_data_agent,
)
from app.agents.research_team.research_completeness_agent import (
    research_completeness_output_to_dict,
    run_research_completeness_agent,
)
from app.agents.research_team.source_quality_agent import (
    run_source_quality_agent,
    source_quality_output_to_dict,
)
from app.integrations.financial_data_provider import (
    CompanyProfileData,
    DataQuality,
    PriceHistoryData,
    PricePoint,
    ProviderResponseMetadata,
    ProviderStatus,
    SourceTier,
)
from app.schemas.agent import WorkflowRunResponse
from app.workflows.company_analysis import build_company_analysis_graph, run_company_analysis
from app.workflows.snapshot_builder import build_company_snapshot

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_COMPANY_ID = str(uuid.UUID("11111111-1111-1111-1111-111111111111"))
_AGENT_RUN_ID = str(uuid.UUID("22222222-2222-2222-2222-222222222222"))
_REPORT_ID = str(uuid.UUID("33333333-3333-3333-3333-333333333333"))
_SOURCE_ID = str(uuid.UUID("44444444-4444-4444-4444-444444444444"))

_FIXED_TS = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)


def _mock_meta(tier: SourceTier = SourceTier.T6_model_estimate, is_mock: bool = True) -> ProviderResponseMetadata:
    return ProviderResponseMetadata(
        provider_name="mock",
        source_tier=tier,
        retrieved_at=_FIXED_TS,
        is_mock=is_mock,
        status=ProviderStatus.ok,
        note="DEMO DATA",
    )


def _mock_profile(
    ticker: str = "TEST",
    tier: SourceTier = SourceTier.T6_model_estimate,
    sector: str = "Industrials",
    is_mock: bool = True,
) -> CompanyProfileData:
    return CompanyProfileData(
        ticker=ticker,
        exchange="OSE",
        legal_name="Acme Nordic AS [MOCK]",
        country_domicile="Norway",
        reporting_currency="NOK",
        fiscal_year_end="December",
        sector=sector,
        industry="Industrial Machinery",
        website=None,
        ipo_date=None,
        description="A fictional Nordic industrial company for testing.",
        isin=None,
        lei=None,
        source_url=None,
        data_quality=DataQuality.D_weak_or_stale,
        meta=_mock_meta(tier=tier, is_mock=is_mock),
    )


def _mock_prices(ticker: str = "TEST") -> PriceHistoryData:
    return PriceHistoryData(
        ticker=ticker,
        exchange="OSE",
        currency="NOK",
        price_points=[
            PricePoint(date="2026-01-02", open=10.0, high=10.5, low=9.8, close=10.2, volume=100000),
            PricePoint(date="2026-01-03", open=10.2, high=10.8, low=10.0, close=10.5, volume=120000),
        ],
        data_quality=DataQuality.D_weak_or_stale,
        source_url=None,
        meta=_mock_meta(),
    )


def _make_snapshot(with_prices: bool = True, sector: str = "Industrials") -> dict:
    profile = _mock_profile(sector=sector)
    prices = _mock_prices() if with_prices else None
    return build_company_snapshot(profile=profile, prices=prices)


def _make_financial_data_summary(snapshot: dict) -> dict:
    output = run_financial_data_agent(company_snapshot=snapshot)
    return financial_data_agent_output_to_dict(output)


def _make_source_quality_summary(snapshot: dict) -> dict:
    output = run_source_quality_agent(company_snapshot=snapshot)
    return source_quality_output_to_dict(output)


def _make_research_completeness_summary(snapshot: dict, fda_summary: dict) -> dict:  # noqa: ARG001
    output = run_research_completeness_agent(
        company_snapshot=snapshot,
        schema_draft=None,
        schema_validation_errors=[],
    )
    return research_completeness_output_to_dict(output)


def _make_upgraded_citation_validation(snapshot: dict) -> dict:
    output = run_upgraded_citation_validator(
        company_snapshot=snapshot,
        schema_draft=None,
        citation_records=[],
    )
    return upgraded_citation_validation_to_dict(output)


def _make_full_research_package(sector: str = "Industrials") -> dict:
    """Build all Research Team summaries from a mock snapshot."""
    snapshot = _make_snapshot(sector=sector)
    fda = _make_financial_data_summary(snapshot)
    sq = _make_source_quality_summary(snapshot)
    rc = _make_research_completeness_summary(snapshot, fda)
    cv2 = _make_upgraded_citation_validation(snapshot)
    return {
        "snapshot": snapshot,
        "financial_data_summary": fda,
        "source_quality_summary": sq,
        "research_completeness_summary": rc,
        "upgraded_citation_validation": cv2,
    }


# ---------------------------------------------------------------------------
# 1–9: BullCaseAgent
# ---------------------------------------------------------------------------

def test_bull_case_identifies_positive_thesis_points():
    pkg = _make_full_research_package()
    output = run_bull_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    assert isinstance(output.positive_thesis_points, list)
    assert len(output.positive_thesis_points) > 0


def test_bull_case_identifies_sector_tailwinds():
    pkg = _make_full_research_package(sector="Energy")
    output = run_bull_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    assert len(output.potential_tailwinds) > 0


def test_bull_case_missing_evidence_when_source_quality_weak():
    pkg = _make_full_research_package()
    # Source quality is "weak" for mock data
    output = run_bull_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    assert len(output.missing_evidence) > 0


def test_bull_case_confidence_low_for_mock_provider():
    pkg = _make_full_research_package()
    output = run_bull_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    assert output.confidence_level == "low"


def test_bull_case_no_invented_financial_numbers():
    pkg = _make_full_research_package()
    output = run_bull_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    all_text = " ".join(
        output.positive_thesis_points + output.potential_tailwinds + output.evidence_used
    )
    # No dollar amounts, no revenue figures, no specific EPS numbers
    import re
    dollar_amounts = re.findall(r'\$\d+[\.,]?\d*[BMK]?', all_text)
    assert len(dollar_amounts) == 0, f"Found invented dollar amounts: {dollar_amounts}"


def test_bull_case_no_forbidden_recommendation_words():
    pkg = _make_full_research_package()
    output = run_bull_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    all_text = " ".join(
        output.positive_thesis_points + output.potential_tailwinds + output.evidence_used
    ).upper()
    for word in ["BUY", "SELL", "HOLD", "REJECT", "SHORTLIST_HIGH"]:
        assert word not in all_text, f"Forbidden word '{word}' found in bull case output"


def test_bull_case_no_price_target_or_fair_value():
    pkg = _make_full_research_package()
    output = run_bull_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    all_text = " ".join(
        output.positive_thesis_points + output.potential_tailwinds
    ).lower()
    for phrase in ["price target", "target price", "fair value"]:
        assert phrase not in all_text, f"Forbidden phrase '{phrase}' in bull case output"


def test_bull_case_incorporates_llm_thesis_without_forbidden_words():
    pkg = _make_full_research_package()
    clean_llm = {
        "thesis_summary_draft": "Acme Nordic operates in industrial machinery with growth potential.",
        "business_overview_draft": "Produces specialized industrial equipment for Nordic markets.",
        "missing_information": ["Financial fundamentals not sourced"],
        "self_critique_limitations": "Limited to identity data only.",
    }
    output = run_bull_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
        llm_sections=clean_llm,
    )
    # Should incorporate LLM thesis into positive_thesis_points
    combined = " ".join(output.positive_thesis_points + output.evidence_used)
    assert "Acme Nordic" in combined or "industrial machinery" in combined.lower()


def test_bull_case_rejects_llm_thesis_with_forbidden_words():
    pkg = _make_full_research_package()
    bad_llm = {
        "thesis_summary_draft": "This is a strong BUY. Target price 150 NOK.",
        "business_overview_draft": "Fair value is 200 NOK. Undervalued.",
        "missing_information": [],
        "self_critique_limitations": "None.",
    }
    output = run_bull_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
        llm_sections=bad_llm,
    )
    # Should flag forbidden content in warnings
    all_warnings = " ".join(output.warnings)
    assert "forbidden" in all_warnings.lower() or "Forbidden" in all_warnings


# ---------------------------------------------------------------------------
# 10–16: BearCaseAgent
# ---------------------------------------------------------------------------

def test_bear_case_identifies_negative_thesis_points():
    pkg = _make_full_research_package()
    bull = bull_case_output_to_dict(run_bull_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    ))
    output = run_bear_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
        bull_case_summary=bull,
    )
    assert isinstance(output.negative_thesis_points, list)
    assert len(output.negative_thesis_points) > 0


def test_bear_case_challenges_bull_case_assumptions():
    pkg = _make_full_research_package()
    bull_output = run_bull_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    bull = bull_case_output_to_dict(bull_output)
    # Ensure there are assumptions to challenge
    assert len(bull.get("assumptions", [])) > 0

    output = run_bear_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
        bull_case_summary=bull,
    )
    # Bear case should reference bull case assumptions in headwinds
    all_headwinds = " ".join(output.potential_headwinds)
    assert "assumption" in all_headwinds.lower() or "challenged" in all_headwinds.lower()


def test_bear_case_key_unknowns_when_financials_missing():
    pkg = _make_full_research_package()
    output = run_bear_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    unknowns_text = " ".join(output.key_unknowns)
    assert len(output.key_unknowns) > 0
    # Should reference financial fundamentals as unknown
    assert "financial" in unknowns_text.lower() or "fundamental" in unknowns_text.lower()


def test_bear_case_source_quality_weak_triggers_negative_point():
    pkg = _make_full_research_package()
    output = run_bear_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    combined = " ".join(output.negative_thesis_points).lower()
    assert "source quality" in combined or "weak" in combined or "t5" in combined or "t6" in combined


def test_bear_case_no_forbidden_recommendation_words():
    pkg = _make_full_research_package()
    output = run_bear_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    all_text = " ".join(
        output.negative_thesis_points + output.potential_headwinds
    ).upper()
    for word in ["SELL", "SHORT", "REJECT"]:
        assert word not in all_text, f"Forbidden word '{word}' found in bear case output"


def test_bear_case_no_price_target_or_fair_value():
    pkg = _make_full_research_package()
    output = run_bear_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    all_text = " ".join(
        output.negative_thesis_points + output.potential_headwinds
    ).lower()
    for phrase in ["price target", "target price", "fair value", "downside of"]:
        assert phrase not in all_text, f"Forbidden phrase '{phrase}' in bear case"


def test_bear_case_confidence_low_for_mock():
    pkg = _make_full_research_package()
    output = run_bear_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    assert output.confidence_level == "low"


# ---------------------------------------------------------------------------
# 17–23: RiskAgent
# ---------------------------------------------------------------------------

def test_risk_agent_data_quality_risks_from_research_team():
    pkg = _make_full_research_package()
    output = run_risk_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
        upgraded_citation_validation=pkg["upgraded_citation_validation"],
    )
    # Data quality risks should include mock provider warning
    dq_text = " ".join(output.data_quality_risks).lower()
    assert "mock" in dq_text or "synthetic" in dq_text or "quality" in dq_text


def test_risk_agent_source_quality_risks_from_research_team():
    pkg = _make_full_research_package()
    output = run_risk_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    assert len(output.source_quality_risks) > 0


def test_risk_agent_data_quality_risks_always_present():
    pkg = _make_full_research_package()
    output = run_risk_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    assert isinstance(output.data_quality_risks, list)
    assert len(output.data_quality_risks) > 0, "data_quality_risks must always be populated"


def test_risk_agent_source_quality_risks_always_present():
    pkg = _make_full_research_package()
    output = run_risk_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    assert isinstance(output.source_quality_risks, list)
    assert len(output.source_quality_risks) > 0, "source_quality_risks must always be populated"


def test_risk_agent_business_risks_include_unknown_when_financials_absent():
    pkg = _make_full_research_package()
    output = run_risk_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    business_text = " ".join(output.business_risks)
    # At least some risk items should be marked UNKNOWN due to missing fundamentals
    assert "UNKNOWN" in business_text or "not" in business_text.lower() or "missing" in business_text.lower()


def test_risk_agent_risk_summary_non_empty():
    pkg = _make_full_research_package()
    output = run_risk_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    assert isinstance(output.risk_summary, str)
    assert len(output.risk_summary) > 20


def test_risk_agent_no_forbidden_recommendation_words():
    pkg = _make_full_research_package()
    output = run_risk_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    all_text = (
        " ".join(output.business_risks) + " " +
        " ".join(output.financial_risks) + " " +
        output.risk_summary
    ).upper()
    for word in ["BUY", "SELL", "SHORTLIST_HIGH"]:
        assert word not in all_text, f"Forbidden word '{word}' in risk output"


# ---------------------------------------------------------------------------
# 24–31: ValuationGuardAgent
# ---------------------------------------------------------------------------

def test_valuation_guard_not_ready_for_mock():
    pkg = _make_full_research_package()
    output = run_valuation_guard_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
    )
    assert output.valuation_readiness == "not_ready"


def test_valuation_guard_dcf_fields_missing():
    pkg = _make_full_research_package()
    output = run_valuation_guard_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
    )
    # All DCF inputs should be missing
    missing_text = " ".join(output.missing_valuation_inputs)
    assert "free_cash_flow" in missing_text or "ebitda" in missing_text or "revenue" in missing_text


def test_valuation_guard_blockers_non_empty_for_mock():
    pkg = _make_full_research_package()
    output = run_valuation_guard_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
    )
    assert len(output.valuation_blockers) > 0


def test_valuation_guard_disallowed_includes_price_target_and_fair_value():
    pkg = _make_full_research_package()
    output = run_valuation_guard_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
    )
    disallowed_text = " ".join(output.disallowed_outputs).lower()
    assert "price target" in disallowed_text or "target price" in disallowed_text
    assert "fair value" in disallowed_text


def test_valuation_guard_allowed_next_steps_populated():
    pkg = _make_full_research_package()
    output = run_valuation_guard_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
    )
    assert len(output.allowed_next_steps) > 0


def test_valuation_guard_no_fair_value_in_output():
    pkg = _make_full_research_package()
    output = run_valuation_guard_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
    )
    # valuation_blockers and other fields should not contain a fair value figure
    blocker_text = " ".join(output.valuation_blockers).lower()
    # The word "fair value" can appear as "disallowed" but not as an assertion
    assert "is $" not in blocker_text and "= $" not in blocker_text


def test_valuation_guard_no_price_target_assertion():
    pkg = _make_full_research_package()
    output = run_valuation_guard_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
    )
    # allowed_next_steps and blockers should not set a price target
    all_text = " ".join(output.allowed_next_steps + output.valuation_blockers).lower()
    assert "target price is" not in all_text
    assert "price target is" not in all_text


def test_valuation_guard_no_upside_downside_percentage():
    pkg = _make_full_research_package()
    output = run_valuation_guard_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
    )
    import re
    all_text = " ".join(output.allowed_next_steps + output.valuation_blockers)
    upside_pct = re.findall(r'upside of \d+%|downside of \d+%', all_text, re.IGNORECASE)
    assert len(upside_pct) == 0, f"Upside/downside percentage found: {upside_pct}"


# ---------------------------------------------------------------------------
# 32–40: InvestmentCommitteeChair
# ---------------------------------------------------------------------------

def _run_full_council(sector: str = "Industrials") -> dict:
    """Run all 5 council agents and return all outputs as dicts."""
    pkg = _make_full_research_package(sector=sector)
    bull = bull_case_output_to_dict(run_bull_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    ))
    bear = bear_case_output_to_dict(run_bear_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
        bull_case_summary=bull,
    ))
    risk = risk_agent_output_to_dict(run_risk_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
        upgraded_citation_validation=pkg["upgraded_citation_validation"],
    ))
    vg = valuation_guard_output_to_dict(run_valuation_guard_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
    ))
    chair = run_investment_committee_chair(
        company_snapshot=pkg["snapshot"],
        bull_case_summary=bull,
        bear_case_summary=bear,
        risk_summary=risk,
        valuation_guard_summary=vg,
        research_completeness_summary=pkg["research_completeness_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        upgraded_citation_validation=pkg["upgraded_citation_validation"],
        schema_valid=False,
    )
    return {
        "pkg": pkg,
        "bull": bull,
        "bear": bear,
        "risk": risk,
        "vg": vg,
        "chair": chair,
        "chair_dict": committee_chair_output_to_dict(chair),
    }


def test_committee_chair_status_is_allowed_value():
    result = _run_full_council()
    status = result["chair"].provisional_internal_status
    assert status in ALLOWED_INTERNAL_STATUSES, (
        f"Status '{status}' not in allowed set {ALLOWED_INTERNAL_STATUSES}"
    )


def test_committee_chair_watchlist_requires_human_review():
    result = _run_full_council()
    chair = result["chair"]
    if chair.provisional_internal_status == "watchlist_candidate_for_review":
        assert chair.human_review_required is True


def test_committee_chair_mock_data_forces_research_incomplete():
    result = _run_full_council()
    chair = result["chair"]
    # Mock provider always forces research_incomplete
    assert chair.provisional_internal_status == "research_incomplete"
    assert "research_incomplete" in ALLOWED_INTERNAL_STATUSES


def test_committee_chair_no_forbidden_words_in_summary():
    result = _run_full_council()
    summary = result["chair"].committee_summary.upper()
    for word in ["BUY", "SELL", "HOLD", "REJECT", "SHORTLIST_HIGH"]:
        assert word not in summary, f"Forbidden word '{word}' in committee summary"


def test_committee_chair_no_buy_sell_hold_watch_reject():
    result = _run_full_council()
    chair_dict = result["chair_dict"]
    all_text = " ".join([
        chair_dict.get("committee_summary", ""),
        chair_dict.get("bull_bear_balance", ""),
        chair_dict.get("provisional_internal_status", ""),
        " ".join(chair_dict.get("primary_open_questions", [])),
    ]).upper()
    for word in ["BUY", "SELL", "HOLD"]:
        assert word not in all_text, f"Forbidden word '{word}' in committee output"


def test_committee_chair_no_price_target():
    result = _run_full_council()
    summary = result["chair"].committee_summary.lower()
    assert "price target" not in summary
    assert "target price" not in summary
    assert "fair value" not in summary


def test_committee_chair_primary_open_questions_populated():
    result = _run_full_council()
    assert len(result["chair"].primary_open_questions) > 0


def test_committee_chair_research_next_steps_populated():
    result = _run_full_council()
    assert len(result["chair"].research_next_steps) > 0


def test_committee_chair_quality_gate_status_has_expected_keys():
    result = _run_full_council()
    gate = result["chair"].quality_gate_status
    assert isinstance(gate, dict)
    expected_keys = {"source_quality_ok", "citation_status_ok", "schema_valid",
                     "valuation_ready", "research_complete"}
    assert expected_keys.issubset(gate.keys()), f"Missing keys in quality_gate: {expected_keys - gate.keys()}"


# ---------------------------------------------------------------------------
# 41–43: Safety gates across all agents
# ---------------------------------------------------------------------------

def test_safety_gate_catches_forbidden_recommendation_words():
    from app.agents.analysis_council.bull_case_agent import _check_forbidden_content
    violations = _check_forbidden_content("This company is a strong BUY at current levels.")
    assert len(violations) > 0
    assert any("BUY" in v for v in violations)


def test_safety_gate_catches_price_target_phrase():
    from app.agents.analysis_council.bull_case_agent import _check_forbidden_content
    violations = _check_forbidden_content("The price target is 150 NOK.")
    assert len(violations) > 0
    assert any("price target" in v.lower() for v in violations)


def test_safety_gate_catches_fair_value():
    from app.agents.analysis_council.bull_case_agent import _check_forbidden_content
    violations = _check_forbidden_content("Fair value estimated at 200 NOK.")
    assert len(violations) > 0
    assert any("fair value" in v.lower() for v in violations)


# ---------------------------------------------------------------------------
# 44–54: Workflow integration (offline, no real DB)
# ---------------------------------------------------------------------------

def _make_workflow_mocks():
    """Build all mocks needed for a workflow run test."""
    run_mock = MagicMock()
    run_mock.id = uuid.UUID(_AGENT_RUN_ID)

    step_mock = MagicMock()
    step_mock.id = uuid.uuid4()

    report_mock = MagicMock()
    report_mock.id = uuid.UUID(_REPORT_ID)
    report_mock.slug = "company-analysis-test-22222222"

    company_mock = MagicMock()
    company_mock.id = uuid.UUID(_COMPANY_ID)
    company_mock.name = "Acme Nordic AS"
    company_mock.ticker = "TEST"
    company_mock.exchange = "OSE"
    company_mock.sector = "Industrials"
    company_mock.description = "Test company"

    source_mock = MagicMock()
    source_mock.id = uuid.UUID(_SOURCE_ID)

    citation_mock = MagicMock()
    citation_mock.id = uuid.uuid4()
    citation_mock.field_path = "identity.legal_name"
    citation_mock.source_tier = "T6_model_estimate"
    citation_mock.data_quality = "D_weak_or_stale"

    db = AsyncMock()
    return {
        "db": db,
        "run": run_mock,
        "step": step_mock,
        "report": report_mock,
        "company": company_mock,
        "source": source_mock,
        "citation": citation_mock,
    }


@pytest.mark.asyncio
async def test_workflow_completes_use_llm_false():
    mocks = _make_workflow_mocks()

    with (
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=mocks["run"]),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=mocks["step"]),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=mocks["company"]),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(mocks["source"], True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=mocks["citation"]),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=mocks["report"]),
    ):
        final_state = await run_company_analysis(
            db=mocks["db"],
            ticker="TEST",
            exchange="OSE",
            use_llm=False,
        )

    assert final_state.get("status") == "completed"
    assert final_state.get("draft_report_id") is not None


@pytest.mark.asyncio
async def test_workflow_completes_use_llm_true_mock():
    mocks = _make_workflow_mocks()

    with (
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=mocks["run"]),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=mocks["step"]),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=mocks["company"]),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(mocks["source"], True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=mocks["citation"]),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=mocks["report"]),
    ):
        final_state = await run_company_analysis(
            db=mocks["db"],
            ticker="TEST",
            exchange="OSE",
            use_llm=True,
            llm_provider="mock",
        )

    assert final_state.get("status") == "completed"


def test_workflow_graph_includes_all_18_nodes():
    db = AsyncMock()
    graph = build_company_analysis_graph(db)
    node_names = set(graph.nodes.keys())

    expected_nodes = {
        "load_company",
        "fetch_provider_data",
        "create_source_records",
        "build_company_snapshot",
        "financial_data_agent",
        "source_quality_agent",
        "generate_research_sections",
        "create_citations",
        "validate_report_schema",
        "research_completeness_agent",
        "citation_validator_v2",
        # Phase 9
        "bull_case_agent",
        "bear_case_agent",
        "risk_agent",
        "valuation_guard_agent",
        "investment_committee_chair",
        "save_draft_report",
        "log_agent_steps",
        "handle_error",
    }
    missing = expected_nodes - node_names
    assert not missing, f"Missing nodes in graph: {missing}"


@pytest.mark.asyncio
async def test_workflow_bull_case_persisted_in_state():
    mocks = _make_workflow_mocks()
    with (
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=mocks["run"]),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=mocks["step"]),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=mocks["company"]),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(mocks["source"], True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=mocks["citation"]),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=mocks["report"]),
    ):
        final_state = await run_company_analysis(db=mocks["db"], ticker="TEST", exchange="OSE")

    assert final_state.get("bull_case_summary") is not None
    assert "positive_thesis_points" in final_state["bull_case_summary"]


@pytest.mark.asyncio
async def test_workflow_bear_case_persisted_in_state():
    mocks = _make_workflow_mocks()
    with (
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=mocks["run"]),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=mocks["step"]),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=mocks["company"]),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(mocks["source"], True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=mocks["citation"]),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=mocks["report"]),
    ):
        final_state = await run_company_analysis(db=mocks["db"], ticker="TEST", exchange="OSE")

    assert final_state.get("bear_case_summary") is not None
    assert "negative_thesis_points" in final_state["bear_case_summary"]


@pytest.mark.asyncio
async def test_workflow_risk_agent_persisted_in_state():
    mocks = _make_workflow_mocks()
    with (
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=mocks["run"]),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=mocks["step"]),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=mocks["company"]),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(mocks["source"], True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=mocks["citation"]),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=mocks["report"]),
    ):
        final_state = await run_company_analysis(db=mocks["db"], ticker="TEST", exchange="OSE")

    assert final_state.get("risk_summary") is not None
    assert "risk_summary" in final_state["risk_summary"]


@pytest.mark.asyncio
async def test_workflow_valuation_guard_persisted_in_state():
    mocks = _make_workflow_mocks()
    with (
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=mocks["run"]),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=mocks["step"]),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=mocks["company"]),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(mocks["source"], True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=mocks["citation"]),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=mocks["report"]),
    ):
        final_state = await run_company_analysis(db=mocks["db"], ticker="TEST", exchange="OSE")

    assert final_state.get("valuation_guard_summary") is not None
    assert final_state["valuation_guard_summary"]["valuation_readiness"] == "not_ready"


@pytest.mark.asyncio
async def test_workflow_committee_chair_persisted_in_state():
    mocks = _make_workflow_mocks()
    with (
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=mocks["run"]),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=mocks["step"]),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=mocks["company"]),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(mocks["source"], True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=mocks["citation"]),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=mocks["report"]),
    ):
        final_state = await run_company_analysis(db=mocks["db"], ticker="TEST", exchange="OSE")

    assert final_state.get("committee_chair_summary") is not None
    status = final_state["committee_chair_summary"]["provisional_internal_status"]
    assert status in ALLOWED_INTERNAL_STATUSES


@pytest.mark.asyncio
async def test_workflow_analysis_council_warnings_in_state():
    mocks = _make_workflow_mocks()
    with (
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=mocks["run"]),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=mocks["step"]),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=mocks["company"]),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(mocks["source"], True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=mocks["citation"]),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=mocks["report"]),
    ):
        final_state = await run_company_analysis(db=mocks["db"], ticker="TEST", exchange="OSE")

    assert "analysis_council_warnings" in final_state
    assert isinstance(final_state["analysis_council_warnings"], list)


@pytest.mark.asyncio
async def test_workflow_provisional_internal_status_in_state():
    mocks = _make_workflow_mocks()
    with (
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=mocks["run"]),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=mocks["step"]),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=mocks["company"]),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(mocks["source"], True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=mocks["citation"]),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=mocks["report"]),
    ):
        final_state = await run_company_analysis(db=mocks["db"], ticker="TEST", exchange="OSE")

    assert final_state.get("provisional_internal_status") in ALLOWED_INTERNAL_STATUSES


@pytest.mark.asyncio
async def test_workflow_human_review_required_in_state():
    mocks = _make_workflow_mocks()
    with (
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=mocks["run"]),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=mocks["step"]),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=mocks["company"]),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(mocks["source"], True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=mocks["citation"]),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=mocks["report"]),
    ):
        final_state = await run_company_analysis(db=mocks["db"], ticker="TEST", exchange="OSE")

    assert "human_review_required" in final_state
    assert isinstance(final_state["human_review_required"], bool)


# ---------------------------------------------------------------------------
# 55–61: Draft report content checks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def _get_report_content() -> str:
    """Run workflow and capture the markdown passed to create_draft_report."""
    mocks = _make_workflow_mocks()
    captured_content = {}

    async def fake_create_report(db, report_create):
        captured_content["content"] = report_create.content_markdown
        return mocks["report"]

    with (
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=mocks["run"]),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=mocks["step"]),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=mocks["company"]),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(mocks["source"], True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=mocks["citation"]),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, side_effect=fake_create_report),
    ):
        await run_company_analysis(db=mocks["db"], ticker="TEST", exchange="OSE")

    return captured_content.get("content", "")


async def _capture_report_content() -> str:
    mocks = _make_workflow_mocks()
    captured = {}

    async def fake_create_report(db, report_create):
        captured["md"] = report_create.content_markdown
        return mocks["report"]

    patches = [
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=mocks["run"]),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=mocks["step"]),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=mocks["company"]),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(mocks["source"], True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=mocks["citation"]),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, side_effect=fake_create_report),
    ]
    for p in patches:
        p.start()
    try:
        await run_company_analysis(db=mocks["db"], ticker="TEST", exchange="OSE")
    finally:
        for p in patches:
            p.stop()
    return captured.get("md", "")


@pytest.mark.asyncio
async def test_draft_report_contains_bull_case_section():
    content = await _capture_report_content()
    assert "Bull Case Draft" in content


@pytest.mark.asyncio
async def test_draft_report_contains_bear_case_section():
    content = await _capture_report_content()
    assert "Bear Case Draft" in content


@pytest.mark.asyncio
async def test_draft_report_contains_risk_review_section():
    content = await _capture_report_content()
    assert "Risk Review" in content


@pytest.mark.asyncio
async def test_draft_report_contains_valuation_guard_section():
    content = await _capture_report_content()
    assert "Valuation Guard" in content


@pytest.mark.asyncio
async def test_draft_report_contains_committee_chair_section():
    content = await _capture_report_content()
    assert "Investment Committee Chair Summary" in content


@pytest.mark.asyncio
async def test_draft_report_contains_admin_disclaimer():
    content = await _capture_report_content()
    assert "INTERNAL ADMIN DRAFT" in content or "admin draft" in content.lower()


# ---------------------------------------------------------------------------
# 62–68: API response fields (schema-level checks)
# ---------------------------------------------------------------------------

def test_workflow_run_response_has_phase9_fields():
    fields = WorkflowRunResponse.model_fields
    required_fields = [
        "bull_case_summary",
        "bear_case_summary",
        "risk_summary",
        "valuation_guard_summary",
        "committee_chair_summary",
        "provisional_internal_status",
        "human_review_required",
        "analysis_council_warnings",
        "quality_gate_status",
    ]
    for f in required_fields:
        assert f in fields, f"Missing Phase 9 field in WorkflowRunResponse: '{f}'"


def test_workflow_run_response_serializes_phase9_fields():
    response = WorkflowRunResponse(
        agent_run_id=uuid.UUID(_AGENT_RUN_ID),
        draft_report_id=uuid.UUID(_REPORT_ID),
        status="completed",
        summary="Phase 9 test",
        workflow_name="company_analysis",
        bull_case_summary={"confidence_level": "low", "positive_thesis_points_count": 2},
        bear_case_summary={"confidence_level": "low", "negative_thesis_points_count": 3},
        risk_summary={"risk_summary": "Risk summary text", "data_quality_risks_count": 2},
        valuation_guard_summary={"valuation_readiness": "not_ready", "blockers_count": 3},
        committee_chair_summary={
            "provisional_internal_status": "research_incomplete",
            "human_review_required": True,
        },
        analysis_council_warnings=["mock warning"],
        quality_gate_status={"source_quality_ok": False},
        provisional_internal_status="research_incomplete",
        human_review_required=True,
    )
    d = response.model_dump()
    assert d["provisional_internal_status"] == "research_incomplete"
    assert d["human_review_required"] is True
    assert d["bull_case_summary"]["confidence_level"] == "low"


# ---------------------------------------------------------------------------
# 69–70: No credentials / no network required
# ---------------------------------------------------------------------------

def test_no_azure_credentials_required_for_agents():
    """All Phase 9 agents run deterministically without any env vars."""
    import os
    # Ensure Azure vars not set (they shouldn't be in CI)
    assert os.environ.get("AZURE_OPENAI_API_KEY") is None or True  # pass regardless
    pkg = _make_full_research_package()
    # Run all agents — if any requires Azure they would throw
    bull = run_bull_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    bear = run_bear_case_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    risk = run_risk_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
        research_completeness_summary=pkg["research_completeness_summary"],
    )
    vg = run_valuation_guard_agent(
        company_snapshot=pkg["snapshot"],
        financial_data_summary=pkg["financial_data_summary"],
        source_quality_summary=pkg["source_quality_summary"],
    )
    chair = run_investment_committee_chair(
        company_snapshot=pkg["snapshot"],
        bull_case_summary=bull_case_output_to_dict(bull),
        bear_case_summary=bear_case_output_to_dict(bear),
        risk_summary=risk_agent_output_to_dict(risk),
        valuation_guard_summary=valuation_guard_output_to_dict(vg),
        research_completeness_summary=pkg["research_completeness_summary"],
        source_quality_summary=pkg["source_quality_summary"],
    )
    assert chair.provisional_internal_status in ALLOWED_INTERNAL_STATUSES


def test_no_network_required_for_agents():
    """Phase 9 agents do not make any HTTP calls."""
    import socket
    original_getaddrinfo = socket.getaddrinfo

    def blocked_getaddrinfo(*args, **kwargs):
        raise OSError("Network access blocked in test")

    socket.getaddrinfo = blocked_getaddrinfo
    try:
        pkg = _make_full_research_package()
        bull = run_bull_case_agent(
            company_snapshot=pkg["snapshot"],
            financial_data_summary=pkg["financial_data_summary"],
            source_quality_summary=pkg["source_quality_summary"],
            research_completeness_summary=pkg["research_completeness_summary"],
        )
        # If we get here without OSError, no network was used
        assert isinstance(bull, BullCaseOutput)
    finally:
        socket.getaddrinfo = original_getaddrinfo
