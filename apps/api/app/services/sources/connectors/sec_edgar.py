"""
SEC EDGAR connector — Phase 29A reference migration.

This is the one connector wired to demonstrate the framework end-to-end,
including the transport-vs-content tiering that the whole phase turns on:

  transport = SEC EDGAR / data.sec.gov  →  T2_regulator_or_gov
  content   = the company's filing        →  T1_primary_filing

``fetch_filings`` maps an injected filings fetcher's output into typed
``EvidenceItem``s. In 29A the fetcher is not bound (no live calls), so the method
returns an informational, non-blocking result; Phase 29B binds the real
``SecRecentFilingsProvider``. Tests inject a fake fetcher to prove the mapping
and the tier pairing offline.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.services.sources.connector_base import (
    CompanyContext,
    ConnectorResult,
    QueryContext,
    SourceConnector,
)
from app.services.sources.evidence import build_evidence_item
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.rate_limit import RateLimitPolicy
from app.services.sources.taxonomy import (
    SEC_TRANSPORT_LABEL,
    ConnectorStatus,
    sec_tier_pair,
)

# A fetcher returns plain filing dicts so the connector stays decoupled from any
# concrete provider signature. Expected keys (all optional except a title-ish
# one): form_type, title, url, filed_date, summary, fields.
FilingsFetcher = Callable[
    [CompanyContext, QueryContext], Awaitable[list[dict[str, Any]]]
]


class SecEdgarConnector(SourceConnector):
    connector_key = "sec_edgar"
    supported_source_ids = ("sec_edgar",)
    status = ConnectorStatus.enabled

    def __init__(self, filings_fetcher: FilingsFetcher | None = None) -> None:
        self._fetcher = filings_fetcher
        self.rate_limit_policy = RateLimitPolicy(
            requests_per_minute=30,
            min_interval_seconds=0.2,
            max_concurrency=1,
            note="SEC fair-access: identify with a User-Agent, keep the rate low.",
        )

    async def fetch_filings(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        if self._fetcher is None:
            # Framework wired, live retrieval intentionally not enabled in 29A.
            gap = SourceGap(
                connector_key=self.connector_key,
                source_id="sec_edgar",
                gap_type=GapType.connector_planned,
                severity=GapSeverity.info,
                message="SEC EDGAR live filing retrieval is not enabled in this phase.",
                suggested_followup_phase="Phase 29B",
                blocks_research_complete=False,
            )
            return ConnectorResult(
                connector_key=self.connector_key,
                warnings=["SEC EDGAR fetcher not bound; live retrieval lands in 29B."],
                source_gaps=[gap],
            )

        start = time.monotonic()
        raw = await self._fetcher(company, query)
        transport_tier, content_tier = sec_tier_pair()
        items = []
        issuer = company.company_name or company.ticker or "Issuer"
        for i, f in enumerate(raw[: max(1, query.max_items)], start=1):
            title = f.get("title") or f.get("form_type") or "SEC filing"
            items.append(
                build_evidence_item(
                    id=f"SEC{i}",
                    source_id="sec_edgar",
                    source_name="SEC EDGAR",
                    provider_transport=SEC_TRANSPORT_LABEL,
                    provider_transport_tier=transport_tier,
                    content_source=f"{issuer} {title}".strip(),
                    content_source_tier=content_tier,
                    source_type="company_filing",
                    title=str(title),
                    url=f.get("url"),
                    date=str(f.get("filed_date")) if f.get("filed_date") else None,
                    excerpt=f.get("summary") or f.get("title"),
                    fields_supported=list(f.get("fields") or []),
                    data_quality=f.get("data_quality"),
                )
            )
        latency_ms = int((time.monotonic() - start) * 1000)
        return ConnectorResult(
            connector_key=self.connector_key,
            evidence_items=items,
            latency_ms=latency_ms,
        )


__all__ = ["SecEdgarConnector", "FilingsFetcher"]
