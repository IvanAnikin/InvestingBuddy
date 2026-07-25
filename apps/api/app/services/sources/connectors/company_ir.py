"""
Company IR / newsroom connector — Phase 29B / 29B.1.

Turns an issuer's own primary material into typed, tiered ``EvidenceItem``s:

  * **Verified-issuer registry metadata** (Phase 29B.1) — for a known issuer
    (``verified_issuer_sources``), the connector always emits bounded,
    *metadata-only* evidence for the issuer's own investor-relations landing
    page, annual-reports index and press/newsroom index. This works with **no
    network call**, so it enriches non-US reports (Richemont, LVMH, Kering, …)
    at report time where SEC EDGAR is not eligible and only price/model data
    would otherwise exist. Metadata items are honestly labelled
    ``data_quality="metadata_only"`` — the page content / PDF is not read.

  * **Replayed press releases** (Phase 29B) — press items the workflow already
    discovered (``catalyst_discovery.press_release_events``) are re-expressed as
    ``company_ir_press_release`` evidence with no new network call.

  * **Live-extracted links** (Phase 29B.1, preview path only) — when a bounded
    ``page_fetcher`` is injected (evidence-preview endpoint, gated by
    ``source_connector_enabled``), the annual-reports and press pages are fetched
    through the SSRF-safe fetcher and their annual-report / press links become
    ``company_ir_annual_report`` (T1 primary filing) / ``company_ir_press_release``
    evidence.

Tiering:
  company_ir_profile / *_annual_reports_index / *_press_release_index / press
  release  → ``T1_primary_company_source`` (the issuer's own material).
  company_ir_annual_report (an official annual report / URD / integrated report)
  → ``T1_primary_filing`` (an issuer's primary disclosure document).

Guarantees:
  * Bounded (``query.max_items`` / config caps) — no scraping explosion.
  * URL query secrets are stripped by ``EvidenceItem`` before storage.
  * Media-only URLs (images) are never used as a citation URL.
  * When nothing can be sourced, an honest ``SourceGap`` is returned — never a
    fabricated release or filing.
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
from app.services.sources.evidence import EvidenceItem, build_evidence_item
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.safe_web_fetcher import (
    ANNUAL_REPORT_KEYWORDS,
    FALLBACK_REPORT_KEYWORDS,
    SafeFetchResult,
)
from app.services.sources.taxonomy import (
    T1_PRIMARY_COMPANY_SOURCE,
    T1_PRIMARY_FILING,
    ConnectorStatus,
)
from app.services.sources.verified_issuer_sources import VerifiedIssuerSource

# A press fetcher returns plain press-release dicts. Expected keys (all
# optional): headline/title, url, published_at/date, summary, source_name,
# source_url_quality, media_url.
PressFetcher = Callable[
    [CompanyContext, QueryContext], Awaitable[list[dict[str, Any]]]
]

# A page fetcher fetches ONE allowlisted URL and returns a SafeFetchResult. It is
# injected only on the live preview path; the report path never binds one.
PageFetcher = Callable[..., Awaitable[SafeFetchResult]]

_IR_TRANSPORT_LABEL = "Company IR / newsroom (issuer-published)"

# Countries whose primary regulatory disclosures are typically local-language.
_LOCAL_LANGUAGE_COUNTRIES = frozenset(
    {"France", "Italy", "Germany", "Switzerland", "Denmark", "Spain", "Netherlands"}
)


class CompanyIrConnector(SourceConnector):
    connector_key = "company_ir"
    supported_source_ids = ("company_ir",)
    status = ConnectorStatus.enabled

    def __init__(
        self,
        press_fetcher: PressFetcher | None = None,
        *,
        verified_source: VerifiedIssuerSource | None = None,
        page_fetcher: PageFetcher | None = None,
    ) -> None:
        self._fetcher = press_fetcher
        self._verified = verified_source
        self._page_fetcher = page_fetcher

    # -- Helpers -----------------------------------------------------------

    @property
    def _issuer_name(self) -> str | None:
        return self._verified.company_name if self._verified else None

    def _requires_translation(self) -> bool:
        return bool(self._verified and self._verified.country in _LOCAL_LANGUAGE_COUNTRIES)

    def _metadata_item(
        self,
        *,
        id: str,
        source_type: str,
        title: str,
        url: str | None,
        excerpt: str,
        content_tier: str = T1_PRIMARY_COMPANY_SOURCE,
        date: str | None = None,
        requires_translation: bool = False,
    ) -> EvidenceItem:
        warnings = ["Metadata only — page content / document text is not extracted."]
        if self._verified:
            warnings.extend(self._verified.warnings)
        return build_evidence_item(
            id=id,
            source_id="company_ir",
            source_name=self._issuer_name or "Company IR / Newsroom",
            provider_transport=_IR_TRANSPORT_LABEL,
            provider_transport_tier=T1_PRIMARY_COMPANY_SOURCE,
            content_source=title,
            content_source_tier=content_tier,
            source_type=source_type,
            title=title,
            url=url,
            date=date,
            excerpt=excerpt,
            requires_translation=requires_translation,
            data_quality="metadata_only",
            confidence=self._verified.source_confidence if self._verified else None,
            provenance=[
                "Verified issuer source registry (company-owned; metadata only)"
            ],
            warnings=warnings,
        )

    # -- search_company → company profile / IR landing metadata ------------

    async def search_company(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        if not self._verified or not self._verified.investor_relations_url:
            return ConnectorResult(
                connector_key=self.connector_key,
                source_gaps=[
                    SourceGap(
                        connector_key=self.connector_key,
                        source_id="company_ir",
                        gap_type=GapType.data_not_sourced,
                        severity=GapSeverity.info,
                        message=(
                            "No verified company IR page is registered for this "
                            "issuer; company profile evidence is not sourced."
                        ),
                        blocks_research_complete=False,
                    )
                ],
            )
        item = self._metadata_item(
            id="IRPROFILE",
            source_type="company_ir_profile",
            title=f"{self._issuer_name} — Investor Relations",
            url=self._verified.investor_relations_url,
            excerpt=(
                "Issuer investor-relations landing page (company-owned primary "
                f"source). {self._verified.last_verified_note}"
            ),
        )
        return ConnectorResult(connector_key=self.connector_key, evidence_items=[item])

    # -- fetch_filings → annual-report discovery ---------------------------

    async def fetch_filings(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        if not self._verified or not self._verified.annual_reports_url:
            return ConnectorResult(
                connector_key=self.connector_key,
                source_gaps=[
                    SourceGap(
                        connector_key=self.connector_key,
                        source_id="company_ir",
                        gap_type=GapType.primary_filing_unavailable,
                        severity=GapSeverity.info,
                        message=(
                            "No verified company annual-reports page is registered "
                            "for this issuer; annual-report evidence is not sourced."
                        ),
                        blocks_research_complete=False,
                    )
                ],
            )

        v = self._verified
        items: list[EvidenceItem] = [
            self._metadata_item(
                id="IRANNUALIDX",
                source_type="company_ir_annual_reports_index",
                title=f"{self._issuer_name} — Annual reports & results",
                url=v.annual_reports_url,
                excerpt="Issuer annual-reports / results index (company-owned).",
                requires_translation=False,
            )
        ]
        gaps: list[SourceGap] = []

        # Live extraction (preview path only) — turn the index page into bounded
        # annual-report links. Offline, we surface an honest metadata-only gap.
        if self._page_fetcher is None:
            gaps.append(
                SourceGap(
                    connector_key=self.connector_key,
                    source_id="company_ir",
                    gap_type=GapType.primary_filing_unavailable,
                    severity=GapSeverity.info,
                    message=(
                        "Company IR source found but individual annual-report links "
                        "are not identified without live extraction (metadata only)."
                    ),
                    suggested_followup_phase="Phase 29B.x",
                    blocks_research_complete=False,
                )
            )
            return ConnectorResult(
                connector_key=self.connector_key, evidence_items=items, source_gaps=gaps
            )

        start = time.monotonic()
        fetched = await self._page_fetcher(
            v.annual_reports_url,
            allowed_domains=v.allowed_domains,
            keywords=ANNUAL_REPORT_KEYWORDS,
            fallback_keywords=FALLBACK_REPORT_KEYWORDS,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        if fetched.blocked or (fetched.error and not fetched.ok):
            gaps.append(
                SourceGap(
                    connector_key=self.connector_key,
                    source_id="company_ir",
                    gap_type=GapType.primary_filing_unavailable,
                    severity=GapSeverity.info,
                    message=(
                        "Company IR annual-reports page could not be safely fetched "
                        f"({fetched.error or 'blocked'}); annual-report links are not "
                        "identified. Company IR index remains as metadata evidence."
                    ),
                    blocks_research_complete=False,
                )
            )
            return ConnectorResult(
                connector_key=self.connector_key,
                evidence_items=items,
                source_gaps=gaps,
                latency_ms=latency_ms,
            )

        cap = max(1, query.max_items)
        for i, link in enumerate(fetched.links[:cap], start=1):
            items.append(
                build_evidence_item(
                    id=f"IRAR{i}",
                    source_id="company_ir",
                    source_name=self._issuer_name or "Company IR",
                    provider_transport=_IR_TRANSPORT_LABEL,
                    provider_transport_tier=T1_PRIMARY_COMPANY_SOURCE,
                    content_source=link.text or "Annual report",
                    content_source_tier=T1_PRIMARY_FILING,
                    source_type="company_ir_annual_report",
                    title=link.text or "Annual report",
                    url=link.url,
                    requires_translation=self._requires_translation(),
                    data_quality="link_metadata_only",
                    confidence=v.source_confidence,
                    provenance=[
                        "Extracted from issuer annual-reports index (link metadata)"
                    ],
                    warnings=(
                        ["Document text not extracted; link title/URL only."]
                        + (
                            ["Local-language primary disclosure; translation pending "
                             "Phase 30."]
                            if self._requires_translation()
                            else []
                        )
                    ),
                )
            )
        if not fetched.links:
            gaps.append(
                SourceGap(
                    connector_key=self.connector_key,
                    source_id="company_ir",
                    gap_type=GapType.primary_filing_unavailable,
                    severity=GapSeverity.info,
                    message=(
                        "Company IR source found but annual report link not "
                        "identified by bounded extractor."
                    ),
                    blocks_research_complete=False,
                )
            )
        return ConnectorResult(
            connector_key=self.connector_key,
            evidence_items=items,
            source_gaps=gaps,
            latency_ms=latency_ms,
        )

    # -- fetch_events → press / newsroom -----------------------------------

    async def fetch_events(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        items: list[EvidenceItem] = []
        gaps: list[SourceGap] = []
        warnings: list[str] = []

        # Verified press/newsroom index (metadata only).
        if self._verified and self._verified.press_releases_url:
            items.append(
                self._metadata_item(
                    id="IRPRESSIDX",
                    source_type="company_ir_press_release_index",
                    title=f"{self._issuer_name} — Press releases / Newsroom",
                    url=self._verified.press_releases_url,
                    excerpt="Issuer press / newsroom index (company-owned).",
                )
            )

        # Replayed press items (deterministic report path) or live feed (preview).
        start = time.monotonic()
        raw = await self._fetcher(company, query) if self._fetcher else []
        cap = max(1, query.max_items)
        for i, e in enumerate(raw[:cap], start=1):
            title = e.get("headline") or e.get("title") or "Press release"
            url = e.get("url")  # never a media/image URL — that stays media_url
            quality = e.get("source_url_quality")
            provenance = ["Issuer-published press release / IR page"]
            if quality:
                provenance.append(f"source_url_quality={quality}")
            items.append(
                build_evidence_item(
                    id=f"IR{i}",
                    source_id="company_ir",
                    source_name=e.get("source_name")
                    or self._issuer_name
                    or "Company IR / Newsroom",
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

        # No press evidence at all (no verified index + no feed) → honest gap.
        if not items:
            gaps.append(
                SourceGap(
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
            )
            warnings.append("Company IR fetcher not bound; no press evidence.")
        elif self._verified and not raw:
            # Have the index but no dated releases — say so honestly.
            gaps.append(
                SourceGap(
                    connector_key=self.connector_key,
                    source_id="company_ir",
                    gap_type=GapType.data_not_sourced,
                    severity=GapSeverity.info,
                    message=(
                        "Company press/newsroom index sourced, but individual dated "
                        "releases are not extracted in this context (metadata only)."
                    ),
                    blocks_research_complete=False,
                )
            )

        return ConnectorResult(
            connector_key=self.connector_key,
            evidence_items=items,
            latency_ms=latency_ms,
            source_gaps=gaps,
            warnings=warnings,
        )


__all__ = ["CompanyIrConnector", "PressFetcher", "PageFetcher"]
