"""Anti-convergence store — V3.20 slice 6.4 (migration 052, spec §3.6).

See ``alembic/versions/052_add_discovery_seen.py`` and ``services/discovery/novelty.py``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DiscoverySeen(Base):
    """One row per listed issuer (``EXCHANGE:TICKER``) that discovery has surfaced.

    Research history is never deleted; this is a summary over it — when the name was
    first and last seen, how often, when it was last shortlisted and last researched, and
    the diversity cell it fell in — so a later run can tell a fresh name from a re-run.
    """

    __tablename__ = "discovery_seen"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    issuer_key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="SET NULL",
                      name="fk_discovery_seen_company_id_companies"),
        nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True),
                                                    default=_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True),
                                                   default=_now, nullable=False)
    times_seen: Mapped[int] = mapped_column(sa.Integer, default=1, nullable=False)
    times_shortlisted: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    last_shortlisted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True),
                                                                 nullable=True)
    last_researched_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True),
                                                                nullable=True)
    #: The newest change-event date known when the name was last researched (slice 6.2):
    #: the novelty gate re-admits a name only on an event NEWER than this.
    last_event_date: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    last_discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(as_uuid=True),
                                                                    nullable=True)
    #: ``{layer, driver, regime}`` — the diversity cell at the last sighting.
    cell_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB,
                                                                     nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 nullable=False)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 onupdate=_now, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("issuer_key", name="uq_discovery_seen_issuer_key"),
        sa.Index("ix_discovery_seen_company_id", "company_id"),
    )
