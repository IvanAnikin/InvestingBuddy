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

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"company_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"},
                {"ticker": "NOVO B", "exchange": "CPH"},
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
