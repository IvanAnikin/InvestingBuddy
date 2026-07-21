"""
Phase 27: Market Segment Discovery — deterministic universe builder.

Given a parsed thesis (+ optional filters), produce a BOUNDED list of real,
publicly-listed candidate companies to feed the existing Phase 25 discovery
scan. Fully deterministic (a curated reference registry only — no LLM, no
network), so a build is reproducible and CI-safe.

DATA SOURCE (bootstrap): ``THEME_COMPANY_REGISTRY`` is a small, hand-curated
reference list of REAL, publicly-listed issuers grouped by research theme. It is
a bounded starting universe — NOT an exhaustive index and NOT a recommendation
list. Every entry is a real company with a real ticker; nothing here is
fabricated. When richer symbol search (EODHD / SEC company facts) is wired in
later, it can extend this registry — the contract (a bounded, source-tagged
list of universe items) stays the same.

GUARDRAILS:
  * Hard cap on universe size (default 25, absolute max 50) — never an
    uncontrolled full-market scan.
  * A vague/too-broad thesis is refused upstream (``parsed.needs_narrowing``).
  * Every universe item records WHY it was included (matched keywords + reason)
    and WHERE it came from (universe_source + source_tier).
  * Companies filtered out by a region/exclusion filter are recorded in
    ``excluded`` with a reason (never silently dropped).
  * No company is fabricated. If metadata is unknown for an entry, only the
    ticker is emitted and ``metadata_not_sourced`` is set.

SAFETY: nothing here is an investment recommendation. The universe is a research
search space only; every downstream candidate is human-review-required.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.discovery_thesis_scoring import score_thesis_relevance
from app.services.exchange_registry import region_for_country
from app.services.sector_taxonomy import industry_matches, sector_matches

# Absolute ceiling regardless of the requested ``max_universe_size`` — the hard
# guardrail against an accidental full-market scan.
HARD_MAX_UNIVERSE_SIZE = 50
DEFAULT_MAX_UNIVERSE_SIZE = 25

# Curated reference source tag for every registry-sourced ticker.
_CURATED_SOURCE = "curated_theme_registry"
_CURATED_SOURCE_TIER = "T3_curated_reference_list"

# Country -> canonical region is derived from the exchange registry so the two
# cannot drift. See app.services.exchange_registry.


def _entry(
    ticker: str,
    name: str,
    exchange: str,
    country: str,
    sector: str,
    industry: str,
) -> dict[str, str]:
    return {
        "ticker": ticker,
        "company_name": name,
        "exchange": exchange,
        "country": country,
        "sector": sector,
        "industry": industry,
    }


# ---------------------------------------------------------------------------
# Curated theme -> real public company registry (bootstrap universe source).
# Entries are real, publicly-listed issuers. This is a bounded reference list,
# not an exhaustive index and not a recommendation.
# ---------------------------------------------------------------------------

THEME_COMPANY_REGISTRY: dict[str, list[dict[str, str]]] = {
    "defense": [
        _entry("LMT", "Lockheed Martin Corp.", "US", "United States", "Industrials", "Aerospace & Defense"),
        _entry("RTX", "RTX Corporation", "US", "United States", "Industrials", "Aerospace & Defense"),
        _entry("NOC", "Northrop Grumman Corp.", "US", "United States", "Industrials", "Aerospace & Defense"),
        _entry("GD", "General Dynamics Corp.", "US", "United States", "Industrials", "Aerospace & Defense"),
        _entry("LHX", "L3Harris Technologies Inc.", "US", "United States", "Industrials", "Aerospace & Defense"),
        _entry("RHM", "Rheinmetall AG", "XETRA", "Germany", "Industrials", "Aerospace & Defense"),
        _entry("BA", "BAE Systems plc", "LSE", "United Kingdom", "Industrials", "Aerospace & Defense"),
        _entry("HO", "Thales SA", "PA", "France", "Industrials", "Aerospace & Defense"),
        _entry("SAAB-B", "Saab AB", "ST", "Sweden", "Industrials", "Aerospace & Defense"),
        _entry("LDO", "Leonardo S.p.A.", "MI", "Italy", "Industrials", "Aerospace & Defense"),
    ],
    "semiconductors": [
        _entry("NVDA", "NVIDIA Corp.", "US", "United States", "Technology", "Semiconductors"),
        _entry("AMD", "Advanced Micro Devices Inc.", "US", "United States", "Technology", "Semiconductors"),
        _entry("AVGO", "Broadcom Inc.", "US", "United States", "Technology", "Semiconductors"),
        _entry("AMAT", "Applied Materials Inc.", "US", "United States", "Technology", "Semiconductor Equipment"),
        _entry("LRCX", "Lam Research Corp.", "US", "United States", "Technology", "Semiconductor Equipment"),
        _entry("KLAC", "KLA Corp.", "US", "United States", "Technology", "Semiconductor Equipment"),
        _entry("TER", "Teradyne Inc.", "US", "United States", "Technology", "Semiconductor Equipment"),
        _entry("ASML", "ASML Holding N.V.", "AS", "Netherlands", "Technology", "Semiconductor Equipment"),
        _entry("8035", "Tokyo Electron Ltd.", "TSE", "Japan", "Technology", "Semiconductor Equipment"),
    ],
    "nuclear_energy": [
        _entry("CCJ", "Cameco Corp.", "US", "Canada", "Energy", "Uranium"),
        _entry("UEC", "Uranium Energy Corp.", "US", "United States", "Energy", "Uranium"),
        _entry("UUUU", "Energy Fuels Inc.", "US", "United States", "Energy", "Uranium"),
        _entry("LEU", "Centrus Energy Corp.", "US", "United States", "Energy", "Nuclear"),
        _entry("SMR", "NuScale Power Corp.", "US", "United States", "Utilities", "Nuclear"),
        _entry("BWXT", "BWX Technologies Inc.", "US", "United States", "Industrials", "Nuclear"),
        _entry("OKLO", "Oklo Inc.", "US", "United States", "Utilities", "Nuclear"),
    ],
    "grid_electrification": [
        _entry("ETN", "Eaton Corp. plc", "US", "United States", "Industrials", "Electrical Equipment"),
        _entry("GEV", "GE Vernova Inc.", "US", "United States", "Industrials", "Electrical Equipment"),
        _entry("PWR", "Quanta Services Inc.", "US", "United States", "Industrials", "Construction & Engineering"),
        _entry("NVT", "nVent Electric plc", "US", "United States", "Industrials", "Electrical Equipment"),
        _entry("POWL", "Powell Industries Inc.", "US", "United States", "Industrials", "Electrical Equipment"),
        _entry("SU", "Schneider Electric SE", "PA", "France", "Industrials", "Electrical Equipment"),
        _entry("SIE", "Siemens AG", "XETRA", "Germany", "Industrials", "Electrical Equipment"),
    ],
    "robotics_automation": [
        _entry("ROK", "Rockwell Automation Inc.", "US", "United States", "Industrials", "Robotics & Automation"),
        _entry("TER", "Teradyne Inc.", "US", "United States", "Technology", "Robotics & Automation"),
        _entry("ISRG", "Intuitive Surgical Inc.", "US", "United States", "Healthcare", "Robotics & Automation"),
        _entry("ABBN", "ABB Ltd.", "SW", "Switzerland", "Industrials", "Robotics & Automation"),
        _entry("6954", "Fanuc Corp.", "TSE", "Japan", "Industrials", "Robotics & Automation"),
        _entry("6506", "Yaskawa Electric Corp.", "TSE", "Japan", "Industrials", "Robotics & Automation"),
        _entry("6861", "Keyence Corp.", "TSE", "Japan", "Technology", "Robotics & Automation"),
    ],
    "biotech_pharma": [
        _entry("VRTX", "Vertex Pharmaceuticals Inc.", "US", "United States", "Healthcare", "Biotechnology"),
        _entry("REGN", "Regeneron Pharmaceuticals Inc.", "US", "United States", "Healthcare", "Biotechnology"),
        _entry("GILD", "Gilead Sciences Inc.", "US", "United States", "Healthcare", "Biotechnology"),
        _entry("AMGN", "Amgen Inc.", "US", "United States", "Healthcare", "Biotechnology"),
        _entry("BIIB", "Biogen Inc.", "US", "United States", "Healthcare", "Biotechnology"),
        _entry("MRNA", "Moderna Inc.", "US", "United States", "Healthcare", "Biotechnology"),
    ],
    "banks_fintech": [
        _entry("JPM", "JPMorgan Chase & Co.", "US", "United States", "Financials", "Banks"),
        _entry("BAC", "Bank of America Corp.", "US", "United States", "Financials", "Banks"),
        _entry("WFC", "Wells Fargo & Co.", "US", "United States", "Financials", "Banks"),
        _entry("PYPL", "PayPal Holdings Inc.", "US", "United States", "Financials", "Financial Technology"),
        _entry("SQ", "Block Inc.", "US", "United States", "Financials", "Financial Technology"),
        _entry("SOFI", "SoFi Technologies Inc.", "US", "United States", "Financials", "Financial Technology"),
    ],
    "mining_materials": [
        _entry("FCX", "Freeport-McMoRan Inc.", "US", "United States", "Materials", "Metals & Mining"),
        _entry("SCCO", "Southern Copper Corp.", "US", "United States", "Materials", "Metals & Mining"),
        _entry("MP", "MP Materials Corp.", "US", "United States", "Materials", "Metals & Mining"),
        _entry("ALB", "Albemarle Corp.", "US", "United States", "Materials", "Metals & Mining"),
        _entry("LAC", "Lithium Americas Corp.", "US", "United States", "Materials", "Metals & Mining"),
        _entry("CLF", "Cleveland-Cliffs Inc.", "US", "United States", "Materials", "Metals & Mining"),
    ],
    # Phase 27.1B — luxury / watches / jewelry. Predominantly European issuers,
    # so most of these are NOT SEC-eligible venues: their fundamentals degrade
    # honestly to ``not_sourced`` rather than resolving a US ticker collision
    # (MC.PA is LVMH here and must never come back as Moelis — see
    # app.services.exchange_registry).
    "luxury_goods": [
        _entry("UHR", "Swatch Group AG", "SW", "Switzerland", "Consumer Discretionary", "Watches & Jewelry"),
        _entry("CFR", "Compagnie Financiere Richemont SA", "SW", "Switzerland", "Consumer Discretionary", "Watches & Jewelry"),
        _entry("MC", "LVMH Moet Hennessy Louis Vuitton SE", "PA", "France", "Consumer Discretionary", "Luxury Goods"),
        _entry("RMS", "Hermes International SCA", "PA", "France", "Consumer Discretionary", "Luxury Goods"),
        _entry("KER", "Kering SA", "PA", "France", "Consumer Discretionary", "Luxury Goods"),
        _entry("MONC", "Moncler S.p.A.", "MI", "Italy", "Consumer Discretionary", "Luxury Apparel"),
        _entry("BRBY", "Burberry Group plc", "LSE", "United Kingdom", "Consumer Discretionary", "Luxury Apparel"),
        _entry("PNDORA", "Pandora A/S", "CO", "Denmark", "Consumer Discretionary", "Jewelry"),
        # Non-European luxury exposure. A Europe-filtered thesis excludes these
        # (recorded in ``excluded``, never silently dropped); a global thesis
        # keeps them.
        _entry("CPRI", "Capri Holdings Limited", "US", "United States", "Consumer Discretionary", "Luxury Goods"),
        _entry("TPR", "Tapestry, Inc.", "US", "United States", "Consumer Discretionary", "Luxury Goods"),
        _entry("1913", "Prada S.p.A.", "HK", "Hong Kong", "Consumer Discretionary", "Luxury Goods"),
    ],
    "ai_infrastructure": [
        _entry("NVDA", "NVIDIA Corp.", "US", "United States", "Technology", "AI Infrastructure"),
        _entry("AVGO", "Broadcom Inc.", "US", "United States", "Technology", "AI Infrastructure"),
        _entry("VRT", "Vertiv Holdings Co.", "US", "United States", "Industrials", "Data Centers"),
        _entry("DLR", "Digital Realty Trust Inc.", "US", "United States", "Real Estate", "Data Centers"),
        _entry("EQIX", "Equinix Inc.", "US", "United States", "Real Estate", "Data Centers"),
        _entry("SMCI", "Super Micro Computer Inc.", "US", "United States", "Technology", "AI Infrastructure"),
        _entry("ANET", "Arista Networks Inc.", "US", "United States", "Technology", "AI Infrastructure"),
    ],
}


def supported_themes_hint() -> str:
    """
    Comma-joined list of themes the registry can actually build a universe for.

    Derived from the registry so the guidance an admin sees can never drift
    from what the registry contains — the whole reason "European watch
    producers" used to fail with advice that omitted the theme it needed.
    """
    return ", ".join(sorted(THEME_COMPANY_REGISTRY))


@dataclass
class UniverseItem:
    """One bounded, source-tagged candidate company in the generated universe."""

    ticker: str
    company_name: str | None
    exchange: str
    country: str | None
    region: str | None
    sector: str | None
    industry: str | None
    theme: str | None
    matched_keywords: list[str] = field(default_factory=list)
    relevance_reason: str = ""
    universe_source: str = _CURATED_SOURCE
    source_tier: str = _CURATED_SOURCE_TIER
    relevance_score_pre_scan: float = 0.0
    metadata_not_sourced: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UniverseResult:
    """Result of building a universe from a parsed thesis."""

    items: list[dict[str, Any]] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)
    source_summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    needs_narrowing: bool = False
    requested_max: int = DEFAULT_MAX_UNIVERSE_SIZE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _select_registry_entries(parsed: dict[str, Any]) -> list[tuple[str, dict[str, str]]]:
    """
    Select ``(theme, entry)`` pairs from the registry for a parsed thesis.

    Primary selector is the matched theme(s). When no theme matched but a
    sector/industry filter is present, fall back to matching registry entries by
    sector/industry so an explicit structured filter still yields a universe.
    """
    themes = list(parsed.get("themes") or [])
    selected: list[tuple[str, dict[str, str]]] = []

    if themes:
        for theme in themes:
            for entry in THEME_COMPANY_REGISTRY.get(theme, []):
                selected.append((theme, entry))
        return selected

    # Fallback: no theme, but a sector/industry filter can still drive
    # selection. Matching goes through the taxonomy rather than raw string
    # equality, so a thesis filtered on "Luxury Goods" reaches the entries the
    # registry tags "Consumer Discretionary".
    parsed_sectors = [s for s in parsed.get("sectors") or [] if s]
    parsed_industries = [i for i in parsed.get("industries") or [] if i]
    if parsed_sectors or parsed_industries:
        for theme, entries in THEME_COMPANY_REGISTRY.items():
            for entry in entries:
                sector_hit = any(
                    sector_matches(s, entry["sector"], [entry["industry"]])
                    for s in parsed_sectors
                )
                industry_hit = any(
                    industry_matches(i, entry["industry"]) for i in parsed_industries
                )
                if sector_hit or industry_hit:
                    selected.append((theme, entry))
    return selected


def build_universe(
    parsed: dict[str, Any],
    *,
    max_universe_size: int = DEFAULT_MAX_UNIVERSE_SIZE,
) -> UniverseResult:
    """
    Build a bounded candidate universe from a parsed thesis.

    Returns a :class:`UniverseResult`. When the thesis is too vague
    (``parsed.needs_narrowing``) an empty result with ``needs_narrowing=True`` is
    returned — the caller must refuse to launch a scan.
    """
    cap = max(1, min(int(max_universe_size or DEFAULT_MAX_UNIVERSE_SIZE), HARD_MAX_UNIVERSE_SIZE))
    warnings: list[str] = []

    if parsed.get("needs_narrowing"):
        return UniverseResult(
            items=[],
            excluded=[],
            source_summary={"selected": 0, "excluded": 0},
            warnings=list(parsed.get("warnings") or [])
            or ["Thesis needs narrowing — no bounded universe built."],
            needs_narrowing=True,
            requested_max=cap,
        )

    parsed_regions = set(parsed.get("regions") or [])
    parsed_countries = set(parsed.get("countries") or [])
    region_requested = bool(parsed_regions or parsed_countries)
    exclusion_keywords = {k.lower() for k in parsed.get("exclusion_keywords") or []}

    selected_pairs = _select_registry_entries(parsed)

    items: list[UniverseItem] = []
    excluded: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for theme, entry in selected_pairs:
        key = (entry["ticker"], entry["exchange"])
        if key in seen:
            continue
        seen.add(key)

        region = region_for_country(entry["country"])

        # ── Region filter ─────────────────────────────────────────────────
        if region_requested:
            region_ok = (region in parsed_regions) or (entry["country"] in parsed_countries)
            if not region_ok:
                excluded.append(
                    {
                        "ticker": entry["ticker"],
                        "company_name": entry["company_name"],
                        "reason": (
                            f"region mismatch: {entry['country']} not in requested "
                            f"{sorted(parsed_regions | parsed_countries)}"
                        ),
                    }
                )
                continue

        # ── Exclusion-keyword filter ──────────────────────────────────────
        haystack = " ".join(
            [entry["company_name"], entry["industry"], theme]
        ).lower()
        hit = next((kw for kw in exclusion_keywords if kw and kw in haystack), None)
        if hit:
            excluded.append(
                {
                    "ticker": entry["ticker"],
                    "company_name": entry["company_name"],
                    "reason": f"excluded by keyword '{hit}'",
                }
            )
            continue

        item = UniverseItem(
            ticker=entry["ticker"],
            company_name=entry["company_name"],
            exchange=entry["exchange"],
            country=entry["country"],
            region=region,
            sector=entry["sector"],
            industry=entry["industry"],
            theme=theme,
        )
        rel = score_thesis_relevance(item.to_dict(), parsed)
        item.relevance_score_pre_scan = rel["thesis_relevance_score"]
        item.matched_keywords = rel["matched_keywords"]
        item.relevance_reason = rel["relevance_reason"]
        items.append(item)

    # ── Rank by pre-scan relevance and apply the hard cap ─────────────────
    items.sort(key=lambda i: i.relevance_score_pre_scan, reverse=True)
    if len(items) > cap:
        warnings.append(
            f"Universe truncated to the top {cap} of {len(items)} matched "
            "companies (bounded scan). Narrow the thesis for a tighter set."
        )
        items = items[:cap]

    if not items:
        if selected_pairs and region_requested:
            warnings.append(
                "No companies in the curated registry matched the requested "
                "region/country filter. Broaden the region or remove the filter."
            )
        else:
            warnings.append(
                "No companies matched this thesis in the curated registry yet. "
                "Try a supported theme: " + supported_themes_hint() + "."
            )

    source_summary = {
        "selected": len(items),
        "excluded": len(excluded),
        "by_source": {_CURATED_SOURCE: len(items)},
        "by_source_tier": {_CURATED_SOURCE_TIER: len(items)},
        "themes": list(parsed.get("themes") or []),
        "region_filtered": region_requested,
    }

    return UniverseResult(
        items=[i.to_dict() for i in items],
        excluded=excluded,
        source_summary=source_summary,
        warnings=warnings,
        needs_narrowing=False,
        requested_max=cap,
    )
