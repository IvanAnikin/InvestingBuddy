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

import time
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING

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
    PrimaryDocumentArtifact,
    PrimaryDocumentDeepExtractor,
)
from app.services.sources.connectors.local_language_press import (
    SOURCE_ID as LOCAL_LANGUAGE_PRESS_ID,
)
from app.services.sources.connectors.local_language_press import (
    LocalLanguagePressConnector,
    local_language_press_source_for,
)
from app.services.sources.connectors.sec_edgar import FilingsFetcher, SecEdgarConnector
from app.services.sources.evidence import (
    EvidenceItem,
    PrimaryFactRef,
    build_evidence_item,
)
from app.services.sources.extracted_fact_validator import (
    VALIDATION_VALIDATED,
    IssuerContext,
)
from app.services.sources.financial_fact_categories import (
    financial_fact_diversity_key,
    primary_fact_field,
    primary_fact_period_rank,
    select_category_diverse,
)
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.ocr_provider import OcrBudget, OcrProvider
from app.services.sources.primary_document_extractor import (
    STATUS_EXTRACTED,
    _confidence_bucket,
)
from app.services.sources.registry import SourceRegistry, build_registry
from app.services.sources.taxonomy import SEC_TRANSPORT_LABEL, sec_tier_pair
from app.services.sources.verified_issuer_sources import get_verified_issuer_source

if TYPE_CHECKING:  # reuse lookup is a plain in-memory dict — never a DB session.
    from app.services.extracted_document_service import ReusedDocument

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
    # Private-use readiness PR-E — Italy had NO mapping, so an Italian issuer
    # fell through to the generic region scaffold and its report described its
    # filings in US vocabulary.
    "MI": "borsa_italiana",  # Euronext Milan / Borsa Italiana
    "MIL": "borsa_italiana",
    "BIT": "borsa_italiana",
}
_COUNTRY_TO_REGULATOR: dict[str, str] = {
    "United Kingdom": "uk_fca_nsm",
    "France": "euronext_regulated_info",
    "Netherlands": "euronext_regulated_info",
    "Germany": "deutsche_boerse",
    "Denmark": "nordic_disclosures",
    "Switzerland": "six_swiss",
    "Italy": "borsa_italiana",
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
    # Phase 32A Slice 5: deep primary-document ingestion artifacts (extractions +
    # validated facts + provenance), threaded OUT for a LATER persistence task.
    # Empty on the OFF / shallow path — additive, so existing callers are unchanged.
    primary_document_artifacts: list[PrimaryDocumentArtifact] = Field(
        default_factory=list
    )

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


# A SEC filing-BODY deep extractor resolves a US issuer's filing accessions into
# canonical Archives documents, fetches them through the SSRF-safe fetcher and
# returns bounded ``PrimaryDocumentArtifact``s. Injected only when the master
# ingestion flag is on (Phase 32A Slice 5B.1); when it is None nothing changes.
# Never raises.
SecPrimaryDocumentExtractor = Callable[..., Awaitable[list[PrimaryDocumentArtifact]]]

# Evidence source types for SEC filing-BODY derived items. Deliberately distinct
# from ``company_filing`` (the metadata item) so a body excerpt is never mistaken
# for filing metadata, and from the ``company_ir_*`` types so issuer-site and
# EDGAR provenance stay separable.
SEC_DOCUMENT_EXCERPT_TYPE = "sec_filing_excerpt"
SEC_DOCUMENT_FACT_TYPE = "sec_filing_financial_fact"

# Share of the AGGREGATE ingestion budget the SEC filing-body leg may consume for
# a US issuer that also runs the issuer-IR leg. Without this the SEC path could
# spend the whole budget and leave the IR path nothing (both draw on one budget).
_SEC_BUDGET_SHARE = 0.5


def sec_artifacts_to_evidence(
    artifacts: Sequence[PrimaryDocumentArtifact],
    *,
    company: CompanyContext,
    max_items: int,
) -> tuple[list[EvidenceItem], list[SourceGap]]:
    """Turn SEC filing-BODY artifacts into tiered T1 evidence + honest gaps.

    SUPPLEMENT ONLY — this never touches, replaces or re-derives the SEC/XBRL
    structured facts, which remain the authoritative source for every financial
    number. It adds narrative filing-body excerpts and table-validated datapoints
    that previously did not exist at all for US issuers.

    Tiering matches the rest of the SEC path (``sec_tier_pair()``): transport
    ``T2_regulator_or_gov`` (EDGAR served it), content ``T1_primary_filing`` (the
    issuer wrote it). A non-extracted artifact yields ONLY honest gaps — never a
    fabricated excerpt, figure or filing.
    """
    transport_tier, content_tier = sec_tier_pair()
    issuer = company.company_name or company.ticker or "Issuer"
    cap = max(1, max_items)
    items: list[EvidenceItem] = []
    gaps: list[SourceGap] = []

    for doc_idx, artifact in enumerate(artifacts, start=1):
        gaps.extend(artifact.source_gaps)
        extraction = artifact.extraction
        if (
            extraction is None
            or artifact.status != STATUS_EXTRACTED
            or not extraction.has_content
        ):
            continue

        doc_title = artifact.title or "SEC filing"
        url = artifact.source_url
        doc_hash = artifact.content_hash or extraction.content_hash

        for n, exc in enumerate(extraction.excerpts[:cap], start=1):
            items.append(
                build_evidence_item(
                    id=f"SECDOC{doc_idx}X{n}",
                    source_id=SEC_ID,
                    source_name="SEC EDGAR",
                    provider_transport=SEC_TRANSPORT_LABEL,
                    provider_transport_tier=transport_tier,
                    content_source=f"{issuer} {doc_title}".strip(),
                    content_source_tier=content_tier,
                    source_type=SEC_DOCUMENT_EXCERPT_TYPE,
                    title=f"{doc_title} — excerpt",
                    url=url,
                    excerpt=exc.text,
                    language=extraction.language,
                    data_quality="B" if exc.confidence >= 0.75 else "C",
                    confidence=_confidence_bucket(exc.confidence),
                    fields_supported=[exc.evidence_type],
                    provenance=[
                        p
                        for p in (
                            "Extracted from the issuer's own SEC filing body "
                            "(bounded text)",
                            f"page={exc.page_number}" if exc.page_number else "page=unknown",
                            f"section={exc.section}" if exc.section else None,
                            f"method={exc.extraction_method}",
                            f"confidence={exc.confidence:.2f}",
                        )
                        if p
                    ],
                    document_content_hash=doc_hash,
                    warnings=[
                        "Bounded excerpt from the issuer's own SEC filing; not the "
                        "full document. Human review required."
                    ],
                )
            )

        for j, fact in enumerate(
            (
                f
                for f in artifact.validated_facts
                if f.validation_status == VALIDATION_VALIDATED
            ),
            start=1,
        ):
            value_str = fact.value_text or (
                str(fact.value_numeric) if fact.value_numeric is not None else ""
            )
            unit_bits = " ".join(b for b in (fact.scale, fact.currency, fact.unit) if b)
            conf_bucket = _confidence_bucket(fact.confidence)
            items.append(
                build_evidence_item(
                    id=f"SECFACT{doc_idx}_{j}",
                    source_id=SEC_ID,
                    source_name="SEC EDGAR",
                    provider_transport=SEC_TRANSPORT_LABEL,
                    provider_transport_tier=transport_tier,
                    content_source=f"{issuer} {doc_title}".strip(),
                    content_source_tier=content_tier,
                    source_type=SEC_DOCUMENT_FACT_TYPE,
                    title=f"{doc_title}: {fact.label}",
                    url=url,
                    date=fact.period,
                    excerpt=(
                        f"{fact.label} = {value_str}"
                        + (f" ({unit_bits})" if unit_bits else "")
                        + (f" [{fact.period}]" if fact.period else "")
                    ),
                    data_quality="B" if conf_bucket == "high" else "C",
                    confidence=conf_bucket,
                    fields_supported=[fact.label],
                    provenance=[
                        p
                        for p in (
                            "Validated from an issuer SEC filing table "
                            "(stricter grid validation)",
                            f"page={fact.page_number}" if fact.page_number else "page=unknown",
                            f"table={fact.table_location}" if fact.table_location else None,
                            f"method={fact.extraction_method}",
                            f"confidence={fact.confidence:.2f}",
                            "needs_human_review=true",
                        )
                        if p
                    ],
                    document_content_hash=doc_hash,
                    warnings=(
                        [note for note in fact.validation_notes if note]
                        + [
                            "Validated primary fact from a filing body — it "
                            "SUPPLEMENTS, and never replaces, the SEC/XBRL "
                            "structured facts. Human review required."
                        ]
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

    return items, gaps


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


# Phase 32A corrective (Problem A/B, superseded again by the follow-up
# corrective in section 4/5 of the mission that introduced this comment): a
# document's structured facts (loop 2 of ``company_ir._artifact_to_evidence``)
# are always appended AFTER that same document's prose excerpts (loop 1) — so
# within a single "document excerpts + facts" bucket, a stable sort preserved
# that append order and the per-SOURCE generic item cap
# (``source_connector_max_items_per_source``) could evict every fact in favour
# of excerpts that happened to be listed first, even though a validated
# structured fact is strictly the more valuable evidence (it is what makes
# ``structured_financial_fact_count`` truthful). A flat raw-count floor (the
# previous fix) guaranteed a FEW facts survive but not USEFUL CATEGORY
# COVERAGE — 8 valid facts spanning 5 financial categories could still lose 5
# of them to a floor sized for "some facts survive" rather than "diverse
# categories survive". ``_prioritize_ir_items`` now RESERVES a bounded,
# CATEGORY-DIVERSE set of facts (``company_ir_financial_fact_cap``) that is
# fully INDEPENDENT of — and applied BEFORE — the generic per-source item cap,
# so typed financial facts never compete with generic prose for the same
# slots (mission section 5).
def _prioritize_ir_items(
    items: list[EvidenceItem], *, financial_fact_cap: int
) -> tuple[list[EvidenceItem], list[EvidenceItem]]:
    """Split ``items`` into ``(reserved_facts, rest)``.

    ``reserved_facts`` — up to ``financial_fact_cap`` structured facts,
    selected to maximise DISTINCT financial-category coverage (see
    ``financial_fact_categories.select_category_diverse``) rather than a
    blind raw-count floor or raw list-order priority; the caller must add
    these WITHOUT subjecting them to the generic per-source item cap.

    ``rest`` — every other item (excerpts / annual-report link / profile
    metadata), in the SAME bucket order used before this corrective
    (document excerpts + any non-reserved facts first, then the
    annual-report link, then everything else) — the caller still applies the
    generic per-source cap to THIS list only.
    """
    ordered_facts = [
        it
        for it in items
        if it.source_type in _DOCUMENT_SOURCE_TYPES and it.primary_fact is not None
    ]
    # Phase 32A corrective (LVMH H1 2026) — within each diversity key (same
    # field/scope), the MORE RECENT period must win the round-robin's first
    # slot; without this, a comparison-period fact that merely happened to
    # be appended earlier (e.g. an earlier fiscal year sorts first in the
    # upstream validator's own deterministic ordering) could permanently
    # displace the CURRENT period from this cap-bounded reservation — see
    # ``primary_fact_period_rank`` for the full incident. Both callers of
    # ``select_category_diverse`` over financial facts (this one and
    # ``llm.evidence_budget._apply_category_budget``) must apply the same
    # pre-sort so neither reintroduces the bug the other already fixed.
    ordered_facts = sorted(
        ordered_facts, key=lambda it: primary_fact_period_rank(it.primary_fact)
    )
    reserved_facts = select_category_diverse(
        ordered_facts,
        cap=financial_fact_cap,
        diversity_key_of=lambda it: financial_fact_diversity_key(
            primary_fact_field(it.primary_fact), it.scope
        ),
    )
    reserved_ids = {id(it) for it in reserved_facts}

    def bucket(it: EvidenceItem) -> int:
        if it.source_type in _DOCUMENT_SOURCE_TYPES:
            return 0
        if it.source_type == "company_ir_annual_report":
            return 1
        return 2

    rest = sorted((it for it in items if id(it) not in reserved_ids), key=bucket)
    return reserved_facts, rest


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


async def _safe_sec_document_artifacts(
    extractor: SecPrimaryDocumentExtractor,
    *,
    company: CompanyContext,
    filings: list[dict] | None,
    cfg: Settings,
    budget_seconds: float | None = None,
) -> list[PrimaryDocumentArtifact]:
    """Run the SEC filing-body extractor without letting it break a report.

    The extractor already degrades every failure to an honest artifact; this is
    belt-and-braces so an unexpected error yields no evidence rather than a failed
    run. Only the exception TYPE NAME could ever be surfaced — never its message.
    """
    issuer_context = IssuerContext(
        company_name=company.company_name,
        ticker=company.ticker,
    )
    try:
        artifacts = await extractor(
            company.cik,
            list(filings or []),
            cfg=cfg,
            issuer_context=issuer_context,
            budget_seconds=budget_seconds,
        )
    except Exception:  # noqa: BLE001 - SEC body ingestion never breaks a report
        return []
    return list(artifacts or [])


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
    primary_document_extractor: PrimaryDocumentDeepExtractor | None = None,
    sec_primary_document_extractor: SecPrimaryDocumentExtractor | None = None,
    primary_document_reuse: dict[str, ReusedDocument] | None = None,
    ocr_provider: OcrProvider | None = None,
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
    default set runs. ``primary_document_reuse`` (Phase 32A Slice 5, 3c-iii) is an
    OPTIONAL in-memory lookup (NOT a DB session) keyed by canonical URL: a candidate
    document already present is rebuilt from persisted excerpts + facts and REUSED
    (no re-fetch/re-extract). None / empty ⇒ every candidate is fetched as before.
    ``ocr_provider`` (Phase 32A Slice 5B.2) enables a bounded real-OCR fallback
    for a scanned (no-text) issuer-IR document; None ⇒ byte-identical to
    Slice 5B.1 (OCR never attempted). Scoped to the issuer-IR leg only — the
    SEC filing-body leg never triggers OCR (EDGAR filings are native text).
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
    primary_document_artifacts: list[PrimaryDocumentArtifact] = []
    # Populated only inside the company-IR block below; declared here so the
    # later "Non-US primary-disclosure context" gap check (which reads it even
    # when company-IR wasn't requested at all) never hits an UnboundLocalError.
    ir_items: list[EvidenceItem] = []

    # Phase 32A Slice 5B.1 — ``primary_document_ingestion_budget_seconds`` is an
    # AGGREGATE wall budget for the WHOLE request, so the SEC filing-body path and
    # the issuer-IR path SHARE it rather than each claiming a full one. Without
    # this, adding SEC bodies would double the worst-case ingestion time and push
    # ingestion + the ~150s council past the ~230s gateway timeout.
    ingestion_started = time.monotonic()
    ingestion_budget = float(
        max(0, getattr(cfg, "primary_document_ingestion_budget_seconds", 0) or 0)
    )

    def _remaining_budget() -> float:
        """Seconds left of the shared aggregate budget. ``0.0`` means EXHAUSTED.

        Callers must pass this through :func:`_budget_or_unbounded` (never
        ``or None``) so a genuinely spent budget is not mistaken for "no budget
        configured" and turned into an unbounded run.
        """
        return max(0.0, ingestion_budget - (time.monotonic() - ingestion_started))

    # Phase 32A Slice 5B.2 — cross-document OCR usage tracker for THIS request,
    # shared by every document the issuer-IR leg attempts OCR on. Constructed
    # ONLY when an OCR provider was injected (byte-identical / None when not).
    # ``deadline`` is the SAME aggregate ingestion-budget expiry as
    # ``_remaining_budget()`` above, so an OCR call started late in the 60s
    # window is clamped to what is ACTUALLY left, never given the full
    # per-call timeout regardless of elapsed time.
    ocr_budget = (
        OcrBudget(
            max_documents_per_run=cfg.primary_document_max_ocr_documents_per_run,
            deadline=(ingestion_started + ingestion_budget) if ingestion_budget > 0 else None,
            clock=time.monotonic,
        )
        if ocr_provider is not None
        else None
    )

    def _budget_or_unbounded(seconds: float) -> float | None:
        """``None`` ONLY when no budget is configured at all.

        With a budget configured, ``0.0`` is passed through as ``0.0`` — an
        exhausted budget, which stops further fetches — rather than collapsing to
        the ``None`` sentinel that means "unbounded".
        """
        return None if ingestion_budget <= 0 else seconds

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

        # Phase 32A Slice 5B.1 — SEC filing-BODY ingestion. Until this slice the
        # SEC path read only structured JSON, so a US issuer produced ZERO primary
        # document candidates and every SEC result carried a
        # ``primary_filing_unavailable`` gap. The injected extractor resolves each
        # already-known accession to its canonical Archives body document and runs
        # the SAME bounded extraction + stricter validation as the issuer-IR path.
        #
        # SUPPLEMENT ONLY: SEC/XBRL structured facts are untouched here and remain
        # authoritative for every financial number.
        #
        # Runs off the ALREADY-FETCHED deterministic filing list (the report /
        # council path). A live ``filings_fetcher`` (preview path) is deliberately
        # not re-invoked — one bounded fetch per request, never two. Extractor not
        # injected (master flag off) ⇒ this block is inert and nothing changes.
        if (
            sec_primary_document_extractor is not None
            and filings
            and is_sec_eligible(company.exchange)
        ):
            # The SEC leg gets AT MOST half of what is left, so a slow EDGAR path
            # can never starve the issuer-IR path that runs after it (both draw on
            # the same aggregate budget).
            sec_remaining = _remaining_budget()
            sec_artifacts = await _safe_sec_document_artifacts(
                sec_primary_document_extractor,
                company=company,
                filings=filings,
                cfg=cfg,
                budget_seconds=_budget_or_unbounded(
                    min(sec_remaining, ingestion_budget * _SEC_BUDGET_SHARE)
                ),
            )
            if sec_artifacts:
                sec_items, sec_gaps = sec_artifacts_to_evidence(
                    sec_artifacts, company=company, max_items=max_items
                )
                items.extend(sec_items[:max_items])
                gaps.extend(sec_gaps)
                primary_document_artifacts.extend(sec_artifacts)

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
            primary_document_extractor=primary_document_extractor,
            primary_document_reuse=primary_document_reuse,
            ocr_provider=ocr_provider,
            ocr_budget=ocr_budget,
            max_docs_per_issuer=cfg.primary_document_max_docs_per_issuer,
            cfg=cfg,
            # Slice 5B.1: what is LEFT of the shared aggregate budget after the SEC
            # filing-body path. With no SEC path this is effectively the full
            # budget, so Slice 5A behaviour is unchanged.
            ingestion_budget_seconds=(
                _budget_or_unbounded(_remaining_budget())
                if primary_document_extractor is not None
                else None
            ),
        )
        for method in (ir.search_company, ir.fetch_filings, ir.fetch_events):
            res = await ir.call_safe(method, company, query)
            ir_items.extend(res.evidence_items)
            gaps.extend(res.source_gaps)
            warnings.extend(res.warnings)
        # Prioritise extracted document excerpts/facts so they survive the
        # per-source cap (Phase 29B.2). Phase 32A corrective: structured
        # financial facts get a CATEGORY-DIVERSE reserved budget
        # (``company_ir_financial_fact_cap``) that bypasses the generic cap
        # entirely — only the remaining excerpt/link/metadata items are
        # bounded by ``max_items`` (mission section 5).
        reserved_ir_facts, rest_ir_items = _prioritize_ir_items(
            _dedup_evidence(ir_items),
            financial_fact_cap=cfg.company_ir_financial_fact_cap,
        )
        items.extend(reserved_ir_facts + rest_ir_items[:max_items])
        # Phase 32A Slice 5: thread the deep ingestion artifacts OUT for a later
        # persistence task (empty unless the deep extractor was injected).
        primary_document_artifacts.extend(ir.collected_primary_document_artifacts)

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
        # Problem F follow-up (found during live staging acceptance): this gap
        # used to fire from the issuer's country alone (``verified.country in
        # _LOCAL_LANGUAGE_COUNTRIES``), independent of what language the
        # actually-collected IR evidence was really detected as — so an
        # English document from a French/Swiss-domiciled issuer still got an
        # honest-sounding "translation pending" gap attached. Now gated on the
        # SAME content-based ``requires_translation`` flag already carried on
        # the real collected ``ir_items`` (fixed to be content-first, domicile
        # only as a weak fallback) — never on domicile alone.
        if verified.country in _LOCAL_LANGUAGE_COUNTRIES and any(
            getattr(it, "requires_translation", False) for it in ir_items
        ):
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
        evidence_items=items,
        source_gaps=gaps,
        warnings=warnings,
        primary_document_artifacts=primary_document_artifacts,
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
    "SEC_DOCUMENT_EXCERPT_TYPE",
    "SEC_DOCUMENT_FACT_TYPE",
    "SecPrimaryDocumentExtractor",
    "collect_company_source_evidence",
    "sec_artifacts_to_evidence",
    "sec_filings_from_catalyst",
    "press_items_from_catalyst",
    "regulator_connector_for",
    "SEC_ID",
    "COMPANY_IR_ID",
    "REGULATOR_REFERENCE_IDS",
    "LOCAL_LANGUAGE_REFERENCE_IDS",
    "LOCAL_LANGUAGE_PRESS_ID",
]
