"""
Phase 7 — Real Azure OpenAI Integration Test.

Calls the real AzureOpenAIResearchLLMClient against the deployed gpt-4.1-mini
model. Requires a valid .env with:
    LLM_PROVIDER=azure_openai
    AZURE_OPENAI_ENDPOINT=https://ib-stg-openai-d52d2.openai.azure.com/
    AZURE_OPENAI_API_KEY=<real key>
    AZURE_OPENAI_API_VERSION=2025-01-01-preview
    AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4.1-mini

All tests are guarded by:
    @pytest.mark.skipif(settings.llm_provider != "azure_openai", reason="real Azure OpenAI only")

So this file is silent/skipped in CI (CI has LLM_PROVIDER=mock, no credentials).

Safety assertions enforced:
  - No BUY/SELL/WATCH/HOLD/REJECT rating in any output field
  - No price target or fair value in any output field
  - No invented financial numbers presented as facts
  - Output is draft only — self_critique_limitations present
  - Schema validates against ResearchSectionsOutput
  - validate_llm_sections() passes on clean output
"""

from datetime import datetime, timezone

import pytest

from app.core.config import settings
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
    ResearchSectionsOutput,
    validate_llm_sections,
)
from app.workflows.snapshot_builder import build_company_snapshot

_SKIP = pytest.mark.skipif(
    settings.llm_provider != "azure_openai",
    reason="Skipped in CI — requires LLM_PROVIDER=azure_openai and real Azure credentials",
)

_FIXED_AT = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)


def _sample_snapshot() -> dict:
    meta = ProviderResponseMetadata(
        provider_name="mock",
        source_tier=SourceTier.T6_model_estimate,
        retrieved_at=_FIXED_AT,
        is_mock=True,
        status=ProviderStatus.ok,
        note="DEMO DATA — MockFinancialDataProvider",
    )
    profile = CompanyProfileData(
        ticker="NESTE",
        exchange="HSE",
        legal_name="Neste Oyj",
        country_domicile="Finland",
        reporting_currency="EUR",
        fiscal_year_end="December",
        sector="Energy",
        industry="Oil & Gas Refining & Marketing",
        website="https://www.neste.com",
        ipo_date=None,
        description=(
            "Neste Oyj is a Finnish oil refining and marketing company. "
            "It is one of the world's largest producers of renewable diesel and "
            "sustainable aviation fuel (SAF) refined from waste and residues."
        ),
        isin="FI0009013403",
        lei=None,
        source_url=None,
        data_quality=DataQuality.D_weak_or_stale,
        meta=meta,
    )
    prices = PriceHistoryData(
        ticker="NESTE",
        exchange="HSE",
        currency="EUR",
        price_points=[
            PricePoint(date="2026-06-20", open=11.5, high=12.1, low=11.4, close=11.8, volume=4500000),
            PricePoint(date="2026-06-23", open=11.8, high=12.0, low=11.6, close=11.9, volume=3800000),
        ],
        data_quality=DataQuality.D_weak_or_stale,
        source_url=None,
        meta=meta,
    )
    return build_company_snapshot(profile=profile, prices=prices)


@_SKIP
@pytest.mark.asyncio
async def test_azure_openai_client_instantiates():
    """AzureOpenAIResearchLLMClient instantiates when env vars are present."""
    client = AzureOpenAIResearchLLMClient()
    assert client.provider_name == "azure_openai"
    assert client.is_mock is False


@_SKIP
@pytest.mark.asyncio
async def test_azure_openai_returns_research_sections():
    """Real Azure OpenAI call returns a populated ResearchSectionsOutput."""
    client = AzureOpenAIResearchLLMClient()
    snapshot = _sample_snapshot()

    from pathlib import Path
    prompt_path = (
        Path(__file__).resolve().parents[3]
        / "packages" / "prompts" / "research" / "phase7_company_research_v1.md"
    )
    prompt_template = prompt_path.read_text(encoding="utf-8")

    result = await client.generate_research_sections(
        company_snapshot=snapshot,
        prompt_template=prompt_template,
    )

    assert isinstance(result, ResearchSectionsOutput)
    assert result.thesis_summary_draft, "thesis_summary_draft must not be empty"
    assert result.business_overview_draft, "business_overview_draft must not be empty"
    assert isinstance(result.missing_information, list)
    assert result.self_critique_limitations, "self_critique_limitations must not be empty"


@_SKIP
@pytest.mark.asyncio
async def test_azure_openai_output_references_company():
    """LLM output references the supplied company (Neste / Finland / Energy)."""
    client = AzureOpenAIResearchLLMClient()
    snapshot = _sample_snapshot()

    from pathlib import Path
    prompt_path = (
        Path(__file__).resolve().parents[3]
        / "packages" / "prompts" / "research" / "phase7_company_research_v1.md"
    )
    prompt_template = prompt_path.read_text(encoding="utf-8")

    result = await client.generate_research_sections(
        company_snapshot=snapshot,
        prompt_template=prompt_template,
    )

    combined = (
        result.thesis_summary_draft + " " +
        result.business_overview_draft + " " +
        result.self_critique_limitations
    ).lower()

    # Must reference the company or its sector
    assert any(word in combined for word in ["neste", "finland", "energy", "renewable", "refin"]), (
        f"Output does not reference the supplied company. Combined text: {combined[:300]}"
    )


@_SKIP
@pytest.mark.asyncio
async def test_azure_openai_no_rating_emitted():
    """Real LLM output must not contain investment rating keywords."""
    import re
    client = AzureOpenAIResearchLLMClient()
    snapshot = _sample_snapshot()

    from pathlib import Path
    prompt_path = (
        Path(__file__).resolve().parents[3]
        / "packages" / "prompts" / "research" / "phase7_company_research_v1.md"
    )
    prompt_template = prompt_path.read_text(encoding="utf-8")

    result = await client.generate_research_sections(
        company_snapshot=snapshot,
        prompt_template=prompt_template,
    )

    all_text = " ".join([
        result.thesis_summary_draft,
        result.business_overview_draft,
        result.self_critique_limitations,
    ])

    rating_pattern = re.compile(
        r"\b(BUY|SELL|HOLD|WATCH|REJECT|SHORTLIST_HIGH|SHORTLIST|WATCHLIST|PASS)\b",
        re.IGNORECASE,
    )
    match = rating_pattern.search(all_text)
    assert match is None, f"Rating keyword '{match.group()}' found in LLM output: {all_text[:500]}"


@_SKIP
@pytest.mark.asyncio
async def test_azure_openai_no_price_target_emitted():
    """Real LLM output must not contain price target or fair value references."""
    import re
    client = AzureOpenAIResearchLLMClient()
    snapshot = _sample_snapshot()

    from pathlib import Path
    prompt_path = (
        Path(__file__).resolve().parents[3]
        / "packages" / "prompts" / "research" / "phase7_company_research_v1.md"
    )
    prompt_template = prompt_path.read_text(encoding="utf-8")

    result = await client.generate_research_sections(
        company_snapshot=snapshot,
        prompt_template=prompt_template,
    )

    all_text = " ".join([
        result.thesis_summary_draft,
        result.business_overview_draft,
        result.self_critique_limitations,
    ])

    price_target_pattern = re.compile(
        r"\bprice\s+target\b|\btarget\s+price\b|\bfair\s+value\b|\bupside\s+of\b",
        re.IGNORECASE,
    )
    match = price_target_pattern.search(all_text)
    assert match is None, (
        f"Price target phrase '{match.group()}' found in LLM output: {all_text[:500]}"
    )


@_SKIP
@pytest.mark.asyncio
async def test_azure_openai_validate_sections_passes():
    """validate_llm_sections() must pass on real Azure OpenAI output."""
    client = AzureOpenAIResearchLLMClient()
    snapshot = _sample_snapshot()

    from pathlib import Path
    prompt_path = (
        Path(__file__).resolve().parents[3]
        / "packages" / "prompts" / "research" / "phase7_company_research_v1.md"
    )
    prompt_template = prompt_path.read_text(encoding="utf-8")

    result = await client.generate_research_sections(
        company_snapshot=snapshot,
        prompt_template=prompt_template,
    )

    validation = validate_llm_sections(result)
    assert validation.passed, (
        f"Safety gate failed. Warnings: {validation.warnings}\n"
        f"Output: thesis={result.thesis_summary_draft[:200]}"
    )
    assert validation.warnings == []


@_SKIP
@pytest.mark.asyncio
async def test_azure_openai_self_critique_is_present():
    """self_critique_limitations must acknowledge draft status and data limitations."""
    client = AzureOpenAIResearchLLMClient()
    snapshot = _sample_snapshot()

    from pathlib import Path
    prompt_path = (
        Path(__file__).resolve().parents[3]
        / "packages" / "prompts" / "research" / "phase7_company_research_v1.md"
    )
    prompt_template = prompt_path.read_text(encoding="utf-8")

    result = await client.generate_research_sections(
        company_snapshot=snapshot,
        prompt_template=prompt_template,
    )

    critique = result.self_critique_limitations.lower()
    # Must acknowledge limitations — not investment advice, data missing, draft
    assert any(
        phrase in critique
        for phrase in ["not investment", "draft", "limited", "missing", "advice", "data"]
    ), f"self_critique_limitations does not acknowledge limitations: {result.self_critique_limitations}"


@_SKIP
@pytest.mark.asyncio
async def test_azure_openai_missing_information_is_populated():
    """missing_information list must include at least some financial fields."""
    client = AzureOpenAIResearchLLMClient()
    snapshot = _sample_snapshot()

    from pathlib import Path
    prompt_path = (
        Path(__file__).resolve().parents[3]
        / "packages" / "prompts" / "research" / "phase7_company_research_v1.md"
    )
    prompt_template = prompt_path.read_text(encoding="utf-8")

    result = await client.generate_research_sections(
        company_snapshot=snapshot,
        prompt_template=prompt_template,
    )

    assert len(result.missing_information) > 0, "missing_information must not be empty"
    missing_lower = " ".join(result.missing_information).lower()
    assert any(
        term in missing_lower
        for term in ["revenue", "ebitda", "earnings", "market cap", "financial", "debt", "profit"]
    ), f"missing_information does not list financial fields: {result.missing_information}"
