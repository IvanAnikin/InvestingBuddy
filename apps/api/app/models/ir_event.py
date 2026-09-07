"""IR events and their materials — V3.4 Slice 4.8.

WHY THE ABSENCE NEEDS A ROW
===========================
[ADR-051](../../../docs/DECISIONS.md) says there is no paid transcript subscription and
that **missing means missing**. That only works if the architecture can tell the
difference between three states a naive schema collapses into one:

1. the issuer held an earnings call and published a transcript — we have it;
2. the issuer held an earnings call and published **no** transcript — a research gap a
   human can close by other means;
3. we have **not looked**.

An absent row is state 3. State 2 needs its own row, with ``availability`` saying so, or
"CFR published no FY2025 transcript" becomes indistinguishable from "CFR held no call" —
and the second is a much stranger fact about a listed company than the first.

Coverage will be uneven and worst exactly where the existing pipeline is already
thinnest: European issuers who publish a presentation but not a transcript. That is the
expected outcome of the decision, and representing it precisely is what makes it
actionable rather than invisible.

THE PERIOD IS THE ISSUER'S, NOT THE CALENDAR'S
==============================================
``fiscal_period_key`` is a ``financial_period.ReportingPeriod`` key, because an earnings
call is *about* a reporting period and Richemont's FY2025 ends in March. This is the
one place in V3.4 where the **financial** period vocabulary is right and the macro one
(slice 4.7) would be wrong.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IrEvent(Base):
    """One investor-relations event: a call, a results release, a capital-markets day."""

    __tablename__ = "ir_events"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id",
            ondelete="SET NULL",
            name="fk_ir_events_company_id_companies",
        ),
        nullable=True,
    )
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "legal_entities.id",
            ondelete="SET NULL",
            name="fk_ir_events_legal_entity_id_legal_entities",
        ),
        nullable=True,
    )
    #: Stable identity derived from the issuer, the event type and the period —
    #: computed in ``services.ir_events.identity``, never assigned by an ingest run,
    #: so two discoveries of the same call converge instead of duplicating.
    event_key: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    #: ``earnings_call`` | ``results_release`` | ``capital_markets_day`` |
    #: ``agm`` | ``investor_day`` | ``guidance_update``.
    event_type: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    title: Mapped[str | None] = mapped_column(sa.String(400))
    #: An issuer's own reporting period, from ``financial_period`` — NOT a calendar one.
    fiscal_period_key: Mapped[str | None] = mapped_column(sa.String(20))
    period_type: Mapped[str | None] = mapped_column(sa.String(20))
    scheduled_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    occurred_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: ``announced`` | ``occurred`` | ``cancelled``. ``announced`` is a calendar entry;
    #: only ``occurred`` asserts the event took place.
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    #: Where the platform learned the event exists. An event nobody can trace back to a
    #: published source is a claim, not a record.
    source_url: Mapped[str | None] = mapped_column(sa.String(1000))
    source_ref: Mapped[str | None] = mapped_column(sa.String(200))
    first_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index("ix_ir_events_event_key", "event_key", unique=True),
        sa.Index("ix_ir_events_company_period", "company_id", "fiscal_period_key"),
        sa.Index("ix_ir_events_type_occurred", "event_type", "occurred_at"),
        sa.CheckConstraint(
            "event_type IN ('earnings_call', 'results_release', "
            "'capital_markets_day', 'agm', 'investor_day', 'guidance_update')",
            name="ck_ir_events_event_type",
        ),
        sa.CheckConstraint(
            "status IN ('announced', 'occurred', 'cancelled')",
            name="ck_ir_events_status",
        ),
        # Only an event that OCCURRED may claim a date it occurred on. An announced
        # event with an occurrence date is a calendar entry asserting a past it has
        # not been confirmed to have.
        sa.CheckConstraint(
            "status = 'occurred' OR occurred_at IS NULL",
            name="ck_ir_events_only_occurred_has_an_occurrence",
        ),
    )


class IrEventMaterial(Base):
    """One material an event may have — and, when it does not, the record saying so."""

    __tablename__ = "ir_event_materials"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ir_event_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "ir_events.id",
            ondelete="CASCADE",
            name="fk_ir_event_materials_event_id_ir_events",
        ),
        nullable=False,
    )
    #: ``transcript`` | ``presentation`` | ``press_release`` | ``report`` |
    #: ``webcast`` | ``audio`` | ``qa``.
    material_type: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    #: ``available`` | ``not_published`` | ``paywalled`` | ``unknown``.
    #:
    #: **``not_published`` is the point of the table.** It says the platform looked and
    #: the issuer published nothing, which is a research gap somebody can close. An
    #: absent row says only that nobody looked.
    availability: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    url: Mapped[str | None] = mapped_column(sa.String(1000))
    #: Set once the material has been fetched into the corpus. NULL beside
    #: ``available`` means "published, not yet ingested" — a real and different state
    #: from "published and we hold it".
    research_document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_document_versions.id",
            ondelete="SET NULL",
            name="fk_ir_event_materials_version_id",
        ),
        nullable=True,
    )
    #: Free text for a human: which page was checked, what it said. Never the only
    #: record of a state — ``availability`` is.
    note: Mapped[str | None] = mapped_column(sa.String(500))
    #: When the platform last established this availability. An availability with no
    #: date is a claim about the present made at an unknown time.
    checked_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index(
            "ix_ir_event_materials_event_type",
            "ir_event_id",
            "material_type",
            unique=True,
        ),
        sa.Index("ix_ir_event_materials_availability", "availability"),
        sa.CheckConstraint(
            "material_type IN ('transcript', 'presentation', 'press_release', "
            "'report', 'webcast', 'audio', 'qa')",
            name="ck_ir_event_materials_material_type",
        ),
        sa.CheckConstraint(
            "availability IN ('available', 'not_published', 'paywalled', 'unknown')",
            name="ck_ir_event_materials_availability",
        ),
        # A material that is available has to say WHERE. Without this, "available" with
        # no URL and no ingested version is a claim nothing can act on or check.
        sa.CheckConstraint(
            "availability <> 'available' OR url IS NOT NULL "
            "OR research_document_version_id IS NOT NULL",
            name="ck_ir_event_materials_available_has_a_location",
        ),
        # And the reverse: only something available can have been ingested. A corpus
        # document attached to a `not_published` row is the two facts contradicting
        # each other in one record.
        sa.CheckConstraint(
            "research_document_version_id IS NULL OR availability = 'available'",
            name="ck_ir_event_materials_only_available_is_ingested",
        ),
    )


__all__ = ["IrEvent", "IrEventMaterial"]
