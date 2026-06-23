import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AgentRunRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    workflow_name: str
    workflow_version: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    trigger_type: str
    total_tokens: int | None
    total_cost: float | None
    error_message: str | None


class WorkflowRunRequest(BaseModel):
    company_id: uuid.UUID | None = Field(
        None, description="ID of an existing company record"
    )
    ticker: str | None = Field(
        None, max_length=20, description="Ticker symbol if company_id not provided"
    )
    exchange: str | None = Field(
        None, max_length=20, description="Exchange code when creating from ticker"
    )
    # Phase 6: provider and validation control
    provider_name: str | None = Field(
        None,
        description=(
            "Financial data provider to use. "
            "Omit to use the FINANCIAL_DATA_PROVIDER config value (default: mock). "
            "Options: mock, stooq, gleif, sec_edgar, eodhd."
        ),
    )
    require_schema_valid: bool = Field(
        False,
        description=(
            "If true, the workflow fails with status=failed when the schema draft "
            "does not validate against the real-asset report schema. "
            "Default false — validation result is stored but does not block completion."
        ),
    )
    # Phase 7: LLM research sections control
    use_llm: bool = Field(
        False,
        description=(
            "If true, the workflow runs the generate_research_sections LLM node "
            "after building the company snapshot. "
            "Default false — safe offline mode with no LLM calls. "
            "When true, uses the LLM_PROVIDER config (default: mock)."
        ),
    )
    llm_provider: str | None = Field(
        None,
        description=(
            "LLM provider to use when use_llm=true. "
            "Omit to use the LLM_PROVIDER config value (default: mock). "
            "Options: mock, azure_openai."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"company_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"},
                {"ticker": "NOVO B", "exchange": "CPH"},
                {
                    "ticker": "AAPL",
                    "exchange": "NASDAQ",
                    "provider_name": "mock",
                    "require_schema_valid": False,
                    "use_llm": False,
                },
                {
                    "ticker": "AAPL",
                    "exchange": "NASDAQ",
                    "provider_name": "mock",
                    "use_llm": True,
                    "llm_provider": "mock",
                },
            ]
        }
    }


class WorkflowRunResponse(BaseModel):
    agent_run_id: uuid.UUID
    draft_report_id: uuid.UUID | None
    status: str
    summary: str
    workflow_name: str
    company_name: str | None = None
    ticker: str | None = None
    # Phase 6: provider + validation summary
    provider_name: str | None = None
    is_mock: bool | None = None
    schema_valid: bool | None = None
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    # Phase 7: LLM summary
    llm_provider: str | None = None
    llm_used: bool | None = None
