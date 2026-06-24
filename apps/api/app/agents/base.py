"""
Base types shared across all InvestingBuddy agent workflows.
"""

from typing import TypedDict


class CompanyAnalysisState(TypedDict):
    """Workflow state passed between nodes in the company analysis graph."""

    # --- input ---
    company_id: str | None
    ticker: str | None
    exchange: str | None

    # --- workflow control ---
    agent_run_id: str | None
    company_name: str | None
    company_sector: str | None
    company_description: str | None

    # --- Phase 6: provider selection ---
    provider_name: str | None          # which provider to use (None = config default)
    is_mock: bool | None               # True when mock provider is active

    # --- analysis output ---
    analysis_output: dict | None       # structured JSON matching agent output schema
    draft_report_id: str | None

    # --- Phase 3: source + citation tracking ---
    placeholder_source_id: str | None  # UUID of the placeholder Source record
    citation_ids: list[str] | None     # UUIDs of Citation records created

    # --- Phase 6: provider data + snapshot ---
    company_snapshot: dict | None              # structured provider snapshot
    provider_source_id: str | None            # UUID of Source record for profile data
    price_source_id: str | None               # UUID of Source record for price data (if available)
    source_ids: list[str] | None              # all Source UUIDs created from provider data
    schema_validation_result: dict | None     # {is_valid, errors, warnings} from validator
    schema_valid: bool | None                 # convenience flag

    # --- Phase 7: LLM research sections ---
    use_llm: bool | None                      # True = run generate_research_sections node
    llm_provider: str | None                  # resolved LLM provider name (mock | azure_openai)
    llm_used: bool | None                     # True when LLM node ran (not skipped)
    llm_sections: dict | None                 # ResearchSectionsOutput as dict
    llm_section_warnings: list[str] | None    # safety-gate warnings from validate_llm_sections

    # --- Phase 8: Research Team agent outputs ---
    financial_data_summary: dict | None          # FinancialDataAgent output
    source_quality_summary: dict | None          # SourceQualityAgent output
    research_completeness_summary: dict | None   # ResearchCompletenessAgent output
    upgraded_citation_validation: dict | None    # UpgradedCitationValidator output
    research_team_warnings: list[str] | None     # aggregated warnings from all RT agents
    research_team_complete: bool | None          # True when all RT nodes ran without fatal error

    # --- control ---
    error: str | None
    status: str   # running | completed | failed
