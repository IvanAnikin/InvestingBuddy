"""Earnings surprises + the "Earnings track record" report block — V3.20 slice 4.4 (ADR-097).

Two parts:

1. ``apply_surprises`` (a pipeline step, ``earnings_surprise_step``) fills the
   consensus / surprise columns of ``earnings_results`` from ``consensus_snapshots``
   (slice 4.2):

   * **Consensus columns** come ONLY from a dated pre-report snapshot of the same period
     (``is_pre_report`` and ``as_of`` strictly before the row's ``report_date``; the
     latest such snapshot wins). A provider's undated record of "the estimate" is never
     written there (ADR-097 §2).
   * **Revenue surprise** = the filed revenue as first reported − that snapshot's revenue
     average (ADR-097 §6: computed).
   * **EPS surprise** = the provider's own reported-minus-estimated figure, stored as
     given and labelled with the provider and its EPS basis. The filed diluted EPS is
     never subtracted from a provider estimate; when the provider's reported EPS differs
     from the filed diluted EPS by more than one cent the row is flagged
     ``eps_basis_difference`` (adjusted vs GAAP).
   * ``consensus_status``: ``available`` (≥ 3 analysts), ``thin`` (1–2, or a count not
     given), ``none`` (the provider covers the company but holds no pre-report estimate
     for this period), ``source_unavailable`` (nothing from the provider at all).

2. ``earnings_track_record_block`` — the report block (newest 8 quarters + 3 years):
   actuals with their filings, the pre-report expectation with source / date / analyst
   count beside every value, both surprises, guidance vs actual (slice 4.3), and the
   streak of quarters above or below the provider's EPS estimate. Typed fields only;
   every value names its source. Never a forecast of the platform's own.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

EARNINGS_SURPRISE_STRATEGY = "pre_report_snapshot_v1"
QUARTERS_SHOWN = 8
YEARS_SHOWN = 3
BASIS_TOLERANCE = Decimal("0.01")

PROVIDER_LABELS = {"alpha_vantage": "Alpha Vantage"}

NOTE = ("Expectations are third-party analyst data (Alpha Vantage, private use), shown "
        "only as read before each result was announced, with their date and analyst "
        "count. The provider's EPS surprise is on the provider's EPS basis, which can "
        "differ from the filed diluted EPS. This is research context, not advice.")


def _pct(diff: Decimal, base: Decimal | None) -> Decimal | None:
    if base is None or base == 0:
        return None
    return (diff / abs(base) * 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def status_for(count: int | None, *, has_snapshot: bool, covered: bool) -> str:
    from app.models.earnings import (
        CONSENSUS_AVAILABLE,
        CONSENSUS_NONE,
        CONSENSUS_SOURCE_UNAVAILABLE,
        CONSENSUS_THIN,
    )

    if not covered:
        return CONSENSUS_SOURCE_UNAVAILABLE
    if not has_snapshot or count == 0:
        return CONSENSUS_NONE
    if count is not None and count >= 3:
        return CONSENSUS_AVAILABLE
    return CONSENSUS_THIN


def _best_pre_report(snapshots: Sequence[Any], *, period_end: date, horizon: str,
                     report_date: date | None) -> Any | None:
    if report_date is None:
        return None
    pool = [s for s in snapshots if s.horizon == horizon and s.period_end == period_end
            and s.is_pre_report is True and s.as_of < report_date
            and (s.eps_avg is not None or s.revenue_avg is not None)]
    return max(pool, key=lambda s: s.as_of, default=None)


def _vendor_record(snapshots: Sequence[Any], *, period_end: date) -> Any | None:
    from app.models.consensus import HORIZON_REPORTED

    pool = [s for s in snapshots if s.horizon == HORIZON_REPORTED and s.period_end == period_end]
    return max(pool, key=lambda s: s.as_of, default=None)


def apply_to_row(row: Any, snapshots: Sequence[Any], *, covered: bool) -> dict[str, Any]:
    """Fill one ``EarningsResult``'s consensus + surprise columns. Returns the detail."""
    from app.models.consensus import HORIZON_QUARTER, HORIZON_YEAR

    horizon = HORIZON_YEAR if row.fiscal_quarter == 0 else HORIZON_QUARTER
    snap = _best_pre_report(snapshots, period_end=row.period_end, horizon=horizon,
                            report_date=row.report_date)
    for name in ("consensus_eps_avg", "consensus_eps_high", "consensus_eps_low",
                 "consensus_revenue_avg", "consensus_revenue_high", "consensus_revenue_low",
                 "analyst_count_eps", "analyst_count_revenue", "consensus_source",
                 "consensus_as_of", "surprise_revenue_abs", "surprise_revenue_pct",
                 "surprise_eps_abs", "surprise_eps_pct"):
        setattr(row, name, None)
    detail: dict[str, Any] = {"strategy": EARNINGS_SURPRISE_STRATEGY}
    if snap is not None:
        row.consensus_eps_avg, row.consensus_eps_high = snap.eps_avg, snap.eps_high
        row.consensus_eps_low, row.analyst_count_eps = snap.eps_low, snap.eps_analyst_count
        row.consensus_revenue_avg, row.consensus_revenue_high = snap.revenue_avg, snap.revenue_high
        row.consensus_revenue_low = snap.revenue_low
        row.analyst_count_revenue = snap.revenue_analyst_count
        row.consensus_source, row.consensus_as_of = snap.source, snap.as_of
        detail["snapshot_id"] = str(snap.id)
        if row.actual_revenue is not None and snap.revenue_avg is not None:
            diff = row.actual_revenue - snap.revenue_avg
            row.surprise_revenue_abs = diff
            row.surprise_revenue_pct = _pct(diff, snap.revenue_avg)
    count = snap.eps_analyst_count if snap is not None else None
    row.consensus_status = status_for(count, has_snapshot=snap is not None, covered=covered)
    vendor = (_vendor_record(snapshots, period_end=row.period_end)
              if horizon == HORIZON_QUARTER else None)
    if vendor is not None and vendor.vendor_surprise is not None:
        row.surprise_eps_abs = vendor.vendor_surprise
        row.surprise_eps_pct = vendor.vendor_surprise_pct
        detail["eps_surprise"] = {
            "source": vendor.source, "basis": "provider",
            "provider_reported_eps": str(vendor.vendor_reported_eps)
            if vendor.vendor_reported_eps is not None else None,
            "provider_estimated_eps": str(vendor.eps_avg) if vendor.eps_avg is not None else None,
            "provider_reported_date": vendor.vendor_reported_date.isoformat()
            if vendor.vendor_reported_date else None,
            "read_on": vendor.as_of.isoformat(),
        }
        if vendor.vendor_reported_eps is not None and row.actual_eps is not None:
            gap = vendor.vendor_reported_eps - row.actual_eps
            detail["eps_surprise"]["eps_basis_difference"] = abs(gap) > BASIS_TOLERANCE
            detail["eps_surprise"]["provider_minus_filed_eps"] = str(gap)
    return detail


async def apply_surprises(session: Any, *, company_id: Any) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models.consensus import ConsensusSnapshot
    from app.models.earnings import EarningsResult

    rows = (await session.execute(select(EarningsResult).where(
        EarningsResult.company_id == company_id))).scalars().all()
    snapshots = (await session.execute(select(ConsensusSnapshot).where(
        ConsensusSnapshot.company_id == company_id))).scalars().all()
    covered = bool(snapshots)
    by_status: dict[str, int] = {}
    revenue = eps = 0
    for row in rows:
        detail = apply_to_row(row, snapshots, covered=covered)
        merged = dict(row.detail_json or {})
        merged["surprise"] = detail
        row.detail_json = merged
        by_status[row.consensus_status or ""] = by_status.get(row.consensus_status or "", 0) + 1
        revenue += row.surprise_revenue_abs is not None
        eps += row.surprise_eps_abs is not None
    await session.flush()
    return {"rows": len(rows), "consensus_status": by_status,
            "revenue_surprises": revenue, "eps_surprises": eps, "covered": covered}


async def earnings_surprise_step(session: Any, company: Any, *, cfg: Any) -> dict[str, Any]:
    """Fill consensus / surprise columns for one company. Never raises."""
    if not getattr(cfg, "v3_earnings_track_record_enabled", False):
        return {"state": "disabled"}
    try:
        async with session.begin_nested():
            out = await apply_surprises(session, company_id=company.id)
        if not out["rows"]:
            return {"state": "no_actuals"}
        return {"state": "applied", **out}
    except Exception as exc:  # noqa: BLE001 - the report shows actuals without surprises
        return {"state": "error", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


# ── The report block ──────────────────────────────────────────────────────────


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _d(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _row_view(row: Any) -> dict[str, Any]:
    detail = row.detail_json or {}
    eps_surprise = (detail.get("surprise") or {}).get("eps_surprise") or {}
    guidance = detail.get("guidance") or {}
    source = row.consensus_source
    expectation = None
    if row.consensus_as_of is not None:
        expectation = {
            "source": PROVIDER_LABELS.get(source or "", source),
            "as_of": _d(row.consensus_as_of),
            "eps_avg": _s(row.consensus_eps_avg), "eps_high": _s(row.consensus_eps_high),
            "eps_low": _s(row.consensus_eps_low), "eps_analyst_count": row.analyst_count_eps,
            "revenue_avg": _s(row.consensus_revenue_avg),
            "revenue_high": _s(row.consensus_revenue_high),
            "revenue_low": _s(row.consensus_revenue_low),
            "revenue_analyst_count": row.analyst_count_revenue,
        }
    eps_vs = None
    if row.surprise_eps_abs is not None:
        eps_vs = {
            "source": PROVIDER_LABELS.get(eps_surprise.get("source") or "",
                                          eps_surprise.get("source")),
            "basis": "provider EPS basis",
            "difference": _s(row.surprise_eps_abs), "pct": _s(row.surprise_eps_pct),
            "provider_reported_eps": eps_surprise.get("provider_reported_eps"),
            "provider_estimated_eps": eps_surprise.get("provider_estimated_eps"),
            "eps_basis_difference": bool(eps_surprise.get("eps_basis_difference")),
            "read_on": eps_surprise.get("read_on"),
        }
    return {
        "period": row.fiscal_period_label, "period_end": _d(row.period_end),
        "report_date": _d(row.report_date), "report_date_source": row.report_date_source,
        "revenue": _s(row.actual_revenue), "revenue_currency": row.revenue_currency,
        "revenue_basis": row.revenue_basis, "revenue_source_url": row.revenue_source_url,
        "eps_diluted": _s(row.actual_eps), "eps_currency": row.eps_currency,
        "eps_source_url": row.eps_source_url,
        "consensus_status": row.consensus_status,
        "expectation": expectation,
        "revenue_vs_expectation": None if row.surprise_revenue_abs is None else {
            "difference": _s(row.surprise_revenue_abs), "pct": _s(row.surprise_revenue_pct)},
        "eps_vs_provider_estimate": eps_vs,
        "guidance": None if row.vs_guidance is None else {
            "metric": row.guidance_metric, "low": _s(row.guidance_low),
            "high": _s(row.guidance_high), "mid": _s(row.guidance_mid),
            "result": row.vs_guidance, "evidence_ref": row.guidance_evidence_ref,
            "pct_vs_mid": guidance.get("pct_vs_mid"),
        },
    }


def streak(rows: Sequence[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """Consecutive quarters, newest first, on the same side of the expectation."""
    direction, count = None, 0
    for row in rows:
        block = row.get(key)
        if not block or block.get("difference") is None:
            break
        diff = Decimal(block["difference"])
        side = "above" if diff > 0 else "below" if diff < 0 else "in_line"
        if direction is None:
            direction = side
        if side != direction:
            break
        count += 1
    return None if direction is None else {"direction": direction, "quarters": count}


def earnings_track_record_block(rows: Sequence[Any]) -> dict[str, Any]:
    """The report block from ``earnings_results`` rows (any order)."""
    quarters = sorted((r for r in rows if r.fiscal_quarter != 0),
                      key=lambda r: r.period_end, reverse=True)[:QUARTERS_SHOWN]
    years = sorted((r for r in rows if r.fiscal_quarter == 0),
                   key=lambda r: r.period_end, reverse=True)[:YEARS_SHOWN]
    if not quarters and not years:
        return {"state": "not_researched", "reason": "no_filed_quarterly_results",
                "note": "No quarterly results from SEC filings are held for this company."}
    q_views = [_row_view(r) for r in quarters]
    statuses = [v["consensus_status"] for v in q_views if v["consensus_status"]]
    return {
        "state": "held",
        "title": "Earnings track record",
        "quarters": q_views,
        "years": [_row_view(r) for r in years],
        "eps_streak": streak(q_views, "eps_vs_provider_estimate"),
        "revenue_streak": streak(q_views, "revenue_vs_expectation"),
        "expectations_available": sum(1 for s in statuses if s in {"available", "thin"}),
        "note": NOTE,
    }


async def earnings_track_record_section(session: Any, company: Any, *,
                                        cfg: Any) -> dict[str, Any] | None:
    """The block for one company, or None when the flag is off (report unchanged)."""
    if not getattr(cfg, "v3_earnings_track_record_enabled", False):
        return None
    from sqlalchemy import select

    from app.models.earnings import EarningsResult

    try:
        rows = (await session.execute(select(EarningsResult).where(
            EarningsResult.company_id == company.id))).scalars().all()
    except Exception:  # noqa: BLE001 - the report is still written without the block
        return None
    return earnings_track_record_block(rows)


__all__ = [
    "EARNINGS_SURPRISE_STRATEGY",
    "apply_surprises",
    "apply_to_row",
    "earnings_surprise_step",
    "earnings_track_record_block",
    "earnings_track_record_section",
    "status_for",
    "streak",
]
