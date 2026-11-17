"""V3.20 slice 8.1 — gather the EV inputs and build the report block.

* Market cap — the run's dated observation (slice 1.3), native currency; with no thesis
  observation, an analysis-time re-read only when ``V3_ENTERPRISE_VALUE_RECHECK_ENABLED``
  (an exchange-directory read; off by default).
* Net debt and EBIT — the issuer's own group-scope facts (``ExtractedFact``, inline XBRL
  first): the newest ANNUAL period holding both ``total_debt`` and
  ``cash_and_equivalents`` in one currency, and the newest annual ``operating_profit``.
* Capacity — the disclosed readings of the real-asset block (slice 5.4), when present.

``V3_ENTERPRISE_VALUE_ENABLED``. Never raises; ``None`` when off or on failure.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.valuation.enterprise_value import absolute, enterprise_value_block

logger = logging.getLogger(__name__)

LABELS = ("total_debt", "cash_and_equivalents", "operating_profit")
MAX_FACTS = 400


async def load_fact_rows(session: Any, company: Any,
                         labels: Sequence[str] = LABELS) -> list[dict[str, Any]]:
    from sqlalchemy import case, select

    from app.models.extracted_document import ExtractedDocument, ExtractedFact
    from app.services.sources.company_documents import company_documents_clause
    from app.services.sources.inline_xbrl_facts import TAGGED_EXTRACTION_METHODS

    stmt = (
        select(ExtractedFact.id, ExtractedFact.label, ExtractedFact.value_numeric,
               ExtractedFact.currency, ExtractedFact.scale, ExtractedFact.period,
               ExtractedDocument.doc_date, ExtractedDocument.canonical_url)
        .join(ExtractedDocument, ExtractedFact.extracted_document_id == ExtractedDocument.id)
        .where(
            company_documents_clause(company.id),
            ExtractedFact.is_active.is_(True),
            ExtractedFact.scope_type == "group",
            ExtractedFact.label.in_(list(labels)),
            ExtractedFact.value_numeric.is_not(None),
        )
        .order_by(case((ExtractedFact.extraction_method.in_(TAGGED_EXTRACTION_METHODS), 0),
                       else_=1), ExtractedFact.confidence.desc())
        .limit(MAX_FACTS)
    )
    async with session.begin_nested():
        rows = (await session.execute(stmt)).all()
    return [
        {"fact_id": str(fid), "label": label, "value": value, "currency": currency,
         "scale": scale, "period": period, "filed": doc_date.isoformat() if doc_date else None,
         "source_url": url}
        for fid, label, value, currency, scale, period, doc_date, url in rows
    ]


def pick_inputs(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any] | None,
                                                         dict[str, Any] | None]:
    """(balance, ebit) from fact rows. Annual periods only; the first row per (label,
    period, currency) wins (the query orders inline XBRL / confidence first)."""
    from app.services.sources.financial_period import PERIOD_TYPE_ANNUAL, parse_period

    best: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    years: dict[str, int] = {}
    for row in rows:
        period = parse_period(row.get("period"))
        pkey = period.key
        if period.period_type != PERIOD_TYPE_ANNUAL or period.year is None or not pkey:
            continue
        amount = absolute(row.get("value"), row.get("scale"))
        ccy = (row.get("currency") or "").upper()
        if amount is None or not ccy:
            continue
        key = (str(row["label"]), pkey, ccy)
        if key not in best:
            best[key] = {**row, "amount": amount, "currency": ccy}
            years[pkey] = period.year
    by_period: dict[tuple[str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for (label, pkey, ccy), row in best.items():
        by_period[(pkey, ccy)][label] = row
    balance = None
    for (pkey, ccy), labels in sorted(by_period.items(), key=lambda kv: years[kv[0][0]],
                                      reverse=True):
        if "total_debt" in labels and "cash_and_equivalents" in labels:
            debt, cash = labels["total_debt"], labels["cash_and_equivalents"]
            balance = {
                "period": debt.get("period") or pkey, "currency": ccy,
                "total_debt": debt["amount"],
                "cash": cash["amount"],
                "filed": max(str(debt.get("filed") or ""), str(cash.get("filed") or ""))
                or None,
                "sources": [{"label": r["label"], "source_url": r.get("source_url"),
                             "fact_id": r.get("fact_id")} for r in (debt, cash)],
            }
            break
    ebit = None
    for (pkey, ccy), labels in sorted(by_period.items(), key=lambda kv: years[kv[0][0]],
                                      reverse=True):
        if "operating_profit" in labels:
            r = labels["operating_profit"]
            ebit = {"period": r.get("period") or pkey, "currency": ccy, "value": r["amount"],
                    "filed": r.get("filed"), "source_url": r.get("source_url")}
            break
    return balance, ebit


def market_cap_from(observation: Any) -> dict[str, Any] | None:
    """The observation in its native currency (USD when it was a USD figure)."""
    if observation is None:
        return None
    amount = getattr(observation, "native_amount", None)
    ccy = getattr(observation, "native_currency", None)
    if amount is None:
        amount, ccy = getattr(observation, "market_cap_usd", None), "USD"
    if amount is None:
        return None
    return {"amount": amount, "currency": (ccy or "").upper() or None,
            "as_of": getattr(observation, "as_of", None),
            "source_url": getattr(observation, "source_url", None),
            "method": getattr(observation, "method", None),
            "lower_bound": bool(getattr(observation, "is_lower_bound", False))}


async def enterprise_value_section(
    session: Any, company: Any, *, cfg: Any, observation: Any = None,
    real_asset: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not getattr(cfg, "v3_enterprise_value_enabled", False):
        return None
    try:
        if observation is None and getattr(cfg, "v3_enterprise_value_recheck_enabled", False):
            from app.services.discovery.size_recheck import recheck_market_cap

            observation, _reason = await recheck_market_cap(
                name=getattr(company, "name", None), ticker=getattr(company, "ticker", None),
                venue=getattr(company, "exchange", None), isin=getattr(company, "isin", None),
                cfg=cfg)
        balance, ebit = pick_inputs(await load_fact_rows(session, company))
        capacity = list(((real_asset or {}).get("capacity") or {}).get("readings") or [])
        market_cap = market_cap_from(observation)
        if market_cap is None and balance is None:
            return None
        return enterprise_value_block(market_cap=market_cap, balance=balance, ebit=ebit,
                                      capacity=capacity)
    except Exception:  # noqa: BLE001 - an additive block never costs the report
        logger.warning("enterprise value block failed")
        return None


__all__ = ["enterprise_value_section", "load_fact_rows", "market_cap_from", "pick_inputs"]
