"""V3.20 slice 6.2 — change events for the report (growth_and_catalysts section).

Two inputs, each behind its own flag:

* ``V3_CHANGE_EVENTS_ENABLED`` — classify the titles of the company's held primary
  disclosures (``document_coverage.current_inventory``) with the ``event_trigger``
  rules. No external call.
* ``V3_INSIDER_CLUSTERS_ENABLED`` — read the issuer's Form 4 filings from EDGAR (one
  submissions request plus up to ``MAX_FORM4_FILINGS`` documents, research User-Agent,
  SEC rate limiter) and detect open-market purchase clusters. Needs a CIK.

Returns ``None`` when off or when nothing was found, so every other report is
byte-identical. Never raises.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.services.discovery.change_events import change_events_block, events_from_documents

logger = logging.getLogger(__name__)


async def _live_form4(cik: str, today: date) -> Any:  # pragma: no cover - network seam
    from app.integrations.sec_form4 import fetch_form4_transactions

    return await fetch_form4_transactions(cik, today=today)


async def insider_section(session: Any, company: Any, *, today: date,
                          lookup: Any = None) -> dict[str, Any] | None:
    """Form 4 clusters for the company, or ``None`` without a CIK."""
    from app.integrations.sec_form4 import insider_clusters
    from app.services.pipeline.earnings_step import _company_cik

    async with session.begin_nested():
        cik = await _company_cik(session, company)
    if not cik:
        return None
    report = await (lookup or _live_form4)(cik, today)
    purchases = [t for t in report.transactions if t.is_open_market_purchase]
    return {
        "clusters": insider_clusters(report.transactions),
        "lookup": {
            "source": "SEC EDGAR Form 4",
            "cik": report.cik,
            "filings_available": report.filings_available,
            "filings_read": report.filings_read,
            "truncated": report.truncated,
            "open_market_purchases": len(purchases),
            "retrieved_at": today.isoformat(),
            "notes": list(report.notes)[:10],
        },
    }


async def change_events_section(
    session: Any, company: Any, *, cfg: Any, today: date | None = None,
    form4_lookup: Any = None,
) -> dict[str, Any] | None:
    """The report's "Change events" block, or ``None`` when off / empty / on failure."""
    docs_on = getattr(cfg, "v3_change_events_enabled", False)
    insider_on = getattr(cfg, "v3_insider_clusters_enabled", False)
    if not (docs_on or insider_on):
        return None
    today = today or date.today()
    classified: dict[str, Any] = {"events": [], "negated": [], "considered": 0,
                                  "undated": 0}
    insider: dict[str, Any] | None = None
    try:
        if docs_on:
            from app.services.pipeline.document_coverage import current_inventory

            inventory = await current_inventory(session, company.id)
            classified = events_from_documents(inventory or [], today=today)
        if insider_on:
            try:
                insider = await insider_section(session, company, today=today,
                                                lookup=form4_lookup)
            except Exception:  # noqa: BLE001 - one input, never the block
                logger.warning("Form 4 insider lookup failed")
        return change_events_block(classified=classified, insider=insider)
    except Exception:  # noqa: BLE001 - an additive block never costs the report
        logger.warning("change events failed")
        return None


__all__ = ["change_events_section", "insider_section"]
