"""The company query plan — open-web W5 (spec §4.1–4.4, §5.1–5.2, §19.1).

WHAT THIS IS
============
A **deterministic** query set for ONE company, built only from facts the platform has
verified and from versioned data in this file:

* the company's legal name and its short English name, its ticker and venue (identity
  verification, never a page);
* its verified official domains (``verified_issuer_sources``), used only as a ``site:``
  operator the planner itself appends;
* its industry (the classification's own label) and the commodity / product names its
  OWN filings state (``subject_profile``), both closed vocabularies;
* the date.

Nothing a fetched page said can become a query token here (threat model PI-07): the
planner takes no page text, and the optional model expansion receives only the same
facts and the template queries — never page text, never a search snippet, never a title.

FAMILIES (spec §4.2) AND WAVES (§4.4)
=====================================
Wave 2 (depth): ``COMPANY_DOCS`` · ``CATALYST`` · ``COMPETITIVE`` · ``INDUSTRY``.
Wave 3 (challenge): ``RISK`` — always planned, and always given a share of a small budget
(the round-robin below takes one query per family before any family gets a second).
A development-stage issuer (track C stage signals) additionally gets project-milestone
templates — permits, offtake, financing, construction/commissioning, capex, government
support — filed under ``CATALYST`` and recognisable by their template key.

FRESHNESS (spec §5.1)
=====================
Per family and mode, mapped to the provider's date filter (``date_from``/``date_to``):
``CATALYST`` 90 days (DEEP and MAX 12 months, topic ``news``); ``COMPANY_DOCS`` 18 months
for presentations and 3 years for annual reports (the latest AND the previous one, asked
by explicit year); ``COMPETITIVE`` 3 years; ``INDUSTRY`` 3 years (5 from STANDARD up);
``RISK`` 1 year (18 months for litigation). A search engine's own date is only a hint:
the document's own date is checked after the fetch.

LANGUAGE (spec §5.2)
====================
English is always planned. A venue→locale table adds local-language variants for the
issuer's venue from a versioned glossary (reviewed like code), using the company's legal
local name; the English variant keeps the English short name. ``language`` and
``country`` are passed as filters (the adapter says which it enforces).

TEMPLATE VERSIONING
===================
:data:`QUERY_TEMPLATE_VERSION` prefixes ``template_version`` on EVERY query row
(``w5.1:<template key>``, ≤ 40 chars), so a changed template is never mistaken for an
unchanged one when two runs are compared.

MODEL EXPANSION (spec §4.3)
===========================
Optional, bounded and recorded: a cheap model (``thinking=False``, JSON) may propose
extra ``COMPETITIVE`` / ``INDUSTRY`` queries (synonyms, technology and process terms).
Every proposal passes :func:`~app.services.web_research.queries.sanitise_query` AND is
refused if cleaning changed it (a URL or operator was in it), exceeds
:data:`MAX_EXPANSION_WORDS`, repeats a template query, or contains a private token. They
are cached by ``(intent_hash, model, prompt_version)`` so the same company produces the
same query set until the prompt version changes, and recorded with
``origin="llm_expansion"``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections import OrderedDict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.services.consumption import ConsumptionUnits
from app.services.providers.contracts import QueryFamily, SearchRequest
from app.services.web_research.queries import (
    MAX_QUERY_CHARS,
    find_private_token,
    sanitise_query,
)

logger = logging.getLogger(__name__)

QUERY_TEMPLATE_VERSION = "w5.1"
EXPANSION_PROMPT_VERSION = "exp1"
GLOSSARY_VERSION = "2026-10-04.1"

ORIGIN_TEMPLATE = "template"
ORIGIN_LLM_EXPANSION = "llm_expansion"

#: A model proposal longer than this is refused (spec §4.3).
MAX_EXPANSION_WORDS = 12
#: The families wave 2 and wave 3 plan, in the order a small budget serves them.
WAVE_FAMILIES: tuple[QueryFamily, ...] = (
    QueryFamily.COMPANY_DOCS,
    QueryFamily.CATALYST,
    QueryFamily.COMPETITIVE,
    QueryFamily.INDUSTRY,
    QueryFamily.RISK,
)
WAVE_OF: dict[QueryFamily, int] = {
    QueryFamily.COMPANY_DOCS: 2,
    QueryFamily.CATALYST: 2,
    QueryFamily.COMPETITIVE: 2,
    QueryFamily.INDUSTRY: 2,
    QueryFamily.RISK: 3,
}
EXPANSION_FAMILIES: tuple[QueryFamily, ...] = (QueryFamily.COMPETITIVE, QueryFamily.INDUSTRY)

#: Local-language queries per mode (English is always planned on top).
LOCALE_QUERIES_BY_MODE: dict[str, int] = {"quick": 0, "standard": 3, "deep": 6, "max": 8}
#: Results asked of the provider per query.
RESULTS_PER_QUERY = 8


# --------------------------------------------------------------------------- #
# Verified company facts (the planner's only inputs)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CompanyFacts:
    """Verified facts a query may be built from. Nothing here came from a fetched page."""

    legal_name: str
    short_name: str
    ticker: str | None = None
    #: A registry venue code (``PA``, ``XETRA``, ``US`` …), or None.
    venue: str | None = None
    #: Verified official domains only (``verified_issuer_sources``).
    official_domains: tuple[str, ...] = ()
    #: The classification's industry label (SIC/GICS text), a closed vocabulary.
    industry: str | None = None
    #: Commodity / product names the company's OWN documents state (subject profile).
    themes: tuple[str, ...] = ()
    #: A development-stage issuer (track C stage signals).
    development_stage: bool = False
    company_id: uuid.UUID | None = None

    def intent(self) -> dict[str, Any]:
        """What the model expansion may see, and what its cache key is made of."""
        return {
            "company": self.short_name,
            "ticker": self.ticker,
            "venue": self.venue,
            "industry": self.industry,
            "themes": list(self.themes),
            "development_stage": self.development_stage,
        }


_LEGAL_SUFFIX_TOKENS = frozenset(
    {
        "inc", "inc.", "corp", "corp.", "corporation", "co", "co.", "company", "ltd",
        "ltd.", "limited", "plc", "llc", "lp", "ag", "sa", "s.a.", "nv", "n.v.", "se",
        "ab", "asa", "as", "a/s", "oyj", "spa", "s.p.a.", "gmbh", "kgaa", "sas", "pty",
        "bhd", "holdings", "holding", "group", "the",
    }
)
_UPPER_KEEP = frozenset({"SA", "AG", "NV", "SE", "PLC", "ASA", "AB", "SPA"})


def display_name(name: str | None) -> str:
    """"SOUTHERN COPPER CORP/" → "Southern Copper Corp" (the registrant's legal form)."""
    text = " ".join((name or "").replace("/", " ").split())
    if text.isupper():
        text = " ".join(w if w in _UPPER_KEEP else w.title() for w in text.split())
    return text


def short_company_name(name: str | None) -> str:
    """The English short name: the legal name without trailing legal-form words."""
    words = display_name(name).split()
    while len(words) > 1 and words[-1].lower().rstrip(",") in _LEGAL_SUFFIX_TOKENS:
        words.pop()
    while len(words) > 1 and words[0].lower() == "the":
        words.pop(0)
    return " ".join(words).strip(" ,")


def facts_from_company(
    company: Any,
    *,
    industry: str | None = None,
    themes: Iterable[str] = (),
    development_stage: bool = False,
    official_domains: Iterable[str] = (),
) -> CompanyFacts:
    """:class:`CompanyFacts` from a ``Company`` row and what the pipeline already knows."""
    from app.services.discovery.identity import normalise_venue

    legal = display_name(getattr(company, "name", None))
    ticker = str(getattr(company, "ticker", "") or "").strip().upper() or None
    exchange = getattr(company, "exchange", None)
    venue = normalise_venue(exchange) or (str(exchange).strip().upper() if exchange else None)
    return CompanyFacts(
        legal_name=legal,
        short_name=short_company_name(legal) or legal,
        ticker=ticker,
        venue=venue,
        official_domains=tuple(dict.fromkeys(d.lower() for d in official_domains if d)),
        industry=(industry or "").strip() or None,
        themes=tuple(dict.fromkeys(t.strip() for t in themes if t and t.strip()))[:3],
        development_stage=bool(development_stage),
        company_id=getattr(company, "id", None),
    )


# --------------------------------------------------------------------------- #
# Freshness windows (spec §5.1)
# --------------------------------------------------------------------------- #

DAYS_90 = 90
DAYS_12M = 365
DAYS_18M = 548
DAYS_3Y = 3 * 365
DAYS_5Y = 5 * 365


def window_days(family: QueryFamily, mode: str, key: str = "") -> int:
    """The freshness window, in days, for a template of ``family`` under ``mode``."""
    deep = mode in ("deep", "max")
    if family is QueryFamily.CATALYST:
        return DAYS_12M if deep else DAYS_90
    if family is QueryFamily.COMPANY_DOCS:
        # "latest plus the previous" annual report: asked by explicit year, so the window
        # has to reach back past one filing cycle.
        return DAYS_3Y if key.startswith("annual") else DAYS_18M
    if family is QueryFamily.COMPETITIVE:
        return DAYS_3Y
    if family is QueryFamily.INDUSTRY:
        return DAYS_3Y if mode == "quick" else DAYS_5Y
    if family is QueryFamily.RISK:
        return DAYS_18M if key.startswith("litigation") else DAYS_12M
    return DAYS_3Y


# --------------------------------------------------------------------------- #
# Templates (versioned data — QUERY_TEMPLATE_VERSION)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Template:
    family: QueryFamily
    key: str
    pattern: str
    #: Lower runs first within its family.
    priority: int
    #: Slots the pattern needs; a template whose slot has no value is skipped.
    needs: tuple[str, ...] = ()
    #: The glossary concept a local-language variant is built from, if any.
    concept: str | None = None
    #: Only for a development-stage issuer.
    development_only: bool = False
    topic: str = "general"
    #: Words a result must relate to (selection's relevance terms).
    terms: tuple[str, ...] = ()


TEMPLATES: tuple[Template, ...] = (
    # -- COMPANY_DOCS: issuer material beyond filings ------------------------------
    Template(QueryFamily.COMPANY_DOCS, "presentation", "{company} investor presentation {year}", 0,
             concept="investor_presentation", terms=("presentation", "investor")),
    Template(QueryFamily.COMPANY_DOCS, "annual_latest", "{company} annual report {ar_year}", 1,
             concept="annual_report", terms=("annual", "report")),
    Template(QueryFamily.COMPANY_DOCS, "annual_previous",
             "{company} annual report {ar_prev_year}", 2, terms=("annual", "report")),
    Template(QueryFamily.COMPANY_DOCS, "capital_markets_day", "{company} capital markets day", 3,
             terms=("capital", "markets", "day")),
    # -- CATALYST: recent events ----------------------------------------------------
    Template(QueryFamily.CATALYST, "contract", "{company} contract order", 0,
             concept="contract_order", topic="news", terms=("contract", "order", "awarded")),
    Template(QueryFamily.CATALYST, "factory", "{company} new factory expansion", 1,
             concept="new_factory", topic="news", terms=("factory", "plant", "expansion")),
    Template(QueryFamily.CATALYST, "acquisition", "{company} acquisition agreement", 2,
             concept="acquisition", topic="news", terms=("acquisition", "acquire", "agreement")),
    Template(QueryFamily.CATALYST, "guidance", "{company} guidance outlook announcement", 3,
             topic="news", terms=("guidance", "outlook")),
    # -- COMPETITIVE: peers ---------------------------------------------------------
    Template(QueryFamily.COMPETITIVE, "competitors", "{company} competitors", 0,
             concept="competitors", terms=("competitor", "competitors", "rivals", "peers")),
    Template(QueryFamily.COMPETITIVE, "market_share", "{company} market share {industry}", 1,
             needs=("industry",), terms=("market", "share")),
    Template(QueryFamily.COMPETITIVE, "theme_producers",
             "{theme} producers competitive landscape", 2,
             needs=("theme",), terms=("producers", "competitive", "landscape")),
    # -- INDUSTRY: structure and context -------------------------------------------
    Template(QueryFamily.INDUSTRY, "value_chain", "{industry} value chain", 0,
             needs=("industry",), concept="industry_outlook", terms=("value", "chain")),
    Template(QueryFamily.INDUSTRY, "outlook", "{industry} market outlook {year}", 1,
             needs=("industry",), terms=("market", "outlook")),
    Template(QueryFamily.INDUSTRY, "theme_supply_demand", "{theme} supply demand outlook", 2,
             needs=("theme",), terms=("supply", "demand", "outlook")),
    Template(QueryFamily.INDUSTRY, "capacity", "{industry} capacity", 3,
             needs=("industry",), terms=("capacity",)),
    # -- RISK: disconfirming evidence ----------------------------------------------
    Template(QueryFamily.RISK, "delay", "{company} delay", 0,
             concept="delay", terms=("delay", "delayed", "postponed")),
    Template(QueryFamily.RISK, "litigation_lawsuit", "{company} lawsuit", 1,
             concept="lawsuit", terms=("lawsuit", "litigation", "court", "sued")),
    Template(QueryFamily.RISK, "profit_warning", "{company} profit warning", 2,
             concept="profit_warning", terms=("profit", "warning", "downgrade", "guidance")),
    Template(QueryFamily.RISK, "customer_loss", "{company} customer loss", 3,
             terms=("customer", "lost", "loss", "cancelled")),
    Template(QueryFamily.RISK, "oversupply", "{industry} oversupply", 4,
             needs=("industry",), terms=("oversupply", "glut", "surplus")),
    # -- development-stage issuer: project milestones (generic wording; CATALYST) ---
    Template(QueryFamily.CATALYST, "project_permits", "{company} project permits approval", 4,
             development_only=True, topic="news", terms=("permit", "permits", "approval")),
    Template(QueryFamily.CATALYST, "project_offtake", "{company} offtake agreement", 5,
             development_only=True, topic="news", terms=("offtake", "agreement")),
    Template(QueryFamily.CATALYST, "project_financing", "{company} project financing", 6,
             development_only=True, topic="news", terms=("financing", "funding", "loan")),
    Template(QueryFamily.CATALYST, "project_construction",
             "{company} construction commissioning timeline", 7,
             development_only=True, topic="news", terms=("construction", "commissioning")),
    Template(QueryFamily.CATALYST, "project_capex", "{company} project capex estimate", 8,
             development_only=True, terms=("capex", "capital", "cost")),
    Template(QueryFamily.CATALYST, "project_government_support",
             "{company} government support grant", 9,
             development_only=True, topic="news", terms=("government", "grant", "support")),
)

#: Terms a family's results should relate to when no template names its own (selection).
FAMILY_TERMS: dict[QueryFamily, tuple[str, ...]] = {
    QueryFamily.COMPANY_DOCS: ("annual", "report", "presentation", "investor", "results"),
    QueryFamily.CATALYST: ("contract", "order", "acquisition", "factory", "agreement",
                           "announces", "announced"),
    QueryFamily.COMPETITIVE: ("competitor", "competitors", "market", "share", "peers"),
    QueryFamily.INDUSTRY: ("industry", "market", "supply", "demand", "capacity", "outlook"),
    QueryFamily.RISK: ("delay", "lawsuit", "litigation", "warning", "risk", "decline"),
}

# --------------------------------------------------------------------------- #
# Venue → locale and the glossary (spec §5.2) — versioned data, reviewed like code
# --------------------------------------------------------------------------- #

#: Registry venue code → (language, ISO country). English is always planned as well.
VENUE_LOCALES: dict[str, tuple[str, str]] = {
    "XETRA": ("de", "DE"), "F": ("de", "DE"), "VI": ("de", "AT"),
    "SW": ("de", "CH"), "VX": ("de", "CH"),
    "PA": ("fr", "FR"), "MI": ("it", "IT"), "MC": ("es", "ES"), "MX": ("es", "MX"),
    "CO": ("da", "DK"), "ST": ("sv", "SE"), "OL": ("no", "NO"), "HE": ("fi", "FI"),
    "WA": ("pl", "PL"), "AS": ("nl", "NL"), "BR": ("nl", "BE"),
    "LS": ("pt", "PT"), "SA": ("pt", "BR"),
    "TSE": ("ja", "JP"), "HK": ("zh", "HK"), "SHG": ("zh", "CN"), "SHE": ("zh", "CN"),
    "KO": ("ko", "KR"), "KQ": ("ko", "KR"),
}

#: concept → language → the phrase. Company names stay in their legal local form.
GLOSSARY: dict[str, dict[str, str]] = {
    "annual_report": {
        "de": "Geschäftsbericht", "fr": "rapport annuel", "it": "relazione annuale",
        "es": "informe anual", "da": "årsrapport", "sv": "årsredovisning",
        "no": "årsrapport", "fi": "vuosikertomus", "pl": "raport roczny",
        "nl": "jaarverslag", "pt": "relatório anual", "ja": "有価証券報告書",
        "zh": "年度报告", "ko": "사업보고서",
    },
    "investor_presentation": {
        "de": "Investorenpräsentation", "fr": "présentation investisseurs",
        "it": "presentazione investitori", "es": "presentación inversores",
        "da": "investorpræsentation", "sv": "investerarpresentation",
        "no": "investorpresentasjon", "fi": "sijoittajaesitys",
        "pl": "prezentacja dla inwestorów", "nl": "investeerderspresentatie",
        "pt": "apresentação investidores", "ja": "決算説明資料", "zh": "投资者介绍",
        "ko": "투자자 설명자료",
    },
    "contract_order": {
        "de": "Auftrag Vertrag", "fr": "contrat commande", "it": "contratto ordine",
        "es": "contrato pedido", "da": "kontrakt ordre", "sv": "kontrakt order",
        "no": "kontrakt ordre", "fi": "sopimus tilaus", "pl": "kontrakt zamówienie",
        "nl": "contract order", "pt": "contrato encomenda", "ja": "受注 契約",
        "zh": "合同 订单", "ko": "계약 수주",
    },
    "new_factory": {
        "de": "neues Werk Erweiterung", "fr": "nouvelle usine extension",
        "it": "nuovo stabilimento ampliamento", "es": "nueva fábrica ampliación",
        "da": "ny fabrik udvidelse", "sv": "ny fabrik utbyggnad",
        "no": "ny fabrikk utvidelse", "fi": "uusi tehdas laajennus",
        "pl": "nowa fabryka rozbudowa", "nl": "nieuwe fabriek uitbreiding",
        "pt": "nova fábrica expansão", "ja": "新工場 増設", "zh": "新工厂 扩建",
        "ko": "신공장 증설",
    },
    "acquisition": {
        "de": "Übernahme Akquisition", "fr": "acquisition rachat", "it": "acquisizione",
        "es": "adquisición", "da": "opkøb", "sv": "förvärv", "no": "oppkjøp",
        "fi": "yrityskauppa", "pl": "przejęcie", "nl": "overname", "pt": "aquisição",
        "ja": "買収", "zh": "收购", "ko": "인수",
    },
    "competitors": {
        "de": "Wettbewerber", "fr": "concurrents", "it": "concorrenti", "es": "competidores",
        "da": "konkurrenter", "sv": "konkurrenter", "no": "konkurrenter",
        "fi": "kilpailijat", "pl": "konkurenci", "nl": "concurrenten", "pt": "concorrentes",
        "ja": "競合", "zh": "竞争对手", "ko": "경쟁사",
    },
    "industry_outlook": {
        "de": "Branche Marktausblick", "fr": "secteur perspectives du marché",
        "it": "settore prospettive di mercato", "es": "sector perspectivas del mercado",
        "da": "branche markedsudsigter", "sv": "bransch marknadsutsikter",
        "no": "bransje markedsutsikter", "fi": "toimiala markkinanäkymät",
        "pl": "branża perspektywy rynku", "nl": "sector marktvooruitzichten",
        "pt": "setor perspectivas de mercado", "ja": "業界 市場見通し",
        "zh": "行业 市场前景", "ko": "산업 시장 전망",
    },
    "delay": {
        "de": "Verzögerung", "fr": "retard", "it": "ritardo", "es": "retraso",
        "da": "forsinkelse", "sv": "försening", "no": "forsinkelse", "fi": "viivästys",
        "pl": "opóźnienie", "nl": "vertraging", "pt": "atraso", "ja": "遅延",
        "zh": "延误", "ko": "지연",
    },
    "lawsuit": {
        "de": "Klage Rechtsstreit", "fr": "procès litige", "it": "causa contenzioso",
        "es": "demanda litigio", "da": "retssag", "sv": "rättsprocess", "no": "søksmål",
        "fi": "oikeusprosessi", "pl": "pozew spór sądowy", "nl": "rechtszaak",
        "pt": "ação judicial", "ja": "訴訟", "zh": "诉讼", "ko": "소송",
    },
    "profit_warning": {
        "de": "Gewinnwarnung", "fr": "avertissement sur résultats", "it": "profit warning",
        "es": "aviso de beneficios", "da": "resultatadvarsel", "sv": "vinstvarning",
        "no": "resultatadvarsel", "fi": "tulosvaroitus", "pl": "ostrzeżenie o wynikach",
        "nl": "winstwaarschuwing", "pt": "alerta de resultados", "ja": "業績予想 下方修正",
        "zh": "盈利预警", "ko": "실적 경고",
    },
}


def locale_for_venue(venue: str | None) -> tuple[str, str] | None:
    """``(language, country)`` for the issuer's venue, or None (English only)."""
    return VENUE_LOCALES.get((venue or "").strip().upper()) if venue else None


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PlannedQuery:
    """One sanitised query ready for ``run_searches``, plus what selection needs."""

    request: SearchRequest
    family: QueryFamily
    key: str
    wave: int
    priority: int
    terms: tuple[str, ...] = ()
    locale: str | None = None

    @property
    def origin(self) -> str:
        return self.request.origin


@dataclass
class QueryPlan:
    queries: list[PlannedQuery] = field(default_factory=list)
    #: Template queries the sanitiser refused, with the code: recorded, never sent.
    refused: list[tuple[str, str]] = field(default_factory=list)
    #: Template queries dropped because the budget could not take them.
    trimmed: int = 0
    template_version: str = QUERY_TEMPLATE_VERSION
    mode: str = "standard"
    today: date | None = None
    expansion: dict[str, Any] = field(default_factory=dict)

    def by_wave(self, wave: int) -> list[PlannedQuery]:
        return [q for q in self.queries if q.wave == wave]

    def by_family(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for q in self.queries:
            out[q.family.value] = out.get(q.family.value, 0) + 1
        return out

    def terms_by_family(self) -> dict[QueryFamily, tuple[str, ...]]:
        out: dict[QueryFamily, list[str]] = {}
        for q in self.queries:
            out.setdefault(q.family, []).extend(q.terms)
        return {f: tuple(dict.fromkeys(t)) for f, t in out.items()}


def _slots(facts: CompanyFacts, today: date) -> dict[str, str]:
    return {
        "company": facts.short_name,
        "ticker": facts.ticker or "",
        "venue": facts.venue or "",
        "industry": (facts.industry or "").strip(),
        "theme": facts.themes[0] if facts.themes else "",
        "year": str(today.year),
        # The latest and the previous ANNUAL report: the filing year trails the calendar.
        "ar_year": str(today.year - 1),
        "ar_prev_year": str(today.year - 2),
    }


def _fill(pattern: str, slots: Mapping[str, str]) -> str:
    return " ".join(re.sub(r"\{(\w+)\}", lambda m: slots.get(m.group(1), ""), pattern).split())


def _request(
    text: str,
    template: Template,
    *,
    family: QueryFamily,
    key: str,
    mode: str,
    today: date,
    private_tokens: Collection[str],
    origin: str,
    version: str,
    language: str | None = None,
    country: str | None = None,
    site_domains: Sequence[str] = (),
) -> tuple[SearchRequest | None, str | None]:
    clean = sanitise_query(text, site_domains=site_domains, private_tokens=private_tokens)
    if not clean.ok:
        return None, clean.refusal
    days = window_days(family, mode, key)
    return (
        SearchRequest(
            query=clean.text,
            family=family,
            max_results=RESULTS_PER_QUERY,
            date_from=today - timedelta(days=days),
            date_to=today,
            country=country,
            language=language,
            topic="news" if template.topic == "news" else "general",  # type: ignore[arg-type]
            origin=origin,
            template_version=version[:40],
        ),
        None,
    )


def build_plan(
    facts: CompanyFacts,
    *,
    mode: str = "standard",
    max_queries: int = 16,
    max_locale_queries: int | None = None,
    today: date | None = None,
    private_tokens: Collection[str] = (),
    expansion: Sequence[str] = (),
    expansion_origin: str = ORIGIN_LLM_EXPANSION,
) -> QueryPlan:
    """The deterministic plan for ``facts`` under ``mode``, at most ``max_queries`` long.

    Same inputs → the same queries in the same order. ``expansion`` is the (already
    validated) model proposals; they join the COMPETITIVE / INDUSTRY pools after the
    templates and are recorded with ``origin="llm_expansion"``.
    """
    today = today or date.today()
    slots = _slots(facts, today)
    plan = QueryPlan(mode=mode, today=today)
    locale = locale_for_venue(facts.venue)
    n_locale = (
        LOCALE_QUERIES_BY_MODE.get(mode, 3) if max_locale_queries is None else max_locale_queries
    )

    # One rotating pool per family, plus a "project" pool for a development-stage
    # issuer's milestone templates (family CATALYST) so they take their own share of a
    # small budget instead of queuing behind the generic catalyst templates.
    pools: dict[str, list[PlannedQuery]] = {f.value: [] for f in WAVE_FAMILIES}
    pools["project"] = []
    pool_order = [
        QueryFamily.COMPANY_DOCS.value, QueryFamily.CATALYST.value, "project",
        QueryFamily.COMPETITIVE.value, QueryFamily.INDUSTRY.value, QueryFamily.RISK.value,
    ]
    locale_pool: list[PlannedQuery] = []
    seen: set[str] = set()

    def add(pool: list[PlannedQuery], query: PlannedQuery) -> None:
        marker = " ".join(query.request.query.split()).casefold()
        if marker in seen:
            return
        seen.add(marker)
        pool.append(query)

    for template in TEMPLATES:
        if template.development_only and not facts.development_stage:
            continue
        if any(not slots.get(need) for need in template.needs):
            continue
        text = _fill(template.pattern, slots)
        terms = template.terms or FAMILY_TERMS[template.family]
        request, refusal = _request(
            text, template, family=template.family, key=template.key, mode=mode,
            today=today, private_tokens=private_tokens, origin=ORIGIN_TEMPLATE,
            version=f"{QUERY_TEMPLATE_VERSION}:{template.key}",
        )
        if request is None:
            plan.refused.append((template.key, refusal or "refused"))
            continue
        add(
            pools["project" if template.development_only else template.family.value],
            PlannedQuery(request, template.family, template.key, WAVE_OF[template.family],
                         template.priority, terms),
        )
        if locale and template.concept and template.concept in GLOSSARY:
            phrase = GLOSSARY[template.concept].get(locale[0])
            if not phrase:
                continue
            local_text = " ".join(
                [facts.legal_name, phrase]
                + ([slots["ar_year"]] if "{ar_year}" in template.pattern else
                   [slots["year"]] if "{year}" in template.pattern else [])
            )
            local, local_refusal = _request(
                local_text, template, family=template.family, key=f"{template.key}.{locale[0]}",
                mode=mode, today=today, private_tokens=private_tokens,
                origin=ORIGIN_TEMPLATE,
                version=f"{QUERY_TEMPLATE_VERSION}:{template.key}.{locale[0]}",
                language=locale[0], country=locale[1],
            )
            if local is None:
                plan.refused.append((f"{template.key}.{locale[0]}", local_refusal or "refused"))
                continue
            add(
                locale_pool,
                PlannedQuery(local, template.family, f"{template.key}.{locale[0]}",
                             WAVE_OF[template.family], template.priority, terms,
                             locale=locale[0]),
            )

    # Model proposals: built apart from the template pools and appended AFTER the
    # templates are trimmed, so they spend the budget's expansion share (spec §19.1:
    # "within the above") and a full template set can never crowd them out or be
    # crowded out by them.
    expansion_template = Template(QueryFamily.INDUSTRY, "expansion", "", 99)
    exp_queries: list[PlannedQuery] = []
    for index, text in enumerate(expansion):
        family = EXPANSION_FAMILIES[index % len(EXPANSION_FAMILIES)]
        request, refusal = _request(
            text, expansion_template, family=family, key=f"expansion{index}", mode=mode,
            today=today, private_tokens=private_tokens, origin=expansion_origin,
            version=f"{QUERY_TEMPLATE_VERSION}+{EXPANSION_PROMPT_VERSION}",
        )
        if request is None:
            plan.refused.append((f"expansion{index}", refusal or "refused"))
            continue
        marker = " ".join(request.query.split()).casefold()
        if marker in seen:
            continue
        seen.add(marker)
        exp_queries.append(
            PlannedQuery(request, family, f"expansion{index}", WAVE_OF[family],
                         100 + index, FAMILY_TERMS[family])
        )

    for pool in pools.values():
        pool.sort(key=lambda q: q.priority)
    locale_pool.sort(key=lambda q: (q.priority, q.family.value))

    # Round-robin by family so a small budget still reaches every family (RISK always
    # runs): the first pass takes each family's top template, the next the second, …
    ordered: list[PlannedQuery] = []
    depth = 0
    first_pass_len = 0
    while any(depth < len(pool) for pool in pools.values()):
        for name in pool_order:
            if depth < len(pools[name]):
                ordered.append(pools[name][depth])
        if depth == 0:
            first_pass_len = len(ordered)
        depth += 1
    local_picks = locale_pool[: max(0, n_locale)]
    # Local-language variants take the slots just after the first pass of English ones.
    ordered = ordered[:first_pass_len] + local_picks + ordered[first_pass_len:]
    room = max(0, max_queries - len(exp_queries))
    plan.trimmed = max(0, len(ordered) - room)
    plan.queries = ordered[:room] + exp_queries[: max(0, max_queries)]
    return plan


def intent_hash(facts: CompanyFacts, plan: QueryPlan | Sequence[str]) -> str:
    """Stable identity of an expansion request: the facts and the template queries."""
    queries = (
        [q.request.query for q in plan.queries if q.origin == ORIGIN_TEMPLATE]
        if isinstance(plan, QueryPlan)
        else list(plan)
    )
    blob = json.dumps(
        {"intent": facts.intent(), "queries": queries}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Bounded model expansion (spec §4.3)
# --------------------------------------------------------------------------- #

_EXPANSION_SYSTEM = (
    "You propose additional web search queries for researching ONE public company's "
    "competitors and industry. Reply in json: {\"queries\": [\"...\"]}. Each query is at "
    f"most {MAX_EXPANSION_WORDS} words of plain words: synonyms, technology names, "
    "process terms, product categories. No URLs, no search operators, no quotation "
    "marks, no company other than the one named. You are given only the company, its "
    "industry and the queries already planned."
)

_CACHE_MAX = 256
_CACHE: OrderedDict[tuple[str, str, str], tuple[str, ...]] = OrderedDict()


def clear_expansion_cache() -> None:
    _CACHE.clear()


@dataclass
class ExpansionResult:
    queries: tuple[str, ...] = ()
    from_cache: bool = False
    proposed: int = 0
    refused: dict[str, int] = field(default_factory=dict)
    units: ConsumptionUnits = field(default_factory=ConsumptionUnits)
    error: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "queries": len(self.queries),
            "proposed": self.proposed,
            "from_cache": self.from_cache,
            "refused": dict(self.refused),
            "model": self.model,
            "error": self.error,
            "prompt_version": EXPANSION_PROMPT_VERSION,
        }


_FORBIDDEN_CHARS_RE = re.compile(r"[\"“”<>{}\[\]\\|]")


def validate_proposals(
    raw: Iterable[Any],
    *,
    template_queries: Iterable[str],
    limit: int,
    private_tokens: Collection[str] = (),
) -> tuple[list[str], dict[str, int]]:
    """Proposals that may join the plan, and why the rest did not (counts by code)."""
    existing = {" ".join(q.split()).casefold() for q in template_queries}
    accepted: list[str] = []
    refused: dict[str, int] = {}

    def refuse(code: str) -> None:
        refused[code] = refused.get(code, 0) + 1

    for item in raw:
        if not isinstance(item, str):
            refuse("not_text")
            continue
        text = " ".join(item.split())
        if not text:
            refuse("empty")
            continue
        if len(accepted) >= limit:
            refuse("over_cap")
            continue
        if len(text.split()) > MAX_EXPANSION_WORDS:
            refuse("too_many_words")
            continue
        if len(text) > MAX_QUERY_CHARS or _FORBIDDEN_CHARS_RE.search(text):
            refuse("bad_characters")
            continue
        cleaned = sanitise_query(text, private_tokens=private_tokens)
        if not cleaned.ok:
            refuse(cleaned.refusal or "refused")
            continue
        if cleaned.extracted_urls or cleaned.stripped_operators or cleaned.text != text:
            # A URL or an operator was in it: refuse, never "fix" it into a query the
            # model did not propose.
            refuse("url_or_operator")
            continue
        if find_private_token(text, private_tokens):
            refuse("G1_private_token")
            continue
        marker = text.casefold()
        if marker in existing or any(marker == a.casefold() for a in accepted):
            refuse("duplicate")
            continue
        accepted.append(text)
    return accepted, refused


async def propose_expansion(
    transport: Any,
    facts: CompanyFacts,
    plan: QueryPlan,
    *,
    limit: int,
    max_tokens: int,
    private_tokens: Collection[str] = (),
    timeout: int = 60,
) -> ExpansionResult:
    """Ask the model for up to ``limit`` extra queries. Never raises.

    ``transport`` is a DeepSeek-shaped transport (``complete(...)``). The prompt carries
    the intent and the template queries only — no page text, snippet or title, by
    construction: this function has no parameter that could carry one.
    """
    result = ExpansionResult()
    if transport is None or limit <= 0 or max_tokens <= 0:
        return result
    model = str(getattr(transport, "model", "") or "unknown")
    result.model = model
    template_queries = [q.request.query for q in plan.queries if q.origin == ORIGIN_TEMPLATE]
    key = (intent_hash(facts, template_queries), model, EXPANSION_PROMPT_VERSION)
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        result.queries = tuple(cached[:limit])
        result.from_cache = True
        return result
    user = json.dumps(
        {
            "company": facts.short_name,
            "industry": facts.industry,
            "themes": list(facts.themes),
            "already_planned": template_queries,
            "max_queries": limit,
        },
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
        proposals, template_queries=template_queries, limit=limit,
        private_tokens=private_tokens,
    )
    result.queries = tuple(accepted)
    result.refused = refused
    _CACHE[key] = tuple(accepted)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return result


__all__ = [
    "EXPANSION_FAMILIES",
    "EXPANSION_PROMPT_VERSION",
    "FAMILY_TERMS",
    "GLOSSARY",
    "GLOSSARY_VERSION",
    "LOCALE_QUERIES_BY_MODE",
    "MAX_EXPANSION_WORDS",
    "ORIGIN_LLM_EXPANSION",
    "ORIGIN_TEMPLATE",
    "QUERY_TEMPLATE_VERSION",
    "TEMPLATES",
    "VENUE_LOCALES",
    "WAVE_FAMILIES",
    "WAVE_OF",
    "CompanyFacts",
    "ExpansionResult",
    "PlannedQuery",
    "QueryPlan",
    "Template",
    "build_plan",
    "clear_expansion_cache",
    "display_name",
    "facts_from_company",
    "intent_hash",
    "locale_for_venue",
    "propose_expansion",
    "short_company_name",
    "validate_proposals",
    "window_days",
]
