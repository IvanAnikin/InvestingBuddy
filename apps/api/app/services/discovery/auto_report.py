"""Automatic report on the best candidate of each thesis run — V3.21.3 (ADR-110).

WHAT IT DOES
============
When a THESIS run completes, the best-ranked candidate that is ELIGIBLE (dynamic stage
``eligible``; never size-unverified, never novelty-held; a hard size requirement, when
there is one, PASSED with evidence) gets ONE durable ``company_research`` job, linked to
the candidate (``discovery_candidate_id``) and at ``V3_AUTO_REPORT_MODE`` depth. The
candidate's existing analysis-job envelope points at the job, so the pages that already
show a candidate's research progress show this one. The company is added to the team
Shortlist with ``source=auto_report``.

FALLBACK (V3.21.1b, ADR-115)
============================
When no candidate is eligible, the best-ranked candidate that is ``eligible_unverified``
with its hard size PASSED, not held back, and whose ONLY open hard check is the industry
(``unknown``; a verified denial is ``fail`` and never qualifies) is queued with reason
``industry_unverified_fallback``: the report itself verifies the segment exposure.

BOUNDS
======
* One per run: ``auto_report_json`` on the run is the idempotency record
  (``auto_report:{run_id}``). A run that already has one is never auto-reported again.
* At most ``V3_AUTO_REPORT_MAX_PER_DAY`` QUEUED per UTC day, across all runs.
* A completed final report for the company newer than ``V3_AUTO_REPORT_FRESH_DAYS`` is
  LINKED instead of re-researched.
* Ticker runs and a request with ``auto_report=false`` are not auto-reported.
* Durable jobs only: with ``V3_DURABLE_JOBS_ENABLED`` off it is recorded as skipped. A
  process-local background task would die with the process and nobody would know.

NEVER FATAL
===========
``auto_report_for_run`` never raises. The run's final status is committed BEFORE it is
called, so an error here can neither fail nor roll back the run; the error is recorded as
``state=failed`` with the exception TYPE (never a message) and logged.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.structured_logging import log_event
from app.models.company import Company
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.models.report import Report

logger = logging.getLogger(__name__)

STATE_QUEUED = "queued"
STATE_SKIPPED = "skipped"
STATE_LINKED = "linked"
STATE_FAILED = "failed"

REASON_OPTED_OUT = "opted_out"
REASON_RUN_NOT_COMPLETED = "run_not_completed"
REASON_NO_ELIGIBLE = "no_eligible_candidate"
REASON_COMPANY_NOT_FOUND = "company_not_found"
REASON_FRESH_REPORT = "fresh_report"
REASON_DURABLE_OFF = "durable_jobs_off"
REASON_DAILY_CAP = "daily_cap"
REASON_JOINED = "joined_in_flight_job"
#: V3.21.1b (ADR-115) — no candidate was fully eligible; the best size-verified one whose
#: only open hard check is the industry (``unknown``, never ``fail``) was queued, so the
#: report itself verifies the exposure.
REASON_INDUSTRY_FALLBACK = "industry_unverified_fallback"
SELECTION_ELIGIBLE = "eligible"

_COMPLETED = frozenset({"completed", "completed_with_warnings"})
_MODES = frozenset({"quick", "standard", "deep"})

#: ``(company, *, discovery_candidate_id, research_mode, provider_name) -> (envelope,
#: created)`` — ``company_research_job.submit`` in production, a fake in tests.
Submit = Callable[..., Awaitable[tuple[dict[str, Any], bool]]]


def idempotency_key(run_id: uuid.UUID | str) -> str:
    return f"auto_report:{run_id}"


def enabled(cfg: Any) -> bool:
    return bool(getattr(cfg, "v3_discovery_auto_report_enabled", False))


def research_mode(cfg: Any) -> str:
    mode = str(getattr(cfg, "v3_auto_report_mode", "standard") or "standard").lower()
    return mode if mode in _MODES else "standard"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Eligibility ──────────────────────────────────────────────────────────── #


def _listing_key(exchange: Any, ticker: Any) -> tuple[str, str]:
    return (str(exchange or "").upper(), str(ticker or "").upper())


def _held_back_keys(run: DiscoveryRun) -> set[tuple[str, str]]:
    """Listings the dynamic stage held back: size unverified or novelty held."""
    stage = (run.universe_json or {}).get("dynamic") or {}
    rows: list[Any] = list(stage.get("size_unverified") or [])
    rows += list((stage.get("anti_convergence") or {}).get("novelty_held") or [])
    keys: set[tuple[str, str]] = set()
    for row in rows:
        identity = (row or {}).get("identity") or {}
        keys.add(_listing_key(identity.get("exchange"), identity.get("ticker")))
    return keys


def is_eligible(candidate: DiscoveryCandidate, held_back: set[tuple[str, str]]) -> bool:
    """Dynamic-stage ``eligible``, not held back, and any HARD size requirement passed."""
    if _listing_key(candidate.exchange, candidate.ticker) in held_back:
        return False
    v319 = (candidate.thesis_match_json or {}).get("v319") or {}
    if (v319.get("eligibility") or {}).get("status") != "eligible":
        return False
    for result in v319.get("constraint_results") or []:
        if (result or {}).get("key") == "size" and result.get("hardness") == "hard":
            if result.get("status") != "pass":
                return False
    return True


#: Hard checks a research report verifies itself, so a candidate with only these
#: ``unknown`` may be auto-reported as a fallback (ADR-115, widened at V3.21 acceptance).
_REPORT_VERIFIES = frozenset({"industry", "growth"})


def is_industry_fallback(candidate: DiscoveryCandidate, held_back: set[tuple[str, str]]) -> bool:
    """V3.21.1b (ADR-115) — a candidate the report may verify itself.

    Dynamic-stage ``eligible_unverified``, not held back (size unverified, novelty held),
    a HARD size requirement that PASSED with evidence, and every other hard requirement
    passed except the industry and/or growth, which are ``unknown``. A ``fail`` (a
    verified denial) excludes the name; it is never this.
    """
    if _listing_key(candidate.exchange, candidate.ticker) in held_back:
        return False
    v319 = (candidate.thesis_match_json or {}).get("v319") or {}
    if (v319.get("eligibility") or {}).get("status") != "eligible_unverified":
        return False
    hard = [r for r in v319.get("constraint_results") or []
            if isinstance(r, dict) and r.get("hardness") == "hard"
            and r.get("status") != "not_requested"]
    size = [r for r in hard if r.get("key") == "size"]
    if not size or any(r.get("status") != "pass" for r in size):
        return False
    # V3.21 live finding (run 09567859): "growing recurring revenue" made growth a hard
    # check that discovery could not verify, so verified-size, verified-exposure names
    # were never reported. Industry and growth are what the report verifies itself;
    # listing, geography and size never fall back.
    open_checks = [r for r in hard if r.get("status") != "pass"]
    if not open_checks:
        return False
    return all(r.get("key") in _REPORT_VERIFIES and r.get("status") == "unknown"
               for r in open_checks)


def pick_candidate(
    run: DiscoveryRun, candidates: Sequence[DiscoveryCandidate]
) -> DiscoveryCandidate | None:
    """The best-ranked eligible candidate, or None."""
    held_back = _held_back_keys(run)
    ranked = sorted(candidates, key=lambda c: (c.rank is None, c.rank or 0))
    for candidate in ranked:
        if is_eligible(candidate, held_back):
            return candidate
    return None


def pick_fallback_candidate(
    run: DiscoveryRun, candidates: Sequence[DiscoveryCandidate]
) -> DiscoveryCandidate | None:
    """The best-ranked industry-unverified fallback (:func:`is_industry_fallback`), or
    None. Used only when :func:`pick_candidate` found nothing."""
    held_back = _held_back_keys(run)
    ranked = sorted(candidates, key=lambda c: (c.rank is None, c.rank or 0))
    for candidate in ranked:
        if is_industry_fallback(candidate, held_back):
            return candidate
    return None


# ── Lookups ──────────────────────────────────────────────────────────────── #


async def _company_for(db: AsyncSession, candidate: DiscoveryCandidate) -> Company | None:
    v319 = (candidate.thesis_match_json or {}).get("v319") or {}
    held_id = (v319.get("identity") or {}).get("company_id")
    if held_id:
        try:
            found = await db.get(Company, uuid.UUID(str(held_id)))
        except (ValueError, TypeError):
            found = None
        if found is not None:
            return found
    result = await db.execute(select(Company).where(
        Company.ticker == str(candidate.ticker or "").upper(),
        Company.exchange == str(candidate.exchange or "").upper()))
    return result.scalars().first()


async def fresh_report(
    db: AsyncSession, company_id: uuid.UUID, *, fresh_days: int, now: datetime
) -> Report | None:
    """The newest final report for the company created within ``fresh_days``."""
    cutoff = now - timedelta(days=max(0, int(fresh_days)))
    result = await db.execute(
        select(Report)
        .where(Report.company_id == company_id,
               Report.final_report_version.isnot(None),
               Report.created_at >= cutoff)
        .order_by(Report.created_at.desc())
        .limit(1))
    return result.scalars().first()


async def queued_today(db: AsyncSession, *, now: datetime,
                       exclude_run_id: uuid.UUID | None = None) -> int:
    """Auto-reports QUEUED since UTC midnight, across all runs."""
    day_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0,
                                                     microsecond=0)
    query = select(DiscoveryRun.id, DiscoveryRun.auto_report_json).where(
        DiscoveryRun.auto_report_json.isnot(None),
        DiscoveryRun.updated_at >= day_start)
    count = 0
    for run_id, record in (await db.execute(query)).all():
        if run_id == exclude_run_id or not isinstance(record, dict):
            continue
        if record.get("state") != STATE_QUEUED:
            continue
        try:
            at = datetime.fromisoformat(str(record.get("at")))
        except ValueError:
            continue
        if (_aware(at) or day_start) >= day_start:
            count += 1
    return count


# ── The hook ─────────────────────────────────────────────────────────────── #


def _record(state: str, reason: str | None, *, run: DiscoveryRun, now: datetime,
            candidate: DiscoveryCandidate | None = None,
            company: Company | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "state": state,
        "reason": reason,
        "idempotency_key": idempotency_key(run.id),
        "candidate_id": str(candidate.id) if candidate is not None else None,
        "ticker": candidate.ticker if candidate is not None else None,
        "exchange": candidate.exchange if candidate is not None else None,
        "rank": candidate.rank if candidate is not None else None,
        "company_id": str(company.id) if company is not None else None,
        "job_id": None,
        "report_id": None,
        "at": now.isoformat(),
        **extra,
    }


def _point_candidate_at_job(candidate: DiscoveryCandidate, envelope: dict[str, Any],
                            *, mode: str, now: datetime) -> None:
    """The candidate's analysis-job envelope points at the durable job."""
    from app.services import market_discovery_service as mds

    existing = mds.get_analysis_job_envelope(candidate) or {}
    if existing.get("status") in mds._ANALYSIS_IN_FLIGHT | mds._ANALYSIS_HAS_RESULT \
            and not existing.get("durable_job_id"):
        return  # a manual analysis is already running or done; never overwrite it
    pending = mds._new_analysis_job_envelope(
        status="pending", started_at=now.isoformat(),
        provider_name=envelope.get("provider_name"))
    pending["durable_job_id"] = str(envelope.get("job_id"))
    pending["source"] = "auto_report"
    pending["research_mode"] = mode
    mds._store_analysis_job_envelope(candidate, pending)
    candidate.updated_at = now


async def _shortlist(db: AsyncSession, company: Company, run: DiscoveryRun, *,
                     selection: str = SELECTION_ELIGIBLE) -> str | None:
    """Add to the team Shortlist; returns an error note, never raises."""
    what = ("best size-verified candidate, segment exposure not yet verified"
            if selection == REASON_INDUSTRY_FALLBACK else "best eligible candidate")
    try:
        from app.services.monitoring.team_lists import (
            SHORTLIST,
            SOURCE_AUTO_REPORT,
            add_to_team_list,
        )

        # A savepoint, so a failure here rolls back the shortlist write only — never
        # the run's auto-report record committed beside it.
        async with db.begin_nested():
            await add_to_team_list(
                db, SHORTLIST, company.id, source=SOURCE_AUTO_REPORT,
                note=f"Auto-report: {what} of discovery run {run.id}")
        return None
    except Exception as exc:  # noqa: BLE001 - the shortlist is never fatal
        log_event(logger, "discovery_auto_report_shortlist_failed", level=logging.WARNING,
                  run_id=run.id, company_id=company.id, error_type=type(exc).__name__)
        return f"shortlist_error:{type(exc).__name__}"


async def _default_submit(company: Company, **kwargs: Any) -> tuple[dict[str, Any], bool]:
    from app.services.jobs import company_research_job

    return await company_research_job.submit(company, **kwargs)


def _durable_on(cfg: Any) -> bool:
    return bool(getattr(cfg, "v3_durable_jobs_enabled", False))


async def _decide(
    db: AsyncSession, run: DiscoveryRun, candidates: Sequence[DiscoveryCandidate], *,
    cfg: Any, now: datetime, submit: Submit,
) -> dict[str, Any]:
    if (run.config_json or {}).get("auto_report") is False:
        return _record(STATE_SKIPPED, REASON_OPTED_OUT, run=run, now=now)
    if run.status not in _COMPLETED:
        return _record(STATE_SKIPPED, REASON_RUN_NOT_COMPLETED, run=run, now=now)
    candidate = pick_candidate(run, candidates)
    selection = SELECTION_ELIGIBLE
    if candidate is None:
        candidate = pick_fallback_candidate(run, candidates)
        selection = REASON_INDUSTRY_FALLBACK
    if candidate is None:
        return _record(STATE_SKIPPED, REASON_NO_ELIGIBLE, run=run, now=now)
    company = await _company_for(db, candidate)
    if company is None:
        return _record(STATE_SKIPPED, REASON_COMPANY_NOT_FOUND, run=run, now=now,
                       candidate=candidate, selection=selection)

    fresh_days = int(getattr(cfg, "v3_auto_report_fresh_days", 14) or 0)
    existing = await fresh_report(db, company.id, fresh_days=fresh_days, now=now)
    if existing is not None:
        record = _record(STATE_LINKED, REASON_FRESH_REPORT, run=run, now=now,
                         candidate=candidate, company=company,
                         report_id=str(existing.id),
                         report_created_at=_iso(existing.created_at), selection=selection)
        if candidate.analysis_report_id is None:
            candidate.analysis_report_id = existing.id
        note = await _shortlist(db, company, run, selection=selection)
        if note:
            record["note"] = note
        return record

    if not _durable_on(cfg):
        return _record(STATE_SKIPPED, REASON_DURABLE_OFF, run=run, now=now,
                       candidate=candidate, company=company, selection=selection)
    cap = int(getattr(cfg, "v3_auto_report_max_per_day", 5) or 0)
    if await queued_today(db, now=now, exclude_run_id=run.id) >= cap:
        return _record(STATE_SKIPPED, REASON_DAILY_CAP, run=run, now=now,
                       candidate=candidate, company=company, daily_cap=cap,
                       selection=selection)

    mode = research_mode(cfg)
    try:
        envelope, created = await submit(
            company, discovery_candidate_id=candidate.id, research_mode=mode,
            provider_name=run.provider_name)
    except Exception as exc:  # noqa: BLE001 - recorded, never fatal
        log_event(logger, "discovery_auto_report_submit_failed", level=logging.WARNING,
                  run_id=run.id, candidate_id=candidate.id, company_id=company.id,
                  error_type=type(exc).__name__)
        return _record(STATE_FAILED, f"submit_error:{type(exc).__name__}", run=run,
                       now=now, candidate=candidate, company=company, selection=selection)
    fallback = selection == REASON_INDUSTRY_FALLBACK
    reason = (REASON_INDUSTRY_FALLBACK if fallback
              else None if created else REASON_JOINED)
    record = _record(STATE_QUEUED, reason, run=run, now=now,
                     candidate=candidate, company=company,
                     job_id=str(envelope.get("job_id")) if envelope.get("job_id") else None,
                     research_mode=mode, selection=selection)
    if fallback and not created:
        record["joined_in_flight_job"] = True
    _point_candidate_at_job(candidate, envelope, mode=mode, now=now)
    note = await _shortlist(db, company, run, selection=selection)
    if note:
        record["note"] = note
    return record


def _iso(dt: datetime | None) -> str | None:
    aware = _aware(dt)
    return aware.isoformat() if aware is not None else None


async def auto_report_for_run(
    db: AsyncSession,
    run: DiscoveryRun,
    candidates: Sequence[DiscoveryCandidate],
    *,
    cfg: Any = None,
    now: datetime | None = None,
    submit: Submit | None = None,
) -> dict[str, Any] | None:
    """Queue, link or skip the run's auto-report and record it. NEVER raises.

    Returns the record written to ``run.auto_report_json`` (or the one already there),
    or None when auto-report does not apply (flag off, ticker run).
    """
    if cfg is None:
        from app.core.config import settings

        cfg = settings
    try:
        if not enabled(cfg) or run.mode != "thesis":
            return None
        if isinstance(run.auto_report_json, dict) and run.auto_report_json.get("state"):
            return run.auto_report_json  # idempotent per run
        now = now or _utcnow()
        record = await _decide(db, run, candidates, cfg=cfg, now=now,
                               submit=submit or _default_submit)
        run.auto_report_json = record
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - the run is already final; never fail it
        logger.exception("Auto-report failed for discovery run %s.", run.id)
        record = {"state": STATE_FAILED, "reason": f"error:{type(exc).__name__}",
                  "idempotency_key": idempotency_key(run.id),
                  "at": (now or _utcnow()).isoformat()}
        try:
            await db.rollback()
            run.auto_report_json = record
            await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Could not record the auto-report failure for run %s.",
                             run.id)
            return record
    log_event(logger, "discovery_auto_report", run_id=run.id, state=record.get("state"),
              reason=record.get("reason"), candidate_id=record.get("candidate_id"),
              company_id=record.get("company_id"), job_id=record.get("job_id"),
              report_id=record.get("report_id"))
    return record


__all__ = [
    "REASON_COMPANY_NOT_FOUND",
    "REASON_DAILY_CAP",
    "REASON_DURABLE_OFF",
    "REASON_FRESH_REPORT",
    "REASON_INDUSTRY_FALLBACK",
    "REASON_JOINED",
    "REASON_NO_ELIGIBLE",
    "REASON_OPTED_OUT",
    "REASON_RUN_NOT_COMPLETED",
    "STATE_FAILED",
    "STATE_LINKED",
    "STATE_QUEUED",
    "STATE_SKIPPED",
    "auto_report_for_run",
    "enabled",
    "fresh_report",
    "idempotency_key",
    "is_eligible",
    "is_industry_fallback",
    "pick_candidate",
    "pick_fallback_candidate",
    "queued_today",
    "research_mode",
]
