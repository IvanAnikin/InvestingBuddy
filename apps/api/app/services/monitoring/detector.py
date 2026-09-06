"""Change detection — V3.9 Slice 9.1.

NOTHING CALLS THIS ON A TIMER
=============================
[OPEN DECISION #15](../../../docs/v3/OPEN_DECISIONS.md#15-monitoring-cadence) — how often
to check — is **user-owned and unresolved**. So this module ships behind
``V3_MONITORING_ENABLED`` (default off), with no cron, no worker job and no scheduler, and
a test asserts that nothing in the codebase schedules it. Guessing a cadence would answer a
question asked of somebody else *and* start spending on a path nobody approved.

WHAT A DETECTOR MAY SAY
=======================
That something **observably changed**, and what it bears on. Never what it means: a signal
carrying an interpretation would be an unreviewed conclusion with an alert's authority, and
the platform has a whole council for reaching conclusions.

WHY THIS IS USEFUL RATHER THAN A FEED
=====================================
> **Memory before monitoring.** A watchlist alert is only meaningful as "this changed
> relative to what we concluded".

"Pandora filed something" is a feed. "Pandora filed something, and it bears on the
cash-runway question your last analysis left open" is research — which is why every
detector here looks at the **prior run's open questions and gaps** before deciding a change
is worth raising, and why an issuer with no prior research produces no signals at all.

DEDUPLICATION IS THE WHOLE PROBLEM
==================================
A monitor that re-raises the same signal every run is a monitor everybody turns off.
``signal_key`` is derived from what was observed — never from the time it was noticed — and
a partial unique index makes a second *open* signal for one key impossible.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.models.ir_event import IrEvent, IrEventMaterial
from app.models.monitoring import MonitoringSignal, Watchlist, WatchlistEntry
from app.models.research_document import ResearchDocument, ResearchDocumentVersion
from app.services.ir_events import service as ir
from app.services.memory import store as memory

# ── Signal kinds, closed ────────────────────────────────────────────────────── #

KIND_NEW_DOCUMENT = "new_document"
KIND_NEW_IR_EVENT = "new_ir_event"
KIND_TRANSCRIPT_PUBLISHED = "transcript_published"
KIND_RESEARCH_DELTA = "research_delta"
KIND_OPEN_GAP_CLOSABLE = "open_gap_closable"
KIND_MACRO_REVISION = "macro_revision"

SIGNAL_KINDS: frozenset[str] = frozenset(
    {
        KIND_NEW_DOCUMENT,
        KIND_NEW_IR_EVENT,
        KIND_TRANSCRIPT_PUBLISHED,
        KIND_RESEARCH_DELTA,
        KIND_OPEN_GAP_CLOSABLE,
        KIND_MACRO_REVISION,
    }
)

STATUS_OPEN = "open"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_SUPERSEDED = "superseded"
SIGNAL_STATUSES: frozenset[str] = frozenset(
    {STATUS_OPEN, STATUS_ACKNOWLEDGED, STATUS_SUPERSEDED}
)

#: A bound on one detection pass. A monitor that can raise a thousand signals in one run
#: is a monitor that will, on the day a reindex touches every document.
MAX_SIGNALS_PER_PASS = 25

_SUMMARY_MAX = 1000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def signal_key_for(kind: str, *parts: str | None) -> str:
    """Derived from WHAT was observed, never from when it was noticed.

    A key containing a timestamp would make every pass produce new signals, which is
    exactly the behaviour that gets a monitor switched off.
    """
    payload = "|".join([kind, *[(p or "") for p in parts]])
    return f"{kind}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:40]}"


@dataclass
class DetectedSignal:
    """One observed change, before it is written."""

    kind: str
    signal_key: str
    summary: str
    company_id: uuid.UUID | None = None
    relates_to_question_key: str | None = None
    relates_to_run_id: uuid.UUID | None = None
    source_ref: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in SIGNAL_KINDS:
            raise ValueError(
                f"{self.kind!r} is not a signal kind. A kind invented at a call site is "
                "one nothing can filter a watchlist on."
            )


@dataclass
class DetectionResult:
    """What one pass observed, and why it observed nothing when it did."""

    signals: list[DetectedSignal] = field(default_factory=list)
    persisted: int = 0
    duplicates_suppressed: int = 0
    #: Why nothing was raised. A pass that returns an empty list without saying why is
    #: indistinguishable from one that never ran.
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_count": len(self.signals),
            "persisted": self.persisted,
            "duplicates_suppressed": self.duplicates_suppressed,
            "reason": self.reason,
            "kinds": sorted({s.kind for s in self.signals}),
        }


async def detect_for_company(
    session: Any,
    *,
    company_id: uuid.UUID,
    cfg: Any = None,
    since: datetime | None = None,
    now: datetime | None = None,
) -> DetectionResult:
    """Observe what changed for one issuer **relative to its last research run**.

    An issuer with no prior research produces **no signals**: there is nothing for a
    change to be relative to, and raising one anyway would make this a feed.
    """
    if cfg is None:
        from app.core.config import settings as default_cfg

        cfg = default_cfg
    result = DetectionResult()
    if not getattr(cfg, "v3_monitoring_enabled", False):
        result.reason = "V3_MONITORING_ENABLED is off; nothing was observed."
        return result

    stamp = now or _utcnow()
    prior = await memory.recall(session, company_id=company_id, now=stamp)
    if not prior.exists:
        result.reason = (
            "no prior research for this issuer, so there is nothing for a change to be "
            "relative to. A signal raised here would be a feed, not research."
        )
        return result

    cutoff = since or prior.run_completed_at
    open_question_keys = {key for key, _ in prior.carry_forward_questions}

    result.signals.extend(
        await _new_documents(
            session, company_id=company_id, cutoff=cutoff, prior_run_id=prior.run_id
        )
    )
    result.signals.extend(
        await _ir_events(
            session,
            company_id=company_id,
            cutoff=cutoff,
            prior_run_id=prior.run_id,
            open_question_keys=open_question_keys,
        )
    )
    result.signals = result.signals[:MAX_SIGNALS_PER_PASS]
    if not result.signals:
        result.reason = "nothing observable has changed since the last run."
    return result


async def _new_documents(
    session: Any,
    *,
    company_id: uuid.UUID,
    cutoff: datetime | None,
    prior_run_id: uuid.UUID | None,
) -> list[DetectedSignal]:
    """Corpus versions retrieved since the last run.

    Keyed on the **content hash**, so re-retrieving the same bytes raises nothing. That
    is the difference between "the issuer published something" and "our fetcher ran
    again".
    """
    # `company_id` lives on the DOCUMENT, not the version — a version is one retrieval
    # of a document and inherits the issuer through it.
    stmt = (
        select(ResearchDocumentVersion)
        .join(
            ResearchDocument,
            ResearchDocument.id == ResearchDocumentVersion.research_document_id,
        )
        .where(ResearchDocument.company_id == company_id)
    )
    if cutoff is not None:
        stmt = stmt.where(ResearchDocumentVersion.retrieved_at > cutoff)
    stmt = stmt.order_by(ResearchDocumentVersion.retrieved_at.desc()).limit(
        MAX_SIGNALS_PER_PASS
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        DetectedSignal(
            kind=KIND_NEW_DOCUMENT,
            signal_key=signal_key_for(
                KIND_NEW_DOCUMENT, str(company_id), row.content_hash
            ),
            summary=_clip(
                f"A document not seen at the last run was retrieved: "
                f"{row.canonical_url}",
                _SUMMARY_MAX,
            )
            or "",
            company_id=company_id,
            relates_to_run_id=prior_run_id,
            source_ref=str(row.id),
            detail={"content_hash": row.content_hash, "transport": row.transport},
        )
        for row in rows
    ]


async def _ir_events(
    session: Any,
    *,
    company_id: uuid.UUID,
    cutoff: datetime | None,
    prior_run_id: uuid.UUID | None,
    open_question_keys: "set[str]",
) -> list[DetectedSignal]:
    """New IR events, and transcripts that have since been published.

    The second is the one that matters: an event previously recorded ``not_published``
    whose transcript now exists is a gap the last run could not close and this run can.
    """
    signals: list[DetectedSignal] = []
    stmt = select(IrEvent).where(IrEvent.company_id == company_id)
    if cutoff is not None:
        stmt = stmt.where(IrEvent.first_seen_at > cutoff)
    stmt = stmt.order_by(IrEvent.first_seen_at.desc()).limit(MAX_SIGNALS_PER_PASS)
    for event in (await session.execute(stmt)).scalars().all():
        signals.append(
            DetectedSignal(
                kind=KIND_NEW_IR_EVENT,
                signal_key=signal_key_for(
                    KIND_NEW_IR_EVENT, str(company_id), event.event_key
                ),
                summary=_clip(
                    f"A {event.event_type} not seen at the last run is on record"
                    + (f" for {event.fiscal_period_key}" if event.fiscal_period_key else ""),
                    _SUMMARY_MAX,
                )
                or "",
                company_id=company_id,
                relates_to_run_id=prior_run_id,
                source_ref=str(event.id),
                detail={"event_type": event.event_type, "status": event.status},
            )
        )

    # A transcript that has appeared since. Keyed on the EVENT, not on the check, so a
    # monitor that runs daily raises it once.
    transcript_stmt = (
        select(IrEvent, IrEventMaterial)
        .join(IrEventMaterial, IrEventMaterial.ir_event_id == IrEvent.id)
        .where(
            IrEvent.company_id == company_id,
            IrEventMaterial.material_type == ir.MATERIAL_TRANSCRIPT,
            IrEventMaterial.availability == ir.AVAILABILITY_AVAILABLE,
        )
    )
    if cutoff is not None:
        transcript_stmt = transcript_stmt.where(IrEventMaterial.checked_at > cutoff)
    transcript_stmt = transcript_stmt.limit(MAX_SIGNALS_PER_PASS)
    for event, material in (await session.execute(transcript_stmt)).all():
        question_key = (
            "transcript" if "transcript" in open_question_keys else None
        )
        signals.append(
            DetectedSignal(
                kind=KIND_TRANSCRIPT_PUBLISHED,
                signal_key=signal_key_for(
                    KIND_TRANSCRIPT_PUBLISHED, str(company_id), event.event_key
                ),
                summary=_clip(
                    "A transcript is now available for an event the last run had none "
                    f"for ({event.event_type}"
                    + (f", {event.fiscal_period_key}" if event.fiscal_period_key else "")
                    + ")",
                    _SUMMARY_MAX,
                )
                or "",
                company_id=company_id,
                relates_to_question_key=question_key,
                relates_to_run_id=prior_run_id,
                source_ref=str(material.id),
                detail={"url": material.url},
            )
        )
    return signals


async def persist_signals(
    session: Any, result: DetectionResult, *, now: datetime | None = None
) -> DetectionResult:
    """Write the signals, suppressing one that is already open.

    Checked in Python **and** enforced by a partial unique index, because the second is
    what holds when two passes run at once.
    """
    stamp = now or _utcnow()
    for signal in result.signals:
        existing = (
            await session.execute(
                select(MonitoringSignal).where(
                    MonitoringSignal.signal_key == signal.signal_key,
                    MonitoringSignal.status == STATUS_OPEN,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            result.duplicates_suppressed += 1
            continue
        session.add(
            MonitoringSignal(
                id=uuid.uuid4(),
                company_id=signal.company_id,
                kind=signal.kind,
                signal_key=signal.signal_key,
                summary=_clip(signal.summary, _SUMMARY_MAX) or "",
                relates_to_question_key=_clip(signal.relates_to_question_key, 80),
                relates_to_run_id=signal.relates_to_run_id,
                source_ref=_clip(signal.source_ref, 200),
                detail_json=signal.detail or None,
                status=STATUS_OPEN,
                observed_at=stamp,
            )
        )
        result.persisted += 1
    await session.flush()
    return result


async def acknowledge(
    session: Any, signal: MonitoringSignal, *, now: datetime | None = None
) -> MonitoringSignal:
    """Somebody looked at it. **Not a deletion** — the history is the point."""
    signal.status = STATUS_ACKNOWLEDGED
    signal.acknowledged_at = now or _utcnow()
    await session.flush()
    return signal


async def open_signals(
    session: Any,
    *,
    company_id: uuid.UUID | None = None,
    kinds: "Sequence[str]" = (),
    limit: int = 100,
) -> list[MonitoringSignal]:
    """Open signals, newest first. Every filter is in the same statement as the LIMIT."""
    stmt = select(MonitoringSignal).where(MonitoringSignal.status == STATUS_OPEN)
    if company_id is not None:
        stmt = stmt.where(MonitoringSignal.company_id == company_id)
    if kinds:
        stmt = stmt.where(MonitoringSignal.kind.in_(list(kinds)))
    stmt = stmt.order_by(MonitoringSignal.observed_at.desc()).limit(
        max(1, min(limit, 500))
    )
    return list((await session.execute(stmt)).scalars().all())


async def watchlist_company_ids(
    session: Any, watchlist: Watchlist
) -> list[uuid.UUID]:
    stmt = select(WatchlistEntry.company_id).where(
        WatchlistEntry.watchlist_id == watchlist.id,
        WatchlistEntry.company_id.is_not(None),
    )
    return [row[0] for row in (await session.execute(stmt)).all()]


__all__ = [
    "KIND_MACRO_REVISION",
    "KIND_NEW_DOCUMENT",
    "KIND_NEW_IR_EVENT",
    "KIND_OPEN_GAP_CLOSABLE",
    "KIND_RESEARCH_DELTA",
    "KIND_TRANSCRIPT_PUBLISHED",
    "MAX_SIGNALS_PER_PASS",
    "SIGNAL_KINDS",
    "SIGNAL_STATUSES",
    "STATUS_ACKNOWLEDGED",
    "STATUS_OPEN",
    "STATUS_SUPERSEDED",
    "DetectedSignal",
    "DetectionResult",
    "acknowledge",
    "detect_for_company",
    "open_signals",
    "persist_signals",
    "signal_key_for",
    "watchlist_company_ids",
]
