"""Portfolio review — V3.20 slice 12.2 (ADR-100), deterministic.

A research review of the user's own hand-entered list. For each holding it compares the
research record the platform held at the ENTRY DATE with the newest one, and gathers the
monitoring signals observed since entry, then reports the thesis status:

* ``insufficient_evidence`` — the holding is not linked to a researched company, or there
  is no record, or nothing has been researched or observed since entry;
* ``intact`` — researched again since entry and no weakening evidence found;
* ``weakened`` — at least one weakening item;
* ``broken`` — at least ``BROKEN_MIN_ITEMS`` weakening items of at least
  ``BROKEN_MIN_KINDS`` different kinds.

Weakening items (``RULES_VERSION``): a customer or parent no longer held; a consensus
revision with a lower EPS average; fund ownership down by the 9.4 threshold; two or more
quarters below the held EPS expectation in the newest record; a same-layer competitor's
capacity rise; a lower EV/EBIT than at entry by ``MULTIPLE_DRIFT``. Each item cites its
source. Everything else that changed is listed as context, not counted.

WHAT IT NEVER DOES (ADR-100 §3): propose buying, selling, adding, trimming or
rebalancing; give a size, weight or order; emit a rating token. Its proposals are
research changes only — re-run the analysis, investigate a named change. The ADR-098
verdict at entry and now (slice 9.2) are read from each record's ``rubric_json``; a record
without one shows ``not_available``. A lower verdict now than at entry is a weakening item.
The labels appear only at ``holdings[*].{entry,current}_verdict.recommendation``, which
the gate allows by path (``safety_terms.PORTFOLIO_REVIEW_ALLOWANCES``).

PRIVATE DATA (ADR-100 §4): the review reads the user's positions and PUBLIC research
records; it writes nothing, sends nothing outside the process and calls no model.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

RULES_VERSION = "2026-11-16.3"
BROKEN_MIN_ITEMS = 3
BROKEN_MIN_KINDS = 2
#: A lower EV/EBIT than at entry by this fraction counts as a weakening item.
MULTIPLE_DRIFT = Decimal("0.25")
#: A newest record older than this proposes a re-run.
STALE_RECORD_DAYS = 90

INTACT, WEAKENED, BROKEN, INSUFFICIENT = ("intact", "weakened", "broken",
                                          "insufficient_evidence")
VERDICT_NOT_AVAILABLE = {"state": "not_available",
                         "reason": "no computed verdict is held for this record"}
#: Order of the ADR-098 labels, lowest first, for the "verdict lowered" item.
_VERDICT_RANK = {"PASS": 0, "WATCHLIST": 1, "SHORTLIST": 2}


def verdict_of(record: Any) -> dict[str, Any]:
    from app.services.research_verdict import summary

    if record is None:
        return dict(VERDICT_NOT_AVAILABLE)
    out = summary(getattr(record, "rubric_json", None))
    if out is None:
        return dict(VERDICT_NOT_AVAILABLE)
    return {**out, "record_as_of": record.as_of.isoformat()}
NOTE = ("A research review of a list you entered: what changed in the evidence since each "
        "entry date, with sources. It proposes research, never a trade, a size or a rating.")


def _dec(v: Any) -> Decimal | None:
    try:
        return None if v is None or isinstance(v, bool) else Decimal(str(v))
    except ArithmeticError:
        return None


def _item(kind: str, summary: str, source_url: Any = None, **detail: Any) -> dict[str, Any]:
    return {"kind": kind, "summary": summary, "source_url": source_url,
            "detail": {k: v for k, v in detail.items() if v is not None}}


def weakening_items(entry: Any, current: Any, signals: Sequence[Any], *,
                    company_id: uuid.UUID) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(weakening, context) — pure over two records and the signals since entry."""
    from app.services.monitoring import research_signals as rs

    weak: list[dict[str, Any]] = []
    context: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    if entry is not None and current is not None and entry.id != current.id:
        prev = rs._blocks(entry.record_json)  # noqa: SLF001 - same package family
        cur = rs._blocks(current.record_json)  # noqa: SLF001
        for fn in (rs.earnings_signals, rs.passive_signals, rs.relationship_signals,
                   rs.change_event_signals):
            for s in fn(prev, cur, company_id=company_id, run_id=None):
                seen_keys.add(s.signal_key)
                _classify(s.kind, s.summary, s.detail, weak, context)
        streak = ((cur.get("financial_capacity.earnings_track_record") or {})
                  .get("eps_streak") or {})
        if streak.get("direction") == "below" and (streak.get("quarters") or 0) >= 2:
            weak.append(_item("results_below_expectations",
                              f"{streak['quarters']} consecutive quarters below the held "
                              "EPS expectation in the newest record"))
        a, b = _dec(entry.ev_to_ebit), _dec(current.ev_to_ebit)
        if a is not None and b is not None and a > 0:
            drift = (b - a) / a
            row = _item("multiple_drift", f"EV/EBIT {a} at entry record, {b} now",
                        entry_as_of=str(entry.as_of), current_as_of=str(current.as_of),
                        change=float(round(drift, 3)))
            (weak if drift <= -MULTIPLE_DRIFT else context).append(row)
        _position_drift(entry, current, weak, context)
        before, after = verdict_of(entry), verdict_of(current)
        if before.get("state") == after.get("state") == "available":
            a_rank = _VERDICT_RANK[before["recommendation"]]
            b_rank = _VERDICT_RANK[after["recommendation"]]
            if a_rank != b_rank:
                row = _item("verdict_change",
                            "The computed research verdict is "
                            f"{'lower' if b_rank < a_rank else 'higher'} than at the entry "
                            "record", entry_as_of=str(entry.as_of),
                            current_as_of=str(current.as_of))
                (weak if b_rank < a_rank else context).append(row)
    for sig in signals:
        if sig.signal_key in seen_keys:
            continue
        _classify(sig.kind, sig.summary, sig.detail_json or {}, weak, context,
                  observed_at=sig.observed_at)
    return weak, context


#: The price against the computed range, as an order (slice 8.6 wiring).
_POSITION_RANK = {"undervalued": 0, "in_line": 1, "overvalued": 2}
_POSITION_WORDS = {"undervalued": "below", "in_line": "within", "overvalued": "above"}


def _position_drift(entry: Any, current: Any, weak: list, context: list) -> None:
    """A price that moved up through the computed range is a weakening item; down is
    context. The ADR-099 terms are not repeated here: the summary says where the price
    sits against the range."""
    def pos(rec: Any) -> str | None:
        v = ((getattr(rec, "record_json", None) or {}).get("valuation") or {})
        p = v.get("relative_position")
        return p if p in _POSITION_RANK else None

    a, b = pos(entry), pos(current)
    if a is None or b is None or a == b:
        return
    row = _item("valuation_range_position",
                f"The price was {_POSITION_WORDS[a]} the computed range at the entry record "
                f"and is {_POSITION_WORDS[b]} it now", entry_as_of=str(entry.as_of),
                current_as_of=str(current.as_of))
    (weak if _POSITION_RANK[b] > _POSITION_RANK[a] else context).append(row)


def _classify(kind: str, summary: str, detail: Mapping[str, Any], weak: list, context: list,
              observed_at: Any = None) -> None:
    url = detail.get("source_url") or detail.get("revenue_source_url") or next(
        iter(detail.get("source_urls") or []), None)
    row = _item(kind, summary, url, observed_at=str(observed_at) if observed_at else None)
    negative = (
        (kind == "relationship_change" and detail.get("change") == "no_longer_held")
        or (kind == "passive_ownership_change" and (detail.get("change_pp") or 0) < 0)
        or kind == "competitor_capacity_announcement"
        or (kind == "consensus_revision" and _lower_eps(detail))
    )
    (weak if negative else context).append(row)


def _lower_eps(detail: Mapping[str, Any]) -> bool:
    a = _dec((detail.get("before") or {}).get("eps_avg"))
    b = _dec((detail.get("after") or {}).get("eps_avg"))
    return a is not None and b is not None and b < a


def status_for(weak: Sequence[Mapping[str, Any]], *, researched_since: bool,
               observed_since: bool) -> str:
    if len(weak) >= BROKEN_MIN_ITEMS and len({w["kind"] for w in weak}) >= BROKEN_MIN_KINDS:
        return BROKEN
    if weak:
        return WEAKENED
    return INTACT if researched_since else INSUFFICIENT if not observed_since else INTACT


def proposals(status: str, weak: Sequence[Mapping[str, Any]], *, current: Any,
              today: date) -> list[str]:
    """Research changes only (ADR-100 §3)."""
    out: list[str] = []
    if current is None:
        out.append("Research this company: no research record is held.")
        return out
    if (today - current.as_of).days > STALE_RECORD_DAYS or status in (WEAKENED, BROKEN):
        out.append("Re-run the research analysis to update the record.")
    for kind in sorted({w["kind"] for w in weak}):
        out.append(f"Investigate the change: {kind.replace('_', ' ')}.")
    return out


async def review_position(session: Any, position: Any, *, today: date | None = None
                          ) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models.monitoring import MonitoringSignal
    from app.models.research_record import CompanyResearchRecord as R

    today = today or date.today()
    base = {"position_id": str(position.id), "ticker": position.ticker,
            "exchange": position.exchange, "entry_date": position.entry_date.isoformat(),
            "entry_verdict": dict(VERDICT_NOT_AVAILABLE),
            "current_verdict": dict(VERDICT_NOT_AVAILABLE)}
    if position.company_id is None:
        return {**base, "thesis_status": INSUFFICIENT,
                "reason": "not linked to a researched company",
                "weakening": [], "context": [],
                "proposed_research": ["Research this company: it is not in the platform yet."]}
    records = list((await session.execute(
        select(R).where(R.company_id == position.company_id)
        .order_by(R.as_of, R.created_at))).scalars().all())
    current = records[-1] if records else None
    at_entry = [r for r in records if r.as_of <= position.entry_date]
    entry = at_entry[-1] if at_entry else (records[0] if records else None)
    since = datetime.combine(position.entry_date, time.min, tzinfo=timezone.utc)
    signals = list((await session.execute(
        select(MonitoringSignal).where(MonitoringSignal.company_id == position.company_id,
                                       MonitoringSignal.observed_at >= since)
        .order_by(MonitoringSignal.observed_at.desc()).limit(50))).scalars().all())
    weak, context = weakening_items(entry, current, signals, company_id=position.company_id)
    researched_since = current is not None and entry is not None and (
        current.id != entry.id and current.as_of > position.entry_date)
    status = (INSUFFICIENT if current is None else
              status_for(weak, researched_since=researched_since,
                         observed_since=bool(signals)))
    reason = None
    if current is None:
        reason = "no research record is held for this company"
    elif status == INSUFFICIENT:
        reason = "nothing has been researched or observed since the entry date"
    return {
        **base,
        "entry_verdict": verdict_of(entry),
        "current_verdict": verdict_of(current),
        "thesis_status": status,
        "reason": reason,
        "entry_record": None if entry is None else {
            "as_of": entry.as_of.isoformat(), "research_run_id": str(entry.research_run_id)
            if entry.research_run_id else None,
            "basis": "at or before entry" if entry.as_of <= position.entry_date
            else "first record after entry"},
        "current_record": None if current is None else {
            "as_of": current.as_of.isoformat(), "research_run_id": str(current.research_run_id)
            if current.research_run_id else None},
        "weakening": weak,
        "context": context,
        "proposed_research": proposals(status, weak, current=current, today=today),
    }


async def review_portfolio(session: Any, positions: Sequence[Any], *,
                           today: date | None = None) -> dict[str, Any]:
    holdings = [await review_position(session, p, today=today) for p in positions]
    counts: dict[str, int] = {}
    for h in holdings:
        counts[h["thesis_status"]] = counts.get(h["thesis_status"], 0) + 1
    return {"rules_version": RULES_VERSION, "holdings": holdings, "status_counts": counts,
            "thresholds": {"broken_min_items": BROKEN_MIN_ITEMS,
                           "broken_min_kinds": BROKEN_MIN_KINDS,
                           "multiple_drift": float(MULTIPLE_DRIFT),
                           "stale_record_days": STALE_RECORD_DAYS},
            "note": NOTE}


def gate_review(review: dict[str, Any]) -> dict[str, Any]:
    """The safety gate with the review's path allowances. A hit outside them withholds
    the verdicts (the only typed labels) and records why; nothing else is altered."""
    from app.services import safety_terms

    # The portfolio's name is the user's own words, shown back to them; it is not output.
    hits = safety_terms.scan_value({k: v for k, v in review.items() if k != "name"},
                                   allowances=safety_terms.PORTFOLIO_REVIEW_ALLOWANCES)
    if not hits:
        return review
    for h in review.get("holdings") or []:
        h["entry_verdict"] = h["current_verdict"] = {
            "state": "withheld", "reason": "the safety scan flagged this review"}
    review["safety_hits"] = len(hits)
    return review


__all__ = ["BROKEN", "INSUFFICIENT", "INTACT", "RULES_VERSION", "WEAKENED", "gate_review",
           "proposals", "review_portfolio", "review_position", "status_for", "verdict_of",
           "weakening_items"]
