"""The monitoring pass — V3.21.2 (spec §3, ADR-109).

WHAT IT DOES
============
For every company on an **active** watchlist (the team ``Shortlist`` and ``Watchlist``,
and any other active list), it calls ``detect_for_company`` and ``persist_signals``, and
adds two kinds read from the company's **latest research record**
(``company_research_records``):

* ``missing_information`` — a blocking gap the record's verdict still lists (a cap that
  forbids ``SHORTLIST``: an unscored or thin key pillar, no computed valuation range, low
  confidence or coverage). ``relates_to_question_key`` is the cap key. Keyed on the
  record, so the same gap is raised once per record; a NEW record that still lists it
  raises it again, because then the re-research did not close it.
* ``catalyst_window`` — a dated item in the record (an earnings report date, an event,
  an auction or permit date) that falls in the next ``CATALYST_WINDOW_DAYS`` days.
  Keyed on the item and its date, so it is raised once.

Both are raised at most once per key in ANY status: an acknowledged gap is not re-raised
every morning. Every summary says what the record holds, never what it means.

WHAT IT NEVER DOES
==================
It never starts research, paid or otherwise: a material signal is shown with a
"Re-run research" link for a human to follow (spec §11: proposed, never started). It
makes no network call. It is bounded: ``MAX_COMPANIES_PER_PASS`` companies, and
``MAX_SIGNALS_PER_PASS`` signals per company.

WHO RUNS IT
===========
The ``monitoring_pass`` durable job: daily through the periodic-task registry
(``scheduling.periodic``, flag ``V3_MONITORING_PASS_ENABLED``), or by hand through
``POST /api/v1/monitoring/run``. Nothing is written unless ``V3_MONITORING_ENABLED`` is on.
This module lives in the ``monitoring`` package so the guard test (nothing outside the
package calls the detector) stays meaningful.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.structured_logging import log_event
from app.models.monitoring import MonitoringSignal, Watchlist, WatchlistEntry
from app.models.research_record import CompanyResearchRecord
from app.services.jobs.worker import JobContext, JobOutcome
from app.services.monitoring.detector import (
    KIND_CATALYST_WINDOW,
    KIND_MISSING_INFORMATION,
    MAX_SIGNALS_PER_PASS,
    DetectedSignal,
    DetectionResult,
    detect_for_company,
    persist_signals,
    signal_key_for,
)
from app.services.scheduling.periodic import (
    DAILY,
    PeriodicTask,
    periodic_job_handler,
    register_periodic_task,
)

logger = logging.getLogger(__name__)

JOB_TYPE = "monitoring_pass"
RESULT_TYPE = "monitoring_pass"
TASK_KEY = "monitoring_pass"

#: Companies looked at per pass. A list longer than this is read oldest-entry first.
MAX_COMPANIES_PER_PASS = 200
#: A dated item counts as a catalyst window when it falls within this many days.
CATALYST_WINDOW_DAYS = 30

#: Date fields read from a record's blocks. Closed: a field not named here is not a date
#: this pass claims to understand.
_DATE_FIELDS: tuple[str, ...] = (
    "report_date", "next_report_date", "expected_date", "event_date", "catalyst_date",
    "auction_date", "permit_date", "decision_date", "scheduled_date", "date")
_LABEL_FIELDS: tuple[str, ...] = (
    "title", "label", "event_type", "trigger_type", "period", "name", "description")
_URL_FIELDS: tuple[str, ...] = ("source_url", "url", "revenue_source_url", "eps_source_url")
_WALK_DEPTH = 6
_WALK_ITEMS = 500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _day(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _clip(text: Any, n: int) -> str:
    return " ".join(str(text or "").split())[:n]


# ── record-derived signals (pure) ─────────────────────────────────────────── #


def missing_information_signals(record: Any, *, company_id: uuid.UUID
                                ) -> list[DetectedSignal]:
    """Blocking gaps the latest record's verdict still lists (its caps)."""
    rubric = getattr(record, "rubric_json", None)
    if not isinstance(rubric, Mapping):
        return []
    as_of = getattr(record, "as_of", None)
    out: list[DetectedSignal] = []
    for cap in rubric.get("caps") or []:
        if not isinstance(cap, Mapping) or not cap.get("key"):
            continue
        key = str(cap["key"])
        out.append(DetectedSignal(
            kind=KIND_MISSING_INFORMATION,
            signal_key=signal_key_for(KIND_MISSING_INFORMATION, str(company_id), key,
                                      str(record.id)),
            summary=_clip(
                f"The latest research record (as of {as_of}) still lists a blocking "
                f"gap: {cap.get('reason') or key}.", 1000),
            company_id=company_id,
            relates_to_question_key=key[:80],
            relates_to_run_id=getattr(record, "research_run_id", None),
            source_ref=f"company_research_record:{record.id}",
            detail={"cap_key": key, "reason": cap.get("reason"),
                    "record_id": str(record.id),
                    "record_as_of": as_of.isoformat() if as_of else None,
                    "watchlist_triggers": list(rubric.get("watchlist_triggers") or [])[:10]},
        ))
    return out


def _walk(node: Any, path: str, depth: int, out: list[tuple[str, Mapping[str, Any]]]
          ) -> None:
    if depth > _WALK_DEPTH or len(out) >= _WALK_ITEMS:
        return
    if isinstance(node, Mapping):
        out.append((path, node))
        for k, v in node.items():
            if isinstance(v, (Mapping, list)):
                _walk(v, f"{path}.{k}" if path else str(k), depth + 1, out)
    elif isinstance(node, list):
        for v in node[:100]:
            if isinstance(v, (Mapping, list)):
                _walk(v, path, depth + 1, out)


def catalyst_window_signals(record: Any, *, company_id: uuid.UUID, today: date,
                            days: int = CATALYST_WINDOW_DAYS) -> list[DetectedSignal]:
    """Dated items in the record that fall in ``[today, today + days]``."""
    blocks = ((getattr(record, "record_json", None) or {}).get("blocks") or {})
    nodes: list[tuple[str, Mapping[str, Any]]] = []
    _walk(blocks, "", 0, nodes)
    horizon = today + timedelta(days=days)
    as_of = getattr(record, "as_of", None)
    out: list[DetectedSignal] = []
    seen: set[str] = set()
    for path, node in nodes:
        for fname in _DATE_FIELDS:
            when = _day(node.get(fname))
            if when is None or not (today <= when <= horizon):
                continue
            label = next((_clip(node[f], 160) for f in _LABEL_FIELDS
                          if isinstance(node.get(f), str) and node[f].strip()), "")
            url = next((node[f] for f in _URL_FIELDS
                        if isinstance(node.get(f), str) and node[f].startswith("http")),
                       None)
            key = signal_key_for(KIND_CATALYST_WINDOW, str(company_id), path, fname,
                                 when.isoformat(), label)
            if key in seen:
                continue
            seen.add(key)
            what = f"{label} ({fname.replace('_', ' ')})" if label else fname.replace(
                "_", " ")
            out.append(DetectedSignal(
                kind=KIND_CATALYST_WINDOW, signal_key=key,
                summary=_clip(
                    f"The latest research record (as of {as_of}) holds a dated item "
                    f"within {days} days: {what} on {when.isoformat()}.", 1000),
                company_id=company_id,
                relates_to_run_id=getattr(record, "research_run_id", None),
                source_ref=f"company_research_record:{record.id}",
                detail={"date": when.isoformat(), "field": fname, "block": path or None,
                        "label": label or None, "source_url": url,
                        "days_ahead": (when - today).days, "record_id": str(record.id),
                        "record_as_of": as_of.isoformat() if as_of else None},
            ))
    return out


# ── reads ──────────────────────────────────────────────────────────────────── #


async def latest_record(session: Any, company_id: uuid.UUID) -> CompanyResearchRecord | None:
    return (await session.execute(
        select(CompanyResearchRecord)
        .where(CompanyResearchRecord.company_id == company_id)
        .order_by(CompanyResearchRecord.as_of.desc(),
                  CompanyResearchRecord.created_at.desc())
        .limit(1))).scalars().first()


async def listed_company_ids(
    session: Any, *, watchlist_id: uuid.UUID | None = None,
    limit: int = MAX_COMPANIES_PER_PASS,
) -> list[uuid.UUID]:
    """Distinct companies on active lists (or one list), oldest entry first, bounded."""
    stmt = (select(WatchlistEntry.company_id, WatchlistEntry.added_at)
            .join(Watchlist, Watchlist.id == WatchlistEntry.watchlist_id)
            .where(Watchlist.is_active.is_(True), WatchlistEntry.company_id.is_not(None)))
    if watchlist_id is not None:
        stmt = stmt.where(Watchlist.id == watchlist_id)
    stmt = stmt.order_by(WatchlistEntry.added_at, WatchlistEntry.id)
    ids: list[uuid.UUID] = []
    for company_id, _ in (await session.execute(stmt)).all():
        if company_id not in ids:
            ids.append(company_id)
        if len(ids) >= limit:
            break
    return ids


async def _keys_on_record(session: Any, keys: Iterable[str]) -> set[str]:
    wanted = list(keys)
    if not wanted:
        return set()
    rows = await session.execute(
        select(MonitoringSignal.signal_key).where(MonitoringSignal.signal_key.in_(wanted)))
    return {r[0] for r in rows.all()}


# ── the pass ───────────────────────────────────────────────────────────────── #


@dataclass
class PassResult:
    companies: int = 0
    persisted: int = 0
    duplicates_suppressed: int = 0
    failed: int = 0
    truncated: bool = False
    reason: str | None = None
    per_company: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"companies": self.companies, "persisted": self.persisted,
                "duplicates_suppressed": self.duplicates_suppressed,
                "failed": self.failed, "truncated": self.truncated,
                "reason": self.reason, "per_company": self.per_company[:50]}


async def monitor_company(session: Any, company_id: uuid.UUID, *, cfg: Any,
                          now: datetime) -> DetectionResult:
    """One company: the detector's kinds plus the two record-derived kinds, persisted."""
    result = await detect_for_company(session, company_id=company_id, cfg=cfg, now=now)
    if not getattr(cfg, "v3_monitoring_enabled", False):
        return result
    record = await latest_record(session, company_id)
    if record is not None:
        extra = [*missing_information_signals(record, company_id=company_id),
                 *catalyst_window_signals(record, company_id=company_id,
                                          today=now.date())]
        held = await _keys_on_record(session, (s.signal_key for s in extra))
        seen = {s.signal_key for s in result.signals}
        for s in extra:
            if s.signal_key in held:
                result.duplicates_suppressed += 1
            elif s.signal_key not in seen:
                seen.add(s.signal_key)
                result.signals.append(s)
    result.signals = result.signals[:MAX_SIGNALS_PER_PASS]
    if result.signals:
        result.reason = None
    elif record is None and result.reason is None:
        result.reason = "no research record for this issuer."
    return await persist_signals(session, result, now=now)


async def run_monitoring_pass(
    session: Any, *, company_ids: list[uuid.UUID] | None = None,
    watchlist_id: uuid.UUID | None = None, cfg: Any = None, now: datetime | None = None,
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> PassResult:
    """The pass over listed companies (or the given ones). Commits per company."""
    if cfg is None:
        from app.core.config import settings as cfg
    stamp = now or _utcnow()
    result = PassResult()
    if not getattr(cfg, "v3_monitoring_enabled", False):
        result.reason = "V3_MONITORING_ENABLED is off; nothing was observed."
        return result
    if company_ids is None:
        ids = await listed_company_ids(session, watchlist_id=watchlist_id,
                                       limit=MAX_COMPANIES_PER_PASS + 1)
    else:
        ids = list(dict.fromkeys(company_ids))
    result.truncated = len(ids) > MAX_COMPANIES_PER_PASS
    ids = ids[:MAX_COMPANIES_PER_PASS]
    if not ids:
        result.reason = "no company is on an active list."
        return result
    for company_id in ids:
        if progress is not None:
            await progress("monitoring")
        try:
            one = await monitor_company(session, company_id, cfg=cfg, now=stamp)
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - one company never costs the pass
            await session.rollback()
            result.failed += 1
            logger.warning("v3_monitoring_company_failed company_id=%s error_type=%s",
                           company_id, type(exc).__name__)
            continue
        result.companies += 1
        result.persisted += one.persisted
        result.duplicates_suppressed += one.duplicates_suppressed
        result.per_company.append({"company_id": str(company_id),
                                   "persisted": one.persisted,
                                   "kinds": sorted({s.kind for s in one.signals}),
                                   "reason": one.reason})
    return result


# ── the durable job ────────────────────────────────────────────────────────── #


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def _session_factory() -> Any:
    from app.db.session import async_session_factory

    return async_session_factory


@periodic_job_handler(JOB_TYPE)
async def run_monitoring_pass_job(ctx: JobContext) -> JobOutcome:
    """Daily (periodic) or manual. Payload: optional ``company_id`` or ``watchlist_id``."""
    company_id = _uuid(ctx.payload.get("company_id"))
    watchlist_id = _uuid(ctx.payload.get("watchlist_id"))
    async with _session_factory()() as session:
        result = await run_monitoring_pass(
            session, company_ids=[company_id] if company_id else None,
            watchlist_id=watchlist_id, progress=ctx.checkpoint)
    log_event(logger, "v3_monitoring_pass_completed", job_id=ctx.job_id,
              companies=result.companies, persisted=result.persisted,
              duplicates_suppressed=result.duplicates_suppressed, failed=result.failed,
              truncated=result.truncated)
    warnings: list[str] = []
    if result.reason:
        warnings.append(result.reason)
    if result.truncated:
        warnings.append(f"bounded at {MAX_COMPANIES_PER_PASS} companies")
    if result.failed:
        warnings.append(f"{result.failed} company pass(es) failed")
    return JobOutcome(result_type=RESULT_TYPE, warnings=tuple(warnings))


MONITORING_PASS_TASK = register_periodic_task(PeriodicTask(
    key=TASK_KEY, interval=DAILY, job_type=JOB_TYPE,
    flag_attr="v3_monitoring_pass_enabled"))


__all__ = [
    "CATALYST_WINDOW_DAYS",
    "JOB_TYPE",
    "MAX_COMPANIES_PER_PASS",
    "MONITORING_PASS_TASK",
    "TASK_KEY",
    "PassResult",
    "catalyst_window_signals",
    "latest_record",
    "listed_company_ids",
    "missing_information_signals",
    "monitor_company",
    "run_monitoring_pass",
    "run_monitoring_pass_job",
]
