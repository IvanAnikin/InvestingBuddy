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

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.structured_logging import log_event
from app.services.sources.connector_base import (
    CompanyContext,
    ConnectorResult,
    QueryContext,
    SourceConnector,
)
from app.services.sources.document_text_extractor import (
    EVIDENCE_TYPE_BUSINESS,
    EVIDENCE_TYPE_RISK,
    DocumentTextExtraction,
)
from app.services.sources.evidence import (
    EvidenceItem,
    PrimaryFactRef,
    build_evidence_item,
)
from app.services.sources.extracted_fact_validator import (
    VALIDATION_EXCERPT_ONLY,
    VALIDATION_VALIDATED,
    IssuerContext,
    ValidatedFact,
)
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.primary_document_extractor import (
    STATUS_EXTRACTED,
    PrimaryDocumentExtraction,
    _confidence_bucket,
)
from app.services.sources.primary_fact_parser import PrimaryFact
from app.services.sources.safe_web_fetcher import (
    ANNUAL_REPORT_KEYWORDS,
    FALLBACK_REPORT_KEYWORDS,
    SafeFetchResult,
    SafeLink,
)
from app.services.sources.taxonomy import (
    T1_PRIMARY_COMPANY_SOURCE,
    T1_PRIMARY_FILING,
    ConnectorStatus,
)
from app.services.sources.verified_issuer_sources import VerifiedIssuerSource

_log = logging.getLogger("app.services.sources.connectors.company_ir")

# A press fetcher returns plain press-release dicts. Expected keys (all
# optional): headline/title, url, published_at/date, summary, source_name,
# source_url_quality, media_url.
PressFetcher = Callable[
    [CompanyContext, QueryContext], Awaitable[list[dict[str, Any]]]
]

# A page fetcher fetches ONE allowlisted URL and returns a SafeFetchResult. It is
# injected only on the live preview path; the report path never binds one.
PageFetcher = Callable[..., Awaitable[SafeFetchResult]]


@dataclass
class PrimaryDocumentBundle:
    """The bounded result of fetching + extracting + parsing ONE primary document.

    Produced by an injected ``DocumentExtractor`` (Phase 29B.2). Carries only
    bounded excerpts + high-confidence facts + honest gaps — never a raw document.
    """

    source_url: str
    document_type: str | None = None
    extraction: DocumentTextExtraction | None = None
    facts: list[PrimaryFact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_gaps: list[SourceGap] = field(default_factory=list)


# A document extractor fetches ONE allowlisted annual-report document and returns
# a bounded ``PrimaryDocumentBundle``. Injected only when document extraction is
# enabled (evidence-preview live path, or the council path when both connector +
# document-extraction flags are on). Never raises.
DocumentExtractor = Callable[..., Awaitable[PrimaryDocumentBundle]]


class PrimaryDocumentArtifact(BaseModel):
    """Deep primary-document ingestion result for ONE document — Phase 32A Slice 5.

    Bundles the bounded ``PrimaryDocumentExtraction`` (pdfplumber/HTML excerpts +
    tables) with its stricter-validated structured facts and secret-stripped
    provenance so a LATER persistence task can store ``ExtractedDocument`` /
    ``ExtractedFact`` rows without re-fetching or re-extracting. It carries ONLY
    bounded excerpts / tables / facts + a token-stripped URL — never raw bytes or
    the whole document, and never a fabricated figure.
    """

    source_url: str
    document_type: str | None = None
    content_tier: str = T1_PRIMARY_FILING
    title: str | None = None
    retrieved_at: datetime | None = None
    status: str = "extraction_failed"
    extraction: PrimaryDocumentExtraction | None = None
    validated_facts: list[ValidatedFact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_gaps: list[SourceGap] = Field(default_factory=list)
    # Secret-free per-document timings for telemetry (never bytes/text).
    fetch_ms: int | None = None
    extraction_ms: int | None = None


# A DEEP document extractor fetches ONE allowlisted annual-report document, runs
# the structure-aware ``primary_document_extractor`` (pdfplumber tables) + the
# stricter ``extracted_fact_validator``, and returns a ``PrimaryDocumentArtifact``.
# Injected only when ``primary_document_ingestion_enabled`` (the master flag) is
# on. Never raises: an honest failure degrades to a metadata_only/failed artifact.
PrimaryDocumentDeepExtractor = Callable[..., Awaitable[PrimaryDocumentArtifact]]

# Report-link text markers used to rank the most material documents first (annual
# report / results / registration document) when the per-issuer cap is > 1.
_MATERIAL_DOCUMENT_MARKERS = (
    "annual",
    "registration document",
    "results",
    "integrated report",
    "financial report",
    "full-year",
    "full year",
)

_IR_TRANSPORT_LABEL = "Company IR / newsroom (issuer-published)"

# Map an extraction excerpt's evidence_type to an EvidenceItem source_type.
_EXCERPT_SOURCE_TYPE = {
    EVIDENCE_TYPE_BUSINESS: "company_ir_business_description",
    EVIDENCE_TYPE_RISK: "company_ir_risk_excerpt",
}
_DEFAULT_EXCERPT_SOURCE_TYPE = "company_ir_annual_report_excerpt"

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
        document_extractor: DocumentExtractor | None = None,
        primary_document_extractor: PrimaryDocumentDeepExtractor | None = None,
        max_docs_per_issuer: int = 1,
        ingestion_budget_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetcher = press_fetcher
        self._verified = verified_source
        self._page_fetcher = page_fetcher
        self._document_extractor = document_extractor
        # Phase 32A Slice 5 (deep ingestion, gated by the master flag): when a deep
        # extractor is injected the connector fetches up to ``max_docs_per_issuer``
        # documents under an AGGREGATE wall-budget and collects rich artifacts.
        self._primary_document_extractor = primary_document_extractor
        self._max_docs_per_issuer = max(1, max_docs_per_issuer)
        self._ingestion_budget_seconds = ingestion_budget_seconds
        self._clock = clock
        # Deep artifacts (extractions + validated facts) collected this run, threaded
        # OUT for a later persistence task. Empty on the OFF / shallow path.
        self.collected_primary_document_artifacts: list[PrimaryDocumentArtifact] = []

    # -- Helpers -----------------------------------------------------------

    @property
    def _issuer_name(self) -> str | None:
        return self._verified.company_name if self._verified else None

    def _requires_translation(self) -> bool:
        return bool(self._verified and self._verified.country in _LOCAL_LANGUAGE_COUNTRIES)

    def _original_language(self) -> str | None:
        """Best-guess primary-disclosure language from the issuer's country."""
        if not self._verified:
            return None
        return {
            "France": "fr",
            "Italy": "it",
            "Germany": "de",
        }.get(self._verified.country)

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

        # Phase 32A Slice 5: DEEP document ingestion (master flag on). When a deep
        # extractor is injected, fetch up to ``max_docs_per_issuer`` of the most
        # material discovered documents under the AGGREGATE ingestion budget, run
        # pdfplumber/HTML extraction + stricter fact validation, and emit rich T1
        # excerpt / validated-fact evidence plus honest gaps. Takes precedence over
        # the Phase 29B.2 shallow path; when it is NOT injected the shallow path
        # below runs byte-for-byte unchanged.
        if self._primary_document_extractor is not None and fetched.links:
            doc_items, doc_gaps, artifacts = await self._extract_primary_documents_deep(
                fetched.links, query, company
            )
            items.extend(doc_items)
            gaps.extend(doc_gaps)
            self.collected_primary_document_artifacts.extend(artifacts)
        # Phase 29B.2: bounded (shallow) document extraction. When a document
        # extractor is injected (both connector + document-extraction flags on),
        # fetch ONE already-discovered annual-report document, extract bounded
        # excerpts and parse high-confidence primary facts into tiered T1 evidence.
        # A blocked / scanned / JS-gated document degrades to an honest gap.
        elif self._document_extractor is not None and fetched.links:
            doc_items, doc_gaps = await self._extract_primary_document(
                fetched.links, query
            )
            items.extend(doc_items)
            gaps.extend(doc_gaps)

        return ConnectorResult(
            connector_key=self.connector_key,
            evidence_items=items,
            source_gaps=gaps,
            latency_ms=latency_ms,
        )

    async def _extract_primary_document(
        self, links: list[SafeLink], query: QueryContext
    ) -> tuple[list[EvidenceItem], list[SourceGap]]:
        """Extract one annual-report document into bounded T1 evidence + facts."""
        assert self._document_extractor is not None
        v = self._verified
        allowed = v.allowed_domains if v else ()
        # Prefer a downloadable document link (PDF); else the first report link.
        target = next((ln for ln in links if ln.is_document), links[0])

        bundle = await self._document_extractor(
            target.url,
            allowed_domains=allowed,
            title_hint=target.text or None,
            original_language=self._original_language(),
        )
        items: list[EvidenceItem] = []
        gaps: list[SourceGap] = list(bundle.source_gaps)
        ext = bundle.extraction

        if ext is None or not ext.excerpts:
            for msg in (ext.source_gaps if ext else []) or [
                "Annual-report document text could not be extracted; company IR "
                "index and link remain as metadata evidence."
            ]:
                gaps.append(
                    SourceGap(
                        connector_key=self.connector_key,
                        source_id="company_ir",
                        gap_type=GapType.primary_filing_unavailable,
                        severity=GapSeverity.info,
                        message=msg,
                        blocks_research_complete=False,
                    )
                )
            return items, gaps

        requires_tr = ext.requires_translation or self._requires_translation()
        year = str(ext.inferred_year) if ext.inferred_year else None
        doc_title = ext.title or target.text or "Annual report"
        cap = max(1, query.max_items)

        # One bounded excerpt per evidence item (each already length-bounded).
        for i, exc in enumerate(ext.excerpts[:cap], start=1):
            source_type = _EXCERPT_SOURCE_TYPE.get(
                exc.evidence_type, _DEFAULT_EXCERPT_SOURCE_TYPE
            )
            data_quality = {"high": "B", "medium": "C", "low": "C"}.get(
                exc.confidence, "C"
            )
            items.append(
                build_evidence_item(
                    id=f"IRDOC{i}",
                    source_id="company_ir",
                    source_name=self._issuer_name or "Company IR",
                    provider_transport=_IR_TRANSPORT_LABEL,
                    provider_transport_tier=T1_PRIMARY_COMPANY_SOURCE,
                    content_source=doc_title,
                    content_source_tier=T1_PRIMARY_FILING,
                    source_type=source_type,
                    title=(
                        f"{doc_title} — excerpt"
                        if year is None
                        else f"{doc_title} ({year}) — excerpt"
                    ),
                    url=ext.source_url or target.url,
                    date=year,
                    excerpt=exc.text,
                    requires_translation=requires_tr,
                    original_language=ext.original_language,
                    language=ext.language,
                    data_quality=data_quality,
                    confidence=exc.confidence,
                    fields_supported=[exc.evidence_type],
                    provenance=[
                        "Extracted from issuer annual-report document (bounded text)",
                        f"page={exc.page_number}" if exc.page_number else "page=unknown",
                    ],
                    warnings=(
                        ["Bounded excerpt from the issuer's own annual report; "
                         "not the full document. Human review required."]
                        + (
                            ["Local-language primary disclosure; machine translation "
                             "pending Phase 30 — excerpt is unmodified source text."]
                            if requires_tr
                            else []
                        )
                    ),
                )
            )

        # High-confidence parsed facts become their own T1 fact evidence. Each
        # item also carries the STRUCTURED fact payload (Phase 29B.3) so the final
        # report can surface a real T1 datapoint without re-parsing the excerpt.
        for j, fact in enumerate(bundle.facts[:cap], start=1):
            unit_bits = " ".join(
                b for b in (fact.scale, fact.currency, fact.unit) if b
            )
            excerpt = (
                f"{fact.field} = {fact.value}"
                + (f" ({unit_bits})" if unit_bits else "")
                + (f" [{fact.period}]" if fact.period else "")
            )
            # The effective, token-stripped source URL used for both the item and
            # the structured fact's own provenance.
            fact_url = fact.source_url or ext.source_url or target.url
            items.append(
                build_evidence_item(
                    id=f"IRFACT{j}",
                    source_id="company_ir",
                    source_name=self._issuer_name or "Company IR",
                    provider_transport=_IR_TRANSPORT_LABEL,
                    provider_transport_tier=T1_PRIMARY_COMPANY_SOURCE,
                    content_source=doc_title,
                    content_source_tier=T1_PRIMARY_FILING,
                    source_type="company_ir_financial_fact",
                    title=f"{doc_title}: {fact.field}",
                    url=fact_url,
                    date=fact.period or year,
                    excerpt=excerpt,
                    requires_translation=requires_tr,
                    data_quality="B" if fact.confidence == "high" else "C",
                    confidence=fact.confidence,
                    fields_supported=[fact.field],
                    provenance=[
                        "Parsed from issuer annual-report excerpt "
                        f"({fact.excerpt_id or 'excerpt'})",
                        "needs_human_review=true",
                    ],
                    warnings=(
                        [w for w in [fact.parser_warning] if w]
                        + ["Parsed primary fact — unverified; human review required."]
                    ),
                    primary_fact=PrimaryFactRef(
                        field=fact.field,
                        value=fact.value,
                        numeric_value=fact.numeric_value,
                        unit=fact.unit,
                        currency=fact.currency,
                        scale=fact.scale,
                        period=fact.period,
                        source_url=fact_url,
                        excerpt_id=fact.excerpt_id,
                        page_number=fact.page_number,
                        confidence=fact.confidence,
                        needs_human_review=fact.needs_human_review,
                    ),
                )
            )
        return items, gaps

    # -- Deep primary-document ingestion (Phase 32A Slice 5, master flag) ---

    def _issuer_context(self, company: CompanyContext) -> IssuerContext:
        """Known issuer identity a table-derived fact must be tied to validate."""
        return IssuerContext(
            company_name=self._issuer_name or company.company_name,
            legal_name=self._verified.company_name if self._verified else None,
            ticker=company.ticker,
        )

    def _rank_deep_targets(self, links: list[SafeLink]) -> list[SafeLink]:
        """Order report links most-material-first, de-dup by URL, cap per issuer.

        Prefers annual-report / results / registration documents and downloadable
        (PDF) links; stable within equal rank. Bounded by ``max_docs_per_issuer``.
        """

        def rank(link: SafeLink) -> tuple[int, int]:
            text = (link.text or "").lower()
            material = 0 if any(m in text for m in _MATERIAL_DOCUMENT_MARKERS) else 1
            doc = 0 if link.is_document else 1
            return (material, doc)

        seen: set[str] = set()
        ordered: list[SafeLink] = []
        for link in sorted(links, key=rank):
            if link.url in seen:
                continue
            seen.add(link.url)
            ordered.append(link)
        return ordered[: self._max_docs_per_issuer]

    async def _extract_primary_documents_deep(
        self, links: list[SafeLink], query: QueryContext, company: CompanyContext
    ) -> tuple[list[EvidenceItem], list[SourceGap], list[PrimaryDocumentArtifact]]:
        """Ingest up to N material documents under the AGGREGATE ingestion budget.

        Emits rich T1 excerpt + validated-fact evidence and honest gaps, and returns
        the collected artifacts (extractions + validated facts) for a later
        persistence task. The aggregate budget stops STARTING new fetches once
        exhausted, recording an honest ``ingestion_budget_exhausted`` gap for the
        remaining documents (never a fabricated excerpt/fact). Runs BEFORE the
        council deadline so ingestion + council stays under the gateway timeout.
        """
        assert self._primary_document_extractor is not None
        allowed = self._verified.allowed_domains if self._verified else ()
        issuer_context = self._issuer_context(company)
        targets = self._rank_deep_targets(links)

        items: list[EvidenceItem] = []
        gaps: list[SourceGap] = []
        artifacts: list[PrimaryDocumentArtifact] = []
        budget = self._ingestion_budget_seconds
        start = self._clock()
        total_started = time.perf_counter()
        ingested = 0

        for doc_idx, target in enumerate(targets, start=1):
            if budget is not None and (self._clock() - start) >= budget:
                remaining = len(targets) - (doc_idx - 1)
                gaps.append(
                    SourceGap(
                        connector_key=self.connector_key,
                        source_id="company_ir",
                        gap_type=GapType.primary_filing_unavailable,
                        severity=GapSeverity.info,
                        message=(
                            "Primary-document ingestion budget exhausted "
                            f"(ingestion_budget_exhausted); {remaining} further issuer "
                            "document(s) were not fetched."
                        ),
                        blocks_research_complete=False,
                    )
                )
                log_event(
                    _log,
                    "primary_document_ingestion_budget_exhausted",
                    level=logging.WARNING,
                    connector_key=self.connector_key,
                    documents_ingested=ingested,
                    documents_skipped=remaining,
                    budget_seconds=budget,
                )
                break

            artifact = await self._primary_document_extractor(
                target.url,
                allowed_domains=allowed,
                title_hint=target.text or None,
                original_language=self._original_language(),
                issuer_context=issuer_context,
            )
            artifacts.append(artifact)
            ingested += 1
            doc_items, doc_gaps = self._artifact_to_evidence(
                artifact, target, doc_idx, query
            )
            items.extend(doc_items)
            gaps.extend(doc_gaps)
            validated = sum(
                1
                for f in artifact.validated_facts
                if f.validation_status == VALIDATION_VALIDATED
            )
            log_event(
                _log,
                "primary_document_ingested",
                connector_key=self.connector_key,
                document_index=doc_idx,
                status=artifact.status,
                document_type=artifact.document_type,
                fetch_ms=artifact.fetch_ms,
                extraction_ms=artifact.extraction_ms,
                excerpt_count=(
                    len(artifact.extraction.excerpts) if artifact.extraction else 0
                ),
                table_count=(
                    len(artifact.extraction.tables) if artifact.extraction else 0
                ),
                validated_fact_count=validated,
            )

        log_event(
            _log,
            "primary_document_ingestion_completed",
            connector_key=self.connector_key,
            document_count=len(artifacts),
            total_ingestion_ms=int((time.perf_counter() - total_started) * 1000),
        )
        return items, gaps, artifacts

    def _artifact_to_evidence(
        self,
        artifact: PrimaryDocumentArtifact,
        target: SafeLink,
        doc_idx: int,
        query: QueryContext,
    ) -> tuple[list[EvidenceItem], list[SourceGap]]:
        """Turn ONE deep artifact into bounded T1 excerpt + validated-fact evidence.

        * prose excerpts → ``company_ir_*_excerpt`` T1 items (page/section/method/
          confidence in provenance);
        * ``validated`` facts → ``company_ir_financial_fact`` T1 items carrying the
          structured ``PrimaryFactRef`` (with table location + page);
        * ``excerpt_only`` facts → an excerpt EvidenceItem (table-located) — NEVER a
          fact; a scanned/failed extraction yields honest gaps only.
        """
        items: list[EvidenceItem] = []
        gaps: list[SourceGap] = list(artifact.source_gaps)
        ext = artifact.extraction

        if ext is None or artifact.status != STATUS_EXTRACTED or not ext.has_content:
            for msg in (ext.source_gaps if ext else []) or [
                "Annual-report document text could not be extracted; company IR "
                "index and link remain as metadata evidence."
            ]:
                gaps.append(
                    SourceGap(
                        connector_key=self.connector_key,
                        source_id="company_ir",
                        gap_type=GapType.primary_filing_unavailable,
                        severity=GapSeverity.info,
                        message=msg,
                        blocks_research_complete=False,
                    )
                )
            return items, gaps

        requires_tr = ext.requires_translation or self._requires_translation()
        doc_title = artifact.title or target.text or "Annual report"
        url = artifact.source_url or target.url
        cap = max(1, query.max_items)
        orig_lang = self._original_language()
        tr_warn = (
            ["Local-language primary disclosure; machine translation pending "
             "Phase 30 — excerpt is unmodified source text."]
            if requires_tr
            else []
        )

        # 1) prose excerpts (bounded) → T1 primary-document excerpt items.
        for n, exc in enumerate(ext.excerpts[:cap], start=1):
            source_type = _EXCERPT_SOURCE_TYPE.get(
                exc.evidence_type, _DEFAULT_EXCERPT_SOURCE_TYPE
            )
            provenance = [
                p
                for p in (
                    "Extracted from issuer annual-report document (deep, bounded text)",
                    f"page={exc.page_number}" if exc.page_number else "page=unknown",
                    f"section={exc.section}" if exc.section else None,
                    f"method={exc.extraction_method}",
                    f"confidence={exc.confidence:.2f}",
                )
                if p
            ]
            items.append(
                build_evidence_item(
                    id=f"IRDOC{doc_idx}X{n}",
                    source_id="company_ir",
                    source_name=self._issuer_name or "Company IR",
                    provider_transport=_IR_TRANSPORT_LABEL,
                    provider_transport_tier=T1_PRIMARY_COMPANY_SOURCE,
                    content_source=doc_title,
                    content_source_tier=T1_PRIMARY_FILING,
                    source_type=source_type,
                    title=f"{doc_title} — excerpt",
                    url=url,
                    excerpt=exc.text,
                    requires_translation=requires_tr,
                    original_language=orig_lang,
                    language=ext.language,
                    data_quality="B" if exc.confidence >= 0.75 else "C",
                    confidence=_confidence_bucket(exc.confidence),
                    fields_supported=[exc.evidence_type],
                    provenance=provenance,
                    warnings=(
                        ["Bounded excerpt from the issuer's own annual report; "
                         "not the full document. Human review required."]
                        + tr_warn
                    ),
                )
            )

        # 2) validated facts → structured T1 primary-filing datapoints.
        for j, fact in enumerate(
            (f for f in artifact.validated_facts if f.validation_status == VALIDATION_VALIDATED),
            start=1,
        ):
            value_str = fact.value_text or (
                str(fact.value_numeric) if fact.value_numeric is not None else ""
            )
            unit_bits = " ".join(b for b in (fact.scale, fact.currency, fact.unit) if b)
            excerpt = (
                f"{fact.label} = {value_str}"
                + (f" ({unit_bits})" if unit_bits else "")
                + (f" [{fact.period}]" if fact.period else "")
            )
            conf_bucket = _confidence_bucket(fact.confidence)
            provenance = [
                p
                for p in (
                    "Validated from issuer annual-report table "
                    "(deep, stricter grid validation)",
                    f"page={fact.page_number}" if fact.page_number else "page=unknown",
                    f"table={fact.table_location}" if fact.table_location else None,
                    f"method={fact.extraction_method}",
                    f"confidence={fact.confidence:.2f}",
                    f"validation_status={fact.validation_status}",
                    "needs_human_review=true",
                )
                if p
            ]
            items.append(
                build_evidence_item(
                    id=f"IRFACT{doc_idx}_{j}",
                    source_id="company_ir",
                    source_name=self._issuer_name or "Company IR",
                    provider_transport=_IR_TRANSPORT_LABEL,
                    provider_transport_tier=T1_PRIMARY_COMPANY_SOURCE,
                    content_source=doc_title,
                    content_source_tier=T1_PRIMARY_FILING,
                    source_type="company_ir_financial_fact",
                    title=f"{doc_title}: {fact.label}",
                    url=url,
                    date=fact.period,
                    excerpt=excerpt,
                    requires_translation=requires_tr,
                    data_quality="B" if conf_bucket == "high" else "C",
                    confidence=conf_bucket,
                    fields_supported=[fact.label],
                    provenance=provenance,
                    warnings=(
                        [note for note in fact.validation_notes if note]
                        + ["Validated primary fact — unverified; human review required."]
                    ),
                    primary_fact=PrimaryFactRef(
                        field=fact.label,
                        value=value_str or fact.label,
                        numeric_value=fact.value_numeric,
                        unit=fact.unit,
                        currency=fact.currency,
                        scale=fact.scale,
                        period=fact.period,
                        source_url=url,
                        excerpt_id=fact.table_location,
                        page_number=fact.page_number,
                        confidence=conf_bucket,
                        needs_human_review=fact.needs_human_review,
                    ),
                )
            )

        # 3) excerpt_only facts → a table-located excerpt item — NEVER a fact.
        for k, fact in enumerate(
            (f for f in artifact.validated_facts if f.validation_status == VALIDATION_EXCERPT_ONLY),
            start=1,
        ):
            value_str = fact.value_text or (
                str(fact.value_numeric) if fact.value_numeric is not None else ""
            )
            unit_bits = " ".join(b for b in (fact.scale, fact.currency, fact.unit) if b)
            provenance = [
                p
                for p in (
                    "Extracted from issuer annual-report table "
                    "(deep, bounded; not promoted to a validated fact)",
                    f"page={fact.page_number}" if fact.page_number else "page=unknown",
                    f"table={fact.table_location}" if fact.table_location else None,
                    f"method={fact.extraction_method}",
                    "validation_status=excerpt_only",
                )
                if p
            ]
            items.append(
                build_evidence_item(
                    id=f"IRTBL{doc_idx}_{k}",
                    source_id="company_ir",
                    source_name=self._issuer_name or "Company IR",
                    provider_transport=_IR_TRANSPORT_LABEL,
                    provider_transport_tier=T1_PRIMARY_COMPANY_SOURCE,
                    content_source=doc_title,
                    content_source_tier=T1_PRIMARY_FILING,
                    source_type=_DEFAULT_EXCERPT_SOURCE_TYPE,
                    title=f"{doc_title} — table excerpt",
                    url=url,
                    excerpt=(
                        f"{fact.label}: {value_str}"
                        + (f" ({unit_bits})" if unit_bits else "")
                        + (f" [{fact.period}]" if fact.period else "")
                    ),
                    requires_translation=requires_tr,
                    original_language=orig_lang,
                    language=artifact.extraction.language if artifact.extraction else "en",
                    data_quality="C",
                    confidence=_confidence_bucket(fact.confidence),
                    provenance=provenance,
                    warnings=(
                        ["Table cell retained as a bounded excerpt (not a validated "
                         "fact); human review required."]
                        + tr_warn
                    ),
                )
            )

        return items, gaps

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


__all__ = [
    "CompanyIrConnector",
    "PressFetcher",
    "PageFetcher",
    "DocumentExtractor",
    "PrimaryDocumentBundle",
    "PrimaryDocumentArtifact",
    "PrimaryDocumentDeepExtractor",
]
