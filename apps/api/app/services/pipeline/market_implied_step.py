"""V3.20 slice 8.4 — the "What today's price implies" block for one run.

Reads the newest annual group-scope revenue, operating cash flow and capital expenditure
(inline XBRL first), then reverses today's enterprise value against them and the
layer-peer median (``valuation.market_implied``). ``None`` when the flag is off or there is
no computed EV, so every other report is byte-identical. Never raises.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.valuation.enterprise_value import absolute
from app.services.valuation.market_implied import market_implied_block

logger = logging.getLogger(__name__)

LABELS = ("revenue", "operating_cash_flow", "capital_expenditure")


def pick_annual(rows: Sequence[Mapping[str, Any]], currency: str | None
                ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(revenue, cash_flow) — each from the newest annual period that has it in
    ``currency``; cash flow needs both operating cash flow and capex in ONE period."""
    from app.services.sources.financial_period import PERIOD_TYPE_ANNUAL, parse_period

    if not currency:
        return None, None
    by_period: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    years: dict[str, int] = {}
    for row in rows:
        period = parse_period(row.get("period"))
        if (period.period_type != PERIOD_TYPE_ANNUAL or period.year is None
                or not period.key or (row.get("currency") or "").upper() != currency):
            continue
        amount = absolute(row.get("value"), row.get("scale"))
        if amount is None:
            continue
        slot = by_period[period.key]
        if row["label"] not in slot:  # the query orders inline XBRL / confidence first
            slot[str(row["label"])] = {**row, "amount": amount}
            years[period.key] = period.year
    ordered = sorted(by_period.items(), key=lambda kv: years[kv[0]], reverse=True)
    revenue = cash_flow = None
    for key, labels in ordered:
        if revenue is None and "revenue" in labels:
            r = labels["revenue"]
            revenue = {"value": r["amount"], "currency": currency,
                       "period": r.get("period") or key, "source_url": r.get("source_url")}
        if cash_flow is None and {"operating_cash_flow", "capital_expenditure"} <= labels.keys():
            cfo, capex = labels["operating_cash_flow"], labels["capital_expenditure"]
            cash_flow = {"operating_cash_flow": cfo["amount"],
                         "capital_expenditure": capex["amount"], "currency": currency,
                         "period": cfo.get("period") or key,
                         "sources": [{"label": x["label"], "source_url": x.get("source_url"),
                                      "fact_id": x.get("fact_id")} for x in (cfo, capex)]}
    return revenue, cash_flow


async def market_implied_section(
    session: Any, company: Any, *, cfg: Any, ev_block: Mapping[str, Any] | None,
    peers_block: Mapping[str, Any] | None, blocks: Mapping[str, Mapping[str, Any] | None],
    size_fit: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not getattr(cfg, "v3_market_implied_enabled", False) or not ev_block:
        return None
    try:
        from app.services.pipeline.enterprise_value_step import load_fact_rows

        ev = ev_block.get("enterprise_value") or {}
        if ev.get("state") != "computed":
            return None
        rows = await load_fact_rows(session, company, labels=LABELS)
        revenue, cash_flow = pick_annual(rows, ev.get("currency"))
        return market_implied_block(ev_block=ev_block, peers_block=peers_block,
                                    revenue=revenue, cash_flow=cash_flow, blocks=blocks,
                                    size_fit=size_fit)
    except Exception:  # noqa: BLE001 - an additive block never costs the report
        logger.warning("market-implied block failed")
        return None


__all__ = ["LABELS", "market_implied_section", "pick_annual"]
