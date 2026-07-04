"""
LLM Provider Abstraction — Phase 7.

Provides a pluggable interface for LLM-powered research section generation.

Implementations:
  MockResearchLLMClient      — deterministic, offline, no credentials, default for CI
  AzureOpenAIResearchLLMClient — calls Azure OpenAI; requires env vars; never used in CI

The workflow selects the client via get_llm_client(provider_name).
Default is "mock" — no Azure credentials required unless explicitly configured.

Output constraints (enforced by Pydantic schema and prompt):
  - NO BUY/SELL/WATCH/HOLD/REJECT rating
  - NO price target
  - NO valuation conclusion
  - NO unsupported financial numbers
  - All uncertainty marked as assumption, hypothesis or missing information
  - Draft sections only — not investment advice
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import settings

# ── Forbidden patterns in LLM output (safety gate) ──────────────────────────

_FORBIDDEN_RATING_PATTERN = re.compile(
    r"\b(BUY|SELL|HOLD|WATCH|REJECT|SHORTLIST_HIGH|SHORTLIST|WATCHLIST|PASS)\b",
    re.IGNORECASE,
)

_FORBIDDEN_PRICE_TARGET_PATTERN = re.compile(
    r"\bprice\s+target\b|\btarget\s+price\b|\bfair\s+value\b|\bupside\s+of\b",
    re.IGNORECASE,
)


# ── Output schema ────────────────────────────────────────────────────────────


class ResearchSectionsOutput(BaseModel):
    """
    Structured output from the LLM research sections node.

    Contains only safe draft sections — no rating, no price target,
    no valuation conclusion, no unsupported numbers.
    """

    thesis_summary_draft: str = Field(
        description=(
            "1-3 sentence factual summary of what the company does and why it "
            "may be relevant to the research universe. "
            "Must not contain a BUY/SELL/WATCH recommendation."
        )
    )
    business_overview_draft: str = Field(
        description=(
            "2-4 sentence description of the company's business model, "
            "primary products/services, and key markets. "
            "Based only on supplied context."
        )
    )
    missing_information: list[str] = Field(
        default_factory=list,
        description=(
            "List of important fields or data points that are missing from the "
            "supplied company snapshot and would be needed for a full analysis."
        ),
    )
    self_critique_limitations: str = Field(
        description=(
            "1-2 sentences describing the key limitations of this draft: "
            "what data is missing, what assumptions were made, "
            "and why the reader should not treat this as investment advice."
        )
    )


class LLMSectionsValidation(BaseModel):
    """Result of validating LLM output for forbidden content."""

    passed: bool
    warnings: list[str] = Field(default_factory=list)


def validate_llm_sections(output: ResearchSectionsOutput) -> LLMSectionsValidation:
    """
    Check generated sections for forbidden investment advice content.

    Returns warnings but does NOT crash — the output is demoted to draft with
    warnings appended. A hard failure would require re-running the LLM, which
    is expensive; instead we flag and let admin review.
    """
    warnings: list[str] = []
    all_text = " ".join(
        [
            output.thesis_summary_draft,
            output.business_overview_draft,
            output.self_critique_limitations,
        ]
    )

    if _FORBIDDEN_RATING_PATTERN.search(all_text):
        warnings.append(
            "LLM output contains a rating keyword (BUY/SELL/WATCH/etc). "
            "This section is draft-only and must not be used as investment advice."
        )

    if _FORBIDDEN_PRICE_TARGET_PATTERN.search(all_text):
        warnings.append(
            "LLM output contains a price target or fair value reference. "
            "Unsupported price estimates must be removed before use."
        )

    return LLMSectionsValidation(passed=len(warnings) == 0, warnings=warnings)


# ── Abstract base ────────────────────────────────────────────────────────────


class ResearchLLMClient(ABC):
    """Abstract interface for LLM-powered research section generation."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Identifier for this LLM backend (e.g. 'mock', 'azure_openai')."""

    @property
    @abstractmethod
    def is_mock(self) -> bool:
        """True when no real LLM is called."""

    @abstractmethod
    async def generate_research_sections(
        self,
        company_snapshot: dict[str, Any],
        prompt_template: str,
    ) -> ResearchSectionsOutput:
        """
        Generate structured research sections from the company snapshot.

        Args:
            company_snapshot: the dict produced by build_company_snapshot().
            prompt_template: versioned prompt text loaded from packages/prompts/.

        Returns:
            ResearchSectionsOutput with draft sections only. No rating or price target.
        """


# ── Mock implementation ──────────────────────────────────────────────────────


class MockResearchLLMClient(ResearchLLMClient):
    """
    Deterministic mock LLM client for offline tests and CI.

    Never makes network calls. Returns predictable output derived
    from the company snapshot. Marked is_mock=True in all outputs.
    """

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def is_mock(self) -> bool:
        return True

    async def generate_research_sections(
        self,
        company_snapshot: dict[str, Any],
        prompt_template: str,
    ) -> ResearchSectionsOutput:
        identity = company_snapshot.get("company_identity", {})
        profile = company_snapshot.get("profile", {})
        missing = company_snapshot.get("missing_fields", [])

        legal_name = identity.get("legal_name", "Unknown Company")
        ticker = identity.get("ticker", "N/A")
        sector = profile.get("sector") or "unknown sector"
        country = identity.get("country_domicile") or "unknown country"
        currency = profile.get("reporting_currency") or "unknown currency"
        is_mock_data = company_snapshot.get("is_mock", True)

        mock_note = " [MOCK DATA — not real financial information]" if is_mock_data else ""

        thesis_summary_draft = (
            f"{legal_name} ({ticker}) operates in the {sector} sector "
            f"and is domiciled in {country}. "
            f"The company reports in {currency}.{mock_note} "
            "This is a draft summary based on provider identity data only — "
            "no LLM analysis of financials, filings or news has been performed."
        )

        business_overview_draft = (
            f"{legal_name} is classified under the {sector} sector "
            f"({profile.get('industry', 'industry not specified')}). "
            f"Country of domicile: {country}. Reporting currency: {currency}. "
            "Full business overview requires filing data and LLM analysis — "
            f"not yet available.{mock_note}"
        )

        all_missing = list(missing) + [
            "filings_summary",
            "revenue_data",
            "ebitda_data",
            "market_cap",
            "news_summary",
        ]

        self_critique = (
            "This draft was generated by a mock LLM client using identity and profile "
            "data only. No financial metrics, filings, news or valuation data were "
            "available. This output is for development and testing purposes only and "
            "must not be treated as investment research or advice."
        )

        return ResearchSectionsOutput(
            thesis_summary_draft=thesis_summary_draft,
            business_overview_draft=business_overview_draft,
            missing_information=all_missing,
            self_critique_limitations=self_critique,
        )


# ── Azure OpenAI implementation ──────────────────────────────────────────────


class AzureOpenAIResearchLLMClient(ResearchLLMClient):
    """
    Azure OpenAI LLM client using LangChain AzureChatOpenAI with structured output.

    Requires env vars: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION, AZURE_OPENAI_DEPLOYMENT_NAME.

    Will raise ConfigurationError if env vars are absent.
    Only instantiated when LLM_PROVIDER=azure_openai in config.
    Never used in CI — default is MockResearchLLMClient.
    """

    def __init__(self) -> None:
        if not settings.azure_openai_endpoint:
            raise ValueError(
                "AZURE_OPENAI_ENDPOINT is not configured. "
                "Set LLM_PROVIDER=mock to use the offline mock client, "
                "or configure Azure OpenAI env vars."
            )
        if not settings.azure_openai_api_key:
            raise ValueError(
                "AZURE_OPENAI_API_KEY is not configured. "
                "Set LLM_PROVIDER=mock to use the offline mock client."
            )
        if not settings.azure_openai_deployment_name:
            raise ValueError(
                "AZURE_OPENAI_DEPLOYMENT_NAME is not configured. "
                "Set LLM_PROVIDER=mock to use the offline mock client."
            )

        try:
            from langchain_openai import AzureChatOpenAI  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "langchain-openai is required for AzureOpenAIResearchLLMClient. "
                "Install it with: pip install langchain-openai"
            ) from exc

        self._llm = AzureChatOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,  # type: ignore[arg-type]
            api_version=settings.azure_openai_api_version,
            azure_deployment=settings.azure_openai_deployment_name,
            temperature=0.2,
            max_tokens=1500,
        )
        self._structured_llm = self._llm.with_structured_output(ResearchSectionsOutput)

    @property
    def provider_name(self) -> str:
        return "azure_openai"

    @property
    def is_mock(self) -> bool:
        return False

    async def generate_research_sections(
        self,
        company_snapshot: dict[str, Any],
        prompt_template: str,
    ) -> ResearchSectionsOutput:
        identity = company_snapshot.get("company_identity", {})
        profile = company_snapshot.get("profile", {})
        missing = company_snapshot.get("missing_fields", [])
        price_summary = company_snapshot.get("price_history_summary", {})
        provider_meta = company_snapshot.get("provider_metadata", {})

        context = (
            f"Company: {identity.get('legal_name', 'N/A')} ({identity.get('ticker', 'N/A')})\n"
            f"Exchange: {identity.get('exchange', 'N/A')}\n"
            f"Country: {identity.get('country_domicile', 'N/A')}\n"
            f"ISIN: {identity.get('isin', 'N/A')}\n"
            f"LEI: {identity.get('lei', 'N/A')}\n"
            f"Sector: {profile.get('sector', 'N/A')}\n"
            f"Industry: {profile.get('industry', 'N/A')}\n"
            f"Reporting currency: {profile.get('reporting_currency', 'N/A')}\n"
            f"Fiscal year end: {profile.get('fiscal_year_end', 'N/A')}\n"
            f"Website: {profile.get('website', 'N/A')}\n"
            f"Description: {profile.get('description', 'N/A')}\n"
            f"Price data available: {price_summary.get('available', False)}\n"
            f"Latest close: {price_summary.get('latest_close', 'N/A')}\n"
            f"Data provider: {provider_meta.get('provider_name', 'N/A')} "
            f"(tier {provider_meta.get('source_tier', 'N/A')})\n"
            f"Is mock data: {provider_meta.get('is_mock', True)}\n"
            f"Missing fields: {', '.join(missing) if missing else 'none listed'}\n"
        )

        full_prompt = prompt_template.replace("{{COMPANY_CONTEXT}}", context)

        result: ResearchSectionsOutput = await self._structured_llm.ainvoke(full_prompt)
        return result


# ── Factory ──────────────────────────────────────────────────────────────────


def get_llm_client(provider: str | None = None) -> ResearchLLMClient:
    """
    Return the appropriate ResearchLLMClient for the given provider name.

    Falls back to config.llm_provider if provider is None.
    Falls back to MockResearchLLMClient if provider is unknown or 'mock'.
    AzureOpenAIResearchLLMClient is only instantiated when provider='azure_openai'
    AND all required env vars are set.
    """
    resolved = (provider or settings.llm_provider or "mock").lower().strip()

    if resolved == "azure_openai":
        return AzureOpenAIResearchLLMClient()

    return MockResearchLLMClient()
