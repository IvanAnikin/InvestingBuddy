"""
Phase 14: Company Discovery / Screener API endpoints.

All endpoints are admin/dev-only internal endpoints.
No public investment advice, recommendations, price targets, or fair values
are produced by any endpoint in this module.

Endpoints:
  POST   /api/v1/discovery/universes
  GET    /api/v1/discovery/universes
  POST   /api/v1/discovery/runs
  GET    /api/v1/discovery/runs
  GET    /api/v1/discovery/runs/{run_id}
  GET    /api/v1/discovery/runs/{run_id}/candidates
  POST   /api/v1/discovery/candidates/{candidate_id}/promote
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.discovery import (
    PromoteCandidateResponse,
    ScreeningCandidateList,
    ScreeningCandidateRead,
    ScreeningRunCreate,
    ScreeningRunList,
    ScreeningRunRead,
    ScreeningUniverseCreate,
    ScreeningUniverseList,
    ScreeningUniverseRead,
)
from app.services import company_discovery_service

router = APIRouter(prefix="/discovery", tags=["discovery"])


# ---------------------------------------------------------------------------
# Universes
# ---------------------------------------------------------------------------


@router.post(
    "/universes",
    response_model=ScreeningUniverseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create screening universe (admin/dev only)",
)
async def create_universe(
    payload: ScreeningUniverseCreate,
    db: AsyncSession = Depends(get_db),
) -> ScreeningUniverseRead:
    """
    Create a named universe of companies to screen.

    Admin/dev only — not a public endpoint.
    Not investment advice.
    """
    universe = await company_discovery_service.create_universe(db, payload)
    return ScreeningUniverseRead.model_validate(universe)


@router.get(
    "/universes",
    response_model=ScreeningUniverseList,
    summary="List screening universes (admin/dev only)",
)
async def list_universes(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> ScreeningUniverseList:
    """
    List all screening universe definitions.

    Admin/dev only — not a public endpoint.
    """
    items, total = await company_discovery_service.list_universes(
        db, limit=limit, offset=offset
    )
    return ScreeningUniverseList(
        items=[ScreeningUniverseRead.model_validate(u) for u in items],
        total=total,
    )


# ---------------------------------------------------------------------------
# Screening runs
# ---------------------------------------------------------------------------


@router.post(
    "/runs",
    response_model=ScreeningRunRead,
    status_code=status.HTTP_201_CREATED,
    summary="Run a company screen (admin/dev only)",
)
async def create_screening_run(
    payload: ScreeningRunCreate,
    db: AsyncSession = Depends(get_db),
) -> ScreeningRunRead:
    """
    Execute a screening run against a universe.

    The screener produces an internal list of candidate companies.
    No investment recommendation, price target, or fair value is produced.
    Admin/dev only — not a public endpoint.

    Raises 404 if the universe is not found.
    """
    try:
        run = await company_discovery_service.run_screening(db, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return ScreeningRunRead.model_validate(run)


@router.get(
    "/runs",
    response_model=ScreeningRunList,
    summary="List screening runs (admin/dev only)",
)
async def list_screening_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    universe_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> ScreeningRunList:
    """
    List all screening runs, optionally filtered by universe.

    Admin/dev only — not a public endpoint.
    """
    items, total = await company_discovery_service.list_screening_runs(
        db, limit=limit, offset=offset, universe_id=universe_id
    )
    return ScreeningRunList(
        items=[ScreeningRunRead.model_validate(r) for r in items],
        total=total,
    )


@router.get(
    "/runs/{run_id}",
    response_model=ScreeningRunRead,
    summary="Get screening run by ID (admin/dev only)",
)
async def get_screening_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ScreeningRunRead:
    """
    Get a single screening run by UUID.

    Admin/dev only — not a public endpoint.
    """
    run = await company_discovery_service.get_screening_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Screening run {run_id} not found",
        )
    return ScreeningRunRead.model_validate(run)


@router.get(
    "/runs/{run_id}/candidates",
    response_model=ScreeningCandidateList,
    summary="List candidates for a screening run (admin/dev only)",
)
async def list_candidates(
    run_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> ScreeningCandidateList:
    """
    List all screening candidates produced by a run.

    Candidates are internal research funnel entries only.
    Not investment recommendations. Not investment advice.
    Admin/dev only — not a public endpoint.
    """
    run = await company_discovery_service.get_screening_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Screening run {run_id} not found",
        )

    items, total = await company_discovery_service.list_candidates(
        db, run_id=run_id, limit=limit, offset=offset
    )
    return ScreeningCandidateList(
        items=[ScreeningCandidateRead.model_validate(c) for c in items],
        total=total,
    )


# ---------------------------------------------------------------------------
# Candidate promotion
# ---------------------------------------------------------------------------


@router.post(
    "/candidates/{candidate_id}/promote",
    response_model=PromoteCandidateResponse,
    summary="Promote candidate to company analysis funnel (admin/dev only)",
)
async def promote_candidate(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> PromoteCandidateResponse:
    """
    Promote a screening candidate to the company analysis funnel.

    This creates or identifies a Company record and marks the candidate as
    'ready_for_deeper_analysis'. It does NOT:
      - Trigger the analysis workflow automatically.
      - Create any investment recommendation.
      - Publish anything.
      - Produce a price target or fair value.

    The admin must separately run the company-analysis workflow for deeper research.

    Admin/dev only — not a public endpoint.
    Raises 404 if the candidate is not found.
    Raises 422 if the candidate is in error or rejected state.
    """
    try:
        result = await company_discovery_service.promote_candidate_to_analysis(
            db, candidate_id
        )
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=msg,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=msg,
        ) from exc
    return result
