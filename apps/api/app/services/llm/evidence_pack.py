"""
Evidence Pack Builder — Phase 28A.

Turns the deterministic report material for ONE company into a bounded, cited
evidence pack. The council may read nothing else. Design constraints:

  - Bounded: at most ``max_items`` evidence items (config-capped).
  - Excerpts only — never a whole filing. Long text is truncated.
  - Stable, unique ids: E1, E2, ... in build order.
  - Transport vs content tier: SEC EDGAR is a *transport* (T2_regulator_or_gov);
    a company filing pulled through it is *content* T1_primary_filing. Company
    press releases are T1_primary_company_source. Both tiers are recorded.

Inputs are plain dicts (no DB/ORM coupling), so the builder is trivially
unit-testable and works from whatever a given final-report entry point has: the
assembled ``report_content`` always; the richer ``company_snapshot``,
``catalyst_discovery`` and appendix ``source_rows`` when available.
"""

from __future__ import annotations

from typing import Any

from app.services.llm.schemas import (
    EVIDENCE_PACK_VERSION,
    TIER_T1_PRIMARY_COMPANY_SOURCE,
    TIER_T1_PRIMARY_FILING,
    TIER_T2_REGULATOR_OR_GOV,
    TIER_T6_MODEL_ESTIMATE,
    EvidenceCompany,
    EvidenceItem,
    EvidencePack,
    SourcePolicy,
)
from app.services.sources.redaction import strip_url_secrets

_EXCERPT_MAX = 280
_SEC_TRANSPORT = "SEC EDGAR / data.sec.gov"

# Aggregators / model estimates are allowed as evidence but must be labelled so
# agents down-weight them. Model estimates are never treated as primary facts.
_ALLOWED_TIERS = [
    TIER_T1_PRIMARY_FILING,
    TIER_T1_PRIMARY_COMPANY_SOURCE,
    TIER_T2_REGULATOR_OR_GOV,
    "T3_industry_specialist",
    "T4_quality_media",
    "T5_api_aggregator",
    TIER_T6_MODEL_ESTIMATE,
]


def _node_value(node: Any) -> Any:
    """Unwrap a ``{"value": ...}`` datapoint node, else return the value as-is."""
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    return node


def _excerpt(text: Any, limit: int = _EXCERPT_MAX) -> str | None:
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _looks_like_sec(source_tier: str | None, source_type: str | None, url: str | None) -> bool:
    st = (source_type or "").lower()
    u = (url or "").lower()
    return (
        source_tier == TIER_T2_REGULATOR_OR_GOV
        or "sec.gov" in u
        or "edgar" in u
        or "filing" in st
    )


def _tier_pair(
    source_tier: str | None,
    source_type: str | None,
    url: str | None,
) -> tuple[str, str, str | None]:
    """Return (transport_tier, content_tier, provider_transport).

    Encodes the transport-vs-content distinction the task requires.
    """
    if _looks_like_sec(source_tier, source_type, url):
        return TIER_T2_REGULATOR_OR_GOV, TIER_T1_PRIMARY_FILING, _SEC_TRANSPORT
    st = (source_type or "").lower()
    if source_tier == TIER_T1_PRIMARY_COMPANY_SOURCE or "press" in st or "issuer" in st:
        return (
            TIER_T1_PRIMARY_COMPANY_SOURCE,
            TIER_T1_PRIMARY_COMPANY_SOURCE,
            "Company primary source",
        )
    tier = source_tier or TIER_T6_MODEL_ESTIMATE
    return tier, tier, None


class _Builder:
    def __init__(self, max_items: int) -> None:
        self.items: list[EvidenceItem] = []
        self.max_items = max_items
        self._n = 0

    @property
    def full(self) -> bool:
        return len(self.items) >= self.max_items

    def add(
        self,
        *,
        source_tier: str,
        source_type: str,
        title: str | None = None,
        url: str | None = None,
        date: str | None = None,
        excerpt: Any = None,
        data_quality: str | None = None,
        fields_supported: list[str] | None = None,
        transport_tier: str | None = None,
        content_tier: str | None = None,
        provider_transport: str | None = None,
    ) -> bool:
        if self.full:
            return False
        if transport_tier is None or content_tier is None:
            transport_tier, content_tier, provider = _tier_pair(source_tier, source_type, url)
            provider_transport = provider_transport or provider
        self._n += 1
        # Phase 29A: strip any credential-bearing query params before storing a
        # URL, so a ``?api_token=…`` can never survive into an evidence pack.
        self.items.append(
            EvidenceItem(
                id=f"E{self._n}",
                source_tier=content_tier or source_tier,
                source_type=source_type,
                provider_transport=provider_transport,
                transport_tier=transport_tier,
                content_tier=content_tier,
                title=title,
                url=strip_url_secrets(url),
                date=date,
                excerpt=_excerpt(excerpt),
                data_quality=data_quality,
                fields_supported=fields_supported or [],
            )
        )
        return True


def _company_from(
    report_content: dict[str, Any], company_snapshot: dict[str, Any] | None
) -> EvidenceCompany:
    ident = report_content.get("company_identity") or {}
    snap_id = (company_snapshot or {}).get("company_identity") or {}
    snap_profile = (company_snapshot or {}).get("profile") or {}
    exec_summary = report_content.get("executive_summary") or {}

    def pick(*vals: Any) -> Any:
        for v in vals:
            if v not in (None, ""):
                return v
        return None

    return EvidenceCompany(
        ticker=pick(
            _node_value(ident.get("ticker")),
            snap_id.get("ticker"),
            exec_summary.get("ticker"),
        ),
        exchange=pick(_node_value(ident.get("exchange")), snap_id.get("exchange")),
        company_name=pick(
            _node_value(ident.get("legal_name")),
            snap_id.get("legal_name"),
            exec_summary.get("company_name"),
        ),
        legal_name=pick(_node_value(ident.get("legal_name")), snap_id.get("legal_name")),
        country=pick(_node_value(ident.get("country_domicile")), snap_id.get("country_domicile")),
        sector=pick(_node_value(ident.get("sector")), snap_profile.get("sector")),
        industry=pick(snap_profile.get("industry")),
    )


def _add_sec_fundamentals(builder: _Builder, company_snapshot: dict[str, Any] | None) -> None:
    """Add SEC filing facts as T1 content pulled through the T2 EDGAR transport."""
    if not company_snapshot:
        return
    fs = company_snapshot.get("fundamentals_summary") or {}
    if not isinstance(fs, dict) or not fs:
        return
    # Only the metric fields carry filing facts; the rest is filing metadata.
    metric_keys = [
        k
        for k, v in fs.items()
        if isinstance(v, (int, float))
        and not k.endswith("_year")
        and k not in {"source_tier"}
    ]
    if not metric_keys:
        return
    form = fs.get("form_type") or "SEC filing"
    fiscal = fs.get("fiscal_year")
    period = fs.get("fiscal_period")
    excerpt_bits = [f"{k}={fs.get(k)}" for k in metric_keys[:8]]
    builder.add(
        source_tier=TIER_T1_PRIMARY_FILING,
        source_type="company_filing",
        transport_tier=TIER_T2_REGULATOR_OR_GOV,
        content_tier=TIER_T1_PRIMARY_FILING,
        provider_transport=_SEC_TRANSPORT,
        title=f"{form} filing facts (FY{fiscal} {period})".strip(),
        url=None,
        date=str(fs.get("filed_date")) if fs.get("filed_date") else None,
        excerpt="; ".join(excerpt_bits),
        data_quality=fs.get("data_quality"),
        fields_supported=metric_keys,
    )


def _add_financial_snapshot(builder: _Builder, report_content: dict[str, Any]) -> None:
    section = report_content.get("financial_snapshot") or {}
    if not isinstance(section, dict):
        return
    tier = section.get("source_tier") or TIER_T6_MODEL_ESTIMATE
    for key in (
        "latest_close",
        "market_cap_usd_m",
        "ebitda_ttm_usd_m",
        "revenue_ttm_usd_m",
        "pe_ratio",
    ):
        node = section.get(key)
        if not isinstance(node, dict):
            continue
        val = node.get("value")
        if val in (None, ""):
            continue
        node_tier = node.get("source_tier") or tier
        # Snapshot-derived datapoints are not raw filings we retrieved through a
        # transport, so do not assert an SEC/EDGAR transport for them — record
        # the node's own tier as both transport and content.
        builder.add(
            source_tier=node_tier,
            source_type=node.get("source") or "financial_snapshot",
            transport_tier=node_tier,
            content_tier=node_tier,
            title=f"Financial snapshot: {key}",
            excerpt=f"{key}={val} {node.get('unit') or ''}".strip(),
            data_quality=None,
            fields_supported=[key],
        )
        if builder.full:
            return


def _add_sources(builder: _Builder, source_rows: list[dict[str, Any]] | None) -> None:
    for row in source_rows or []:
        if builder.full:
            return
        if not isinstance(row, dict):
            continue
        builder.add(
            source_tier=row.get("source_tier") or TIER_T6_MODEL_ESTIMATE,
            source_type=row.get("source_type") or "source",
            title=row.get("title"),
            url=row.get("url"),
            date=row.get("retrieved_at"),
            excerpt=row.get("source_quote") or row.get("title"),
            data_quality=row.get("data_quality"),
        )


def _add_catalysts(builder: _Builder, catalyst_discovery: dict[str, Any] | None) -> None:
    if not catalyst_discovery:
        return
    events: list[Any] = []
    for key in ("filing_events", "events", "industry_events"):
        events.extend(catalyst_discovery.get(key) or [])
    for e in events:
        if builder.full:
            return
        if not isinstance(e, dict):
            continue
        builder.add(
            source_tier=e.get("source_tier") or TIER_T6_MODEL_ESTIMATE,
            source_type=e.get("source_type") or "catalyst_event",
            title=e.get("headline") or e.get("form_type") or "Catalyst event",
            url=e.get("source_url"),
            date=e.get("event_date"),
            excerpt=e.get("summary") or e.get("headline"),
            data_quality=e.get("evidence_strength"),
            fields_supported=["catalyst"],
        )


def _known_gaps(report_content: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    mi = report_content.get("missing_information") or {}
    if isinstance(mi, dict):
        items = mi.get("missing_items") or mi.get("items") or []
        if isinstance(items, list):
            for it in items[:15]:
                if isinstance(it, dict):
                    gaps.append(str(it.get("field") or it.get("item") or it))
                else:
                    gaps.append(str(it))
    das = report_content.get("data_availability_summary") or {}
    if isinstance(das, dict):
        mf = _node_value(das.get("missing_fields")) or []
        if isinstance(mf, list):
            gaps.extend(str(x) for x in mf[:15])
    # De-dup, keep order.
    seen: dict[str, None] = {}
    for g in gaps:
        seen.setdefault(g, None)
    return list(seen)


def build_evidence_pack(
    *,
    report_content: dict[str, Any],
    company_snapshot: dict[str, Any] | None = None,
    catalyst_discovery: dict[str, Any] | None = None,
    source_rows: list[dict[str, Any]] | None = None,
    max_items: int = 40,
    extra_known_gaps: list[str] | None = None,
) -> EvidencePack:
    """Build a bounded, cited evidence pack for one company.

    ``extra_known_gaps`` (Phase 29A) appends source-framework gaps — e.g. planned
    external connectors whose evidence is not yet sourced — so the source critic
    sees missing coverage as an explicit, honest gap rather than silent absence.
    """
    builder = _Builder(max_items=max(1, max_items))

    # Fall back to the report's own source appendix when explicit rows are not
    # supplied, so the builder works from report_content alone.
    if source_rows is None:
        appendix = report_content.get("source_citation_appendix") or {}
        source_rows = ((appendix.get("sources") or {}).get("value")) or []

    # Order matters: primary filings first, then snapshot metrics, sources,
    # catalysts — so that if the cap truncates, the highest-value evidence stays.
    _add_sec_fundamentals(builder, company_snapshot)
    _add_financial_snapshot(builder, report_content)
    _add_sources(builder, source_rows)
    _add_catalysts(builder, catalyst_discovery)

    is_mock = bool((company_snapshot or {}).get("is_mock")) if company_snapshot else False
    do_not_infer = [
        "Do not infer a valuation, price target, fair value, or upside/downside.",
        "Do not infer a rating or trading action.",
        "Do not treat model estimates (T6) as primary facts.",
    ]
    if is_mock:
        do_not_infer.append("Data is mock/placeholder — treat all values as non-factual.")

    known_gaps = _known_gaps(report_content)
    for g in extra_known_gaps or []:
        if g and g not in known_gaps:
            known_gaps.append(g)

    return EvidencePack(
        evidence_pack_version=EVIDENCE_PACK_VERSION,
        company=_company_from(report_content, company_snapshot),
        source_policy=SourcePolicy(
            allowed_tiers=list(_ALLOWED_TIERS),
            excluded_sources=["uncited_web_search", "social_media", "anonymous_forums"],
        ),
        evidence_items=builder.items,
        known_gaps=known_gaps,
        do_not_infer=do_not_infer,
    )
