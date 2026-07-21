"""
Phase 27: Market Segment Discovery — deterministic market-thesis parser.

Turns an admin's natural-language market segment / investment-theme description
(plus optional structured filters) into a STRUCTURED search intent that the
universe builder can act on. It is fully deterministic (keyword tables only — no
LLM, no network), so parsing is reproducible and CI-safe.

SAFETY — read carefully:
  * This parser ONLY structures a search. It never produces an investment
    recommendation, price target, fair value, or BUY/SELL/HOLD/WATCH label.
  * It never decides that a company "should be bought" — it only extracts which
    themes / regions / sectors an admin wants to search for internal research
    candidates.
  * A vague thesis (e.g. "best stocks to buy") is flagged ``needs_narrowing`` so
    the caller refuses to launch an unbounded scan.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.sector_taxonomy import normalize_industry, normalize_sector

# ---------------------------------------------------------------------------
# Keyword mapping tables (deterministic bootstrap)
#
# Each theme maps a canonical key -> the trigger phrases that select it plus the
# sector/industry hints it implies. Phrases are matched case-insensitively as
# whole words/phrases. Keep these conservative — a theme should only fire on an
# unambiguous phrase.
# ---------------------------------------------------------------------------

_THEME_TABLE: dict[str, dict[str, Any]] = {
    "defense": {
        "phrases": [
            "defense",
            "defence",
            "aerospace",
            "military",
            "weapons",
            "munitions",
            "ammunition",
            "arms",
            "nato",
            "missile",
            "fighter jet",
            "warship",
            "army",
            "navy",
        ],
        "sectors": ["Industrials"],
        "industries": ["Aerospace & Defense"],
    },
    "semiconductors": {
        "phrases": [
            "semiconductor",
            "semiconductors",
            "chip",
            "chips",
            "chipmaker",
            "wafer",
            "foundry",
            "lithography",
            "semiconductor equipment",
            "chip equipment",
            "fab",
        ],
        "sectors": ["Technology"],
        "industries": ["Semiconductors", "Semiconductor Equipment"],
    },
    "nuclear_energy": {
        "phrases": [
            "nuclear",
            "uranium",
            "reactor",
            "smr",
            "small modular reactor",
            "enrichment",
            "fission",
            "atomic energy",
        ],
        "sectors": ["Energy", "Utilities"],
        "industries": ["Nuclear", "Uranium"],
    },
    "grid_electrification": {
        "phrases": [
            "grid",
            "electrification",
            "power grid",
            "transmission",
            "substation",
            "transformer",
            "electrical equipment",
            "electrical grid",
            "utilities",
            "utility",
        ],
        "sectors": ["Utilities", "Industrials"],
        "industries": ["Electrical Equipment", "Utilities"],
    },
    "robotics_automation": {
        "phrases": [
            "robotics",
            "robot",
            "robots",
            "automation",
            "industrial automation",
            "factory automation",
            "motion control",
            "cobot",
            "cobots",
        ],
        "sectors": ["Industrials", "Technology"],
        "industries": ["Robotics & Automation"],
    },
    "biotech_pharma": {
        "phrases": [
            "biotech",
            "biotechnology",
            "pharma",
            "pharmaceutical",
            "pharmaceuticals",
            "biopharma",
            "therapeutics",
            "gene therapy",
            "drug maker",
        ],
        "sectors": ["Healthcare"],
        "industries": ["Biotechnology", "Pharmaceuticals"],
    },
    "banks_fintech": {
        "phrases": [
            "bank",
            "banks",
            "banking",
            "fintech",
            "payments",
            "financial technology",
            "neobank",
            "lender",
        ],
        "sectors": ["Financials"],
        "industries": ["Banks", "Financial Technology"],
    },
    "mining_materials": {
        "phrases": [
            "mining",
            "miner",
            "miners",
            "copper",
            "lithium",
            "rare earth",
            "cobalt",
            "nickel",
            "uranium mining",
            "metals",
        ],
        "sectors": ["Materials"],
        "industries": ["Mining", "Metals & Mining"],
    },
    "ai_infrastructure": {
        "phrases": [
            "ai infrastructure",
            "artificial intelligence infrastructure",
            "data center",
            "data centre",
            "data centers",
            "hyperscaler",
            "cloud infrastructure",
            "gpu",
            "ai accelerator",
            "ai chips",
        ],
        "sectors": ["Technology"],
        "industries": ["Data Centers", "AI Infrastructure"],
    },
    # Phase 27.1B. "watch"/"watches" are ordinary English, so they are safe to
    # match here (this table only STRUCTURES a search) but must never leak into
    # generated prose — see app.services.safety_terms, which is case-sensitive
    # on the ALL-CAPS rating token precisely so "Swatch" and "Watches &
    # Jewelry" stay legal while a "WATCH" label stays blocked.
    "luxury_goods": {
        "phrases": [
            "luxury",
            "luxury goods",
            "luxury brands",
            "watch",
            "watches",
            "watchmaker",
            "watchmakers",
            "watchmaking",
            "timepiece",
            "timepieces",
            "jewelry",
            "jewellery",
            "watches & jewelry",
            "watches and jewelry",
            "watches & jewellery",
            "watches and jewellery",
            "personal goods",
            "leather goods",
            "handbag",
            "handbags",
            "premium brands",
            "high-end consumer",
            "fashion luxury",
        ],
        "sectors": ["Consumer Discretionary"],
        "industries": [
            "Luxury Goods",
            "Watches & Jewelry",
            "Personal Goods",
            "Apparel & Accessories",
        ],
    },
}

# ---------------------------------------------------------------------------
# Human-facing theme descriptions (admin UI + supported-themes endpoint).
#
# Kept separate from ``_THEME_TABLE`` so the matching rules stay free of
# presentation concerns. Every key here MUST exist in ``_THEME_TABLE`` —
# ``get_supported_themes`` iterates the matching table, so a stale entry here is
# simply never emitted, and a missing one degrades to a generated label rather
# than hiding a working theme.
#
# SAFETY: example queries describe a SEARCH ("European watch producers"), never
# an action. None of them may read as a recommendation.
# ---------------------------------------------------------------------------

_THEME_DISPLAY: dict[str, dict[str, Any]] = {
    "defense": {
        "label": "Defense / aerospace",
        "examples": [
            "European defense suppliers benefiting from NATO spending",
            "US aerospace and defense primes",
        ],
    },
    "semiconductors": {
        "label": "Semiconductors / chip equipment",
        "examples": [
            "US semiconductor equipment companies with recent positive catalysts",
            "European semiconductor lithography suppliers",
        ],
    },
    "nuclear_energy": {
        "label": "Nuclear energy / uranium",
        "examples": [
            "US nuclear and uranium companies",
            "Small modular reactor developers",
        ],
    },
    "grid_electrification": {
        "label": "Power grid / electrification",
        "examples": [
            "Power grid and electrical equipment suppliers",
            "European electrification and transmission companies",
        ],
    },
    "robotics_automation": {
        "label": "Robotics / industrial automation",
        "examples": [
            "Japanese industrial robotics companies",
            "Factory automation suppliers",
        ],
    },
    "biotech_pharma": {
        "label": "Biotech / pharmaceuticals",
        "examples": [
            "US biotechnology companies",
            "Large-cap pharmaceutical companies",
        ],
    },
    "banks_fintech": {
        "label": "Banks / fintech",
        "examples": [
            "US banks and payments companies",
            "Fintech and payments companies",
        ],
    },
    "mining_materials": {
        "label": "Mining / materials",
        "examples": [
            "Copper and lithium mining companies",
            "Rare earth and critical metals miners",
        ],
    },
    "ai_infrastructure": {
        "label": "AI infrastructure / data centers",
        "examples": [
            "AI infrastructure and data center companies",
            "Hyperscaler cloud infrastructure suppliers",
        ],
    },
    "luxury_goods": {
        "label": "Luxury goods / watches / jewelry",
        "examples": [
            "European watch producers",
            "Swiss watch companies",
            "European luxury goods companies",
        ],
    },
}


def get_supported_themes() -> list[dict[str, Any]]:
    """
    Describe every theme the parser can match, for the admin UI / API.

    Derived from the SAME tables ``parse_thesis`` matches on, so the guidance an
    admin is shown can never claim a theme the parser does not actually
    support. Returns theme id, display label, trigger keywords, implied
    sectors/industries, and recommendation-free example queries.
    """
    out: list[dict[str, Any]] = []
    for theme_id, spec in _THEME_TABLE.items():
        display = _THEME_DISPLAY.get(theme_id, {})
        out.append(
            {
                "id": theme_id,
                "label": display.get("label") or theme_id.replace("_", " ").title(),
                "keywords": list(spec.get("phrases", [])),
                "sectors": list(spec.get("sectors", [])),
                "industries": list(spec.get("industries", [])),
                "examples": list(display.get("examples") or []),
            }
        )
    out.sort(key=lambda t: str(t["label"]).lower())
    return out


# ---------------------------------------------------------------------------
# Region / country tables
# ---------------------------------------------------------------------------

_REGION_TABLE: dict[str, list[str]] = {
    "Europe": [
        "europe",
        "european",
        "eurozone",
        "eu ",
        "nordic",
    ],
    "North America": [
        "us ",
        "u.s.",
        "usa",
        "united states",
        "american",
        "america",
        "north america",
        "canada",
        "canadian",
    ],
    "Japan": ["japan", "japanese"],
    "China": ["china", "chinese"],
    "Asia": ["asia", "asian", "korea", "korean", "taiwan", "taiwanese", "india", "indian"],
}

# Explicit country phrases -> canonical country + region they belong to.
_COUNTRY_TABLE: dict[str, tuple[str, str]] = {
    "germany": ("Germany", "Europe"),
    "german": ("Germany", "Europe"),
    "france": ("France", "Europe"),
    "french": ("France", "Europe"),
    "united kingdom": ("United Kingdom", "Europe"),
    "uk ": ("United Kingdom", "Europe"),
    "britain": ("United Kingdom", "Europe"),
    "british": ("United Kingdom", "Europe"),
    "italy": ("Italy", "Europe"),
    "italian": ("Italy", "Europe"),
    "spain": ("Spain", "Europe"),
    "sweden": ("Sweden", "Europe"),
    "swedish": ("Sweden", "Europe"),
    "netherlands": ("Netherlands", "Europe"),
    "dutch": ("Netherlands", "Europe"),
    "switzerland": ("Switzerland", "Europe"),
    "swiss": ("Switzerland", "Europe"),
    # Phase 27.1B — venues the luxury registry lists on.
    "denmark": ("Denmark", "Europe"),
    "danish": ("Denmark", "Europe"),
    "hong kong": ("Hong Kong", "Asia"),
    "japan": ("Japan", "Japan"),
    "japanese": ("Japan", "Japan"),
    "china": ("China", "China"),
    "chinese": ("China", "China"),
    "united states": ("United States", "North America"),
    "canada": ("Canada", "North America"),
    "canadian": ("Canada", "North America"),
}

# ---------------------------------------------------------------------------
# Size / source-intent / catalyst-intent hint tables
# ---------------------------------------------------------------------------

_SIZE_TABLE: dict[str, list[str]] = {
    "micro_cap": ["micro-cap", "micro cap", "microcap"],
    "small_cap": ["small-cap", "small cap", "smallcap", "smid"],
    "mid_cap": ["mid-cap", "mid cap", "midcap"],
    "large_cap": ["large-cap", "large cap", "largecap", "mega-cap", "megacap", "mega cap"],
}

_SOURCE_INTENT_PHRASES = [
    "filing",
    "filings",
    "sec",
    "press release",
    "earnings",
    "regulatory",
]

_CATALYST_INTENT_PHRASES = [
    "catalyst",
    "catalysts",
    "positive catalyst",
    "tailwind",
    "spending",
    "order",
    "orders",
    "contract",
    "backlog",
    "growth",
    "demand",
    "expansion",
]

_RISK_INTENT_PHRASES = [
    "risk",
    "risks",
    "volatile",
    "cyclical",
    "regulatory risk",
    "supply chain",
]

# Phrases that mark a thesis as too vague to build a bounded universe from.
_VAGUE_PHRASES = [
    "best stock",
    "best stocks",
    "good stock",
    "good stocks",
    "stocks to buy",
    "top stocks",
    "top companies",
    "make money",
    "hot stocks",
    "winning stocks",
    "anything",
    "any company",
    "all stocks",
]

# Exclusion connectives — words following one of these become exclusion keywords.
_EXCLUSION_CONNECTIVES = ["excluding", "except", "without", "not including", "avoid"]

_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class ParsedThesis:
    """Structured, recommendation-free representation of a market thesis."""

    normalized_text: str
    themes: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    exclusion_keywords: list[str] = field(default_factory=list)
    size_hints: list[str] = field(default_factory=list)
    source_intent_hints: list[str] = field(default_factory=list)
    catalyst_hints: list[str] = field(default_factory=list)
    risk_hints: list[str] = field(default_factory=list)
    unmatched_terms: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0
    needs_narrowing: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _contains(haystack: str, phrase: str) -> bool:
    """Whole-word/phrase containment on a space-padded lowercased string."""
    return f" {phrase.strip()} " in haystack


def _extract_exclusions(text: str) -> list[str]:
    """Pull the keyword(s) following an exclusion connective (best-effort)."""
    exclusions: list[str] = []
    lowered = text.lower()
    for connective in _EXCLUSION_CONNECTIVES:
        idx = lowered.find(connective)
        while idx != -1:
            tail = lowered[idx + len(connective) :].strip()
            # Take up to the next 3 words as the excluded phrase.
            words = [w for w in _WORD_SPLIT_RE.split(tail) if w][:3]
            if words:
                exclusions.append(words[0])
            idx = lowered.find(connective, idx + len(connective))
    return _dedup(exclusions)


def parse_thesis(
    thesis_text: str,
    *,
    region: str | None = None,
    country: str | None = None,
    sector: str | None = None,
    industry: str | None = None,
    industry_keywords: list[str] | None = None,
    market_cap_bucket: str | None = None,
) -> ParsedThesis:
    """
    Parse a natural-language market thesis + optional filters into a
    :class:`ParsedThesis`.

    The structured filter arguments (``region``, ``sector`` …) are additive —
    they seed the parse and are always honored even if the free text does not
    mention them. Returns a fully-populated, recommendation-free structure.
    """
    raw = (thesis_text or "").strip()
    normalized = re.sub(r"\s+", " ", raw)
    # Space-pad so whole-word phrase matching works at string boundaries.
    padded = f" {normalized.lower()} "

    themes: list[str] = []
    keywords: list[str] = []

    # ── Structured sector / industry filters, taxonomy-normalized ─────────
    # The admin's wording is kept AND its canonical form is added, so a thesis
    # filtered on "Luxury Goods" still reaches companies the registry tags
    # "Consumer Discretionary". A sector value that is really an industry
    # ("Watches & Jewelry") seeds BOTH lists — see sector_taxonomy.
    sectors: list[str] = []
    industries: list[str] = []
    if sector:
        sectors.append(sector.strip())
        canonical_sector = normalize_sector(sector)
        if canonical_sector:
            sectors.append(canonical_sector)
        sector_as_industry = normalize_industry(sector)
        if sector_as_industry:
            industries.append(sector_as_industry)
    if industry:
        industries.append(industry.strip())
        canonical_industry = normalize_industry(industry)
        if canonical_industry:
            industries.append(canonical_industry)
        industry_parent = normalize_sector(industry)
        if industry_parent:
            sectors.append(industry_parent)

    # ── Themes ────────────────────────────────────────────────────────────
    for theme, spec in _THEME_TABLE.items():
        matched_phrases = [p for p in spec["phrases"] if _contains(padded, p)]
        if matched_phrases:
            themes.append(theme)
            sectors.extend(spec.get("sectors", []))
            industries.extend(spec.get("industries", []))
            keywords.extend(matched_phrases)

    # ── Explicit industry keywords (structured input) ─────────────────────
    for kw in industry_keywords or []:
        kw_clean = kw.strip().lower()
        if kw_clean:
            keywords.append(kw_clean)
            # Let an explicit keyword also select a theme if it is a trigger.
            for theme, spec in _THEME_TABLE.items():
                if kw_clean in spec["phrases"] and theme not in themes:
                    themes.append(theme)
                    sectors.extend(spec.get("sectors", []))
                    industries.extend(spec.get("industries", []))

    # ── Regions ───────────────────────────────────────────────────────────
    regions: list[str] = []
    countries: list[str] = []
    if region:
        regions.append(region.strip())
    for canonical, phrases in _REGION_TABLE.items():
        if any(_contains(padded, p) or padded.strip().startswith(p) for p in phrases):
            regions.append(canonical)
    # ── Countries ─────────────────────────────────────────────────────────
    if country:
        countries.append(country.strip())
    for phrase, (canonical_country, canonical_region) in _COUNTRY_TABLE.items():
        if _contains(padded, phrase):
            countries.append(canonical_country)
            regions.append(canonical_region)

    # ── Size hints ────────────────────────────────────────────────────────
    size_hints: list[str] = []
    if market_cap_bucket:
        size_hints.append(market_cap_bucket.strip())
    for bucket, phrases in _SIZE_TABLE.items():
        if any(_contains(padded, p) for p in phrases):
            size_hints.append(bucket)

    # ── Intent hints (do not drive selection, only enrich context) ────────
    source_intent = [p for p in _SOURCE_INTENT_PHRASES if _contains(padded, p)]
    catalyst_hints = [p for p in _CATALYST_INTENT_PHRASES if _contains(padded, p)]
    risk_hints = [p for p in _RISK_INTENT_PHRASES if _contains(padded, p)]

    # ── Exclusions ────────────────────────────────────────────────────────
    exclusion_keywords = _extract_exclusions(normalized)

    # ── Unmatched terms (words that contributed no signal) ────────────────
    stop = {
        "the", "and", "or", "with", "for", "from", "that", "this", "companies",
        "company", "public", "stock", "stocks", "exposed", "to", "in", "of",
        "a", "an", "benefiting", "recent", "positive", "such", "as", "on", "by",
        "supply", "chain", "sector", "industry", "market", "segment",
    }
    matched_tokens: set[str] = set()
    for phrase in keywords + [r.lower() for r in regions] + [c.lower() for c in countries]:
        for tok in _WORD_SPLIT_RE.split(phrase):
            if tok:
                matched_tokens.add(tok)
    unmatched_terms = [
        tok
        for tok in _dedup([t for t in _WORD_SPLIT_RE.split(padded) if t])
        if tok not in matched_tokens and tok not in stop and len(tok) > 2
    ]

    # ── Confidence + needs_narrowing ──────────────────────────────────────
    signal_count = (
        len(themes) * 2
        + len(sectors)
        + len(industries)
        + len(regions)
        + len(countries)
        + len(keywords)
    )
    confidence = round(min(1.0, signal_count / 8.0), 2)

    warnings: list[str] = []
    vague = any(_contains(padded, p) for p in _VAGUE_PHRASES)
    has_structured_filter = bool(
        themes or sector or industry or industry_keywords or (sectors and sectors != [sector])
    )
    # Too vague when: an explicitly vague phrase is present, OR no theme and no
    # sector/industry filter of any kind could be derived (nothing to search on).
    needs_narrowing = vague or (not has_structured_filter and not themes)
    if needs_narrowing:
        if vague:
            warnings.append(
                "Thesis is too broad to build a bounded universe (matched a "
                "generic phrase). Add a sector, industry, theme, or region."
            )
        else:
            warnings.append(
                "Thesis did not match any known theme, sector, or industry. "
                "Add a market segment, theme, region, or explicit industry "
                "keywords to narrow the search. Supported themes and example "
                "queries are listed at "
                "GET /api/v1/market-discovery/supported-themes."
            )
    if not raw:
        warnings.append("Empty thesis text.")
        needs_narrowing = True

    return ParsedThesis(
        normalized_text=normalized,
        themes=_dedup(themes),
        sectors=_dedup(sectors),
        industries=_dedup(industries),
        regions=_dedup(regions),
        countries=_dedup(countries),
        keywords=_dedup(keywords),
        exclusion_keywords=exclusion_keywords,
        size_hints=_dedup(size_hints),
        source_intent_hints=_dedup(source_intent),
        catalyst_hints=_dedup(catalyst_hints),
        risk_hints=_dedup(risk_hints),
        unmatched_terms=unmatched_terms[:20],
        warnings=warnings,
        confidence=confidence,
        needs_narrowing=needs_narrowing,
    )
