"""
SEC EDGAR connector — Phase 29B filing connector.

The reference connector for the whole framework, including the
transport-vs-content tiering the phase turns on:

  transport = SEC EDGAR / data.sec.gov  →  T2_regulator_or_gov
  content   = the company's filing        →  T1_primary_filing

``fetch_filings`` maps an injected filings fetcher's output into typed
``EvidenceItem``s. The fetcher returns plain filing dicts (form_type, title,
url, filed_date, summary, fields) so the connector stays decoupled from any
concrete provider signature:

  * In the single-company evidence flow the fetcher replays filing metadata the
    workflow already retrieved (``catalyst_discovery.filing_events``) — no new
    network call, fully deterministic.
  * In the read-only evidence-preview endpoint a bounded live fetcher backed by
    ``SecRecentFilingsProvider`` may be injected (gated by config).

Guarantees:
  * Non-US issuers are gated by exchange-aware SEC eligibility (Phase 27.1A):
    a non-eligible exchange yields an honest ``source_not_eligible`` gap, never
    a wrong-CIK lookup and never fabricated filings.
  * Filing *metadata* is real evidence, but full filing *text* is not fetched in
    this phase — every metadata result also carries a ``primary_filing_unavailable``
    gap so the critic knows the body was not read.
  * A fetcher failure degrades to a safe gap (via ``call_safe``); it never
    crashes a report or a discovery run.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.services.exchange_registry import is_sec_eligible
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
# one): form_type, title, url, filed_date, summary, fields, accession_number.
FilingsFetcher = Callable[
    [CompanyContext, QueryContext], Awaitable[list[dict[str, Any]]]
]

# Recognised US primary-disclosure forms (used only to label the coverage gap).
_TARGET_FORMS = "10-K, 10-Q, 8-K, DEF 14A, Form 4, 13D/G, 20-F, 6-K"


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

    def _full_text_gap(self) -> SourceGap:
        return SourceGap(
            connector_key=self.connector_key,
            source_id="sec_edgar",
            gap_type=GapType.primary_filing_unavailable,
            severity=GapSeverity.info,
            message=(
                "SEC filing metadata is sourced (transport T2 / content T1), but "
                "full filing text is not retrieved in this phase; the full-text "
                "fetcher is pending."
            ),
            suggested_followup_phase="Phase 29B.x",
            blocks_research_complete=False,
        )

    async def fetch_filings(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        # Exchange-aware eligibility (Phase 27.1A): a US ticker-only run
        # (exchange is None) stays eligible; a known non-US / non-eligible venue
        # yields an honest gap rather than a wrong-CIK lookup.
        if not is_sec_eligible(company.exchange):
            gap = SourceGap(
                connector_key=self.connector_key,
                source_id="sec_edgar",
                gap_type=GapType.source_not_eligible,
                severity=GapSeverity.info,
                message=(
                    "SEC EDGAR covers US issuers only; "
                    f"{company.ticker or 'this issuer'} on exchange "
                    f"'{company.exchange}' is not SEC-eligible. Its primary "
                    "filings are sourced through the issuer's home regulator "
                    "(scaffolded, not yet live)."
                ),
                blocks_research_complete=False,
            )
            return ConnectorResult(
                connector_key=self.connector_key,
                warnings=[
                    f"SEC not applicable for non-US exchange '{company.exchange}'."
                ],
                source_gaps=[gap],
            )

        if self._fetcher is None:
            # No fetcher bound → no metadata available in this context.
            gap = SourceGap(
                connector_key=self.connector_key,
                source_id="sec_edgar",
                gap_type=GapType.primary_filing_unavailable,
                severity=GapSeverity.info,
                message=(
                    "No SEC filing metadata was available in this context "
                    f"(target forms: {_TARGET_FORMS})."
                ),
                blocks_research_complete=False,
            )
            return ConnectorResult(
                connector_key=self.connector_key,
                warnings=["SEC EDGAR fetcher not bound; no filing metadata."],
                source_gaps=[gap],
            )

        start = time.monotonic()
        raw = await self._fetcher(company, query)
        transport_tier, content_tier = sec_tier_pair()
        items = []
        issuer = company.company_name or company.ticker or "Issuer"
        cap = max(1, min(query.max_items, len(raw)))
        for i, f in enumerate(raw[:cap], start=1):
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
                    provenance=["SEC EDGAR filing index (metadata only)"],
                )
            )
        latency_ms = int((time.monotonic() - start) * 1000)
        gaps: list[SourceGap] = []
        if items:
            # Metadata is real, but the filing body was not read — say so.
            gaps.append(self._full_text_gap())
        else:
            gaps.append(
                SourceGap(
                    connector_key=self.connector_key,
                    source_id="sec_edgar",
                    gap_type=GapType.primary_filing_unavailable,
                    severity=GapSeverity.info,
                    message=(
                        "No recent SEC filings were found for this issuer in the "
                        "lookback window."
                    ),
                    blocks_research_complete=False,
                )
            )
        return ConnectorResult(
            connector_key=self.connector_key,
            evidence_items=items,
            latency_ms=latency_ms,
            source_gaps=gaps,
        )


__all__ = ["SecEdgarConnector", "FilingsFetcher"]
