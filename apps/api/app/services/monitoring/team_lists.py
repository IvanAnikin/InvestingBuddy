"""The two team lists — V3.21.2 (spec §3).

``Shortlist`` and ``Watchlist`` are created on first use with ``owner`` NULL: one research
space for the allow-listed admins, not a per-person list. An entry records who added it
(SHA-256 of the proxied admin e-mail, as for portfolios; never the address), when, and
from where (``manual`` | ``discovery_candidate`` | ``report`` | ``auto_report``).

Both helpers are idempotent: a second ``ensure_team_list`` returns the same row, and a
second ``add_to_team_list`` for a company already on the list returns the existing entry
unchanged (``created=False``). They flush and never commit, so a caller's transaction
decides. The auto-report slice (21.3) adds its rank-1 company with ``source="auto_report"``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.monitoring import Watchlist, WatchlistEntry

SHORTLIST = "Shortlist"
WATCHLIST = "Watchlist"
TEAM_LISTS: tuple[str, ...] = (SHORTLIST, WATCHLIST)

SOURCE_MANUAL = "manual"
SOURCE_DISCOVERY_CANDIDATE = "discovery_candidate"
SOURCE_REPORT = "report"
SOURCE_AUTO_REPORT = "auto_report"
ENTRY_SOURCES: frozenset[str] = frozenset(
    {SOURCE_MANUAL, SOURCE_DISCOVERY_CANDIDATE, SOURCE_REPORT, SOURCE_AUTO_REPORT})

_DESCRIPTIONS = {
    SHORTLIST: "Team shortlist: candidates the evidence supports enough for further work.",
    WATCHLIST: "Team watchlist: plausible names monitored for missing items and catalysts.",
}


@dataclass(frozen=True)
class AddResult:
    entry: WatchlistEntry
    created: bool


async def _find(session: Any, name: str) -> Watchlist | None:
    return (await session.execute(
        select(Watchlist).where(Watchlist.name == name, Watchlist.owner.is_(None))
    )).scalars().first()


async def ensure_team_list(session: Any, name: str) -> Watchlist:
    """Get or create the team list ``name`` (``Shortlist`` or ``Watchlist``)."""
    if name not in TEAM_LISTS:
        raise ValueError(f"{name!r} is not a team list; use one of {TEAM_LISTS}")
    found = await _find(session, name)
    if found is not None:
        return found
    row = Watchlist(id=uuid.uuid4(), name=name, owner=None,
                    description=_DESCRIPTIONS[name], is_active=True)
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        found = await _find(session, name)
        if found is None:  # pragma: no cover - the row that collided vanished
            raise
        return found
    return row


async def ensure_team_lists(session: Any) -> list[Watchlist]:
    return [await ensure_team_list(session, name) for name in TEAM_LISTS]


async def add_entry(
    session: Any, watchlist: Watchlist, company_id: uuid.UUID, *,
    source: str = SOURCE_MANUAL, note: str | None = None,
    added_by_hash: str | None = None,
) -> AddResult:
    """Add ``company_id`` to ``watchlist``; the existing entry if it is already there."""
    if source not in ENTRY_SOURCES:
        raise ValueError(f"{source!r} is not an entry source; use one of "
                         f"{sorted(ENTRY_SOURCES)}")
    existing = (await session.execute(select(WatchlistEntry).where(
        WatchlistEntry.watchlist_id == watchlist.id,
        WatchlistEntry.company_id == company_id))).scalars().first()
    if existing is not None:
        return AddResult(existing, False)
    entry = WatchlistEntry(
        id=uuid.uuid4(), watchlist_id=watchlist.id, company_id=company_id,
        note=(note or "").strip()[:500] or None, source=source,
        added_by_hash=added_by_hash)
    try:
        async with session.begin_nested():
            session.add(entry)
            await session.flush()
    except IntegrityError:
        again = (await session.execute(select(WatchlistEntry).where(
            WatchlistEntry.watchlist_id == watchlist.id,
            WatchlistEntry.company_id == company_id))).scalars().first()
        if again is None:  # pragma: no cover
            raise
        return AddResult(again, False)
    return AddResult(entry, True)


async def add_to_team_list(
    session: Any, list_name: str, company_id: uuid.UUID, *,
    source: str, note: str | None = None, added_by_hash: str | None = None,
) -> AddResult:
    """Idempotent: ensure the team list, then add the company once."""
    watchlist = await ensure_team_list(session, list_name)
    return await add_entry(session, watchlist, company_id, source=source, note=note,
                           added_by_hash=added_by_hash)


__all__ = [
    "ENTRY_SOURCES",
    "SHORTLIST",
    "SOURCE_AUTO_REPORT",
    "SOURCE_DISCOVERY_CANDIDATE",
    "SOURCE_MANUAL",
    "SOURCE_REPORT",
    "TEAM_LISTS",
    "WATCHLIST",
    "AddResult",
    "add_entry",
    "add_to_team_list",
    "ensure_team_list",
    "ensure_team_lists",
]
