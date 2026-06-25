import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


REVIEW_ACTIONS = {
    "mark_under_review",
    "approve",
    "reject",
    "needs_revision",
}

REVIEW_STATUSES = {
    "draft",
    "under_review",
    "approved_internal",
    "rejected_internal",
    "needs_revision",
    "archived",
}


class ReportReviewEvent(Base):
    __tablename__ = "report_review_events"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    from_status: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    to_status: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    actor_label: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index("ix_review_events_report_id", "report_id"),
        sa.Index("ix_review_events_action", "action"),
    )
