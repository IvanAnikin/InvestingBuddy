"""The macro observation store — V3.4 Slice 4.7.

WHAT MAKES A PAST REPORT REPRODUCIBLE
=====================================
``as_of``. A macro series is revised, so "euro-area GDP for Q2" is one number in July and
a different number in October. A report written in August cited the July number and was
right to. Reading the series *as of* the report's date returns what the platform knew
then; reading it today returns the current view. Both are available and neither
overwrites the other.

A REVISION IS A NEW ROW
=======================
``record_observation`` never updates a value. It writes a row at a new vintage and
demotes the previous ``is_current``, so the history is complete and the "latest reading"
is still one indexed row. Writing the **same** vintage twice is a unique-index conflict
rather than a silent overwrite, which is what turns "the publisher re-released" and "our
connector ran twice" into different events.

NULL IS A PUBLISHED ABSENCE
===========================
A statistical agency publishes "no observation for this period" all the time, and that is
information: it is not the same as the platform never having asked. So ``value`` is
nullable and a NULL observation is a real row with a real vintage. What the store never
does is invent a zero for one.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update

from app.models.macro import MacroDataset, MacroObservation, MacroSeries
from app.services.macro.definitions import (
    DatasetSpec,
    SeriesSpec,
    parse_macro_period,
)

#: A bound on how many observations one call may return. A daily series over twenty
#: years is 7,300 rows and an agent paying for them in tokens did not ask for that.
DEFAULT_LIMIT = 200
MAX_LIMIT = 2000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ObservationValue:
    """One reading, with everything needed to cite and to interpret it."""

    period_key: str
    value: float | None
    unit: str
    vintage_at: datetime
    retrieved_at: datetime
    currency: str | None = None
    scale: str | None = None
    seasonal_adjustment: str | None = None
    source_ref: str | None = None
    is_current: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_key": self.period_key,
            "value": self.value,
            "unit": self.unit,
            "currency": self.currency,
            "scale": self.scale,
            "seasonal_adjustment": self.seasonal_adjustment,
            "vintage_at": self.vintage_at.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "source_ref": self.source_ref,
            "is_current": self.is_current,
        }


async def upsert_dataset(session: Any, spec: DatasetSpec) -> MacroDataset:
    """Find or create the dataset. Identity is ``dataset_key``, never the row id."""
    existing = (
        await session.execute(
            select(MacroDataset).where(MacroDataset.dataset_key == spec.dataset_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    dataset = MacroDataset(
        id=uuid.uuid4(),
        dataset_key=spec.dataset_key,
        source_id=spec.source_id,
        display_name=spec.display_name,
        source_url=spec.source_url,
        licence=spec.licence,
        update_cadence=spec.update_cadence,
        access_class=spec.access_class,
    )
    session.add(dataset)
    await session.flush()
    return dataset


async def upsert_series(
    session: Any, dataset: MacroDataset, spec: SeriesSpec
) -> MacroSeries:
    """Find or create a series within a dataset.

    An existing series' **unit is never rewritten**. If a publisher genuinely changes a
    series' unit that is a new series, not a mutation of this one: rewriting it would
    silently re-scale every observation already stored against it.
    """
    existing = (
        await session.execute(
            select(MacroSeries).where(
                MacroSeries.dataset_id == dataset.id,
                MacroSeries.series_key == spec.series_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.unit != spec.unit:
            raise ValueError(
                f"series {spec.series_key!r} is stored in {existing.unit!r} and the "
                f"source now declares {spec.unit!r}. A unit change is a NEW series: "
                "rewriting it would silently re-scale every observation already "
                "recorded against this one."
            )
        return existing
    series = MacroSeries(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        series_key=spec.series_key,
        display_name=spec.display_name,
        indicator_code=spec.indicator_code,
        unit=spec.unit,
        currency=spec.currency,
        scale=spec.scale,
        geography=spec.geography,
        frequency=spec.frequency,
        seasonal_adjustment=spec.seasonal_adjustment,
    )
    session.add(series)
    await session.flush()
    return series


async def record_observation(
    session: Any,
    series: MacroSeries,
    *,
    period_key: str,
    value: float | Decimal | None,
    vintage_at: datetime,
    source_ref: str | None = None,
    retrieved_at: datetime | None = None,
) -> MacroObservation:
    """Write one reading at one vintage. **Never updates a value.**

    The period key is parsed rather than trusted: it was built by a connector from a
    publisher's structured response, so an unrecognised one is a defect in the connector
    and storing it would put a row in the series that nothing can order.
    """
    period = parse_macro_period(period_key)
    if period.frequency != series.frequency:
        raise ValueError(
            f"{period_key!r} is a {period.frequency} period and "
            f"{series.series_key!r} is a {series.frequency} series. Mixing frequencies "
            "in one series produces something that looks continuous and is not."
        )
    stamp = retrieved_at or _utcnow()
    # Demote whatever was current for this period. A revision supersedes; it does not
    # erase. Older rows keep is_current=False and stay readable through `as_of`.
    await session.execute(
        update(MacroObservation)
        .where(
            MacroObservation.series_id == series.id,
            MacroObservation.period_key == period_key,
            MacroObservation.is_current.is_(True),
            MacroObservation.vintage_at <= vintage_at,
        )
        .values(is_current=False)
    )
    # Is this vintage actually the newest? A backfill of an OLD release must not become
    # the current reading just because it was loaded last.
    newest = (
        await session.execute(
            select(MacroObservation.vintage_at)
            .where(
                MacroObservation.series_id == series.id,
                MacroObservation.period_key == period_key,
            )
            .order_by(MacroObservation.vintage_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    is_current = newest is None or _aware(vintage_at) >= _aware(newest)
    observation = MacroObservation(
        id=uuid.uuid4(),
        series_id=series.id,
        period_key=period_key,
        period_start=period.start,
        period_end=period.end,
        value=Decimal(str(value)) if value is not None else None,
        vintage_at=vintage_at,
        retrieved_at=stamp,
        source_ref=source_ref,
        is_current=is_current,
    )
    session.add(observation)
    await session.flush()
    return observation


def _aware(value: datetime) -> datetime:
    """UTC-aware, so a naive vintage from sqlite never compares against an aware one.

    A comparison that raises is better than one that silently orders a naive datetime
    before every aware one, which is what makes a stored revision look older than the
    release it superseded.
    """
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def find_series(
    session: Any, *, dataset_key: str, series_key: str
) -> MacroSeries | None:
    stmt = (
        select(MacroSeries)
        .join(MacroDataset, MacroSeries.dataset_id == MacroDataset.id)
        .where(
            MacroDataset.dataset_key == dataset_key,
            MacroSeries.series_key == series_key,
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _to_value(series: MacroSeries, row: MacroObservation) -> ObservationValue:
    return ObservationValue(
        period_key=row.period_key,
        value=float(row.value) if row.value is not None else None,
        unit=series.unit,
        currency=series.currency,
        scale=series.scale,
        seasonal_adjustment=series.seasonal_adjustment,
        vintage_at=row.vintage_at,
        retrieved_at=row.retrieved_at,
        source_ref=row.source_ref,
        is_current=row.is_current,
    )


async def latest(
    session: Any,
    series: MacroSeries,
    *,
    period_keys: Sequence[str] = (),
    limit: int = DEFAULT_LIMIT,
) -> list[ObservationValue]:
    """The current reading of each period. One indexed row per period."""
    stmt = select(MacroObservation).where(
        MacroObservation.series_id == series.id,
        MacroObservation.is_current.is_(True),
    )
    if period_keys:
        # The filter is in the SAME statement as the LIMIT. A filter applied in Python
        # after a LIMIT returns the wrong rows, which V3.3.2 shipped once.
        stmt = stmt.where(MacroObservation.period_key.in_(list(period_keys)))
    stmt = stmt.order_by(MacroObservation.period_key.desc()).limit(
        max(1, min(limit, MAX_LIMIT))
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_value(series, row) for row in rows]


async def as_of(
    session: Any,
    series: MacroSeries,
    when: datetime,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[ObservationValue]:
    """What the platform knew about this series at ``when``.

    For each period, the observation with the greatest vintage **at or before** ``when``.
    This is what makes a report written in August still say what it said: reading the
    current value instead would silently re-base its macro context months later.

    Implemented by reading the vintages and choosing in Python rather than by a window
    function, because the bound has to be on **periods** and a SQL ``LIMIT`` would bound
    *rows* — with several vintages per period, a row limit returns fewer periods than
    asked for and none of them is wrong, which is the worst kind of quietly-short answer.
    """
    cutoff = _aware(when)
    stmt = (
        select(MacroObservation)
        .where(
            MacroObservation.series_id == series.id,
            MacroObservation.vintage_at <= when,
        )
        .order_by(
            MacroObservation.period_key.desc(), MacroObservation.vintage_at.desc()
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    chosen: dict[str, MacroObservation] = {}
    for row in rows:
        if _aware(row.vintage_at) > cutoff:
            continue
        current = chosen.get(row.period_key)
        if current is None or _aware(row.vintage_at) > _aware(current.vintage_at):
            chosen[row.period_key] = row
    ordered = sorted(chosen.values(), key=lambda r: r.period_key, reverse=True)
    return [_to_value(series, row) for row in ordered[: max(1, min(limit, MAX_LIMIT))]]


async def revision_history(
    session: Any, series: MacroSeries, *, period_key: str
) -> list[ObservationValue]:
    """Every vintage of one period, newest first. The revision itself as a finding."""
    stmt = (
        select(MacroObservation)
        .where(
            MacroObservation.series_id == series.id,
            MacroObservation.period_key == period_key,
        )
        .order_by(MacroObservation.vintage_at.desc())
        .limit(MAX_LIMIT)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_value(series, row) for row in rows]


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "ObservationValue",
    "as_of",
    "find_series",
    "latest",
    "record_observation",
    "revision_history",
    "upsert_dataset",
    "upsert_series",
]
