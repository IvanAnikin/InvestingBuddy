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


_MILLION = 1_000_000


def balance_from_normalized(n: Any) -> dict[str, Any] | None:
    """Net-debt inputs from the SEC normaliser's annual record (ADR-118), or None.

    The issuer's inline XBRL never yields a ``total_debt`` fact (no single US-GAAP tag
    is total debt, ADR-067), so a US filer without a "total debt" text line had no net
    debt at all. The normaliser's total is built from one compatible combination of
    concepts at the reporting period; it is used only when that rule published it, and
    only beside the same record's cash for the same period.
    """
    from app.services.agent_tools.peers import total_debt_is_usable

    if n is None or getattr(n, "period_basis", None) != "annual":
        return None
    if (getattr(n, "reporting_currency", "USD") or "USD").upper() != "USD":
        return None
    cash = getattr(n, "cash_and_equivalents", None)
    if cash is None or "cash_and_equivalents" in (n.withheld_fields or {}):
        return None
    if not total_debt_is_usable(n):
        return None
    source = getattr(n, "source_url", None)
    basis = getattr(n, "debt_basis", None) or {}
    return {
        "period": f"FY{n.fiscal_year}" if n.fiscal_year else n.reporting_period_end,
        "currency": "USD",
        "total_debt": absolute(n.total_debt * _MILLION, None),
        "cash": absolute(cash * _MILLION, None),
        "filed": getattr(n, "filed_date", None),
        "sources": [
            {"label": "total_debt", "source_url": source, "fact_id": None,
             "formula": basis.get("formula")},
            {"label": "cash_and_equivalents", "source_url": source, "fact_id": None},
        ],
        "origin": "sec_xbrl_normalized",
    }


_CASH_CONCEPT = "CashAndCashEquivalentsAtCarryingValue"


def quarterly_balance_from_facts(facts: dict[str, Any]) -> dict[str, Any] | None:
    """Net-debt inputs at the newest 10-Q balance sheet, or None (ADR-120).

    The annual record is up to a year old: Rapid7's FY2025 balance showed 892m of
    convertibles due after a year, while its Q3 2026 10-Q had 599m of them due within
    twelve months. Same ADR-067/118 rules (``derive_total_debt``) at the 10-Q's own
    balance-sheet date and accession; cash is the same filing's tagged figure.
    """
    from app.integrations.sec_total_debt import derive_total_debt

    us_gaap = ((facts or {}).get("facts") or {}).get("us-gaap") or {}
    rows = [r for r in ((us_gaap.get(_CASH_CONCEPT) or {}).get("units") or {}).get("USD") or []
            if r.get("form") == "10-Q" and not r.get("start") and r.get("val") is not None]
    if not rows:
        return None
    newest = max(rows, key=lambda r: (str(r.get("end") or ""), str(r.get("filed") or "")))
    same_filing = [r for r in rows if r.get("end") == newest.get("end")
                   and r.get("accn") == newest.get("accn")]
    cash = same_filing[0] if same_filing else newest
    debt = derive_total_debt(us_gaap, period_end=str(newest["end"]), forms=("10-Q",),
                             prefer_accn=newest.get("accn"), tolerance_days=10)
    if debt.total is None or debt.withheld_reason is not None or debt.long_term_missing:
        return None
    cik = str((facts or {}).get("cik") or "").strip()
    source = (f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
              if cik.isdigit() else None)
    return {
        "period": f"{newest['end']} (10-Q)",
        "period_end": str(newest["end"]),
        "currency": "USD",
        "total_debt": absolute(debt.total * _MILLION, None),
        "cash": absolute(float(cash["val"]), None),
        "filed": cash.get("filed"),
        "sources": [
            {"label": "total_debt", "source_url": source, "fact_id": None,
             "formula": debt.formula, "accn": newest.get("accn")},
            {"label": "cash_and_equivalents", "source_url": source, "fact_id": None,
             "accn": cash.get("accn")},
        ],
        "current_debt": None if debt.current is None else absolute(
            debt.current * _MILLION, None),
        "origin": "sec_xbrl_10q",
    }


def newest_balance(annual: dict[str, Any] | None,
                   quarterly: dict[str, Any] | None) -> dict[str, Any] | None:
    """The more recent of the two (the 10-Q when it was filed after the 10-K)."""
    if quarterly is None:
        return annual
    if annual is None:
        return quarterly
    return quarterly if str(quarterly.get("filed") or "") > str(annual.get("filed") or "") \
        else annual


async def sec_balance(company: Any) -> dict[str, Any] | None:
    """``balance_from_normalized`` over the company's SEC companyfacts. Never raises."""
    import asyncio

    from app.integrations.providers.sec_edgar_fundamentals import SecEdgarFundamentalsProvider
    from app.integrations.sec_fundamentals_normalizer import normalize_company_facts
    from app.services.agent_tools.peers import _facts_for

    ticker = getattr(company, "ticker", None)
    if not ticker:
        return None
    try:
        facts, _reason = await _facts_for(ticker, getattr(company, "exchange", None),
                                          SecEdgarFundamentalsProvider())
        if facts is None:
            return None
        n = await asyncio.to_thread(normalize_company_facts, facts, ticker)
        return newest_balance(balance_from_normalized(n), quarterly_balance_from_facts(facts))
    except Exception:  # noqa: BLE001 - an additive input never costs the report
        logger.warning("SEC balance for enterprise value failed")
        return None


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
        if balance is None and getattr(cfg, "v3_enterprise_value_sec_debt_enabled", False):
            balance = await sec_balance(company)
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
