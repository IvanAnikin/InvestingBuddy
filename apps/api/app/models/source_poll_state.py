"""Periodic-task and source poll state — V3.21.2 (migration 063, ADR-109).

One row per periodic task (``task_key``). The registry in
``services/scheduling/periodic.py`` reads ``last_started_at`` to decide whether a task is
due in the current UTC bucket, and claims the bucket with a guarded UPDATE so two worker
processes (or two restarts on the same day) can never start a task twice in one bucket.
The handler stamps ``last_ok_at`` or ``last_error``.

``etag``, ``last_modified`` and ``detail_json`` are for the later source pollers (trade
press, coverage, industry data) that need HTTP conditional requests and a small cursor.
No user data is held here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SourcePollState(Base):
    __tablename__ = "source_poll_state"

    task_key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    last_started_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True)
    #: Exception TYPE and a short reason; never a payload, prompt or body.
    last_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    etag: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_now,
        server_default=sa.func.now())


__all__ = ["SourcePollState"]
