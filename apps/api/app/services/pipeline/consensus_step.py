"""Consensus snapshot step — V3.20 slice 4.2 (ADR-097, owner decision D2).

During company research, for a US-listed company: read Alpha Vantage ``EARNINGS`` and
``EARNINGS_ESTIMATES`` (two calls) and store what the provider said TODAY in
``consensus_snapshots``:

* one ``fiscal_quarter`` / ``fiscal_year`` row per estimate, with ``is_pre_report``:
  True when the period's results were not yet announced on ``as_of`` (the period has
  not ended, or the stored / provider report date is after ``as_of``), False when they
  were, None when it cannot be told (period ended, no report date known). Only a
  ``True`` snapshot is an expectation (ADR-097 §2); the others are kept as data.
* one ``reported_quarter`` row per provider-reported quarter (newest 12): the
  provider's reported EPS, its estimate and its surprise, on the PROVIDER'S EPS basis.

It never writes ``earnings_results`` (slice 4.4 computes surprises from these rows).

Budget: the free tier allows 25 calls a day; ``vendor_api_calls`` counts them per UTC
day across processes, and a run that would exceed it makes no call (``budget_exhausted``).
Cache: a company already snapshotted today is not called again (``cached``).

States: ``disabled`` | ``no_api_key`` | ``not_us_listed`` | ``cached`` |
``budget_exhausted`` | ``vendor_error`` (key-free ``reason``) | ``stored`` | ``error``.
Never raises.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from typing import Any

from app.integrations.alpha_vantage import (
    DAILY_CALL_BUDGET,
    FUNCTION_EARNINGS,
    FUNCTION_ESTIMATES,
    HORIZON_QUARTER,
    VENDOR,
    AlphaVantageError,
    Estimate,
    Fetch,
    ReportedQuarter,
    fetch_function,
    parse_earnings,
    parse_estimates,
    public_url,
    valid_symbol,
)

CONSENSUS_SNAPSHOT_STRATEGY = "alpha_vantage_snapshot_v1"
CALLS_PER_RUN = 2
REPORTED_QUARTERS_KEPT = 12
_US_EXCHANGES = {"NASDAQ", "NYSE", "AMEX"}

#: ``(calls) -> allowed`` — takes ``calls`` from today's budget, or refuses.
Budget = Callable[[int], Awaitable[bool]]


def us_listed_symbol(company: Any) -> str | None:
    """The provider symbol for a company listed on a US exchange, else None.

    An empty or generic exchange ("US", "OTC") is not enough: Alpha Vantage symbols are
    bare US tickers, and a non-US ticker with the same letters is a different company.
    """
    from app.integrations.exchange_source_registry import normalize_exchange

    raw = (getattr(company, "exchange", None) or "").strip()
    ticker = (getattr(company, "ticker", None) or "").strip().upper()
    if not raw or normalize_exchange(raw) not in _US_EXCHANGES:
        return None
    return ticker if valid_symbol(ticker) else None


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


async def take_budget(session: Any, *, calls: int, day: date, vendor: str = VENDOR,
                      limit: int = DAILY_CALL_BUDGET) -> bool:
    """Atomically take ``calls`` from ``vendor``'s budget for ``day``. False = refused.

    One conditional ``UPDATE ... WHERE calls + n <= limit``; the row is created on first
    use. The caller commits (the default budget below uses its own short transaction so
    a long research run never holds the row lock).
    """
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    from app.models.consensus import VendorApiCall

    stmt = (update(VendorApiCall)
            .where(VendorApiCall.vendor == vendor, VendorApiCall.day == day,
                   VendorApiCall.calls + calls <= limit)
            .values(calls=VendorApiCall.calls + calls,
                    updated_at=datetime.now(timezone.utc)))
    if (await session.execute(stmt)).rowcount:
        return True
    if calls > limit:
        return False
    try:
        async with session.begin_nested():
            session.add(VendorApiCall(id=uuid.uuid4(), vendor=vendor, day=day, calls=calls))
        return True
    except IntegrityError:
        # The row exists (a concurrent run created it, or it is full): retry once.
        return bool((await session.execute(stmt)).rowcount)


def _default_budget(day: date) -> Budget:  # pragma: no cover - opens its own session
    async def take(calls: int) -> bool:
        from app.db.session import async_session_factory

        async with async_session_factory() as own:
            ok = await take_budget(own, calls=calls, day=day)
            await own.commit()
            return ok

    return take


async def _report_dates(session: Any, company_id: uuid.UUID) -> dict[date, date]:
    """period_end → report date from ``earnings_results`` (SEC filings, slice 4.1)."""
    from sqlalchemy import select

    from app.models.earnings import EarningsResult

    rows = (await session.execute(
        select(EarningsResult.period_end, EarningsResult.report_date).where(
            EarningsResult.company_id == company_id,
            EarningsResult.report_date.is_not(None)))).all()
    return {end: reported for end, reported in rows if end is not None}


def pre_report(period_end: date, as_of: date, report_date: date | None) -> bool | None:
    """Was ``as_of`` before the period's results were announced? None = cannot tell."""
    if report_date is not None:
        return as_of < report_date
    if period_end >= as_of:
        return True  # the period has not ended, so it cannot have been reported
    return None


async def _upsert(session: Any, key: dict[str, Any], values: dict[str, Any]) -> None:
    from sqlalchemy import select

    from app.models.consensus import ConsensusSnapshot

    row = (await session.execute(select(ConsensusSnapshot).filter_by(**key))).scalar_one_or_none()
    if row is None:
        row = ConsensusSnapshot(id=uuid.uuid4(), **key)
        session.add(row)
    for name, value in values.items():
        setattr(row, name, value)


def _estimate_values(e: Estimate, *, is_pre: bool | None, url: str,
                     report_date: date | None) -> dict[str, Any]:
    return {
        "is_pre_report": is_pre, "source_url": url,
        "eps_avg": e.eps_avg, "eps_high": e.eps_high, "eps_low": e.eps_low,
        "eps_analyst_count": e.eps_analyst_count,
        "revenue_avg": e.revenue_avg, "revenue_high": e.revenue_high,
        "revenue_low": e.revenue_low, "revenue_analyst_count": e.revenue_analyst_count,
        "eps_avg_7d_ago": e.eps_avg_7d_ago, "eps_avg_30d_ago": e.eps_avg_30d_ago,
        "eps_avg_60d_ago": e.eps_avg_60d_ago, "eps_avg_90d_ago": e.eps_avg_90d_ago,
        "eps_revisions_up_30d": e.eps_revisions_up_30d,
        "eps_revisions_down_30d": e.eps_revisions_down_30d,
        "detail_json": {
            "strategy": CONSENSUS_SNAPSHOT_STRATEGY, "eps_basis": "provider",
            "report_date_used": report_date.isoformat() if report_date else None,
        },
    }


def _reported_values(r: ReportedQuarter, *, url: str) -> dict[str, Any]:
    return {
        "is_pre_report": False, "source_url": url,
        "eps_avg": r.estimated_eps, "vendor_reported_date": r.reported_date,
        "vendor_reported_eps": r.reported_eps, "vendor_surprise": r.surprise,
        "vendor_surprise_pct": r.surprise_pct, "vendor_report_time": r.report_time,
        "detail_json": {"strategy": CONSENSUS_SNAPSHOT_STRATEGY, "eps_basis": "provider"},
    }


async def store_snapshots(session: Any, *, company_id: uuid.UUID, symbol: str, as_of: date,
                          estimates: list[Estimate],
                          reported: list[ReportedQuarter]) -> dict[str, int]:
    """Upsert today's rows. Returns counts by kind."""
    from app.models.consensus import (
        HORIZON_QUARTER as ROW_QUARTER,
    )
    from app.models.consensus import (
        HORIZON_REPORTED,
        HORIZON_YEAR,
        SOURCE_ALPHA_VANTAGE,
    )

    stored = await _report_dates(session, company_id)
    vendor_dates = {r.period_end: r.reported_date for r in reported if r.reported_date}
    base = {"company_id": company_id, "source": SOURCE_ALPHA_VANTAGE, "as_of": as_of}
    counts = {"estimates": 0, "pre_report": 0, "post_report": 0, "undetermined": 0,
              "reported_quarters": 0}
    est_url = public_url(FUNCTION_ESTIMATES, symbol)
    for e in estimates:
        # The filed (SEC) report date wins over the provider's date.
        report_date = stored.get(e.period_end) or vendor_dates.get(e.period_end)
        is_pre = pre_report(e.period_end, as_of, report_date)
        horizon = ROW_QUARTER if e.horizon == HORIZON_QUARTER else HORIZON_YEAR
        await _upsert(session, {**base, "horizon": horizon, "period_end": e.period_end},
                      {"symbol": symbol, **_estimate_values(
                          e, is_pre=is_pre, url=est_url, report_date=report_date)})
        counts["estimates"] += 1
        counts["pre_report" if is_pre else "post_report" if is_pre is False
               else "undetermined"] += 1
    earn_url = public_url(FUNCTION_EARNINGS, symbol)
    for r in reported[:REPORTED_QUARTERS_KEPT]:
        await _upsert(session, {**base, "horizon": HORIZON_REPORTED, "period_end": r.period_end},
                      {"symbol": symbol, **_reported_values(r, url=earn_url)})
        counts["reported_quarters"] += 1
    await session.flush()
    return counts


async def _snapshotted_today(session: Any, company_id: uuid.UUID, day: date) -> bool:
    from sqlalchemy import select

    from app.models.consensus import ConsensusSnapshot

    return (await session.execute(select(ConsensusSnapshot.id).where(
        ConsensusSnapshot.company_id == company_id,
        ConsensusSnapshot.as_of == day).limit(1))).first() is not None


async def consensus_snapshot_step(session: Any, company: Any, *, cfg: Any,
                                  fetch: Fetch | None = None, budget: Budget | None = None,
                                  today: date | None = None) -> dict[str, Any]:
    """Snapshot the provider's estimates for one company. Never raises."""
    if not getattr(cfg, "v3_consensus_snapshots_enabled", False):
        return {"state": "disabled"}
    api_key = getattr(cfg, "alpha_vantage_api_key", "") or ""
    if not api_key:
        return {"state": "no_api_key", "consensus_status": "source_unavailable",
                "note": "ALPHA_VANTAGE_API_KEY is not set; no consensus is read."}
    symbol = us_listed_symbol(company)
    if symbol is None:
        return {"state": "not_us_listed", "consensus_status": "source_unavailable",
                "note": "The consensus provider covers US-listed tickers only."}
    day = today or _utc_today()
    try:
        if await _snapshotted_today(session, company.id, day):
            return {"state": "cached", "symbol": symbol, "as_of": day.isoformat()}
        take = budget or _default_budget(day)
        if not await take(CALLS_PER_RUN):
            return {"state": "budget_exhausted", "symbol": symbol,
                    "note": f"The provider's {DAILY_CALL_BUDGET} calls for {day} are used."}
        try:
            earnings = parse_earnings(await fetch_function(
                FUNCTION_EARNINGS, symbol, api_key=api_key, fetch=fetch))
            estimates = parse_estimates(await fetch_function(
                FUNCTION_ESTIMATES, symbol, api_key=api_key, fetch=fetch))
        except AlphaVantageError as exc:
            return {"state": "vendor_error", "symbol": symbol, "reason": exc.reason,
                    "consensus_status": "source_unavailable"}
        if not earnings and not estimates:
            return {"state": "vendor_error", "symbol": symbol, "reason": "no_coverage",
                    "consensus_status": "source_unavailable"}
        async with session.begin_nested():
            counts = await store_snapshots(session, company_id=company.id, symbol=symbol,
                                           as_of=day, estimates=estimates, reported=earnings)
        return {"state": "stored", "symbol": symbol, "as_of": day.isoformat(), **counts}
    except Exception as exc:  # noqa: BLE001 - a missing snapshot costs nothing else
        return {"state": "error", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


__all__ = [
    "CALLS_PER_RUN",
    "CONSENSUS_SNAPSHOT_STRATEGY",
    "consensus_snapshot_step",
    "pre_report",
    "store_snapshots",
    "take_budget",
    "us_listed_symbol",
]
