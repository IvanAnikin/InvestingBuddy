"""Macro, government and industry observations — V3.4 Slice 4.7.

WHY THE PLATFORM NEEDS A PLACE TO PUT A NUMBER IT DID NOT GET FROM A FILING
==========================================================================
Fifteen macro sources are in the registry today and every one of them is
**reference-only by design**: they emit a bounded pointer plus an honest gap and make no
network call at report time. That was the right call when nothing could store a time
series. This is the somewhere for one to land.

VINTAGES ARE THE WHOLE POINT
============================
A macro series is **revised**. Euro-area GDP for Q2 is one number in July and a different
number in October, and both are correct as of when they were published. A store that
overwrote the first would silently change the macro context of every report that cited
it, months after the report was signed off.

So an observation is keyed on ``(series, period, vintage)`` and a revision is a **new
row**. ``UNIQUE (series_id, period_key, vintage_at)`` makes a second write of the same
vintage a conflict rather than an overwrite, and a partial unique index makes "the
current reading" exactly one row per period. Reading a series *as of* a past date returns
what the platform knew then — which is what makes a past report reproducible instead of
quietly re-based.

THE UNIT LIVES ON THE SERIES, NOT ON THE OBSERVATION
====================================================
Because a series whose unit could change between observations is not a series. The same
rule the fact pipeline applies to a financial figure, applied to a statistical one: an
ambiguous unit is worse than a missing one, because a missing one makes the arithmetic
refuse and an ambiguous one lets it run and be wrong.
"""

import uuid
from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MacroDataset(Base):
    """One published dataset from one official provider."""

    __tablename__ = "macro_datasets"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: Stable, human-readable identity: ``world_bank:WDI``. Never a row id in a URL.
    dataset_key: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    #: The registry ``source_id`` this dataset belongs to, so a dataset can always be
    #: traced back to the policy entry that describes its licence and rate limits.
    source_id: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    source_url: Mapped[str | None] = mapped_column(sa.String(1000))
    #: The licence as the publisher states it. "We already fetched it" is not a licence.
    licence: Mapped[str | None] = mapped_column(sa.String(200))
    update_cadence: Mapped[str | None] = mapped_column(sa.String(60))
    #: The governance class, from ``corpus.policy``. Official statistics are
    #: ``public_official``; nothing here may be ``user_private``.
    access_class: Mapped[str] = mapped_column(
        sa.String(30), nullable=False, server_default=sa.text("'public_official'")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index("ix_macro_datasets_dataset_key", "dataset_key", unique=True),
    )


class MacroSeries(Base):
    """One measured quantity within a dataset, for one geography."""

    __tablename__ = "macro_series"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "macro_datasets.id",
            ondelete="CASCADE",
            name="fk_macro_series_dataset_id_macro_datasets",
        ),
        nullable=False,
    )
    #: Unique WITHIN a dataset. Two providers may legitimately both publish
    #: ``NY.GDP.MKTP.CD``, and they are not the same series.
    series_key: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    #: The publisher's own indicator code, kept as printed so a citation can quote it.
    indicator_code: Mapped[str | None] = mapped_column(sa.String(120))
    #: A series whose unit could change between observations is not a series.
    unit: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    currency: Mapped[str | None] = mapped_column(sa.String(10))
    #: ``units`` | ``thousand`` | ``million`` | ``billion``. NULL means base units.
    scale: Mapped[str | None] = mapped_column(sa.String(20))
    #: ISO-3166 alpha-3, an aggregate code (``EMU``, ``WLD``), or NULL for global.
    geography: Mapped[str | None] = mapped_column(sa.String(20))
    #: ``annual`` | ``quarterly`` | ``monthly`` | ``weekly`` | ``daily``. A macro
    #: vocabulary, deliberately NOT the financial ``ReportingPeriod`` one: an issuer's
    #: fiscal half has no statistical analogue and a shared vocabulary would invite
    #: comparing a fiscal year with a calendar one.
    frequency: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    #: ``sa`` | ``nsa`` | NULL when the publisher does not say. NULL is "not stated",
    #: never "not adjusted" — reading one as the other is a seasonal artefact presented
    #: as a trend.
    seasonal_adjustment: Mapped[str | None] = mapped_column(sa.String(10))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index(
            "ix_macro_series_dataset_key", "dataset_id", "series_key", unique=True
        ),
        sa.Index("ix_macro_series_geography_frequency", "geography", "frequency"),
        sa.CheckConstraint(
            "frequency IN ('annual', 'quarterly', 'monthly', 'weekly', 'daily')",
            name="ck_macro_series_frequency",
        ),
        sa.CheckConstraint(
            "seasonal_adjustment IS NULL OR seasonal_adjustment IN ('sa', 'nsa')",
            name="ck_macro_series_seasonal_adjustment",
        ),
    )


class MacroObservation(Base):
    """One value of one series for one period, **as published at one vintage**."""

    __tablename__ = "macro_observations"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    series_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "macro_series.id",
            ondelete="CASCADE",
            name="fk_macro_observations_series_id_macro_series",
        ),
        nullable=False,
    )
    #: ``2025`` | ``2025-Q2`` | ``2025-07`` | ``2025-07-14``. Sortable as text within a
    #: frequency, which is why the month and day are zero-padded.
    period_key: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    period_start: Mapped[date | None] = mapped_column(sa.Date)
    period_end: Mapped[date | None] = mapped_column(sa.Date)
    value: Mapped[float | None] = mapped_column(sa.Numeric(30, 10))
    #: When the PUBLISHER released this reading. A revision is a new row with a later
    #: vintage, never an update of this one.
    vintage_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    #: When THIS PLATFORM retrieved it. Distinct from the vintage: retrieving a
    #: three-year-old release today does not make it a new release.
    retrieved_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow
    )
    #: The exact request or document this value came from, for a citation.
    source_ref: Mapped[str | None] = mapped_column(sa.String(1000))
    #: Exactly one current row per (series, period). A partial unique index, not a
    #: convention — see the migration.
    is_current: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        # A revision is a NEW ROW. Writing the same vintage twice is a conflict.
        sa.Index(
            "ix_macro_observations_series_period_vintage",
            "series_id",
            "period_key",
            "vintage_at",
            unique=True,
        ),
        sa.Index(
            "ix_macro_observations_series_period", "series_id", "period_key"
        ),
        # Exactly one CURRENT reading per (series, period). A partial unique index, so
        # the superseded vintages sit beside it without contending for the slot.
        # PostgreSQL-only: sqlite supports partial indexes, but declaring it here for
        # both keeps the ORM and migration 033 saying the same thing.
        sa.Index(
            "ix_macro_observations_one_current",
            "series_id",
            "period_key",
            unique=True,
            postgresql_where=sa.text("is_current"),
            sqlite_where=sa.text("is_current"),
        ),
        sa.CheckConstraint(
            "period_end IS NULL OR period_start IS NULL OR period_end >= period_start",
            name="ck_macro_observations_period_order",
        ),
    )


__all__ = ["MacroDataset", "MacroObservation", "MacroSeries"]
