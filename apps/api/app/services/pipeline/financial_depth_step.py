"""V3.20 slice 8.5 — the "Financial depth" block (financial_capacity section).

For a company with an SEC CIK: nine ``companyconcept`` reads (the six debt-maturity
buckets, diluted shares, contract assets, contract liabilities) through the SEC fetch
seam — research User-Agent and the SEC rate limiter. An untagged concept (HTTP 404) is
``not_disclosed``. ``V3_FINANCIAL_DEPTH_ENABLED``. Never raises; ``None`` when off, with
no CIK, when nothing is tagged, or on failure.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.services.sources.sec_actuals import concept_url, filing_url
from app.services.sources.sec_financial_depth import ALL_CONCEPTS, financial_depth_block

logger = logging.getLogger(__name__)


async def _live_fetch(url: str, accept: str) -> tuple[int, bytes | None]:  # pragma: no cover
    from app.integrations.sec_relationships import _default_fetch

    return await _default_fetch(url, accept)


async def financial_depth_section(session: Any, company: Any, *, cfg: Any,
                                  fetch: Any = None) -> dict[str, Any] | None:
    if not getattr(cfg, "v3_financial_depth_enabled", False):
        return None
    try:
        from app.services.pipeline.earnings_step import _company_cik

        async with session.begin_nested():
            cik = await _company_cik(session, company)
        if not cik:
            return None
        get = fetch or _live_fetch
        payloads: dict[str, Any] = {}
        for concept in ALL_CONCEPTS:
            status, body = await get(concept_url(cik, concept), "application/json")
            if status == 200 and body:
                loaded = json.loads(body)
                payloads[concept] = loaded if isinstance(loaded, dict) else None
            elif status not in (200, 404):
                raise RuntimeError(f"SEC HTTP {status}")
        return financial_depth_block(
            payloads, url_for=lambda accession: filing_url(cik, accession),
            retrieved_at=datetime.now(timezone.utc).date().isoformat())
    except Exception:  # noqa: BLE001 - an additive block never costs the report
        logger.warning("financial depth block failed")
        return None


__all__ = ["financial_depth_section"]
