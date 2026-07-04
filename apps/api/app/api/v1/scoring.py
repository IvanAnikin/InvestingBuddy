"""
Phase 15: Scoring + Valuation Framework — API endpoints.

All endpoints are admin/dev-only internal endpoints.
No public investment advice, recommendations, price targets, or fair values
are produced by any endpoint in this module.

Endpoints:
  POST  /api/v1/scoring/candidates/{candidate_id}
  GET   /api/v1/scoring/candidates/{candidate_id}
  POST  /api/v1/scoring/runs/{run_id}
  GET   /api/v1/scoring/runs/{run_id}/ranked-candidates
  POST  /api/v1/scoring/companies/{company_id}      (optional, company analysis)
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.company import Company
from app.models.scorecard import Scorecard
from app.schemas.scoring import (
    RankedCandidateItem,
    RankedCandidateList,
    ScoreCandidateResponse,
    ScorecardRead,
    ScoreRunResponse,
    ValuationReadinessRead,
)
from app.services.scoring_engine import ScoringEngine, ValuationReadinessService
from app.services.scoring_service import ScoringService

router = APIRouter(prefix="/scoring", tags=["scoring"])

_scoring_service = ScoringService()
_vr_service = ValuationReadinessService()
_engine = ScoringEngine()

_DISCLAIMER = (
    "INTERNAL SCORE ONLY. Not investment advice. "
    "Not a public recommendation. Human review required before any action."
)


# ---------------------------------------------------------------------------
# Candidate scoring
# ---------------------------------------------------------------------------


@router.post(
    "/candidates/{candidate_id}",
    response_model=ScoreCandidateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Score a screening candidate (admin/dev only)",
)
async def score_candidate(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ScoreCandidateResponse:
    """
    Score a screening candidate and persist the scorecard.

    Produces a multi-dimension internal research attractiveness score (0–100).

    CONSTRAINTS:
    - No investment recommendation (BUY/SELL/HOLD/WATCH) is produced.
    - No price target, fair value, or upside estimate is produced.
    - internal_status is a research queue label — not investment advice.
    - Human review required before any further action on high-priority items.

    Admin/dev only — not a public endpoint.
    """
    try:
        scorecard = await _scoring_service.score_candidate(db, candidate_id)
        await db.commit()
        await db.refresh(scorecard)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    explain = await _scoring_service.explain_candidate_score(db, candidate_id)

    # Build valuation readiness from available data
    available_data = list(
        (scorecard.scores_json or {})
        .get("valuation_readiness_score", {})
        .get("evidence_used", [])
    )
    vr = _vr_service.check(
        available_data=available_data,
        source_tier=scorecard.source_quality_summary_json.get("source_tier", "T6_model_estimate")
        if scorecard.source_quality_summary_json
        else "T6_model_estimate",
        is_mock=scorecard.source_quality_summary_json.get("is_mock", True)
        if scorecard.source_quality_summary_json
        else True,
    )

    return ScoreCandidateResponse(
        candidate_id=candidate_id,
        scorecard_id=scorecard.id,
        overall_score=scorecard.overall_score,
        internal_status=scorecard.internal_status,
        scores=scorecard.scores_json or {},
        warnings=scorecard.warnings_json or [],
        missing_data=scorecard.missing_data_json or [],
        source_quality_summary=scorecard.source_quality_summary_json or {},
        reasoning=explain.get("reasoning", _DISCLAIMER),
        next_research_steps=explain.get("next_research_steps", []),
        valuation_readiness=ValuationReadinessRead(**vr.to_dict()),
        provider_name=scorecard.provider_name,
        created_at=scorecard.created_at,
        disclaimer=_DISCLAIMER,
    )


@router.get(
    "/candidates/{candidate_id}",
    response_model=ScorecardRead,
    summary="Get scorecard for a candidate (admin/dev only)",
)
async def get_candidate_scorecard(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ScorecardRead:
    """
    Retrieve the most recent scorecard for a screening candidate.

    Returns 404 if no scorecard exists yet.
    Admin/dev only — not a public endpoint.
    Not investment advice.
    """
    scorecard = await _scoring_service.get_candidate_scorecard(db, candidate_id)
    if scorecard is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No scorecard found for candidate {candidate_id}. "
            "Run POST /scoring/candidates/{candidate_id} to score first.",
        )
    return _scorecard_to_read(scorecard)


# ---------------------------------------------------------------------------
# Run scoring
# ---------------------------------------------------------------------------


@router.post(
    "/runs/{run_id}",
    response_model=ScoreRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Score all candidates in a screening run (admin/dev only)",
)
async def score_screening_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ScoreRunResponse:
    """
    Score all candidates in a screening run and persist scorecards.

    Returns a summary of scoring results.

    CONSTRAINTS:
    - No investment recommendation is produced.
    - No price targets, fair values, or upside estimates are produced.
    - Candidates are scored purely on research attractiveness.

    Admin/dev only — not a public endpoint.
    """
    try:
        scorecards = await _scoring_service.score_screening_run(db, run_id)
        await db.commit()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    # Build summary
    status_counts: dict[str, int] = {}
    for sc in scorecards:
        status_counts[sc.internal_status] = status_counts.get(sc.internal_status, 0) + 1

    scores_list = [sc.overall_score for sc in scorecards]
    avg_score = int(sum(scores_list) / len(scores_list)) if scores_list else 0
    max_score = max(scores_list) if scores_list else 0

    return ScoreRunResponse(
        run_id=run_id,
        candidates_scored=len(scorecards),
        scorecards_created=len(scorecards),
        score_summary={
            "status_counts": status_counts,
            "average_overall_score": avg_score,
            "max_overall_score": max_score,
            "note": (
                "Internal research attractiveness scores only. "
                "No investment recommendation produced. "
                "No price targets. No fair values. Human review required."
            ),
        },
        disclaimer=_DISCLAIMER,
    )


@router.get(
    "/runs/{run_id}/ranked-candidates",
    response_model=RankedCandidateList,
    summary="List candidates ranked by research attractiveness score (admin/dev only)",
)
async def list_ranked_candidates(
    run_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> RankedCandidateList:
    """
    List candidates from a screening run ranked by internal research attractiveness score.

    Candidates without scorecards appear at the bottom.
    Ranking is NOT a public investment recommendation.
    Score is NOT investment advice.
    Human admin review is required before any action.

    Admin/dev only — not a public endpoint.
    """
    try:
        items, total = await _scoring_service.list_ranked_candidates(
            db, run_id=run_id, limit=limit, offset=offset
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return RankedCandidateList(
        run_id=run_id,
        items=[RankedCandidateItem(**item) for item in items],
        total=total,
        disclaimer=_DISCLAIMER,
        note=(
            "Candidates are ranked by internal research attractiveness score. "
            "Ranking is NOT a public investment recommendation. "
            "Score is NOT investment advice. Human admin review is required."
        ),
    )


# ---------------------------------------------------------------------------
# Company analysis scoring
# ---------------------------------------------------------------------------


@router.post(
    "/companies/{company_id}",
    response_model=ScorecardRead,
    status_code=status.HTTP_201_CREATED,
    summary="Score a company from analysis workflow (admin/dev only)",
)
async def score_company(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ScorecardRead:
    """
    Score a company using the most recent available analysis data.

    This endpoint is used to manually trigger scoring for a company
    that has already been through the company-analysis workflow.

    The scoring node in the workflow (score_research_attractiveness) runs
    this automatically after the Analysis Council phase.

    CONSTRAINTS:
    - No investment recommendation is produced.
    - No price target, fair value, or upside is produced.
    - internal_status is a research queue label only.

    Admin/dev only — not a public endpoint.
    """
    from sqlalchemy import select

    # Verify company exists
    company_result = await db.execute(
        select(Company).where(Company.id == company_id)
    )
    company = company_result.scalar_one_or_none()
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found.",
        )

    # Get most recent scorecard for this company if it exists
    existing_sc_result = await db.execute(
        select(Scorecard)
        .where(
            Scorecard.company_id == company_id,
            Scorecard.score_type == "company_analysis_scoring",
        )
        .order_by(Scorecard.created_at.desc())
        .limit(1)
    )
    existing_sc = existing_sc_result.scalar_one_or_none()

    if existing_sc:
        return _scorecard_to_read(existing_sc)

    # No existing scorecard — create a minimal one from company data
    company_snapshot = {
        "company_identity": {
            "ticker": company.ticker,
            "exchange": company.exchange,
            "legal_name": company.name,
            "country_domicile": company.country,
        },
        "provider_metadata": {
            "provider_name": "mock",
            "source_tier": "T6_model_estimate",
            "is_mock": True,
        },
        "profile": {"sector": company.sector or ""},
        "is_mock": True,
    }

    try:
        sc = await _scoring_service.score_company_analysis(
            db=db,
            company_id=company_id,
            report_id=None,
            company_snapshot=company_snapshot,
        )
        await db.commit()
        await db.refresh(sc)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Scoring failed: {exc}",
        ) from exc

    return _scorecard_to_read(sc)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _scorecard_to_read(sc: Scorecard) -> ScorecardRead:
    return ScorecardRead(
        id=sc.id,
        score_type=sc.score_type,
        company_id=sc.company_id,
        screening_candidate_id=sc.screening_candidate_id,
        report_id=sc.report_id,
        overall_score=sc.overall_score,
        internal_status=sc.internal_status,
        scores=sc.scores_json,
        warnings=sc.warnings_json,
        missing_data=sc.missing_data_json,
        source_quality_summary=sc.source_quality_summary_json,
        provider_name=sc.provider_name,
        created_at=sc.created_at,
        disclaimer=_DISCLAIMER,
    )
