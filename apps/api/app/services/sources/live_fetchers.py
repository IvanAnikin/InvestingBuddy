"""
Bounded live fetchers for the read-only evidence-preview endpoint — Phase 29B.

These adapt the existing catalyst providers into the connector fetcher shape so
the admin evidence-preview endpoint can demonstrate *real* SEC / company-IR
evidence on demand. They are used ONLY by that endpoint and ONLY when
``source_connector_enabled`` is set; the deterministic report path never calls
them (it replays already-fetched data instead).

Safety properties:
  * No user-supplied URL is ever fetched. SEC uses the fixed ``data.sec.gov``
    host keyed by ticker/CIK; company IR uses the curated verified-issuer feed
    allowlist keyed by ticker. There is no open proxy and no SSRF surface.
  * Bounded by ``query.max_items``; providers never raise (they degrade to an
    empty result), and the connector wraps them in ``call_safe`` regardless.
  * Nothing here logs prompts, completions, or credentials.
"""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.structured_logging import log_event
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.company_ir import (
    PrimaryDocumentArtifact,
    PrimaryDocumentBundle,
)
from app.services.sources.document_fetcher import (
    DocumentFetchResult,
    safe_fetch_document,
)
from app.services.sources.document_text_extractor import extract_document_text
from app.services.sources.extracted_fact_validator import (
    IssuerContext,
    validate_extracted_facts,
)
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.ingestion_status import (
    FAILURE_BUDGET_EXHAUSTED,
    FAILURE_NOT_A_PDF,
    FAILURE_UNKNOWN,
)
from app.services.sources.primary_document_extractor import (
    STATUS_EXTRACTED,
    STATUS_EXTRACTION_FAILED,
    STATUS_METADATA_ONLY,
    extract_primary_document,
)
from app.services.sources.primary_fact_parser import parse_primary_facts
from app.services.sources.safe_web_fetcher import (
    ANNUAL_REPORT_KEYWORDS,
    Resolver,
    SafeFetchResult,
    looks_like_pdf,
    safe_fetch_page,
)
from app.services.sources.sec_filing_documents import (
    STRATEGY_SEC_ACCESSION,
    SecFilingDocument,
    SecRateLimiter,
    doc_kind_for_form,
    fetch_filing_body,
    resolve_filing_documents,
)

_logger = logging.getLogger("app.services.sources.live_fetchers")


def _filing_dict(event: Any) -> dict[str, Any]:
    """Map a ``CatalystEvent`` (SEC filing) into a connector filing dict."""
    get = event.get if isinstance(event, dict) else lambda k, d=None: getattr(event, k, d)
    return {
        "form_type": get("form_type"),
        "title": get("headline") or get("form_type") or "SEC filing",
        "url": get("source_url") or get("related_document_url"),
        "filed_date": get("filing_date") or get("event_date"),
        "summary": get("summary") or get("headline"),
        "accession_number": get("accession_number"),
    }


def _press_dict(item: Any) -> dict[str, Any]:
    """Map a ``NewsItem`` (press release) into a connector press dict."""
    get = item.get if isinstance(item, dict) else lambda k, d=None: getattr(item, k, d)
    return {
        "headline": get("headline"),
        "url": get("url"),
        "published_at": get("published_at"),
        "summary": get("summary"),
        "source_name": get("source_name") or "Company IR / Newsroom",
        "media_url": get("media_url"),
    }


async def live_sec_filings_fetcher(
    company: CompanyContext, query: QueryContext
) -> list[dict[str, Any]]:
    """Fetch recent SEC filing metadata for a US issuer (bounded, never raises)."""
    if not company.ticker:
        return []
    from app.integrations.providers.sec_recent_filings_provider import (
        SecRecentFilingsProvider,
    )

    provider = SecRecentFilingsProvider()
    result = await provider.get_recent_events(
        ticker=company.ticker,
        cik=company.cik,
        company_name=company.company_name,
        lookback_days=query.lookback_days or 90,
        max_events=query.max_items,
        exchange=company.exchange,
    )
    return [_filing_dict(e) for e in result.events]


async def live_ir_press_fetcher(
    company: CompanyContext, query: QueryContext
) -> list[dict[str, Any]]:
    """Fetch issuer press releases from the curated verified-issuer feed only.

    Only a curated, verified issuer feed URL is used — never a URL supplied by
    the caller — so there is no arbitrary-URL fetch / SSRF surface.
    """
    if not company.ticker:
        return []
    from app.integrations.exchange_source_registry import get_curated_issuer_source
    from app.integrations.providers.company_press_release_provider import (
        CompanyPressReleaseProvider,
    )

    curated = get_curated_issuer_source(company.ticker)
    feed_urls = []
    website = None
    if curated:
        website = curated.website
        if curated.press_release_feed_url:
            feed_urls.append(curated.press_release_feed_url)
    if not feed_urls and not website:
        return []

    provider = CompanyPressReleaseProvider()
    result = await provider.get_press_releases(
        ticker=company.ticker,
        company_name=company.company_name,
        website=website,
        lookback_days=query.lookback_days or 90,
        max_items=query.max_items,
        feed_urls=feed_urls or None,
    )
    return [_press_dict(i) for i in result.items]


async def live_ir_page_fetcher(
    url: str,
    *,
    allowed_domains: tuple[str, ...],
    keywords: tuple[str, ...] = ANNUAL_REPORT_KEYWORDS,
    fallback_keywords: tuple[str, ...] = (),
    resolver: Resolver = socket.getaddrinfo,
) -> SafeFetchResult:
    """Bounded, SSRF-safe fetch of ONE allowlisted issuer page (preview path).

    The URL is never caller-supplied — it originates from the code-defined
    verified-issuer registry (or a link already extracted from an allowlisted
    page) and is re-checked against ``allowed_domains`` before the request. Never
    raises: every failure degrades to a ``SafeFetchResult`` with ``error`` set.

    Phase 32A Slice 5B.1: ``resolve_ip=True`` so the host's resolved addresses are
    validated before the connection AND after every redirect hop, and the
    connection is pinned to the validated address (ADR-014). This matters because
    this page's BODY is what the discovery strategies parse for document
    candidates — an unvalidated address here would feed everything downstream.
    """
    return await safe_fetch_page(
        url,
        allowed_domains=allowed_domains,
        keywords=keywords,
        fallback_keywords=fallback_keywords,
        resolve_ip=True,
        resolver=resolver,
    )


async def live_document_extractor(
    url: str,
    *,
    allowed_domains: tuple[str, ...],
    title_hint: str | None = None,
    original_language: str | None = None,
    cfg: Settings | None = None,
) -> PrimaryDocumentBundle:
    """Fetch + extract + parse ONE allowlisted annual-report document (Phase 29B.2).

    The URL is never caller-supplied — it is an annual-report link already
    extracted from an allowlisted issuer page (or the verified-issuer registry)
    and is re-checked against ``allowed_domains`` inside ``safe_fetch_document``.
    Bounded and SSRF-safe; never raises — every failure degrades to a bundle with
    honest ``source_gaps``.
    """
    cfg = cfg or default_settings
    fetched = await safe_fetch_document(url, allowed_domains=allowed_domains, cfg=cfg)
    bundle = PrimaryDocumentBundle(
        source_url=fetched.final_url or fetched.requested_url,
        document_type=fetched.document_type,
        warnings=list(fetched.warnings),
        source_gaps=list(fetched.source_gaps),
    )
    if not fetched.ok or fetched.content is None or fetched.document_type is None:
        return bundle

    extraction = extract_document_text(
        fetched.content,
        document_type=fetched.document_type,
        source_url=bundle.source_url,
        title_hint=title_hint,
        original_language=original_language,
        cfg=cfg,
    )
    bundle.extraction = extraction
    bundle.warnings.extend(extraction.warnings)
    if extraction.excerpts:
        bundle.facts = parse_primary_facts(extraction)
    return bundle


def _honest_gap(message: str) -> SourceGap:
    """A bounded, non-blocking company-IR ``SourceGap`` (never fabricated data)."""
    return SourceGap(
        connector_key="company_ir",
        source_id="company_ir",
        gap_type=GapType.primary_filing_unavailable,
        severity=GapSeverity.info,
        message=message,
        blocks_research_complete=False,
    )


def _artifact_from_fetch(
    fetched: DocumentFetchResult,
    *,
    title: str | None,
    original_language: str | None,
    issuer_context: IssuerContext | None,
    cfg: Settings,
    fetch_ms: int,
) -> PrimaryDocumentArtifact:
    """Extract + validate ONE already-fetched document into an artifact.

    The shared body of the issuer-IR and SEC filing-body deep extractors, so the
    two can never drift on magic-byte guarding, status, gap text or the failure
    telemetry Slice 5B.1 persists. Pure of network: the fetch already happened.

    Never raises. Every non-``extracted`` outcome carries a sanitized
    ``failure_code`` from the CLOSED ``ingestion_status`` vocabulary — never
    provider text, a URL, an address or an exact HTTP status code.
    """
    artifact = PrimaryDocumentArtifact(
        source_url=fetched.final_url or fetched.requested_url,
        document_type=fetched.document_type,
        title=title,
        retrieved_at=datetime.now(timezone.utc),
        status=STATUS_EXTRACTION_FAILED,
        warnings=list(fetched.warnings),
        source_gaps=list(fetched.source_gaps),
        fetch_ms=fetch_ms,
    )
    # Only the status CLASS is retained — the exact code is never carried.
    artifact.http_status_class = fetched.status_class
    # Was the connection pinned to a pre-validated address (ADR-014/015)? False is
    # an honest "not pinned" and is recorded as such, never silently dropped.
    artifact.pinned = bool(fetched.pinned)

    # Blocked / off-domain / rebinding / http error / disallowed type → honest gap.
    if not fetched.ok or fetched.content is None or fetched.document_type is None:
        artifact.failure_code = fetched.failure_code
        return artifact

    # Magic-byte guard: never feed a non-PDF blob (an HTML error page served as
    # octet-stream, say) to the PDF parser — degrade honestly instead.
    if fetched.document_type == "pdf" and not looks_like_pdf(fetched.content):
        artifact.status = STATUS_METADATA_ONLY
        artifact.failure_code = FAILURE_NOT_A_PDF
        artifact.source_gaps.append(
            _honest_gap(
                "Annual-report document is not a valid PDF (missing %PDF- "
                "signature); document text is not extracted."
            )
        )
        return artifact

    extract_started = time.perf_counter()
    extraction = extract_primary_document(
        fetched.content,
        document_type=fetched.document_type,
        cfg=cfg,
        original_language=original_language,
    )
    artifact.extraction_ms = int((time.perf_counter() - extract_started) * 1000)
    artifact.extraction = extraction
    artifact.status = extraction.status
    artifact.warnings.extend(extraction.warnings)
    # The extractor already reports WHY (scanned / encrypted / malformed / …);
    # a successful extraction leaves this None.
    artifact.failure_code = extraction.failure_code
    artifact.content_hash = extraction.content_hash or None

    # Only a fully-extracted document is validated into structured facts; a
    # scanned / empty document stays metadata_only with no fabricated fact.
    if extraction.status == STATUS_EXTRACTED:
        artifact.validated_facts = validate_extracted_facts(
            extraction,
            issuer_context=issuer_context or IssuerContext(),
            cfg=cfg,
        )
    return artifact


async def live_primary_document_extractor(
    url: str,
    *,
    allowed_domains: tuple[str, ...],
    title_hint: str | None = None,
    original_language: str | None = None,
    issuer_context: IssuerContext | None = None,
    cfg: Settings | None = None,
    resolver: Resolver = socket.getaddrinfo,
) -> PrimaryDocumentArtifact:
    """DEEP fetch + structure-aware extraction + stricter validation of ONE doc.

    The structure-aware sibling of :func:`live_document_extractor` (Phase 32A
    Slice 5, master flag). The URL is never caller-supplied — it is an
    annual-report link already extracted from an allowlisted issuer page and is
    re-checked against ``allowed_domains`` inside ``safe_fetch_document``. The
    fetch additionally DNS-resolves the host (``resolve_ip=True``) and rejects any
    non-public resolved IP (DNS-rebinding SSRF guard). For a PDF the ``%PDF`` magic
    is verified before parsing. Runs ``extract_primary_document`` (pdfplumber
    tables / HTML) then ``validate_extracted_facts`` (stricter grid validation).

    Never raises: an honest failure degrades to a ``metadata_only`` /
    ``extraction_failed`` artifact with honest gaps. Never logs bytes or text; the
    stored URL is secret-stripped by the fetch layer.
    """
    cfg = cfg or default_settings
    fetch_started = time.perf_counter()
    fetched = await safe_fetch_document(
        url,
        allowed_domains=allowed_domains,
        cfg=cfg,
        resolve_ip=True,
        resolver=resolver,
    )
    fetch_ms = int((time.perf_counter() - fetch_started) * 1000)
    return _artifact_from_fetch(
        fetched,
        title=title_hint,
        original_language=original_language,
        issuer_context=issuer_context,
        cfg=cfg,
        fetch_ms=fetch_ms,
    )


# --------------------------------------------------------------------------- #
# SEC filing-BODY ingestion (Phase 32A Slice 5B.1)
# --------------------------------------------------------------------------- #

# SUPPLEMENT ONLY. This path adds narrative filing-BODY evidence for a US issuer.
# It does NOT touch, replace, re-derive or second-guess the SEC/XBRL structured
# facts — those keep coming from the companyfacts pipeline and stay authoritative
# for every financial number. Nothing here produces a figure of its own beyond
# what the stricter table validator extracts from the filing itself.


def _sec_gap(message: str) -> SourceGap:
    """A bounded, non-blocking ``sec_edgar`` ``SourceGap`` (never fabricated)."""
    return SourceGap(
        connector_key="sec_edgar",
        source_id="sec_edgar",
        gap_type=GapType.primary_filing_unavailable,
        severity=GapSeverity.info,
        message=message,
        blocks_research_complete=False,
    )


def _sec_budget_artifact(doc: SecFilingDocument) -> PrimaryDocumentArtifact:
    """An honest 'not fetched — budget exhausted' record for one filing body.

    Emitted INSTEAD of a fetch so the durable attempt record still shows the
    document was identified and deliberately skipped. Carries no content, no
    excerpt and no fact.
    """
    return PrimaryDocumentArtifact(
        source_url=doc.canonical_url,
        title=f"{doc.form_type} {doc.accession_number}",
        retrieved_at=datetime.now(timezone.utc),
        status=STATUS_EXTRACTION_FAILED,
        failure_code=FAILURE_BUDGET_EXHAUSTED,
        doc_kind=doc_kind_for_form(doc.form_type),
        discovery_strategy=STRATEGY_SEC_ACCESSION,
        source_gaps=[
            _sec_gap(
                "Primary-document ingestion budget exhausted "
                f"(ingestion_budget_exhausted); SEC {doc.form_type} filing body "
                "was identified but not fetched."
            )
        ],
    )


async def live_sec_primary_document_extractor(
    cik: str | int | None,
    filings: list[dict[str, Any]] | None,
    *,
    cfg: Settings | None = None,
    issuer_context: IssuerContext | None = None,
    resolver: Resolver | None = None,
    max_documents: int | None = None,
    budget_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> list[PrimaryDocumentArtifact]:
    """Fetch + extract the BODY of up to N of a US issuer's own SEC filings.

    Phase 32A Slice 5B.1. Until now the SEC path read only *structured* JSON, so
    a US issuer produced zero primary-document candidates and every SEC result
    carried a ``primary_filing_unavailable`` gap. This resolves each filing's
    accession number to its canonical body document on ``www.sec.gov/Archives``
    and runs the SAME extraction + stricter validation as the issuer-IR path.

    SUPPLEMENT ONLY: SEC/XBRL structured facts are untouched by this function and
    remain the authoritative source for financial numbers.

    Bounded and safe: returns ``[]`` immediately (NO network) unless BOTH
    ``primary_document_ingestion_enabled`` and ``primary_document_sec_body_enabled``
    are on; capped by ``primary_document_sec_max_bodies``; every URL is built from
    code-defined SEC constants and re-checked against ``SEC_ALLOWED_DOMAINS``
    inside the SSRF-safe fetcher (with ``resolve_ip=True``); every request is
    throttled by a single shared ``SecRateLimiter``.

    ``budget_seconds`` is the AGGREGATE wall-budget for this call, and it covers
    RESOLUTION as well as fetching: the same deadline is handed to
    ``resolve_filing_documents`` so a long filings list cannot burn the whole
    request in index round-trips before the fetch loop is even entered. Once the
    budget is spent no further fetch is STARTED and each remaining resolved
    document is recorded honestly with a ``budget_exhausted`` failure code —
    never a fabricated excerpt or fact.

    Never raises: any failure degrades to an honest artifact (or an empty list).
    """
    cfg = cfg or default_settings
    if not (
        getattr(cfg, "primary_document_ingestion_enabled", False)
        and getattr(cfg, "primary_document_sec_body_enabled", False)
    ):
        return []

    raw_cap = (
        max_documents
        if max_documents is not None
        else getattr(cfg, "primary_document_sec_max_bodies", 0)
    )
    try:
        cap = max(0, int(raw_cap))
    except (TypeError, ValueError):
        cap = 0
    if cap == 0 or not filings:
        return []

    started = clock()
    # Resolution shares the SAME clock and budget as the fetch loop below, so the
    # index round-trips can never escape the aggregate wall-budget.
    deadline = started + budget_seconds if budget_seconds is not None else None
    limiter = SecRateLimiter(cfg=cfg)
    try:
        documents = await resolve_filing_documents(
            cik,
            list(filings),
            max_documents=cap,
            cfg=cfg,
            limiter=limiter,
            deadline=deadline,
            clock=clock,
            resolver=resolver,
        )
    except Exception:  # noqa: BLE001 - resolution never breaks a run
        return []

    artifacts: list[PrimaryDocumentArtifact] = []
    exhausted = False
    for doc in documents:
        if exhausted or (
            budget_seconds is not None and (clock() - started) >= budget_seconds
        ):
            exhausted = True
            artifacts.append(_sec_budget_artifact(doc))
            continue

        fetch_started = time.perf_counter()
        try:
            fetched = await fetch_filing_body(
                doc, cfg=cfg, resolver=resolver, limiter=limiter
            )
        except Exception:  # noqa: BLE001 - the fetcher should not raise; belt-and-braces
            fetched = None
        fetch_ms = int((time.perf_counter() - fetch_started) * 1000)

        if fetched is None:
            artifact = PrimaryDocumentArtifact(
                source_url=doc.canonical_url,
                retrieved_at=datetime.now(timezone.utc),
                status=STATUS_EXTRACTION_FAILED,
                failure_code=FAILURE_UNKNOWN,
                fetch_ms=fetch_ms,
                source_gaps=[
                    _sec_gap(
                        f"SEC {doc.form_type} filing body could not be fetched; "
                        "filing text is not extracted."
                    )
                ],
            )
        else:
            try:
                artifact = _artifact_from_fetch(
                    fetched,
                    title=f"{doc.form_type} {doc.accession_number}",
                    original_language=None,
                    issuer_context=issuer_context,
                    cfg=cfg,
                    fetch_ms=fetch_ms,
                )
            except Exception:  # noqa: BLE001 - extraction never breaks a run
                artifact = PrimaryDocumentArtifact(
                    source_url=doc.canonical_url,
                    retrieved_at=datetime.now(timezone.utc),
                    status=STATUS_EXTRACTION_FAILED,
                    failure_code=FAILURE_UNKNOWN,
                    fetch_ms=fetch_ms,
                    source_gaps=[
                        _sec_gap(
                            f"SEC {doc.form_type} filing body could not be "
                            "extracted; filing text is not extracted."
                        )
                    ],
                )

        # Provenance: the accession number located this document, not a page scan.
        artifact.title = artifact.title or f"{doc.form_type} {doc.accession_number}"
        artifact.doc_kind = doc_kind_for_form(doc.form_type)
        artifact.discovery_strategy = STRATEGY_SEC_ACCESSION
        artifacts.append(artifact)

    log_event(
        _logger,
        "sec_primary_document_ingestion_completed",
        document_count=len(artifacts),
        extracted_count=sum(1 for a in artifacts if a.status == STATUS_EXTRACTED),
        budget_exhausted=exhausted,
    )
    return artifacts


__all__ = [
    "live_sec_filings_fetcher",
    "live_ir_press_fetcher",
    "live_ir_page_fetcher",
    "live_document_extractor",
    "live_primary_document_extractor",
    "live_sec_primary_document_extractor",
]
