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

import re
from typing import Any

from app.schemas.evidence_state import FinancialDataSummary
from app.services.llm.schemas import (
    EVIDENCE_PACK_VERSION,
    TIER_T1_PRIMARY_COMPANY_SOURCE,
    TIER_T1_PRIMARY_FILING,
    TIER_T2_REGULATOR_OR_GOV,
    TIER_T5_API_AGGREGATOR,
    TIER_T6_MODEL_ESTIMATE,
    EvidenceCompany,
    EvidenceItem,
    EvidencePack,
    SourcePolicy,
)
from app.services.sources.fact_scope import parse_scope
from app.services.sources.financial_history import (
    DEFAULT_MAX_PERIODS,
    MIN_PERIODS_FOR_TREND,
    build_financial_history,
)
from app.services.sources.financial_period import parse_period
from app.services.sources.period_state import (
    build_reporting_period_state,
    periods_of,
)
from app.services.sources.redaction import strip_url_secrets

_EXCERPT_MAX = 280
_SEC_TRANSPORT = "SEC EDGAR / data.sec.gov"

# Phase 32A Slice 2 — tier-split SEC/XBRL fundamentals. The keys below come from
# ``company_snapshot["fundamentals_summary"]`` (SEC path, snapshot_builder). Every
# statement fact is ANNUAL (10-K / 20-F) — NEVER TTM — so no value here is mapped
# into any ``*_ttm`` field. Derived ratios/margins/growth are model-computed and
# labelled T6 (never T1/T2). Price/market metrics come from
# ``market_metrics_summary`` (per-field ``source_tiers``): market cap / EV / P/E
# are T6 (derived), latest close / 52-week range are T5 (price aggregator).
_SEC_INCOME_KEYS = (
    "revenue_usd_m",
    "gross_profit_usd_m",
    "operating_income_usd_m",
    "net_income_usd_m",
    "eps_basic",
    "eps_diluted",
)
_SEC_CASH_FLOW_KEYS = (
    "operating_cash_flow_usd_m",
    "capital_expenditures_usd_m",
)
_SEC_BALANCE_KEYS = (
    "total_assets_usd_m",
    "total_liabilities_usd_m",
    "shareholders_equity_usd_m",
    "cash_and_equivalents_usd_m",
    "short_term_debt_usd_m",
    "long_term_debt_usd_m",
    "shares_outstanding_mln",
)
_SEC_DERIVED_KEYS = (
    "gross_margin_pct",
    "operating_margin_pct",
    "net_margin_pct",
    "free_cash_flow_usd_m",
    "free_cash_flow_margin_pct",
    "return_on_equity_pct",
    "total_debt_usd_m",
    "debt_to_equity",
    "revenue_yoy_growth_pct",
    "net_income_yoy_growth_pct",
    "free_cash_flow_yoy_growth_pct",
)
# (market_metrics_summary key, fundamentals_summary fallback key)
_MARKET_METRIC_KEYS = (
    ("market_cap_mln", "market_cap_usd_m"),
    ("enterprise_value_mln", "enterprise_value_usd_m"),
    ("pe_ratio", "pe_ratio"),
)
_PRICE_METRIC_KEYS = (
    ("latest_close", "latest_close"),
    ("week52_high", "52_week_high"),
    ("week52_low", "52_week_low"),
)
_DERIVED_QUALITY = "C_inferred"

# Conservative, deterministic derivative-instrument (leveraged/inverse single-
# stock ETF) noise detector for catalyst/news items — e.g. an ``AAPD`` / ``AAPU``
# story surfacing under an ``AAPL`` analysis. NOT a news-platform rewrite: an item
# is only down-ranked when BOTH a near-ticker symbol AND a leverage/inverse cue
# are present in its text.
_LEVERAGE_CUES = (
    "leverag",
    "inverse",
    "2x",
    "3x",
    "-1x",
    "1.5x",
    "bull ",
    "bear ",
    "daily ",
    "single-stock",
    "single stock",
)
_SYMBOL_RE = re.compile(r"[(\$]([A-Z]{2,6})\)?")

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


#: How many historical SERIES lines the council pack may carry. Each is one
#: dense line covering up to five periods, so this is a token bound, not a
#: research bound — the full history stays available to the report layer.
DEFAULT_MAX_HISTORY_SERIES = 8


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
        relevance_level: str | None = None,
        source_id: str | None = None,
        primary_fact: dict[str, Any] | None = None,
        provenance: list[str] | None = None,
        document_content_hash: str | None = None,
        scope: str | None = None,
        period: str | None = None,
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
                relevance_level=relevance_level,
                scope=scope,
                period=period,
                # Phase 32A Slice 3: runtime-only persistence carriers (excluded
                # from serialization) — preserve upstream provenance for persist time.
                source_id=source_id,
                primary_fact=primary_fact,
                provenance=provenance or [],
                # Phase 32A Slice 5 (3c-ii): raw-bytes document identity for a deep
                # primary-document item (excluded from serialization). Present only
                # for deep-ingested items ⇒ the dark path carries None.
                document_content_hash=document_content_hash,
            )
        )
        return True

    def add_framework_item(self, item: Any) -> bool:
        """Add a Phase 29B framework ``EvidenceItem`` (from a connector).

        Maps its transport-vs-content tiers straight through so the connector's
        provenance is preserved. The item's URL was already secret-stripped by
        the framework model; ``add`` strips again defensively.

        Phase 32A Slice 3: also carries the framework item's stable ``source_id``,
        structured ``primary_fact`` (PrimaryFactRef, dumped) and ``provenance``
        onto the (runtime-only, excluded) council-item carriers so a cited E# can
        resolve to a canonical source at persist time. These never reach the LLM
        prompt (excluded from serialization).
        """
        pf = getattr(item, "primary_fact", None)
        pf_dump = (
            pf.model_dump(mode="json")
            if pf is not None and hasattr(pf, "model_dump")
            else None
        )
        # Semantic-grounding fields: the item's own ``scope`` (set generically
        # from document structure, see ``primary_document_extractor._infer_scope``)
        # and ``period``, taken from the structured fact when present (a fact's
        # own period is the more precise signal; the item itself carries no
        # separate period field).
        scope = getattr(item, "scope", None)
        period = getattr(pf, "period", None) if pf is not None else None
        return self.add(
            source_tier=item.content_source_tier,
            source_type=item.source_type or "source",
            transport_tier=item.provider_transport_tier or item.content_source_tier,
            content_tier=item.content_source_tier,
            provider_transport=item.provider_transport,
            title=item.title,
            url=item.url,
            date=item.date,
            excerpt=item.excerpt,
            data_quality=item.data_quality,
            fields_supported=list(item.fields_supported),
            source_id=getattr(item, "source_id", None),
            primary_fact=pf_dump,
            provenance=list(getattr(item, "provenance", None) or []),
            document_content_hash=getattr(item, "document_content_hash", None),
            scope=scope,
            period=period,
        )


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


def _present_pairs(fs: dict[str, Any], keys: tuple[str, ...]) -> list[tuple[str, Any]]:
    """Return ``(key, value)`` for numeric keys actually present (no fabrication)."""
    out: list[tuple[str, Any]] = []
    for k in keys:
        v = fs.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append((k, v))
    return out


def _period_label(fs: dict[str, Any]) -> str:
    """Honest fiscal label — honours ``period_basis`` (never assumes annual/TTM)."""
    form = fs.get("form_type") or "SEC filing"
    basis = str(fs.get("period_basis") or "annual").upper()
    fiscal = fs.get("fiscal_year")
    if fiscal:
        return f"FY{fiscal} {basis} {form}".strip()
    return f"{basis} {form}".strip()


def _add_sec_fundamentals(
    builder: _Builder,
    company_snapshot: dict[str, Any] | None,
    *,
    tier_split: bool = False,
) -> None:
    """Add SEC filing facts as T1 content pulled through the T2 EDGAR transport.

    When ``tier_split`` is off (default / dark path) this emits the ORIGINAL
    single wholesale item, byte-for-byte as before. When on (Phase 32A Slice 2)
    it emits MULTIPLE correctly-tiered items: SEC statement facts stay T1/T2, but
    model-derived ratios/margins/growth are labelled T6 (DERIVED) and price /
    market metrics are sourced at their true T5 / T6 tier, so the council never
    sees a derived or price value stamped as a primary filing.
    """
    if not company_snapshot:
        return
    fs = company_snapshot.get("fundamentals_summary") or {}
    if not isinstance(fs, dict) or not fs:
        return

    if not tier_split:
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
        return

    # ── Tier-split path (Phase 32A Slice 2) ──────────────────────────────
    label = _period_label(fs)
    filed = str(fs.get("filed_date")) if fs.get("filed_date") else None
    quality = fs.get("data_quality") or "B_single_credible"

    def _add_statement(keys: tuple[str, ...], statement: str) -> None:
        pairs = _present_pairs(fs, keys)
        if not pairs:
            return
        builder.add(
            source_tier=TIER_T1_PRIMARY_FILING,
            source_type="sec_financial_statement",
            transport_tier=TIER_T2_REGULATOR_OR_GOV,
            content_tier=TIER_T1_PRIMARY_FILING,
            provider_transport=_SEC_TRANSPORT,
            title=f"{label} — {statement}",
            url=None,
            date=filed,
            excerpt="; ".join(f"{k}={v}" for k, v in pairs),
            data_quality=quality,
            fields_supported=[k for k, _ in pairs],
        )

    _add_statement(_SEC_INCOME_KEYS, "income statement")
    _add_statement(_SEC_CASH_FLOW_KEYS, "cash flow statement")
    _add_statement(_SEC_BALANCE_KEYS, "balance sheet")

    # Derived metrics — computed, not reported. NEVER T1/T2.
    derived = _present_pairs(fs, _SEC_DERIVED_KEYS)
    if derived:
        builder.add(
            source_tier=TIER_T6_MODEL_ESTIMATE,
            source_type="derived_financial_metric",
            transport_tier=TIER_T6_MODEL_ESTIMATE,
            content_tier=TIER_T6_MODEL_ESTIMATE,
            provider_transport=None,
            title=f"{label} — derived metrics (DERIVED, computed not reported)",
            url=None,
            date=filed,
            excerpt="; ".join(f"{k}={v}" for k, v in derived),
            data_quality=_DERIVED_QUALITY,
            fields_supported=[k for k, _ in derived],
        )

    _add_market_and_price(builder, company_snapshot, fs)


def _add_market_and_price(
    builder: _Builder,
    company_snapshot: dict[str, Any],
    fs: dict[str, Any],
) -> None:
    """Emit a T6 market-metrics item + a T5 price item at their TRUE tiers."""
    raw_mm = company_snapshot.get("market_metrics_summary")
    mm: dict[str, Any] = raw_mm if isinstance(raw_mm, dict) else {}
    raw_tiers = mm.get("source_tiers")
    tiers: dict[str, Any] = raw_tiers if isinstance(raw_tiers, dict) else {}

    def _collect(pairs_def: tuple[tuple[str, str], ...]) -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        for mm_key, fs_key in pairs_def:
            v = mm.get(mm_key)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                v = fs.get(fs_key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append((mm_key, v))
        return out

    market = _collect(_MARKET_METRIC_KEYS)
    if market:
        builder.add(
            source_tier=TIER_T6_MODEL_ESTIMATE,
            source_type="market_metric",
            transport_tier=TIER_T6_MODEL_ESTIMATE,
            content_tier=TIER_T6_MODEL_ESTIMATE,
            provider_transport=None,
            title="Market metrics (DERIVED — market cap / EV / P/E)",
            excerpt="; ".join(f"{k}={v}" for k, v in market),
            data_quality=_DERIVED_QUALITY,
            fields_supported=[k for k, _ in market],
        )

    price = _collect(_PRICE_METRIC_KEYS)
    if price:
        # All price fields are T5; honour a per-field tier if one is recorded.
        price_tier = tiers.get("latest_close") or TIER_T5_API_AGGREGATOR
        builder.add(
            source_tier=price_tier,
            source_type="price_metric",
            transport_tier=price_tier,
            content_tier=price_tier,
            provider_transport=None,
            title="Price snapshot (latest close / 52-week range)",
            excerpt="; ".join(f"{k}={v}" for k, v in price),
            data_quality=None,
            fields_supported=[k for k, _ in price],
        )


def _add_financial_context(
    builder: _Builder,
    report_content: dict[str, Any],
    company_snapshot: dict[str, Any] | None,
) -> None:
    """Surface ``financial_data_summary`` / ``trend_signal_summary`` as bounded,
    honestly-tiered items — ONLY when actually populated (never fabricated)."""
    snap = company_snapshot or {}

    fds = report_content.get("financial_data_summary") or snap.get("financial_data_summary")
    if isinstance(fds, dict) and fds:
        context = (
            fds.get("financial_context_summary")
            or fds.get("summary")
            or ", ".join(
                (FinancialDataSummary.from_payload(fds) or FinancialDataSummary())
                .available_fields[:8]
            )
        )
        if context:
            builder.add(
                source_tier=TIER_T6_MODEL_ESTIMATE,
                source_type="financial_data_summary",
                transport_tier=TIER_T6_MODEL_ESTIMATE,
                content_tier=TIER_T6_MODEL_ESTIMATE,
                provider_transport=None,
                title="Financial data availability summary (re-presentation)",
                excerpt=context,
                data_quality=None,
                fields_supported=["financial_data_summary"],
            )

    trend = snap.get("trend_signal_summary")
    if isinstance(trend, dict) and trend.get("momentum_label"):
        bits = [f"momentum={trend.get('momentum_label')}"]
        for k in ("return_1m", "return_3m", "return_6m", "pct_above_ma200"):
            v = trend.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                bits.append(f"{k}={v}")
        builder.add(
            source_tier=trend.get("source_tier") or TIER_T6_MODEL_ESTIMATE,
            source_type="trend_signal",
            transport_tier=TIER_T6_MODEL_ESTIMATE,
            content_tier=TIER_T6_MODEL_ESTIMATE,
            provider_transport=None,
            title="Trend signals (DERIVED momentum — T6, from T5 price)",
            excerpt="; ".join(bits),
            data_quality=None,
            fields_supported=["trend_signal"],
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


def _looks_like_derivative_of(text: str | None, analyzed_ticker: str | None) -> bool:
    """True when ``text`` looks like a leveraged/inverse derivative-instrument
    story about a NEAR-ticker symbol (e.g. ``AAPD`` / ``AAPU`` under ``AAPL``).

    Deterministic + conservative: requires BOTH a leverage/inverse cue AND a
    parenthesised/``$``-prefixed symbol that shares the analysed ticker's prefix
    but is not the ticker itself. Avoids reclassifying genuine company news.
    """
    if not text or not analyzed_ticker or len(analyzed_ticker) < 3:
        return False
    low = text.lower()
    if not any(cue in low for cue in _LEVERAGE_CUES):
        return False
    prefix = analyzed_ticker[:3]
    for match in _SYMBOL_RE.finditer(text):
        sym = match.group(1)
        if sym != analyzed_ticker and sym.startswith(prefix):
            return True
    return False


def _add_catalysts(
    builder: _Builder,
    catalyst_discovery: dict[str, Any] | None,
    *,
    carry_relevance: bool = False,
) -> None:
    """Add catalyst/news events. When ``carry_relevance`` is on (Phase 32A Slice
    2) each item carries its upstream ``relevance_level`` so the budgeter can rank
    by materiality, and obvious leveraged/inverse derivative-instrument noise is
    down-ranked to low-tier / irrelevant. Dark path (off) is byte-identical."""
    if not catalyst_discovery:
        return
    analyzed_ticker = str(catalyst_discovery.get("ticker") or "").upper() or None
    events: list[Any] = []
    for key in ("filing_events", "events", "industry_events"):
        events.extend(catalyst_discovery.get(key) or [])
    for e in events:
        if builder.full:
            return
        if not isinstance(e, dict):
            continue
        if not carry_relevance:
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
            continue

        source_tier = e.get("source_tier") or TIER_T6_MODEL_ESTIMATE
        relevance_level = e.get("relevance_level")
        text = f"{e.get('headline') or ''} {e.get('summary') or ''}"
        if _looks_like_derivative_of(text, analyzed_ticker):
            # Not the analysed company — a leveraged/inverse instrument tracking
            # it. Demote to low-tier aggregator noise + mark irrelevant so the
            # budgeter caps it, but keep it (honest, never silently dropped).
            source_tier = TIER_T6_MODEL_ESTIMATE
            relevance_level = "irrelevant"
        builder.add(
            source_tier=source_tier,
            source_type=e.get("source_type") or "catalyst_event",
            title=e.get("headline") or e.get("form_type") or "Catalyst event",
            url=e.get("source_url"),
            date=e.get("event_date"),
            excerpt=e.get("summary") or e.get("headline"),
            data_quality=e.get("evidence_strength"),
            fields_supported=["catalyst"],
            relevance_level=relevance_level,
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


def _facts_from_connector_evidence(connector_evidence: "list[Any] | None") -> list[dict[str, Any]]:
    """Every structured fact carried by the connector evidence, as plain dicts.

    Deliberately WIDER than ``council._primary_facts``: that function keeps only
    ``confidence == "high"`` facts because it feeds canonical single-value
    report slots, where a medium-confidence figure must not be presented as THE
    number. A trend is a different question — a five-year revenue series whose
    middle years are medium-confidence is still a real, citeable trend, and
    dropping them would leave the council saying "no historical revenue trend"
    beside a complete series. Low confidence is still excluded.
    """
    out: list[dict[str, Any]] = []
    for item in connector_evidence or []:
        pf = getattr(item, "primary_fact", None)
        if pf is None:
            continue
        if getattr(pf, "confidence", None) not in ("high", "medium"):
            continue
        data = pf.model_dump(mode="json") if hasattr(pf, "model_dump") else dict(pf)
        url = getattr(item, "url", None)
        if url:
            data.setdefault("source_url", url)
        out.append(data)
    return out


def _add_historical_series(
    builder: _Builder,
    connector_evidence: "list[Any] | None",
    *,
    max_lines: int,
    max_periods: int,
    historical_facts: "list[dict[str, Any]] | None" = None,
) -> None:
    """Add the compact multi-period trend slice — private-use readiness PR-B.

    ONE evidence item per series, each a single dense line stating its own
    scope, unit and periods. This is the whole design constraint: the extractor
    can produce ~50 period-scoped facts for a single issuer, and pushing those
    in as ~50 items would blow the council's token budget and crowd out every
    other kind of evidence. A series line costs roughly one item and carries
    the shape of five years.

    Added straight after the connector items so it survives the cap, and marked
    ``historical_financial_series`` so the budgeter and the citation checker can
    both recognise it for what it is.
    """
    if builder.full or max_lines <= 0:
        return
    # Prefer the caller's COMPLETE fact set. Deriving from ``connector_evidence``
    # reads the per-document-CAPPED evidence items, which is correct for the
    # prompt and fatal for a series: a real annual report yields ~50
    # period-scoped facts of which only ~10 become items, so every metric
    # arrives as one observation and no trend can exist.
    facts = list(historical_facts or []) or _facts_from_connector_evidence(
        connector_evidence
    )
    if not facts:
        return
    history = build_financial_history(facts, max_periods=max_periods)
    if not history.available:
        return

    ordered = sorted(
        history.series,
        key=lambda s: (0 if s.scope.is_group else 1, s.metric, s.scope_label),
    )
    added = 0
    for series in ordered:
        if builder.full or added >= max_lines:
            break
        # A single-observation "series" is already covered by the ordinary
        # per-fact evidence; repeating it here would spend a slot to say
        # nothing new.
        if series.period_count < MIN_PERIODS_FOR_TREND:
            continue
        line = series.compact_line()
        if not series.is_comparable:
            line += " (not comparable: " + ", ".join(series.comparability_reasons) + ")"
        elif series.missing_periods:
            line += " (missing: " + ", ".join(series.missing_periods) + ")"
        for change in series.changes:
            line += (
                f" | {change.calculation} {change.from_period}->{change.to_period}: "
                f"{change.value}{change.unit}"
            )
        first = series.points[0]
        added += builder.add(
            source_tier=TIER_T1_PRIMARY_FILING,
            source_type="historical_financial_series",
            title=f"{series.metric} history ({series.scope_label})",
            url=first.source_url,
            date=series.points[-1].period_label,
            excerpt=line,
            data_quality="B",
            fields_supported=[series.metric],
            scope=series.scope.label,
            period=series.points[-1].period.key,
        )


#: Hard bound on current-period lines. A results release states a handful of
#: headline figures; more than this is a sign something went wrong upstream.
DEFAULT_MAX_CURRENT_PERIOD_LINES = 8


def _add_current_period_state(
    builder: _Builder,
    historical_facts: "list[dict[str, Any]] | None",
    *,
    max_lines: int = DEFAULT_MAX_CURRENT_PERIOD_LINES,
) -> None:
    """Add the issuer's CURRENT-PERIOD reporting, explicitly labelled.

    Current-period acceptance. Interim facts do reach the council as ordinary
    per-fact items, but nothing tells it which of them are the issuer's NEWEST
    reporting, and nothing states that they are not comparable with the annual
    figures beside them. A council that cannot see the difference will write the
    two into one sentence.

    So this mirrors ``_add_historical_series``: one compact, dense line per
    metric and scope, each stating its own period, and one leading line naming
    the state. Every line says the period in words — ``H1 2026``, ``Q1 2027`` —
    so an interim figure can never read as a year.

    No arithmetic of any kind: no annualisation, no run-rate, no comparison
    with the annual figure. The council is given the facts and told they are
    different spans.
    """
    if builder.full or max_lines <= 0:
        return
    facts = list(historical_facts or [])
    if not facts:
        return
    state = build_reporting_period_state(periods_of(facts))
    if not state.has_current_period:
        return

    # Every fact whose period is one of the two part-year states — a release
    # commonly states BOTH ("H1 2026 revenue" and "Q2 2026 revenue"), and both
    # are the issuer's current reporting.
    wanted = {
        p.key
        for p in (state.latest_current_period, state.latest_interim, state.latest_quarter)
        if not p.is_unknown
    }
    selected: list[tuple[str, str, dict[str, Any]]] = []
    for fact in facts:
        period = parse_period(fact.get("period"))
        if period.is_unknown or period.key not in wanted:
            continue
        metric = fact.get("field") or fact.get("label")
        if not isinstance(metric, str) or fact.get("numeric_value") is None:
            continue
        selected.append((metric, period.key or "", fact))
    if not selected:
        return

    labels = state.as_labels()
    builder.add(
        source_tier=TIER_T1_PRIMARY_FILING,
        source_type="current_period_financial_state",
        title="Current-period reporting state",
        excerpt=(
            f"Latest annual period: {labels['latest_annual'] or 'not reported'}. "
            f"Latest interim: {labels['latest_interim'] or 'not reported'}. "
            f"Latest quarter: {labels['latest_quarter'] or 'not reported'}. "
            "An interim or quarterly figure covers PART of a year: it does not "
            "supersede the annual figure, is not comparable with it, and has "
            "not been annualised or extrapolated."
        ),
        data_quality="B",
        period=state.latest_current_period.key,
    )

    added = 0
    seen: set[tuple[str, str, str]] = set()
    for metric, period_key, fact in sorted(selected, key=lambda t: (t[0], t[1])):
        if builder.full or added >= max_lines:
            break
        scope = parse_scope(fact.get("scope"))
        key = (metric, period_key, scope.scope_key or "")
        if key in seen:
            continue
        seen.add(key)
        period = parse_period(fact.get("period"))
        unit = " ".join(
            b for b in (fact.get("currency"), fact.get("scale")) if isinstance(b, str)
        )
        value = fact.get("value") or fact.get("numeric_value")
        added += builder.add(
            source_tier=TIER_T1_PRIMARY_FILING,
            source_type="current_period_financial_state",
            title=f"{metric} — {period.label()} ({scope.human_label()})",
            url=fact.get("source_url"),
            date=period.label(),
            excerpt=(
                f"{metric} ({scope.human_label()}) for {period.label()}: "
                f"{value}{f' {unit}' if unit else ''}. Part-year figure — not "
                "comparable with an annual figure."
            ),
            data_quality="B",
            fields_supported=[metric],
            scope=fact.get("scope"),
            period=period.key,
        )


def build_evidence_pack(
    *,
    report_content: dict[str, Any],
    company_snapshot: dict[str, Any] | None = None,
    catalyst_discovery: dict[str, Any] | None = None,
    source_rows: list[dict[str, Any]] | None = None,
    max_items: int = 40,
    extra_known_gaps: list[str] | None = None,
    connector_evidence: list[Any] | None = None,
    connector_gap_messages: list[str] | None = None,
    historical_facts: list[dict[str, Any]] | None = None,
    apply_budget: bool = False,
    budget_cfg: Any | None = None,
) -> EvidencePack:
    """Build a bounded, cited evidence pack for one company.

    ``extra_known_gaps`` (Phase 29A) appends source-framework gaps — e.g. planned
    external connectors whose evidence is not yet sourced — so the source critic
    sees missing coverage as an explicit, honest gap rather than silent absence.

    ``connector_evidence`` / ``connector_gap_messages`` (Phase 29B) inject the
    source-registry connector output (SEC filing metadata, company-IR press
    releases, and honest scaffold/eligibility gaps). Connector evidence is added
    first so its primary filings survive the cap.

    ``apply_budget`` (Phase 29B.2) runs the deterministic evidence budgeter as a
    final step so a larger primary-source pack cannot balloon the council prompt.
    It de-duplicates, prefers higher-tier factual excerpts, and bounds item count
    + total chars while never dropping all source gaps. Off by default so the
    Phase 28A/29B tests keep their exact item counts.
    """
    builder = _Builder(max_items=max(1, max_items))

    # Phase 32A Slice 2: the same flag gates the build-time tier-split + news
    # materiality here AND the category-aware selection inside the budgeter. Off
    # by default → byte-identical dark path. Read defensively from ``budget_cfg``.
    budgets_enabled = bool(
        getattr(budget_cfg, "llm_council_evidence_budgets_enabled", False)
    )

    # Fall back to the report's own source appendix when explicit rows are not
    # supplied, so the builder works from report_content alone.
    if source_rows is None:
        appendix = report_content.get("source_citation_appendix") or {}
        source_rows = ((appendix.get("sources") or {}).get("value")) or []

    # Order matters: connector primary filings first, then SEC fundamentals,
    # snapshot metrics, sources, catalysts — so that if the cap truncates, the
    # highest-value evidence stays.
    for fw_item in connector_evidence or []:
        if builder.full:
            break
        builder.add_framework_item(fw_item)
    _add_historical_series(
        builder,
        connector_evidence,
        historical_facts=historical_facts,
        max_lines=int(
            getattr(budget_cfg, "llm_council_history_max_series", DEFAULT_MAX_HISTORY_SERIES)
            or DEFAULT_MAX_HISTORY_SERIES
        ),
        max_periods=int(
            getattr(budget_cfg, "financial_history_max_periods", DEFAULT_MAX_PERIODS)
            or DEFAULT_MAX_PERIODS
        ),
    )
    # Directly after the trend slice, and for the same reason: what the issuer
    # reported MOST RECENTLY is as material as how it has trended, and both
    # must survive the cap.
    _add_current_period_state(
        builder,
        historical_facts,
        max_lines=int(
            getattr(
                budget_cfg,
                "llm_council_current_period_max_lines",
                DEFAULT_MAX_CURRENT_PERIOD_LINES,
            )
            or DEFAULT_MAX_CURRENT_PERIOD_LINES
        ),
    )
    _add_sec_fundamentals(builder, company_snapshot, tier_split=budgets_enabled)
    _add_financial_snapshot(builder, report_content)
    if budgets_enabled:
        _add_financial_context(builder, report_content, company_snapshot)
    _add_sources(builder, source_rows)
    _add_catalysts(builder, catalyst_discovery, carry_relevance=budgets_enabled)

    is_mock = bool((company_snapshot or {}).get("is_mock")) if company_snapshot else False
    do_not_infer = [
        "Do not infer a valuation, price target, fair value, or upside/downside.",
        "Do not infer a rating or trading action.",
        "Do not treat model estimates (T6) as primary facts.",
    ]
    if is_mock:
        do_not_infer.append("Data is mock/placeholder — treat all values as non-factual.")

    known_gaps = _known_gaps(report_content)
    for g in list(extra_known_gaps or []) + list(connector_gap_messages or []):
        if g and g not in known_gaps:
            known_gaps.append(g)

    pack = EvidencePack(
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
    if apply_budget:
        # Local import avoids any import-time coupling to the budgeter/config.
        from app.services.llm.evidence_budget import apply_evidence_budget

        pack = apply_evidence_budget(pack, cfg=budget_cfg)
    return pack
