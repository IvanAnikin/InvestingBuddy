import uuid
from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    slug: Mapped[str] = mapped_column(sa.String(500), unique=True, nullable=False)
    report_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    period_start: Mapped[date | None] = mapped_column(sa.Date)
    period_end: Mapped[date | None] = mapped_column(sa.Date)
    status: Mapped[str] = mapped_column(sa.String(50), nullable=False, default="draft")
    summary: Mapped[str | None] = mapped_column(sa.Text)
    content_markdown: Mapped[str | None] = mapped_column(sa.Text)
    content_html: Mapped[str | None] = mapped_column(sa.Text)
    created_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    # Phase 11 review workflow fields
    review_status: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="draft"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    reviewer_note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    review_decision_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    human_review_required: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True
    )
    approved_by: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        sa.Index("ix_reports_slug", "slug"),
        sa.Index("ix_reports_status", "status"),
        sa.Index("ix_reports_review_status", "review_status"),
        sa.Index("ix_reports_report_type", "report_type"),
        sa.Index("ix_reports_published_at", "published_at"),
    )
