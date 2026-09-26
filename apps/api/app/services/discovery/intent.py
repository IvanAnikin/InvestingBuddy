"""The Discovery Intent — a thesis as a filter HYPOTHESIS, never as company facts. V3.19.2.

THE DEFECT
==========
*"small cap growing european luxury companies"* returned LVMH, Hermès, Richemont and Kering,
and the discovery council described them as small-cap. ``size_hints`` was parsed and then
read by nothing; "growing" was a catalyst-intent phrase worth four points. The user's words
went straight from the query into the prose about the companies.

WHAT THIS DOES
==============
It structures what the user ASKED FOR — and only that — from closed vocabularies:

* themes, sectors, industries, regions, countries (the parser's tables, now multi-valued);
* materials (``macro.commodities``), end markets (the V3.18 thesis dimensions), catalysts
  and a time horizon;
* size, growth and profitability as ``Constraint``s, each HARD or SOFT by a declared,
  deterministic rule whose basis is recorded and shown.

Every value comes from a table in this repository; no model writes a filter. Words the
tables do not cover are returned in ``unmatched_terms`` and shown, never silently dropped.
A constraint is ``verification_required`` by construction: it becomes an attribute of a
company only in ``discovery.constraints``, from evidence.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from app.services.exchange_registry import COUNTRY_TO_REGION, region_for_country
from app.services.macro.commodities import COMMODITIES
from app.services.market_thesis_parser import ParsedThesis, parse_thesis

SCHEMA = "discovery_intent/1"

HARD = "hard"
SOFT = "soft"

SIZE_BUCKETS: tuple[str, ...] = ("micro_cap", "small_cap", "mid_cap", "large_cap", "mega_cap")

# ── Size, growth, profitability vocabularies ───────────────────────────────── #

#: (pattern, buckets, is_comparative). Most specific first; the first match at a span wins.
_SIZE_TERMS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    (r"micro[\s-]?caps?", ("micro_cap",), False),
    (r"small[\s-]?(?:and|&|/)[\s-]?mid[\s-]?caps?|smid[\s-]?caps?|\bsmid\b",
     ("small_cap", "mid_cap"), False),
    (r"small[\s-]?caps?", ("small_cap",), False),
    (r"mid[\s-]?caps?", ("mid_cap",), False),
    (r"mega[\s-]?caps?", ("mega_cap",), False),
    (r"large[\s-]?caps?", ("large_cap", "mega_cap"), False),
    # Comparatives and a bare size adjective name a DIRECTION, not a band.
    (r"\bsmaller\b", ("micro_cap", "small_cap", "mid_cap"), True),
    (r"\bsmall\b(?=\s+(?:\w+\s+){0,4}(?:compan(?:y|ies)|firms?|issuers?|producers?|"
     r"miners?|developers?|names|stocks|businesses))",
     ("micro_cap", "small_cap", "mid_cap"), True),
    (r"\blarger\b", ("large_cap", "mega_cap"), True),
)

_HIGH_GROWTH = r"high[\s-]growth|fast[\s-]growing|rapidly[\s-]growing|hyper[\s-]?growth"
_GROWTH = (
    r"\bgrowing\b|\bgrowth\s+(?:compan(?:y|ies)|stocks|names|businesses)"
    r"|\bexpanding\s+(?:revenues?|sales)"
)
_PROFITABLE = r"\bprofitable\b|\bcash[\s-]generative\b"

# ── Hardness cues ──────────────────────────────────────────────────────────── #

_HARD_CUES = ("only", "strictly", "must", "exclusively", "solely", "just")
_SOFT_CUES = (
    "prefer", "preferably", "preferred", "ideally", "ideal", "if possible", "bonus",
    "nice to have", "ideally with", "where possible", "possibly", "perhaps",
)
#: A clause ends at a full stop, semicolon or colon, or at a comma that opens a qualifying
#: clause ("…, ideally growing"). The comma is the boundary; the cue stays in the NEXT clause.
_CLAUSE_SPLIT = re.compile(r"[.;:]|,(?=\s+(?:but|and|preferably|ideally|if possible)\b)")

# ── Catalysts and horizon ──────────────────────────────────────────────────── #

CATALYST_VOCABULARY: dict[str, str] = {
    "permitting": r"\bpermit(?:s|ting)?\b|\bpermitted\b|\blicen[cs]ing\b",
    "government_funding": (
        r"government[\s-](?:funding|grants?|loans?|support|backing)|\bgrants?\b"
        r"|loan guarantees?|\bsubsid(?:y|ies)\b|\bdoe\b|\bdod\b|public funding"
    ),
    "offtake": r"\boff[\s-]?takes?\b",
    "production": (
        r"\bproduction\b|first production|commissioning|ramp[\s-]?up|\bstart[\s-]?up\b"
    ),
    "contracts": r"\bcontracts?\b|\bawards?\b",
    "orders": r"\borders?\b|\bbacklog\b",
    "regulatory_approval": r"\bapprovals?\b|\bapproved\b",
}
_HORIZON_RANGE = re.compile(
    r"(\d{1,2})\s*(?:–|-|to)\s*(\d{1,2})\s*(?:years?|yrs?)", re.IGNORECASE
)
_HORIZON_SINGLE = re.compile(
    r"(?:within|over|in|next)\s+(?:the\s+next\s+)?(\d{1,2})\s*(?:years?|yrs?)", re.IGNORECASE
)

# ── Themes the intent adds to the parser's ────────────────────────────────── #

_CRITICAL_MATERIALS = re.compile(
    r"critical[\s-]+(?:materials?|minerals?|metals?)|strategic[\s-]+(?:materials?|minerals?|metals?)",
    re.IGNORECASE,
)
#: Parser themes that describe what a company's products are USED IN. When the thesis
#: names materials, these are end markets of the materials — the company is a materials
#: company — unless the text names them as the kind of company wanted ("semiconductor
#: companies").
_END_MARKET_THEMES: dict[str, str] = {
    "semiconductors": "semiconductors",
    "ai_infrastructure": "ai_data_centres",
    "grid_electrification": "electrification",
    "defense": "defence",
    "nuclear_energy": "nuclear_energy",
}
_COMPANY_NOUN = (
    r"(?:\s+\w+){0,2}\s+(?:compan(?:y|ies)|firms?|makers?|manufacturers?|producers?|miners?|developers?|"
    r"suppliers?|stocks|names|businesses|equipment)"
)

_REGION_WORDS: dict[str, str] = {
    "europe": "Europe", "european": "Europe", "eurozone": "Europe", "nordic": "Europe",
    "north america": "North America", "north american": "North America",
    "asia": "Asia", "asian": "Asia", "oceania": "Oceania", "australasia": "Oceania",
    "latin america": "South America", "south america": "South America",
    "africa": "Africa", "african": "Africa", "japan": "Japan", "japanese": "Japan",
    "china": "China", "chinese": "China",
}
_COUNTRY_WORDS: dict[str, str] = {
    "australia": "Australia", "australian": "Australia", "canada": "Canada",
    "canadian": "Canada", "united states": "United States", "usa": "United States",
    "u.s.": "United States", "us": "United States", "american": "United States",
    "germany": "Germany", "german": "Germany", "france": "France", "french": "France",
    "united kingdom": "United Kingdom", "uk": "United Kingdom", "britain": "United Kingdom",
    "british": "United Kingdom", "italy": "Italy", "italian": "Italy",
    "spain": "Spain", "spanish": "Spain", "sweden": "Sweden", "swedish": "Sweden",
    "norway": "Norway", "norwegian": "Norway", "finland": "Finland", "finnish": "Finland",
    "denmark": "Denmark", "danish": "Denmark", "netherlands": "Netherlands",
    "dutch": "Netherlands", "switzerland": "Switzerland", "swiss": "Switzerland",
    "belgium": "Belgium", "austria": "Austria", "ireland": "Ireland", "portugal": "Portugal",
    "poland": "Poland", "new zealand": "New Zealand", "south africa": "South Africa",
    "brazil": "Brazil", "mexico": "Mexico", "india": "India", "south korea": "South Korea",
    "korea": "South Korea", "taiwan": "Taiwan", "hong kong": "Hong Kong",
}


@dataclass(frozen=True)
class Constraint:
    """One thing the user asked for that a company must be SHOWN to satisfy."""

    key: str
    requested: tuple[str, ...]
    hardness: str
    hardness_basis: str
    phrase: str
    verification_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["requested"] = list(self.requested)
        return out


@dataclass(frozen=True)
class DiscoveryIntent:
    text: str
    themes: tuple[str, ...] = ()
    sectors: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    materials: tuple[str, ...] = ()
    end_markets: tuple[str, ...] = ()
    catalysts: tuple[str, ...] = ()
    horizon_years: tuple[int, int] | None = None
    size: Constraint | None = None
    growth: Constraint | None = None
    profitability: Constraint | None = None
    geography: Constraint | None = None
    industry: Constraint | None = None
    exclusions: tuple[str, ...] = ()
    unmatched_terms: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    needs_narrowing: bool = False
    schema: str = SCHEMA

    #: The listing constraint is always hard: discovery returns listed securities only.
    @property
    def listing(self) -> Constraint:
        return Constraint(
            key="listing",
            requested=("listed_security",),
            hardness=HARD,
            hardness_basis="always: discovery returns only verified listed securities",
            phrase="",
        )

    def constraints(self) -> list[Constraint]:
        return [
            c
            for c in (self.listing, self.geography, self.industry, self.size, self.growth,
                      self.profitability)
            if c is not None
        ]

    def hard(self) -> list[Constraint]:
        return [c for c in self.constraints() if c.hardness == HARD]

    def soft(self) -> list[Constraint]:
        return [c for c in self.constraints() if c.hardness == SOFT]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "text": self.text,
            "themes": list(self.themes),
            "sectors": list(self.sectors),
            "industries": list(self.industries),
            "regions": list(self.regions),
            "countries": list(self.countries),
            "materials": list(self.materials),
            "end_markets": list(self.end_markets),
            "catalysts": list(self.catalysts),
            "horizon_years": list(self.horizon_years) if self.horizon_years else None,
            "constraints": [c.to_dict() for c in self.constraints()],
            "exclusions": list(self.exclusions),
            "unmatched_terms": list(self.unmatched_terms),
            "warnings": list(self.warnings),
            "needs_narrowing": self.needs_narrowing,
        }

    def universe_filter(self) -> dict[str, Any]:
        """The dict the curated universe builder reads, with UNION geography semantics.

        The legacy parser treats a country as strict and a region as the fallback; a
        thesis like "Europe, North America and Australia" means any of the three, so a
        named country contributes its REGION to a region-only filter and the constraint
        check (``discovery.constraints.geography``) applies the exact union later.
        """
        regions = list(self.regions)
        countries: list[str] = []
        if regions:
            for country in self.countries:
                region = region_for_country(country)
                if region and region not in regions:
                    regions.append(region)
        else:
            # Only countries named: the legacy strict country filter is exactly right.
            countries = list(self.countries)
        return {
            "themes": list(self.themes),
            "sectors": list(self.sectors),
            "industries": list(self.industries),
            "regions": regions,
            "countries": countries,
            "exclusion_keywords": list(self.exclusions),
            "needs_narrowing": self.needs_narrowing,
            "warnings": list(self.warnings),
        }


# ── helpers ─────────────────────────────────────────────────────────────────── #


def _padded(text: str) -> str:
    return f" {re.sub(r'\s+', ' ', (text or '').lower())} "


def _has_word(padded: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", padded) is not None


def _clause_of(text: str, start: int) -> tuple[str, int]:
    """The clause containing ``start``, and ``start``'s offset within it."""
    begin = 0
    for match in _CLAUSE_SPLIT.finditer(text):
        if match.end() <= start:
            begin = match.end()
        elif match.start() >= start:
            return text[begin : match.start()], start - begin
    return text[begin:], start - begin


def hardness_for(text: str, start: int, *, comparative: bool = False) -> tuple[str, str]:
    """HARD or SOFT for a term at ``start`` in lower-cased ``text``, with the rule's basis.

    Rules, first match wins (spec §5.1):
      1. a hard cue within three words before the term → hard;
      2. a soft cue anywhere earlier in the term's clause → soft;
      3. a comparative ("smaller", "larger", a bare "small") → soft;
      4. otherwise → hard: the user named it as a property of what they want.
    """
    clause, offset = _clause_of(text, start)
    before = clause[:offset]
    words_before = re.findall(r"[a-z]+", before)[-3:]
    for cue in _HARD_CUES:
        if cue in words_before:
            return HARD, f"'{cue}' before the term marks it hard"
    for cue in _SOFT_CUES:
        if re.search(rf"\b{re.escape(cue)}\b", before):
            return SOFT, f"'{cue}' earlier in the clause marks it soft"
    if comparative:
        return SOFT, "a comparative or bare size word names a direction, not a band"
    return HARD, "default: a bare term names a property of the companies wanted"


def _size_constraint(text: str) -> Constraint | None:
    taken: list[tuple[int, int]] = []
    buckets: list[str] = []
    phrases: list[str] = []
    hardness: tuple[str, str] | None = None
    for pattern, bands, comparative in _SIZE_TERMS:
        for match in re.finditer(pattern, text):
            if any(match.start() < end and match.end() > begin for begin, end in taken):
                continue
            taken.append((match.start(), match.end()))
            for band in bands:
                if band not in buckets:
                    buckets.append(band)
            phrases.append(match.group(0).strip())
            this = hardness_for(text, match.start(), comparative=comparative)
            # Several size words: one hard word makes the constraint hard.
            if hardness is None or (this[0] == HARD and hardness[0] == SOFT):
                hardness = this
    if not buckets or hardness is None:
        return None
    ordered = tuple(b for b in SIZE_BUCKETS if b in buckets)
    return Constraint("size", ordered, hardness[0], hardness[1], ", ".join(phrases))


def _growth_constraint(text: str) -> Constraint | None:
    match = re.search(_HIGH_GROWTH, text)
    requested = "high_growth"
    if match is None:
        match = re.search(_GROWTH, text)
        requested = "growing"
    if match is None:
        return None
    hardness, basis = hardness_for(text, match.start())
    return Constraint("growth", (requested,), hardness, basis, match.group(0).strip())


def _profitability_constraint(text: str) -> Constraint | None:
    match = re.search(_PROFITABLE, text)
    if match is None:
        return None
    hardness, basis = hardness_for(text, match.start())
    return Constraint("profitability", ("profitable",), hardness, basis, match.group(0).strip())


def _materials(padded: str) -> list[str]:
    found: list[str] = []
    for commodity in COMMODITIES:
        pattern = r"\b(?:" + "|".join(commodity.patterns) + r")\b"
        if re.search(pattern, padded, re.IGNORECASE) and commodity.slug not in found:
            found.append(commodity.slug)
    return found


def _geography(
    padded: str, *, region: str | None, country: str | None
) -> tuple[list[str], list[str]]:
    regions: list[str] = []
    countries: list[str] = []
    for word, named_region in _REGION_WORDS.items():
        if _has_word(padded, word) and named_region not in regions:
            regions.append(named_region)
    for word, named_country in _COUNTRY_WORDS.items():
        if (
            named_country in COUNTRY_TO_REGION
            and _has_word(padded, word)
            and named_country not in countries
        ):
            # "American" names the United States unless "North American" named a region.
            if word == "american" and "North America" in regions:
                continue
            countries.append(named_country)
    # An explicit selector is kept even if the text never named it. A region the PARSER
    # derived from a country is not: "Swiss watch companies" means Switzerland, and
    # widening it to Europe would admit every European watchmaker.
    from app.services.discovery_filters import canonical_country, canonical_region

    explicit_region = canonical_region(region) if region else None
    explicit_country = canonical_country(country) if country else None
    if explicit_region and explicit_region not in regions:
        regions.append(explicit_region)
    if explicit_country and explicit_country not in countries:
        countries.append(explicit_country)
    # A country INSIDE a named region narrows nothing and widens nothing ("European …
    # in France" is still a France filter only if the user picked the country): keep both
    # and let the union apply. A country OUTSIDE every named region widens the geography,
    # which is exactly "Europe, North America and Australia".
    return regions, countries


def _dedup(items: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(i for i in items if i))


def build_intent(
    thesis_text: str,
    *,
    parsed: ParsedThesis | None = None,
    region: str | None = None,
    country: str | None = None,
    sector: str | None = None,
    industry: str | None = None,
    industry_keywords: list[str] | None = None,
    market_cap_bucket: str | None = None,
) -> DiscoveryIntent:
    """The Discovery Intent for a thesis. Deterministic, network-free, never raises."""
    from app.services.director.thesis import dimensions_in

    parsed = parsed or parse_thesis(
        thesis_text,
        region=region,
        country=country,
        sector=sector,
        industry=industry,
        industry_keywords=industry_keywords,
        market_cap_bucket=market_cap_bucket,
    )
    text = re.sub(r"\s+", " ", (thesis_text or "").lower()).strip()
    padded = _padded(thesis_text)

    materials = _materials(padded)
    themes = list(parsed.themes)
    sectors = list(parsed.sectors)
    industries = list(parsed.industries)
    theme_dimensions = {_END_MARKET_THEMES[t] for t in parsed.themes if t in _END_MARKET_THEMES}
    # An end market is where a company's products GO. When the thesis names the kind of
    # company directly ("US semiconductor companies"), that is the industry, not a market.
    end_markets = [
        d for d in dimensions_in(thesis_text)
        if d != "critical_materials" and d not in theme_dimensions
    ]
    warnings = list(parsed.warnings)

    if _CRITICAL_MATERIALS.search(text) and "critical_materials" not in themes:
        themes.insert(0, "critical_materials")
    if materials:
        # A materials thesis: industry themes that are really END MARKETS of the
        # materials are demoted, unless the text asks for companies OF that kind.
        for theme, market in _END_MARKET_THEMES.items():
            if theme not in themes:
                continue
            words = {
                "semiconductors": r"semiconductors?|chips?",
                "ai_infrastructure": r"ai|data[\s-]?cent(?:er|re)s?",
                "grid_electrification": r"grid|electrification|electrical",
                "defense": r"defen[cs]e|aerospace",
                "nuclear_energy": r"nuclear|uranium",
            }[theme]
            if not re.search(rf"\b(?:{words})\b{_COMPANY_NOUN}", text):
                themes.remove(theme)
                if market not in end_markets:
                    end_markets.append(market)
        if "mining_materials" not in themes:
            themes.append("mining_materials")
        for value in ("Materials",):
            if value not in sectors:
                sectors.append(value)
        if "Metals & Mining" not in industries:
            industries.append("Metals & Mining")
        # Sectors/industries that belonged only to a demoted theme are dropped.
        from app.services.market_thesis_parser import _THEME_TABLE

        kept_sectors = {s for t in themes for s in _THEME_TABLE.get(t, {}).get("sectors", [])}
        kept_industries = {
            i for t in themes for i in _THEME_TABLE.get(t, {}).get("industries", [])
        }
        explicit = {s for s in (sector, industry) if s}
        sectors = [s for s in sectors if s in kept_sectors or s == "Materials" or s in explicit]
        industries = [
            i for i in industries
            if i in kept_industries or i == "Metals & Mining" or i in explicit
        ]

    regions, countries = _geography(padded, region=region, country=country)
    catalysts = [key for key, pattern in CATALYST_VOCABULARY.items() if re.search(pattern, text)]
    horizon: tuple[int, int] | None = None
    match = _HORIZON_RANGE.search(text)
    if match:
        low, high = sorted((int(match.group(1)), int(match.group(2))))
        horizon = (low, high)
    else:
        single = _HORIZON_SINGLE.search(text)
        if single:
            horizon = (0, int(single.group(1)))

    size = _size_constraint(text)
    if size is None and market_cap_bucket:
        bucket = market_cap_bucket.strip().lower().replace("-", "_").replace(" ", "_")
        if not bucket.endswith("_cap"):
            bucket += "_cap"
        if bucket in SIZE_BUCKETS:
            size = Constraint(
                "size", (bucket,), HARD, "an explicit size selector is a hard filter",
                market_cap_bucket,
            )
    growth = _growth_constraint(text)
    profitability = _profitability_constraint(text)

    geography = None
    if regions or countries:
        geography = Constraint(
            "geography",
            _dedup([*regions, *countries]),
            HARD,
            "a named region or country is a hard filter",
            ", ".join([*regions, *countries]),
        )
    industry_constraint = None
    if themes or materials:
        industry_constraint = Constraint(
            "industry",
            _dedup([*themes, *materials]),
            HARD,
            "a named industry, theme or material is a hard filter",
            ", ".join([*themes, *materials]),
        )

    consumed = set()
    for phrase in [*themes, *materials, *regions, *countries, *catalysts, *end_markets]:
        consumed.update(re.findall(r"[a-z0-9]+", phrase.lower().replace("_", " ")))
    for constraint in (size, growth, profitability):
        if constraint is not None:
            consumed.update(re.findall(r"[a-z0-9]+", constraint.phrase.lower()))
    for commodity in COMMODITIES:
        if commodity.slug in materials:
            consumed.update(re.findall(r"[a-z]+", " ".join(commodity.patterns)))
    for word in [*_REGION_WORDS, *_COUNTRY_WORDS, *_HARD_CUES, *_SOFT_CUES]:
        if _has_word(padded, word):
            consumed.update(re.findall(r"[a-z0-9]+", word))
    consumed.update({"earth", "cap", "caps", "listed", "years", "year", "critical", "strategic",
                     "materials", "minerals", "metals", "used", "developing", "within",
                     "smaller", "small", "larger", "find", "preferably", "ideally",
                     "catalysts", "catalyst", "other", "hardware", "evs", "ai"})
    unmatched = tuple(t for t in parsed.unmatched_terms if t not in consumed)

    needs_narrowing = parsed.needs_narrowing and not (materials or themes)
    if not needs_narrowing and parsed.needs_narrowing:
        warnings = [w for w in warnings if "did not match any known theme" not in w]

    return DiscoveryIntent(
        text=thesis_text,
        themes=_dedup(themes),
        sectors=_dedup(sectors),
        industries=_dedup(industries),
        regions=_dedup(regions),
        countries=_dedup(countries),
        materials=_dedup(materials),
        end_markets=_dedup(end_markets),
        catalysts=_dedup(catalysts),
        horizon_years=horizon,
        size=size,
        growth=growth,
        profitability=profitability,
        geography=geography,
        industry=industry_constraint,
        exclusions=tuple(parsed.exclusion_keywords),
        unmatched_terms=unmatched,
        warnings=tuple(warnings),
        needs_narrowing=needs_narrowing,
    )


def intent_from_dict(data: dict[str, Any] | None) -> DiscoveryIntent | None:
    """Rebuild a stored intent. ``None`` for a legacy run that has none."""
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    by_key = {
        c.get("key"): Constraint(
            key=str(c.get("key")),
            requested=tuple(c.get("requested") or ()),
            hardness=str(c.get("hardness") or HARD),
            hardness_basis=str(c.get("hardness_basis") or ""),
            phrase=str(c.get("phrase") or ""),
        )
        for c in data.get("constraints") or []
        if isinstance(c, dict)
    }
    horizon = data.get("horizon_years")
    return DiscoveryIntent(
        text=str(data.get("text") or ""),
        themes=tuple(data.get("themes") or ()),
        sectors=tuple(data.get("sectors") or ()),
        industries=tuple(data.get("industries") or ()),
        regions=tuple(data.get("regions") or ()),
        countries=tuple(data.get("countries") or ()),
        materials=tuple(data.get("materials") or ()),
        end_markets=tuple(data.get("end_markets") or ()),
        catalysts=tuple(data.get("catalysts") or ()),
        horizon_years=(int(horizon[0]), int(horizon[1])) if horizon else None,
        size=by_key.get("size"),
        growth=by_key.get("growth"),
        profitability=by_key.get("profitability"),
        geography=by_key.get("geography"),
        industry=by_key.get("industry"),
        exclusions=tuple(data.get("exclusions") or ()),
        unmatched_terms=tuple(data.get("unmatched_terms") or ()),
        warnings=tuple(data.get("warnings") or ()),
        needs_narrowing=bool(data.get("needs_narrowing")),
    )


__all__ = [
    "CATALYST_VOCABULARY",
    "Constraint",
    "DiscoveryIntent",
    "HARD",
    "SCHEMA",
    "SIZE_BUCKETS",
    "SOFT",
    "build_intent",
    "hardness_for",
    "intent_from_dict",
]
