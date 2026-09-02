"""Async company-research job — request and response schemas.

INTERNAL ADMIN USE ONLY. A job's ``status`` is a WORKFLOW lifecycle state and
its ``stage`` is which part of the pipeline is running. Neither is ever an
investment action, and no rating, price target, fair value or return
projection appears anywhere in this contract.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.services import research_job

INTERNAL_DISCLAIMER = (
    "INTERNAL USE ONLY. NOT INVESTMENT ADVICE. NOT A PUBLIC RECOMMENDATION. "
    "No rating, price target, fair value or return projection is produced. "
    "Human review is required before any use."
)


class CompanyResearchJobCreate(BaseModel):
    """Start a research job for one company.

    Identity is given as a canonical ``company_id``, or as the exact
    ``(ticker, exchange)`` pair the company is registered under. It is resolved
    against the database ONCE, before the job is created, and carried on the
    job from there — nothing downstream re-derives which company this is from
    a label, a title or a search string.
    """

    company_id: uuid.UUID | None = None
    ticker: str | None = None
    exchange: str | None = None
    provider_name: str | None = Field(
        default=None,
        description=(
            "Financial-data provider for this run. Defaults to the server's "
            "configured provider."
        ),
    )
    use_llm: bool = Field(
        default=False,
        description=(
            "Run the optional LLM research-sections node. This is NOT the "
            "research council — the council is a server-side setting applied "
            "when the report is assembled, and no request flag turns it on."
        ),
    )
    llm_provider: str | None = None
    require_schema_valid: bool = False

    @model_validator(mode="after")
    def _identity_present(self) -> "CompanyResearchJobCreate":
        if self.company_id is None and not (self.ticker and self.exchange):
            raise ValueError(
                "Provide either company_id, or both ticker and exchange."
            )
        return self


class CompanyResearchJobCompany(BaseModel):
    """The company a job is FOR, as resolved when the job was created."""

    id: uuid.UUID
    ticker: str
    exchange: str
    name: str | None = None


class CompanyResearchStage(BaseModel):
    """One pipeline stage, with the words a reader sees."""

    key: str
    label: str
    complete: bool
    current: bool


class CompanyResearchJobResponse(BaseModel):
    """The state of ONE async company-research job.

    ``status`` is the job lifecycle (pending | running | interrupted |
    completed | completed_with_warnings | failed).

    ``interrupted`` is DERIVED at read time, never stored: execution is
    process-local, so an app restart mid-run would otherwise leave a job
    reading ``running`` forever. ``recoverable`` says re-running is safe and
    will not duplicate a completed report.
    """

    job_id: uuid.UUID
    status: str
    stage: str
    stage_label: str
    stages: list[CompanyResearchStage] = Field(default_factory=list)
    company: CompanyResearchJobCompany | None = None
    provider_name: str
    started_at: str | None = None
    completed_at: str | None = None
    workflow_status: str | None = None
    error: str | None = None
    recoverable: bool | None = None
    interrupted_reason: str | None = None
    #: The STRUCTURED final report this job produced. Null until it exists —
    #: the deterministic draft the workflow writes is not one.
    analysis_report_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    legacy_draft_report_id: uuid.UUID | None = None
    report: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    message: str
    human_review_required: bool = True
    disclaimer: str = INTERNAL_DISCLAIMER

    @classmethod
    def from_envelope(
        cls, envelope: dict[str, Any], *, message: str
    ) -> "CompanyResearchJobResponse":
        """Adapt a stored job envelope into the API response."""

        def _uid(key: str) -> uuid.UUID | None:
            raw = envelope.get(key)
            if not raw:
                return None
            try:
                return uuid.UUID(str(raw))
            except (ValueError, AttributeError, TypeError):
                return None

        stage = str(envelope.get("stage") or research_job.STAGE_QUEUED)
        completed = set(envelope.get("stages_completed") or [])
        stages = [
            CompanyResearchStage(
                key=key,
                label=research_job.stage_label(key),
                complete=key in completed and key != stage,
                current=key == stage,
            )
            for key in research_job.STAGE_ORDER
        ]
        company_raw = envelope.get("company")
        company = (
            CompanyResearchJobCompany.model_validate(company_raw)
            if isinstance(company_raw, dict) and company_raw.get("id")
            else None
        )
        return cls(
            job_id=uuid.UUID(str(envelope["job_id"])),
            status=str(envelope.get("status") or research_job.STATUS_PENDING),
            stage=stage,
            stage_label=research_job.stage_label(stage),
            stages=stages,
            company=company,
            provider_name=str(envelope.get("provider_name") or "unknown"),
            started_at=envelope.get("started_at"),
            completed_at=envelope.get("completed_at"),
            workflow_status=envelope.get("workflow_status"),
            error=envelope.get("error"),
            recoverable=envelope.get("recoverable"),
            interrupted_reason=envelope.get("interrupted_reason"),
            analysis_report_id=_uid("analysis_report_id"),
            agent_run_id=_uid("agent_run_id"),
            legacy_draft_report_id=_uid("legacy_draft_report_id"),
            report=(
                envelope.get("report")
                if isinstance(envelope.get("report"), dict)
                else None
            ),
            warnings=list(envelope.get("warnings") or []),
            message=message,
        )
