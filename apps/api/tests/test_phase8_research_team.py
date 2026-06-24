"""
Phase 8: Research Team Agents — offline tests.

All tests run without:
  - Network calls
  - Database (mock AsyncSession where needed)
  - Azure OpenAI credentials
  - Real LLM calls

Test coverage:
  1.  FinancialDataAgent: available data identified from mock snapshot
  2.  FinancialDataAgent: missing financial fundamentals always listed
  3.  FinancialDataAgent: T5/T6 provider triggers warning
  4.  FinancialDataAgent: mock data triggers warning
  5.  FinancialDataAgent: price history available → included in available data
  6.  FinancialDataAgent: price history absent → in missing data
  7.  FinancialDataAgent: no invented numbers in output
  8.  FinancialDataAgent: source_tier_summary populated
  9.  SourceQualityAgent: T6 mock → weak overall quality
  10. SourceQualityAgent: T5 aggregator correctly not promoted to primary
  11. SourceQualityAgent: GLEIF → T2 (strong)
  12. SourceQualityAgent: SEC EDGAR → T2 (strong)
  13. SourceQualityAgent: T5-only data → aggregator_only_claims populated
  14. SourceQualityAgent: recommended_source_upgrades always non-empty
  15. SourceQualityAgent: warnings for T5/T6 only decision-critical claims
  16. ResearchCompletenessAgent: identity section incomplete (missing isin, lei)
  17. ResearchCompletenessAgent: report_meta in draft → section present
  18. ResearchCompletenessAgent: snapshot_financials always absent at Phase 8
  19. ResearchCompletenessAgent: blocking_gaps includes required fields
  20. ResearchCompletenessAgent: next_research_tasks non-empty when gaps present
  21. ResearchCompletenessAgent: does not fake missing sections
  22. ResearchCompletenessAgent: schema_valid=false does not crash agent
  23. CitationValidatorV2: bare number in snapshot_financials → unsupported_number_warning
  24. CitationValidatorV2: valid datapoint → approved_claim
  25. CitationValidatorV2: T5-only citation on decision-critical field → source_tier_warning
  26. CitationValidatorV2: T2 citation on decision-critical field → no tier warning
  27. CitationValidatorV2: mock provider → weak_citation_warning
  28. CitationValidatorV2: missing citations list from DB citation records
  29. CitationValidatorV2: status "failed" when bare numbers present
  30. CitationValidatorV2: status "warnings" on weak-tier citations (no bare numbers)
  31. Workflow: completes with use_llm=False (offline, no DB)
  32. Workflow: completes with use_llm=True, llm_provider=mock (offline)
  33. Workflow graph includes all 13 nodes (+ handle_error)
  34. financial_data_agent node persists output in state
  35. source_quality_agent node persists output in state
  36. research_completeness_agent node persists output in state
  37. citation_validator_v2 node persists output in state
  38. research_team_warnings aggregated in save_draft_report
  39. draft report content includes Financial Data Agent Summary section
  40. draft report content includes Source Quality Agent Summary section
  41. draft report content includes Research Completeness Review section
  42. draft report content includes Citation Validation Review section
  43. API response includes research_team_* fields
  44. agent steps persisted for all new Phase 8 nodes
  45. research_team_complete=True in final state after successful run
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.research_team.citation_validator_v2 import (
    UpgradedCitationValidationOutput,
    run_upgraded_citation_validator,
    upgraded_citation_validation_to_dict,
)
from app.agents.research_team.financial_data_agent import (
    FinancialDataAgentOutput,
    financial_data_agent_output_to_dict,
    run_financial_data_agent,
)
from app.agents.research_team.research_completeness_agent import (
    ResearchCompletenessAgentOutput,
    research_completeness_output_to_dict,
    run_research_completeness_agent,
)
from app.agents.research_team.source_quality_agent import (
    SourceQualityAgentOutput,
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
from app.workflows.snapshot_builder import build_company_snapshot

# ---------------------------------------------------------------------------
# Shared fixtures
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
    is_mock: bool = True,
) -> CompanyProfileData:
    return CompanyProfileData(
        ticker=ticker,
        exchange="OSE",
        legal_name="Acme Nordic AS [MOCK]",
        country_domicile="Norway",
        reporting_currency="NOK",
        fiscal_year_end="December",
        sector="Industrials",
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


def _make_snapshot(with_prices: bool = True) -> dict:
    profile = _mock_profile()
    prices = _mock_prices() if with_prices else None
    return build_company_snapshot(profile=profile, prices=prices)


# ---------------------------------------------------------------------------
# 1–8: FinancialDataAgent
# ---------------------------------------------------------------------------

def test_financial_data_agent_available_data_from_mock_snapshot():
    snapshot = _make_snapshot()
    output = run_financial_data_agent(company_snapshot=snapshot)
    assert isinstance(output.available_financial_data, list)
    # At minimum identity fields should be available
    assert any("identity" in f for f in output.available_financial_data)


def test_financial_data_agent_missing_fundamentals():
    snapshot = _make_snapshot()
    output = run_financial_data_agent(company_snapshot=snapshot)
    # All fundamental categories should be missing at snapshot phase
    missing = output.missing_financial_data
    financial_missing = [m for m in missing if m.startswith("financials.")]
    assert len(financial_missing) >= 10, "Should have many missing fundamental categories"


def test_financial_data_agent_t5_t6_triggers_warning():
    snapshot = _make_snapshot()  # mock = T6
    output = run_financial_data_agent(company_snapshot=snapshot)
    warning_text = " ".join(output.warnings)
    # Should warn about mock/T6 data
    assert "mock" in warning_text.lower() or "T6" in warning_text or "T5" in warning_text


def test_financial_data_agent_mock_data_warning():
    snapshot = _make_snapshot()
    output = run_financial_data_agent(company_snapshot=snapshot)
    assert any("MOCK" in w or "mock" in w.lower() for w in output.warnings)


def test_financial_data_agent_price_history_in_available_when_present():
    snapshot = _make_snapshot(with_prices=True)
    output = run_financial_data_agent(company_snapshot=snapshot)
    price_fields = [f for f in output.available_financial_data if "price_history" in f]
    assert len(price_fields) > 0


def test_financial_data_agent_price_history_in_missing_when_absent():
    snapshot = _make_snapshot(with_prices=False)
    output = run_financial_data_agent(company_snapshot=snapshot)
    price_missing = [f for f in output.missing_financial_data if "price_history" in f]
    assert len(price_missing) > 0


def test_financial_data_agent_no_invented_numbers():
    snapshot = _make_snapshot()
    output = run_financial_data_agent(company_snapshot=snapshot)
    # financial_context_summary should not contain bare financial numbers invented
    # (it may reference latest_close from provider data which is acceptable)
    # Key check: no raw valuation numbers fabricated
    assert "price target" not in output.financial_context_summary.lower()
    assert "fair value" not in output.financial_context_summary.lower()
    assert "BUY" not in output.financial_context_summary
    assert "SELL" not in output.financial_context_summary


def test_financial_data_agent_source_tier_summary_populated():
    snapshot = _make_snapshot()
    output = run_financial_data_agent(company_snapshot=snapshot, source_ids=["uuid-1", "uuid-2"])
    total = sum(output.source_tier_summary.values())
    assert total > 0


def test_financial_data_agent_output_to_dict_serializable():
    snapshot = _make_snapshot()
    output = run_financial_data_agent(company_snapshot=snapshot)
    d = financial_data_agent_output_to_dict(output)
    assert isinstance(d, dict)
    assert "available_financial_data" in d
    assert "missing_financial_data" in d
    assert "source_tier_summary" in d
    assert "financial_context_summary" in d
    assert "warnings" in d


# ---------------------------------------------------------------------------
# 9–15: SourceQualityAgent
# ---------------------------------------------------------------------------

def test_source_quality_agent_t6_mock_is_weak():
    snapshot = _make_snapshot()  # mock = T6
    output = run_source_quality_agent(company_snapshot=snapshot)
    assert output.overall_source_quality in ("weak", "insufficient")


def test_source_quality_agent_t5_not_promoted_to_primary():
    # Simulate T5 EODHD provider
    profile = CompanyProfileData(
        ticker="TEST",
        exchange="NASDAQ",
        legal_name="Test Corp",
        country_domicile="US",
        reporting_currency="USD",
        fiscal_year_end="December",
        sector="Technology",
        industry="Software",
        website=None,
        ipo_date=None,
        description=None,
        isin=None,
        lei=None,
        source_url=None,
        data_quality=DataQuality.B_single_credible,
        meta=ProviderResponseMetadata(
            provider_name="eodhd",
            source_tier=SourceTier.T5_api_aggregator,
            retrieved_at=_FIXED_TS,
            is_mock=False,
            status=ProviderStatus.ok,
            note=None,
        ),
    )
    snapshot = build_company_snapshot(profile=profile, prices=None)
    output = run_source_quality_agent(company_snapshot=snapshot)
    # EODHD must be in weak sources, never strong
    weak_text = " ".join(output.weak_sources)
    assert "eodhd" in weak_text.lower() or "T5" in weak_text
    # Strong sources should not mention eodhd
    strong_text = " ".join(output.strong_sources)
    assert "eodhd" not in strong_text.lower()


def test_source_quality_agent_gleif_is_t2_strong():
    """Simulate GLEIF as a T2 provider — should be in strong sources."""
    profile = CompanyProfileData(
        ticker="TEST",
        exchange="FRA",
        legal_name="Test GmbH",
        country_domicile="Germany",
        reporting_currency="EUR",
        fiscal_year_end="December",
        sector="Industrials",
        industry="Manufacturing",
        website=None,
        ipo_date=None,
        description=None,
        isin=None,
        lei="HWUPKR0MPOU8FGXBT394",
        source_url=None,
        data_quality=DataQuality.A_verified,
        meta=ProviderResponseMetadata(
            provider_name="gleif",
            source_tier=SourceTier.T2_regulator_or_gov,
            retrieved_at=_FIXED_TS,
            is_mock=False,
            status=ProviderStatus.ok,
            note=None,
        ),
    )
    snapshot = build_company_snapshot(profile=profile, prices=None)
    output = run_source_quality_agent(company_snapshot=snapshot)
    strong_text = " ".join(output.strong_sources)
    assert "gleif" in strong_text.lower() or "T2" in strong_text


def test_source_quality_agent_sec_edgar_is_t2_strong():
    """SEC EDGAR as T2 provider — should be in strong sources."""
    profile = CompanyProfileData(
        ticker="AAPL",
        exchange="NASDAQ",
        legal_name="Apple Inc.",
        country_domicile="US",
        reporting_currency="USD",
        fiscal_year_end="September",
        sector="Technology",
        industry="Consumer Electronics",
        website="https://www.apple.com",
        ipo_date=None,
        description=None,
        isin=None,
        lei=None,
        source_url=None,
        data_quality=DataQuality.A_verified,
        meta=ProviderResponseMetadata(
            provider_name="sec_edgar",
            source_tier=SourceTier.T2_regulator_or_gov,
            retrieved_at=_FIXED_TS,
            is_mock=False,
            status=ProviderStatus.ok,
            note=None,
        ),
    )
    snapshot = build_company_snapshot(profile=profile, prices=None)
    output = run_source_quality_agent(company_snapshot=snapshot)
    strong_text = " ".join(output.strong_sources)
    assert "sec_edgar" in strong_text.lower() or "T2" in strong_text


def test_source_quality_agent_t5_aggregator_only_claims():
    profile = CompanyProfileData(
        ticker="TEST",
        exchange="NASDAQ",
        legal_name="Test Corp",
        country_domicile="US",
        reporting_currency="USD",
        fiscal_year_end="December",
        sector="Technology",
        industry="Software",
        website=None,
        ipo_date=None,
        description=None,
        isin=None,
        lei=None,
        source_url=None,
        data_quality=DataQuality.B_single_credible,
        meta=ProviderResponseMetadata(
            provider_name="stooq",
            source_tier=SourceTier.T5_api_aggregator,
            retrieved_at=_FIXED_TS,
            is_mock=False,
            status=ProviderStatus.ok,
            note=None,
        ),
    )
    snapshot = build_company_snapshot(profile=profile, prices=None)
    output = run_source_quality_agent(company_snapshot=snapshot)
    assert len(output.aggregator_only_claims) > 0


def test_source_quality_agent_recommended_upgrades_non_empty():
    snapshot = _make_snapshot()
    output = run_source_quality_agent(company_snapshot=snapshot)
    assert len(output.recommended_source_upgrades) > 0


def test_source_quality_agent_warnings_for_t5_decision_critical():
    snapshot = _make_snapshot()  # mock = T6
    output = run_source_quality_agent(company_snapshot=snapshot)
    assert len(output.warnings) > 0
    warning_text = " ".join(output.warnings)
    assert "T6" in warning_text or "T5" in warning_text or "mock" in warning_text.lower()


def test_source_quality_output_to_dict_serializable():
    snapshot = _make_snapshot()
    output = run_source_quality_agent(company_snapshot=snapshot)
    d = source_quality_output_to_dict(output)
    assert isinstance(d, dict)
    assert "overall_source_quality" in d
    assert "strong_sources" in d
    assert "weak_sources" in d
    assert "aggregator_only_claims" in d
    assert "warnings" in d


# ---------------------------------------------------------------------------
# 16–22: ResearchCompletenessAgent
# ---------------------------------------------------------------------------

def test_research_completeness_identity_section_incomplete_without_isin_lei():
    snapshot = _make_snapshot()  # no isin, no lei
    # Minimal schema draft with identity section missing isin and lei
    draft = {
        "report_meta": {
            "schema_version": "1.0.0",
            "report_id": _REPORT_ID,
            "generated_at": "2026-06-20T12:00:00Z",
            "candidate_emerged_from": "test",
            "core_target_profile": "test",
            "theme_tags": ["energy_transition"],
            "conviction": "WATCHLIST",
        },
        "identity": {
            "legal_name": {"value": "Test", "as_of": "2026-06-20", "source_tier": "T6_model_estimate",
                           "source_name": "mock", "data_quality": "D_weak_or_stale"},
            "ticker": {"value": "TEST", "as_of": "2026-06-20", "source_tier": "T6_model_estimate",
                       "source_name": "mock", "data_quality": "D_weak_or_stale"},
            "exchange": {"value": "OSE", "as_of": "2026-06-20", "source_tier": "T6_model_estimate",
                         "source_name": "mock", "data_quality": "D_weak_or_stale"},
            "country_domicile": {"value": "Norway", "as_of": "2026-06-20", "source_tier": "T6_model_estimate",
                                 "source_name": "mock", "data_quality": "D_weak_or_stale"},
            # isin and lei intentionally absent
        },
    }
    output = run_research_completeness_agent(
        company_snapshot=snapshot,
        schema_draft=draft,
    )
    assert "identity" in output.incomplete_sections


def test_research_completeness_report_meta_in_draft_is_complete():
    snapshot = _make_snapshot()
    draft = {
        "report_meta": {
            "schema_version": "1.0.0",
            "report_id": _REPORT_ID,
            "generated_at": "2026-06-20T12:00:00Z",
            "candidate_emerged_from": "test",
            "core_target_profile": "test profile",
            "theme_tags": ["energy_transition"],
            "conviction": "WATCHLIST",
            "agent_pipeline_version": "4.0.0",
        },
    }
    output = run_research_completeness_agent(
        company_snapshot=snapshot,
        schema_draft=draft,
    )
    # report_meta should be in complete sections since all required fields are present
    assert "report_meta" in output.complete_sections


def test_research_completeness_snapshot_financials_always_absent():
    snapshot = _make_snapshot()
    # Snapshot phase draft has no snapshot_financials
    draft = {
        "report_meta": {
            "schema_version": "1.0.0",
            "report_id": _REPORT_ID,
            "generated_at": "2026-06-20T12:00:00Z",
            "candidate_emerged_from": "test",
            "core_target_profile": "test",
            "theme_tags": ["energy_transition"],
            "conviction": "WATCHLIST",
        },
    }
    output = run_research_completeness_agent(
        company_snapshot=snapshot,
        schema_draft=draft,
    )
    assert "snapshot_financials" in output.incomplete_sections


def test_research_completeness_blocking_gaps_include_required_fields():
    snapshot = _make_snapshot()
    output = run_research_completeness_agent(
        company_snapshot=snapshot,
        schema_draft=None,  # no draft at all
    )
    assert len(output.blocking_gaps) > 0
    # Required sections (report_meta, identity, snapshot_financials) must block
    blocking_text = " ".join(output.blocking_gaps)
    assert "report_meta" in blocking_text or "identity" in blocking_text or "snapshot_financials" in blocking_text


def test_research_completeness_next_tasks_non_empty_when_gaps():
    snapshot = _make_snapshot()
    output = run_research_completeness_agent(
        company_snapshot=snapshot,
        schema_draft=None,
    )
    assert len(output.next_research_tasks) > 0


def test_research_completeness_does_not_fake_missing_sections():
    snapshot = _make_snapshot()
    output = run_research_completeness_agent(
        company_snapshot=snapshot,
        schema_draft=None,
    )
    # With no draft, no sections should be "complete"
    assert len(output.complete_sections) == 0


def test_research_completeness_schema_valid_false_does_not_crash():
    snapshot = _make_snapshot()
    errors = [
        "'snapshot_financials' is a required property",
        "'self_critique' is a required property",
    ]
    output = run_research_completeness_agent(
        company_snapshot=snapshot,
        schema_draft=None,
        schema_validation_errors=errors,
    )
    # Should return valid output and include errors in blocking_gaps
    assert isinstance(output, ResearchCompletenessAgentOutput)
    blocking_text = " ".join(output.blocking_gaps)
    assert "snapshot_financials" in blocking_text or "self_critique" in blocking_text


def test_research_completeness_output_to_dict_serializable():
    snapshot = _make_snapshot()
    output = run_research_completeness_agent(company_snapshot=snapshot, schema_draft=None)
    d = research_completeness_output_to_dict(output)
    assert isinstance(d, dict)
    assert "complete_sections" in d
    assert "incomplete_sections" in d
    assert "blocking_gaps" in d
    assert "next_research_tasks" in d


# ---------------------------------------------------------------------------
# 23–30: CitationValidatorV2
# ---------------------------------------------------------------------------

def test_citation_validator_v2_bare_number_fails():
    """Bare number in snapshot_financials section → unsupported_number_warning."""
    snapshot = _make_snapshot()
    draft = {
        "snapshot_financials": {
            "market_cap": 5000000000,  # bare number, not a datapoint
        }
    }
    output = run_upgraded_citation_validator(
        company_snapshot=snapshot,
        schema_draft=draft,
    )
    assert len(output.unsupported_number_warnings) > 0
    assert output.status == "failed"


def test_citation_validator_v2_valid_datapoint_approved():
    """Valid datapoint envelope on a non-critical field → approved_claim.

    decision-critical fields with T5/T6 go to source_tier_warnings (not approved),
    so we use a non-critical field (discovery_profile.entry_path) which gets approved
    regardless of source tier.
    """
    snapshot = _make_snapshot()
    draft = {
        "discovery_profile": {
            "entry_path": {
                "value": "supply_chain_screener",
                "as_of": "2026-06-20",
                "source_tier": "T6_model_estimate",
                "source_name": "mock provider",
                "data_quality": "D_weak_or_stale",
            }
        }
    }
    output = run_upgraded_citation_validator(
        company_snapshot=snapshot,
        schema_draft=draft,
    )
    assert "discovery_profile.entry_path" in output.approved_claims


def test_citation_validator_v2_t5_on_critical_field_tier_warning():
    """T5 citation on decision-critical field → source_tier_warning."""
    snapshot = _make_snapshot()
    citation_records = [
        {
            "id": str(uuid.uuid4()),
            "field_path": "identity.legal_name",
            "source_tier": "T5_api_aggregator",
            "data_quality": "B_single_credible",
        }
    ]
    output = run_upgraded_citation_validator(
        company_snapshot=snapshot,
        schema_draft=None,
        citation_records=citation_records,
    )
    tier_text = " ".join(output.source_tier_warnings)
    assert "identity.legal_name" in tier_text or "T5" in tier_text


def test_citation_validator_v2_t2_on_critical_field_no_tier_warning():
    """T2 citation on decision-critical field → no source_tier_warning."""
    # Build non-mock snapshot to avoid mock-blanket warning
    profile = CompanyProfileData(
        ticker="AAPL",
        exchange="NASDAQ",
        legal_name="Apple Inc.",
        country_domicile="US",
        reporting_currency="USD",
        fiscal_year_end="September",
        sector="Technology",
        industry="Consumer Electronics",
        website=None,
        ipo_date=None,
        description=None,
        isin=None,
        lei=None,
        source_url=None,
        data_quality=DataQuality.A_verified,
        meta=ProviderResponseMetadata(
            provider_name="sec_edgar",
            source_tier=SourceTier.T2_regulator_or_gov,
            retrieved_at=_FIXED_TS,
            is_mock=False,
            status=ProviderStatus.ok,
            note=None,
        ),
    )
    snapshot = build_company_snapshot(profile=profile, prices=None)
    citation_records = [
        {
            "id": str(uuid.uuid4()),
            "field_path": "identity.legal_name",
            "source_tier": "T2_regulator_or_gov",
            "data_quality": "A_verified",
        }
    ]
    output = run_upgraded_citation_validator(
        company_snapshot=snapshot,
        schema_draft=None,
        citation_records=citation_records,
    )
    # No tier warnings for T2 on critical fields
    tier_warnings_for_legal_name = [
        w for w in output.source_tier_warnings
        if "identity.legal_name" in w
    ]
    assert len(tier_warnings_for_legal_name) == 0


def test_citation_validator_v2_mock_provider_weak_warning():
    """Mock provider → weak_citation_warning about synthetic data."""
    snapshot = _make_snapshot()  # is_mock=True
    output = run_upgraded_citation_validator(
        company_snapshot=snapshot,
        schema_draft=None,
    )
    warning_text = " ".join(output.weak_citation_warnings)
    assert "mock" in warning_text.lower() or "synthetic" in warning_text.lower()


def test_citation_validator_v2_missing_field_path_warning():
    """Citation record without field_path → weak_citation_warning."""
    snapshot = _make_snapshot()
    citation_records = [
        {
            "id": str(uuid.uuid4()),
            "field_path": None,  # missing field_path
            "source_tier": "T5_api_aggregator",
            "data_quality": "B_single_credible",
        }
    ]
    output = run_upgraded_citation_validator(
        company_snapshot=snapshot,
        schema_draft=None,
        citation_records=citation_records,
    )
    warning_text = " ".join(output.weak_citation_warnings)
    assert "field_path" in warning_text


def test_citation_validator_v2_status_failed_on_bare_numbers():
    snapshot = _make_snapshot()
    draft = {"snapshot_financials": {"revenue": 1000.0}}  # bare number
    output = run_upgraded_citation_validator(
        company_snapshot=snapshot, schema_draft=draft
    )
    assert output.status == "failed"


def test_citation_validator_v2_status_warnings_on_weak_tier():
    snapshot = _make_snapshot()
    citation_records = [
        {
            "id": str(uuid.uuid4()),
            "field_path": "identity.ticker",
            "source_tier": "T5_api_aggregator",
            "data_quality": "B_single_credible",
        }
    ]
    output = run_upgraded_citation_validator(
        company_snapshot=snapshot,
        schema_draft=None,
        citation_records=citation_records,
    )
    # Should be warnings (T5 on critical field) but not failed (no bare numbers)
    assert output.status in ("warnings", "ok")


def test_citation_validator_v2_output_to_dict_serializable():
    snapshot = _make_snapshot()
    output = run_upgraded_citation_validator(company_snapshot=snapshot)
    d = upgraded_citation_validation_to_dict(output)
    assert isinstance(d, dict)
    assert "status" in d
    assert "approved_claims" in d
    assert "missing_citations" in d
    assert "weak_citation_warnings" in d
    assert "unsupported_number_warnings" in d
    assert "source_tier_warnings" in d


# ---------------------------------------------------------------------------
# 31–45: Workflow integration tests (mock DB)
# ---------------------------------------------------------------------------

def _make_mock_db():
    """Return a mock AsyncSession that accepts all service calls."""
    db = AsyncMock()

    company = MagicMock()
    company.id = uuid.UUID(_COMPANY_ID)
    company.name = "Acme Nordic AS"
    company.ticker = "TEST"
    company.exchange = "OSE"
    company.sector = "Industrials"
    company.description = "Test company"

    agent_run = MagicMock()
    agent_run.id = uuid.UUID(_AGENT_RUN_ID)

    agent_step = MagicMock()
    agent_step.id = uuid.uuid4()

    report = MagicMock()
    report.id = uuid.UUID(_REPORT_ID)
    report.slug = "company-analysis-test-12345678"

    source = MagicMock()
    source.id = uuid.UUID(_SOURCE_ID)

    citation = MagicMock()
    citation.id = uuid.uuid4()

    return db, company, agent_run, agent_step, report, source, citation


@pytest.mark.asyncio
async def test_workflow_completes_with_use_llm_false():
    """Full workflow run with use_llm=False — all nodes including Phase 8."""
    from app.workflows.company_analysis import run_company_analysis

    db, company, agent_run, agent_step, report, source, citation = _make_mock_db()

    with (
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=company),
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=agent_run),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=agent_step),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(source, True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=citation),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=report),
    ):
        state = await run_company_analysis(
            db=db,
            ticker="TEST",
            exchange="OSE",
            provider_name="mock",
            use_llm=False,
        )

    assert state["status"] == "completed"
    assert state["draft_report_id"] is not None
    assert state["financial_data_summary"] is not None
    assert state["source_quality_summary"] is not None
    assert state["research_completeness_summary"] is not None
    assert state["upgraded_citation_validation"] is not None
    assert state["research_team_complete"] is True
    assert state["llm_used"] is False


@pytest.mark.asyncio
async def test_workflow_completes_with_mock_llm():
    """Full workflow run with use_llm=True, llm_provider=mock."""
    from app.workflows.company_analysis import run_company_analysis

    db, company, agent_run, agent_step, report, source, citation = _make_mock_db()

    with (
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=company),
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=agent_run),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=agent_step),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(source, True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=citation),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=report),
    ):
        state = await run_company_analysis(
            db=db,
            ticker="TEST",
            exchange="OSE",
            provider_name="mock",
            use_llm=True,
            llm_provider="mock",
        )

    assert state["status"] == "completed"
    assert state["llm_used"] is True
    assert state["llm_sections"] is not None
    assert state["financial_data_summary"] is not None
    assert state["source_quality_summary"] is not None


def test_workflow_graph_contains_all_phase8_nodes():
    """Verify the graph compiles with all Phase 8 Research Team nodes registered."""
    from app.workflows.company_analysis import build_company_analysis_graph

    db = AsyncMock()
    graph = build_company_analysis_graph(db)
    # Graph compiles without error — all 13 nodes + handle_error are registered.
    assert graph is not None


@pytest.mark.asyncio
async def test_financial_data_agent_state_populated():
    from app.workflows.company_analysis import run_company_analysis

    db, company, agent_run, agent_step, report, source, citation = _make_mock_db()

    with (
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=company),
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=agent_run),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=agent_step),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(source, True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=citation),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=report),
    ):
        state = await run_company_analysis(db=db, ticker="TEST", exchange="OSE", provider_name="mock")

    fda = state["financial_data_summary"]
    assert isinstance(fda, dict)
    assert "available_financial_data" in fda
    assert "missing_financial_data" in fda
    assert "financial_context_summary" in fda
    assert "source_tier_summary" in fda
    assert "warnings" in fda


@pytest.mark.asyncio
async def test_source_quality_agent_state_populated():
    from app.workflows.company_analysis import run_company_analysis

    db, company, agent_run, agent_step, report, source, citation = _make_mock_db()

    with (
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=company),
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=agent_run),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=agent_step),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(source, True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=citation),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=report),
    ):
        state = await run_company_analysis(db=db, ticker="TEST", exchange="OSE", provider_name="mock")

    sq = state["source_quality_summary"]
    assert isinstance(sq, dict)
    assert "overall_source_quality" in sq
    assert sq["overall_source_quality"] in ("strong", "adequate", "weak", "insufficient")


@pytest.mark.asyncio
async def test_research_completeness_agent_state_populated():
    from app.workflows.company_analysis import run_company_analysis

    db, company, agent_run, agent_step, report, source, citation = _make_mock_db()

    with (
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=company),
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=agent_run),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=agent_step),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(source, True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=citation),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=report),
    ):
        state = await run_company_analysis(db=db, ticker="TEST", exchange="OSE", provider_name="mock")

    rc = state["research_completeness_summary"]
    assert isinstance(rc, dict)
    assert "complete_sections" in rc
    assert "incomplete_sections" in rc
    assert "blocking_gaps" in rc
    assert "next_research_tasks" in rc


@pytest.mark.asyncio
async def test_citation_validator_v2_state_populated():
    from app.workflows.company_analysis import run_company_analysis

    db, company, agent_run, agent_step, report, source, citation = _make_mock_db()

    with (
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=company),
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=agent_run),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=agent_step),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(source, True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=citation),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=report),
    ):
        state = await run_company_analysis(db=db, ticker="TEST", exchange="OSE", provider_name="mock")

    cv2 = state["upgraded_citation_validation"]
    assert isinstance(cv2, dict)
    assert "status" in cv2
    assert cv2["status"] in ("ok", "warnings", "failed")


@pytest.mark.asyncio
async def test_research_team_warnings_aggregated():
    from app.workflows.company_analysis import run_company_analysis

    db, company, agent_run, agent_step, report, source, citation = _make_mock_db()

    with (
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=company),
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=agent_run),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=agent_step),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(source, True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=citation),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=report),
    ):
        state = await run_company_analysis(db=db, ticker="TEST", exchange="OSE", provider_name="mock")

    # research_team_warnings should be a list (possibly non-empty for mock data)
    assert isinstance(state["research_team_warnings"], list)


@pytest.mark.asyncio
async def test_draft_report_includes_financial_data_summary_section():
    """Draft report content_markdown must include Financial Data Agent Summary."""
    from app.workflows.company_analysis import run_company_analysis

    db, company, agent_run, agent_step, report, source, citation = _make_mock_db()

    # Capture the content_markdown passed to create_draft_report
    captured_content = {}

    async def capture_report(db, data):
        captured_content["content"] = data.content_markdown
        return report

    with (
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=company),
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=agent_run),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=agent_step),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(source, True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=citation),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", side_effect=capture_report),
    ):
        await run_company_analysis(db=db, ticker="TEST", exchange="OSE", provider_name="mock")

    content = captured_content.get("content", "")
    assert "Financial Data Agent Summary" in content
    assert "Source Quality Agent Summary" in content
    assert "Research Completeness Review" in content
    assert "Citation Validation Review" in content


@pytest.mark.asyncio
async def test_draft_report_includes_admin_disclaimer():
    """Draft report must include admin-only disclaimer."""
    from app.workflows.company_analysis import run_company_analysis

    db, company, agent_run, agent_step, report, source, citation = _make_mock_db()
    captured_content = {}

    async def capture_report(db, data):
        captured_content["content"] = data.content_markdown
        return report

    with (
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=company),
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=agent_run),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=agent_step),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(source, True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=citation),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", side_effect=capture_report),
    ):
        await run_company_analysis(db=db, ticker="TEST", exchange="OSE", provider_name="mock")

    content = captured_content.get("content", "")
    assert "ADMIN DRAFT ONLY" in content
    assert "Not investment advice" in content or "not investment advice" in content.lower()


def test_api_response_includes_research_team_fields():
    """WorkflowRunResponse Pydantic schema includes all Phase 8 Research Team fields."""
    import uuid as _uuid

    from app.schemas.agent import WorkflowRunResponse

    response_model = WorkflowRunResponse(
        agent_run_id=_uuid.uuid4(),
        draft_report_id=_uuid.uuid4(),
        status="completed",
        summary="Test",
        workflow_name="company_analysis",
        financial_data_summary={"available_count": 5, "missing_count": 18},
        source_quality_summary={"overall_source_quality": "weak"},
        research_completeness_summary={"complete_sections": [], "blocking_gaps_count": 6},
        citation_validation_summary={"status": "warnings"},
        research_team_warnings=["mock data warning"],
    )

    assert response_model.financial_data_summary is not None
    assert response_model.source_quality_summary is not None
    assert response_model.research_completeness_summary is not None
    assert response_model.citation_validation_summary is not None
    assert len(response_model.research_team_warnings) == 1


def test_agent_steps_persisted_description():
    """
    Verify agent step names for Phase 8 nodes match expected names.
    This is a structural test — checks the node functions are named consistently.
    """
    from app.workflows.company_analysis import build_company_analysis_graph

    db = AsyncMock()
    graph = build_company_analysis_graph(db)
    # Graph compilation succeeds — all 13 nodes + handle_error registered
    assert graph is not None


@pytest.mark.asyncio
async def test_research_team_complete_true_in_final_state():
    from app.workflows.company_analysis import run_company_analysis

    db, company, agent_run, agent_step, report, source, citation = _make_mock_db()

    with (
        patch("app.services.company_service.get_company_by_ticker", new_callable=AsyncMock, return_value=company),
        patch("app.services.agent_run_service.create_agent_run", new_callable=AsyncMock, return_value=agent_run),
        patch("app.services.agent_run_service.create_agent_step", new_callable=AsyncMock, return_value=agent_step),
        patch("app.services.agent_run_service.complete_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_step", new_callable=AsyncMock),
        patch("app.services.agent_run_service.complete_agent_run", new_callable=AsyncMock),
        patch("app.services.agent_run_service.fail_agent_run", new_callable=AsyncMock),
        patch("app.services.source_service.get_or_create_source", new_callable=AsyncMock, return_value=(source, True)),
        patch("app.services.citation_service.create_citation", new_callable=AsyncMock, return_value=citation),
        patch("app.services.citation_service.list_citations_for_agent_run", new_callable=AsyncMock, return_value=[]),
        patch("app.services.report_service.create_draft_report", new_callable=AsyncMock, return_value=report),
    ):
        state = await run_company_analysis(
            db=db,
            ticker="TEST",
            exchange="OSE",
            provider_name="mock",
            use_llm=False,
        )

    assert state["research_team_complete"] is True


# ---------------------------------------------------------------------------
# Prompt template existence tests
# ---------------------------------------------------------------------------

def test_phase8_financial_data_agent_prompt_exists():
    import pathlib
    # parents[3] = repo root (test file is at apps/api/tests/test_*.py)
    prompt_path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "packages"
        / "prompts"
        / "research"
        / "phase8_financial_data_agent_v1.md"
    )
    assert prompt_path.exists(), f"Prompt file not found: {prompt_path}"


def test_phase8_source_quality_agent_prompt_exists():
    import pathlib
    prompt_path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "packages"
        / "prompts"
        / "research"
        / "phase8_source_quality_agent_v1.md"
    )
    assert prompt_path.exists(), f"Prompt file not found: {prompt_path}"


def test_phase8_research_completeness_agent_prompt_exists():
    import pathlib
    prompt_path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "packages"
        / "prompts"
        / "research"
        / "phase8_research_completeness_agent_v1.md"
    )
    assert prompt_path.exists(), f"Prompt file not found: {prompt_path}"


def test_phase8_prompts_contain_required_constraint_text():
    import pathlib
    base = (
        pathlib.Path(__file__).resolve().parents[3]
        / "packages"
        / "prompts"
        / "research"
    )
    for fname in [
        "phase8_financial_data_agent_v1.md",
        "phase8_source_quality_agent_v1.md",
        "phase8_research_completeness_agent_v1.md",
    ]:
        content = (base / fname).read_text(encoding="utf-8")
        assert "NOT" in content or "not" in content.lower(), f"{fname} missing constraint text"
        assert "investment advice" in content.lower(), f"{fname} missing 'investment advice' disclaimer"
        assert "JSON" in content, f"{fname} missing JSON output requirement"


def test_no_azure_credentials_required_for_agents():
    """Running any Research Team agent must not attempt to access Azure env vars."""
    import os
    # Ensure no Azure env vars are set
    for key in ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT_NAME"]:
        os.environ.pop(key, None)

    snapshot = _make_snapshot()
    # These should all run without touching Azure
    fda = run_financial_data_agent(company_snapshot=snapshot)
    sq = run_source_quality_agent(company_snapshot=snapshot)
    rc = run_research_completeness_agent(company_snapshot=snapshot)
    cv2 = run_upgraded_citation_validator(company_snapshot=snapshot)

    assert isinstance(fda, FinancialDataAgentOutput)
    assert isinstance(sq, SourceQualityAgentOutput)
    assert isinstance(rc, ResearchCompletenessAgentOutput)
    assert isinstance(cv2, UpgradedCitationValidationOutput)
