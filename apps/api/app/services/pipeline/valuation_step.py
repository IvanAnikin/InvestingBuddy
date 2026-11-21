"""V3.20 slice 8.6 wiring — the report's ``valuation`` block for one run (ADR-099).

Reads the held annual facts (revenue history, operating cash flow, capex), the run's
enterprise-value, layer-peer and financial-depth blocks, the owner-entered equity risk
premium, and — for a USD company, when allowed — the newest FRED DGS10 observation as
the cited risk-free rate. Then runs the deterministic engine
(``valuation.engine_inputs``). ``None`` when off or there is no computed EV, so every other
report is byte-identical. Never raises.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.calculations.valuation_engine import Cited
from app.services.valuation.engine_inputs import (
    build_inputs,
    equity_risk_premium,
    valuation_block,
)

logger = logging.getLogger(__name__)

FRED_HOST = "fred.stlouisfed.org"
RISK_FREE_SERIES = "DGS10"
MAX_REVENUE_YEARS = 4


def risk_free_url() -> str:
    return f"https://{FRED_HOST}/graph/fredgraph.csv?id={RISK_FREE_SERIES}"


def parse_dgs10(body: str, url: str) -> Cited | None:
    """The newest published DGS10 observation, as a fraction. ``.`` rows are skipped."""
    try:
        rows = list(csv.reader(io.StringIO(body)))
    except csv.Error:
        return None
    for row in reversed(rows[1:]):
        if len(row) < 2:
            continue
        try:
            pct = float(row[1].strip())
        except ValueError:
            continue
        if 0 < pct < 20:
            return Cited(value=round(pct / 100, 6), source_url=url, period=row[0].strip(),
                         currency="USD")
    return None


async def fetch_risk_free(cfg: Any, *, fetcher: Any = None) -> Cited | None:
    from app.services.sources.document_fetcher import safe_fetch_document

    url = risk_free_url()
    result = await (fetcher or safe_fetch_document)(
        url, allowed_domains=(FRED_HOST,), cfg=cfg, resolve_ip=True,
        extra_text_content_types=("application/csv", "text/csv"))
    content = getattr(result, "content", None) if getattr(result, "ok", False) else None
    return parse_dgs10(content.decode("utf-8", "replace"), url) if content else None


def annual_revenues(rows: Sequence[Mapping[str, Any]], currency: str
                    ) -> list[dict[str, Any]]:
    """Oldest → newest annual revenue in ``currency`` (first row per period wins)."""
    from app.services.sources.financial_period import PERIOD_TYPE_ANNUAL, parse_period
    from app.services.valuation.enterprise_value import absolute

    seen: dict[int, dict[str, Any]] = {}
    for row in rows:
        if row.get("label") != "revenue":
            continue
        period = parse_period(row.get("period"))
        if (period.period_type != PERIOD_TYPE_ANNUAL or period.year is None
                or (row.get("currency") or "").upper() != currency):
            continue
        amount = absolute(row.get("value"), row.get("scale"))
        if amount is not None and period.year not in seen:
            seen[period.year] = {"value": float(amount),
                                 "period": row.get("period") or period.key,
                                 "source_url": row.get("source_url")}
    return [seen[y] for y in sorted(seen)][-MAX_REVENUE_YEARS:]


async def valuation_section(
    session: Any, company: Any, *, cfg: Any, ev_block: Mapping[str, Any] | None,
    peers_block: Mapping[str, Any] | None, depth_block: Mapping[str, Any] | None,
    size_fit: Mapping[str, Any] | None, fetcher: Any = None,
    precedents_block: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not getattr(cfg, "v3_valuation_engine_enabled", False) or not ev_block:
        return None
    try:
        from app.services.pipeline.enterprise_value_step import load_fact_rows
        from app.services.pipeline.market_implied_step import LABELS, pick_annual

        ev = ev_block.get("enterprise_value") or {}
        if ev.get("state") != "computed" or not ev.get("currency"):
            return None
        ccy = str(ev["currency"])
        rows = await load_fact_rows(session, company, labels=LABELS)
        _, cash_flow = pick_annual(rows, ccy)
        risk_free = None
        if ccy == "USD" and getattr(cfg, "v3_valuation_risk_free_fetch_enabled", False):
            try:
                risk_free = await fetch_risk_free(cfg, fetcher=fetcher)
            except Exception:  # noqa: BLE001 - one input, never the block
                logger.warning("risk-free rate fetch failed")
        cap = (size_fit or {}).get("market_cap_usd")
        inputs = build_inputs(
            ev_block=ev_block, peers_block=peers_block, depth_block=depth_block,
            cash_flow=cash_flow, revenue_history=annual_revenues(rows, ccy),
            risk_free=risk_free,
            erp=equity_risk_premium(getattr(cfg, "v3_equity_risk_premium_json", "")),
            market_cap_usd=float(cap) if isinstance(cap, int | float) else None,
            precedents_block=precedents_block)
        return valuation_block(inputs, ev_block)
    except Exception:  # noqa: BLE001 - an additive block never costs the report
        logger.warning("valuation block failed")
        return None


__all__ = ["annual_revenues", "fetch_risk_free", "parse_dgs10", "risk_free_url",
           "valuation_section"]
