"""The comparable-set record — V3.20 slice 9.1 (spec §3.10).

One structured record per completed research run, READ from the run's own report blocks
(never re-derived), so the memo, the report and the record cannot disagree:

* ``business_model.electricity_layers`` → ``layer``
* ``valuation_context.enterprise_value`` → EV, currency, EV/EBIT, market cap
* ``valuation_context.obscurity`` → ``obscurity_score``
* the size check (``thesis.size_fit``) → USD market cap and its date
* every block above plus ``operations.real_asset``, ``growth_and_catalysts.change_events``,
  ``financial_capacity.earnings_track_record``, ``valuation_context.passive_ownership``
  and ``business_model.relationships`` (slice 9.4 diffs it) verbatim in ``record_json``.

A missing block is a NULL column, never zero. ``comparable_set`` returns the newest
record per company in one layer — what peers-by-layer (slice 8.2) reads.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

RECORD_VERSION = "2026-11-16.1"

#: (section key, block key) read verbatim into ``record_json``.
RECORD_BLOCKS: tuple[tuple[str, str], ...] = (
    ("business_model", "electricity_layers"),
    ("business_model", "relationships"),
    ("operations", "real_asset"),
    ("valuation_context", "enterprise_value"),
    ("valuation_context", "market_implied"),
    ("valuation_context", "obscurity"),
    ("valuation_context", "passive_ownership"),
    ("growth_and_catalysts", "change_events"),
    ("financial_capacity", "earnings_track_record"),
)


def _block(report: Mapping[str, Any] | None, section: str, key: str) -> Any:
    for s in (report or {}).get("sections") or []:
        if isinstance(s, Mapping) and s.get("key") == section:
            return s.get(key)
    return None


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def valuation_summary(report: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The report's top-level ``valuation`` block, compacted for the record."""
    block = (report or {}).get("valuation")
    if not isinstance(block, Mapping):
        return None
    return {"block_version": block.get("block_version"),
            "valuation_range": block.get("valuation_range"),
            "price_reference": block.get("price_reference"),
            "relative_position": block.get("relative_position"),
            "upside_downside_pct": block.get("upside_downside_pct")}


def build_record(
    *,
    report: Mapping[str, Any] | None,
    company: Any,
    size_fit: Mapping[str, Any] | None,
    as_of: date,
) -> dict[str, Any]:
    """The record's column values (pure)."""
    blocks = {f"{section}.{key}": _block(report, section, key)
              for section, key in RECORD_BLOCKS}
    layers = blocks["business_model.electricity_layers"] or {}
    ev_block = blocks["valuation_context.enterprise_value"] or {}
    ev = ev_block.get("enterprise_value") or {}
    computed = ev.get("state") == "computed"
    ev_to_ebit = next((m.get("value") for m in ev_block.get("multiples") or []
                       if m.get("key") == "ev_to_ebit" and m.get("state") == "computed"),
                      None)
    obscurity = blocks["valuation_context.obscurity"] or {}
    provenance = (size_fit or {}).get("provenance") or size_fit or {}
    return {
        "as_of": as_of,
        "record_version": RECORD_VERSION,
        "ticker": getattr(company, "ticker", None),
        "exchange": getattr(company, "exchange", None),
        "sector": getattr(company, "sector", None),
        "industry": getattr(company, "industry", None),
        "layer": layers.get("primary_layer") if layers.get("status") == "classified" else None,
        "market_cap_usd": _dec((size_fit or {}).get("market_cap_usd")),
        "market_cap_as_of": _day(provenance.get("market_cap_as_of")),
        "enterprise_value": _dec(ev.get("value")) if computed else None,
        "ev_currency": (ev.get("currency") or None) if computed else None,
        "ev_to_ebit": _dec(ev_to_ebit),
        "obscurity_score": obscurity.get("score") if isinstance(obscurity.get("score"),
                                                                int) else None,
        # V3.20 slice 9.2 — the computed verdict block, only when the report carries one.
        "rubric_json": (dict(verdict) if isinstance(
            verdict := (report or {}).get("verdict"), Mapping) else None),
        "record_json": {
            "record_version": RECORD_VERSION,
            "report_version": (report or {}).get("version"),
            "size_fit": dict(size_fit) if size_fit else None,
            "blocks": {k: v for k, v in blocks.items() if v is not None},
            # V3.20 slice 8.6 wiring — the range and its price, not the whole engine output.
            **({"valuation": valuation_summary(report)} if valuation_summary(report) else {}),
        },
    }


async def write_record(
    session: Any, *, company: Any, research_run_id: Any, report: Mapping[str, Any] | None,
    size_fit: Mapping[str, Any] | None, cfg: Any, as_of: date | None = None,
) -> bool:
    """Insert the run's record. Off, no report, or any failure: nothing. Never raises."""
    if not getattr(cfg, "v3_research_records_enabled", False) or not report:
        return False
    from app.models.research_record import CompanyResearchRecord

    try:
        values = build_record(report=report, company=company, size_fit=size_fit,
                              as_of=as_of or date.today())
        async with session.begin_nested():
            session.add(CompanyResearchRecord(
                id=uuid.uuid4(), company_id=company.id,
                research_run_id=research_run_id, **values))
            await session.flush()
        return True
    except Exception:  # noqa: BLE001 - the record is additive; the run already succeeded
        logger.warning("company research record write failed")
        return False


async def comparable_set(
    session: Any, layer: str, *, exclude_company_id: Any = None, limit: int = 25,
) -> list[Any]:
    """The newest record per company in ``layer``, newest first."""
    from sqlalchemy import func, select

    from app.models.research_record import CompanyResearchRecord as R

    ranked = (
        select(R.id, func.row_number().over(
            partition_by=R.company_id, order_by=(R.as_of.desc(), R.created_at.desc())
        ).label("rn"))
        .where(R.layer == layer)
        .subquery()
    )
    stmt = (select(R).join(ranked, ranked.c.id == R.id).where(ranked.c.rn == 1)
            .order_by(R.as_of.desc()).limit(limit))
    if exclude_company_id is not None:
        stmt = stmt.where(R.company_id != exclude_company_id)
    async with session.begin_nested():
        return list((await session.execute(stmt)).scalars().all())


__all__ = ["RECORD_BLOCKS", "RECORD_VERSION", "build_record", "comparable_set",
           "write_record"]
