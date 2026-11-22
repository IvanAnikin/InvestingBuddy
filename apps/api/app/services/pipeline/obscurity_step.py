"""V3.20 slice 6.3 — gather the obscurity components and build the report block.

Reads what the run already has (the passive-ownership and electricity-layer blocks, the
newest consensus snapshot, the company's labels and listing). The only optional external
call is a GDELT article count (``V3_OBSCURITY_NEWS_VOLUME_ENABLED``, off by default),
through a seam so tests never reach the network. Never raises.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import date
from typing import Any

from app.services.discovery.obscurity import obscurity_block

logger = logging.getLogger(__name__)

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

#: ``(query) -> JSON payload`` — the GDELT seam.
GdeltFetch = Callable[[str], Awaitable[Any]]


async def _gdelt_fetch(query: str) -> Any:  # pragma: no cover - network
    import httpx

    from app.core.outbound_identity import USER_AGENT_HEADER, research_user_agent

    params = {"query": query, "mode": "timelinevolraw", "timespan": "3m", "format": "json"}
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        resp = await client.get(GDELT_DOC_URL, params=params,
                                headers={USER_AGENT_HEADER: research_user_agent()})
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def gdelt_count(payload: Any) -> int | None:
    """Sum of a ``timelinevolraw`` series; ``None`` when the payload is not one."""
    if not isinstance(payload, dict):
        return None
    timeline = payload.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        return None
    data = timeline[0].get("data") if isinstance(timeline[0], dict) else None
    if not isinstance(data, list):
        return None
    total = 0
    for point in data:
        value = point.get("value") if isinstance(point, dict) else None
        if isinstance(value, (int, float)):
            total += int(value)
    return total


async def news_volume(name: str, *, fetch: GdeltFetch | None = None,
                      today: date | None = None) -> dict[str, Any] | None:
    if not name:
        return None
    payload = await (fetch or _gdelt_fetch)(f'"{name}" sourcelang:english')
    count = gdelt_count(payload)
    if count is None:
        return None
    return {"count": count, "as_of": (today or date.today()).isoformat(),
            "source": "GDELT 2.1 DOC API (3-month window)"}


async def newest_analyst_count(session: Any, company_id: Any) -> dict[str, Any] | None:
    from sqlalchemy import select

    from app.models.consensus import ConsensusSnapshot as S

    stmt = (select(S.eps_analyst_count, S.as_of, S.source)
            .where(S.company_id == company_id, S.eps_analyst_count.is_not(None))
            .order_by(S.as_of.desc(), S.period_end.asc()).limit(1))
    async with session.begin_nested():
        row = (await session.execute(stmt)).first()
    if row is None:
        return None
    count, as_of, source = row
    return {"count": int(count), "as_of": as_of.isoformat() if as_of else None,
            "source": source}


async def obscurity_section(
    session: Any, company: Any, *, cfg: Any,
    passive_block: Mapping[str, Any] | None,
    layers_block: Mapping[str, Any] | None,
    fetch: GdeltFetch | None = None,
    coverage: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The report's "Obscurity" block, or ``None`` when off or on failure.

    ``coverage`` (V3.21 slice 21.7) is the research-coverage step's obscurity input; it is
    ``None`` when ``V3_RESEARCH_COVERAGE_ENABLED`` is off, and the block is then unchanged.
    """
    if not getattr(cfg, "v3_obscurity_score_enabled", False):
        return None
    try:
        from app.services.exchange_registry import country_for_exchange

        analyst = await newest_analyst_count(session, company.id)
        news = None
        if getattr(cfg, "v3_obscurity_news_volume_enabled", False):
            try:
                news = await news_volume(getattr(company, "name", "") or "", fetch=fetch)
            except Exception:  # noqa: BLE001 - one component, never the block
                logger.warning("GDELT news volume failed")
        return obscurity_block(
            analyst=analyst, passive_block=passive_block, news=news,
            layers_block=layers_block, sector=getattr(company, "sector", None),
            industry=getattr(company, "industry", None),
            listing_country=country_for_exchange(getattr(company, "exchange", None)),
            coverage=coverage,
        )
    except Exception:  # noqa: BLE001 - an additive block never costs the report
        logger.warning("obscurity score failed")
        return None


__all__ = ["gdelt_count", "newest_analyst_count", "news_volume", "obscurity_section"]
