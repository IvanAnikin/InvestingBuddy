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

    # --- populated during workflow ---
    agent_run_id: str | None
    company_name: str | None
    company_sector: str | None
    company_description: str | None

    # --- analysis output ---
    analysis_output: dict | None   # structured JSON matching agent output schema
    draft_report_id: str | None

    # --- control ---
    error: str | None
    status: str   # running | completed | failed
