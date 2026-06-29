import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Source(Base):
    """A research document or data point that backs investment claims."""

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    url: Mapped[str | None] = mapped_column(sa.String(2000))
    publisher: Mapped[str | None] = mapped_column(sa.String(200))
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, nullable=False
    )
    credibility_score: Mapped[float | None] = mapped_column(sa.Numeric(4, 3))
    content_hash: Mapped[str | None] = mapped_column(sa.String(64))
    blob_path: Mapped[str | None] = mapped_column(sa.String(1000))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index("ix_sources_source_type", "source_type"),
        sa.Index("ix_sources_retrieved_at", "retrieved_at"),
        sa.Index("ix_sources_content_hash", "content_hash"),
    )


class Citation(Base):
    """Links a claim in a report or agent run to a Source record."""

    __tablename__ = "citations"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=True,
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    claim_text: Mapped[str | None] = mapped_column(sa.Text)
    source_quote: Mapped[str | None] = mapped_column(sa.Text)
    url: Mapped[str | None] = mapped_column(sa.String(2000))
    retrieved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # Phase 6: structured provenance fields for provider-sourced citations
    field_path: Mapped[str | None] = mapped_column(sa.String(200))
    source_tier: Mapped[str | None] = mapped_column(sa.String(50))
    data_quality: Mapped[str | None] = mapped_column(sa.String(50))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index("ix_citations_report_id", "report_id"),
        sa.Index("ix_citations_source_id", "source_id"),
        sa.Index("ix_citations_agent_run_id", "agent_run_id"),
    )
