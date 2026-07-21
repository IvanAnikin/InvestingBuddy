"""
Phase 25: Real Market Candidate Discovery — API endpoints.

ADMIN / INTERNAL ONLY. No public routes. These endpoints drive a bounded,
internal-only market discovery workflow that produces an internal research
candidate queue.

Hard guarantees:
  - No BUY/SELL/HOLD/WATCH labels are ever returned.
  - No price targets, fair values, upside/downside, or recommendations.
  - ``candidate_score`` is an internal prioritization signal only.
  - Every candidate is human-review-required and non-public.
  - The universe size is validated before any work — an oversized run is
    rejected (422) to prevent an accidental full-market scan.

Endpoints:
  GET    /api/v1/market-discovery/supported-themes
  GET    /api/v1/market-discovery/runs
  POST   /api/v1/market-discovery/runs
  GET    /api/v1/market-discovery/runs/{run_id}
  GET    /api/v1/market-discovery/runs/{run_id}/summary
  GET    /api/v1/market-discovery/runs/{run_id}/candidates
  GET    /api/v1/market-discovery/candidates/{candidate_id}
  POST   /api/v1/market-discovery/candidates/{candidate_id}/run-analysis
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.market_discovery import (
    DiscoveryCandidateDetail,
    DiscoveryCandidateListResponse,
    DiscoveryCandidateRead,
    DiscoveryRunCreate,
    DiscoveryRunListResponse,
    DiscoveryRunRead,
    DiscoveryRunSummary,
    RunCandidateAnalysisResponse,
    SupportedThemesResponse,
    ThesisDiscoveryRunCreate,
)
from app.services import market_discovery_service as svc

router = APIRouter(prefix="/market-discovery", tags=["market-discovery"])

_INTERNAL = (
    "INTERNAL ADMIN ONLY. Not investment advice. Not a public recommendation. "
    "Candidate scores are an internal prioritization signal only. Human review "
    "required."
)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@router.get(
    "/runs",
    response_model=DiscoveryRunListResponse,
    summary="List discovery runs (admin/internal only)",
)
async def list_discovery_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> DiscoveryRunListResponse:
    runs, total = await svc.list_runs(db, limit=limit, offset=offset)
    return DiscoveryRunListResponse(
        runs=[DiscoveryRunRead.model_validate(r) for r in runs],
        total=total,
    )


@router.post(
    "/runs",
    response_model=DiscoveryRunRead,
    status_code=status.HTTP_201_CREATED,
    summary="Start an internal discovery scan asynchronously (admin/internal only)",
    description=(
        "ADMIN/INTERNAL ONLY. Creates a bounded market discovery run over a "
        "curated seed universe or a manual ticker list and returns the run_id "
        "IMMEDIATELY (status='pending'). Tickers are processed in the "
        "background — poll GET /runs/{run_id} for progress and "
        "GET /runs/{run_id}/candidates for results as they appear. Rejects "
        "(422) an empty universe or one exceeding DISCOVERY_MAX_UNIVERSE_SIZE "
        "BEFORE any background work is scheduled. " + _INTERNAL
    ),
)
async def create_discovery_run(
    payload: DiscoveryRunCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> DiscoveryRunRead:
    try:
        run = await svc.create_pending_run(db, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    # Process the (already-committed) run in the background using its own DB
    # session — never the request-scoped one, which is closed after the
    # response. Only the primitive run_id is handed to the task.
    background_tasks.add_task(svc.process_discovery_run_task, str(run.id))

    dto = DiscoveryRunRead.model_validate(run)
    dto.message = (
        "Discovery run started. Processing in the background — refresh or poll "
        "run status for progress."
    )
    return dto


# ---------------------------------------------------------------------------
# Thesis runs (Phase 27 — market segment / thesis-to-universe discovery)
# ---------------------------------------------------------------------------


@router.get(
    "/supported-themes",
    response_model=SupportedThemesResponse,
    summary="List thesis themes and sector aliases the parser supports (admin)",
    description=(
        "ADMIN/INTERNAL ONLY. Returns the research themes a thesis can match, "
        "the sector aliases that resolve to each canonical sector, and "
        "recommendation-free example queries the admin UI offers as starting "
        "points. Derived from the parser and the curated registry, so it can "
        "never advertise a theme that yields an empty universe. Thesis "
        "discovery runs against a bounded curated universe bootstrap — not a "
        "full-market scan. " + _INTERNAL
    ),
)
async def list_supported_themes() -> SupportedThemesResponse:
    return SupportedThemesResponse.model_validate(svc.get_supported_themes())


@router.post(
    "/thesis-runs",
    response_model=DiscoveryRunRead,
    status_code=status.HTTP_201_CREATED,
    summary="Start an internal thesis-to-universe discovery scan (admin only)",
    description=(
        "ADMIN/INTERNAL ONLY. Parses a natural-language market segment / theme / "
        "region thesis, builds a BOUNDED real-company universe from a curated "
        "reference registry, and scans it through the existing discovery "
        "pipeline. Returns the run_id IMMEDIATELY (status='pending') — the scan "
        "runs in the background; poll GET /runs/{run_id} and "
        "GET /runs/{run_id}/candidates. Rejects (422) a vague thesis that needs "
        "narrowing or one that matches no company BEFORE any work is scheduled. "
        "Produces internal research candidates only — never investment advice, "
        "never a recommendation, never a public publish action. " + _INTERNAL
    ),
)
async def create_thesis_discovery_run(
    payload: ThesisDiscoveryRunCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> DiscoveryRunRead:
    try:
        run = await svc.create_pending_thesis_run(db, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    background_tasks.add_task(svc.process_discovery_run_task, str(run.id))

    dto = DiscoveryRunRead.model_validate(run)
    dto.message = (
        "Thesis discovery run started. A bounded universe was generated and is "
        "being scanned in the background — poll run status for progress."
    )
    return dto


@router.get(
    "/thesis-runs/{run_id}",
    response_model=DiscoveryRunRead,
    summary="Get a thesis discovery run incl. parsed thesis + universe (admin)",
)
async def get_thesis_discovery_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DiscoveryRunRead:
    run = await svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Discovery run {run_id} not found",
        )
    return DiscoveryRunRead.model_validate(run)


@router.get(
    "/runs/{run_id}",
    response_model=DiscoveryRunRead,
    summary="Get a discovery run (admin/internal only)",
)
async def get_discovery_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DiscoveryRunRead:
    run = await svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Discovery run {run_id} not found",
        )
    return DiscoveryRunRead.model_validate(run)


@router.get(
    "/runs/{run_id}/summary",
    response_model=DiscoveryRunSummary,
    summary="Get an aggregate summary of a discovery run (admin/internal only)",
)
async def get_discovery_run_summary(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DiscoveryRunSummary:
    run = await svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Discovery run {run_id} not found",
        )
    return await svc.summarize_run(db, run)


@router.get(
    "/runs/{run_id}/candidates",
    response_model=DiscoveryCandidateListResponse,
    summary="List internal research candidates for a run (admin/internal only)",
)
async def list_discovery_candidates(
    run_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="candidate_score"),
    sector: str | None = Query(default=None),
    grade: str | None = Query(default=None),
    momentum_label: str | None = Query(default=None),
    catalyst_coverage_status: str | None = Query(default=None),
    source_quality: str | None = Query(default=None),
    score_min: float | None = Query(default=None, ge=0, le=100),
    missing_info_max: int | None = Query(default=None, ge=0),
    has_press_releases: bool | None = Query(default=None),
    has_news: bool | None = Query(default=None),
    ticker: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> DiscoveryCandidateListResponse:
    run = await svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Discovery run {run_id} not found",
        )
    candidates, total = await svc.list_candidates(
        db,
        run_id,
        limit=limit,
        offset=offset,
        sort=sort,
        sector=sector,
        grade=grade,
        momentum_label=momentum_label,
        catalyst_coverage_status=catalyst_coverage_status,
        source_quality=source_quality,
        score_min=score_min,
        missing_info_max=missing_info_max,
        has_press_releases=has_press_releases,
        has_news=has_news,
        ticker=ticker,
    )
    return DiscoveryCandidateListResponse(
        candidates=[DiscoveryCandidateRead.model_validate(c) for c in candidates],
        total=total,
        run_id=run_id,
    )


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


@router.get(
    "/candidates/{candidate_id}",
    response_model=DiscoveryCandidateDetail,
    summary="Get a discovery candidate detail (admin/internal only)",
)
async def get_discovery_candidate(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DiscoveryCandidateDetail:
    candidate = await svc.get_candidate(db, candidate_id)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Discovery candidate {candidate_id} not found",
        )
    return DiscoveryCandidateDetail.model_validate(candidate)


@router.post(
    "/candidates/{candidate_id}/run-analysis",
    response_model=RunCandidateAnalysisResponse,
    summary="Run the full analysis workflow for a candidate (admin/internal only)",
    description=(
        "ADMIN/INTERNAL ONLY. Promotes an internal research candidate to the "
        "existing company-analysis workflow and links the produced draft report "
        "to the candidate. Produces an internal admin draft only — never "
        "investment advice, never a recommendation. " + _INTERNAL
    ),
)
async def run_candidate_analysis(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RunCandidateAnalysisResponse:
    try:
        result = await svc.run_candidate_analysis(db, candidate_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except Exception as exc:  # workflow execution failure
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Candidate analysis failed: {exc}",
        ) from exc

    return RunCandidateAnalysisResponse(
        candidate_id=result["candidate_id"],
        ticker=result["ticker"],
        status=result["status"],
        analysis_report_id=result["analysis_report_id"],
        agent_run_id=result["agent_run_id"],
        provider_name=result["provider_name"],
        message=(
            f"Full analysis workflow completed for {result['ticker']}. "
            "Internal admin draft only — human review required."
        ),
    )
