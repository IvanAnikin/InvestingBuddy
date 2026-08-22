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
  GET    /api/v1/market-discovery/candidates/{candidate_id}/analysis-job
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
    DiscoveryCouncilReviewResponse,
    DiscoveryRunCreate,
    DiscoveryRunListResponse,
    DiscoveryRunRead,
    DiscoveryRunSummary,
    ParseThesisRequest,
    ParseThesisResponse,
    RunCandidateAnalysisResponse,
    SupportedFiltersResponse,
    SupportedThemesResponse,
    ThesisDiscoveryRunCreate,
)
from app.services import market_discovery_service as svc
from app.services.market_discovery_service import DiscoveryCouncilDisabledError
from app.services.market_thesis_parser import parse_thesis

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


@router.get(
    "/supported-filters",
    response_model=SupportedFiltersResponse,
    summary="Canonical Region/Country/Sector/Industry selector options (admin)",
    description=(
        "ADMIN/INTERNAL ONLY. Returns the canonical, controlled selector values "
        "for the thesis form's Region, Country, Sector and Industry fields. The "
        "admin UI renders these as searchable selects whose allowed values come "
        "from here — they are not arbitrary free text, and the backend rejects "
        "anything outside them. Sector values are canonical (aliases resolve to "
        "them internally). " + _INTERNAL
    ),
)
async def list_supported_filters() -> SupportedFiltersResponse:
    return SupportedFiltersResponse.model_validate(svc.get_supported_filters())


@router.post(
    "/parse-thesis",
    response_model=ParseThesisResponse,
    summary="Parse a thesis for selector auto-fill — does NOT create a run (admin)",
    description=(
        "ADMIN/INTERNAL ONLY. Parses a natural-language thesis and returns the "
        "canonical single-value Region / Country / Sector / Industry it detects, "
        "so the admin UI can auto-fill the selectors as the admin types. This "
        "endpoint does NOT create a run and does NOT touch the database — it is a "
        "pure preview/autofill helper. It never produces an investment "
        "recommendation, price target, or BUY/SELL/HOLD/WATCH label. " + _INTERNAL
    ),
)
async def parse_thesis_preview(payload: ParseThesisRequest) -> ParseThesisResponse:
    parsed = parse_thesis(payload.thesis)
    return ParseThesisResponse(
        themes=parsed.themes,
        region=parsed.region,
        country=parsed.country,
        sector=parsed.sector,
        industry=parsed.industry,
        theme=parsed.theme,
        confidence=parsed.confidence,
        extraction_source=parsed.extraction_source,
        needs_narrowing=parsed.needs_narrowing,
        warnings=parsed.warnings,
    )


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
    detail = DiscoveryCandidateDetail.model_validate(candidate)
    # Phase 28A.1 — attach compact metadata about the linked report so the UI can
    # honestly label "View Latest Final Report" vs "View Legacy Draft".
    if candidate.analysis_report_id is not None:
        report = await svc.get_report_for_candidate(db, candidate.analysis_report_id)
        detail.latest_report = svc.report_link_summary_from_report(report)
    return detail


_JOB_MESSAGES = {
    "pending": (
        "Full analysis queued. Processing in the background — poll "
        "GET /candidates/{candidate_id}/analysis-job for progress."
    ),
    "running": "Full analysis is running. Poll for progress.",
    "failed": "Full analysis failed. See 'error'.",
}


def _analysis_job_message(envelope: dict, ticker: str) -> str:
    """Human-facing message for one analysis-job state (never a recommendation)."""
    status = str(envelope.get("status") or "pending")
    if status in _JOB_MESSAGES:
        base = _JOB_MESSAGES[status]
        if status == "failed" and envelope.get("error"):
            base = f"Full analysis failed ({envelope['error']})."
        return f"{base} Internal admin draft only — human review required."

    report = envelope.get("report") or {}
    if report.get("report_kind") == "final":
        llm_note = (
            "LLM council analysis draft."
            if report.get("llm_used")
            else "Internal analysis draft (LLM council not used)."
        )
    else:
        llm_note = "Legacy deterministic draft (predates LLM council generation)."
    message = (
        f"Full analysis completed for {ticker}. {llm_note} "
        "Internal admin draft only — human review required."
    )
    if envelope.get("warnings"):
        message += " Note: final-report generation degraded to the deterministic draft."
    return message


@router.post(
    "/candidates/{candidate_id}/run-analysis",
    response_model=RunCandidateAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an async full-analysis job for a candidate (admin/internal only)",
    description=(
        "ADMIN/INTERNAL ONLY. Starts the full company-analysis pipeline "
        "(workflow → primary-document ingestion → LLM council → final report) "
        "ASYNCHRONOUSLY and returns IMMEDIATELY with a job envelope "
        "(status='pending'). It does NOT block until the council finishes — "
        "that repeatedly exceeded the ~230s gateway ceiling and surfaced as a "
        "browser HTTP 504 even though the backend completed successfully. Poll "
        "GET /candidates/{candidate_id}/analysis-job for progress and the "
        "linked final report. IDEMPOTENT: while a job for this candidate is "
        "pending/running the current state is returned and NO second (expensive) "
        "council run is started; a completed job is returned as-is unless "
        "force=true. Produces an internal admin draft only — never investment "
        "advice, never a recommendation. " + _INTERNAL
    ),
)
async def run_candidate_analysis(
    candidate_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    force: bool = Query(
        default=False,
        description="Re-run even if a completed analysis already exists.",
    ),
    db: AsyncSession = Depends(get_db),
) -> RunCandidateAnalysisResponse:
    candidate = await svc.get_candidate(db, candidate_id)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Discovery candidate {candidate_id} not found",
        )

    envelope, scheduled = await svc.start_candidate_analysis(
        db, candidate, force=force
    )
    if scheduled:
        # Run the (already-committed pending) job in the background using its own
        # DB session — never the request-scoped one. Only the primitive id is
        # handed to the task.
        background_tasks.add_task(
            svc.process_candidate_analysis_task, str(candidate_id)
        )
        message = (
            "Full analysis started. Processing in the background — poll "
            "GET /candidates/{id}/analysis-job for progress. Internal admin "
            "draft only — human review required."
        )
    elif envelope.get("status") in {"pending", "running"}:
        message = (
            "Full analysis already in progress for this candidate — no second "
            "job was started. Poll GET /candidates/{id}/analysis-job."
        )
    else:
        message = _analysis_job_message(envelope, candidate.ticker)

    return RunCandidateAnalysisResponse.from_job_envelope(
        candidate_id=candidate_id,
        ticker=candidate.ticker,
        envelope=envelope,
        message=message,
    )


@router.get(
    "/candidates/{candidate_id}/analysis-job",
    response_model=RunCandidateAnalysisResponse,
    summary="Get the async full-analysis job status for a candidate (admin only)",
    description=(
        "ADMIN/INTERNAL ONLY. Returns the current full-analysis job state for "
        "ONE candidate: pending/running while a background job is in flight, "
        "the completed job (with the FINAL report id produced for THIS "
        "candidate) when done, or a failed status with a safe reason. Scoped "
        "strictly to the given candidate — never a global-latest or "
        "cross-candidate lookup. 404 when no job has ever run for it. "
        + _INTERNAL
    ),
)
async def get_candidate_analysis_job(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RunCandidateAnalysisResponse:
    candidate = await svc.get_candidate(db, candidate_id)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Discovery candidate {candidate_id} not found",
        )
    envelope = svc.get_analysis_job_envelope(candidate)
    if envelope is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No full-analysis job has been run for this candidate.",
        )
    return RunCandidateAnalysisResponse.from_job_envelope(
        candidate_id=candidate_id,
        ticker=candidate.ticker,
        envelope=envelope,
        message=_analysis_job_message(envelope, candidate.ticker),
    )


# ---------------------------------------------------------------------------
# Discovery council review (Phase 28B — run-level LLM council)
# ---------------------------------------------------------------------------


@router.post(
    "/runs/{run_id}/council-review",
    response_model=DiscoveryCouncilReviewResponse,
    summary="Start an async run-level LLM discovery council job (admin only)",
    description=(
        "ADMIN/INTERNAL ONLY. Starts the run-level LLM discovery council over one "
        "discovery run's whole candidate set ASYNCHRONOUSLY and returns "
        "IMMEDIATELY with a job status (pending) — it does NOT block until every "
        "LLM agent finishes. Poll GET /runs/{run_id}/council-review for progress "
        "and the completed review. The council decides internal research PRIORITY "
        "only (research_next / monitor_for_evidence / insufficient_data / "
        "reject_for_now) — never investment advice, never a recommendation, never "
        "a price target, fair value, or upside/downside. If a job is already "
        "running the current status is returned (no second job starts); if a "
        "completed review exists it is returned unless force=true. Disabled by "
        "default: returns 409 when LLM_COUNCIL_ENABLED or "
        "LLM_DISCOVERY_COUNCIL_ENABLED is off (or no provider is available) and no "
        "prior review exists — no LLM call, no fake result in production. Requires "
        "a terminal run or at least one candidate. " + _INTERNAL
    ),
)
async def create_discovery_council_review(
    run_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    force: bool = Query(
        default=False,
        description="Re-run even if a completed review already exists.",
    ),
    db: AsyncSession = Depends(get_db),
) -> DiscoveryCouncilReviewResponse:
    run = await svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Discovery run {run_id} not found",
        )
    try:
        envelope, scheduled = await svc.start_discovery_council_review(
            db, run, force=force
        )
    except DiscoveryCouncilDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if scheduled:
        # Run the (already-committed pending) job in the background using its own
        # DB session — never the request-scoped one. Only the primitive run_id is
        # handed to the task.
        background_tasks.add_task(svc.process_discovery_council_task, str(run_id))
        message = "Discovery council review started."
    elif envelope.get("status") in {"pending", "running"}:
        message = "Discovery council review already in progress."
    else:
        message = "Returning the existing discovery council review."
    return DiscoveryCouncilReviewResponse.from_envelope(
        run_id, envelope, message=message
    )


@router.get(
    "/runs/{run_id}/council-review",
    response_model=DiscoveryCouncilReviewResponse,
    summary="Get the async LLM discovery council job status / review (admin only)",
    description=(
        "ADMIN/INTERNAL ONLY. Returns the current run-level discovery council job "
        "state: pending/running while a background job is in flight, the completed "
        "review when done, or a failed status with a safe reason. A completed "
        "review stays readable even after the council flags are later turned off. "
        "When no job has ever run and the council is disabled, a 'disabled' "
        "response is returned; when no job has run and the council is enabled, "
        "404. " + _INTERNAL
    ),
)
async def get_discovery_council_review(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DiscoveryCouncilReviewResponse:
    run = await svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Discovery run {run_id} not found",
        )
    envelope = svc.get_council_envelope(run)
    if envelope is None:
        # No council job has ever run for this run. If the council is disabled,
        # surface a clear disabled state for the polling UI; otherwise 404.
        if not svc.discovery_council_enabled():
            return DiscoveryCouncilReviewResponse.disabled_response(run_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No discovery council review found for this run.",
        )
    return DiscoveryCouncilReviewResponse.from_envelope(run_id, envelope)
