"""
Phase 15: Scoring + Valuation Framework — Scorecard DB model.

Stores multi-dimension internal research scores for screening candidates
and company analysis outputs.

IMPORTANT CONSTRAINTS:
  - No final investment recommendations (BUY/SELL/HOLD/WATCH) are stored.
  - No price targets, fair values, or upside percentages are stored.
  - internal_status values are research queue labels only — not public advice.
  - Human admin review is required before any further action on high-priority items.
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
# Allowed internal status values
# ---------------------------------------------------------------------------

SCORECARD_INTERNAL_STATUSES = {
    "not_enough_data",
    "low_priority_research",
    "needs_primary_sources",
    "ready_for_deeper_analysis",
    "high_priority_for_human_review",
    "reject_due_to_data_quality",
}

# Forbidden status values — never stored
FORBIDDEN_SCORECARD_STATUSES = {
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "REJECT",
    "price_target",
    "fair_value",
    "upside_percent",
}

# Score type values
SCORECARD_SCORE_TYPES = {
    "candidate_scoring",
    "company_analysis_scoring",
}


class Scorecard(Base):
    """
    Internal research attractiveness scorecard.

    Produced by the ScoringEngine for screening candidates or company
    analysis outputs.  Scores are multi-dimension 0–100 integers.

    NOT investment advice. NOT a public recommendation.
    internal_status is a research queue label for admin use only.

    Relationships:
      - company_id  → companies.id  (SET NULL on delete)
      - screening_candidate_id → screening_candidates.id (SET NULL)
      - report_id   → reports.id    (SET NULL on delete)
    """

    __tablename__ = "scorecards"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── Source links (at least one should be set) ────────────────────────────
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    screening_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("screening_candidates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("reports.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Score type ───────────────────────────────────────────────────────────
    score_type: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="candidate_scoring"
    )

    # ── Composite score (0–100) ──────────────────────────────────────────────
    overall_score: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    # ── Internal research status (admin-only; never public recommendation) ───
    internal_status: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="not_enough_data"
    )

    # ── Dimension scores + metadata (JSONB) ──────────────────────────────────
    scores_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    warnings_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    missing_data_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # ── Source quality summary ────────────────────────────────────────────────
    source_quality_summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Provider metadata ────────────────────────────────────────────────────
    provider_name: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        sa.Index("ix_sc_score_company_id", "company_id"),
        sa.Index("ix_sc_score_candidate_id", "screening_candidate_id"),
        sa.Index("ix_sc_score_report_id", "report_id"),
        sa.Index("ix_sc_score_type", "score_type"),
        sa.Index("ix_sc_overall_score", "overall_score"),
        sa.Index("ix_sc_internal_status", "internal_status"),
    )
