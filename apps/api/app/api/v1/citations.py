import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.source import (
    CitationCreate,
    CitationList,
    CitationRead,
    CitationValidationResult,
)
from app.services import citation_service, report_service, source_service

router = APIRouter(prefix="/reports", tags=["citations"])


@router.post(
    "/{report_id}/citations",
    response_model=CitationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_citation(
    report_id: uuid.UUID,
    payload: CitationCreate,
    db: AsyncSession = Depends(get_db),
) -> CitationRead:
    report = await report_service.get_report(db, report_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )
    source = await source_service.get_source(db, payload.source_id)
    if not source:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Source {payload.source_id} not found",
        )
    data = payload.model_copy(update={"report_id": report_id})
    citation = await citation_service.create_citation(db, data)
    return CitationRead.model_validate(citation)


@router.get("/{report_id}/citations", response_model=CitationList)
async def list_citations(
    report_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CitationList:
    report = await report_service.get_report(db, report_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )
    citations = await citation_service.list_citations_for_report(db, report_id)
    total = await citation_service.count_citations_for_report(db, report_id)
    return CitationList(
        items=[CitationRead.model_validate(c) for c in citations],
        total=total,
    )


@router.post("/{report_id}/validate-citations", response_model=CitationValidationResult)
async def validate_citations(
    report_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CitationValidationResult:
    report = await report_service.get_report(db, report_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )
    citations = await citation_service.list_citations_for_report(db, report_id)
    # Use content_markdown as a minimal proxy for analysis_output when no
    # structured data is stored on the report itself.
    analysis_output: dict = {"is_placeholder": True, "rating": "WATCH", "thesis": ""}
    return citation_service.validate_citations_for_draft(analysis_output, citations)
