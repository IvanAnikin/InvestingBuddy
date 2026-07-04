import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.report import (
    GenerateFinalReportResponse,
    ReportList,
    ReportRead,
    ValidateReportResponse,
)
from app.services import report_service

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("", response_model=ReportList)
async def list_reports(
    report_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> ReportList:
    reports = await report_service.list_reports(
        db, status=report_status, limit=limit, offset=offset
    )
    total = await report_service.count_reports(db, status=report_status)
    return ReportList(
        items=[ReportRead.model_validate(r) for r in reports],
        total=total,
    )


@router.get("/{report_id}", response_model=ReportRead)
async def get_report(
    report_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ReportRead:
    report = await report_service.get_report(db, report_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )
    return ReportRead.model_validate(report)


@router.post(
    "/{report_id}/generate-final",
    response_model=GenerateFinalReportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate internal final report draft",
    description=(
        "INTERNAL ADMIN ONLY — NOT FOR PUBLICATION. "
        "Triggers generation of an internal final report draft. "
        "Output requires human review before any publication decision."
    ),
)
async def generate_final_report(
    report_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> GenerateFinalReportResponse:
    report = await report_service.get_report(db, report_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )
    # Placeholder: real LLM generation wired in a later phase
    return GenerateFinalReportResponse(
        report_id=report_id,
        status="draft_generated",
        message=(
            "Internal final report draft queued. "
            "NOT INVESTMENT ADVICE — HUMAN REVIEW REQUIRED before any publication."
        ),
    )


@router.post(
    "/{report_id}/validate",
    response_model=ValidateReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate final report",
    description=(
        "INTERNAL ADMIN ONLY — NOT INVESTMENT ADVICE. "
        "Runs internal validation checks on the draft report. "
        "Does not publish. Human review required."
    ),
)
async def validate_report(
    report_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ValidateReportResponse:
    report = await report_service.get_report(db, report_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )
    # Placeholder: real validation wired in a later phase
    has_content = bool(report.content_markdown or report.summary)
    issues = [] if has_content else ["Report has no content to validate"]
    return ValidateReportResponse(
        report_id=report_id,
        validation_passed=has_content,
        issues=issues,
        message=(
            "Validation complete (placeholder). "
            "NOT INVESTMENT ADVICE — HUMAN REVIEW REQUIRED."
        ),
    )
