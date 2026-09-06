"""Watchlists and monitoring signals — V3.9 Slice 9.1.

WHY MONITORING COMES LAST
=========================
> **Memory before monitoring.** A watchlist alert is only meaningful as "this changed
> relative to what we concluded".

An alert that says "Pandora filed something" is a feed. An alert that says "Pandora filed
something, and it bears on the cash-runway question your last analysis left open" is
research. The second needs V3.8, which is why this phase is ninth rather than second.

NOTHING SCHEDULES THIS
======================
[OPEN DECISION #15](../../../docs/v3/OPEN_DECISIONS.md#15-monitoring-cadence) — how often to
check — is **user-owned and unresolved**. So the mechanism ships behind
``V3_MONITORING_ENABLED`` (default off) with **no timer, no cron and no worker job**, and a
test asserts that nothing in the codebase schedules it. Choosing a cadence is configuration
once somebody decides; guessing one would answer a question asked of somebody else, and it
would start spending money on a path nobody approved.

A SIGNAL IS AN OBSERVATION, NOT A CONCLUSION
============================================
``monitoring_signals`` records *that something observably changed* and what it bears on. It
never says what the change means — that is a research run's job, and a signal that carried
an interpretation would be an unreviewed conclusion with an alert's authority.

DEDUPLICATION IS THE WHOLE PROBLEM
==================================
A monitor that re-raises the same signal every time it runs is a monitor everybody turns
off. ``signal_key`` is derived from what was observed, and a partial unique index makes one
*open* signal per key impossible to duplicate — while the closed ones stay as history.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Watchlist(Base):
    """A named set of issuers somebody wants to be told about."""

    __tablename__ = "watchlists"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    #: Who it belongs to. Nullable while the platform is single-user, and present from
    #: day one because retrofitting a tenant boundary onto a store that never had an
    #: owner column is a rewrite (governance §8).
    owner: Mapped[str | None] = mapped_column(sa.String(200))
    description: Mapped[str | None] = mapped_column(sa.String(1000))
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index("ix_watchlists_owner_name", "owner", "name", unique=True),
    )


class WatchlistEntry(Base):
    """One issuer on one watchlist, and what is being watched about it."""

    __tablename__ = "watchlist_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    watchlist_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "watchlists.id",
            ondelete="CASCADE",
            name="fk_watchlist_entries_watchlist_id",
        ),
        nullable=False,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id",
            ondelete="CASCADE",
            name="fk_watchlist_entries_company_id",
        ),
        nullable=True,
    )
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "legal_entities.id",
            ondelete="SET NULL",
            name="fk_watchlist_entries_legal_entity_id",
        ),
        nullable=True,
    )
    #: Which signal kinds this entry wants. Empty means all of them.
    watched_kinds_json: Mapped[list | None] = mapped_column(JSONB)
    #: Free text for the human: why this issuer is here.
    note: Mapped[str | None] = mapped_column(sa.String(500))
    added_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        sa.Index(
            "ix_watchlist_entries_list_company",
            "watchlist_id",
            "company_id",
            unique=True,
        ),
        sa.Index("ix_watchlist_entries_company_id", "company_id"),
    )


class MonitoringSignal(Base):
    """One observed change. **An observation, never a conclusion.**"""

    __tablename__ = "monitoring_signals"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id",
            ondelete="SET NULL",
            name="fk_monitoring_signals_company_id",
        ),
        nullable=True,
    )
    #: ``new_document`` | ``new_ir_event`` | ``transcript_published`` |
    #: ``research_delta`` | ``open_gap_closable`` | ``macro_revision``.
    kind: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    #: Derived from WHAT was observed, so re-running the monitor cannot raise the same
    #: signal twice. A monitor that re-raises is a monitor everybody turns off.
    signal_key: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    #: What changed, in plain terms. **Never what it means.**
    summary: Mapped[str] = mapped_column(sa.String(1000), nullable=False)
    #: Which prior research question or finding it bears on, when it bears on one. This
    #: is the whole reason monitoring comes after memory: "a new filing" is a feed,
    #: "a new filing bearing on the question your last analysis left open" is research.
    relates_to_question_key: Mapped[str | None] = mapped_column(sa.String(80))
    relates_to_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_runs.id",
            ondelete="SET NULL",
            name="fk_monitoring_signals_run_id",
        ),
        nullable=True,
    )
    #: The observed thing, for provenance: a document version id, an IR event id, a
    #: delta id. Never a rendered sentence.
    source_ref: Mapped[str | None] = mapped_column(sa.String(200))
    detail_json: Mapped[dict | None] = mapped_column(JSONB)
    #: ``open`` | ``acknowledged`` | ``superseded``. No ``dismissed``: a signal somebody
    #: looked at and judged unimportant is *acknowledged*, and one the world overtook is
    #: *superseded*. Neither is a deletion.
    status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default="open", server_default=sa.text("'open'")
    )
    observed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index("ix_monitoring_signals_company_status", "company_id", "status"),
        sa.Index("ix_monitoring_signals_kind_observed", "kind", "observed_at"),
        # ONE OPEN SIGNAL PER KEY. The closed ones stay as history, so "we told you in
        # March and again in June" is answerable while "we told you eleven times in
        # March" is impossible.
        sa.Index(
            "ix_monitoring_signals_open_key",
            "signal_key",
            unique=True,
            postgresql_where=sa.text("status = 'open'"),
            sqlite_where=sa.text("status = 'open'"),
        ),
        sa.CheckConstraint(
            "kind IN ('new_document', 'new_ir_event', 'transcript_published', "
            "'research_delta', 'open_gap_closable', 'macro_revision')",
            name="ck_monitoring_signals_kind",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'superseded')",
            name="ck_monitoring_signals_status",
        ),
        # An acknowledged signal must say when. "Somebody dealt with this" with no time
        # on it is indistinguishable from a status nobody set deliberately.
        sa.CheckConstraint(
            "status <> 'acknowledged' OR acknowledged_at IS NOT NULL",
            name="ck_monitoring_signals_acknowledged_has_a_time",
        ),
    )


__all__ = ["MonitoringSignal", "Watchlist", "WatchlistEntry"]
