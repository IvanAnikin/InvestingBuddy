"""
Phase 15: Scoring Service — DB-aware wrapper for scorecards.

Persists ScorecardResult objects to the `scorecards` table and provides
query methods for candidate and run-level scoring operations.

All scorecards are internal research queue data only.
No public investment recommendations, price targets, or fair values are stored.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scorecard import Scorecard
from app.models.screening import ScreeningCandidate, ScreeningRun
from app.services.scoring_engine import ScorecardResult, ScoringEngine


class ScoringService:
    """
    DB-aware scoring service.

    Methods:
      score_candidate           — score one candidate and persist scorecard
      score_screening_run       — score all candidates in a run
      list_ranked_candidates    — list candidates sorted by overall score (desc)
      get_candidate_scorecard   — fetch scorecard for a candidate
      explain_candidate_score   — return full ScorecardResult dict for a candidate
    """

    def __init__(self) -> None:
        self._engine = ScoringEngine()

    # ── Candidate scoring ────────────────────────────────────────────────────

    async def score_candidate(
        self, db: AsyncSession, candidate_id: uuid.UUID
    ) -> Scorecard:
        """
        Score a screening candidate and persist the scorecard.

        Raises ValueError if the candidate is not found.
        """
        candidate = await _fetch_candidate(db, candidate_id)
        if candidate is None:
            raise ValueError(f"Screening candidate {candidate_id} not found.")

        candidate_data = _candidate_to_dict(candidate)
        result = self._engine.score_candidate(candidate_data)
        return await _persist_scorecard(
            db,
            result=result,
            score_type="candidate_scoring",
            candidate_id=candidate.id,
            company_id=candidate.company_id,
            report_id=None,
            provider_name=_infer_provider(candidate),
        )

    async def score_screening_run(
        self, db: AsyncSession, run_id: uuid.UUID
    ) -> list[Scorecard]:
        """
        Score all candidates in a screening run.

        Returns list of persisted Scorecard records.
        Raises ValueError if the run is not found.
        """
        run = await _fetch_run(db, run_id)
        if run is None:
            raise ValueError(f"Screening run {run_id} not found.")

        candidates = await _fetch_run_candidates(db, run_id)
        scorecards: list[Scorecard] = []
        for candidate in candidates:
            candidate_data = _candidate_to_dict(candidate)
            result = self._engine.score_candidate(candidate_data)
            sc = await _persist_scorecard(
                db,
                result=result,
                score_type="candidate_scoring",
                candidate_id=candidate.id,
                company_id=candidate.company_id,
                report_id=None,
                provider_name=_infer_provider(candidate),
            )
            scorecards.append(sc)

        return scorecards

    async def list_ranked_candidates(
        self,
        db: AsyncSession,
        run_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        List candidates from a run ranked by overall_score descending.

        Returns (items, total) where items include candidate + scorecard info.
        """
        run = await _fetch_run(db, run_id)
        if run is None:
            raise ValueError(f"Screening run {run_id} not found.")

        candidates = await _fetch_run_candidates(db, run_id)

        # Collect scorecard for each candidate
        ranked: list[dict[str, Any]] = []
        for candidate in candidates:
            scorecard = await _fetch_candidate_scorecard(db, candidate.id)
            ranked.append({
                "candidate_id": str(candidate.id),
                "ticker": candidate.ticker,
                "exchange": candidate.exchange,
                "name": candidate.name,
                "country": candidate.country,
                "sector": candidate.sector,
                "candidate_status": candidate.candidate_status,
                "source_tier": candidate.source_tier,
                "data_quality": candidate.data_quality,
                "warnings": candidate.warnings_json or [],
                "scorecard": _scorecard_to_dict(scorecard) if scorecard else None,
                "overall_score": scorecard.overall_score if scorecard else None,
                "internal_status": scorecard.internal_status if scorecard else "not_scored",
                "disclaimer": (
                    "INTERNAL SCORE ONLY. Not investment advice. "
                    "Not a public recommendation. Human review required."
                ),
            })

        # Sort by overall_score descending (unscored candidates go to bottom)
        ranked.sort(
            key=lambda x: x["overall_score"] if x["overall_score"] is not None else -1,
            reverse=True,
        )

        total = len(ranked)
        return ranked[offset: offset + limit], total

    async def get_candidate_scorecard(
        self, db: AsyncSession, candidate_id: uuid.UUID
    ) -> Scorecard | None:
        """Fetch the most recent scorecard for a candidate."""
        return await _fetch_candidate_scorecard(db, candidate_id)

    async def explain_candidate_score(
        self, db: AsyncSession, candidate_id: uuid.UUID
    ) -> dict[str, Any]:
        """
        Return a full explanation dict for a candidate's scorecard.

        Re-runs scoring if no scorecard exists.
        """
        candidate = await _fetch_candidate(db, candidate_id)
        if candidate is None:
            raise ValueError(f"Screening candidate {candidate_id} not found.")

        scorecard = await _fetch_candidate_scorecard(db, candidate_id)

        if scorecard is None:
            # Score on the fly (not persisted)
            candidate_data = _candidate_to_dict(candidate)
            result = self._engine.score_candidate(candidate_data)
            return result.to_dict()

        # Reconstruct explanation from persisted data
        scores_json = scorecard.scores_json or {}
        return {
            "overall_score": scorecard.overall_score,
            "internal_status": scorecard.internal_status,
            "scores": scores_json,
            "warnings": scorecard.warnings_json or [],
            "missing_data": scorecard.missing_data_json or [],
            "source_quality_summary": scorecard.source_quality_summary_json or {},
            "provider_name": scorecard.provider_name,
            "created_at": scorecard.created_at.isoformat() if scorecard.created_at else None,
            "disclaimer": (
                "INTERNAL SCORE ONLY. Not investment advice. "
                "Not a public recommendation. Human review required."
            ),
        }

    async def score_company_analysis(
        self,
        db: AsyncSession,
        company_id: uuid.UUID,
        report_id: uuid.UUID | None,
        company_snapshot: dict[str, Any],
        financial_data_summary: dict[str, Any] | None = None,
        source_quality_summary: dict[str, Any] | None = None,
        research_completeness_summary: dict[str, Any] | None = None,
        citation_validation_summary: dict[str, Any] | None = None,
        bull_case_summary: dict[str, Any] | None = None,
        bear_case_summary: dict[str, Any] | None = None,
        risk_summary: dict[str, Any] | None = None,
        valuation_guard_summary: dict[str, Any] | None = None,
        committee_chair_summary: dict[str, Any] | None = None,
    ) -> Scorecard:
        """
        Score a company from full company-analysis workflow outputs.

        Returns persisted Scorecard. Used by the scoring agent node.
        """
        result = self._engine.score_company_analysis(
            company_snapshot=company_snapshot,
            financial_data_summary=financial_data_summary,
            source_quality_summary=source_quality_summary,
            research_completeness_summary=research_completeness_summary,
            citation_validation_summary=citation_validation_summary,
            bull_case_summary=bull_case_summary,
            bear_case_summary=bear_case_summary,
            risk_summary=risk_summary,
            valuation_guard_summary=valuation_guard_summary,
            committee_chair_summary=committee_chair_summary,
        )
        provider_name = (
            company_snapshot.get("provider_metadata", {}).get("provider_name")
        )
        return await _persist_scorecard(
            db,
            result=result,
            score_type="company_analysis_scoring",
            candidate_id=None,
            company_id=company_id,
            report_id=report_id,
            provider_name=provider_name,
        )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _fetch_candidate(
    db: AsyncSession, candidate_id: uuid.UUID
) -> ScreeningCandidate | None:
    result = await db.execute(
        select(ScreeningCandidate).where(ScreeningCandidate.id == candidate_id)
    )
    return result.scalar_one_or_none()


async def _fetch_run(
    db: AsyncSession, run_id: uuid.UUID
) -> ScreeningRun | None:
    result = await db.execute(
        select(ScreeningRun).where(ScreeningRun.id == run_id)
    )
    return result.scalar_one_or_none()


async def _fetch_run_candidates(
    db: AsyncSession, run_id: uuid.UUID
) -> list[ScreeningCandidate]:
    result = await db.execute(
        select(ScreeningCandidate).where(
            ScreeningCandidate.screening_run_id == run_id
        )
    )
    return list(result.scalars().all())


async def _fetch_candidate_scorecard(
    db: AsyncSession, candidate_id: uuid.UUID
) -> Scorecard | None:
    result = await db.execute(
        select(Scorecard)
        .where(Scorecard.screening_candidate_id == candidate_id)
        .order_by(Scorecard.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _persist_scorecard(
    db: AsyncSession,
    result: ScorecardResult,
    score_type: str,
    candidate_id: uuid.UUID | None,
    company_id: uuid.UUID | None,
    report_id: uuid.UUID | None,
    provider_name: str | None,
) -> Scorecard:
    """Create and flush a Scorecard record."""
    scores_json = {
        dim_name: {
            "score": dim.score,
            "explanation": dim.explanation,
            "evidence_used": dim.evidence_used,
            "missing_data": dim.missing_data,
            "warnings": dim.warnings,
        }
        for dim_name, dim in result.scores.items()
    }
    sc = Scorecard(
        score_type=score_type,
        company_id=company_id,
        screening_candidate_id=candidate_id,
        report_id=report_id,
        overall_score=result.overall_score,
        internal_status=result.internal_status,
        scores_json=scores_json,
        warnings_json=result.warnings,
        missing_data_json=result.missing_data,
        source_quality_summary_json=result.source_quality_summary,
        provider_name=provider_name,
    )
    db.add(sc)
    await db.flush()
    return sc


def _candidate_to_dict(candidate: ScreeningCandidate) -> dict[str, Any]:
    return {
        "ticker": candidate.ticker,
        "exchange": candidate.exchange,
        "name": candidate.name,
        "country": candidate.country,
        "sector": candidate.sector,
        "market_cap": float(candidate.market_cap) if candidate.market_cap is not None else None,
        "currency": candidate.currency,
        "source_tier": candidate.source_tier or "T6_model_estimate",
        "data_quality": candidate.data_quality or "D_weak_or_stale",
        "discovery_reasons": candidate.discovery_reasons_json or [],
        "available_data": candidate.available_data_json or [],
        "missing_data": candidate.missing_data_json or [],
        "warnings": candidate.warnings_json or [],
        "candidate_status": candidate.candidate_status,
    }


def _infer_provider(candidate: ScreeningCandidate) -> str | None:
    if candidate.source_tier == "T5_api_aggregator":
        return "eodhd"
    if candidate.source_tier == "T6_model_estimate":
        return "mock"
    return None


def _scorecard_to_dict(sc: Scorecard) -> dict[str, Any]:
    return {
        "id": str(sc.id),
        "score_type": sc.score_type,
        "overall_score": sc.overall_score,
        "internal_status": sc.internal_status,
        "scores": sc.scores_json or {},
        "warnings": sc.warnings_json or [],
        "missing_data": sc.missing_data_json or [],
        "source_quality_summary": sc.source_quality_summary_json or {},
        "provider_name": sc.provider_name,
        "created_at": sc.created_at.isoformat() if sc.created_at else None,
        "disclaimer": (
            "INTERNAL SCORE ONLY. Not investment advice. "
            "Not a public recommendation. Human review required."
        ),
    }
