"""
Phase 22: Backtesting Service — orchestrates backtest runs and result storage.

Coordinates backtest run creation, report addition, historical outcome
fetching, and judge evaluation.

IMPORTANT CONSTRAINTS:
  - No BUY/SELL/HOLD/WATCH public recommendations are produced.
  - No price targets, fair values, or upside percentages are produced.
  - CI/tests use MockHistoricalOutcomeProvider — no live EODHD/Stooq calls.
  - All evaluations are internal historical quality assessments only.
  - Human review is required before any action on results.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.historical_outcome_provider import (
    HistoricalOutcomeProvider,
    get_historical_outcome_provider,
)
from app.models.backtest import BacktestResult, BacktestRun
from app.models.report import Report
from app.schemas.backtesting import (
    INTERNAL_DISCLAIMER,
    BacktestRunCreate,
    BacktestRunSummary,
    JudgeEvaluation,
)
from app.services.research_judge_service import ResearchJudgeService

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BacktestingService:
    """Orchestrates internal backtesting runs and research quality evaluation.

    All methods are async and require an AsyncSession.
    No public recommendations are produced at any step.
    CI-safe: defaults to mock provider.
    """

    def __init__(
        self,
        provider: HistoricalOutcomeProvider | None = None,
        judge: ResearchJudgeService | None = None,
    ) -> None:
        self._provider = provider or get_historical_outcome_provider("mock")
        self._judge = judge or ResearchJudgeService()

    # ------------------------------------------------------------------
    # Run management
    # ------------------------------------------------------------------

    async def create_backtest_run(
        self,
        db: AsyncSession,
        payload: BacktestRunCreate,
    ) -> BacktestRun:
        """Create a new backtest run record."""
        run = BacktestRun(
            id=uuid.uuid4(),
            name=payload.name,
            description=payload.description,
            status="pending",
            horizon_days=payload.horizon_days,
            benchmark_symbol=payload.benchmark_symbol,
            provider_name=payload.provider_name or self._provider.provider_name,
            parameters_json=payload.parameters or {},
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        logger.info("Created backtest run %s: %r", run.id, run.name)
        return run

    async def list_backtest_runs(
        self,
        db: AsyncSession,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BacktestRun]:
        """List backtest runs ordered by created_at desc."""
        result = await db.execute(
            select(BacktestRun)
            .order_by(BacktestRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_backtest_run(
        self,
        db: AsyncSession,
        run_id: uuid.UUID,
    ) -> BacktestRun | None:
        """Fetch a single backtest run by ID."""
        result = await db.execute(
            select(BacktestRun).where(BacktestRun.id == run_id)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Report association
    # ------------------------------------------------------------------

    async def add_report_to_backtest(
        self,
        db: AsyncSession,
        run_id: uuid.UUID,
        report_id: uuid.UUID,
        horizon_days: int | None = None,
        benchmark_symbol: str | None = None,
    ) -> BacktestResult:
        """Associate a report with a backtest run, creating a result record."""
        run = await self.get_backtest_run(db, run_id)
        if run is None:
            raise ValueError(f"Backtest run {run_id} not found.")

        # Fetch report metadata
        report_result = await db.execute(
            select(Report).where(Report.id == report_id)
        )
        report = report_result.scalar_one_or_none()

        ticker: str | None = None
        exchange: str | None = None
        company_id: uuid.UUID | None = None
        scorecard_id: uuid.UUID | None = None

        if report is not None:
            scorecard_id = getattr(report, "scorecard_id", None)

        effective_horizon = horizon_days or run.horizon_days or 90
        effective_benchmark = benchmark_symbol or run.benchmark_symbol

        result = BacktestResult(
            id=uuid.uuid4(),
            backtest_run_id=run_id,
            report_id=report_id,
            company_id=company_id,
            scorecard_id=scorecard_id,
            ticker=ticker,
            exchange=exchange,
            horizon_days=effective_horizon,
            benchmark_symbol=effective_benchmark,
            status="pending",
        )
        db.add(result)
        await db.commit()
        await db.refresh(result)
        logger.info(
            "Added report %s to backtest run %s → result %s",
            report_id, run_id, result.id,
        )
        return result

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    async def evaluate_report_outcome(
        self,
        db: AsyncSession,
        result_id: uuid.UUID,
        evaluation_start_date: date | None = None,
        evaluation_end_date: date | None = None,
    ) -> BacktestResult:
        """Evaluate historical outcome for a single backtest result."""
        res_query = await db.execute(
            select(BacktestResult).where(BacktestResult.id == result_id)
        )
        bt_result = res_query.scalar_one_or_none()
        if bt_result is None:
            raise ValueError(f"BacktestResult {result_id} not found.")

        start_date = evaluation_start_date or date.today() - timedelta(
            days=bt_result.horizon_days or 90
        )
        end_date = evaluation_end_date or date.today()

        bt_result.evaluation_start_date = start_date
        bt_result.evaluation_end_date = end_date

        # Fetch report data for judge
        report_data: dict[str, Any] = {}
        report_obj: Report | None = None
        if bt_result.report_id:
            rep_query = await db.execute(
                select(Report).where(Report.id == bt_result.report_id)
            )
            report_obj = rep_query.scalar_one_or_none()
            if report_obj:
                report_data = {
                    "content_markdown": report_obj.content_markdown,
                    "summary": report_obj.summary,
                    "title": report_obj.title,
                    "source_summary_json": getattr(report_obj, "source_summary_json", None),
                    "safety_validation_json": getattr(report_obj, "safety_validation_json", None),
                    "schema_validation_json": getattr(report_obj, "schema_validation_json", None),
                }

        # Fetch historical outcome (mock by default)
        outcome: dict[str, Any] | None = None
        ticker = bt_result.ticker or (report_obj.title[:10] if report_obj else "UNKNOWN")
        if ticker:
            try:
                outcome_obj = await self._provider.get_outcome(
                    ticker=ticker,
                    exchange=bt_result.exchange,
                    start_date=start_date,
                    end_date=end_date,
                    benchmark_symbol=bt_result.benchmark_symbol,
                )
                outcome = outcome_obj.model_dump()
            except Exception as exc:
                logger.warning("Historical outcome fetch failed for %s: %s", ticker, exc)
                outcome = {
                    "data_available": False,
                    "warnings": [f"Provider error: {exc}"],
                }

        bt_result.outcome_json = outcome

        # Run judge evaluation
        judge_eval: JudgeEvaluation = self._judge.evaluate_report(
            report_id=bt_result.report_id,
            report_data=report_data,
            outcome_data=outcome,
            company_id=bt_result.company_id,
            ticker=ticker,
        )
        bt_result.judge_evaluation_json = judge_eval.model_dump(mode="json")
        bt_result.warnings_json = judge_eval.warnings
        bt_result.missing_data_json = judge_eval.missing_data
        bt_result.status = judge_eval.judge_status

        await db.commit()
        await db.refresh(bt_result)
        return bt_result

    async def evaluate_backtest_run(
        self,
        db: AsyncSession,
        run_id: uuid.UUID,
    ) -> BacktestRun:
        """Evaluate all pending results in a backtest run."""
        run = await self.get_backtest_run(db, run_id)
        if run is None:
            raise ValueError(f"Backtest run {run_id} not found.")

        run.status = "running"
        run.started_at = _utcnow()
        await db.commit()

        results_query = await db.execute(
            select(BacktestResult).where(BacktestResult.backtest_run_id == run_id)
        )
        results = list(results_query.scalars().all())

        errors: list[str] = []
        for bt_result in results:
            try:
                await self.evaluate_report_outcome(db, bt_result.id)
            except Exception as exc:
                errors.append(f"Result {bt_result.id}: {exc}")
                logger.warning("Evaluation error for result %s: %s", bt_result.id, exc)

        # Refresh run and build summary
        await db.refresh(run)
        summary = await self.summarize_backtest_run(db, run_id)

        run.status = "failed" if errors and not results else "completed"
        run.completed_at = _utcnow()
        run.summary_json = summary.model_dump(mode="json")
        if errors:
            run.error_message = "; ".join(errors[:5])

        await db.commit()
        await db.refresh(run)
        return run

    # ------------------------------------------------------------------
    # Results listing
    # ------------------------------------------------------------------

    async def list_backtest_results(
        self,
        db: AsyncSession,
        run_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BacktestResult]:
        """List results for a backtest run."""
        query = await db.execute(
            select(BacktestResult)
            .where(BacktestResult.backtest_run_id == run_id)
            .order_by(BacktestResult.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(query.scalars().all())

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    async def summarize_backtest_run(
        self,
        db: AsyncSession,
        run_id: uuid.UUID,
    ) -> BacktestRunSummary:
        """Build a summary of a backtest run from its results."""
        run = await self.get_backtest_run(db, run_id)
        if run is None:
            raise ValueError(f"Backtest run {run_id} not found.")

        results_query = await db.execute(
            select(BacktestResult).where(BacktestResult.backtest_run_id == run_id)
        )
        results = list(results_query.scalars().all())

        total = len(results)
        completed = sum(
            1 for r in results
            if r.status not in ("pending", "running", "failed")
        )
        failed = sum(1 for r in results if r.status == "failed")

        status_breakdown: dict[str, int] = {}
        judge_scores: list[float] = []
        for r in results:
            status_breakdown[r.status] = status_breakdown.get(r.status, 0) + 1
            if r.judge_evaluation_json:
                sc = r.judge_evaluation_json.get("judge_score")
                if sc is not None:
                    judge_scores.append(float(sc))

        avg_judge_score = (
            round(sum(judge_scores) / len(judge_scores), 4) if judge_scores else None
        )

        return BacktestRunSummary(
            backtest_run_id=run_id,
            name=run.name,
            status=run.status,
            total_results=total,
            completed_results=completed,
            failed_results=failed,
            avg_judge_score=avg_judge_score,
            status_breakdown=status_breakdown,
            warnings=[INTERNAL_DISCLAIMER],
        )
