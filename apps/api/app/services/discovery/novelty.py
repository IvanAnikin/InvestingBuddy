"""Anti-convergence — V3.20 slice 6.4 (spec §3.6).

Three parts, each stated in the run's output, never silent:

1. **The store** (``discovery_seen``). Every issuer a discovery run evaluates is recorded
   by ``EXCHANGE:TICKER`` — first / last seen, times seen and shortlisted, the diversity
   cell. The research pipeline stamps ``last_researched_at`` and the newest change-event
   date it knew of (slice 6.2).
2. **The novelty gate.** A name researched within ``NOVELTY_WINDOW_DAYS`` is held back
   from the shortlist unless one of its held disclosures carries a change event dated
   AFTER the last research's newest event: re-researching an unchanged company is the
   convergence the mandate warns about. Held names are listed with the date they were
   researched; they are not deleted, excluded or scored down.
3. **The diversity quota.** Within one shortlist, at most ``MAX_PER_CELL`` names per cell
   of layer × demand driver × regulatory regime. The quota REORDERS and never shrinks:
   names over a cell's quota move behind every within-quota name and still fill any
   slot left under the cap. It applies only to names with an electricity-layer hint, so
   a thesis outside the vertical ranks exactly as before.

The layer here is a HINT from the lead's own words (name, the reason it was surfaced,
the registry's industry) through the taxonomy anchors — an ordering signal, never a
classification claim (that is slice 5.2's job, on held disclosures).
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

NOVELTY_WINDOW_DAYS = 90
MAX_PER_CELL = 2
UNKNOWN = "unknown"
NO_DRIVER = "none"

T = TypeVar("T")


def issuer_key(exchange: str | None, ticker: str | None) -> str | None:
    if not exchange or not ticker:
        return None
    return f"{str(exchange).strip().upper()}:{str(ticker).strip().upper()}"[:64]


def layer_hint(*texts: str | None) -> str | None:
    """The layer with the most anchor hits in the lead's own words; ``None`` when none."""
    from app.services.electricity.taxonomy import match_layers

    text = " ".join(t for t in texts if t)
    hits = match_layers(text)
    if not hits:
        return None
    counts = Counter(h.layer for h in hits)
    best = max(counts.values())
    # Ties resolve in taxonomy order (match_layers returns hits in that order).
    return next(h.layer for h in hits if counts[h.layer] == best)


def diversity_cell(*, layer: str | None, driver: str | None,
                   regime: str | None) -> dict[str, str] | None:
    """``{layer, driver, regime}``, or ``None`` outside the vertical (no layer hint)."""
    if not layer:
        return None
    return {"layer": layer, "driver": driver or NO_DRIVER, "regime": regime or UNKNOWN}


def apply_diversity_quota(
    ordered: Sequence[T],
    cell_of: Callable[[T], Mapping[str, str] | None],
    *,
    max_per_cell: int = MAX_PER_CELL,
) -> tuple[list[T], list[T]]:
    """(reordered, moved back). Stable: within-quota names keep their order, then the
    names over a cell's quota in their order. ``max_per_cell`` < 1 turns it off."""
    if max_per_cell < 1:
        return list(ordered), []
    seen: Counter[tuple[str, ...]] = Counter()
    kept: list[T] = []
    moved: list[T] = []
    for item in ordered:
        cell = cell_of(item)
        if cell is None:
            kept.append(item)
            continue
        key = (cell.get("layer", ""), cell.get("driver", ""), cell.get("regime", ""))
        seen[key] += 1
        (kept if seen[key] <= max_per_cell else moved).append(item)
    return [*kept, *moved], moved


def novelty_decision(
    seen: Any | None, *, now: datetime, newest_event: date | None,
    window_days: int = NOVELTY_WINDOW_DAYS,
) -> dict[str, Any]:
    """Pure. ``{state: new | not_recent | new_event | held, ...}`` for one name."""
    researched = getattr(seen, "last_researched_at", None) if seen is not None else None
    if researched is None:
        return {"state": "new" if seen is None else "not_researched"}
    if researched.tzinfo is None:
        researched = researched.replace(tzinfo=timezone.utc)
    if now - researched > timedelta(days=window_days):
        return {"state": "not_recent", "last_researched_at": researched.isoformat()}
    known = getattr(seen, "last_event_date", None)
    if newest_event is not None and (known is None or newest_event > known):
        return {"state": "new_event", "last_researched_at": researched.isoformat(),
                "event_date": newest_event.isoformat()}
    return {"state": "held", "last_researched_at": researched.isoformat(),
            "rule": f"researched within {window_days} days and no change event since"}


# ── Store ───────────────────────────────────────────────────────────────── #


async def load_seen(session: Any, keys: Iterable[str]) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models.discovery_seen import DiscoverySeen

    wanted = sorted({k for k in keys if k})
    if not wanted:
        return {}
    async with session.begin_nested():
        rows = (await session.execute(
            select(DiscoverySeen).where(DiscoverySeen.issuer_key.in_(wanted))
        )).scalars().all()
    return {r.issuer_key: r for r in rows}


async def newest_event_date(session: Any, company_id: Any, *, today: date) -> date | None:
    """The newest change-event date in the company's held disclosures (6.2 rules)."""
    if company_id is None:
        return None
    from app.services.discovery.change_events import events_from_documents
    from app.services.pipeline.document_coverage import current_inventory

    cid = company_id if isinstance(company_id, uuid.UUID) else uuid.UUID(str(company_id))
    inventory = await current_inventory(session, cid)
    events = events_from_documents(inventory or [], today=today)["events"]
    dates = [date.fromisoformat(e["date"]) for e in events if e.get("date")]
    return max(dates) if dates else None


async def record_seen(
    session: Any,
    sightings: Iterable[Mapping[str, Any]],
    *,
    run_id: Any = None,
    now: datetime | None = None,
) -> int:
    """Upsert one row per sighting ``{key, company_id?, shortlisted, cell}``. Never raises."""
    from app.models.discovery_seen import DiscoverySeen

    now = now or datetime.now(timezone.utc)
    rows = {s["key"]: s for s in sightings if s.get("key")}
    if not rows:
        return 0
    try:
        existing = await load_seen(session, rows)
        async with session.begin_nested():
            for key, s in rows.items():
                row = existing.get(key)
                company_id = _uuid(s.get("company_id"))
                if row is None:
                    row = DiscoverySeen(id=uuid.uuid4(), issuer_key=key, first_seen_at=now,
                                        last_seen_at=now, times_seen=1, times_shortlisted=0)
                    session.add(row)
                else:
                    row.times_seen = (row.times_seen or 0) + 1
                    row.last_seen_at = now
                if company_id is not None:
                    row.company_id = company_id
                if s.get("shortlisted"):
                    row.times_shortlisted = (row.times_shortlisted or 0) + 1
                    row.last_shortlisted_at = now
                row.last_discovery_run_id = _uuid(run_id)
                row.cell_json = dict(s["cell"]) if s.get("cell") else None
            await session.flush()
        return len(rows)
    except Exception:  # noqa: BLE001 - the store is bookkeeping, never the run
        logger.warning("discovery_seen record failed")
        return 0


async def mark_researched(
    session: Any, company: Any, *, now: datetime | None = None,
    newest_event: date | None = None,
) -> bool:
    """Stamp ``last_researched_at`` (and the newest event known) for the company's
    listing. Creates the row for a company researched without discovery. Never raises."""
    from app.models.discovery_seen import DiscoverySeen

    key = issuer_key(getattr(company, "exchange", None), getattr(company, "ticker", None))
    if key is None:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        row = (await load_seen(session, [key])).get(key)
        async with session.begin_nested():
            if row is None:
                row = DiscoverySeen(id=uuid.uuid4(), issuer_key=key, first_seen_at=now,
                                    last_seen_at=now, times_seen=0, times_shortlisted=0)
                session.add(row)
            row.company_id = getattr(company, "id", None) or row.company_id
            row.last_researched_at = now
            row.last_event_date = newest_event
            await session.flush()
        return True
    except Exception:  # noqa: BLE001
        logger.warning("discovery_seen mark_researched failed")
        return False


def _uuid(value: Any) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


__all__ = [
    "MAX_PER_CELL",
    "NOVELTY_WINDOW_DAYS",
    "apply_diversity_quota",
    "diversity_cell",
    "issuer_key",
    "layer_hint",
    "load_seen",
    "mark_researched",
    "newest_event_date",
    "novelty_decision",
    "record_seen",
]
