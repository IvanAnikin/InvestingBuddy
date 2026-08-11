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
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.core.structured_logging import log_event
from app.services.sources.connector_base import (
    CompanyContext,
    ConnectorResult,
    QueryContext,
    SourceConnector,
)
from app.services.sources.document_discovery import (
    DEFAULT_STRATEGIES,
    DOC_KIND_ANNUAL_REPORT,
    DOC_KIND_INTERIM_REPORT,
    DOC_KIND_OTHER,
    DOC_KIND_PRESENTATION,
    DOC_KIND_RESULTS_RELEASE,
    STRATEGY_ANCHORS,
    discover_documents,
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
from app.services.sources.language import detect_language
from app.services.sources.primary_document_extractor import (
    STATUS_EXTRACTED,
    PrimaryDocumentExtraction,
    _confidence_bucket,
    _infer_scope,
    classify_statement_type,
)
from app.services.sources.primary_fact_parser import PrimaryFact
from app.services.sources.redaction import canonicalize_source_url
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

if TYPE_CHECKING:  # reuse lookup is a plain in-memory dict — never a DB session.
    from app.core.config import Settings
    from app.services.extracted_document_service import ReusedDocument
    from app.services.sources.ocr_provider import OcrBudget, OcrProvider

_log = logging.getLogger("app.services.sources.connectors.company_ir")

# Phase 32A Slice 6B (C7) — content-based markers of a bot-protection /
# challenge page (e.g. Burberry's "Challenge Validation" wall). These pages
# often return a normal 2xx status (so ``SafeFetchResult.blocked``/``error``
# is never set — the fetch technically "succeeded") but their body is NOT the
# real IR page, so zero real annual-report links are ever found. Checked only
# to DISTINGUISH the gap message from a genuine "fetched fine, no links"
# state — never used to fabricate a success, never to retry/bypass anything.
_BOT_PROTECTION_MARKERS: tuple[str, ...] = (
    "just a moment",
    "attention required",
    "checking your browser",
    "verify you are human",
    "please verify you are a human",
    "access denied",
    "challenge validation",
    "cf-challenge",
    "captcha",
    "unusual traffic",
    "enable javascript and cookies",
    "are you a robot",
)


def _looks_like_bot_protection_page(fetched: SafeFetchResult) -> bool:
    """
    Heuristic detection of a bot-protection / challenge page in an otherwise
    "successful" fetch (no ``fetched.blocked``, no ``fetched.error``). Bounded
    to the page title + first 2KB of body — never logs or persists the body
    itself, only the True/False verdict feeding the gap message.
    """
    haystack = " ".join(
        filter(
            None,
            [(fetched.title or "").lower(), (fetched.body_html or "")[:2000].lower()],
        )
    )
    if not haystack:
        return False
    return any(marker in haystack for marker in _BOT_PROTECTION_MARKERS)

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
    # Phase 32A Slice 5B.1 — bounded, sanitized provenance + failure telemetry.
    # Every field is optional and defaulted, so no existing construction changes.
    # None means "not known", never a claim; nothing here can carry provider text,
    # a URL secret, an address or an exact HTTP status code.
    #
    # What kind of document the discovery layer classified this as
    # (``annual_report`` / ``interim_report`` / …) and HOW its URL was found
    # (``anchors`` / ``next_data`` / ``sec_accession`` / …).
    doc_kind: str | None = None
    discovery_strategy: str | None = None
    # A member of the CLOSED ``ingestion_status`` vocabulary saying why this
    # document did not reach ``extracted``. Never raw provider/exception text.
    failure_code: str | None = None
    # ``2xx``/``3xx``/``4xx``/``5xx`` only — the exact status code is never kept.
    http_status_class: str | None = None
    # Whether the connection was PINNED to a pre-validated address (ADR-014/015).
    # ``True`` = pinned; ``False`` = an honest "not pinned" (kill-switch off, or
    # this httpx build cannot support it) — never a claim that pinning happened;
    # ``None`` = no fetch was attempted at all (budget-exhausted / reused).
    pinned: bool | None = None
    # sha256 of the RAW fetched bytes — ties an attempt back to its extracted
    # document row without duplicating it. Never a secret.
    content_hash: str | None = None


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

# Slice 5B.1 ranking: a document the discovery layer explicitly classified
# outranks one that merely matched a keyword. Unclassified links sort between
# results releases and presentations so 5A behaviour is not demoted.
_DOC_KIND_RANK: dict[str | None, int] = {
    DOC_KIND_ANNUAL_REPORT: 0,
    DOC_KIND_RESULTS_RELEASE: 1,
    DOC_KIND_INTERIM_REPORT: 2,
    DOC_KIND_PRESENTATION: 4,
    DOC_KIND_OTHER: 5,
}
_DOC_KIND_RANK_DEFAULT = 3

_IR_TRANSPORT_LABEL = "Company IR / newsroom (issuer-published)"

# Map an extraction excerpt's evidence_type to an EvidenceItem source_type.
_EXCERPT_SOURCE_TYPE = {
    EVIDENCE_TYPE_BUSINESS: "company_ir_business_description",
    EVIDENCE_TYPE_RISK: "company_ir_risk_excerpt",
}
_DEFAULT_EXCERPT_SOURCE_TYPE = "company_ir_annual_report_excerpt"
# Phase 32A Problem C: statement/table-derived financial content (a prose
# excerpt whose heading/section classifies as a balance sheet / cash-flow
# statement / income statement / segment note, or ANY demoted table row that
# matched a known financial-statement label but fell short of the stricter
# validated-fact bar) gets its OWN source_type so ``evidence_budget.py`` can
# recognise it as ``CATEGORY_STATEMENT_TABLE`` and give it a floor — it must
# not lose a same-category ordering race against generic narrative prose.
_STATEMENT_EXCERPT_SOURCE_TYPE = "company_ir_statement_excerpt"

# --------------------------------------------------------------------------- #
# Bounded issuer-publication traversal (Phase 32A Problem B)
#
# ``fetch_filings`` historically fetched ONLY ``annual_reports_url`` — the
# issuer's separately-registered ``investor_relations_url`` stayed inert
# metadata, and nothing ever followed a link one page deeper. This left a
# proven gap: an issuer's official, English, CURRENT financial results were
# publicly reachable on its own site but never traversed (the confirmed LVMH
# case). The fix below is deliberately narrow: ONE extra candidate index page
# (``investor_relations_url``) + AT MOST ``_MAX_CHILD_LANDING_PAGES`` bounded,
# single-hop child fetches — never a general crawler, never unbounded, and
# every fetch reuses the SAME safe/allowlisted/SSRF-guarded ``page_fetcher``.
# --------------------------------------------------------------------------- #

# Named, conservative bound: at most this many non-document candidate links
# (already keyword-matched on a verified index page) are followed ONE hop
# deeper to look for the actual current-results document/page.
_MAX_CHILD_LANDING_PAGES = 3

# Generic (never issuer-specific) ranking vocabulary for candidate report
# links — annual > half-year/interim > generic financial publication.
_CANDIDATE_ANNUAL_MARKERS: tuple[str, ...] = (
    "annual report",
    "annual results",
    "universal registration document",
    "registration document",
    "integrated report",
    "annual financial report",
    "full-year results",
    "full year results",
)
_CANDIDATE_INTERIM_MARKERS: tuple[str, ...] = (
    "half-year",
    "half year",
    "interim",
    "first-half",
    "first half",
    "h1 results",
    "quarterly results",
)
_CANDIDATE_GENERIC_MARKERS: tuple[str, ...] = (
    "financial report",
    "results presentation",
    "financial results",
    "results release",
    "regulated financial publication",
)
_CANDIDATE_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _candidate_rank_tier(text: str, url: str) -> int:
    """Generic candidate-report ranking tier (0 is best). Never issuer-specific."""
    hay = f"{text} {url}".lower()
    if any(m in hay for m in _CANDIDATE_ANNUAL_MARKERS):
        return 0
    if any(m in hay for m in _CANDIDATE_INTERIM_MARKERS):
        return 1
    if any(m in hay for m in _CANDIDATE_GENERIC_MARKERS):
        return 2
    return 3


def _candidate_recency(text: str, url: str) -> int:
    """Most recent plausible 4-digit year mentioned, else 0 (never guessed)."""
    years = [int(m.group(0)) for m in _CANDIDATE_YEAR_RE.finditer(f"{text} {url}")]
    return max(years) if years else 0


def _rank_candidate_links(links: list[SafeLink]) -> list[SafeLink]:
    """Rank candidate report links generically: annual/full-year results beat
    half-year/interim/H1 results beat a generic financial-report/regulated
    publication link; ties broken by more-recent year-in-text/url, then an
    English-language link (best-effort, from link text only), then original
    discovery order. Never hardcodes which document an issuer should have.
    """

    def key(link: SafeLink) -> tuple[int, int, int]:
        text = link.text or ""
        tier = _candidate_rank_tier(text, link.url)
        recency = -_candidate_recency(text, link.url)
        english = 0 if detect_language(text) == "en" else 1
        return (tier, recency, english)

    return sorted(links, key=key)


def _merge_links(*link_lists: list[SafeLink]) -> list[SafeLink]:
    """Dedup-merge ``SafeLink`` lists by URL, preserving first-seen order."""
    seen: set[str] = set()
    merged: list[SafeLink] = []
    for links in link_lists:
        for link in links:
            if link.url in seen:
                continue
            seen.add(link.url)
            merged.append(link)
    return merged


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
        primary_document_reuse: "dict[str, ReusedDocument] | None" = None,
        ocr_provider: "OcrProvider | None" = None,
        ocr_budget: "OcrBudget | None" = None,
        max_docs_per_issuer: int = 1,
        ingestion_budget_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        cfg: "Settings | None" = None,
    ) -> None:
        self._fetcher = press_fetcher
        self._verified = verified_source
        self._page_fetcher = page_fetcher
        self._document_extractor = document_extractor
        # Phase 32A Slice 5 (deep ingestion, gated by the master flag): when a deep
        # extractor is injected the connector fetches up to ``max_docs_per_issuer``
        # documents under an AGGREGATE wall-budget and collects rich artifacts.
        self._primary_document_extractor = primary_document_extractor
        # Phase 32A Slice 5B.2: optional real-OCR fallback for a scanned document,
        # threaded straight into the injected ``primary_document_extractor`` call
        # (e.g. ``live_primary_document_extractor``). None ⇒ never attempted,
        # byte-identical to Slice 5B.1.
        self._ocr_provider = ocr_provider
        self._ocr_budget = ocr_budget
        # Phase 32A Slice 5 (3c-iii): an OPTIONAL, in-memory reuse lookup (NOT a DB
        # session) keyed by canonical URL. When a candidate document is already in
        # it, the connector rebuilds the persisted artifact and SKIPS the network
        # fetch/extract. Empty / None ⇒ every candidate is fetched (byte-identical).
        self._primary_document_reuse: dict[str, ReusedDocument] = (
            primary_document_reuse or {}
        )
        self._max_docs_per_issuer = max(1, max_docs_per_issuer)
        self._ingestion_budget_seconds = ingestion_budget_seconds
        self._clock = clock
        # Deep artifacts (extractions + validated facts) collected this run, threaded
        # OUT for a later persistence task. Empty on the OFF / shallow path.
        self.collected_primary_document_artifacts: list[PrimaryDocumentArtifact] = []
        self._cfg = cfg
        # Phase 32A Slice 5B.1: url -> (doc_kind, discovery_strategy) for every
        # candidate the discovery layer classified. Side map (not on SafeLink) so
        # the Slice 5A link shape and every existing caller stay unchanged.
        self._document_kinds: dict[str, tuple[str, str]] = {}

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

    # -- bounded second-hop child-page discovery (Phase 32A Problem B) -----

    async def _hop_into_landing_pages(
        self, candidates: list[SafeLink], v: VerifiedIssuerSource
    ) -> tuple[list[SafeLink], int, int]:
        """Follow AT MOST ``_MAX_CHILD_LANDING_PAGES`` candidate links ONE hop
        deeper when they look like a results/reports LANDING page rather than a
        direct document.

        ``candidates`` are already keyword-matched (they came out of an
        allowlisted index-page fetch), so a non-document candidate here is
        exactly the "half-year results" / "financial results" style link that
        itself lists the actual current-results document rather than being one.
        Every fetch reuses the SAME safe, allowlisted, SSRF-guarded
        ``self._page_fetcher`` — no new fetch machinery, and this method never
        recurses into what IT discovers (a single, capped extra hop only).
        Returns ``(discovered_links, pages_examined, pages_with_candidates)``
        so the caller can distinguish "hop attempted, nothing found" from
        "hop never attempted" in its gap message.
        """
        assert self._page_fetcher is not None
        landing_candidates = [ln for ln in candidates if not ln.is_document][
            :_MAX_CHILD_LANDING_PAGES
        ]
        discovered: list[SafeLink] = []
        pages_with_candidates = 0
        for candidate in landing_candidates:
            try:
                child = await self._page_fetcher(
                    candidate.url,
                    allowed_domains=v.allowed_domains,
                    keywords=ANNUAL_REPORT_KEYWORDS,
                    fallback_keywords=FALLBACK_REPORT_KEYWORDS,
                )
            except Exception:  # noqa: BLE001 - a bounded hop must never break the run
                continue
            if child.blocked or (child.error and not child.ok):
                continue
            if child.links:
                pages_with_candidates += 1
                discovered.extend(child.links)
        return discovered, len(landing_candidates), pages_with_candidates

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

        # Phase 32A Problem B — a second, independent candidate index page. The
        # investor-relations landing page is registered separately from the
        # annual-reports/results index and was historically only ever used as
        # INERT metadata (never fetched) — this is the proven LVMH gap: current
        # results existed on the issuer's own site but were never traversed
        # because only ONE page was ever fetched. Reuses the SAME safe fetcher /
        # allowlist / SSRF guards; a failure here is non-fatal — the primary
        # index result still stands.
        fetched_ir: SafeFetchResult | None = None
        if v.investor_relations_url and v.investor_relations_url != v.annual_reports_url:
            try:
                fetched_ir = await self._page_fetcher(
                    v.investor_relations_url,
                    allowed_domains=v.allowed_domains,
                    keywords=ANNUAL_REPORT_KEYWORDS,
                    fallback_keywords=FALLBACK_REPORT_KEYWORDS,
                )
            except Exception:  # noqa: BLE001 - a second index fetch must never break the run
                fetched_ir = None
            if fetched_ir is not None and (
                fetched_ir.blocked or (fetched_ir.error and not fetched_ir.ok)
            ):
                gaps.append(
                    SourceGap(
                        connector_key=self.connector_key,
                        source_id="company_ir",
                        gap_type=GapType.primary_filing_unavailable,
                        severity=GapSeverity.info,
                        message=(
                            "Company IR investor-relations page could not be "
                            f"safely fetched ({fetched_ir.error or 'blocked'}); "
                            "only the primary annual-reports index was used."
                        ),
                        blocks_research_complete=False,
                    )
                )
                fetched_ir = None

        combined_links = _merge_links(
            fetched.links, fetched_ir.links if fetched_ir is not None else []
        )

        # Phase 32A Problem B — ONE bounded extra hop: a same-domain candidate
        # discovered on an already-verified index page that itself looks like a
        # results/reports LANDING page (already keyword-matched, not itself a
        # direct document) is followed exactly once, to recover the actual
        # current-results document one level deeper. Never a general crawler —
        # capped at ``_MAX_CHILD_LANDING_PAGES`` pages, no recursion into what
        # this hop itself discovers.
        child_links, child_pages_examined, child_pages_with_candidates = (
            await self._hop_into_landing_pages(combined_links, v)
        )
        all_candidate_links = _rank_candidate_links(
            _merge_links(combined_links, child_links)
        )

        log_event(
            _log,
            "company_ir_index_discovery",
            connector_key=self.connector_key,
            primary_index_links=len(fetched.links),
            secondary_index_fetched=fetched_ir is not None,
            secondary_index_links=len(fetched_ir.links) if fetched_ir is not None else 0,
            child_pages_examined=child_pages_examined,
            child_pages_with_candidates=child_pages_with_candidates,
            child_discovered_links=len(child_links),
            total_candidate_links=len(all_candidate_links),
        )

        cap = max(1, query.max_items)
        for i, link in enumerate(all_candidate_links[:cap], start=1):
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
        # Phase 32A Problem B — a distinct, always-reachable status for "the
        # bounded extra hop WAS attempted (at least one landing-page candidate
        # existed and was followed) but it discovered no further document/page".
        # This is deliberately a SEPARATE check from the "no candidate at all"
        # block below: a landing-page candidate that led nowhere still counts as
        # itself being "a candidate" for that block (it is still a same-domain,
        # keyword-matched link and may still be surfaced as weak evidence), so
        # the two states are not mutually exclusive with "no candidate at all"
        # and must not be collapsed into one message.
        if child_pages_examined > 0 and not child_links:
            gaps.append(
                SourceGap(
                    connector_key=self.connector_key,
                    source_id="company_ir",
                    gap_type=GapType.primary_filing_unavailable,
                    severity=GapSeverity.info,
                    message=(
                        "Company IR index page(s) were fetched and "
                        f"{child_pages_examined} child result-page candidate(s) "
                        "were followed one hop deeper, but no further document "
                        "or page was discovered there (child result-page hop "
                        "attempted, no candidate)."
                    ),
                    blocks_research_complete=False,
                )
            )

        if not all_candidate_links:
            # Phase 32A Slice 6B (C7) — distinguish an ACTIVELY BLOCKED fetch
            # (bot protection / challenge page returned instead of the real
            # IR page) from a fetch that genuinely succeeded and found zero
            # candidate links. Both are honest, non-fabricating gap states —
            # neither ever claims success — but conflating them into one
            # message hid the real reason no links were found (the confirmed
            # Burberry case: an active "Challenge Validation" wall, not a
            # missing/absent link).
            if _looks_like_bot_protection_page(fetched):
                gap_message = (
                    "Company IR source fetch was blocked (bot protection / "
                    "access denied) — annual report links could not be "
                    "evaluated."
                )
            else:
                gap_message = (
                    "Company IR source found but annual report link not "
                    "identified by bounded extractor (no candidate on primary "
                    "index)."
                )
            gaps.append(
                SourceGap(
                    connector_key=self.connector_key,
                    source_id="company_ir",
                    gap_type=GapType.primary_filing_unavailable,
                    severity=GapSeverity.info,
                    message=gap_message,
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
        if self._primary_document_extractor is not None:
            # Slice 5B.1: augment the anchor links with the bounded non-browser
            # discovery strategies before ranking, so a JS-rendered IR page can
            # still yield real document candidates. Phase 32A Problem B: also
            # runs discovery over the secondary (investor-relations) index page
            # and merges in whatever the bounded child-landing-page hop found.
            deep_links = self._discover_deep_targets(fetched)
            if fetched_ir is not None:
                deep_links = _merge_links(
                    deep_links, self._discover_deep_targets(fetched_ir)
                )
            deep_links = _rank_candidate_links(_merge_links(deep_links, child_links))
            if deep_links:
                doc_items, doc_gaps, artifacts = (
                    await self._extract_primary_documents_deep(deep_links, query, company)
                )
            else:
                doc_items, doc_gaps, artifacts = [], [], []
            items.extend(doc_items)
            gaps.extend(doc_gaps)
            self.collected_primary_document_artifacts.extend(artifacts)
        # Phase 29B.2: bounded (shallow) document extraction. When a document
        # extractor is injected (both connector + document-extraction flags on),
        # fetch ONE already-discovered annual-report document, extract bounded
        # excerpts and parse high-confidence primary facts into tiered T1 evidence.
        # A blocked / scanned / JS-gated document degrades to an honest gap.
        elif self._document_extractor is not None and all_candidate_links:
            doc_items, doc_gaps = await self._extract_primary_document(
                all_candidate_links, query
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

        # Phase 32A Problem F: trust the CONTENT-based determination on ``ext`` —
        # it already threads the issuer's domicile guess through as a WEAK
        # fallback (via ``original_language=`` on the extractor call below), so
        # ORing in a second, cruder domicile-only guess here could only ever
        # wrongly force a confident "this is English" result to True (the
        # LVMH/CFR bug). Never silently override an honest ``False``.
        requires_tr = ext.requires_translation
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
                    scope=_infer_scope(exc.heading),
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
                    scope=fact.scope,
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
                        scope=fact.scope,
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

    def _discovery_strategies(self) -> tuple[str, ...]:
        """Parse ``primary_document_discovery_strategies`` into an ordered tuple.

        Comma-separated, whitespace-trimmed, case-insensitive; an unknown name is
        ignored rather than failing the run (config is operator-set, not user
        input). An absent / blank / entirely-unknown setting falls back to the
        module default, so discovery is never accidentally switched off by a typo.
        """
        raw = getattr(self._cfg, "primary_document_discovery_strategies", None)
        if not isinstance(raw, str):
            return DEFAULT_STRATEGIES
        known = set(DEFAULT_STRATEGIES)
        wanted = tuple(
            name
            for name in dict.fromkeys(
                part.strip().lower() for part in raw.split(",") if part.strip()
            )
            if name in known
        )
        return wanted or DEFAULT_STRATEGIES

    def _discover_deep_targets(self, fetched: SafeFetchResult) -> list[SafeLink]:
        """Merge anchor links with the richer, non-browser discovery strategies.

        Phase 32A Slice 5B.1. Slice 5A only read ``<a href>`` tags, so a
        JS-rendered IR page (Burberry, Kering, LVMH, Hermes, BAE) yielded zero
        candidates even though the document URLs were sitting in the page's
        hydration payload. This runs the bounded strategies — JSON-LD, Next.js /
        Nuxt / ``__INITIAL_STATE__`` hydration state, embedded script JSON — over
        the already-fetched, already-capped body and merges what they find.

        Anchors keep priority: a document found by both appears once, attributed
        to the anchor. Everything still passes the same https / safe-host /
        allowlist / secret-strip checks. No browser, no crawl, no extra fetch.
        """
        links = list(fetched.links)
        body = fetched.body_html
        if not body or not self._verified:
            return links

        try:
            discovered = discover_documents(
                body,
                base_url=fetched.final_url or fetched.requested_url,
                allowed_domains=self._verified.allowed_domains,
                cfg=self._cfg,
                strategies=self._discovery_strategies(),
            )
        except Exception:  # noqa: BLE001 - discovery must never break a run
            return links

        known_anchors = {ln.url for ln in links}
        known = set(known_anchors)
        added = 0
        for doc in discovered:
            if doc.url in known:
                # Already an anchor hit — keep the anchor, but record the kind so
                # ranking and the attempt record still see the classification.
                self._document_kinds.setdefault(doc.url, (doc.doc_kind, STRATEGY_ANCHORS))
                continue
            known.add(doc.url)
            self._document_kinds[doc.url] = (doc.doc_kind, doc.strategy)
            links.append(
                SafeLink(url=doc.url, text=doc.title, is_document=doc.is_document)
            )
            added += 1

        if added:
            log_event(
                _log,
                "primary_document_discovery_augmented",
                connector_key=self.connector_key,
                anchor_links=len(fetched.links),
                discovered_links=added,
                strategies=",".join(
                    sorted({d.strategy for d in discovered if d.url not in known_anchors})
                ),
            )
        return links

    def _rank_deep_targets(self, links: list[SafeLink]) -> list[SafeLink]:
        """Order report links most-material-first, de-dup by URL, cap per issuer.

        Prefers annual-report / results / registration documents and downloadable
        (PDF) links; stable within equal rank. Bounded by ``max_docs_per_issuer``.
        Slice 5B.1 adds the discovery layer's explicit document classification as
        the primary key, so a classified annual report outranks a generic
        marketing PDF whose link text happens to contain a keyword.
        """

        def rank(link: SafeLink) -> tuple[int, int, int]:
            kind = self._document_kinds.get(link.url, (None, None))[0]
            kind_rank = _DOC_KIND_RANK.get(kind, _DOC_KIND_RANK_DEFAULT)
            text = (link.text or "").lower()
            material = 0 if any(m in text for m in _MATERIAL_DOCUMENT_MARKERS) else 1
            doc = 0 if link.is_document else 1
            return (kind_rank, material, doc)

        seen: set[str] = set()
        ordered: list[SafeLink] = []
        for link in sorted(links, key=rank):
            if link.url in seen:
                continue
            seen.add(link.url)
            ordered.append(link)
        return ordered[: self._max_docs_per_issuer]

    def _stamp_provenance(
        self, artifact: PrimaryDocumentArtifact, target: SafeLink
    ) -> None:
        """Record HOW this candidate was found + its raw-bytes identity.

        Phase 32A Slice 5B.1. Only fills a field the extractor left unset, so a
        deep extractor that already knows its own provenance (the SEC filing-body
        path) is never overwritten. A candidate the discovery layer did not
        classify keeps ``None`` — an honest "not known", never a guessed kind.
        """
        kind = self._document_kinds.get(target.url)
        if kind is not None:
            if artifact.doc_kind is None:
                artifact.doc_kind = kind[0]
            if artifact.discovery_strategy is None:
                artifact.discovery_strategy = kind[1]
        extraction = artifact.extraction
        if artifact.content_hash is None and extraction is not None:
            artifact.content_hash = extraction.content_hash or None

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

            # Phase 32A Slice 5 (3c-iii): reuse a previously extracted document
            # (rebuilt from persisted excerpts + validated facts) INSTEAD of a fresh
            # fetch/extract when its canonical URL is in the reuse lookup. The reused
            # artifact flows into evidence + facts + (idempotent) persistence exactly
            # as a freshly-fetched one. Empty lookup ⇒ always fetch (byte-identical).
            reuse_key = canonicalize_source_url(target.url) or target.url
            reused = self._primary_document_reuse.get(reuse_key)
            if reused is not None:
                artifact = reused.artifact
                log_event(
                    _log,
                    "primary_document_reused",
                    connector_key=self.connector_key,
                    document_index=doc_idx,
                    status=artifact.status,
                    excerpt_count=(
                        len(artifact.extraction.excerpts)
                        if artifact.extraction
                        else 0
                    ),
                    validated_fact_count=sum(
                        1
                        for f in artifact.validated_facts
                        if f.validation_status == VALIDATION_VALIDATED
                    ),
                )
            else:
                # Phase 32A Slice 5B.2: only ever passed when an OCR provider
                # was actually injected, so every existing fake/deep extractor
                # (tests, and any future extractor with the pre-5B.2 signature)
                # keeps working unchanged — this is additive, never required.
                ocr_kwargs: dict[str, Any] = {}
                if self._ocr_provider is not None:
                    ocr_kwargs["ocr_provider"] = self._ocr_provider
                    ocr_kwargs["ocr_budget"] = self._ocr_budget
                artifact = await self._primary_document_extractor(
                    target.url,
                    allowed_domains=allowed,
                    title_hint=target.text or None,
                    original_language=self._original_language(),
                    issuer_context=issuer_context,
                    **ocr_kwargs,
                )
            # Phase 32A Slice 5B.1: carry the discovery provenance + the raw-bytes
            # identity onto the artifact so the durable ingestion-attempt record can
            # say WHAT was tried and HOW it was found — for a reused artifact too.
            # Absent classification stays None (honest "not known"), never a guess.
            self._stamp_provenance(artifact, target)
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
                # Honest record of whether the connection was pinned to a
                # pre-validated address (ADR-014/015); None = no fetch happened.
                pinned=artifact.pinned,
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

        # Phase 32A Problem F: trust ``ext`` (content-first, domicile-hint-as-
        # weak-fallback already applied inside the extractor) — never re-OR a
        # second, cruder domicile-only guess over an honest content result.
        requires_tr = ext.requires_translation
        doc_title = artifact.title or target.text or "Annual report"
        url = artifact.source_url or target.url
        # Phase 32A Slice 5 (3c-ii): the RAW-bytes document identity every deep item
        # from this document carries, so the citation write keys one canonical Source
        # per distinct document (not per url+tier+excerpt hash). Never a secret.
        doc_hash = ext.content_hash
        cap = max(1, query.max_items)
        # The language ``ext`` actually determined (content-first) — never a bare
        # domicile guess, so a confidently-English document is never labelled
        # with the issuer's registered country language (the LVMH/CFR bug).
        orig_lang = ext.language if ext.language != "en" else None
        tr_warn = (
            ["Local-language primary disclosure; machine translation pending "
             "Phase 30 — excerpt is unmodified source text."]
            if requires_tr
            else []
        )

        # 1) prose excerpts (bounded) → T1 primary-document excerpt items.
        for n, exc in enumerate(ext.excerpts[:cap], start=1):
            # Phase 32A Problem C: a heading that classifies as a known
            # financial-statement section (balance sheet / cash-flow statement
            # / income statement / segment note) outranks the generic
            # business/risk/narrative mapping below, so it lands in its own
            # evidence-budget category instead of competing with narrative prose.
            statement_type = classify_statement_type(exc.section or exc.heading)
            if statement_type is not None:
                source_type = _STATEMENT_EXCERPT_SOURCE_TYPE
            else:
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
                    scope=_infer_scope(exc.section or exc.heading),
                    requires_translation=requires_tr,
                    original_language=orig_lang,
                    language=ext.language,
                    data_quality="B" if exc.confidence >= 0.75 else "C",
                    confidence=_confidence_bucket(exc.confidence),
                    fields_supported=[exc.evidence_type],
                    provenance=provenance,
                    document_content_hash=doc_hash,
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
                    document_content_hash=doc_hash,
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
                    # Every excerpt_only fact matched a known financial-statement
                    # row-header label (``_LABEL_PATTERNS``) — it is inherently
                    # statement/table-derived (Phase 32A Problem C), regardless
                    # of whether the surrounding table had a classifiable
                    # heading, so it always gets the priority category.
                    source_type=_STATEMENT_EXCERPT_SOURCE_TYPE,
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
                    document_content_hash=doc_hash,
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
