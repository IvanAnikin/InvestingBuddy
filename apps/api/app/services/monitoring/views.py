"""Read models for the watchlist and monitoring API — V3.21.2 (spec §3).

The router (``api/v1/watchlists.py``) stays thin and imports from here, from
``team_lists`` and from ``pass_job`` — never from the detector, so the guard test that
nothing outside this package calls the detector stays meaningful.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.models.company import Company
from app.models.monitoring import MonitoringSignal, Watchlist, WatchlistEntry
from app.models.research_record import CompanyResearchRecord
from app.services.monitoring import detector

SIGNAL_STATUSES = detector.SIGNAL_STATUSES
STATUS_OPEN = detector.STATUS_OPEN

#: Kinds shown with a "Re-run research" link: what bears on a conclusion, not a feed item.
MATERIAL_KINDS: frozenset[str] = frozenset({
    detector.KIND_MISSING_INFORMATION, detector.KIND_CATALYST_WINDOW,
    detector.KIND_EARNINGS_RELEASED, detector.KIND_CONSENSUS_REVISION,
    detector.KIND_RELATIONSHIP_CHANGE, detector.KIND_CHANGE_EVENT,
    detector.KIND_TRANSCRIPT_PUBLISHED, detector.KIND_OPEN_GAP_CLOSABLE,
    detector.KIND_COMPETITOR_CAPACITY,
})


def signal_view(s: MonitoringSignal) -> dict[str, Any]:
    return {"id": s.id, "company_id": s.company_id, "kind": s.kind, "summary": s.summary,
            "status": s.status, "relates_to_question_key": s.relates_to_question_key,
            "relates_to_run_id": s.relates_to_run_id, "source_ref": s.source_ref,
            "detail": s.detail_json or {}, "observed_at": s.observed_at,
            "acknowledged_at": s.acknowledged_at,
            "material": s.kind in MATERIAL_KINDS}


async def open_signal_counts(session: Any, company_ids: Sequence[uuid.UUID]
                             ) -> dict[uuid.UUID, int]:
    if not company_ids:
        return {}
    rows = await session.execute(
        select(MonitoringSignal.company_id, func.count())
        .where(MonitoringSignal.status == STATUS_OPEN,
               MonitoringSignal.company_id.in_(list(company_ids)))
        .group_by(MonitoringSignal.company_id))
    return {cid: int(n) for cid, n in rows.all()}


async def _entries(session: Any, watchlist_id: uuid.UUID) -> list[WatchlistEntry]:
    return list((await session.execute(
        select(WatchlistEntry).where(WatchlistEntry.watchlist_id == watchlist_id)
        .order_by(WatchlistEntry.added_at, WatchlistEntry.id))).scalars().all())


async def list_watchlists(session: Any) -> list[dict[str, Any]]:
    """Every list with its entry count and the open signals on its companies."""
    lists = list((await session.execute(
        select(Watchlist).order_by(Watchlist.created_at, Watchlist.name))).scalars().all())
    out: list[dict[str, Any]] = []
    for w in lists:
        entries = await _entries(session, w.id)
        ids = [e.company_id for e in entries if e.company_id is not None]
        counts = await open_signal_counts(session, ids)
        out.append({"id": w.id, "name": w.name, "description": w.description,
                    "is_active": w.is_active, "is_team_list": w.owner is None,
                    "created_at": w.created_at, "entry_count": len(entries),
                    "open_signal_count": sum(counts.values())})
    return out


async def _latest_record(session: Any, company_id: uuid.UUID) -> dict[str, Any] | None:
    r = (await session.execute(
        select(CompanyResearchRecord)
        .where(CompanyResearchRecord.company_id == company_id)
        .order_by(CompanyResearchRecord.as_of.desc(),
                  CompanyResearchRecord.created_at.desc()).limit(1))).scalars().first()
    if r is None:
        return None
    rubric = r.rubric_json if isinstance(r.rubric_json, dict) else {}
    return {"id": r.id, "research_run_id": r.research_run_id, "as_of": r.as_of,
            "verdict": rubric.get("recommendation"),
            "verdict_meaning": rubric.get("meaning"),
            "composite_score": rubric.get("composite_score")}


async def watchlist_detail(session: Any, watchlist: Watchlist, *,
                           signals_per_entry: int = 25) -> dict[str, Any]:
    entries = await _entries(session, watchlist.id)
    ids = [e.company_id for e in entries if e.company_id is not None]
    companies = {c.id: c for c in (await session.execute(
        select(Company).where(Company.id.in_(ids)))).scalars().all()} if ids else {}
    rows: list[dict[str, Any]] = []
    for e in entries:
        c = companies.get(e.company_id) if e.company_id else None
        signals = (await detector.open_signals(session, company_id=e.company_id,
                                               limit=signals_per_entry)
                   if e.company_id else [])
        rows.append({
            "id": e.id, "company_id": e.company_id,
            "company": ({"id": c.id, "ticker": c.ticker, "exchange": c.exchange,
                         "name": c.name} if c else None),
            "note": e.note, "source": e.source, "added_at": e.added_at,
            "added_by_hash": e.added_by_hash,
            "latest_record": await _latest_record(session, e.company_id)
            if e.company_id else None,
            "open_signals": [signal_view(s) for s in signals],
        })
    return {"id": watchlist.id, "name": watchlist.name,
            "description": watchlist.description, "is_active": watchlist.is_active,
            "is_team_list": watchlist.owner is None, "created_at": watchlist.created_at,
            "entry_count": len(entries),
            "open_signal_count": sum(len(r["open_signals"]) for r in rows),
            "entries": rows}


async def list_signals(session: Any, *, status: str | None = STATUS_OPEN,
                       company_id: uuid.UUID | None = None, limit: int = 100
                       ) -> list[MonitoringSignal]:
    stmt = select(MonitoringSignal)
    if status is not None:
        stmt = stmt.where(MonitoringSignal.status == status)
    if company_id is not None:
        stmt = stmt.where(MonitoringSignal.company_id == company_id)
    stmt = stmt.order_by(MonitoringSignal.observed_at.desc(),
                         MonitoringSignal.id).limit(max(1, min(limit, 500)))
    return list((await session.execute(stmt)).scalars().all())


async def acknowledge_signal(session: Any, signal: MonitoringSignal,
                             now: datetime | None = None) -> MonitoringSignal:
    """Open → acknowledged. An already-closed signal is returned unchanged."""
    if signal.status != STATUS_OPEN:
        return signal
    return await detector.acknowledge(session, signal, now=now or datetime.now(timezone.utc))


__all__ = ["MATERIAL_KINDS", "SIGNAL_STATUSES", "acknowledge_signal", "list_signals",
           "list_watchlists", "open_signal_counts", "signal_view", "watchlist_detail"]
