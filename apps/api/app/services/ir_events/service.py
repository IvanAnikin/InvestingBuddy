"""IR event and material persistence — V3.4 Slice 4.8.

THE STATE THIS MODULE EXISTS TO KEEP DISTINCT
=============================================
> **"The issuer held the call and published no transcript" is not "the issuer held no
> call", and neither is "we have not looked".**

[ADR-051](../../../docs/DECISIONS.md) chose free public sources and accepted that
coverage will be uneven — worst exactly where the existing pipeline is already thinnest,
European issuers who publish a presentation but not a transcript. Representing that
precisely is what turns it from an invisible hole into a research gap somebody can close.

So ``record_availability`` writes a row for an absence. ``not_published`` is a **finding**
about the issuer; an absent row is a statement about this platform.

IDENTITY IS DERIVED, NOT ASSIGNED
=================================
``event_key`` comes from the issuer, the event type and the reporting period, the same
way ``corpus.identity`` derives a document key. Two discoveries of one earnings call —
one from an IR calendar, one from a results release — converge on the same row instead of
producing two events the reader has to reconcile.

And it **over-splits on purpose**, as the corpus does: an event with no stated period
falls back to a date, so two genuinely different calls stay separate. Splitting produces
a duplicate; merging attributes one call's transcript to another call, and only one of
those is recoverable.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select

from app.models.ir_event import IrEvent, IrEventMaterial
from app.services.sources.financial_period import parse_period

# ── Vocabularies. Closed, and mirrored by CHECK constraints in migration 034. ── #

EVENT_EARNINGS_CALL = "earnings_call"
EVENT_RESULTS_RELEASE = "results_release"
EVENT_CAPITAL_MARKETS_DAY = "capital_markets_day"
EVENT_AGM = "agm"
EVENT_INVESTOR_DAY = "investor_day"
EVENT_GUIDANCE_UPDATE = "guidance_update"

EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_EARNINGS_CALL,
        EVENT_RESULTS_RELEASE,
        EVENT_CAPITAL_MARKETS_DAY,
        EVENT_AGM,
        EVENT_INVESTOR_DAY,
        EVENT_GUIDANCE_UPDATE,
    }
)

STATUS_ANNOUNCED = "announced"
STATUS_OCCURRED = "occurred"
STATUS_CANCELLED = "cancelled"
EVENT_STATUSES: frozenset[str] = frozenset(
    {STATUS_ANNOUNCED, STATUS_OCCURRED, STATUS_CANCELLED}
)

MATERIAL_TRANSCRIPT = "transcript"
MATERIAL_PRESENTATION = "presentation"
MATERIAL_PRESS_RELEASE = "press_release"
MATERIAL_REPORT = "report"
MATERIAL_WEBCAST = "webcast"
MATERIAL_AUDIO = "audio"
MATERIAL_QA = "qa"

MATERIAL_TYPES: frozenset[str] = frozenset(
    {
        MATERIAL_TRANSCRIPT,
        MATERIAL_PRESENTATION,
        MATERIAL_PRESS_RELEASE,
        MATERIAL_REPORT,
        MATERIAL_WEBCAST,
        MATERIAL_AUDIO,
        MATERIAL_QA,
    }
)

AVAILABILITY_AVAILABLE = "available"
#: **The state this table exists for.** The platform looked and the issuer published
#: nothing. Distinct from an absent row, which says only that nobody looked.
AVAILABILITY_NOT_PUBLISHED = "not_published"
#: Published, and behind a paywall. ADR-051: a paywalled source is not scraped.
AVAILABILITY_PAYWALLED = "paywalled"
AVAILABILITY_UNKNOWN = "unknown"

AVAILABILITIES: frozenset[str] = frozenset(
    {
        AVAILABILITY_AVAILABLE,
        AVAILABILITY_NOT_PUBLISHED,
        AVAILABILITY_PAYWALLED,
        AVAILABILITY_UNKNOWN,
    }
)

_TITLE_MAX = 400
_URL_MAX = 1000
_NOTE_MAX = 500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def event_key_for(
    *,
    issuer_key: str,
    event_type: str,
    fiscal_period_key: str | None = None,
    event_date: date | None = None,
) -> str:
    """A stable identity for one event.

    Prefers the reporting period, because "CFR's FY2025 earnings call" is the same event
    whether it is found on an IR calendar in January or in a results release in May. Falls
    back to the date, and then to nothing — and the fallback **over-splits**: two calls
    with neither a period nor a date stay separate. Splitting produces a duplicate a
    reader can merge; merging attributes one call's transcript to another call.
    """
    if fiscal_period_key:
        discriminator = f"period:{parse_period(fiscal_period_key).key or fiscal_period_key}"
    elif event_date is not None:
        discriminator = f"date:{event_date.isoformat()}"
    else:
        discriminator = f"opaque:{uuid.uuid4().hex}"
    payload = f"{issuer_key}|{event_type}|{discriminator}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


@dataclass(frozen=True)
class EventSpec:
    """What a source declares about an event before anything is stored."""

    issuer_key: str
    event_type: str
    status: str
    title: str | None = None
    fiscal_period_key: str | None = None
    scheduled_at: datetime | None = None
    occurred_at: datetime | None = None
    source_url: str | None = None
    source_ref: str | None = None

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(
                f"{self.event_type!r} is not an IR event type. Recognised: "
                f"{', '.join(sorted(EVENT_TYPES))}."
            )
        if self.status not in EVENT_STATUSES:
            raise ValueError(f"{self.status!r} is not an IR event status.")
        if self.status != STATUS_OCCURRED and self.occurred_at is not None:
            raise ValueError(
                "only an event recorded as 'occurred' may carry an occurrence time. "
                "An announced event with one is a calendar entry asserting a past "
                "nobody confirmed."
            )


async def upsert_event(
    session: Any,
    spec: EventSpec,
    *,
    company_id: uuid.UUID | None = None,
    legal_entity_id: uuid.UUID | None = None,
) -> IrEvent:
    """Find or create the event, converging on its derived identity.

    An existing event is **promoted** from ``announced`` to ``occurred`` when a source
    confirms it happened, and is never demoted: a later calendar scrape that still calls
    it "upcoming" must not un-happen a call the platform already saw occur.
    """
    stamp = spec.occurred_at or spec.scheduled_at
    key = event_key_for(
        issuer_key=spec.issuer_key,
        event_type=spec.event_type,
        fiscal_period_key=spec.fiscal_period_key,
        event_date=stamp.date() if stamp is not None else None,
    )
    existing = (
        await session.execute(select(IrEvent).where(IrEvent.event_key == key))
    ).scalar_one_or_none()
    if existing is not None:
        if spec.status == STATUS_OCCURRED and existing.status != STATUS_OCCURRED:
            existing.status = STATUS_OCCURRED
            existing.occurred_at = spec.occurred_at or existing.occurred_at
        elif spec.status == STATUS_CANCELLED and existing.status == STATUS_ANNOUNCED:
            existing.status = STATUS_CANCELLED
        if spec.title and not existing.title:
            existing.title = _clip(spec.title, _TITLE_MAX)
        await session.flush()
        return existing

    period = parse_period(spec.fiscal_period_key) if spec.fiscal_period_key else None
    event = IrEvent(
        id=uuid.uuid4(),
        company_id=company_id,
        legal_entity_id=legal_entity_id,
        event_key=key,
        event_type=spec.event_type,
        title=_clip(spec.title, _TITLE_MAX),
        fiscal_period_key=(period.key if period and not period.is_unknown else None),
        period_type=(period.period_type if period and not period.is_unknown else None),
        scheduled_at=spec.scheduled_at,
        occurred_at=spec.occurred_at if spec.status == STATUS_OCCURRED else None,
        status=spec.status,
        source_url=_clip(spec.source_url, _URL_MAX),
        source_ref=_clip(spec.source_ref, 200),
    )
    session.add(event)
    await session.flush()
    return event


async def record_availability(
    session: Any,
    event: IrEvent,
    *,
    material_type: str,
    availability: str,
    url: str | None = None,
    research_document_version_id: uuid.UUID | None = None,
    note: str | None = None,
    checked_at: datetime | None = None,
) -> IrEventMaterial:
    """Record what the platform found — **including that it found nothing.**

    Writing ``not_published`` is the whole point. It converts "we have no CFR FY2025
    transcript" from an absence nobody can act on into a statement about the issuer that
    a human can close by other means.
    """
    if material_type not in MATERIAL_TYPES:
        raise ValueError(f"{material_type!r} is not an IR material type.")
    if availability not in AVAILABILITIES:
        raise ValueError(
            f"{availability!r} is not an availability. The vocabulary is closed so that "
            "'no transcript' can be aggregated; free text cannot be."
        )
    if availability == AVAILABILITY_AVAILABLE and not (
        url or research_document_version_id
    ):
        raise ValueError(
            "an available material must say where it is: 'available' with no URL and "
            "no ingested version is a claim nothing can act on or check."
        )
    if research_document_version_id is not None and (
        availability != AVAILABILITY_AVAILABLE
    ):
        raise ValueError(
            "only an available material can have been ingested; a corpus document "
            "attached to a not_published row is one record contradicting itself."
        )
    existing = (
        await session.execute(
            select(IrEventMaterial).where(
                IrEventMaterial.ir_event_id == event.id,
                IrEventMaterial.material_type == material_type,
            )
        )
    ).scalar_one_or_none()
    stamp = checked_at or _utcnow()
    if existing is not None:
        existing.availability = availability
        existing.url = _clip(url, _URL_MAX)
        existing.research_document_version_id = research_document_version_id
        existing.note = _clip(note, _NOTE_MAX)
        existing.checked_at = stamp
        await session.flush()
        return existing
    material = IrEventMaterial(
        id=uuid.uuid4(),
        ir_event_id=event.id,
        material_type=material_type,
        availability=availability,
        url=_clip(url, _URL_MAX),
        research_document_version_id=research_document_version_id,
        note=_clip(note, _NOTE_MAX),
        checked_at=stamp,
    )
    session.add(material)
    await session.flush()
    return material


@dataclass(frozen=True)
class TranscriptState:
    """Whether a transcript exists for one event, and how confident that is.

    Four states, and the middle two are the ones a naive schema loses.
    """

    event_id: uuid.UUID
    #: ``held`` — ingested. ``published`` — exists, not yet retrieved.
    #: ``not_published`` — the issuer published none. ``not_checked`` — nobody looked.
    #: ``paywalled`` — published behind a paywall, which ADR-051 does not scrape.
    state: str
    url: str | None = None
    research_document_version_id: uuid.UUID | None = None
    checked_at: datetime | None = None
    note: str | None = None

    @property
    def is_a_research_gap(self) -> bool:
        """True when a human could usefully be told about it.

        ``not_checked`` is **not** a research gap — it is a task. ``not_published`` and
        ``paywalled`` are: they are established facts about the issuer that bound what
        the analysis can say.
        """
        return self.state in {"not_published", "paywalled"}


async def transcript_state(session: Any, event: IrEvent) -> TranscriptState:
    material = (
        await session.execute(
            select(IrEventMaterial).where(
                IrEventMaterial.ir_event_id == event.id,
                IrEventMaterial.material_type == MATERIAL_TRANSCRIPT,
            )
        )
    ).scalar_one_or_none()
    if material is None:
        return TranscriptState(event_id=event.id, state="not_checked")
    if material.availability == AVAILABILITY_AVAILABLE:
        state = "held" if material.research_document_version_id else "published"
    elif material.availability == AVAILABILITY_NOT_PUBLISHED:
        state = "not_published"
    elif material.availability == AVAILABILITY_PAYWALLED:
        state = "paywalled"
    else:
        state = "not_checked"
    return TranscriptState(
        event_id=event.id,
        state=state,
        url=material.url,
        research_document_version_id=material.research_document_version_id,
        checked_at=material.checked_at,
        note=material.note,
    )


async def events_for_company(
    session: Any,
    company_id: uuid.UUID,
    *,
    event_types: Sequence[str] = (),
    period_keys: Sequence[str] = (),
    limit: int = 50,
) -> list[IrEvent]:
    """Events for one issuer, newest first.

    Every filter is in the same statement as the LIMIT. A filter applied afterwards
    bounds the wrong population — V3.3.2 shipped that once and it returned zero segment
    facts for a real company.
    """
    stmt = select(IrEvent).where(IrEvent.company_id == company_id)
    if event_types:
        stmt = stmt.where(IrEvent.event_type.in_(list(event_types)))
    if period_keys:
        stmt = stmt.where(IrEvent.fiscal_period_key.in_(list(period_keys)))
    stmt = stmt.order_by(
        IrEvent.occurred_at.desc().nullslast(),
        IrEvent.scheduled_at.desc().nullslast(),
        IrEvent.event_key,
    ).limit(max(1, min(limit, 500)))
    return list((await session.execute(stmt)).scalars().all())


__all__ = [
    "AVAILABILITIES",
    "AVAILABILITY_AVAILABLE",
    "AVAILABILITY_NOT_PUBLISHED",
    "AVAILABILITY_PAYWALLED",
    "AVAILABILITY_UNKNOWN",
    "EVENT_AGM",
    "EVENT_CAPITAL_MARKETS_DAY",
    "EVENT_EARNINGS_CALL",
    "EVENT_GUIDANCE_UPDATE",
    "EVENT_INVESTOR_DAY",
    "EVENT_RESULTS_RELEASE",
    "EVENT_STATUSES",
    "EVENT_TYPES",
    "MATERIAL_AUDIO",
    "MATERIAL_PRESENTATION",
    "MATERIAL_PRESS_RELEASE",
    "MATERIAL_QA",
    "MATERIAL_REPORT",
    "MATERIAL_TRANSCRIPT",
    "MATERIAL_TYPES",
    "MATERIAL_WEBCAST",
    "STATUS_ANNOUNCED",
    "STATUS_CANCELLED",
    "STATUS_OCCURRED",
    "EventSpec",
    "TranscriptState",
    "event_key_for",
    "events_for_company",
    "record_availability",
    "transcript_state",
    "upsert_event",
]
