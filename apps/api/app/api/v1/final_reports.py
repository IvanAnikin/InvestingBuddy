"""
Phase 16: Final Report Generator — API endpoints.

Admin/dev-only endpoints.  No public-facing routes.
No investment recommendations, price targets, fair values, or upside
percentages are produced.  Human review is always required.

Endpoints:
  POST /api/v1/final-reports/from-scorecard/{scorecard_id}
  POST /api/v1/final-reports/from-candidate/{candidate_id}
  POST /api/v1/final-reports/from-company/{company_id}
  POST /api/v1/final-reports/from-report/{report_id}
  POST /api/v1/final-reports/{report_id}/validate
  POST /api/v1/final-reports/{report_id}/regenerate-section
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.final_report import (
    FinalReportResponse,
    FinalReportValidateResponse,
    RegenerateSectionRequest,
    RegenerateSectionResponse,
)
from app.services.final_report_generator import FinalReportGeneratorService
from app.services.report_lineage import AmbiguousReportLineageError

router = APIRouter(prefix="/final-reports", tags=["final-reports"])
logger = logging.getLogger(__name__)

_svc = FinalReportGeneratorService()


@router.post(
    "/from-scorecard/{scorecard_id}",
    response_model=FinalReportResponse,
    status_code=201,
    summary="Generate final report draft from a scorecard",
    description=(
        "ADMIN/DEV ONLY. Generates a structured internal final report draft "
        "from a research attractiveness scorecard. "
        "Report is saved with status=draft, review_status=draft, "
        "human_review_required=True. "
        "NOT investment advice. No BUY/SELL/HOLD/WATCH recommendation is produced. "
        "No price target or fair value is produced."
    ),
)
async def generate_from_scorecard(
    scorecard_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> FinalReportResponse:
    try:
        return await _svc.generate_from_scorecard(db, scorecard_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Final report generation from scorecard %s failed: %s",
            scorecard_id,
            exc,
        )
        raise HTTPException(
            status_code=422,
            detail=f"Final report generation failed: {exc}",
        ) from exc


@router.post(
    "/from-candidate/{candidate_id}",
    response_model=FinalReportResponse,
    status_code=201,
    summary="Generate final report draft from a screening candidate",
    description=(
        "ADMIN/DEV ONLY. Generates a structured internal final report draft "
        "from a screening candidate. "
        "Report is saved with status=draft, review_status=draft, "
        "human_review_required=True. "
        "NOT investment advice. No BUY/SELL/HOLD/WATCH recommendation is produced. "
        "No price target or fair value is produced."
    ),
)
async def generate_from_candidate(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> FinalReportResponse:
    try:
        return await _svc.generate_from_candidate(db, candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Final report generation from candidate %s failed: %s",
            candidate_id,
            exc,
        )
        raise HTTPException(
            status_code=422,
            detail=f"Final report generation failed: {exc}",
        ) from exc


@router.post(
    "/from-company/{company_id}",
    response_model=FinalReportResponse,
    status_code=201,
    summary="Generate final report draft for a company",
    description=(
        "ADMIN/DEV ONLY. Generates a structured internal final report draft "
        "for a company in the research universe. "
        "Selects the most recent COMPLETED analysis report FOR THAT COMPANY "
        "(company-scoped and deterministic — newest by created_at then id), "
        "along with that company's scorecard and citations. "
        "Returns 404 when the company is unknown OR when it has no eligible "
        "completed analysis report — there is NO cross-company fallback. "
        "Report is saved with status=draft, review_status=draft, "
        "human_review_required=True. "
        "NOT investment advice. No BUY/SELL/HOLD/WATCH recommendation is produced. "
        "No price target or fair value is produced."
    ),
)
async def generate_from_company(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> FinalReportResponse:
    try:
        return await _svc.generate_from_company(db, company_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Final report generation from company %s failed: %s",
            company_id,
            exc,
        )
        raise HTTPException(
            status_code=422,
            detail=f"Final report generation failed: {exc}",
        ) from exc


@router.post(
    "/from-report/{report_id}",
    response_model=FinalReportResponse,
    status_code=201,
    summary="Generate final report draft from an existing report",
    description=(
        "ADMIN/DEV ONLY. Generates a structured internal final report draft "
        "from an existing report and its linked workflow artifacts. "
        "Preserves the source report's EXACT lineage (company, agent run, "
        "discovery run, discovery candidate, discovery rationale). Returns 409 "
        "when two exact linkages conflict — it never picks a winner. "
        "Report is saved with status=draft, review_status=draft, "
        "human_review_required=True. "
        "NOT investment advice. No BUY/SELL/HOLD/WATCH recommendation is produced. "
        "No price target or fair value is produced."
    ),
)
async def generate_from_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> FinalReportResponse:
    try:
        return await _svc.generate_from_report(db, report_id)
    except AmbiguousReportLineageError as exc:
        # Two equally-exact linkages disagree about this report's discovery
        # origin. Regeneration FAILS CLOSED rather than picking one — a wrong
        # candidate/discovery-run attribution is worse than no report. 409, not
        # 404: the problem is too much linkage, not a missing record.
        logger.warning(
            "Final report regeneration from report %s has ambiguous lineage: %s",
            report_id,
            exc,
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Final report generation from report %s failed: %s",
            report_id,
            exc,
        )
        raise HTTPException(
            status_code=422,
            detail=f"Final report generation failed: {exc}",
        ) from exc


@router.post(
    "/{report_id}/validate",
    response_model=FinalReportValidateResponse,
    status_code=200,
    summary="Validate an existing final report (safety + schema)",
    description=(
        "ADMIN/DEV ONLY. Re-runs safety gate and schema validation against "
        "an existing final report draft. "
        "Updates safety_validation_json and schema_validation_json on the report. "
        "Returns full validation result including missing sections. "
        "Human review is still required even after validation passes."
    ),
)
async def validate_final_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> FinalReportValidateResponse:
    try:
        return await _svc.validate_final_report(db, report_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Final report validation for report %s failed: %s",
            report_id,
            exc,
        )
        raise HTTPException(
            status_code=422,
            detail=f"Report validation failed: {exc}",
        ) from exc


@router.post(
    "/{report_id}/regenerate-section",
    response_model=RegenerateSectionResponse,
    status_code=200,
    summary="Regenerate a single section of a final report",
    description=(
        "ADMIN/DEV ONLY. Regenerates a single named section of an existing "
        "final report draft using current workflow state data. "
        "The safety gate runs on the regenerated section. "
        "If the safety gate fails, the section is NOT saved. "
        "Human review is required after any section regeneration."
    ),
)
async def regenerate_section(
    report_id: uuid.UUID,
    request: RegenerateSectionRequest,
    db: AsyncSession = Depends(get_db),
) -> RegenerateSectionResponse:
    try:
        return await _svc.regenerate_report_section(
            db, report_id, request.section_name
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Section %s regeneration for report %s failed: %s",
            request.section_name,
            report_id,
            exc,
        )
        raise HTTPException(
            status_code=422,
            detail=f"Section regeneration failed: {exc}",
        ) from exc
