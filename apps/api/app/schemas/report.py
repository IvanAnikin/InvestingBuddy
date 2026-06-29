import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Review status constants
# ---------------------------------------------------------------------------

REVIEW_STATUSES = [
    "draft",
    "under_review",
    "approved_internal",
    "rejected_internal",
    "needs_revision",
    "archived",
]

REVIEW_ACTIONS = [
    "mark_under_review",
    "approve",
    "reject",
    "needs_revision",
]


# ---------------------------------------------------------------------------
# Report CRUD schemas
# ---------------------------------------------------------------------------


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
    human_review_required: bool = True


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

    # Phase 11 review workflow fields
    review_status: str = "draft"
    reviewed_at: datetime | None = None
    reviewer_note: str | None = None
    review_decision_reason: str | None = None
    human_review_required: bool = True
    approved_by: str | None = None
    rejected_by: str | None = None


class ReportList(BaseModel):
    items: list[ReportRead]
    total: int


# ---------------------------------------------------------------------------
# Phase 11: Review action schemas
# ---------------------------------------------------------------------------


class ReviewActionRequest(BaseModel):
    note: str | None = Field(
        None,
        description="Reviewer note. Required for reject and needs_revision actions.",
    )
    actor_label: str | None = Field(
        None,
        max_length=200,
        description="Label identifying the reviewer (e.g. admin email or username).",
    )
    acknowledge_warnings: bool = Field(
        False,
        description=(
            "Must be True to approve a report when schema_valid=False or "
            "human_review_required=True."
        ),
    )


class ReviewActionResponse(BaseModel):
    report_id: uuid.UUID
    action: str
    from_status: str | None
    to_status: str
    note: str | None
    actor_label: str | None
    message: str


# ---------------------------------------------------------------------------
# Phase 11: Review event schemas
# ---------------------------------------------------------------------------


class ReviewEventRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    report_id: uuid.UUID
    action: str
    from_status: str | None
    to_status: str
    note: str | None
    actor_label: str | None
    created_at: datetime


class ReviewEventList(BaseModel):
    items: list[ReviewEventRead]
    total: int
