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
    non-US issuers instead surface honest scaffold gaps for their home-regulator
    connectors (SEDAR+, ASX, UK FCA NSM, Euronext, Deutsche Börse, Nordic).
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
    is_sec_eligible,
    is_us_exchange,
    region_for_exchange,
)
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.company_ir import CompanyIrConnector, PressFetcher
from app.services.sources.connectors.sec_edgar import FilingsFetcher, SecEdgarConnector
from app.services.sources.evidence import EvidenceItem
from app.services.sources.gaps import SourceGap
from app.services.sources.registry import SourceRegistry, build_registry

# Source ids whose connectors can produce live company evidence in this phase.
SEC_ID = "sec_edgar"
COMPANY_IR_ID = "company_ir"


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


def _relevant_scaffold_ids(
    registry: SourceRegistry,
    company: CompanyContext,
    requested: Sequence[str] | None,
) -> list[str]:
    """Scaffold source ids whose honest gaps are relevant to this issuer.

    - Explicit request: only the requested ids that are actually scaffolded.
    - Default: none for US / SEC-eligible issuers; for a non-US issuer, the
      scaffolds whose region matches the issuer's venue, falling back to *all*
      scaffolds when the region can't be resolved (honest over-disclosure).
    """
    scaffold_ids = [s.source_id for s in registry.scaffolded_sources()]
    if requested is not None:
        return [sid for sid in requested if sid in scaffold_ids]

    # US / SEC-eligible issuers need no non-US regulator scaffolds.
    if is_us_exchange(company.exchange) or is_sec_eligible(company.exchange):
        return []

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
    cfg: Settings | None = None,
    registry: SourceRegistry | None = None,
) -> CompanySourceEvidence:
    """Collect connector evidence + gaps for one company.

    ``filings`` / ``press_items`` are already-fetched deterministic data (report
    path). ``filings_fetcher`` / ``press_fetcher`` are live fetchers (preview
    path) and take precedence when supplied. ``source_ids`` restricts which
    connectors run; when ``None`` a sensible default set runs.
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
    if want(COMPANY_IR_ID):
        fetcher = press_fetcher or (
            _static_fetcher(press_items) if press_items is not None else None
        )
        ir = CompanyIrConnector(press_fetcher=fetcher)
        res = await ir.call_safe(ir.fetch_events, company, query)
        items.extend(res.evidence_items[:max_items])
        gaps.extend(res.source_gaps)
        warnings.extend(res.warnings)

    # -- Regulated-disclosure scaffolds (honest gaps only) -----------------
    for sid in _relevant_scaffold_ids(registry, company, requested):
        conn = registry.connectors().get(sid)
        if conn is None:
            continue
        res = await conn.call_safe(conn.fetch_filings, company, query)
        # Scaffolds never yield evidence — collect only their gaps/warnings.
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
    "SEC_ID",
    "COMPANY_IR_ID",
]
