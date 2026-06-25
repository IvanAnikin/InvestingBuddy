import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class ReportCreate(BaseModel):
    title: str = Field(..., max_length=500)
    slug: str = Field(..., max_length=500)
    report_type: str = Field(
        ...,
        description="weekly|monthly|quarterly|yearly|company_deep_dive|theme_report|personalized",
    )
    summary: str | None = None
    content_markdown: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    created_by_agent_run_id: uuid.UUID | None = None


class ReportRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    title: str
    slug: str
    report_type: str
    period_start: date | None
    period_end: date | None
    status: str
    summary: str | None
    content_markdown: str | None
    content_html: str | None
    created_by_agent_run_id: uuid.UUID | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ReportList(BaseModel):
    items: list[ReportRead]
    total: int
