"""
Single-company source-evidence collector — Phase 29B.

Runs the source-registry connectors for ONE company and returns bounded, tiered
``EvidenceItem``s plus honest ``SourceGap``s. This is the seam that wires the
connector framework (Phase 29A) into the single-company evidence pack and the
read-only evidence-preview endpoint.

Design guarantees:
  * **No fabrication.** SEC and company-IR connectors only emit evidence when
    given real data; every other case is a gap.
  * **No surprise network calls at report time.** The report path passes
    already-fetched deterministic data (``catalyst_discovery`` filing / press
    events) through a static in-memory fetcher — the connector re-expresses
    known facts as tiered evidence, it does not re-fetch. Live fetching is only
    ever done by the evidence-preview endpoint, which injects a live fetcher and
    is gated by ``source_connector_enabled``.
  * **Exchange-aware.** SEC runs only for SEC-eligible issuers (Phase 27.1A);
    non-US issuers instead route to their home-regulator connector — the
    dedicated UK FCA NSM (29B.4A), Euronext (29B.4B), Deutsche Börse, Nordic and
    SIX Swiss (29B.4C) connectors emit a bounded T2 regulator-transport SOURCE
    REFERENCE plus an honest content gap, while the remaining venues (SEDAR+, ASX)
    surface honest scaffold gaps only.
  * **Bounded.** Every connector is capped at
    ``source_connector_max_items_per_source`` items.
  * **Never raises.** Each connector call goes through ``call_safe``.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.services.exchange_registry import (
    country_for_exchange,
    is_sec_eligible,
    is_us_exchange,
    normalize_exchange,
    region_for_exchange,
)
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.company_ir import (
    _LOCAL_LANGUAGE_COUNTRIES,
    CompanyIrConnector,
    DocumentExtractor,
    PageFetcher,
    PressFetcher,
)
from app.services.sources.connectors.local_language_press import (
    SOURCE_ID as LOCAL_LANGUAGE_PRESS_ID,
)
from app.services.sources.connectors.local_language_press import (
    LocalLanguagePressConnector,
    local_language_press_source_for,
)
from app.services.sources.connectors.sec_edgar import FilingsFetcher, SecEdgarConnector
from app.services.sources.evidence import EvidenceItem
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.registry import SourceRegistry, build_registry
from app.services.sources.verified_issuer_sources import get_verified_issuer_source

# Source ids whose connectors can produce live company evidence in this phase.
SEC_ID = "sec_edgar"
COMPANY_IR_ID = "company_ir"

# Dedicated regulator connectors (Phase 29B.4A/29B.4B/29B.4C). Unlike the generic
# scaffolds, these are real connectors that emit a bounded T2 regulator-transport
# SOURCE REFERENCE (plus an honest content gap), so their evidence items are kept
# — not just their gaps. They are still run through the same regulator loop.
REGULATOR_REFERENCE_IDS = frozenset(
    {
        "uk_fca_nsm",
        "euronext_regulated_info",
        "deutsche_boerse",
        "nordic_disclosures",
        "six_swiss",
    }
)

# Allowlisted local-language business-press reference connector (Phase 30B). Not a
# regulator: it emits a bounded T4 quality-media SOURCE REFERENCE with a genuine
# local-language excerpt for a verified FR / DE / IT / DA issuer (never a
# fabricated news story), consumed by the Phase 30A translation layer. Kept in its
# own set so ``REGULATOR_REFERENCE_IDS`` stays regulator-only.
LOCAL_LANGUAGE_REFERENCE_IDS = frozenset({LOCAL_LANGUAGE_PRESS_ID})

# Explicit, minimal venue/country -> dedicated regulator connector. Keeps each
# issuer mapped to its own home-regulator connector specifically (UK/LSE ->
# uk_fca_nsm; Euronext Paris/Amsterdam -> euronext_regulated_info; German
# Xetra/Frankfurt -> deutsche_boerse; Nasdaq Copenhagen -> nordic_disclosures;
# SIX Swiss -> six_swiss), instead of every Europe-region scaffold (the previous
# over-match).
_EXCHANGE_TO_REGULATOR: dict[str, str] = {
    "LSE": "uk_fca_nsm",
    "PA": "euronext_regulated_info",  # Euronext Paris
    "AS": "euronext_regulated_info",  # Euronext Amsterdam
    "XETRA": "deutsche_boerse",  # Deutsche Börse Xetra
    "F": "deutsche_boerse",  # Frankfurt Stock Exchange
    "DE": "deutsche_boerse",  # EODHD Germany suffix
    "CO": "nordic_disclosures",  # Nasdaq Copenhagen
    "SW": "six_swiss",  # SIX Swiss Exchange
    "VX": "six_swiss",  # SIX Swiss (blue chip)
}
_COUNTRY_TO_REGULATOR: dict[str, str] = {
    "United Kingdom": "uk_fca_nsm",
    "France": "euronext_regulated_info",
    "Netherlands": "euronext_regulated_info",
    "Germany": "deutsche_boerse",
    "Denmark": "nordic_disclosures",
    "Switzerland": "six_swiss",
}


def regulator_connector_for(
    exchange: str | None, country: str | None = None
) -> str | None:
    """Return the dedicated regulator connector id for a venue, or None.

    Resolves by exchange first (``LSE`` -> ``uk_fca_nsm``), then falls back to
    the venue's country (or the caller-supplied country). Explicit and minimal
    by design — a venue with no mapping falls through to the region-scaffold
    behaviour unchanged.
    """
    code = normalize_exchange(exchange)
    if code in _EXCHANGE_TO_REGULATOR:
        return _EXCHANGE_TO_REGULATOR[code]
    resolved_country = country_for_exchange(exchange) or (country or "").strip()
    return _COUNTRY_TO_REGULATOR.get(resolved_country)


class CompanySourceEvidence(BaseModel):
    """Everything the connector layer produced for one company."""

    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    source_gaps: list[SourceGap] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def gap_messages(self) -> list[str]:
        """Compact, de-duplicated gap strings for an evidence pack's known_gaps."""
        seen: dict[str, None] = {}
        for g in self.source_gaps:
            seen.setdefault(g.as_message(), None)
        return list(seen)


def _static_fetcher(items: list[dict] | None):
    """Wrap already-fetched dicts as an async fetcher (no network)."""

    async def _fetch(_company: CompanyContext, _query: QueryContext) -> list[dict]:
        return list(items or [])

    return _fetch


# Document-derived company-IR source types (Phase 29B.2). These legitimately
# share the annual-report URL with the link item, so dedup must key on more than
# the URL, and they must be prioritised ahead of metadata-only items.
_DOCUMENT_SOURCE_TYPES = frozenset(
    {
        "company_ir_annual_report_text",
        "company_ir_annual_report_excerpt",
        "company_ir_business_description",
        "company_ir_risk_excerpt",
        "company_ir_financial_fact",
    }
)


def _dedup_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    """De-duplicate by (URL, source_type, excerpt-snippet), preserving order.

    Keying on the URL alone would collapse the annual-report *link* and the
    bounded *excerpts* / *facts* extracted from that same document (Phase 29B.2)
    into one item. Including source_type + a short excerpt snippet keeps those
    distinct while still dropping true duplicates.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[EvidenceItem] = []
    for it in items:
        key = (
            (it.url or it.id or ""),
            it.source_type or "",
            (it.excerpt or "")[:60],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _prioritize_ir_items(items: list[EvidenceItem]) -> list[EvidenceItem]:
    """Stable sort so extracted document excerpts/facts survive the per-source cap.

    Bucket order: document excerpts + parsed facts (highest value) → annual-report
    link → everything else (profile / index metadata). Order within a bucket is
    preserved, so this never reorders same-value items.
    """

    def bucket(it: EvidenceItem) -> int:
        if it.source_type in _DOCUMENT_SOURCE_TYPES:
            return 0
        if it.source_type == "company_ir_annual_report":
            return 1
        return 2

    return sorted(items, key=bucket)


def _relevant_scaffold_ids(
    registry: SourceRegistry,
    company: CompanyContext,
    requested: Sequence[str] | None,
) -> list[str]:
    """Regulator connector/scaffold source ids relevant to this issuer.

    - Explicit request: only the requested ids that are runnable regulator
      connectors or scaffolds.
    - Default: none for US / SEC-eligible issuers. For a non-US issuer with an
      explicit venue -> regulator mapping (Phase 29B.4A/29B.4B/29B.4C), just that
      dedicated connector (a UK/LSE issuer maps to ``uk_fca_nsm``; a Euronext
      Paris/Amsterdam FR/NL issuer to ``euronext_regulated_info``; a German
      Xetra/Frankfurt issuer to ``deutsche_boerse``; a Nasdaq Copenhagen issuer to
      ``nordic_disclosures``; a SIX Swiss issuer to ``six_swiss`` — each dropping
      the other Europe scaffolds). Otherwise the scaffolds whose region matches
      the issuer's venue, falling back to *all* scaffolds when the region can't be
      resolved (honest over-disclosure).
    """
    scaffold_ids = [s.source_id for s in registry.scaffolded_sources()]
    # Dedicated regulator connectors are real (no longer scaffolds) but still run
    # through this loop, so they must be runnable when explicitly requested.
    runnable = set(scaffold_ids) | {
        sid for sid in REGULATOR_REFERENCE_IDS if sid in registry.connectors()
    }
    if requested is not None:
        return [sid for sid in requested if sid in runnable]

    # US / SEC-eligible issuers need no non-US regulator connectors.
    if is_us_exchange(company.exchange) or is_sec_eligible(company.exchange):
        return []

    # Explicit venue -> regulator mapping wins (e.g. UK/LSE -> uk_fca_nsm,
    # Euronext Paris/Amsterdam -> euronext_regulated_info).
    regulator = regulator_connector_for(company.exchange, company.country)
    if regulator and regulator in registry.connectors():
        return [regulator]

    region = (region_for_exchange(company.exchange) or "").strip().lower()
    matched = [
        s.source_id
        for s in registry.scaffolded_sources()
        if region and (s.region or "").strip().lower() == region
    ]
    return matched or scaffold_ids


async def collect_company_source_evidence(
    *,
    company: CompanyContext,
    source_ids: Sequence[str] | None = None,
    filings: list[dict] | None = None,
    press_items: list[dict] | None = None,
    filings_fetcher: FilingsFetcher | None = None,
    press_fetcher: PressFetcher | None = None,
    ir_page_fetcher: PageFetcher | None = None,
    document_extractor: DocumentExtractor | None = None,
    cfg: Settings | None = None,
    registry: SourceRegistry | None = None,
) -> CompanySourceEvidence:
    """Collect connector evidence + gaps for one company.

    ``filings`` / ``press_items`` are already-fetched deterministic data (report
    path). ``filings_fetcher`` / ``press_fetcher`` are live fetchers (preview
    path) and take precedence when supplied. ``ir_page_fetcher`` (preview path
    only) enables live annual-report / press-link extraction; when None the
    company-IR connector still emits verified-issuer *metadata* evidence with no
    network call. ``document_extractor`` (Phase 29B.2, preview path or the council
    path when both connector + document-extraction flags are on) enables bounded
    fetch + text-extraction + fact-parsing of ONE discovered annual-report
    document; when None no document is fetched (Phase 29B.1 behaviour preserved).
    ``source_ids`` restricts which connectors run; when ``None`` a sensible
    default set runs.
    """
    cfg = cfg or default_settings
    registry = registry or build_registry(cfg)
    max_items = max(1, cfg.source_connector_max_items_per_source)
    query = QueryContext(
        max_items=max_items,
        lookback_days=cfg.discovery_lookback_days,
        country=company.country,
    )
    requested = list(source_ids) if source_ids is not None else None
    verified = get_verified_issuer_source(company.ticker, company.exchange)

    def want(sid: str) -> bool:
        return requested is None or sid in requested

    items: list[EvidenceItem] = []
    gaps: list[SourceGap] = []
    warnings: list[str] = []

    # -- SEC EDGAR (self-gates on eligibility) -----------------------------
    if want(SEC_ID):
        fetcher = filings_fetcher or (
            _static_fetcher(filings) if filings is not None else None
        )
        sec = SecEdgarConnector(filings_fetcher=fetcher)
        res = await sec.call_safe(sec.fetch_filings, company, query)
        items.extend(res.evidence_items[:max_items])
        gaps.extend(res.source_gaps)
        warnings.extend(res.warnings)

    # -- Company IR / newsroom ---------------------------------------------
    # Verified-issuer metadata (profile / annual-reports index / press index)
    # comes from ``search_company`` + ``fetch_filings`` + ``fetch_events``; live
    # annual-report / press links are added only when ``ir_page_fetcher`` is set.
    # The merged company-IR item set is capped at ``max_items``.
    if want(COMPANY_IR_ID):
        fetcher = press_fetcher or (
            _static_fetcher(press_items) if press_items is not None else None
        )
        ir = CompanyIrConnector(
            press_fetcher=fetcher,
            verified_source=verified,
            page_fetcher=ir_page_fetcher,
            document_extractor=document_extractor,
        )
        ir_items: list[EvidenceItem] = []
        for method in (ir.search_company, ir.fetch_filings, ir.fetch_events):
            res = await ir.call_safe(method, company, query)
            ir_items.extend(res.evidence_items)
            gaps.extend(res.source_gaps)
            warnings.extend(res.warnings)
        # Prioritise extracted document excerpts/facts so they survive the
        # per-source cap (Phase 29B.2), then de-dup, then bound.
        items.extend(_prioritize_ir_items(_dedup_evidence(ir_items))[:max_items])

    # -- Non-US primary-disclosure context (Phase 29B.1) -------------------
    # For a verified non-US issuer, home-regulator connectors are still
    # scaffolded — say so honestly, and note the translation limitation.
    if verified and not (is_us_exchange(company.exchange) or is_sec_eligible(company.exchange)):
        gaps.append(
            SourceGap(
                connector_key="company_ir",
                source_id="company_ir",
                gap_type=GapType.connector_scaffolded,
                severity=GapSeverity.info,
                message=(
                    f"{verified.country} regulated-disclosure connector scaffolded; "
                    "company IR annual report used as primary source pending "
                    "regulator integration."
                ),
                suggested_followup_phase="Phase 29B.x",
                blocks_research_complete=False,
            )
        )
        if verified.country in _LOCAL_LANGUAGE_COUNTRIES:
            gaps.append(
                SourceGap(
                    connector_key="company_ir",
                    source_id="company_ir",
                    gap_type=GapType.translation_required,
                    severity=GapSeverity.info,
                    message="Local-language filing extraction pending Phase 30 translation.",
                    suggested_followup_phase="Phase 30",
                    blocks_research_complete=False,
                )
            )

    # -- Regulated-disclosure connectors / scaffolds -----------------------
    # Generic scaffolds yield honest gaps only; the dedicated regulator
    # connectors (e.g. uk_fca_nsm, Phase 29B.4A) additionally yield a bounded
    # T2 regulator-transport SOURCE REFERENCE (never a fabricated filing).
    for sid in _relevant_scaffold_ids(registry, company, requested):
        conn = registry.connectors().get(sid)
        if conn is None:
            continue
        res = await conn.call_safe(conn.fetch_filings, company, query)
        items.extend(res.evidence_items[:max_items])
        gaps.extend(res.source_gaps)
        warnings.extend(res.warnings)

    # -- Local-language business-press reference (Phase 30B) ---------------
    # For a verified non-US issuer whose home market is FR / DE / IT / DA, add a
    # bounded T4 quality-media SOURCE REFERENCE with a genuine local-language
    # excerpt (never a fabricated news story), alongside the regulator reference.
    # It carries requires_translation for the Phase 30A translation layer and
    # deliberately lowers source quality (low confidence, needs human review).
    if (
        want(LOCAL_LANGUAGE_PRESS_ID)
        and verified
        and not (is_us_exchange(company.exchange) or is_sec_eligible(company.exchange))
        and local_language_press_source_for(company, verified=verified) is not None
    ):
        press = LocalLanguagePressConnector(verified_source=verified)
        res = await press.call_safe(press.fetch_filings, company, query)
        items.extend(res.evidence_items[:max_items])
        gaps.extend(res.source_gaps)
        warnings.extend(res.warnings)

    return CompanySourceEvidence(
        evidence_items=items, source_gaps=gaps, warnings=warnings
    )


def sec_filings_from_catalyst(catalyst_discovery: dict | None) -> list[dict]:
    """Adapt already-fetched SEC filing events into connector filing dicts."""
    if not isinstance(catalyst_discovery, dict):
        return []
    out: list[dict] = []
    for e in catalyst_discovery.get("filing_events") or []:
        if not isinstance(e, dict):
            continue
        out.append(
            {
                "form_type": e.get("form_type"),
                "title": e.get("headline") or e.get("form_type") or "SEC filing",
                "url": e.get("source_url") or e.get("related_document_url"),
                "filed_date": e.get("filing_date") or e.get("event_date"),
                "summary": e.get("summary") or e.get("headline"),
                "accession_number": e.get("accession_number"),
            }
        )
    return out


def press_items_from_catalyst(catalyst_discovery: dict | None) -> list[dict]:
    """Adapt already-discovered issuer press releases into connector press dicts."""
    if not isinstance(catalyst_discovery, dict):
        return []
    out: list[dict] = []
    for e in catalyst_discovery.get("press_release_events") or []:
        if not isinstance(e, dict):
            continue
        out.append(
            {
                "headline": e.get("headline"),
                "url": e.get("source_url"),
                "published_at": e.get("event_date") or e.get("discovered_at"),
                "summary": e.get("summary"),
                "source_name": e.get("source_name") or "Company IR / Newsroom",
                "source_url_quality": e.get("source_url_quality"),
                "media_url": e.get("media_url"),
            }
        )
    return out


__all__ = [
    "CompanySourceEvidence",
    "collect_company_source_evidence",
    "sec_filings_from_catalyst",
    "press_items_from_catalyst",
    "regulator_connector_for",
    "SEC_ID",
    "COMPANY_IR_ID",
    "REGULATOR_REFERENCE_IDS",
    "LOCAL_LANGUAGE_REFERENCE_IDS",
    "LOCAL_LANGUAGE_PRESS_ID",
]
