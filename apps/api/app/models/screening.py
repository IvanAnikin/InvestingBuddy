"""
Phase 14: Company Discovery / Screener DB models.

Three tables:
  screening_universes  — defines a universe of companies to screen
  screening_runs       — one execution of a screen against a universe
  screening_candidates — individual companies found by a screening run

No final investment recommendations, price targets, or fair values are
stored here. Candidates are internal research funnel entries only.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Allowed values (enforced at service layer)
# ---------------------------------------------------------------------------

CANDIDATE_STATUS_VALUES = {
    "candidate_found",
    "needs_data",
    "needs_primary_sources",
    "ready_for_deeper_analysis",
    "rejected_by_screen",
    "error",
}

SCREENING_RUN_STATUS_VALUES = {
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
}

SCREENING_THEMES = {
    "energy_transition",
    "electrification_grid",
    "defense_security",
    "industrial_resilience",
    "real_assets",
    "materials_mining",
}


class ScreeningUniverse(Base):
    """
    Defines a reusable universe of companies to screen.

    A universe is a set of filter parameters (region, exchange, sector, theme)
    that a screening run uses to discover candidates.
    """

    __tablename__ = "screening_universes"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    # ── Filter parameters ────────────────────────────────────────────────────
    region: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    exchange: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    sector_filter: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    theme: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)

    # ── Provider ─────────────────────────────────────────────────────────────
    provider_name: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="mock"
    )

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        sa.Index("ix_su_theme", "theme"),
        sa.Index("ix_su_region", "region"),
        sa.Index("ix_su_provider", "provider_name"),
    )


class ScreeningRun(Base):
    """
    A single execution of a screening universe.

    Records inputs, status, timing, and a summary of what was found.
    Linked to all ScreeningCandidate records produced by this run.
    """

    __tablename__ = "screening_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    universe_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("screening_universes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # ── Status ───────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="pending"
    )
    provider_name: Mapped[str] = mapped_column(sa.String(50), nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    # ── Parameters used in this run ──────────────────────────────────────────
    parameters_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Run summary ──────────────────────────────────────────────────────────
    summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        sa.Index("ix_sr_status", "status"),
        sa.Index("ix_sr_provider", "provider_name"),
        sa.Index("ix_sr_created_at", "created_at"),
    )


class ScreeningCandidate(Base):
    """
    A company found by a screening run.

    Internal research funnel entry only. NOT a public investment recommendation.

    candidate_status allowed values:
      candidate_found         — raw find, minimal data
      needs_data              — more data needed before analysis
      needs_primary_sources   — T5/T6 data only; needs T1/T2 validation
      ready_for_deeper_analysis — sufficient data for full company analysis
      rejected_by_screen      — did not meet screen criteria on closer look
      error                   — error during candidate processing

    Forbidden values (never stored here):
      BUY, SELL, HOLD, WATCH, price_target, fair_value, upside_percent
    """

    __tablename__ = "screening_candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    screening_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("screening_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Company link (optional until promoted) ───────────────────────────────
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Identity ─────────────────────────────────────────────────────────────
    ticker: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    exchange: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    name: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    country: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    sector: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    provider_symbol: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)

    # ── Basic financials (from provider, if available) ───────────────────────
    market_cap: Mapped[float | None] = mapped_column(sa.Numeric(20, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(sa.String(10), nullable=True)

    # ── Discovery outcome (internal only — never public) ─────────────────────
    candidate_status: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="candidate_found"
    )

    # ── Discovery metadata ────────────────────────────────────────────────────
    discovery_reasons_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    available_data_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    missing_data_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # ── Source quality ────────────────────────────────────────────────────────
    source_tier: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    data_quality: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    warnings_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        sa.Index("ix_sc_candidate_status", "candidate_status"),
        sa.Index("ix_sc_ticker", "ticker"),
        sa.Index("ix_sc_company_id", "company_id"),
    )
