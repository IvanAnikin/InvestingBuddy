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

_HIGH_GROWTH = (
    r"high[\s-]growth|fast[\s-]growing|rapidly[\s-]growing|hyper[\s-]?growth"
    r"|rapid(?:ly)?\s+growth|growing\s+(?:fast|rapidly|quickly|strongly)"
)
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
    "latin america": "South America", "latin american": "South America",
    "south america": "South America", "south american": "South America",
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
    #: What the user EXCLUDED ("non-US", "not large cap"). A candidate matching an
    #: excluded value fails the constraint; it is never read as a request for it.
    excluded: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["requested"] = list(self.requested)
        out["excluded"] = list(self.excluded)
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
    #: "product" when the company is wanted FOR the material (a gallium producer);
    #: "input" when it merely uses it ("semiconductor companies using gallium").
    materials_role: str | None = None
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
            "materials_role": self.materials_role,
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
        excluded = list(self.geography.excluded) if self.geography else []
        return {
            "themes": list(self.themes),
            "sectors": list(self.sectors),
            "industries": list(self.industries),
            "regions": regions,
            "countries": countries,
            "excluded_regions": [g for g in excluded if g not in COUNTRY_TO_REGION],
            "excluded_countries": [g for g in excluded if g in COUNTRY_TO_REGION],
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


# ── Polarity: a named thing may be WANTED, EXCLUDED, or not a filter at all ── #

POSITIVE = "positive"
NEGATED = "negated"
NOT_A_FILTER = "not_a_filter"

#: Immediately before a term, these EXCLUDE it: "non-US", "not large cap", "excluding
#: the UK", "Asia ex-Japan", "outside the US", "other than British", "avoid large caps".
#: The cue must sit DIRECTLY before the term — only a determiner may come between — so
#: "non-cyclical European", "no-nonsense European" or "not overvalued European" never
#: turn Europe into an exclusion.
_NEGATION_BEFORE = re.compile(
    r"(?:\bnon-|\bnon\s|\bnot\s+|\bno\s+|\bexclud(?:e|es|ing)\s+|\bexcept\s+(?:for\s+)?"
    r"|\boutside\s+(?:of\s+)?|\bex-|\bex\s|\bother\s+than\s+|\bavoid(?:ing|s)?\s+"
    r"|\bwithout\s+|\bbut\s+not\s+|\bbesides\s+)(?:(?:the|a|an|any)\s+)?$"
)
#: "not just small caps", "not necessarily profitable": the user says the term is NOT a
#: requirement — neither wanted nor excluded. Followed by "but also", it is WANTED:
#: "not only European but also US" means both.
_NOT_A_FILTER_BEFORE = re.compile(
    r"\bnot\s+(?:just|only|necessarily|merely|exclusively)\s+(?:(?:the|a|an)\s+)?$"
)
_BUT_ALSO_AFTER = re.compile(r"^[^.;:]{0,60}?\bbut\s+also\b")


def polarity(text: str, start: int) -> str:
    """POSITIVE, NEGATED or NOT_A_FILTER for the term at ``start`` in lower-cased text."""
    before = text[max(0, start - 48) : start]
    if _NOT_A_FILTER_BEFORE.search(before):
        return POSITIVE if _BUT_ALSO_AFTER.search(text[start:]) else NOT_A_FILTER
    if _NEGATION_BEFORE.search(before):
        return NEGATED
    return POSITIVE


def _size_constraint(text: str) -> tuple[Constraint | None, list[str]]:
    """The size constraint, or None, plus notes. Negated sizes are EXCLUDED buckets."""
    taken: list[tuple[int, int]] = []
    wanted: list[str] = []
    excluded: list[str] = []
    phrases: list[str] = []
    notes: list[str] = []
    hardness: tuple[str, str] | None = None
    for pattern, bands, comparative in _SIZE_TERMS:
        for match in re.finditer(pattern, text):
            if any(match.start() < end and match.end() > begin for begin, end in taken):
                continue
            taken.append((match.start(), match.end()))
            sign = polarity(text, match.start())
            phrase = match.group(0).strip()
            if sign == NOT_A_FILTER:
                notes.append(f"'{phrase}' is stated as not a requirement, so size is not filtered")
                continue
            phrases.append(("not " if sign == NEGATED else "") + phrase)
            target = excluded if sign == NEGATED else wanted
            for band in bands:
                if band not in target:
                    target.append(band)
            this = (
                (HARD, "an explicit exclusion is a hard filter")
                if sign == NEGATED
                else hardness_for(text, match.start(), comparative=comparative)
            )
            if hardness is None or (this[0] == HARD and hardness[0] == SOFT):
                hardness = this
    if hardness is None or not (wanted or excluded):
        return None, notes
    requested = [b for b in (wanted or list(SIZE_BUCKETS)) if b not in excluded]
    if not requested:
        return None, [*notes, "the size words exclude every band, so size is not filtered"]
    ordered = tuple(b for b in SIZE_BUCKETS if b in requested)
    return (
        Constraint("size", ordered, hardness[0], hardness[1], ", ".join(phrases),
                   excluded=tuple(b for b in SIZE_BUCKETS if b in excluded)),
        notes,
    )


def _growth_constraint(text: str) -> tuple[Constraint | None, list[str]]:
    match = re.search(_HIGH_GROWTH, text)
    requested = "high_growth"
    if match is None:
        match = re.search(_GROWTH, text)
        requested = "growing"
    if match is None:
        return None, []
    sign = polarity(text, match.start())
    if sign != POSITIVE:
        return None, [
            f"'{match.group(0).strip()}' is negated or not a requirement; a condition that a "
            "company is NOT growing is not a supported filter, so growth is not filtered"
        ]
    hardness, basis = hardness_for(text, match.start())
    return Constraint("growth", (requested,), hardness, basis, match.group(0).strip()), []


def _profitability_constraint(text: str) -> tuple[Constraint | None, list[str]]:
    match = re.search(_PROFITABLE, text)
    if match is None:
        return None, []
    if polarity(text, match.start()) != POSITIVE:
        return None, [
            f"'{match.group(0).strip()}' is negated or not a requirement, so profitability "
            "is not filtered"
        ]
    hardness, basis = hardness_for(text, match.start())
    return (
        Constraint("profitability", ("profitable",), hardness, basis, match.group(0).strip()),
        [],
    )


#: A commodity word only names a MATERIAL in a materials context. "silver lining stocks"
#: and "lead the market" name no metal; without one of these words nothing is a material.
_MATERIALS_CONTEXT = re.compile(
    r"\b(?:min(?:e|es|er|ers|ing)|produc\w*|develop\w*|refin\w*|process\w*|deposits?"
    r"|projects?|metals?|minerals?|materials?|resources?|explor\w*|suppl\w*|critical"
    r"|strategic|commodit\w*|smelt\w*|recycl\w*|concentrates?|oxides?)\b"
)


def _materials(text: str, *, mining_theme: bool = False) -> list[str]:
    """Materials the text names POSITIVELY, in a materials context. The legacy parser's
    own mining theme counts as that context ("copper for semiconductors")."""
    if not (mining_theme or _MATERIALS_CONTEXT.search(text)):
        return []
    found: list[str] = []
    for commodity in COMMODITIES:
        pattern = r"\b(?:" + "|".join(commodity.patterns) + r")\b"
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if polarity(text, match.start()) == POSITIVE and commodity.slug not in found:
                found.append(commodity.slug)
                break
    return found


#: "US" as a country, not the pronoun: upper-case in the ORIGINAL text, or lower-case
#: not governed by a verb/preposition that takes the pronoun ("help us", "for us").
_US_UPPER = re.compile(r"(?<![A-Za-z])(?:US|U\.S\.|USA|U\.S\.A\.)(?![A-Za-z])")
_US_NOT_A_PLACE_AFTER = re.compile(
    r"^[\s-]*(?:investors?|dollars?|\$|tax(?:payers?|es)?|residents?|citizens?|persons?"
    r"|accounts?|clients?|customers?\s+only)\b"
)
_US_PRONOUN_BEFORE = re.compile(
    r"\b(?:help|give|show|tell|let|for|to|find|send|with|of|let's|lets|gives|shows)\s+$"
)


def _geo_terms(original: str, text: str) -> list[tuple[int, str, str]]:
    """(position, kind, value) for every region/country mention. kind: region|country."""
    out: list[tuple[int, str, str]] = []
    for word, named_region in _REGION_WORDS.items():
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text):
            out.append((m.start(), "region", named_region))
    for word, named_country in _COUNTRY_WORDS.items():
        if named_country not in COUNTRY_TO_REGION or word in ("us", "u.s.", "usa"):
            continue
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text):
            if word == "american" and re.search(
                r"\b(?:north|south|latin|central)\s+$", text[max(0, m.start() - 10) : m.start()]
            ):
                continue
            out.append((m.start(), "country", named_country))
    # ``original`` and ``text`` are the SAME whitespace-normalised string (one cased, one
    # lower-cased), so a position in one is the same position in the other.
    upper_positions = {m.start() for m in _US_UPPER.finditer(original)}
    for m in re.finditer(r"(?<![a-z0-9])(?:us|u\.s\.|usa)(?![a-z0-9])", text):
        if _US_NOT_A_PLACE_AFTER.search(text[m.end() : m.end() + 24]):
            continue  # "for US investors", "US dollars": the investor or a currency
        if m.start() in upper_positions or not _US_PRONOUN_BEFORE.search(
            text[max(0, m.start() - 12) : m.start()]
        ):
            out.append((m.start(), "country", "United States"))
    out.sort()
    return out


def _geography(
    original: str, text: str, *, region: str | None, country: str | None
) -> tuple[list[str], list[str], list[str], tuple[str, str] | None]:
    """(regions, countries, excluded, hardness) — explicit selectors win over the text.

    An explicit Country selector is the whole geography (Phase 27.1C: an explicit form
    value overrides the prompt, and a country is never widened to its region); an explicit
    Region selector likewise. Otherwise the text's positive mentions are a union, and its
    negated mentions ("non-US", "Europe excluding the UK") are exclusions.
    """
    from app.services.discovery_filters import canonical_country, canonical_region

    explicit_country = canonical_country(country) if country else None
    explicit_region = canonical_region(region) if region else None
    if explicit_country:
        return [], [explicit_country], [], (HARD, "an explicit Country selector is a hard filter")
    if explicit_region:
        return [explicit_region], [], [], (HARD, "an explicit Region selector is a hard filter")
    regions: list[str] = []
    countries: list[str] = []
    excluded: list[str] = []
    hardness: tuple[str, str] | None = None
    for position, kind, value in _geo_terms(original, text):
        sign = polarity(text, position)
        if sign == NOT_A_FILTER:
            continue
        if sign == NEGATED:
            if value not in excluded:
                excluded.append(value)
            continue
        target = regions if kind == "region" else countries
        if value not in target:
            target.append(value)
        if hardness is None:
            hardness = hardness_for(text, position)
    if hardness is None and excluded:
        hardness = (HARD, "an explicit exclusion is a hard filter")
    return regions, countries, excluded, hardness


#: Words that name each industry theme as a KIND of company.
_THEME_WORDS_RE: dict[str, str] = {
    "semiconductors": r"semiconductors?|chips?|chipmakers?",
    "ai_infrastructure": r"ai|data[\s-]?cent(?:er|re)s?",
    "grid_electrification": r"grid|electrification|electrical",
    "defense": r"defen[cs]e|aerospace",
    "nuclear_energy": r"nuclear|uranium",
    "luxury_goods": r"luxury|watch(?:es)?|jewell?ery",
    "robotics_automation": r"robotics?|automation",
    "biotech_pharma": r"biotech\w*|pharma\w*",
    "banks_fintech": r"banks?|fintech|payments?",
}


def _first_theme_position(text: str, keywords: list[str], materials: list[str]) -> int | None:
    positions = []
    for keyword in keywords:
        m = re.search(rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])", text)
        if m:
            positions.append(m.start())
    for material in materials:
        m = re.search(rf"\b{re.escape(material.replace('_', ' ').split()[0])}", text)
        if m:
            positions.append(m.start())
    return min(positions) if positions else None


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

    materials = _materials(text, mining_theme="mining_materials" in parsed.themes)
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
    materials_role: str | None = None
    # A materials thesis names the company BY its material ("gallium producers") unless it
    # names another kind of company and the material as its input ("semiconductor
    # companies using gallium") — then the material is context, and must not widen the
    # universe to miners.
    company_kind_themes = [
        t for t in themes
        if t not in ("mining_materials", "critical_materials")
        and re.search(rf"\b(?:{_THEME_WORDS_RE.get(t, '$^')})\b{_COMPANY_NOUN}", text)
    ]
    if materials and company_kind_themes:
        materials_role = "input"
    elif materials:
        materials_role = "product"
        # Industry themes that are really END MARKETS of the materials are demoted,
        # unless the text asks for companies OF that kind.
        for theme, market in _END_MARKET_THEMES.items():
            if theme not in themes:
                continue
            words = _THEME_WORDS_RE[theme]
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

    normalised = re.sub(r"\s+", " ", thesis_text or "").strip()
    regions, countries, excluded_geo, geo_hardness = _geography(
        normalised, text, region=region, country=country
    )
    # An exclusion of a place the platform cannot filter on is SAID, never dropped silently.
    for exclusion in re.finditer(
        r"\b(?:outside|excluding|except|ex-|non-)\s*(?:of\s+)?(?:the\s+)?([A-Z][a-z]{3,})",
        normalised,
    ):
        name = exclusion.group(1).lower()
        if name not in _REGION_WORDS and name not in _COUNTRY_WORDS:
            warnings.append(
                f"'{exclusion.group(0)}': {exclusion.group(1)} is not a place this platform "
                "can filter on, so it is not excluded"
            )
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

    size, size_notes = _size_constraint(text)
    warnings.extend(size_notes)
    if size is None and market_cap_bucket:
        bucket = market_cap_bucket.strip().lower().replace("-", "_").replace(" ", "_")
        if not bucket.endswith("_cap"):
            bucket += "_cap"
        if bucket in SIZE_BUCKETS:
            size = Constraint(
                "size", (bucket,), HARD, "an explicit size selector is a hard filter",
                market_cap_bucket,
            )
    growth, growth_notes = _growth_constraint(text)
    profitability, profit_notes = _profitability_constraint(text)
    warnings.extend([*growth_notes, *profit_notes])

    geography = None
    if (regions or countries or excluded_geo) and geo_hardness is not None:
        geography = Constraint(
            "geography",
            _dedup([*regions, *countries]),
            geo_hardness[0],
            geo_hardness[1],
            ", ".join([*regions, *countries, *(f"not {g}" for g in excluded_geo)]),
            excluded=_dedup(excluded_geo),
        )
    industry_constraint = None
    industry_terms = [*themes, *(materials if materials_role == "product" else [])]
    if industry_terms:
        first = _first_theme_position(text, parsed.keywords, materials)
        hardness = hardness_for(text, first) if first is not None else (
            HARD, "a named industry, theme or material is a hard filter"
        )
        industry_constraint = Constraint(
            "industry",
            _dedup(industry_terms),
            hardness[0],
            hardness[1],
            ", ".join(industry_terms),
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
        materials_role=materials_role,
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
            excluded=tuple(c.get("excluded") or ()),
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
        materials_role=data.get("materials_role"),
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
