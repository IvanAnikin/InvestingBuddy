"""
Admin-only draft report listing and detail endpoints.
These endpoints are for internal development and admin review only.
They are not public-facing and must not be exposed without authentication.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.primary_document import ReportPrimaryDocumentsResponse
from app.schemas.report import ReportList, ReportRead
from app.services import primary_document_view_service, report_service

router = APIRouter(tags=["reports"])


@router.get("/reports", response_model=ReportList)
async def list_reports(
    limit: int = 50,
    offset: int = 0,
    company_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Optional read filter: return only the reports linked to this "
            "company (reports.company_id, migration 012). Omitting it keeps "
            "the unfiltered global listing."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> ReportList:
    """List draft reports (admin/dev only). Not a public endpoint.

    ``company_id`` is a plain, read-only scope filter. It lets a caller resolve
    which report is a company's CURRENT research report without paging the
    global list, and it neither selects nor ranks anything on the caller's
    behalf — ordering (newest first) and report content are unchanged.
    """
    reports, total = await report_service.list_reports(
        db, limit=limit, offset=offset, company_id=company_id
    )
    return ReportList(items=[ReportRead.model_validate(r) for r in reports], total=total)


@router.get("/reports/{report_id}", response_model=ReportRead)
async def get_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ReportRead:
    """Get a single draft report by ID (admin/dev only). Not a public endpoint."""
    report = await report_service.get_report(db, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return ReportRead.model_validate(report)


@router.get(
    "/reports/{report_id}/primary-documents",
    response_model=ReportPrimaryDocumentsResponse,
)
async def get_report_primary_documents(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ReportPrimaryDocumentsResponse:
    """Primary-document ingestion provenance for one report (admin/dev only).

    Phase 32A Slice 5B.3. Bounded, read-only view of what the primary-document
    ingestion pipeline actually discovered/attempted/extracted for this
    report's generating run — never a raw document body, raw OCR output, or
    provider exception. Scoped to the report's own company/run; never another
    report's data. A report with no ingestion activity returns an honest
    all-zero summary, not an error.
    """
    report = await report_service.get_report(db, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return await primary_document_view_service.get_report_primary_documents(
        db,
        report_company_id=report.company_id,
        report_agent_run_id=report.created_by_agent_run_id,
        report_id=report_id,
    )
