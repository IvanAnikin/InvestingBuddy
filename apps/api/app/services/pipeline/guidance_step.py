"""Guidance vs actual — V3.20 slice 4.3 (spec §3.4).

For a company's ``earnings_results`` rows, find the guidance the company gave for the
same fiscal period in the research ledger and say whether the actual landed within,
above or below it.

THE RULES
=========
* **Only stated guidance.** Ledger findings with ``statement_kind = "guidance"`` (ADR-088)
  that the Red Team has not withdrawn; their figures read by ``guidance_figures`` (one
  unambiguous revenue / EPS figure per clause, or nothing).
* **Same period.** The finding's ``period_key`` must equal the row's label (``2026`` for
  the fiscal year, ``2026-Q3`` for a quarter) — the filer's fiscal calendar on both sides.
* **Guidance comes before the result.** Only a finding whose cited evidence was
  published before the row's ``report_date``; of those, the newest (the guidance in
  force when the result was announced). A finding with no publication date is skipped
  and counted — it cannot be placed before or after the print.
* **Same currency.** The guided currency must equal the actual's. A guided figure with
  no currency sign is accepted only for EPS (per-share, currency from the actual).
* **Revenue before EPS.** One metric per row (the table has one guidance slot): revenue
  when guided, else EPS.

``vs_guidance``: ``within`` / ``above`` / ``below`` a range; ``above_point`` /
``below_point`` / ``at_point`` for a single figure (an "approximately" figure is a point,
and the difference is stated, never rounded into "in line").
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.services.sources.guidance_figures import (
    METRIC_EPS,
    METRIC_REVENUE,
    GuidedFigure,
    guided_figures,
)

GUIDANCE_STRATEGY = "ledger_guidance_v1"


@dataclass(frozen=True)
class GuidanceCandidate:
    finding_id: uuid.UUID
    period_key: str
    published: date | None
    figure: GuidedFigure


@dataclass
class GuidanceMatch:
    label: str
    metric: str
    figure: GuidedFigure
    finding_id: uuid.UUID
    published: date
    actual: Decimal
    vs: str
    diff_pct: float | None


@dataclass
class GuidanceReport:
    matched: list[GuidanceMatch] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def compare(actual: Decimal, figure: GuidedFigure) -> tuple[str, float | None]:
    """``(vs_guidance, % difference from the mid / point)``."""
    mid = figure.mid
    diff = (round(float((actual - mid) / abs(mid) * 100), 2)
            if mid is not None and mid != 0 else None)
    if figure.point is not None:
        if actual > figure.point:
            return "above_point", diff
        if actual < figure.point:
            return "below_point", diff
        return "at_point", diff
    assert figure.low is not None and figure.high is not None
    if actual > figure.high:
        return "above", diff
    if actual < figure.low:
        return "below", diff
    return "within", diff


def _actual(row: Any, metric: str) -> tuple[Decimal | None, str | None]:
    if metric == METRIC_REVENUE:
        return row.actual_revenue, row.revenue_currency
    return row.actual_eps, row.eps_currency


def match_rows(rows: list[Any], candidates: list[GuidanceCandidate]) -> GuidanceReport:
    """Pure: the best guidance per row (see the module rules)."""
    report = GuidanceReport()
    for row in rows:
        if row.report_date is None:
            report.skip("row_has_no_report_date")
            continue
        for metric in (METRIC_REVENUE, METRIC_EPS):
            actual, currency = _actual(row, metric)
            if actual is None:
                continue
            usable: list[GuidanceCandidate] = []
            for c in candidates:
                if c.period_key != row.fiscal_period_label or c.figure.metric != metric:
                    continue
                if c.published is None:
                    report.skip("guidance_publication_date_unknown")
                    continue
                if c.published >= row.report_date:
                    report.skip("guidance_published_on_or_after_the_result")
                    continue
                guided_currency = c.figure.currency or (currency if metric == METRIC_EPS
                                                        else None)
                if guided_currency != currency:
                    report.skip("guidance_currency_differs_or_unstated")
                    continue
                usable.append(c)
            if not usable:
                continue
            best = max(usable, key=lambda c: (c.published, str(c.finding_id)))
            assert best.published is not None
            vs, diff = compare(actual, best.figure)
            report.matched.append(GuidanceMatch(
                label=row.fiscal_period_label, metric=metric, figure=best.figure,
                finding_id=best.finding_id, published=best.published, actual=actual,
                vs=vs, diff_pct=diff))
            break  # revenue first; EPS only when revenue was not guided
    return report


async def _candidates(session: Any, company_id: uuid.UUID) -> list[GuidanceCandidate]:
    from sqlalchemy import select

    from app.models.ledger import ResearchFinding, ResearchRun

    rows = (await session.execute(
        select(ResearchFinding.id, ResearchFinding.period_key, ResearchFinding.statement,
               ResearchFinding.source_published_at)
        .join(ResearchRun, ResearchRun.id == ResearchFinding.research_run_id)
        .where(ResearchRun.company_id == company_id,
               ResearchFinding.statement_kind == "guidance",
               ResearchFinding.verification_status != "withdrawn",
               ResearchFinding.period_key.is_not(None)))).all()
    out: list[GuidanceCandidate] = []
    for finding_id, period_key, statement, published in rows:
        for figure in guided_figures(statement or ""):
            out.append(GuidanceCandidate(finding_id=finding_id, period_key=period_key,
                                         published=published, figure=figure))
    return out


async def guidance_step(session: Any, company: Any, *, cfg: Any) -> dict[str, Any]:
    """Write the guidance columns of the company's ``earnings_results``. Never raises."""
    if not getattr(cfg, "v3_earnings_guidance_enabled", False):
        return {"state": "disabled"}
    try:
        from sqlalchemy import select

        from app.models.earnings import EarningsResult

        async with session.begin_nested():
            rows = list((await session.execute(select(EarningsResult).where(
                EarningsResult.company_id == company.id))).scalars())
            if not rows:
                return {"state": "no_actuals"}
            candidates = await _candidates(session, company.id)
            if not candidates:
                return {"state": "no_guidance_figures", "rows": len(rows)}
            report = match_rows(rows, candidates)
            by_label = {r.fiscal_period_label: r for r in rows}
            for m in report.matched:
                row = by_label[m.label]
                f = m.figure
                row.guidance_metric = m.metric
                row.guidance_low, row.guidance_high = f.low, f.high
                row.guidance_mid = f.mid
                row.guidance_evidence_ref = f"finding:{m.finding_id}"
                row.vs_guidance = m.vs
                detail = dict(row.detail_json or {})
                detail["guidance"] = {
                    "strategy": GUIDANCE_STRATEGY, "text": f.text,
                    "currency": f.currency, "published": m.published.isoformat(),
                    "diff_pct_from_mid": m.diff_pct, "kind": "point" if f.point is not None
                    else "range",
                }
                row.detail_json = detail
            await session.flush()
        return {"state": "matched" if report.matched else "none_matched",
                "matched": [{"period": m.label, "metric": m.metric, "vs": m.vs}
                            for m in report.matched],
                "candidates": len(candidates), "skipped": report.skipped}
    except Exception as exc:  # noqa: BLE001 - guidance columns are optional context
        return {"state": "error", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


__all__ = ["GUIDANCE_STRATEGY", "GuidanceCandidate", "compare", "guidance_step",
           "match_rows"]
