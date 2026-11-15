"""Earnings actuals step — V3.20 slice 4.1 (spec §3.4).

During company research, for a company with an SEC CIK: read revenue and diluted EPS
per fiscal quarter from SEC XBRL (``sources.sec_actuals``) and upsert
``earnings_results``. Only the actual / report-date columns are written; consensus,
guidance and surprise columns are never touched here.

About six small SEC requests per company (four revenue concepts, EPS, submissions),
with the research User-Agent. States: ``disabled`` (flag off), ``no_cik``, ``error``,
``no_quarters`` (nothing tagged), ``written``. Never raises.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.services.sources.sec_actuals import (
    EPS_CONCEPT,
    REVENUE_CONCEPTS,
    QuarterActual,
    annual_actuals,
    concept_url,
    filing_url,
    quarterly_actuals,
)

EARNINGS_ACTUALS_STRATEGY = "sec_xbrl_quarterly_v1"


async def _company_cik(session: Any, company: Any) -> str | None:
    from sqlalchemy import select

    from app.models.legal_entity import EntityIdentifier

    entity_id = getattr(company, "legal_entity_id", None)
    if entity_id is None:
        return None
    return (await session.execute(
        select(EntityIdentifier.value_normalized).where(
            EntityIdentifier.legal_entity_id == entity_id, EntityIdentifier.scheme == "cik")
        .limit(1))).scalar_one_or_none()


async def _get_json(fetch: Any, url: str) -> dict[str, Any] | None:
    status, body = await fetch(url, "application/json")
    if status == 404 or not body:
        return None
    if status != 200:
        raise RuntimeError(f"SEC HTTP {status} for {url}")
    loaded = json.loads(body)
    return loaded if isinstance(loaded, dict) else None


def _currency(unit: str | None) -> str | None:
    head = (unit or "").split("/")[0].upper()
    return head if len(head) == 3 and head.isalpha() else None


async def record_actuals(session: Any, *, company_id: uuid.UUID, cik: str,
                         quarters: list[QuarterActual]) -> int:
    """Upsert the actual / report-date columns of each quarter. Returns rows written."""
    from sqlalchemy import select

    from app.models.earnings import ACTUALS_SEC_XBRL, EarningsResult

    now = datetime.now(timezone.utc)
    written = 0
    for q in quarters:
        row = (await session.execute(select(EarningsResult).where(
            EarningsResult.company_id == company_id,
            EarningsResult.fiscal_period_label == q.label))).scalar_one_or_none()
        if row is None:
            row = EarningsResult(id=uuid.uuid4(), company_id=company_id,
                                 fiscal_period_label=q.label, fiscal_year=q.fiscal_year,
                                 fiscal_quarter=q.fiscal_quarter, period_end=q.period_end)
            session.add(row)
        row.period_start, row.period_end = q.period_start, q.period_end
        row.report_date, row.report_date_source = q.report_date, q.report_date_source
        row.report_date_accession = q.report_date_accession
        row.actuals_source, row.actuals_as_of = ACTUALS_SEC_XBRL, now
        if q.revenue is not None:
            r = q.revenue
            row.actual_revenue, row.actual_revenue_latest = r.value, r.latest
            row.revenue_currency, row.revenue_basis = _currency(r.unit), r.basis
            row.revenue_concept, row.revenue_accession = f"us-gaap:{r.concept}", r.accession
            row.revenue_source_url = filing_url(cik, r.accession)
        if q.eps is not None:
            e = q.eps
            row.actual_eps, row.actual_eps_latest = e.value, e.latest
            row.eps_currency, row.eps_concept = _currency(e.unit), f"us-gaap:{e.concept}"
            row.eps_accession, row.eps_source_url = e.accession, filing_url(cik, e.accession)
        detail = dict(row.detail_json or {})
        detail["actuals"] = {
            "strategy": EARNINGS_ACTUALS_STRATEGY, "notes": list(q.notes),
            "eps_restated": bool(q.eps and q.eps.restated),
            "revenue_restated": bool(q.revenue and q.revenue.restated),
            "periodic_form": q.periodic_form,
            "periodic_filed": q.periodic_filed.isoformat() if q.periodic_filed else None,
        }
        row.detail_json = detail
        written += 1
    await session.flush()
    return written


async def earnings_actuals_step(session: Any, company: Any, *, cfg: Any,
                                fetch: Any | None = None) -> dict[str, Any]:
    """Read and store SEC quarterly actuals for one company. Never raises."""
    if not getattr(cfg, "v3_earnings_actuals_enabled", False):
        return {"state": "disabled"}
    try:
        cik = await _company_cik(session, company)
        if not cik:
            return {"state": "no_cik",
                    "note": "No SEC CIK: quarterly actuals from SEC XBRL do not apply."}
        if fetch is None:  # pragma: no cover - network
            from app.integrations.sec_relationships import _default_fetch

            fetch = _default_fetch
        revenue = {c: await _get_json(fetch, concept_url(cik, c)) for c in REVENUE_CONCEPTS}
        eps = await _get_json(fetch, concept_url(cik, EPS_CONCEPT))
        submissions = await _get_json(
            fetch, f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
        held = {k: v for k, v in revenue.items() if v}
        quarters = quarterly_actuals(held, eps, submissions)
        years = annual_actuals(held, eps, submissions)
        if not quarters and not years:
            return {"state": "no_quarters", "cik": cik,
                    "note": "The filer tags no quarterly revenue or diluted EPS."}
        async with session.begin_nested():
            await record_actuals(session, company_id=company.id, cik=cik,
                                 quarters=quarters + years)
        newest = (quarters or years)[0]
        return {"state": "written", "cik": cik, "quarters": len(quarters),
                "years": len(years),
                "newest": newest.label, "newest_report_date":
                newest.report_date.isoformat() if newest.report_date else None,
                "restated_eps_quarters": [q.label for q in quarters
                                          if q.eps and q.eps.restated]}
    except Exception as exc:  # noqa: BLE001 - a missing table of actuals costs nothing else
        return {"state": "error", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


__all__ = ["EARNINGS_ACTUALS_STRATEGY", "earnings_actuals_step", "record_actuals"]
