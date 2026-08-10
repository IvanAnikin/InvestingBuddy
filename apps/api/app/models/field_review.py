"""
Phase 32A Slice 6D: Deep Field Review — SQLAlchemy models.

Two tables:
  field_review_runs                — one comparative review over a discovery run
  field_review_candidate_summaries — one row per candidate considered by it

WHAT THIS IS (and is not). The Deep Field Review is a THIRD, separate council:

  * The DISCOVERY council (Phase 28B) triages a discovery run's CANDIDATE LIST
    BEFORE any full analysis exists.
  * The COMPANY council (Phase 28A) analyses ONE company in depth.
  * The DEEP FIELD REVIEW (here) runs AFTER 2+ candidates from the SAME
    discovery run already have a COMPLETED full analysis, and compares those
    ALREADY-PERSISTED reports against each other.

IMPORTANT SAFETY CONSTRAINTS (enforced across the model + service + API layers):
  - INTERNAL ADMIN ONLY. Never public, never published.
  - ``priority_tier`` is an INTERNAL RESEARCH-PRIORITY bucket
    (strongest_candidates / second_tier / blocked_insufficient_evidence), NOT an
    investment recommendation. No BUY/SELL/HOLD/WATCH label is ever stored.
  - No price target, fair value, intrinsic value, upside/downside, or return
    projection is ever stored.
  - Nothing is re-computed or re-fetched: every summary re-presents data that is
    already persisted on the candidate's report.
  - ``human_review_required`` is always True; nothing here is publication-ready.
  - Every considered candidate is recorded — including EXCLUDED ones, with an
    honest ``exclusion_reason`` (CLAUDE.md rule 8: rejected/failed cases are
    learning data, never silently dropped).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Allowed values (enforced at the service layer)
# ---------------------------------------------------------------------------

# Lifecycle status of a field-review run.
FIELD_REVIEW_STATUS_VALUES = {
    "pending",
    "running",
    "completed",
    "completed_with_warnings",
    "failed",
    # Fewer than ``field_review_min_candidates`` candidates had a usable
    # completed analysis — an explicit, honest terminal state. NOT a failure of
    # the council; the council never ran.
    "insufficient_candidates",
}

# Why a candidate was NOT included in the comparative review. Closed vocabulary —
# never free-form provider/exception text.
FIELD_REVIEW_EXCLUSION_REASONS = {
    "no_analysis_run",  # candidate has no analysis_report_id
    "report_deleted",  # analysis_report_id points at a missing row
    "draft_only",  # report has no final_report_version
    "not_schema_valid",  # report's schema validation did not pass
    "over_company_cap",  # beyond llm_field_review_council_max_companies
}

# The ONLY internal research-priority buckets. These are workflow states, never
# recommendations — BUY/SELL/HOLD/WATCH are absent by construction.
FIELD_REVIEW_PRIORITY_TIERS = {
    "strongest_candidates",
    "second_tier",
    "blocked_insufficient_evidence",
}

# The ONLY field-quality labels the field chair may return.
FIELD_REVIEW_QUALITY_VALUES = {"strong", "adequate", "thin", "failed"}


# ---------------------------------------------------------------------------
# FieldReviewRun
# ---------------------------------------------------------------------------


class FieldReviewRun(Base):
    """One Deep Field Review job over a single discovery run."""

    __tablename__ = "field_review_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    discovery_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "discovery_runs.id",
            ondelete="CASCADE",
            name="fk_field_review_runs_discovery_run_id_discovery_runs",
        ),
        nullable=False,
    )

    # One of FIELD_REVIEW_STATUS_VALUES.
    status: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="pending"
    )

    included_candidate_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    missing_candidate_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )

    # Honest council metadata — never fabricated. ``llm_used`` is True only when a
    # real (or, in tests, fake) client actually ran the council.
    llm_used: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    council_version: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    provider: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    agents_completed: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    agents_failed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    # One of FIELD_REVIEW_QUALITY_VALUES (chair-set), or NULL when no chair ran.
    field_quality: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    safety_valid: Mapped[bool | None] = mapped_column(sa.Boolean, nullable=True)

    # The full safety-scanned result payload (all eight agents' outputs, the
    # chair verdict buckets, the disclaimer). Never raw prompts or completions.
    review_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    warnings_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Short, safe reason code only — never a raw exception string.
    error: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

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
        sa.Index("ix_field_review_runs_discovery_run_id", "discovery_run_id"),
        sa.Index("ix_field_review_runs_status", "status"),
        sa.Index("ix_field_review_runs_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# FieldReviewCandidateSummary
# ---------------------------------------------------------------------------


class FieldReviewCandidateSummary(Base):
    """One candidate considered by a field review — included OR excluded.

    An EXCLUDED candidate keeps a row with ``included=False`` and a closed-
    vocabulary ``exclusion_reason``, so the review honestly documents what it
    could not compare and why.
    """

    __tablename__ = "field_review_candidate_summaries"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    field_review_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "field_review_runs.id",
            ondelete="CASCADE",
            name="fk_field_review_candidate_summaries_run_id_field_review_runs",
        ),
        nullable=False,
    )
    # SET NULL: research history survives a candidate/report deletion.
    discovery_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "discovery_candidates.id",
            ondelete="SET NULL",
            # NOTE: shortened ("candidate_summaries" -> "summaries"). The fully
            # symmetrical name would be 69 chars and PostgreSQL rejects any
            # identifier over 63. Name MUST match migration 015 exactly.
            name="fk_field_review_summaries_candidate_id_discovery_candidates",
        ),
        nullable=True,
    )
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "reports.id",
            ondelete="SET NULL",
            name="fk_field_review_candidate_summaries_report_id_reports",
        ),
        nullable=True,
    )

    # The stable citation id the council used for this company within THIS
    # review ("F1", "F2", …). Unique per field-review run.
    citation_ref: Mapped[str] = mapped_column(sa.String(20), nullable=False)

    ticker: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    exchange: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)

    included: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    # One of FIELD_REVIEW_EXCLUSION_REASONS; NULL when ``included`` is True.
    exclusion_reason: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    # real | mock | mixed | unknown — carried from the report, never guessed.
    data_provenance: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    # One of FIELD_REVIEW_PRIORITY_TIERS, or NULL when the chair did not place it.
    priority_tier: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)

    # The bounded FieldReviewCompanySummary that was actually sent to the council.
    summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "field_review_run_id",
            "citation_ref",
            name="uq_field_review_candidate_summary_run_ref",
        ),
        sa.Index(
            "ix_field_review_candidate_summaries_run_id", "field_review_run_id"
        ),
        sa.Index(
            "ix_field_review_candidate_summaries_candidate_id",
            "discovery_candidate_id",
        ),
        sa.Index("ix_field_review_candidate_summaries_report_id", "report_id"),
    )
