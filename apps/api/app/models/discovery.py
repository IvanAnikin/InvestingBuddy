"""
Phase 25: Real Market Candidate Discovery — SQLAlchemy models.

Two tables:
  discovery_runs        — one execution of an internal, bounded market scan
  discovery_candidates  — a ranked internal research candidate produced by a run

IMPORTANT SAFETY CONSTRAINTS (enforced across the model + service + API layers):
  - These records are INTERNAL ADMIN ONLY. They are never public and never
    published.
  - No BUY/SELL/HOLD/WATCH labels are stored. No price targets, fair values,
    intrinsic values, upside/downside, or undervalued/overvalued labels.
  - ``candidate_score`` is an INTERNAL PRIORITIZATION signal only — not an
    investment recommendation and not investment advice.
  - Every candidate has ``human_review_required=True`` and ``is_public=False``.
  - Candidate labels are drawn from the safe internal label vocabulary only
    (see ``ALLOWED_CANDIDATE_LABELS``).
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
# Allowed values (enforced at the service layer)
# ---------------------------------------------------------------------------

# Lifecycle status of a discovery run.
DISCOVERY_RUN_STATUS_VALUES = {
    "pending",
    "running",
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
}

# How the universe for a run was sourced.
UNIVERSE_SOURCE_VALUES = {
    "curated_seed",
    "manual_tickers",
}

# Internal prioritization grade — NOT a recommendation.
CANDIDATE_GRADE_VALUES = {
    "high_internal_interest",
    "medium_internal_interest",
    "low_internal_interest",
    "data_insufficient",
}

# Safe internal labels. These are the ONLY labels a candidate may carry.
# None of these are investment-action labels (no BUY/SELL/HOLD/WATCH).
ALLOWED_CANDIDATE_LABELS = {
    "internal_research_candidate",
    "candidate_for_human_review",
    "positive_momentum_candidate",
    "catalyst_rich_candidate",
    "fundamentals_available",
    "data_sparse",
    "needs_human_review",
    "research_incomplete",
    "discovery_candidate",
}


# ---------------------------------------------------------------------------
# DiscoveryRun
# ---------------------------------------------------------------------------


class DiscoveryRun(Base):
    """A single bounded, internal-only market candidate discovery run."""

    __tablename__ = "discovery_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Lifecycle status — never a public recommendation.
    # Allowed: pending, running, completed, completed_with_warnings,
    #          failed, cancelled
    status: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="pending"
    )

    provider_name: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="free_real"
    )
    # curated_seed | manual_tickers
    universe_source: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="curated_seed"
    )
    universe_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    requested_tickers: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    processed_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    lookback_days: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=90)

    warnings: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    config_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    safety_notes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_by: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    # Internal-only, human-review-required at all times.
    human_review_required: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True
    )

    started_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        sa.Index("ix_discovery_runs_status", "status"),
        sa.Index("ix_discovery_runs_created_at", "created_at"),
        sa.Index("ix_discovery_runs_provider", "provider_name"),
    )


# ---------------------------------------------------------------------------
# DiscoveryCandidate
# ---------------------------------------------------------------------------


class DiscoveryCandidate(Base):
    """
    A ranked internal research candidate produced by a discovery run.

    INTERNAL ADMIN ONLY. Not a public investment recommendation. The
    ``candidate_score`` is an internal prioritization signal only.
    """

    __tablename__ = "discovery_candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    discovery_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("discovery_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Identity ─────────────────────────────────────────────────────────
    ticker: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    exchange: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="US")
    company_name: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    legal_name: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    sector: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    industry: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    country: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    lei: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    website: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)

    # ── Candidate scoring (internal prioritization only) ─────────────────
    candidate_score: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    # high_internal_interest | medium_internal_interest |
    # low_internal_interest | data_insufficient
    candidate_score_grade: Mapped[str | None] = mapped_column(
        sa.String(50), nullable=True
    )
    rank: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    # ── Component scores (0–100, internal only) ──────────────────────────
    momentum_score: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    fundamentals_score: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    catalyst_score: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    source_quality_score: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    data_completeness_score: Mapped[float | None] = mapped_column(
        sa.Float, nullable=True
    )
    risk_penalty_score: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    # Safe internal labels (subset of ALLOWED_CANDIDATE_LABELS).
    labels_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    score_explanation: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    # ── Momentum / trend signals (T6 model-derived) ──────────────────────
    momentum_label: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    return_1m: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    return_3m: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    return_6m: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    pct_above_ma50: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    pct_above_ma200: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    # ── Catalyst signals ─────────────────────────────────────────────────
    catalyst_coverage_status: Mapped[str | None] = mapped_column(
        sa.String(50), nullable=True
    )
    latest_catalyst_date: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    positive_catalyst_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    high_strength_catalyst_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    press_release_event_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    news_event_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    filing_event_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    primary_or_regulator_event_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    aggregator_only_event_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )

    # ── Financial / market summary (never fabricated) ────────────────────
    latest_close: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    market_cap_mln: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    enterprise_value_mln: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    pe_ratio: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    revenue_mln: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    revenue_growth_yoy_pct: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    net_income_mln: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    free_cash_flow_mln: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    total_debt_mln: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    cash_mln: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    latest_annual_fy: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)

    # ── Completeness / source quality ────────────────────────────────────
    source_quality: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    missing_info_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    blocking_gap_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    source_tiers_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    warnings_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    missing_sources_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    missing_fields_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    raw_signal_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    snapshot_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Workflow linkage (set when "Run Full Analysis" is triggered) ─────
    analysis_report_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("reports.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Safety ───────────────────────────────────────────────────────────
    human_review_required: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True
    )
    is_public: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    safety_valid: Mapped[bool | None] = mapped_column(sa.Boolean, nullable=True)
    schema_valid: Mapped[bool | None] = mapped_column(sa.Boolean, nullable=True)
    safety_notes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "discovery_run_id",
            "ticker",
            "exchange",
            name="uq_discovery_candidate_run_ticker_exchange",
        ),
        sa.Index("ix_discovery_candidates_run_id", "discovery_run_id"),
        sa.Index("ix_discovery_candidates_ticker", "ticker"),
        sa.Index("ix_discovery_candidates_score", "candidate_score"),
        sa.Index("ix_discovery_candidates_sector", "sector"),
        sa.Index("ix_discovery_candidates_grade", "candidate_score_grade"),
    )
