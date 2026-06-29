"""
Phase 7: Azure OpenAI + First LLM Research Agent — offline tests.

All tests run without:
  - Network calls (mock LLM client only)
  - Database (mock AsyncSession)
  - Azure OpenAI credentials
  - Real LLM calls

Test coverage:
  1.  MockResearchLLMClient returns deterministic output (no network)
  2.  MockResearchLLMClient is_mock=True and provider_name="mock"
  3.  AzureOpenAIResearchLLMClient raises ValueError without credentials
  4.  get_llm_client("mock") returns MockResearchLLMClient
  5.  get_llm_client("azure_openai") raises without env vars
  6.  get_llm_client(None) defaults to mock when LLM_PROVIDER=mock
  7.  ResearchSectionsOutput rejects fields not in schema (no rating/price_target)
  8.  validate_llm_sections passes clean output
  9.  validate_llm_sections flags BUY/SELL rating keywords
  10. validate_llm_sections flags price target keywords
  11. Workflow with use_llm=False skips generate_research_sections
  12. Workflow with use_llm=True and mock LLM produces llm_sections in state
  13. LLM node receives company_snapshot
  14. Generated sections saved into draft report content_markdown
  15. Schema validation still runs after LLM node
  16. llm_used=False when use_llm=False
  17. llm_used=True when use_llm=True with mock LLM
  18. API endpoint passes use_llm/llm_provider through to workflow
  19. API response includes llm_provider and llm_used fields
  20. Prompt template file exists and contains required constraint text
  21. LLM node failure is non-fatal — workflow completes without LLM sections
"""

import pathlib
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    DataQuality,
    PriceHistoryData,
    PricePoint,
    ProviderResponseMetadata,
    ProviderStatus,
    SourceTier,
)
from app.integrations.llm_provider import (
    AzureOpenAIResearchLLMClient,
    MockResearchLLMClient,
    ResearchSectionsOutput,
    get_llm_client,
    validate_llm_sections,
)
from app.workflows.snapshot_builder import build_company_snapshot

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_COMPANY_ID = str(uuid.UUID("11111111-1111-1111-1111-111111111111"))
_AGENT_RUN_ID = str(uuid.UUID("22222222-2222-2222-2222-222222222222"))
_REPORT_ID = str(uuid.UUID("33333333-3333-3333-3333-333333333333"))
_SOURCE_ID = str(uuid.UUID("44444444-4444-4444-4444-444444444444"))
_PRICE_SOURCE_ID = str(uuid.UUID("66666666-6666-6666-6666-666666666666"))
_CITATION_ID = str(uuid.UUID("55555555-5555-5555-5555-555555555555"))

_FIXED_RETRIEVED_AT = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)


def _mock_meta(is_mock: bool = True) -> ProviderResponseMetadata:
    return ProviderResponseMetadata(
        provider_name="mock",
        source_tier=SourceTier.T6_model_estimate,
        retrieved_at=_FIXED_RETRIEVED_AT,
        is_mock=is_mock,
        status=ProviderStatus.ok,
        note="DEMO DATA — MockFinancialDataProvider",
    )


def _mock_profile(ticker: str = "TEST") -> CompanyProfileData:
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
        meta=_mock_meta(),
    )


def _mock_prices(ticker: str = "TEST") -> PriceHistoryData:
    pts = [
        PricePoint(date="2026-01-02", open=10.0, high=10.5, low=9.8, close=10.2, volume=123000),
        PricePoint(date="2026-01-03", open=10.2, high=11.0, low=10.1, close=10.8, volume=145000),
    ]
    return PriceHistoryData(
        ticker=ticker,
        exchange="OSE",
        currency="NOK",
        price_points=pts,
        data_quality=DataQuality.D_weak_or_stale,
        source_url=None,
        meta=_mock_meta(),
    )


def _sample_snapshot(ticker: str = "TEST") -> dict:
    """Build a company snapshot dict using the real builder (no DB)."""
    return build_company_snapshot(profile=_mock_profile(ticker), prices=_mock_prices(ticker))


# ---------------------------------------------------------------------------
# 1–2: MockResearchLLMClient basics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_llm_client_is_mock_and_named():
    client = MockResearchLLMClient()
    assert client.is_mock is True
    assert client.provider_name == "mock"


@pytest.mark.asyncio
async def test_mock_llm_client_returns_output_without_network():
    """MockResearchLLMClient never makes network calls and returns deterministic output."""
    client = MockResearchLLMClient()
    snapshot = _sample_snapshot()
    result = await client.generate_research_sections(
        company_snapshot=snapshot,
        prompt_template="{{COMPANY_CONTEXT}}",
    )
    assert isinstance(result, ResearchSectionsOutput)
    assert result.thesis_summary_draft
    assert result.business_overview_draft
    assert isinstance(result.missing_information, list)
    assert result.self_critique_limitations


@pytest.mark.asyncio
async def test_mock_llm_output_contains_company_name():
    client = MockResearchLLMClient()
    snapshot = _sample_snapshot("ABCDE")
    result = await client.generate_research_sections(
        company_snapshot=snapshot,
        prompt_template="{{COMPANY_CONTEXT}}",
    )
    # Should reference the legal name or ticker from the snapshot
    combined = result.thesis_summary_draft + result.business_overview_draft
    assert "Acme Nordic" in combined or "ABCDE" in combined or "Industrials" in combined


@pytest.mark.asyncio
async def test_mock_llm_output_has_missing_information_list():
    client = MockResearchLLMClient()
    snapshot = _sample_snapshot()
    result = await client.generate_research_sections(
        company_snapshot=snapshot,
        prompt_template="ignored",
    )
    # Missing info should include at least some key financial fields
    missing_lower = [m.lower() for m in result.missing_information]
    assert any("revenue" in m or "ebitda" in m or "market_cap" in m for m in missing_lower)


# ---------------------------------------------------------------------------
# 3: AzureOpenAIResearchLLMClient raises without credentials
# ---------------------------------------------------------------------------


def test_azure_llm_client_raises_without_endpoint():
    """AzureOpenAIResearchLLMClient must not be instantiated without env vars."""
    with patch("app.integrations.llm_provider.settings") as mock_settings:
        mock_settings.azure_openai_endpoint = ""
        mock_settings.azure_openai_api_key = "key"
        mock_settings.azure_openai_deployment_name = "deploy"
        with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT"):
            AzureOpenAIResearchLLMClient()


def test_azure_llm_client_raises_without_api_key():
    with patch("app.integrations.llm_provider.settings") as mock_settings:
        mock_settings.azure_openai_endpoint = "https://example.openai.azure.com/"
        mock_settings.azure_openai_api_key = ""
        mock_settings.azure_openai_deployment_name = "deploy"
        with pytest.raises(ValueError, match="AZURE_OPENAI_API_KEY"):
            AzureOpenAIResearchLLMClient()


def test_azure_llm_client_raises_without_deployment():
    with patch("app.integrations.llm_provider.settings") as mock_settings:
        mock_settings.azure_openai_endpoint = "https://example.openai.azure.com/"
        mock_settings.azure_openai_api_key = "key"
        mock_settings.azure_openai_deployment_name = ""
        with pytest.raises(ValueError, match="AZURE_OPENAI_DEPLOYMENT_NAME"):
            AzureOpenAIResearchLLMClient()


# ---------------------------------------------------------------------------
# 4–6: get_llm_client factory
# ---------------------------------------------------------------------------


def test_get_llm_client_mock_returns_mock():
    client = get_llm_client("mock")
    assert isinstance(client, MockResearchLLMClient)
    assert client.is_mock is True


def test_get_llm_client_none_defaults_to_mock():
    """When provider is None, factory reads config. Default config is 'mock'."""
    with patch("app.integrations.llm_provider.settings") as mock_settings:
        mock_settings.llm_provider = "mock"
        client = get_llm_client(None)
    assert isinstance(client, MockResearchLLMClient)


def test_get_llm_client_azure_raises_without_credentials():
    """Requesting azure_openai without env vars raises — never instantiates silently."""
    with patch("app.integrations.llm_provider.settings") as mock_settings:
        mock_settings.azure_openai_endpoint = ""
        mock_settings.azure_openai_api_key = ""
        mock_settings.azure_openai_deployment_name = ""
        with pytest.raises(ValueError):
            get_llm_client("azure_openai")


# ---------------------------------------------------------------------------
# 7: ResearchSectionsOutput schema — no rating/price_target fields
# ---------------------------------------------------------------------------


def test_research_sections_output_has_no_rating_field():
    """ResearchSectionsOutput schema must not contain a rating, price_target, or conviction."""
    schema_fields = set(ResearchSectionsOutput.model_fields.keys())
    forbidden = {"rating", "price_target", "conviction", "buy_sell", "valuation", "recommendation"}
    overlap = schema_fields & forbidden
    assert not overlap, f"Forbidden fields found in ResearchSectionsOutput: {overlap}"


def test_research_sections_output_is_valid():
    output = ResearchSectionsOutput(
        thesis_summary_draft="Company X operates in the energy sector in Norway.",
        business_overview_draft="Company X manufactures grid components for European utilities.",
        missing_information=["revenue", "ebitda", "market_cap"],
        self_critique_limitations="Draft based on identity data only. Not investment advice.",
    )
    assert output.thesis_summary_draft
    assert len(output.missing_information) == 3


# ---------------------------------------------------------------------------
# 8–10: validate_llm_sections safety gate
# ---------------------------------------------------------------------------


def test_validate_llm_sections_passes_clean_output():
    output = ResearchSectionsOutput(
        thesis_summary_draft="Company X is a grid equipment supplier based in Germany.",
        business_overview_draft="Manufactures high-voltage transformers for transmission grids.",
        missing_information=["revenue"],
        self_critique_limitations="Identity data only. No financial metrics available.",
    )
    result = validate_llm_sections(output)
    assert result.passed is True
    assert result.warnings == []


def test_validate_llm_sections_flags_buy_rating():
    output = ResearchSectionsOutput(
        thesis_summary_draft="Company X is a strong BUY based on grid expansion.",
        business_overview_draft="Transformer manufacturer.",
        missing_information=[],
        self_critique_limitations="No limitations.",
    )
    result = validate_llm_sections(output)
    assert result.passed is False
    assert any("rating" in w.lower() or "BUY" in w for w in result.warnings)


def test_validate_llm_sections_flags_sell_rating():
    output = ResearchSectionsOutput(
        thesis_summary_draft="Recommend SELL given declining margins.",
        business_overview_draft="Industrial company.",
        missing_information=[],
        self_critique_limitations="No limitations.",
    )
    result = validate_llm_sections(output)
    assert result.passed is False


def test_validate_llm_sections_flags_price_target():
    output = ResearchSectionsOutput(
        thesis_summary_draft="Our price target is EUR 25 per share.",
        business_overview_draft="Industrial company.",
        missing_information=[],
        self_critique_limitations="No limitations.",
    )
    result = validate_llm_sections(output)
    assert result.passed is False
    assert any("price" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# 11–17: Workflow integration (using mock DB and mock services)
# ---------------------------------------------------------------------------

def _make_workflow_mocks():
    """Return all the mocks needed to run a workflow test without DB."""
    company = MagicMock()
    company.id = uuid.UUID(_COMPANY_ID)
    company.name = "Acme Nordic AS"
    company.ticker = "TEST"
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

    price_source = MagicMock()
    price_source.id = uuid.UUID(_PRICE_SOURCE_ID)

    citation = MagicMock()
    citation.id = uuid.UUID(_CITATION_ID)

    return {
        "company": company,
        "agent_run": agent_run,
        "agent_step": agent_step,
        "report": report,
        "source": source,
        "price_source": price_source,
        "citation": citation,
    }


@pytest.mark.asyncio
async def test_workflow_skips_llm_when_use_llm_false():
    """When use_llm=False, generate_research_sections runs but marks llm_used=False."""
    mocks = _make_workflow_mocks()
    db = AsyncMock(spec=__import__("sqlalchemy.ext.asyncio", fromlist=["AsyncSession"]).AsyncSession)

    with (
        patch("app.workflows.company_analysis.agent_run_service") as mock_run_svc,
        patch("app.workflows.company_analysis.company_service") as mock_co_svc,
        patch("app.workflows.company_analysis.source_service") as mock_src_svc,
        patch("app.workflows.company_analysis.citation_service") as mock_cit_svc,
        patch("app.workflows.company_analysis.report_service") as mock_rpt_svc,
        patch("app.workflows.company_analysis.FinancialDataService") as MockFDS,
    ):
        # service return values
        mock_run_svc.create_agent_run = AsyncMock(return_value=mocks["agent_run"])
        mock_run_svc.create_agent_step = AsyncMock(return_value=mocks["agent_step"])
        mock_run_svc.complete_agent_step = AsyncMock()
        mock_run_svc.complete_agent_run = AsyncMock()
        mock_run_svc.fail_agent_step = AsyncMock()
        mock_run_svc.fail_agent_run = AsyncMock()
        mock_co_svc.get_company = AsyncMock(return_value=mocks["company"])
        mock_co_svc.get_company_by_ticker = AsyncMock(return_value=mocks["company"])
        mock_src_svc.get_or_create_source = AsyncMock(return_value=(mocks["source"], True))
        mock_cit_svc.create_citation = AsyncMock(return_value=mocks["citation"])
        mock_rpt_svc.create_draft_report = AsyncMock(return_value=mocks["report"])

        # mock financial data service
        mock_fds_instance = AsyncMock()
        mock_fds_instance.get_company_profile = AsyncMock(return_value=_mock_profile())
        mock_fds_instance.get_price_history = AsyncMock(return_value=_mock_prices())
        mock_fds_instance.get_capabilities = MagicMock(return_value=["company_profile", "price_history"])
        MockFDS.return_value = mock_fds_instance

        from app.workflows.company_analysis import run_company_analysis

        final_state = await run_company_analysis(
            db=db,
            company_id=_COMPANY_ID,
            use_llm=False,
        )

    assert final_state["status"] == "completed"
    assert final_state.get("llm_used") is False


@pytest.mark.asyncio
async def test_workflow_runs_mock_llm_when_use_llm_true():
    """When use_llm=True with mock LLM, llm_used=True and llm_sections is populated."""
    mocks = _make_workflow_mocks()
    db = AsyncMock(spec=__import__("sqlalchemy.ext.asyncio", fromlist=["AsyncSession"]).AsyncSession)

    with (
        patch("app.workflows.company_analysis.agent_run_service") as mock_run_svc,
        patch("app.workflows.company_analysis.company_service") as mock_co_svc,
        patch("app.workflows.company_analysis.source_service") as mock_src_svc,
        patch("app.workflows.company_analysis.citation_service") as mock_cit_svc,
        patch("app.workflows.company_analysis.report_service") as mock_rpt_svc,
        patch("app.workflows.company_analysis.FinancialDataService") as MockFDS,
    ):
        mock_run_svc.create_agent_run = AsyncMock(return_value=mocks["agent_run"])
        mock_run_svc.create_agent_step = AsyncMock(return_value=mocks["agent_step"])
        mock_run_svc.complete_agent_step = AsyncMock()
        mock_run_svc.complete_agent_run = AsyncMock()
        mock_run_svc.fail_agent_step = AsyncMock()
        mock_run_svc.fail_agent_run = AsyncMock()
        mock_co_svc.get_company = AsyncMock(return_value=mocks["company"])
        mock_co_svc.get_company_by_ticker = AsyncMock(return_value=mocks["company"])
        mock_src_svc.get_or_create_source = AsyncMock(return_value=(mocks["source"], True))
        mock_cit_svc.create_citation = AsyncMock(return_value=mocks["citation"])
        mock_rpt_svc.create_draft_report = AsyncMock(return_value=mocks["report"])

        mock_fds_instance = AsyncMock()
        mock_fds_instance.get_company_profile = AsyncMock(return_value=_mock_profile())
        mock_fds_instance.get_price_history = AsyncMock(return_value=_mock_prices())
        mock_fds_instance.get_capabilities = MagicMock(return_value=["company_profile", "price_history"])
        MockFDS.return_value = mock_fds_instance

        from app.workflows.company_analysis import run_company_analysis

        final_state = await run_company_analysis(
            db=db,
            company_id=_COMPANY_ID,
            use_llm=True,
            llm_provider="mock",
        )

    assert final_state["status"] == "completed"
    assert final_state.get("llm_used") is True
    assert final_state.get("llm_provider") == "mock"
    llm_sections = final_state.get("llm_sections")
    assert llm_sections is not None
    assert "thesis_summary_draft" in llm_sections
    assert "business_overview_draft" in llm_sections
    assert "missing_information" in llm_sections
    assert "self_critique_limitations" in llm_sections


@pytest.mark.asyncio
async def test_llm_node_receives_company_snapshot():
    """The LLM node receives the company_snapshot from build_company_snapshot."""
    mocks = _make_workflow_mocks()
    db = AsyncMock(spec=__import__("sqlalchemy.ext.asyncio", fromlist=["AsyncSession"]).AsyncSession)

    captured_snapshot = {}

    async def fake_generate(company_snapshot, prompt_template):
        captured_snapshot.update(company_snapshot)
        return ResearchSectionsOutput(
            thesis_summary_draft="test thesis",
            business_overview_draft="test overview",
            missing_information=["revenue"],
            self_critique_limitations="test critique",
        )

    with (
        patch("app.workflows.company_analysis.agent_run_service") as mock_run_svc,
        patch("app.workflows.company_analysis.company_service") as mock_co_svc,
        patch("app.workflows.company_analysis.source_service") as mock_src_svc,
        patch("app.workflows.company_analysis.citation_service") as mock_cit_svc,
        patch("app.workflows.company_analysis.report_service") as mock_rpt_svc,
        patch("app.workflows.company_analysis.FinancialDataService") as MockFDS,
        patch("app.workflows.company_analysis.get_llm_client") as mock_get_client,
    ):
        mock_run_svc.create_agent_run = AsyncMock(return_value=mocks["agent_run"])
        mock_run_svc.create_agent_step = AsyncMock(return_value=mocks["agent_step"])
        mock_run_svc.complete_agent_step = AsyncMock()
        mock_run_svc.complete_agent_run = AsyncMock()
        mock_run_svc.fail_agent_step = AsyncMock()
        mock_run_svc.fail_agent_run = AsyncMock()
        mock_co_svc.get_company = AsyncMock(return_value=mocks["company"])
        mock_co_svc.get_company_by_ticker = AsyncMock(return_value=mocks["company"])
        mock_src_svc.get_or_create_source = AsyncMock(return_value=(mocks["source"], True))
        mock_cit_svc.create_citation = AsyncMock(return_value=mocks["citation"])
        mock_rpt_svc.create_draft_report = AsyncMock(return_value=mocks["report"])

        mock_fds_instance = AsyncMock()
        mock_fds_instance.get_company_profile = AsyncMock(return_value=_mock_profile())
        mock_fds_instance.get_price_history = AsyncMock(return_value=_mock_prices())
        mock_fds_instance.get_capabilities = MagicMock(return_value=["company_profile", "price_history"])
        MockFDS.return_value = mock_fds_instance

        mock_client = AsyncMock()
        mock_client.is_mock = True
        mock_client.provider_name = "mock"
        mock_client.generate_research_sections = fake_generate
        mock_get_client.return_value = mock_client

        from app.workflows.company_analysis import run_company_analysis

        await run_company_analysis(
            db=db,
            company_id=_COMPANY_ID,
            use_llm=True,
            llm_provider="mock",
        )

    # Verify the snapshot was passed to the LLM
    assert "company_identity" in captured_snapshot
    assert "profile" in captured_snapshot
    assert "missing_fields" in captured_snapshot


@pytest.mark.asyncio
async def test_llm_sections_appear_in_draft_report_content():
    """LLM-generated sections are included in the draft report content_markdown."""
    mocks = _make_workflow_mocks()
    db = AsyncMock(spec=__import__("sqlalchemy.ext.asyncio", fromlist=["AsyncSession"]).AsyncSession)

    captured_report_create = {}

    async def fake_create_report(db, payload):
        captured_report_create["content_markdown"] = payload.content_markdown
        captured_report_create["summary"] = payload.summary
        return mocks["report"]

    with (
        patch("app.workflows.company_analysis.agent_run_service") as mock_run_svc,
        patch("app.workflows.company_analysis.company_service") as mock_co_svc,
        patch("app.workflows.company_analysis.source_service") as mock_src_svc,
        patch("app.workflows.company_analysis.citation_service") as mock_cit_svc,
        patch("app.workflows.company_analysis.report_service") as mock_rpt_svc,
        patch("app.workflows.company_analysis.FinancialDataService") as MockFDS,
    ):
        mock_run_svc.create_agent_run = AsyncMock(return_value=mocks["agent_run"])
        mock_run_svc.create_agent_step = AsyncMock(return_value=mocks["agent_step"])
        mock_run_svc.complete_agent_step = AsyncMock()
        mock_run_svc.complete_agent_run = AsyncMock()
        mock_run_svc.fail_agent_step = AsyncMock()
        mock_run_svc.fail_agent_run = AsyncMock()
        mock_co_svc.get_company = AsyncMock(return_value=mocks["company"])
        mock_co_svc.get_company_by_ticker = AsyncMock(return_value=mocks["company"])
        mock_src_svc.get_or_create_source = AsyncMock(return_value=(mocks["source"], True))
        mock_cit_svc.create_citation = AsyncMock(return_value=mocks["citation"])
        mock_rpt_svc.create_draft_report = fake_create_report

        mock_fds_instance = AsyncMock()
        mock_fds_instance.get_company_profile = AsyncMock(return_value=_mock_profile())
        mock_fds_instance.get_price_history = AsyncMock(return_value=_mock_prices())
        mock_fds_instance.get_capabilities = MagicMock(return_value=["company_profile", "price_history"])
        MockFDS.return_value = mock_fds_instance

        from app.workflows.company_analysis import run_company_analysis

        await run_company_analysis(
            db=db,
            company_id=_COMPANY_ID,
            use_llm=True,
            llm_provider="mock",
        )

    md = captured_report_create.get("content_markdown", "")
    assert "Thesis Summary" in md or "thesis" in md.lower()
    assert "Business Overview" in md or "business" in md.lower()
    assert "ADMIN DRAFT ONLY" in md or "Not investment advice" in md
    assert "LLM: mock" in md


@pytest.mark.asyncio
async def test_schema_validation_runs_after_llm_node():
    """Schema validation is always run, regardless of LLM usage."""
    mocks = _make_workflow_mocks()
    db = AsyncMock(spec=__import__("sqlalchemy.ext.asyncio", fromlist=["AsyncSession"]).AsyncSession)

    with (
        patch("app.workflows.company_analysis.agent_run_service") as mock_run_svc,
        patch("app.workflows.company_analysis.company_service") as mock_co_svc,
        patch("app.workflows.company_analysis.source_service") as mock_src_svc,
        patch("app.workflows.company_analysis.citation_service") as mock_cit_svc,
        patch("app.workflows.company_analysis.report_service") as mock_rpt_svc,
        patch("app.workflows.company_analysis.FinancialDataService") as MockFDS,
    ):
        mock_run_svc.create_agent_run = AsyncMock(return_value=mocks["agent_run"])
        mock_run_svc.create_agent_step = AsyncMock(return_value=mocks["agent_step"])
        mock_run_svc.complete_agent_step = AsyncMock()
        mock_run_svc.complete_agent_run = AsyncMock()
        mock_run_svc.fail_agent_step = AsyncMock()
        mock_run_svc.fail_agent_run = AsyncMock()
        mock_co_svc.get_company = AsyncMock(return_value=mocks["company"])
        mock_co_svc.get_company_by_ticker = AsyncMock(return_value=mocks["company"])
        mock_src_svc.get_or_create_source = AsyncMock(return_value=(mocks["source"], True))
        mock_cit_svc.create_citation = AsyncMock(return_value=mocks["citation"])
        mock_rpt_svc.create_draft_report = AsyncMock(return_value=mocks["report"])

        mock_fds_instance = AsyncMock()
        mock_fds_instance.get_company_profile = AsyncMock(return_value=_mock_profile())
        mock_fds_instance.get_price_history = AsyncMock(return_value=_mock_prices())
        mock_fds_instance.get_capabilities = MagicMock(return_value=["company_profile", "price_history"])
        MockFDS.return_value = mock_fds_instance

        from app.workflows.company_analysis import run_company_analysis

        final_state = await run_company_analysis(
            db=db,
            company_id=_COMPANY_ID,
            use_llm=True,
            llm_provider="mock",
        )

    # Schema validation result should be present even with LLM enabled
    assert "schema_validation_result" in final_state
    assert final_state["schema_validation_result"] is not None
    # Draft will still be schema-invalid (many required fields absent even with LLM sections)
    schema_result = final_state["schema_validation_result"]
    assert "is_valid" in schema_result


@pytest.mark.asyncio
async def test_llm_node_failure_is_nonfatal():
    """If LLM node throws, workflow continues without LLM sections."""
    mocks = _make_workflow_mocks()
    db = AsyncMock(spec=__import__("sqlalchemy.ext.asyncio", fromlist=["AsyncSession"]).AsyncSession)

    async def exploding_generate(company_snapshot, prompt_template):
        raise RuntimeError("Simulated LLM failure")

    with (
        patch("app.workflows.company_analysis.agent_run_service") as mock_run_svc,
        patch("app.workflows.company_analysis.company_service") as mock_co_svc,
        patch("app.workflows.company_analysis.source_service") as mock_src_svc,
        patch("app.workflows.company_analysis.citation_service") as mock_cit_svc,
        patch("app.workflows.company_analysis.report_service") as mock_rpt_svc,
        patch("app.workflows.company_analysis.FinancialDataService") as MockFDS,
        patch("app.workflows.company_analysis.get_llm_client") as mock_get_client,
    ):
        mock_run_svc.create_agent_run = AsyncMock(return_value=mocks["agent_run"])
        mock_run_svc.create_agent_step = AsyncMock(return_value=mocks["agent_step"])
        mock_run_svc.complete_agent_step = AsyncMock()
        mock_run_svc.complete_agent_run = AsyncMock()
        mock_run_svc.fail_agent_step = AsyncMock()
        mock_run_svc.fail_agent_run = AsyncMock()
        mock_co_svc.get_company = AsyncMock(return_value=mocks["company"])
        mock_co_svc.get_company_by_ticker = AsyncMock(return_value=mocks["company"])
        mock_src_svc.get_or_create_source = AsyncMock(return_value=(mocks["source"], True))
        mock_cit_svc.create_citation = AsyncMock(return_value=mocks["citation"])
        mock_rpt_svc.create_draft_report = AsyncMock(return_value=mocks["report"])

        mock_fds_instance = AsyncMock()
        mock_fds_instance.get_company_profile = AsyncMock(return_value=_mock_profile())
        mock_fds_instance.get_price_history = AsyncMock(return_value=_mock_prices())
        mock_fds_instance.get_capabilities = MagicMock(return_value=["company_profile", "price_history"])
        MockFDS.return_value = mock_fds_instance

        mock_client = AsyncMock()
        mock_client.is_mock = True
        mock_client.provider_name = "mock"
        mock_client.generate_research_sections = exploding_generate
        mock_get_client.return_value = mock_client

        from app.workflows.company_analysis import run_company_analysis

        final_state = await run_company_analysis(
            db=db,
            company_id=_COMPANY_ID,
            use_llm=True,
            llm_provider="mock",
        )

    # Workflow should still complete despite LLM failure
    assert final_state["status"] == "completed"
    assert final_state.get("llm_used") is False
    assert final_state.get("llm_sections") is None


# ---------------------------------------------------------------------------
# 18–19: API endpoint integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_endpoint_passes_use_llm_to_workflow(client):
    """use_llm and llm_provider are forwarded from the API payload to the workflow."""
    with patch("app.api.v1.workflows.run_company_analysis") as mock_run:
        mock_run.return_value = {
            "status": "completed",
            "agent_run_id": _AGENT_RUN_ID,
            "draft_report_id": _REPORT_ID,
            "company_name": "Test Co",
            "ticker": "TEST",
            "provider_name": "mock",
            "is_mock": True,
            "schema_valid": False,
            "schema_validation_result": {"is_valid": False, "errors": [], "warnings": []},
            "company_snapshot": {"missing_fields": []},
            "use_llm": True,
            "llm_provider": "mock",
            "llm_used": True,
            "llm_sections": {"thesis_summary_draft": "test"},
            "llm_section_warnings": [],
            "error": None,
        }

        response = await client.post(
            "/api/v1/workflows/company-analysis/run",
            json={
                "ticker": "TEST",
                "exchange": "OSE",
                "use_llm": True,
                "llm_provider": "mock",
            },
        )

    assert response.status_code == 202
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs.get("use_llm") is True
    assert call_kwargs.get("llm_provider") == "mock"


@pytest.mark.asyncio
async def test_api_response_includes_llm_fields(client):
    """WorkflowRunResponse includes llm_provider and llm_used fields."""
    with patch("app.api.v1.workflows.run_company_analysis") as mock_run:
        mock_run.return_value = {
            "status": "completed",
            "agent_run_id": _AGENT_RUN_ID,
            "draft_report_id": _REPORT_ID,
            "company_name": "Test Co",
            "ticker": "TEST",
            "provider_name": "mock",
            "is_mock": True,
            "schema_valid": False,
            "schema_validation_result": {"is_valid": False, "errors": [], "warnings": []},
            "company_snapshot": {"missing_fields": []},
            "use_llm": True,
            "llm_provider": "mock",
            "llm_used": True,
            "llm_sections": {"thesis_summary_draft": "test"},
            "llm_section_warnings": [],
            "error": None,
        }

        response = await client.post(
            "/api/v1/workflows/company-analysis/run",
            json={"ticker": "TEST", "exchange": "OSE", "use_llm": True, "llm_provider": "mock"},
        )

    assert response.status_code == 202
    body = response.json()
    assert "llm_provider" in body
    assert "llm_used" in body
    assert body["llm_provider"] == "mock"
    assert body["llm_used"] is True


@pytest.mark.asyncio
async def test_api_default_use_llm_is_false(client):
    """Default API request has use_llm=False — safe offline mode."""
    with patch("app.api.v1.workflows.run_company_analysis") as mock_run:
        mock_run.return_value = {
            "status": "completed",
            "agent_run_id": _AGENT_RUN_ID,
            "draft_report_id": _REPORT_ID,
            "company_name": "Test Co",
            "ticker": "TEST",
            "provider_name": "mock",
            "is_mock": True,
            "schema_valid": False,
            "schema_validation_result": {"is_valid": False, "errors": [], "warnings": []},
            "company_snapshot": {"missing_fields": []},
            "use_llm": False,
            "llm_provider": None,
            "llm_used": False,
            "llm_sections": None,
            "llm_section_warnings": None,
            "error": None,
        }

        response = await client.post(
            "/api/v1/workflows/company-analysis/run",
            json={"ticker": "TEST", "exchange": "OSE"},  # no use_llm field
        )

    assert response.status_code == 202
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs.get("use_llm") is False


# ---------------------------------------------------------------------------
# 20: Prompt template validation
# ---------------------------------------------------------------------------


def test_prompt_template_file_exists():
    prompt_path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "packages"
        / "prompts"
        / "research"
        / "phase7_company_research_v1.md"
    )
    assert prompt_path.exists(), f"Prompt template not found at {prompt_path}"


def test_prompt_template_contains_required_constraints():
    prompt_path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "packages"
        / "prompts"
        / "research"
        / "phase7_company_research_v1.md"
    )
    content = prompt_path.read_text(encoding="utf-8")

    # Must forbid rating output
    assert "BUY" in content or "Do NOT output a rating" in content
    # Must forbid price target
    assert "price target" in content.lower() or "Do NOT output a price target" in content
    # Must forbid invented numbers
    assert "invent" in content.lower() or "invented" in content.lower()
    # Must instruct JSON output
    assert "JSON" in content
    # Must contain the context placeholder
    assert "{{COMPANY_CONTEXT}}" in content


# ---------------------------------------------------------------------------
# 21: Azure client not required for CI
# ---------------------------------------------------------------------------


def test_ci_uses_mock_llm_by_default():
    """Settings.llm_provider field default is 'mock' — CI has no .env so this default applies."""
    from app.core.config import Settings
    field_info = Settings.model_fields.get("llm_provider")
    assert field_info is not None
    assert field_info.default == "mock", (
        f"Settings.llm_provider default must be 'mock' for CI safety. Got: {field_info.default}"
    )
