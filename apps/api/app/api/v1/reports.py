"""
Admin-only draft report listing and detail endpoints.
These endpoints are for internal development and admin review only.
They are not public-facing and must not be exposed without authentication.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.report import ReportList, ReportRead
from app.services import report_service

router = APIRouter(tags=["reports"])


@router.get("/reports", response_model=ReportList)
async def list_reports(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> ReportList:
    """List all draft reports (admin/dev only). Not a public endpoint."""
    reports, total = await report_service.list_reports(db, limit=limit, offset=offset)
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
