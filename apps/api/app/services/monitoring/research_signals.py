"""Signals from the comparable-set records — V3.20 slice 9.4 (spec §3.11).

Six signal kinds the electricity vertical asked for, each derived from what the platform
already HOLDS — no vendor call, no network:

* ``earnings_released`` — a filed quarter newer than any quarter the previous research
  record held (both records must hold an earnings track record, so a first read is never
  a flood of "new" quarters).
* ``consensus_revision`` — the dated expectation for the SAME quarter differs between the
  previous and the newest record (a different as-of date and a different EPS or revenue
  average). The values, sources and dates are in ``detail``.
* ``passive_ownership_change`` — the share of shares outstanding held by US-registered
  funds moved by at least ``PASSIVE_CHANGE_PP`` percentage points between records.
* ``relationship_change`` — a customer or a parent present in one record and absent from
  the other, compared only when BOTH records researched that group (a group that was not
  researched is unknown, never "lost").
* ``change_event`` — an event in the newest record's change-events block that the previous
  record did not hold, and an event classified from a held disclosure retrieved since the
  last run (same key, so the two paths cannot raise one event twice); and (V3.21.6) an
  event in a trade-press item naming the company, labelled "reported by trade press".
* ``competitor_capacity_announcement`` — a same-layer peer researched AFTER this company's
  newest record whose disclosed capacity (same unit) rose by at least
  ``CAPACITY_RISE_MIN`` against the peer's own previous record.

``consensus_revision`` is only as current as the expectations the platform holds; while no
estimates vendor is configured it raises nothing, and says nothing about why.

Every summary says WHAT changed. None says what it means — the module docstring of
``detector`` explains why. Nothing here schedules anything (Open Decision #15).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.monitoring.detector import (
    KIND_CHANGE_EVENT,
    KIND_COMPETITOR_CAPACITY,
    KIND_CONSENSUS_REVISION,
    KIND_EARNINGS_RELEASED,
    KIND_PASSIVE_OWNERSHIP_CHANGE,
    KIND_RELATIONSHIP_CHANGE,
    DetectedSignal,
    signal_key_for,
)

logger = logging.getLogger(__name__)

#: Percentage points of shares outstanding held by funds.
PASSIVE_CHANGE_PP = Decimal("0.5")
#: A same-unit capacity reading must rise by at least this fraction.
CAPACITY_RISE_MIN = Decimal("0.05")
#: Same-layer peers looked at per pass.
MAX_PEERS = 10

_RESEARCHED = frozenset({"disclosed", "not_disclosed"})


def _blocks(record_json: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict((record_json or {}).get("blocks") or {})


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _signal(kind: str, company_id: uuid.UUID, run_id: uuid.UUID | None, summary: str,
            key_parts: Sequence[str | None], detail: dict[str, Any],
            source_ref: str | None = None) -> DetectedSignal:
    return DetectedSignal(
        kind=kind, signal_key=signal_key_for(kind, str(company_id), *key_parts),
        summary=summary[:1000], company_id=company_id, relates_to_run_id=run_id,
        source_ref=source_ref, detail=detail)


# ── earnings + consensus ──────────────────────────────────────────────────── #

def earnings_signals(prev: Mapping[str, Any], cur: Mapping[str, Any], *,
                     company_id: uuid.UUID, run_id: uuid.UUID | None
                     ) -> list[DetectedSignal]:
    old = prev.get("financial_capacity.earnings_track_record") or {}
    new = cur.get("financial_capacity.earnings_track_record") or {}
    if old.get("state") != "held" or new.get("state") != "held":
        return []
    old_q = {q.get("period"): q for q in old.get("quarters") or [] if q.get("period")}
    newest_old_end = max((q.get("period_end") or "" for q in old_q.values()), default="")
    out: list[DetectedSignal] = []
    for q in new.get("quarters") or []:
        period = q.get("period")
        if not period:
            continue
        before = old_q.get(period)
        if before is None:
            if (q.get("period_end") or "") > newest_old_end:
                out.append(_signal(
                    KIND_EARNINGS_RELEASED, company_id, run_id,
                    f"Filed results for {period} are held that the previous research "
                    "record did not hold.",
                    (period, q.get("period_end")),
                    {"period": period, "period_end": q.get("period_end"),
                     "report_date": q.get("report_date"), "revenue": q.get("revenue"),
                     "revenue_currency": q.get("revenue_currency"),
                     "revenue_source_url": q.get("revenue_source_url"),
                     "eps_diluted": q.get("eps_diluted"),
                     "eps_source_url": q.get("eps_source_url")}))
            continue
        a, b = before.get("expectation") or {}, q.get("expectation") or {}
        if not a or not b or a.get("as_of") == b.get("as_of"):
            continue
        moved = [k for k in ("eps_avg", "revenue_avg")
                 if _dec(a.get(k)) is not None and _dec(b.get(k)) is not None
                 and _dec(a.get(k)) != _dec(b.get(k))]
        if not moved:
            continue
        out.append(_signal(
            KIND_CONSENSUS_REVISION, company_id, run_id,
            f"The held expectation for {period} differs: {a.get('source')} as of "
            f"{a.get('as_of')} against {b.get('source')} as of {b.get('as_of')} "
            f"({', '.join(m.replace('_avg', ' average') for m in moved)}).",
            (period, a.get("as_of"), b.get("as_of")),
            {"period": period, "fields": moved,
             "before": {k: a.get(k) for k in ("source", "as_of", "eps_avg", "revenue_avg")},
             "after": {k: b.get(k) for k in ("source", "as_of", "eps_avg", "revenue_avg")}}))
    return out


# ── passive ownership ─────────────────────────────────────────────────────── #

def passive_signals(prev: Mapping[str, Any], cur: Mapping[str, Any], *,
                    company_id: uuid.UUID, run_id: uuid.UUID | None
                    ) -> list[DetectedSignal]:
    old = prev.get("valuation_context.passive_ownership") or {}
    new = cur.get("valuation_context.passive_ownership") or {}
    if old.get("state") != "held" or new.get("state") != "held":
        return []
    a = _dec(old.get("pct_of_shares_outstanding"))
    b = _dec(new.get("pct_of_shares_outstanding"))
    if a is None or b is None or abs(b - a) < PASSIVE_CHANGE_PP:
        return []
    return [_signal(
        KIND_PASSIVE_OWNERSHIP_CHANGE, company_id, run_id,
        f"Shares held by US-registered funds moved from {a}% of shares outstanding "
        f"({old.get('period')}) to {b}% ({new.get('period')}).",
        (str(old.get("period")), str(new.get("period")), str(b)),
        {"before": {"pct": float(a), "period": old.get("period"),
                    "fund_count": old.get("fund_count")},
         "after": {"pct": float(b), "period": new.get("period"),
                   "fund_count": new.get("fund_count")},
         "change_pp": float(b - a), "threshold_pp": float(PASSIVE_CHANGE_PP),
         "source_urls": list(new.get("source_urls") or [])})]


# ── relationships ─────────────────────────────────────────────────────────── #

def _names(rows: Sequence[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for r in rows or []:
        name = str(r.get("counterparty_name") or "").strip()
        if name:
            out.setdefault(name.casefold(), r)
    return out


def relationship_signals(prev: Mapping[str, Any], cur: Mapping[str, Any], *,
                         company_id: uuid.UUID, run_id: uuid.UUID | None
                         ) -> list[DetectedSignal]:
    old = prev.get("business_model.relationships") or {}
    new = cur.get("business_model.relationships") or {}
    if old.get("state") != "ok" or new.get("state") != "ok":
        return []
    pairs: list[tuple[str, Any, Any]] = []
    oc, nc = old.get("customers") or {}, new.get("customers") or {}
    if oc.get("state") in _RESEARCHED and nc.get("state") in _RESEARCHED:
        pairs.append(("customer", oc.get("rows"), nc.get("rows")))
    op, np_ = old.get("parent_and_group") or {}, new.get("parent_and_group") or {}
    if op.get("state") in _RESEARCHED and np_.get("state") in _RESEARCHED:
        pairs.append(("parent",
                      [*(op.get("direct_parents") or []), *(op.get("ultimate_parents") or [])],
                      [*(np_.get("direct_parents") or []),
                       *(np_.get("ultimate_parents") or [])]))
    out: list[DetectedSignal] = []
    for role, before_rows, after_rows in pairs:
        before, after = _names(before_rows), _names(after_rows)
        for change, keys, rows in (("added", after.keys() - before.keys(), after),
                                   ("no_longer_held", before.keys() - after.keys(), before)):
            for key in sorted(keys):
                row = rows[key]
                name = str(row.get("counterparty_name"))
                verb = ("is now held as" if change == "added"
                        else "is no longer held as")
                out.append(_signal(
                    KIND_RELATIONSHIP_CHANGE, company_id, run_id,
                    f"{name} {verb} a {role} (compared with the previous research "
                    "record).",
                    (role, change, key),
                    {"role": role, "change": change, "counterparty_name": name,
                     "counterparty_entity_id": row.get("counterparty_entity_id"),
                     "source": row.get("source"), "source_url": row.get("source_url"),
                     "as_of": row.get("as_of")}))
    return out


# ── change events ─────────────────────────────────────────────────────────── #

def _event_parts(e: Mapping[str, Any]) -> tuple[str | None, ...]:
    if e.get("trigger_type") == "insider_cluster_buy":
        return ("insider_cluster_buy", e.get("start_date"), e.get("end_date"))
    return (e.get("trigger_type"), e.get("date"), e.get("source_url") or e.get("title"))


def _event_signal(e: Mapping[str, Any], *, company_id: uuid.UUID,
                  run_id: uuid.UUID | None, origin: str) -> DetectedSignal:
    label = e.get("label") or str(e.get("trigger_type") or "").replace("_", " ")
    when = e.get("date") or e.get("end_date")
    title = f": {e.get('title')}" if e.get("title") else ""
    detail = {"origin": origin, **{k: e.get(k) for k in (
        "trigger_type", "label", "date", "start_date", "end_date", "title",
        "source_url", "document_type", "rule", "distinct_insiders", "purchases")
        if e.get(k) is not None}}
    summary = f"{label} ({when}){title}"
    if e.get("source_kind") == "trade_press":
        # V3.21.6 — an outlet reporting it, never the issuer disclosing it.
        outlet = f" in {e.get('outlet')}" if e.get("outlet") else ""
        summary = f"{label}, reported by trade press{outlet} ({when}){title}"
        detail.update({k: e.get(k) for k in ("source_kind", "reported_as", "outlet",
                                             "weight") if e.get(k) is not None})
    return _signal(KIND_CHANGE_EVENT, company_id, run_id, summary, _event_parts(e), detail)


def change_event_signals(prev: Mapping[str, Any], cur: Mapping[str, Any], *,
                         company_id: uuid.UUID, run_id: uuid.UUID | None
                         ) -> list[DetectedSignal]:
    old = prev.get("growth_and_catalysts.change_events")
    new = cur.get("growth_and_catalysts.change_events") or {}
    if old is None:  # the previous run did not look; everything would look new
        return []
    def every(block: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        return [*(block.get("events") or []), *(block.get("insider_clusters") or []),
                *(block.get("trade_press_events") or [])]
    seen = {_event_parts(e) for e in every(old)}
    return [_event_signal(e, company_id=company_id, run_id=run_id, origin="research_record")
            for e in every(new) if _event_parts(e) not in seen]


def fresh_event_signals(classified: Mapping[str, Any], *, cutoff: datetime | None,
                        company_id: uuid.UUID, run_id: uuid.UUID | None
                        ) -> list[DetectedSignal]:
    """Events classified from held disclosures dated after the last run."""
    floor = cutoff.date().isoformat() if cutoff else ""
    return [_event_signal(e, company_id=company_id, run_id=run_id, origin="held_disclosure")
            for e in classified.get("events") or [] if (e.get("date") or "") > floor]


def fresh_trade_press_signals(press: Mapping[str, Any], *, cutoff: datetime | None,
                              company_id: uuid.UUID, run_id: uuid.UUID | None
                              ) -> list[DetectedSignal]:
    """V3.21.6 — events in trade-press items naming the company, dated after the last
    run. Labelled "reported by trade press"; never "disclosed"."""
    floor = cutoff.date().isoformat() if cutoff else ""
    return [_event_signal(e, company_id=company_id, run_id=run_id, origin="trade_press")
            for e in press.get("events") or [] if (e.get("date") or "") > floor]


# ── competitor capacity ───────────────────────────────────────────────────── #

def capacity_rises(prev_json: Mapping[str, Any] | None, cur_json: Mapping[str, Any] | None
                   ) -> list[dict[str, Any]]:
    """Same-unit capacity readings that rose by at least ``CAPACITY_RISE_MIN``."""
    def readings(record: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
        block = _blocks(record).get("operations.real_asset") or {}
        out: dict[str, Mapping[str, Any]] = {}
        for r in (block.get("capacity") or {}).get("readings") or []:
            unit = str(r.get("unit") or "").strip()
            if unit and _dec(r.get("value")) is not None:
                out.setdefault(unit, r)
        return out

    before, after = readings(prev_json), readings(cur_json)
    rises = []
    for unit, r in after.items():
        old = before.get(unit)
        a, b = _dec((old or {}).get("value")), _dec(r.get("value"))
        if a is None or b is None or a <= 0 or (b - a) / a < CAPACITY_RISE_MIN:
            continue
        rises.append({"unit": unit, "before": float(a), "after": float(b),
                      "before_as_of": (old or {}).get("as_of"), "as_of": r.get("as_of"),
                      "source_url": r.get("source_url")})
    return rises


# ── the pass ──────────────────────────────────────────────────────────────── #

async def _newest_records(session: Any, company_id: uuid.UUID, n: int = 2) -> list[Any]:
    from sqlalchemy import select

    from app.models.research_record import CompanyResearchRecord as R

    stmt = (select(R).where(R.company_id == company_id)
            .order_by(R.as_of.desc(), R.created_at.desc()).limit(n))
    return list((await session.execute(stmt)).scalars().all())


async def _peer_capacity(session: Any, subject: Any, *, run_id: uuid.UUID | None
                         ) -> list[DetectedSignal]:
    from app.services.research_records import comparable_set

    if not subject.layer:
        return []
    out: list[DetectedSignal] = []
    peers = await comparable_set(session, subject.layer,
                                 exclude_company_id=subject.company_id, limit=MAX_PEERS)
    for peer in peers:
        if peer.created_at is None or subject.created_at is None \
                or peer.created_at <= subject.created_at:
            continue  # researched before this company's latest conclusion
        history = await _newest_records(session, peer.company_id)
        if len(history) < 2:
            continue
        for rise in capacity_rises(history[1].record_json, history[0].record_json):
            name = peer.ticker or "a same-layer peer"
            out.append(_signal(
                KIND_COMPETITOR_CAPACITY, subject.company_id, run_id,
                f"{name} ({subject.layer}) disclosed capacity of {rise['after']:g} "
                f"{rise['unit']} against {rise['before']:g} at its previous research.",
                (str(peer.company_id), rise["unit"], str(rise["after"])),
                {"peer_company_id": str(peer.company_id), "peer_ticker": peer.ticker,
                 "layer": subject.layer, **rise},
                source_ref=str(peer.id)))
    return out


async def research_signals(
    session: Any, *, company_id: uuid.UUID, cutoff: datetime | None,
    prior_run_id: uuid.UUID | None, today: date | None = None,
) -> list[DetectedSignal]:
    """Every kind above for one company, de-duplicated by key. Never raises."""
    signals: list[DetectedSignal] = []
    try:
        async with session.begin_nested():
            records = await _newest_records(session, company_id)
            if len(records) == 2:
                prev, cur = _blocks(records[1].record_json), _blocks(records[0].record_json)
                run_id = records[0].research_run_id or prior_run_id
                for fn in (earnings_signals, passive_signals, relationship_signals,
                           change_event_signals):
                    signals.extend(fn(prev, cur, company_id=company_id, run_id=run_id))
            if records:
                signals.extend(await _peer_capacity(session, records[0],
                                                    run_id=prior_run_id))
    except Exception:  # noqa: BLE001 - one detector never costs the pass
        logger.warning("research-record signals failed")
    try:
        from app.services.discovery.change_events import events_from_documents
        from app.services.pipeline.document_coverage import current_inventory

        async with session.begin_nested():
            inventory = await current_inventory(session, company_id)
        classified = events_from_documents(inventory or [], today=today or date.today())
        signals.extend(fresh_event_signals(classified, cutoff=cutoff,
                                           company_id=company_id, run_id=prior_run_id))
    except Exception:  # noqa: BLE001
        logger.warning("held-disclosure change events failed")
    try:
        from app.core.config import settings as cfg

        if getattr(cfg, "v3_trade_press_feeds_enabled", False):
            from app.services.discovery.change_events import events_from_trade_press
            from app.services.trade_press.store import recent_mentions

            day = today or date.today()
            items = await recent_mentions(session, company_id, today=day, limit=50)
            if items:
                signals.extend(fresh_trade_press_signals(
                    events_from_trade_press(items, today=day), cutoff=cutoff,
                    company_id=company_id, run_id=prior_run_id))
    except Exception:  # noqa: BLE001
        logger.warning("trade-press change events failed")
    unique: dict[str, DetectedSignal] = {}
    for s in signals:
        unique.setdefault(s.signal_key, s)
    return list(unique.values())


__all__ = [
    "CAPACITY_RISE_MIN",
    "PASSIVE_CHANGE_PP",
    "capacity_rises",
    "change_event_signals",
    "earnings_signals",
    "fresh_event_signals",
    "fresh_trade_press_signals",
    "passive_signals",
    "relationship_signals",
    "research_signals",
]
