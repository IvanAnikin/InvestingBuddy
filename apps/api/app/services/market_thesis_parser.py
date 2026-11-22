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

from app.services.discovery_filters import canonical_country, canonical_region
from app.services.exchange_registry import region_for_country
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
    "healthcare_technology": {
        "phrases": [
            "medical imaging",
            "diagnostic imaging",
            "radiology",
            "healthcare software",
            "health software",
            "healthcare technology",
            "health information technology",
            "digital health",
            "medtech",
            "medical technology",
            "medical devices",
            "clinical software",
        ],
        "sectors": ["Healthcare"],
        "industries": ["Medical Devices", "Health Information Technology"],
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
    "healthcare_technology": {
        "label": "Medical imaging / healthcare technology",
        "examples": [
            "Medical imaging and healthcare software companies",
            "European medical technology suppliers",
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
    "uk": ("United Kingdom", "Europe"),
    "australia": ("Australia", "Oceania"),
    "australian": ("Australia", "Oceania"),
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
    # Phase 27.1C — common short forms so "US semiconductor equipment companies"
    # auto-detects Country=United States (matched whole-word/phrase only).
    "us": ("United States", "North America"),
    "usa": ("United States", "North America"),
    "u.s.": ("United States", "North America"),
    "u.s.a.": ("United States", "North America"),
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
    # Phase 27.1C — canonical single-value detections derived from the PROMPT
    # TEXT, used to auto-fill the admin's Region / Country / Sector / Industry
    # selectors. These describe what the text says; an explicit form value always
    # overrides the corresponding detection (see ``parse_thesis`` precedence).
    region: str | None = None
    country: str | None = None
    sector: str | None = None
    industry: str | None = None
    theme: str | None = None
    extraction_source: str = "prompt_text"
    #: V3.21.1 (ADR-107) — the segment a thesis names when no fixed theme matches:
    #: sanitised terms only, never the free text. Empty when a theme matched.
    segment_terms: list[str] = field(default_factory=list)

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
    """Whole-word/phrase containment on a lowercased string.

    Bounded by any non-alphanumeric character, not only a space: "Banks, payments and
    fintech" and "Utilities, grid …" put a comma straight after the phrase, and the
    space-only test never matched it (QA campaign 2026-11, independent review).
    """
    needle = phrase.strip()
    if not needle:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


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


# ---------------------------------------------------------------------------
# V3.21.1 — free-form segment terms (ADR-107)
#
# A thesis outside the fixed themes ("US small-cap cybersecurity software companies")
# still names a segment. The segment is the run of content words left once stop-words,
# geography, size and style words are removed. Deterministic, bounded and sanitised:
# only these terms ever travel to recall, the web planner or the exposure check —
# never the free text itself.
# ---------------------------------------------------------------------------

MAX_SEGMENT_TERMS = 6
SEGMENT_TERM_MIN_CHARS = 3
SEGMENT_TERM_MAX_CHARS = 40
_SEGMENT_TERM_OK = re.compile(r"^[a-z0-9][a-z0-9 -]*[a-z0-9]$")

#: Words that never name a segment: grammar, request verbs, generic company nouns.
_SEGMENT_STOP_WORDS = frozenset(
    """
    the and or with for from that this these those such as on by to in of a an at into
    onto about over under below above between within across via per plus also both either
    is are be been being was were has have having their they them its it which who whose
    what where when how than then there here some any all every each other more most less
    few many much very just only strictly must preferably ideally possibly perhaps maybe
    find finding look looking search searching show list give want need wanted interested
    me my we our us you your i like including include includes etc focused focus focusing
    based headquartered domiciled operating operate active exposure exposed exposures
    benefiting benefit benefitting related relating linked tied supply chain
    developing producing supplying providing offering serving using used making building
    doing working selling sell sells
    company companies firm firms business businesses names name player players stock stocks
    share shares equity equities issuer issuers listed listing listings public publicly
    traded trading exchange exchanges ticker tickers securities
    sector sectors industry industries market markets segment segments space niche theme
    themes area areas field fields universe opportunity opportunities idea ideas thing things
    stuff investment investments invest investing investor investors portfolio portfolios
    buy buying selling hold holding returns return money profit profits upside
    no not none nothing something anything everything whatever word words term terms
    unrelated random various general generic misc miscellaneous different similar same
    kind kinds type types sort sorts example examples
    """.split()
)
#: Generic category nouns. Dropped when a more specific word names the segment
#: ("cybersecurity software" → "cybersecurity"); kept when they are all there is.
_SEGMENT_GENERIC_NOUNS = frozenset(
    """
    software services service solutions solution technology technologies tech products
    product equipment systems system providers provider vendors vendor makers maker
    manufacturers manufacturer suppliers supplier producers producer developers developer
    platforms platform tools tool hardware operators operator contractors contractor
    specialists specialist group groups
    """.split()
)
#: Size and style words: they are constraints, never the segment.
_SEGMENT_SIZE_STYLE_WORDS = frozenset(
    """
    small smaller smallest micro mid middle large larger largest mega big bigger tiny
    cap caps capitalisation capitalization microcap microcaps smallcap smallcaps midcap
    midcaps largecap largecaps megacap megacaps smid sized size nano nanocap
    growth growing grow fast faster rapid rapidly quickly strongly high hyper expanding
    value cheap undervalued overvalued valuation quality dividend dividends income yield
    momentum profitable profitability unprofitable cash generative revenue revenues sales
    margin margins earnings catalyst catalysts positive negative recent recently
    good great best top strong leading leader leaders emerging innovative promising
    attractive interesting new established mature young early stage late
    """.split()
)
#: V3.21.1b (ADR-115) — descriptor words of a qualifier ("growing recurring revenue",
#: "under-followed by analysts", "long backlogs"): they say something ABOUT the companies,
#: never what the companies do. Not "pipeline" ("gas pipeline" is a segment) and not
#: "government" ("government IT services" is one): those rely on the clause cut below.
_SEGMENT_DESCRIPTOR_WORDS = frozenset(
    """
    recurring followed following underfollowed analyst analysts coverage covered uncovered
    undercovered researched underresearched overlooked neglected ignored obscure lesser
    known unknown mispriced radar tailwind tailwinds headwind headwinds spending demand
    backlog backlogs
    """.split()
)
#: Every word that can never anchor a segment by itself: stop-words, size/style words,
#: descriptors and generic category nouns. Used by the synonym filter (ADR-115).
SEGMENT_NON_ANCHOR_WORDS = frozenset(
    _SEGMENT_STOP_WORDS | _SEGMENT_SIZE_STYLE_WORDS | _SEGMENT_DESCRIPTOR_WORDS
    | _SEGMENT_GENERIC_NOUNS
)
#: V3.21.1b (ADR-115) — connectives that open a QUALIFIER clause ("… with growing recurring
#: revenue", "… that benefit from …", "… under-followed by analysts"). The text is cut at
#: the first one once a specific content word precedes it, so the segment is what comes
#: before. "in", "on", "for" and "focused on" never cut ("OT security for utilities").
_QUALIFIER_CUT_RE = re.compile(
    r"\b(?:with|having|that|which|who|whose|where|showing|benefit(?:t)?ing|driven\s+by"
    r"|thanks\s+to|due\s+to|exposed\s+to)\b"
    r"|\b(?:under|well|less|lesser|poorly|thinly|barely)-(?:followed|researched|covered"
    r"|known|analy[sz]ed)\b"
    r"|\bunder-the-radar\b",
    re.IGNORECASE,
)
#: Geography words beyond the parser's own region/country tables.
_SEGMENT_GEO_WORDS = frozenset(
    """
    global worldwide international domestic regional local western eastern northern
    southern central developed emerging nordics scandinavia scandinavian dach cee emea
    apac latam europe european eurozone asia asian america american americas north south
    east west african africa oceania australasia norway norwegian finland finnish belgium
    belgian austria austrian ireland irish portugal portuguese poland polish spanish
    mexico mexican brazil brazilian korea korean zealand israel israeli singapore
    country countries region regions
    """.split()
)


def _geography_tokens() -> frozenset[str]:
    from app.services.exchange_registry import COUNTRY_TO_REGION

    words: set[str] = set(_SEGMENT_GEO_WORDS)
    for phrase in [*_COUNTRY_TABLE, *(p for ps in _REGION_TABLE.values() for p in ps)]:
        words.update(t for t in _WORD_SPLIT_RE.split(phrase.lower()) if t)
    for country in COUNTRY_TO_REGION:
        words.update(t for t in _WORD_SPLIT_RE.split(country.lower()) if t)
    return frozenset(words)


def sanitise_segment_term(value: Any) -> str | None:
    """A segment term in canonical form, or ``None``.

    Lower case, single spaces; letters, digits, spaces and hyphens only; 3–40 characters.
    Anything else (quotes, URLs, punctuation, markup) is refused, never repaired into a
    different string.
    """
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value).strip().lower()
    if not (SEGMENT_TERM_MIN_CHARS <= len(text) <= SEGMENT_TERM_MAX_CHARS):
        return None
    if not _SEGMENT_TERM_OK.match(text):
        return None
    if not re.search(r"[a-z]", text):
        return None
    return text


def _segment_runs(original: str, skip: set[str]) -> list[list[str]]:
    """Runs of contiguous content tokens, in text order."""
    runs: list[list[str]] = []
    current: list[str] = []
    for match in re.finditer(r"[A-Za-z0-9]+|[^A-Za-z0-9\s-]+|\s+|-", original):
        piece = match.group(0)
        if piece.isspace() or piece == "-":
            continue
        token = piece.lower()
        keep = (
            token[0].isalnum()
            and token not in skip
            and not token.isdigit()
            and (len(token) > 2 or (len(piece) == 2 and piece.isupper()))
        )
        if keep:
            current.append(token)
            continue
        if current:
            runs.append(current)
        current = []
    if current:
        runs.append(current)
    return runs


def _cut_at_qualifier(text: str, skip: set[str]) -> str:
    """The text before its first qualifier clause, once a specific content word precedes
    it (ADR-115). With no specific word before a connective ("companies with exposure to
    cybersecurity") the connective is not a cut and the next one is tried."""
    for match in _QUALIFIER_CUT_RE.finditer(text or ""):
        prefix = text[: match.start()]
        if any(t not in _SEGMENT_GENERIC_NOUNS for run in _segment_runs(prefix, skip)
               for t in run):
            return prefix
    return text


def extract_segment_terms(
    thesis_text: str,
    *,
    matched_tokens: set[str] | None = None,
    exclusion_keywords: list[str] | None = None,
    industry_keywords: list[str] | None = None,
) -> list[str]:
    """V3.21.1 — the segment a thesis names, as at most six sanitised terms.

    Deterministic: content-word runs of the text after stop-words, geography, size and
    style words, matched theme/region tokens and excluded words are removed. Generic
    category nouns ("software", "suppliers") are dropped beside a more specific word.
    A run longer than three words is split into pairs. Explicit ``industry_keywords``
    come first. An empty list means the text names no segment.

    V3.21.1b (ADR-115): the text is first cut at its first qualifier clause ("with …",
    "that …", "under-followed …") once a specific word precedes it, and descriptor words
    ("recurring", "analysts", "backlogs") are dropped, so a qualifier never becomes a
    hard industry term.
    """
    skip: set[str] = set(
        _SEGMENT_STOP_WORDS | _SEGMENT_SIZE_STYLE_WORDS | _SEGMENT_DESCRIPTOR_WORDS
        | _geography_tokens()
    )
    skip.update(matched_tokens or ())
    for phrase in exclusion_keywords or []:
        skip.update(t for t in _WORD_SPLIT_RE.split(phrase.lower()) if t)
    for phrase in [*_VAGUE_PHRASES, *_SOURCE_INTENT_PHRASES, *_CATALYST_INTENT_PHRASES,
                   *_RISK_INTENT_PHRASES]:
        skip.update(t for t in _WORD_SPLIT_RE.split(phrase) if t)

    # Words following an exclusion connective are what the user does NOT want.
    excluded_text = (thesis_text or "")
    for connective in _EXCLUSION_CONNECTIVES:
        excluded_text = re.sub(
            rf"\b{re.escape(connective)}\b(?:\s+(?:the|a|an|any))?\s+[A-Za-z0-9-]+",
            " , ", excluded_text, flags=re.IGNORECASE,
        )
    excluded_text = re.sub(r"\bnon-[A-Za-z0-9]+", " , ", excluded_text, flags=re.IGNORECASE)
    excluded_text = _cut_at_qualifier(excluded_text, skip)

    terms: list[str] = []
    for keyword in industry_keywords or []:
        clean = sanitise_segment_term(keyword)
        if clean and clean not in terms:
            terms.append(clean)

    for run in _segment_runs(excluded_text, skip):
        specific = [t for t in run if t not in _SEGMENT_GENERIC_NOUNS]
        words = specific or run
        if len(words) <= 3:
            chunks = [words]
        else:
            chunks = [words[i : i + 2] for i in range(0, len(words), 2)]
            if len(chunks[-1]) == 1:
                chunks[-2] = [*chunks[-2], *chunks.pop()]
        for chunk in chunks:
            clean = sanitise_segment_term(" ".join(chunk))
            if clean and clean not in terms:
                terms.append(clean)
    return terms[:MAX_SEGMENT_TERMS]


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
    # Sectors/industries implied by the TEXT themes only (kept separate from the
    # merged ``sectors``/``industries`` lists, which also carry explicit seeds)
    # so the canonical single-value detections below reflect the prompt, not the
    # admin's structured filters.
    text_theme_sectors: list[str] = []
    text_theme_industries: list[str] = []

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
            text_theme_sectors.extend(spec.get("sectors", []))
            text_theme_industries.extend(spec.get("industries", []))
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

    # ── Regions & countries — prompt-text detection ───────────────────────
    # First, what the PROMPT TEXT alone says (used to auto-fill the selectors).
    # Geography is matched on a copy with list punctuation removed: "the US, UK and
    # Australia" must read as three countries, not one ("us," never matched "us").
    geo = " " + re.sub(r"[,;:()\[\]/\"]+", " ", normalized.lower()) + " "
    geo = re.sub(r"\s+", " ", geo)
    text_regions: list[str] = []
    for canon, phrases in _REGION_TABLE.items():
        if any(_contains(geo, p) or geo.strip().startswith(p) for p in phrases):
            text_regions.append(canon)
    text_countries: list[str] = []
    for phrase, (c_country, c_region) in _COUNTRY_TABLE.items():
        if _contains(geo, phrase):
            text_countries.append(c_country)
            text_regions.append(c_region)
    text_regions = _dedup(text_regions)
    text_countries = _dedup(text_countries)

    # Canonical single-value detections for selector auto-fill.
    detected_country = text_countries[0] if text_countries else None
    detected_region = (
        region_for_country(detected_country)
        if detected_country
        else (text_regions[0] if text_regions else None)
    )
    detected_theme = themes[0] if themes else None
    # Prefer an industry implied by a matched keyword (more specific than the
    # theme's first industry, e.g. "watch" -> "Watches & Jewelry").
    # Keywords are taken in the order the TEXT states them, not the theme table's order:
    # "utilities with grid exposure" names utilities first.
    def _text_position(phrase: str) -> int:
        # Word boundaries, not padded spaces: "Utilities, grid equipment …" puts a comma
        # after the first keyword (independent review).
        m = re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", padded)
        return m.start() if m else len(padded)

    text_ordered_keywords = sorted(keywords, key=_text_position)
    detected_industry = next(
        (ci for ci in (normalize_industry(k) for k in text_ordered_keywords) if ci), None
    ) or next(
        (normalize_industry(i) or i for i in text_theme_industries if i), None
    )
    # The sector is the detected industry's own sector, so the pair can never contradict
    # itself (live QA 2026-11: "Electrical Equipment" was auto-filled under "Utilities",
    # the grid theme's first sector). Only without an industry does the theme's first
    # sector stand.
    detected_sector = normalize_sector(detected_industry) or next(
        (cs for cs in (normalize_sector(s) for s in text_theme_sectors) if cs),
        None,
    )

    # ── Precedence: an explicit form value overrides the prompt detection ──
    # Country is strict; when it is set, the region follows the country and the
    # prompt's own region detection is not allowed to broaden the search.
    conflict_warnings: list[str] = []
    explicit_region = canonical_region(region) or (region.strip() if region else None)
    explicit_country = canonical_country(country) or (
        country.strip() if country else None
    )
    explicit_sector = sector.strip() if sector and sector.strip() else None

    eff_country = explicit_country or detected_country
    eff_region: str | None
    if explicit_region:
        eff_region = explicit_region
    elif explicit_country:
        eff_region = region_for_country(explicit_country) or detected_region
    else:
        eff_region = detected_region
    eff_sector = (
        normalize_sector(explicit_sector) if explicit_sector else detected_sector
    )
    explicit_industry = industry.strip() if industry and industry.strip() else None
    eff_industry = (
        (normalize_industry(explicit_industry) or explicit_industry)
        if explicit_industry
        else detected_industry
    )

    def _diff(a: str | None, b: str | None) -> bool:
        return bool(a and b and a.strip().lower() != b.strip().lower())

    if _diff(explicit_country, detected_country):
        conflict_warnings.append(
            f"Prompt mentions {detected_country}, but explicit "
            f"Country={explicit_country} was selected."
        )
    if _diff(explicit_region, detected_region):
        conflict_warnings.append(
            f"Prompt mentions {detected_region}, but explicit "
            f"Region={explicit_region} was selected."
        )
    if (
        explicit_sector
        and detected_sector
        and normalize_sector(explicit_sector) != detected_sector
    ):
        conflict_warnings.append(
            f"Prompt implies sector {detected_sector}, but explicit "
            f"Sector={explicit_sector} was selected."
        )

    # ── Effective filter lists the universe builder acts on ────────────────
    regions: list[str] = [eff_region] if eff_region else []
    countries: list[str] = [eff_country] if eff_country else []
    # A prompt that names SEVERAL countries ("the US, UK and Australia") is not a
    # request for the first one. With no explicit form value, keep them all.
    if not explicit_country and not explicit_region and len(text_countries) > 1:
        countries = list(text_countries)
        regions = list(text_regions)
        eff_country = None  # the single-value selectors cannot hold several countries
        eff_region = None

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

    # ── V3.21.1 — segment terms, only when no fixed theme matched ─────────
    segment_terms: list[str] = []
    if not themes:
        segment_terms = extract_segment_terms(
            normalized,
            matched_tokens=matched_tokens,
            exclusion_keywords=exclusion_keywords,
            industry_keywords=industry_keywords,
        )

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
        or segment_terms
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

    # Surface any explicit-vs-prompt conflicts (the explicit choice is kept).
    warnings.extend(conflict_warnings)

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
        region=eff_region,
        country=eff_country,
        sector=eff_sector,
        industry=eff_industry,
        theme=detected_theme,
        extraction_source="prompt_text",
        segment_terms=segment_terms,
    )
