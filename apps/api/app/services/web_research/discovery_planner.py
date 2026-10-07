"""The Discovery query plan — open-web W6b, wave 1 (spec §4.2–4.4, §5.1–5.3, §19.1).

A **deterministic** query set for ONE Discovery Intent, built only from the intent's
CLOSED vocabularies (theme keys, commodity slugs, regions, countries, end markets,
catalysts, size bands) and the versioned glossary in ``locales.py``. The user's raw text
never becomes a query token, and nothing a fetched page said can: this module takes no
page text, and the optional model expansion receives only the same closed facts and the
template queries (threat model PI-07).

FAMILIES (wave 1 — "breadth")
=============================
``ENTITY`` surface listed companies · ``VALUE_CHAIN`` suppliers, components, projects,
offtake and financing · ``VENUE`` the long tail by exchange segment (AIM, First North,
Euronext Growth, ASX, TSX Venture ...) · ``LOCAL_LANG`` the theme in the venue's language ·
``DEMAND`` market drivers, regulation, government support, capacity and the COUNTER-thesis
(oversupply, weak demand) · ``DOCUMENT`` market reports, white papers, consultations and
investor presentations.

LONG-TAIL MEASURES (no randomness — spec §5.3)
==============================================
Family diversity (the round-robin below serves every family before any gets a second
query); local-language variants per requested geography; segment queries; and
**saturation-aware follow-ups**: when ≥ 70 % of a query's result domains belong to known
names (the curated registry, held companies, existing candidates — their IR domains), the
next variant of that family excludes those IR domains, and an ``ENTITY`` query is also
re-asked on page 2. Deterministic: same results, same follow-ups.

BUDGET (spec §19.1)
===================
The plan is cut to ``max_queries`` MINUS a small reserve kept for the saturation
follow-ups (the reserve is spent only if a follow-up is warranted); the model expansion
counts inside the same ceiling.

Template queries are stamped ``w6b.1:<key>`` so a changed template is never mistaken for
an unchanged one when two runs are compared.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

from app.services.providers.contracts import QueryFamily, SearchRequest
from app.services.web_research import locales as loc
from app.services.web_research.classify import trade_publication_hosts
from app.services.web_research.planner import (
    DAYS_3Y,
    DAYS_5Y,
    DAYS_12M,
    MAX_EXPANSION_WORDS,
    ORIGIN_LLM_EXPANSION,
    ORIGIN_TEMPLATE,
    RESULTS_PER_QUERY,
    ExpansionResult,
    PlannedQuery,
    QueryPlan,
    validate_proposals,
)
from app.services.web_research.queries import find_private_token, sanitise_query

DISCOVERY_TEMPLATE_VERSION = "w6b.1"
DISCOVERY_EXPANSION_PROMPT_VERSION = "dexp1"

#: Wave 1 families, in the order a small budget serves them.
DISCOVERY_FAMILIES: tuple[QueryFamily, ...] = (
    QueryFamily.ENTITY,
    QueryFamily.VALUE_CHAIN,
    QueryFamily.VENUE,
    QueryFamily.LOCAL_LANG,
    QueryFamily.DEMAND,
    QueryFamily.DOCUMENT,
)
EXPANSION_FAMILIES: tuple[QueryFamily, ...] = (
    QueryFamily.ENTITY,
    QueryFamily.VALUE_CHAIN,
    QueryFamily.DOCUMENT,
)

#: Saturation (spec §5.3).
SATURATION_THRESHOLD = 0.7
MIN_RESULTS_FOR_SATURATION = 3
MAX_EXCLUDE_DOMAINS = 30
#: Queries kept back for saturation follow-ups when the budget allows.
FOLLOWUP_RESERVE = 4
LOCALE_QUERIES = {"standard": 6, "deep": 10}
MAX_NOUNS = 3
MAX_GEOGRAPHIES = 2
MAX_COMPONENTS = 4

#: Theme key -> the noun phrase a query uses (English).
THEME_NOUNS: dict[str, str] = {
    "luxury_goods": "luxury goods",
    "critical_materials": "critical minerals",
    "mining_materials": "mining",
    "defense": "defence",
    "semiconductors": "semiconductor",
    "nuclear_energy": "nuclear energy",
    "grid_electrification": "grid electrification equipment",
    "robotics_automation": "industrial robotics automation",
    "biotech_pharma": "biotechnology",
    "banks_fintech": "fintech",
    "ai_infrastructure": "AI data centre infrastructure",
}

#: Theme key -> value-chain components (suppliers / inputs / sub-systems).
VALUE_CHAIN_TERMS: dict[str, tuple[str, ...]] = {
    "grid_electrification": (
        "transformer",
        "switchgear",
        "high voltage cable",
        "grain oriented electrical steel",
        "grid automation",
    ),
    "semiconductors": (
        "silicon wafer",
        "photoresist",
        "etching equipment",
        "specialty gases",
        "advanced packaging",
    ),
    "defense": ("munitions", "radar components", "military electronics", "drone components"),
    "nuclear_energy": (
        "uranium enrichment",
        "nuclear fuel fabrication",
        "reactor components",
        "small modular reactor",
    ),
    "luxury_goods": ("leather goods", "watch components", "fine jewellery", "fragrance"),
    "robotics_automation": ("servo drive", "harmonic reducer", "machine vision", "robot arm"),
    "biotech_pharma": (
        "contract manufacturing organisation",
        "active pharmaceutical ingredient",
        "clinical stage",
    ),
    "ai_infrastructure": (
        "liquid cooling",
        "optical transceiver",
        "data centre power equipment",
        "GPU server",
    ),
    "banks_fintech": ("payments processor", "core banking software"),
}
#: For a material: where in ITS value chain a long-tail company sits.
MATERIAL_CHAIN_TERMS: tuple[str, ...] = (
    "refining processing",
    "by-product recovery",
    "recycling",
    "project developer",
)

#: Theme / material -> alternative words (a versioned synonym table).
SYNONYMS: dict[str, tuple[str, ...]] = {
    "luxury_goods": ("premium brands", "high-end fashion and jewellery"),
    "critical_materials": ("strategic metals", "critical raw materials"),
    "defense": ("defence contractor", "military equipment"),
    "semiconductors": ("chip", "integrated circuit"),
    "nuclear_energy": ("uranium and nuclear fuel", "nuclear power"),
    "grid_electrification": ("power grid equipment", "electrical infrastructure"),
    "rare_earths": ("rare earth elements", "NdPr magnets"),
    "gallium": ("gallium arsenide", "GaN"),
    "germanium": ("germanium optics", "germanium substrates"),
    "lithium": ("lithium carbonate hydroxide", "battery metals"),
    "graphite": ("anode material", "battery graphite"),
}

# --------------------------------------------------------------------------- #
# Facts: the planner's only inputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DiscoveryFacts:
    """The closed-vocabulary view of a Discovery Intent. Nothing here is user free text."""

    themes: tuple[str, ...] = ()
    materials: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    end_markets: tuple[str, ...] = ()
    catalysts: tuple[str, ...] = ()
    size_bands: tuple[str, ...] = ()
    #: ``input`` when the materials are consumed (not produced) by the wanted companies.
    materials_role: str | None = None

    def intent(self) -> dict[str, Any]:
        """What the model expansion may see, and what its cache key is made of."""
        return {
            "themes": list(self.themes),
            "materials": list(self.materials),
            "industries": list(self.industries),
            "regions": list(self.regions),
            "countries": list(self.countries),
            "end_markets": list(self.end_markets),
            "catalysts": list(self.catalysts),
            "size_bands": list(self.size_bands),
        }

    def nouns(self) -> list[tuple[str, str]]:
        """``(kind, noun)`` pairs — kind is ``material`` or ``theme``. Materials first."""
        out: list[tuple[str, str]] = []
        for slug in self.materials:
            out.append(("material", slug))
        for theme in self.themes:
            # With materials named, "mining" / "critical materials" only describe them.
            if self.materials and theme in ("mining_materials", "critical_materials"):
                continue
            out.append(("theme", theme))
        if not out:
            # Industries only name the thesis when nothing narrower does.
            for industry in self.industries:
                text = industry.strip().lower()
                if text:
                    out.append(("industry", text))
        seen: set[str] = set()
        unique = []
        for kind, key in out:
            if key not in seen:
                seen.add(key)
                unique.append((kind, key))
        return unique[:MAX_NOUNS]


def facts_from_intent(intent: Any) -> DiscoveryFacts:
    """:class:`DiscoveryFacts` from a ``DiscoveryIntent`` (closed values only)."""
    size = getattr(intent, "size", None)
    return DiscoveryFacts(
        themes=tuple(getattr(intent, "themes", ()) or ()),
        materials=tuple(getattr(intent, "materials", ()) or ()),
        industries=tuple(getattr(intent, "industries", ()) or ()),
        regions=tuple(getattr(intent, "regions", ()) or ()),
        countries=tuple(getattr(intent, "countries", ()) or ()),
        end_markets=tuple(getattr(intent, "end_markets", ()) or ()),
        catalysts=tuple(getattr(intent, "catalysts", ()) or ()),
        size_bands=tuple(size.requested) if size is not None else (),
        materials_role=getattr(intent, "materials_role", None),
    )


def noun_text(kind: str, key: str) -> str:
    """The English noun phrase for a ``(kind, key)`` pair."""
    if kind == "theme":
        return THEME_NOUNS.get(key, key.replace("_", " "))
    return key.replace("_", " ")


def theme_terms(facts: DiscoveryFacts) -> tuple[str, ...]:
    """Words a result or a passage must relate to (selection and A3 use these)."""
    terms: list[str] = []
    for kind, key in facts.nouns():
        terms.extend(noun_text(kind, key).split())
        for syn in SYNONYMS.get(key, ()):
            terms.extend(syn.split())
    return tuple(dict.fromkeys(t.lower() for t in terms if len(t) >= 3))


#: Theme key -> words a passage uses when it is about the theme (A3's "theme term from the
#: intent vocabulary", spec §6.2). Generic words are deliberately absent: "company" or
#: "growth" in a paragraph is not evidence of a theme.
THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "luxury_goods": (
        "luxury",
        "jewellery",
        "jewelry",
        "watchmaker",
        "watches",
        "leather goods",
        "fragrance",
        "couture",
        "high-end fashion",
    ),
    "critical_materials": (
        "critical minerals",
        "critical raw materials",
        "strategic metals",
        "rare earth",
        "critical metals",
    ),
    "mining_materials": ("mining", "mineral resource", "ore body", "concentrate", "exploration"),
    "defense": (
        "defence",
        "defense",
        "military",
        "munitions",
        "missile",
        "radar",
        "armoured vehicles",
    ),
    "semiconductors": ("semiconductor", "wafer", "foundry", "lithography", "chip manufacturing"),
    "nuclear_energy": ("nuclear", "uranium", "reactor", "enrichment", "small modular reactor"),
    "grid_electrification": (
        "transformer",
        "switchgear",
        "substation",
        "high voltage",
        "grid equipment",
        "power cable",
        "electrification",
    ),
    "robotics_automation": ("robot", "robotics", "cobot", "industrial automation", "servo"),
    "biotech_pharma": (
        "biotech",
        "pharmaceutical",
        "clinical trial",
        "drug candidate",
        "therapeutics",
    ),
    "banks_fintech": ("bank", "fintech", "payments", "lending"),
    "ai_infrastructure": (
        "data centre",
        "data center",
        "gpu",
        "hyperscale",
        "liquid cooling",
        "ai infrastructure",
    ),
}


def theme_vocabulary_phrases(facts: DiscoveryFacts) -> tuple[str, ...]:
    """The phrases A3 looks for in a passage, from the closed vocabularies only.

    A thesis that names MATERIALS is about those materials (a paragraph that says
    "mining" is not about gallium), so for such a thesis only the materials, their
    synonyms and their glossary names count — unless the materials are INPUTS of the
    wanted companies (``materials_role == "input"``), when the theme keywords count too.
    Local-language names are included so a German or Japanese page can qualify.
    """
    phrases: list[str] = []

    def material_phrases() -> None:
        for slug in facts.materials:
            phrases.append(slug.replace("_", " "))
            phrases.extend(SYNONYMS.get(slug, ()))
            phrases.extend(loc.MATERIAL_GLOSSARY.get(slug, {}).values())

    def theme_phrases() -> None:
        for theme in facts.themes:
            phrases.extend(THEME_KEYWORDS.get(theme, ()))
            for local in loc.THEME_GLOSSARY.get(theme, {}).values():
                phrases.extend(w for w in local.split() if len(w) >= 5 or not w.isascii())
            phrases.extend(SYNONYMS.get(theme, ()))
        for industry in facts.industries:
            phrases.extend(w for w in industry.lower().split() if len(w) >= 6)

    if facts.materials:
        material_phrases()
        if facts.materials_role == "input":
            theme_phrases()
    else:
        theme_phrases()
    return tuple(dict.fromkeys(p.strip() for p in phrases if p and p.strip()))


# --------------------------------------------------------------------------- #
# Freshness (spec §4.2 table)
# --------------------------------------------------------------------------- #


def window_days(family: QueryFamily) -> int:
    """Discovery wave-1 windows: DEMAND 1 year, DOCUMENT 5 years, the rest 3 years."""
    if family is QueryFamily.DEMAND:
        return DAYS_12M
    if family is QueryFamily.DOCUMENT:
        return DAYS_5Y
    return DAYS_3Y


# --------------------------------------------------------------------------- #
# Templates (versioned data)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DTemplate:
    family: QueryFamily
    key: str
    pattern: str
    #: Lower runs first within its family.
    priority: int
    needs: tuple[str, ...] = ()
    #: Only when the intent names this kind of noun ("material" / "theme").
    noun_kind: str | None = None
    #: Only when the intent requests one of these size bands.
    size_bands: tuple[str, ...] = ()
    #: Only when the intent names this catalyst.
    catalyst: str | None = None
    pdf: bool = False
    topic: str = "general"
    terms: tuple[str, ...] = ()
    #: Theme keys this template is meaningless for (a "luxury goods shortage" query).
    skip_keys: tuple[str, ...] = ()
    #: Restrict the provider to these hosts (the trade-press sweep).
    include_domains: tuple[str, ...] = ()


_SMALL = ("micro_cap", "small_cap", "mid_cap")
_NO_SHORTAGE = ("luxury_goods", "banks_fintech", "biotech_pharma")

TEMPLATES: tuple[DTemplate, ...] = (
    # -- ENTITY ------------------------------------------------------------------
    # The trade-press sweep. On the first live critical-minerals run the generic entity
    # queries returned SEO "top miners" lists that rule A3 rightly refuses (one promotional
    # page named six real ASX companies and admitted none). Restricting ONE entity query to the
    # curated trade-press hosts asks for the kind of page A3 can accept.
    DTemplate(
        QueryFamily.ENTITY,
        "entity_trade_press",
        "{noun} listed companies {region}",
        1,
        terms=("listed", "companies"),
        include_domains=trade_publication_hosts(),
    ),
    DTemplate(
        QueryFamily.ENTITY,
        "entity_listed",
        "{noun} companies listed {region}",
        0,
        terms=("listed", "companies"),
    ),
    DTemplate(
        QueryFamily.ENTITY,
        "entity_producer",
        "{noun} producer publicly traded company {region}",
        1,
        terms=("producer", "traded"),
    ),
    DTemplate(
        QueryFamily.ENTITY,
        "entity_small_cap",
        "small cap {noun} company {region}",
        2,
        size_bands=_SMALL,
        terms=("small", "cap"),
    ),
    DTemplate(
        QueryFamily.ENTITY,
        "entity_junior",
        "junior {noun} developer explorer listed",
        3,
        noun_kind="material",
        terms=("junior", "developer", "explorer"),
    ),
    DTemplate(
        QueryFamily.ENTITY,
        "entity_synonym",
        "{synonym} public company {region}",
        4,
        needs=("synonym",),
        terms=("public", "company"),
    ),
    # -- VALUE_CHAIN ---------------------------------------------------------------
    DTemplate(
        QueryFamily.VALUE_CHAIN,
        "vc_component",
        "{component} supplier listed company {region}",
        0,
        needs=("component",),
        terms=("supplier", "listed"),
    ),
    DTemplate(
        QueryFamily.VALUE_CHAIN,
        "vc_input",
        "{component} manufacturer {noun}",
        1,
        needs=("component",),
        noun_kind="theme",
        terms=("manufacturer",),
    ),
    DTemplate(
        QueryFamily.VALUE_CHAIN,
        "vc_project",
        "{noun} project permit approval {region}",
        2,
        catalyst="permitting",
        topic="news",
        terms=("project", "permit", "approval"),
    ),
    DTemplate(
        QueryFamily.VALUE_CHAIN,
        "vc_offtake",
        "{noun} offtake agreement signed",
        3,
        catalyst="offtake",
        topic="news",
        terms=("offtake", "agreement"),
    ),
    DTemplate(
        QueryFamily.VALUE_CHAIN,
        "vc_financing",
        "{noun} project financing funding {region}",
        4,
        noun_kind="material",
        topic="news",
        terms=("financing", "funding"),
    ),
    DTemplate(
        QueryFamily.VALUE_CHAIN,
        "vc_production",
        "{noun} first production commissioning ramp-up",
        5,
        catalyst="production",
        topic="news",
        terms=("production", "commissioning"),
    ),
    DTemplate(
        QueryFamily.VALUE_CHAIN,
        "vc_contracts",
        "{noun} contract award supplier selected",
        6,
        catalyst="contracts",
        topic="news",
        terms=("contract", "award"),
    ),
    # -- VENUE ---------------------------------------------------------------------
    DTemplate(
        QueryFamily.VENUE,
        "venue_segment",
        "{noun} company {segment}",
        0,
        needs=("segment",),
        terms=("company",),
    ),
    # -- DEMAND --------------------------------------------------------------------
    DTemplate(
        QueryFamily.DEMAND,
        "demand_shortage",
        "{region} {noun} shortage {year}",
        0,
        terms=("shortage", "supply"),
        skip_keys=_NO_SHORTAGE,
    ),
    DTemplate(
        QueryFamily.DEMAND,
        "demand_supply",
        "{noun} supply demand outlook {year}",
        1,
        terms=("supply", "demand", "outlook"),
    ),
    DTemplate(
        QueryFamily.DEMAND,
        "demand_capacity",
        "{noun} capacity expansion investment {region}",
        2,
        terms=("capacity", "expansion", "investment"),
    ),
    DTemplate(
        QueryFamily.DEMAND,
        "demand_government",
        "{noun} government support funding {region}",
        3,
        terms=("government", "support", "funding"),
    ),
    DTemplate(
        QueryFamily.DEMAND,
        "demand_regulation",
        "{noun} regulation policy {region}",
        4,
        terms=("regulation", "policy"),
    ),
    DTemplate(
        QueryFamily.DEMAND,
        "demand_counter",
        "{noun} oversupply demand weakness risks",
        5,
        terms=("oversupply", "weakness", "risks"),
    ),
    DTemplate(
        QueryFamily.DEMAND,
        "demand_orders",
        "{noun} orders backlog lead times",
        6,
        catalyst="orders",
        topic="news",
        terms=("orders", "backlog", "lead"),
    ),
    DTemplate(
        QueryFamily.DEMAND,
        "demand_approval",
        "{noun} regulatory approval decision",
        7,
        catalyst="regulatory_approval",
        topic="news",
        terms=("approval", "regulatory"),
    ),
    # -- DOCUMENT ------------------------------------------------------------------
    DTemplate(
        QueryFamily.DOCUMENT,
        "doc_market_report",
        "{noun} market report",
        0,
        pdf=True,
        terms=("market", "report"),
    ),
    DTemplate(
        QueryFamily.DOCUMENT,
        "doc_presentation",
        "{noun} company investor presentation {year}",
        1,
        pdf=True,
        terms=("investor", "presentation"),
    ),
    DTemplate(
        QueryFamily.DOCUMENT,
        "doc_whitepaper",
        "{noun} white paper industry association",
        2,
        terms=("white", "paper", "association"),
    ),
    DTemplate(
        QueryFamily.DOCUMENT,
        "doc_consultation",
        "{noun} government consultation strategy {region}",
        3,
        terms=("consultation", "strategy"),
    ),
    DTemplate(
        QueryFamily.DOCUMENT,
        "doc_critical",
        "{noun} critical raw materials strategy report",
        4,
        noun_kind="material",
        pdf=True,
        terms=("critical", "strategy"),
    ),
)

FAMILY_TERMS: dict[QueryFamily, tuple[str, ...]] = {
    QueryFamily.ENTITY: ("listed", "company", "companies", "producer"),
    QueryFamily.VALUE_CHAIN: ("supplier", "manufacturer", "project", "offtake"),
    QueryFamily.VENUE: ("company", "listed", "exchange"),
    QueryFamily.LOCAL_LANG: (),
    QueryFamily.DEMAND: ("supply", "demand", "shortage", "capacity", "policy"),
    QueryFamily.DOCUMENT: ("report", "market", "strategy", "presentation"),
}

# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


@dataclass
class DiscoveryPlan(QueryPlan):
    """A :class:`QueryPlan` plus the template queries NOT used (the follow-up reserve)."""

    reserve: list[PlannedQuery] = field(default_factory=list)
    nouns: list[str] = field(default_factory=list)
    locales: list[str] = field(default_factory=list)
    glossary_version: str = loc.GLOSSARY_VERSION

    def query_for(self, request: SearchRequest) -> PlannedQuery | None:
        for q in self.queries:
            if q.request is request:
                return q
        return None


def _fill(pattern: str, slots: Mapping[str, str]) -> str:
    return " ".join(re.sub(r"\{(\w+)\}", lambda m: slots.get(m.group(1), ""), pattern).split())


def _make_request(
    text: str,
    template: DTemplate | None,
    *,
    family: QueryFamily,
    today: date,
    private_tokens: Collection[str],
    origin: str,
    version: str,
    language: str | None = None,
    country: str | None = None,
    pdf: bool = False,
    topic: str = "general",
    include_domains: tuple[str, ...] = (),
    windowed: bool = True,
) -> tuple[SearchRequest | None, str | None]:
    clean = sanitise_query(text, filetype_pdf=pdf, private_tokens=private_tokens)
    if not clean.ok:
        return None, clean.refusal
    days = window_days(family)
    return (
        SearchRequest(
            query=clean.text,
            family=family,
            max_results=RESULTS_PER_QUERY,
            date_from=today - timedelta(days=days) if windowed else None,
            date_to=today if windowed else None,
            country=country,
            language=language,
            include_domains=include_domains,
            topic="news" if topic == "news" else "general",  # type: ignore[arg-type]
            origin=origin,
            template_version=version[:40],
        ),
        None,
    )


def _geographies(facts: DiscoveryFacts) -> list[str]:
    geos = [*facts.countries, *facts.regions]
    return geos[:MAX_GEOGRAPHIES] or [""]


def _components(facts: DiscoveryFacts) -> list[str]:
    out: list[str] = []
    for theme in facts.themes:
        out.extend(VALUE_CHAIN_TERMS.get(theme, ()))
    if facts.materials:
        for slug in facts.materials[:2]:
            out.extend(f"{slug.replace('_', ' ')} {t}" for t in MATERIAL_CHAIN_TERMS[:2])
    return list(dict.fromkeys(out))[:MAX_COMPONENTS]


def _synonyms(facts: DiscoveryFacts) -> list[str]:
    out: list[str] = []
    for key in (*facts.materials, *facts.themes):
        out.extend(SYNONYMS.get(key, ()))
    return list(dict.fromkeys(out))[:3]


def _local_queries(
    facts: DiscoveryFacts,
    *,
    mode: str,
    today: date,
    private_tokens: Collection[str],
    plan: DiscoveryPlan,
) -> list[PlannedQuery]:
    """``LOCAL_LANG``: the theme, in each requested geography's language."""
    out: list[PlannedQuery] = []
    cap = LOCALE_QUERIES.get(mode, LOCALE_QUERIES["standard"])
    seen_lang: set[str] = set()
    for language, country in loc.locales_for_geography(facts.regions, facts.countries):
        if language in seen_lang or language not in loc.DISCOVERY_LANGUAGES:
            continue
        listed = loc.phrase("listed_company", language)
        producer = loc.phrase("producer", language)
        if not listed:
            continue
        subject: str | None = None
        for kind, key in facts.nouns():
            subject = (
                loc.material_phrase(key, language)
                if kind == "material"
                else loc.theme_phrase(key, language)
                if kind == "theme"
                else None
            )
            if subject:
                break
        if not subject:
            continue
        seen_lang.add(language)
        text = " ".join([subject, producer or "", listed] if facts.materials else [subject, listed])
        request, refusal = _make_request(
            text,
            None,
            family=QueryFamily.LOCAL_LANG,
            today=today,
            private_tokens=private_tokens,
            origin=ORIGIN_TEMPLATE,
            version=f"{DISCOVERY_TEMPLATE_VERSION}:local.{language}",
            language=language,
            country=country,
        )
        if request is None:
            plan.refused.append((f"local.{language}", refusal or "refused"))
            continue
        out.append(
            PlannedQuery(
                request,
                QueryFamily.LOCAL_LANG,
                f"local.{language}",
                1,
                len(out),
                (),
                locale=language,
            )
        )
        if len(out) >= cap:
            break
    return out


def build_discovery_plan(
    facts: DiscoveryFacts,
    *,
    mode: str = "standard",
    max_queries: int = 24,
    followup_reserve: int = FOLLOWUP_RESERVE,
    today: date | None = None,
    private_tokens: Collection[str] = (),
    expansion: Sequence[str] = (),
    expansion_origin: str = ORIGIN_LLM_EXPANSION,
) -> DiscoveryPlan:
    """The deterministic wave-1 plan for ``facts``, at most ``max_queries`` long.

    ``max_queries - followup_reserve`` queries are planned up front (the model proposals
    count inside it); the unplanned templates wait in ``plan.reserve`` for saturation
    follow-ups. Same inputs, same queries, same order.
    """
    today = today or date.today()
    plan = DiscoveryPlan(mode=mode, today=today, template_version=DISCOVERY_TEMPLATE_VERSION)
    nouns = facts.nouns()
    plan.nouns = [noun_text(k, v) for k, v in nouns]
    if not nouns:
        return plan
    geos = _geographies(facts)
    segments = loc.segments_for_geography(facts.regions, facts.countries)[:3]
    components = _components(facts)
    synonyms = _synonyms(facts)
    seen: set[str] = set()
    pools: dict[QueryFamily, list[PlannedQuery]] = {f: [] for f in DISCOVERY_FAMILIES}

    def add(pool: list[PlannedQuery], query: PlannedQuery) -> None:
        marker = " ".join(query.request.query.split()).casefold()
        if marker in seen:
            return
        seen.add(marker)
        pool.append(query)

    for template in TEMPLATES:
        if template.noun_kind and not any(k == template.noun_kind for k, _ in nouns):
            continue
        if template.size_bands and not set(template.size_bands) & set(facts.size_bands):
            continue
        if template.catalyst and template.catalyst not in facts.catalysts:
            continue
        # One instance per (noun, geography) pair, nouns cycling fastest, so a thesis
        # naming two materials or two regions reaches BOTH in the first variants rather
        # than exhausting one material's variants first.
        variants: list[dict[str, str]] = []
        applicable = [
            (kind, key)
            for kind, key in nouns
            if (not template.noun_kind or kind == template.noun_kind)
            and key not in template.skip_keys
        ]
        if template.family is QueryFamily.VENUE:
            applicable = applicable[:1]
        if not applicable:
            continue
        geo_pool = geos if "{region}" in template.pattern else geos[:1]
        # Diagonal first, then the off-diagonals: the first variants cover every noun and
        # every geography once before any (noun, geography) pair repeats a noun or place.
        span = max(len(applicable), len(geo_pool))
        order = sorted(
            ((i, j) for i in range(len(applicable)) for j in range(len(geo_pool))),
            key=lambda ij: ((ij[0] - ij[1]) % span, ij[0]),
        )
        pairs = [(applicable[i], geo_pool[j]) for i, j in order]
        for (kind, key), geo in pairs:
            base = {"noun": noun_text(kind, key), "year": str(today.year)}
            if template.key == "entity_synonym":
                for syn in synonyms:
                    variants.append({**base, "synonym": syn, "region": geo})
                continue
            if template.key in ("vc_component", "vc_input"):
                for component in components:
                    variants.append({**base, "component": component, "region": geo})
                continue
            if template.key == "venue_segment":
                for segment in segments:
                    variants.append({**base, "segment": segment, "region": ""})
                continue
            variants.append({**base, "region": geo})
        for index, slots in enumerate(variants):
            if any(not slots.get(n) for n in template.needs):
                continue
            text = _fill(template.pattern, slots)
            key = f"{template.key}.{index}"
            request, refusal = _make_request(
                text,
                template,
                family=template.family,
                today=today,
                private_tokens=private_tokens,
                origin=ORIGIN_TEMPLATE,
                version=f"{DISCOVERY_TEMPLATE_VERSION}:{key}",
                pdf=template.pdf,
                topic=template.topic,
                include_domains=template.include_domains,
            )
            if request is None:
                plan.refused.append((key, refusal or "refused"))
                continue
            add(
                pools[template.family],
                PlannedQuery(
                    request,
                    template.family,
                    key,
                    1,
                    (template.priority + index) * 100 + index,
                    template.terms or FAMILY_TERMS[template.family],
                ),
            )

    local = _local_queries(facts, mode=mode, today=today, private_tokens=private_tokens, plan=plan)
    plan.locales = [q.locale for q in local if q.locale]
    for q in local:
        add(pools[QueryFamily.LOCAL_LANG], q)

    # Model proposals: validated apart and appended AFTER the templates are trimmed, so
    # they spend the expansion share of the ceiling (spec §19.1 "within the above").
    expansion_template = DTemplate(QueryFamily.ENTITY, "expansion", "", 99)
    exp_queries: list[PlannedQuery] = []
    for index, text in enumerate(expansion):
        family = EXPANSION_FAMILIES[index % len(EXPANSION_FAMILIES)]
        request, refusal = _make_request(
            text,
            expansion_template,
            family=family,
            today=today,
            private_tokens=private_tokens,
            origin=expansion_origin,
            version=f"{DISCOVERY_TEMPLATE_VERSION}+{DISCOVERY_EXPANSION_PROMPT_VERSION}",
        )
        if request is None:
            plan.refused.append((f"expansion{index}", refusal or "refused"))
            continue
        marker = " ".join(request.query.split()).casefold()
        if marker in seen:
            continue
        seen.add(marker)
        exp_queries.append(
            PlannedQuery(request, family, f"expansion{index}", 1, 100 + index, FAMILY_TERMS[family])
        )

    for pool in pools.values():
        pool.sort(key=lambda q: q.priority)
    # Round-robin by family: one query from each family before any gets a second, so a
    # small budget still reaches every family (the long tail lives in the non-ENTITY ones).
    ordered: list[PlannedQuery] = []
    depth = 0
    while any(depth < len(pool) for pool in pools.values()):
        for family in DISCOVERY_FAMILIES:
            if depth < len(pools[family]):
                ordered.append(pools[family][depth])
        depth += 1
    reserve_n = max(0, min(followup_reserve, max_queries // 4))
    room = max(0, max_queries - reserve_n - len(exp_queries[: max(0, max_queries)]))
    upfront = ordered[:room]
    plan.queries = upfront + exp_queries[: max(0, max_queries - reserve_n)]
    plan.reserve = ordered[room:]
    plan.trimmed = len(plan.reserve)
    return plan


def intent_hash(facts: DiscoveryFacts, template_queries: Iterable[str]) -> str:
    blob = json.dumps(
        {"intent": facts.intent(), "queries": list(template_queries)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Saturation-aware follow-ups (spec §5.3) — deterministic
# --------------------------------------------------------------------------- #


def saturation_share(domains: Sequence[str], known_domains: Collection[str]) -> float:
    """The fraction of ``domains`` that belong to known names' sites."""
    if not domains:
        return 0.0
    known = {d.lower().removeprefix("www.") for d in known_domains}
    hits = sum(
        1
        for d in domains
        if (h := d.lower().removeprefix("www.")) in known or any(h.endswith("." + k) for k in known)
    )
    return hits / len(domains)


def is_saturated(domains: Sequence[str], known_domains: Collection[str]) -> bool:
    return len(domains) >= MIN_RESULTS_FOR_SATURATION and (
        saturation_share(domains, known_domains) >= SATURATION_THRESHOLD
    )


def build_followups(
    plan: DiscoveryPlan,
    saturated: Sequence[PlannedQuery],
    known_ir_domains: Sequence[str],
    *,
    limit: int,
    result_domains: Mapping[str, Sequence[str]] | None = None,
) -> list[PlannedQuery]:
    """The follow-up for each saturated query, at most ``limit`` of them, in plan order.

    * ``ENTITY`` — the SAME query on page 2 with the known names' IR domains excluded;
    * any other family — the NEXT unplanned template of that family, with the same
      exclusion, falling back to page 2 of the saturated query when the family has none
      left.

    A follow-up is a new row, stamped ``<template version>:<key>.sat``.
    """
    # Domains seen in the saturated queries' results come first: they are the ones that
    # demonstrably crowded the answer out.
    seen_domains = [d for q in saturated for d in (result_domains or {}).get(q.key, ())]
    ordered = list(
        dict.fromkeys(
            [d for d in seen_domains if d in set(known_ir_domains)] + list(known_ir_domains)
        )
    )
    exclude = tuple(sorted(ordered[:MAX_EXCLUDE_DOMAINS]))
    reserve = list(plan.reserve)
    out: list[PlannedQuery] = []
    for query in saturated:
        if len(out) >= limit:
            break
        variant: PlannedQuery | None = None
        if query.family is not QueryFamily.ENTITY:
            for candidate in reserve:
                if candidate.family is query.family:
                    variant = candidate
                    reserve.remove(candidate)
                    break
        base = variant.request if variant is not None else query.request
        page = 2 if (variant is None) else base.page
        request = replace(
            base,
            page=page,
            exclude_domains=exclude,
            template_version=f"{DISCOVERY_TEMPLATE_VERSION}:{(variant or query).key}.sat"[:40],
            origin=ORIGIN_TEMPLATE,
        )
        out.append(
            PlannedQuery(
                request,
                query.family,
                f"{(variant or query).key}.sat",
                1,
                1000 + len(out),
                (variant or query).terms,
                locale=query.locale,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Bounded model expansion (spec §4.3)
# --------------------------------------------------------------------------- #

_EXPANSION_SYSTEM = (
    "You propose additional web search queries to FIND listed companies in a thematic "
    "area, especially smaller and less-known ones: synonyms, technology names, process "
    "terms, product categories, supplier and value-chain terms. Reply in json: "
    f'{{"queries": ["..."]}}. Each query is at most {MAX_EXPANSION_WORDS} words of '
    "plain lower-case words. NEVER name a company, brand, ticker or person. No URLs, no "
    "search operators, no quotation marks. You are given only the closed brief and the "
    "queries already planned."
)

_CACHE_MAX = 128
_CACHE: OrderedDict[tuple[str, str, str], tuple[str, ...]] = OrderedDict()

_LEGAL_FORM_RE = re.compile(
    r"\b(?:ltd|plc|inc|corp|corporation|gmbh|ag|nv|asa|oyj|spa|llc|limited|holdings?|"
    r"s\.a\.|a/s|pty)\b",
    re.IGNORECASE,
)
_TICKER_RE = re.compile(r"\b[A-Z]{2,6}\s?[:.]\s?[A-Z0-9]{1,6}\b")


def clear_expansion_cache() -> None:
    _CACHE.clear()


def _company_like(text: str, allowed: Collection[str] = ()) -> bool:
    """A proposal that looks like it NAMES a company: a legal form, a ``VENUE:TICKER``, or
    a Title-Case word that is not part of the closed vocabulary (``Umicore``; but
    ``Gallium`` is the brief's own word). A model that names a company must not steer the
    search toward it (an LLM naming a company admits nothing)."""
    if _LEGAL_FORM_RE.search(text) or _TICKER_RE.search(text):
        return True
    known = {a.lower() for a in allowed}
    return any(
        w[0].isupper() and w[1:].islower() and len(w) > 2 and w.lower() not in known
        for w in text.split()
    )


async def propose_discovery_expansion(
    transport: Any,
    facts: DiscoveryFacts,
    plan: DiscoveryPlan,
    *,
    limit: int,
    max_tokens: int,
    private_tokens: Collection[str] = (),
    timeout: int = 60,
) -> ExpansionResult:
    """Ask the model for up to ``limit`` extra queries. Never raises.

    The prompt carries the closed brief and the template queries only — no page text,
    snippet or title, by construction: this function has no parameter that could carry
    one. Cached by ``(intent_hash, model, prompt_version)``.
    """
    result = ExpansionResult()
    if transport is None or limit <= 0 or max_tokens <= 0:
        return result
    model = str(getattr(transport, "model", "") or "unknown")
    result.model = model
    template_queries = [q.request.query for q in plan.queries if q.origin == ORIGIN_TEMPLATE]
    key = (intent_hash(facts, template_queries), model, DISCOVERY_EXPANSION_PROMPT_VERSION)
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        result.queries = tuple(cached[:limit])
        result.from_cache = True
        return result
    user = json.dumps(
        {"brief": facts.intent(), "already_planned": template_queries, "max_queries": limit},
        ensure_ascii=False,
    )
    try:
        response = await transport.complete(
            system=_EXPANSION_SYSTEM,
            user=user,
            max_tokens=max_tokens,
            temperature=0.2,
            timeout=timeout,
            json_mode=True,
            thinking=False,
        )
    except Exception as exc:  # noqa: BLE001 - expansion is optional; the templates stand
        result.error = type(exc).__name__
        return result
    from app.services.consumption import ConsumptionUnits

    by_vendor: tuple[Any, ...]
    try:
        from app.integrations.deepseek.providers import _vendor_usage

        by_vendor = (_vendor_usage(response),)
    except Exception:  # noqa: BLE001
        by_vendor = ()
    result.units = ConsumptionUnits(
        model_calls=1,
        model_input_tokens=int(getattr(response, "prompt_tokens", 0) or 0),
        model_output_tokens=int(getattr(response, "completion_tokens", 0) or 0),
        cached_tokens=int(getattr(response, "cached_tokens", 0) or 0),
        by_vendor=by_vendor,
        instrumented=frozenset({"model_calls", "model_input_tokens", "model_output_tokens"}),
    )
    try:
        payload = json.loads(getattr(response, "text", "") or "")
        proposals = payload.get("queries") if isinstance(payload, dict) else None
    except (TypeError, ValueError):
        proposals = None
    if not isinstance(proposals, list):
        result.error = "unparseable_reply"
        return result
    result.proposed = len(proposals)
    accepted, refused = validate_proposals(
        proposals,
        template_queries=template_queries,
        limit=limit,
        private_tokens=private_tokens,
    )
    kept: list[str] = []
    vocabulary = set(theme_terms(facts)) | {
        w.lower() for q in plan.queries for w in q.request.query.split()
    }
    for text in accepted:
        if _company_like(text, vocabulary) or find_private_token(text, private_tokens):
            refused["company_like"] = refused.get("company_like", 0) + 1
            continue
        kept.append(text)
    result.queries = tuple(kept)
    result.refused = refused
    plan.expansion = result.to_dict()
    _CACHE[key] = tuple(kept)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return result


__all__ = [
    "DISCOVERY_EXPANSION_PROMPT_VERSION",
    "DISCOVERY_FAMILIES",
    "ORIGIN_LLM_EXPANSION",
    "DISCOVERY_TEMPLATE_VERSION",
    "FOLLOWUP_RESERVE",
    "MAX_EXCLUDE_DOMAINS",
    "SATURATION_THRESHOLD",
    "TEMPLATES",
    "THEME_KEYWORDS",
    "DiscoveryFacts",
    "DiscoveryPlan",
    "build_discovery_plan",
    "build_followups",
    "clear_expansion_cache",
    "facts_from_intent",
    "intent_hash",
    "is_saturated",
    "noun_text",
    "propose_discovery_expansion",
    "saturation_share",
    "theme_terms",
    "theme_vocabulary_phrases",
    "window_days",
]
