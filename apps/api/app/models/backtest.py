"""
Phase 22: Judge + Backtesting Framework — SQLAlchemy models.

Tables:
  backtest_runs           — a named evaluation run over a set of reports
  backtest_results        — per-report outcome + judge evaluation within a run
  thesis_tracking_events  — optional lightweight event log for thesis tracking

IMPORTANT CONSTRAINTS:
  - No BUY/SELL/HOLD/WATCH public recommendations are stored.
  - No price targets, fair values, or upside percentages are stored.
  - All evaluations are internal historical quality assessments only.
  - Human review is required before any action.
  - CI uses mock provider only — no live EODHD/Stooq calls required.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# BacktestRun
# ---------------------------------------------------------------------------


class BacktestRun(Base):
    """A named batch evaluation run over a set of internal reports."""

    __tablename__ = "backtest_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    # Lifecycle status — not a public recommendation
    # Allowed: pending, running, completed, failed, cancelled
    status: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="pending"
    )

    horizon_days: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    benchmark_symbol: Mapped[str | None] = mapped_column(
        sa.String(50), nullable=True
    )
    provider_name: Mapped[str] = mapped_column(
        sa.String(100), nullable=False, default="mock"
    )

    # Flexible JSON config and results
    parameters_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


# ---------------------------------------------------------------------------
# BacktestResult
# ---------------------------------------------------------------------------


class BacktestResult(Base):
    """Per-report outcome + judge evaluation within a backtest run."""

    __tablename__ = "backtest_results"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("backtest_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("reports.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scorecard_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("scorecards.id", ondelete="SET NULL"),
        nullable=True,
    )

    ticker: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    exchange: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)

    evaluation_start_date: Mapped[date | None] = mapped_column(
        sa.Date, nullable=True
    )
    evaluation_end_date: Mapped[date | None] = mapped_column(
        sa.Date, nullable=True
    )
    horizon_days: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    benchmark_symbol: Mapped[str | None] = mapped_column(
        sa.String(50), nullable=True
    )

    # Historical outcome metrics — NOT future recommendations
    # start_price / end_price are for evaluation of past data quality only
    outcome_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Judge evaluation — internal quality assessment
    judge_evaluation_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Metadata
    warnings_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    missing_data_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Lifecycle: insufficient_data | useful_research | needs_better_sources |
    #            poor_evidence_quality | outcome_inconclusive | outcome_review_required
    status: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="pending"
    )

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow
    )


# ---------------------------------------------------------------------------
# ThesisTrackingEvent (optional lightweight audit log)
# ---------------------------------------------------------------------------


class ThesisTrackingEvent(Base):
    """Optional lightweight event log for thesis development tracking."""

    __tablename__ = "thesis_tracking_events"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("reports.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # e.g. "coverage_added", "judge_evaluated", "outcome_computed", "data_updated"
    event_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    event_date: Mapped[date | None] = mapped_column(sa.Date, nullable=True)

    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_tier: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow
    )
