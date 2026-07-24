"""
Company IR / newsroom connector — Phase 29B.

Wraps the issuer's own primary material — press releases and investor-relations
/ newsroom items discovered by the existing catalyst flow (Phase 24/24.1) — into
typed, tiered ``EvidenceItem``s.

Tiering: an issuer's own press release / IR page is a primary *company* source,
so its content tier is ``T1_primary_company_source``. There is no separate
regulator transport (the issuer publishes it directly), so the transport tier is
recorded as the same primary-company tier.

Like the SEC connector, this takes an injected fetcher returning plain dicts
(headline/title, url, published_at, summary, source_name, source_url_quality) so
it is decoupled from the concrete provider:

  * In the single-company evidence flow the fetcher replays press items the
    workflow already discovered (``catalyst_discovery.press_release_events``) —
    no new network call.
  * In the evidence-preview endpoint a bounded live fetcher backed by
    ``CompanyPressReleaseProvider`` may be injected (gated by config).

Guarantees:
  * Bounded (``query.max_items``) — no scraping explosion.
  * URL query secrets are stripped by ``EvidenceItem`` before storage.
  * Media-only URLs (images) are never used as the citation URL.
  * When no IR page / feed is available, an honest ``data_not_sourced`` gap is
    returned — never a fabricated release.
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
from app.services.sources.taxonomy import (
    T1_PRIMARY_COMPANY_SOURCE,
    ConnectorStatus,
)

# A fetcher returns plain press-release dicts. Expected keys (all optional):
# headline/title, url, published_at/date, summary, source_name,
# source_url_quality, media_url.
PressFetcher = Callable[
    [CompanyContext, QueryContext], Awaitable[list[dict[str, Any]]]
]

_IR_TRANSPORT_LABEL = "Company IR / newsroom (issuer-published)"


class CompanyIrConnector(SourceConnector):
    connector_key = "company_ir"
    supported_source_ids = ("company_ir",)
    status = ConnectorStatus.enabled

    def __init__(self, press_fetcher: PressFetcher | None = None) -> None:
        self._fetcher = press_fetcher

    async def fetch_events(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        if self._fetcher is None:
            gap = SourceGap(
                connector_key=self.connector_key,
                source_id="company_ir",
                gap_type=GapType.data_not_sourced,
                severity=GapSeverity.info,
                message=(
                    "No company IR / newsroom feed was available for this issuer; "
                    "press-release evidence is not sourced."
                ),
                blocks_research_complete=False,
            )
            return ConnectorResult(
                connector_key=self.connector_key,
                warnings=["Company IR fetcher not bound; no press evidence."],
                source_gaps=[gap],
            )

        start = time.monotonic()
        raw = await self._fetcher(company, query)
        items = []
        cap = max(1, min(query.max_items, len(raw)))
        for i, e in enumerate(raw[:cap], start=1):
            title = e.get("headline") or e.get("title") or "Press release"
            # Never cite a media/image URL as the source; keep it as media_url.
            url = e.get("url")
            quality = e.get("source_url_quality")
            provenance = ["Issuer-published press release / IR page"]
            if quality:
                provenance.append(f"source_url_quality={quality}")
            items.append(
                build_evidence_item(
                    id=f"IR{i}",
                    source_id="company_ir",
                    source_name=e.get("source_name") or "Company IR / Newsroom",
                    provider_transport=_IR_TRANSPORT_LABEL,
                    provider_transport_tier=T1_PRIMARY_COMPANY_SOURCE,
                    content_source=str(title),
                    content_source_tier=T1_PRIMARY_COMPANY_SOURCE,
                    source_type="company_ir_press_release",
                    title=str(title),
                    url=url,
                    date=str(e.get("published_at") or e.get("date"))
                    if (e.get("published_at") or e.get("date"))
                    else None,
                    excerpt=e.get("summary") or e.get("headline") or e.get("title"),
                    data_quality=e.get("data_quality"),
                    provenance=provenance,
                )
            )
        latency_ms = int((time.monotonic() - start) * 1000)
        gaps: list[SourceGap] = []
        if not items:
            gaps.append(
                SourceGap(
                    connector_key=self.connector_key,
                    source_id="company_ir",
                    gap_type=GapType.data_not_sourced,
                    severity=GapSeverity.info,
                    message="No recent issuer press releases were found in the "
                    "lookback window.",
                    blocks_research_complete=False,
                )
            )
        return ConnectorResult(
            connector_key=self.connector_key,
            evidence_items=items,
            latency_ms=latency_ms,
            source_gaps=gaps,
        )

    # IR discovery is event-shaped; ``search_company`` reuses the same path.
    async def search_company(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        return await self.fetch_events(company, query)


__all__ = ["CompanyIrConnector", "PressFetcher"]
