"""
Phase 22: Judge + Backtesting Framework — API endpoints.

Admin/dev-only endpoints.  No public-facing routes.
No investment recommendations, price targets, fair values, or upside
percentages are produced.  Human review is always required.

All responses include an INTERNAL_DISCLAIMER.

Endpoints:
  POST   /api/v1/backtesting/runs
  GET    /api/v1/backtesting/runs
  GET    /api/v1/backtesting/runs/{run_id}
  POST   /api/v1/backtesting/runs/{run_id}/add-report/{report_id}
  POST   /api/v1/backtesting/runs/{run_id}/evaluate
  GET    /api/v1/backtesting/runs/{run_id}/results
  GET    /api/v1/backtesting/runs/{run_id}/summary
  POST   /api/v1/backtesting/reports/{report_id}/judge
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.report import Report
from app.schemas.backtesting import (
    BacktestResultListResponse,
    BacktestResultResponse,
    BacktestRunCreate,
    BacktestRunListResponse,
    BacktestRunResponse,
    BacktestRunSummary,
    JudgeReportResponse,
)
from app.services.backtesting_service import BacktestingService
from app.services.research_judge_service import ResearchJudgeService

router = APIRouter(prefix="/backtesting", tags=["backtesting"])

_svc = BacktestingService()
_judge = ResearchJudgeService()

_NOT_INVESTMENT_ADVICE = (
    "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. "
    "NOT A PUBLIC RECOMMENDATION. HISTORICAL EVALUATION ONLY. "
    "No BUY/SELL/HOLD/WATCH recommendations are produced."
)


# ---------------------------------------------------------------------------
# Backtest runs
# ---------------------------------------------------------------------------


@router.post(
    "/runs",
    response_model=BacktestRunResponse,
    status_code=201,
    summary="Create a new backtest run",
    description=(
        "ADMIN/DEV ONLY. Creates a new internal backtest run for evaluating "
        "research quality over a historical period. "
        "NOT investment advice. No BUY/SELL/HOLD/WATCH recommendations are produced. "
        "No price targets, fair values, or upside percentages are produced."
    ),
)
async def create_backtest_run(
    payload: BacktestRunCreate,
    db: AsyncSession = Depends(get_db),
) -> BacktestRunResponse:
    try:
        run = await _svc.create_backtest_run(db, payload)
        return BacktestRunResponse.model_validate(run)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/runs",
    response_model=BacktestRunListResponse,
    summary="List backtest runs",
    description=(
        "ADMIN/DEV ONLY. List internal backtest runs. "
        "NOT investment advice. Historical evaluation only."
    ),
)
async def list_backtest_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> BacktestRunListResponse:
    runs = await _svc.list_backtest_runs(db, limit=limit, offset=offset)
    return BacktestRunListResponse(
        runs=[BacktestRunResponse.model_validate(r) for r in runs],
        total=len(runs),
    )


@router.get(
    "/runs/{run_id}",
    response_model=BacktestRunResponse,
    summary="Get a backtest run",
    description="ADMIN/DEV ONLY. Get details of a specific backtest run.",
)
async def get_backtest_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> BacktestRunResponse:
    run = await _svc.get_backtest_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Backtest run {run_id} not found.")
    return BacktestRunResponse.model_validate(run)


# ---------------------------------------------------------------------------
# Report association
# ---------------------------------------------------------------------------


@router.post(
    "/runs/{run_id}/add-report/{report_id}",
    response_model=BacktestResultResponse,
    status_code=201,
    summary="Add a report to a backtest run",
    description=(
        "ADMIN/DEV ONLY. Associate an internal report with a backtest run. "
        "Creates a pending backtest result record. "
        "NOT investment advice. Historical evaluation only."
    ),
)
async def add_report_to_backtest_run(
    run_id: uuid.UUID,
    report_id: uuid.UUID,
    horizon_days: int | None = Query(default=None, ge=1, le=3650),
    benchmark_symbol: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> BacktestResultResponse:
    try:
        result = await _svc.add_report_to_backtest(
            db,
            run_id=run_id,
            report_id=report_id,
            horizon_days=horizon_days,
            benchmark_symbol=benchmark_symbol,
        )
        return BacktestResultResponse.model_validate(result)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@router.post(
    "/runs/{run_id}/evaluate",
    response_model=BacktestRunResponse,
    summary="Evaluate all reports in a backtest run",
    description=(
        "ADMIN/DEV ONLY. Runs historical outcome evaluation and judge scoring "
        "for all reports in a backtest run. "
        "Uses mock provider by default — no live EODHD or market API calls. "
        "NOT investment advice. NOT a public recommendation. "
        "Results are internal quality assessments only."
    ),
)
async def evaluate_backtest_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> BacktestRunResponse:
    try:
        run = await _svc.evaluate_backtest_run(db, run_id)
        return BacktestRunResponse.model_validate(run)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@router.get(
    "/runs/{run_id}/results",
    response_model=BacktestResultListResponse,
    summary="List results for a backtest run",
    description=(
        "ADMIN/DEV ONLY. List all backtest result records for a run. "
        "Includes historical outcome and judge evaluation data. "
        "NOT investment advice. Historical evaluation only."
    ),
)
async def list_backtest_results(
    run_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> BacktestResultListResponse:
    # Verify run exists
    run = await _svc.get_backtest_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Backtest run {run_id} not found.")
    results = await _svc.list_backtest_results(db, run_id=run_id, limit=limit, offset=offset)
    return BacktestResultListResponse(
        results=[BacktestResultResponse.model_validate(r) for r in results],
        total=len(results),
    )


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@router.get(
    "/runs/{run_id}/summary",
    response_model=BacktestRunSummary,
    summary="Get summary for a backtest run",
    description=(
        "ADMIN/DEV ONLY. Get an aggregate summary of a backtest run including "
        "average judge score and status breakdown. "
        "NOT investment advice. Historical evaluation only."
    ),
)
async def get_backtest_run_summary(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> BacktestRunSummary:
    try:
        return await _svc.summarize_backtest_run(db, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Standalone judge endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/reports/{report_id}/judge",
    response_model=JudgeReportResponse,
    summary="Run judge evaluation on a single report",
    description=(
        "ADMIN/DEV ONLY. Run the research quality judge on a stored report. "
        "Returns internal quality scores and calibration notes. "
        "NOT investment advice. NOT a public recommendation. "
        "No BUY/SELL/HOLD/WATCH, price targets, fair values, or upside percentages "
        "are produced."
    ),
)
async def judge_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> JudgeReportResponse:
    # Fetch report from DB
    rep_query = await db.execute(select(Report).where(Report.id == report_id))
    report_obj = rep_query.scalar_one_or_none()
    if report_obj is None:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found.")

    report_data: dict[str, Any] = {
        "content_markdown": report_obj.content_markdown,
        "summary": report_obj.summary,
        "title": report_obj.title,
        "source_summary_json": getattr(report_obj, "source_summary_json", None),
        "safety_validation_json": getattr(report_obj, "safety_validation_json", None),
        "schema_validation_json": getattr(report_obj, "schema_validation_json", None),
    }

    evaluation = _judge.evaluate_report(
        report_id=report_id,
        report_data=report_data,
    )
    return JudgeReportResponse(report_id=report_id, evaluation=evaluation)
