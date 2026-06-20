import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    workflow_version: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="1.0.0"
    )
    status: Mapped[str] = mapped_column(sa.String(50), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    trigger_type: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="manual"
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(as_uuid=True))
    total_tokens: Mapped[int | None] = mapped_column(sa.Integer)
    total_cost: Mapped[float | None] = mapped_column(sa.Numeric(10, 6))
    error_message: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        sa.Index("ix_agent_runs_workflow_name", "workflow_name"),
        sa.Index("ix_agent_runs_status", "status"),
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    step_name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(50), nullable=False, default="running")
    input_json: Mapped[dict | None] = mapped_column(sa.JSON)
    output_json: Mapped[dict | None] = mapped_column(sa.JSON)
    model_name: Mapped[str | None] = mapped_column(sa.String(100))
    tokens_used: Mapped[int | None] = mapped_column(sa.Integer)
    cost: Mapped[float | None] = mapped_column(sa.Numeric(10, 6))
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (sa.Index("ix_agent_steps_agent_run_id", "agent_run_id"),)
