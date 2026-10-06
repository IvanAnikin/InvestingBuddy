"""The bounded web follow-up loop — open-web W7 (spec §7.3–§7.5, §19.1 "targeted").

WHAT THIS IS
============
The company web stage (W5) runs once, before the Council's questions exist. The Director
loop then finds what is still missing — a margin trend nobody sourced, an offtake
counterparty, a permit status — and until W7 the only thing it could do about a gap was
re-read the same corpus. This module is the **web rung** of that loop: given the open,
closable gaps whose field a web search could plausibly answer, it asks the web *those*
questions, fetches and ingests what comes back through the same W2/W3 machinery the stage
uses, and tells the loop whether anything new and relevant arrived.

    gap → GAP queries (generic, versioned templates) → search → select → fetch → ingest
        → the specialist re-reads the corpus → findings → track B reconciles the gap

It is a **rung, not a second loop**: the Director owns rounds, tasks and stop reasons
(``director/loop.py``); this module owns *what to ask the web* and *what the web gave*.

NOTHING A PAGE SAID CAN BECOME A QUERY (threat model PI-07)
==========================================================
A follow-up query is built ONLY from: a versioned template, the verified company's short
and legal name (identity verification, never a page), its ticker/venue, the classification
industry and subject-profile commodity names (closed vocabularies), the calendar year,
and — for a contradiction — the **label of a closed research field** (``research_fields``).
A gap's DESCRIPTION is read to decide *which* closed topic or field it concerns; no word
of it is copied. A project name, a counterparty, a figure or any instruction a model
wrote after reading a page therefore cannot reach a search provider. Every query still
passes the W1 sanitiser (G1 private tokens, operators, URLs, blocklist) before it leaves
the process.

WHAT A WEB SEARCH CAN PLAUSIBLY ANSWER
======================================
Margin trend, backlog quality, capacity and its cost, customer concentration, capex plans,
first production and commissioning, offtake counterparties and volumes, permits and
approvals, financing (committed vs conditional), cash runway, project economics
(NPV/IRR/feasibility) and resource/reserve estimates. Statement figures of a filing
(revenue, cash, net debt, operating cash flow) are deliberately NOT here: they are
filings-only (W4 P4) and a press article is not their source. A gap with no recognised
topic gets no follow-up — fail closed. A ``conflicting_sources`` gap gets one follow-up,
once, keyed to the field the disagreement is about.

BOUNDED
=======
Each round spends the ``followup`` budget profile (spec §19.1: at most 6 queries, 12
fetches, 3 PDFs, 30 MB, 3 minutes), clamped by the operator's ``V3_RUN_MAX_WEB_SEARCHES``
and the platform's daily cap exactly as the stage is; the number of web rounds is capped
per mode (:data:`WEB_ROUNDS_BY_MODE`) and by the Director's own round limit; and a round
whose documents add
nothing relevant ends the web rung as **saturation**. Every query, fetch and document is a
provenance row exactly like the stage's; the round records are secret-free (templates,
counts, codes).

THE RED TEAM'S SEARCH (spec §7.4)
=================================
:meth:`WebFollowup.challenge_wave` issues neutral RISK queries (delay, lawsuit, permit
problem, project cancellation, customer loss, financing risk, cost overrun, production
issue; and — from the subject profile — a technology's disadvantages, an industry's
oversupply, a commodity's substitutes) that the W5 plan has not already run, always
including one counter-thesis query when one exists. What it stores is handed to the Red
Team as :class:`RiskEvidence` — each item with its source class, origin and date — and
:func:`assess_challenge_basis` applies the claim-type rules: **a single low-trust source
cannot carry a challenge**.

Logs carry counts, codes and states — never a query, a URL or page text.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa

from app.core.structured_logging import log_event
from app.services import research_fields as rf
from app.services.consumption import ConsumptionUnits
from app.services.providers.contracts import QueryFamily, SearchRequest, SearchResultItem
from app.services.web_research.budget import WebResearchBudget, budget_for_run
from app.services.web_research.planner import (
    RESULTS_PER_QUERY,
    CompanyFacts,
    PlannedQuery,
    facts_from_company,
    locale_for_venue,
)
from app.services.web_research.queries import sanitise_query
from app.services.web_research.search import (
    STATE_DISABLED,
    STATE_UNAVAILABLE,
    SearchContext,
    SearchRunResult,
    run_searches,
)
from app.services.web_research.selection import (
    W_RELEVANCE,
    Scored,
    SearchCandidate,
    SelectionContext,
    domain_of,
    host_of,
    select_results,
)
from app.services.web_research.stage import (
    _UNSET,
    DISPOSITION_FETCH_FAILED,
    DISPOSITION_INGESTED,
    DISPOSITION_NOT_INGESTED,
    DISPOSITION_NOT_RETRIEVABLE,
    DISPOSITION_REUSED,
    DISPOSITION_SELECTED,
    DISPOSITION_SKIPPED,
    StageDeps,
    WebRunContext,
    _candidate_entity,
    _held_urls,
    _identity_terms,
    _issuer_domains,
    _result_rows,
    _verified_issuer,
    persist_factory_for,
)

logger = logging.getLogger(__name__)

FOLLOWUP_TEMPLATE_VERSION = "w7.1"
FOLLOWUP_GLOSSARY_VERSION = "2026-10-05.1"
SUMMARY_VERSION = 1

#: The "followup" budget profile (spec §19.1 "Targeted follow-up"), per round.
BUDGET_PROFILE = "followup"

#: Gaps targeted per round (each brings up to ``MAX_QUERIES_PER_GAP`` queries; the profile
#: caps the round at 6 whatever these multiply to).
MAX_GAPS_PER_ROUND = 3
MAX_QUERIES_PER_GAP = 2
#: Local-language variants per round (English is always asked first).
MAX_LOCALE_QUERIES_PER_ROUND = 2
#: RISK queries in the challenge wave (one slot is held for a counter-thesis query).
CHALLENGE_QUERIES = 3
#: Web rounds per mode (the Director's own ``max_rounds`` bounds them again, and its last
#: round is never a web round: nothing could read what it fetched).
WEB_ROUNDS_BY_MODE: dict[str, int] = {"quick": 1, "standard": 2, "deep": 3, "max": 4}
#: A newly stored document counts as "relevant" when at least this share of the topic's
#: terms appears in its result's title, snippet or URL path (selection's relevance, 0..1).
SATURATION_RELEVANCE_THRESHOLD = 0.3
#: Candidates the Investigator step may fetch for ONE question in one call.
MAX_CANDIDATE_FETCHES_PER_QUESTION = 3
MAX_CHUNKS_PER_RISK_DOCUMENT = 2
MAX_RISK_EVIDENCE_ITEMS = 12
RISK_EXCERPT_CHARS = 280
#: Gap types a web search could plausibly close or illuminate. ``tool_unavailable`` (a
#: platform tool is absent), ``scope_unknown``, ``entity_ambiguous``, ``calculation_refused``
#: and ``budget_exhausted`` are not about what the web holds.
FOLLOWUP_GAP_TYPES: frozenset[str] = frozenset(
    {"evidence_unavailable", "source_unreachable", "period_missing", "conflicting_sources"}
)
#: Gap types track B can close with a finding (``gap_reconciliation.CLOSABLE_GAP_TYPES``).
ABSENCE_GAP_TYPES: frozenset[str] = frozenset(
    {
        "evidence_unavailable",
        "source_unreachable",
        "tool_unavailable",
        "period_missing",
        "transcript_unavailable",
    }
)

KIND_ABSENCE = "absence"
KIND_CONTRADICTION = "contradiction"

STOP_BUDGET = "web_budget"
STATE_OK = "ok"
STATE_NO_NEW_QUERIES = "no_new_queries"
STATE_BUDGET = "budget"
STATE_UNAVAILABLE_WEB = "web_search_unavailable"
STATE_FAILED = "web_followup_failed"
STATE_WALL = "wall_time"



# --------------------------------------------------------------------------- #
# Gap topics — versioned data (FOLLOWUP_TEMPLATE_VERSION)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GapTopic:
    """One thing a web search could answer, and how to ask for it. Generic wording only."""

    key: str
    #: The glossary concept a local-language variant is built from.
    concept: str
    #: English templates; slots are ``{company}`` ``{year}`` and (contradictions only)
    #: ``{field}``. NEVER an issuer name from a page, a project or a counterparty.
    templates: tuple[str, ...]
    #: Freshness window in days (by gap type): how old a source may be and still answer.
    window: int
    #: Platform-owned words a result should relate to (selection relevance + saturation).
    terms: tuple[str, ...]
    #: ``research_fields`` keys this topic answers.
    fields: tuple[str, ...] = ()
    #: Regexes over LOWER-CASED gap / question text for gaps the field vocabulary has no
    #: key for (margin, backlog, customers, permits, financing).
    patterns: tuple[str, ...] = ()


_DAYS_6M = 183
_DAYS_12M = 365
_DAYS_18M = 548
_DAYS_3Y = 1095

TOPICS: tuple[GapTopic, ...] = (
    GapTopic(
        "margin_trend",
        "margin",
        ("{company} margin guidance", "{company} gross margin outlook {year}"),
        _DAYS_18M,
        ("margin", "margins", "guidance", "outlook", "profitability"),
        patterns=(
            r"\b(?:gross|operating|ebitda|net|profit)\s+margins?\b",
            r"\bmargins?\b",
            r"\bprofitability\b",
        ),
    ),
    GapTopic(
        "backlog_quality",
        "backlog",
        ("{company} order backlog profitability", "{company} order intake backlog {year}"),
        _DAYS_12M,
        ("backlog", "orders", "order", "profitability", "intake"),
        patterns=(r"\bbacklog\b", r"\border\s+(?:book|intake)\b"),
    ),
    GapTopic(
        "capacity",
        "capacity",
        ("{company} capacity expansion cost", "{company} nameplate capacity ramp-up"),
        _DAYS_18M,
        ("capacity", "expansion", "cost", "nameplate", "ramp"),
        fields=("metric:production_capacity",),
        patterns=(r"\bcapacity\b", r"\bnameplate\b", r"\bthroughput\b"),
    ),
    GapTopic(
        "customer_concentration",
        "customer",
        ("{company} largest customer", "{company} customer concentration"),
        _DAYS_3Y,
        ("customer", "customers", "largest", "concentration"),
        patterns=(r"\b(?:largest|major|key|top)\s+customers?\b", r"\bcustomer\s+concentration\b"),
    ),
    GapTopic(
        "capex",
        "capex",
        ("{company} capital expenditure plan {year}", "{company} capex guidance"),
        _DAYS_18M,
        ("capex", "capital", "expenditure", "guidance", "plan"),
        fields=("metric:capex", "metric:capex_sustaining", "metric:capex_period"),
        patterns=(r"\bcapex\b", r"\bcapital\s+expenditures?\b"),
    ),
    GapTopic(
        "project_capex",
        "capex",
        ("{company} project capital cost estimate", "{company} feasibility study capex"),
        _DAYS_3Y,
        ("capital", "cost", "estimate", "feasibility", "capex"),
        fields=("metric:capex_project",),
    ),
    GapTopic(
        "first_production",
        "milestone",
        ("{company} first production timeline", "{company} commissioning schedule update"),
        _DAYS_12M,
        ("production", "first", "commissioning", "timeline", "schedule"),
        fields=("milestone:first_production", "milestone:commissioning"),
        patterns=(r"\bfirst\s+production\b", r"\bcommissioning\b"),
    ),
    GapTopic(
        "final_investment_decision",
        "milestone",
        ("{company} final investment decision timing",),
        _DAYS_12M,
        ("final", "investment", "decision", "timing"),
        fields=("milestone:final_investment_decision",),
    ),
    GapTopic(
        "offtake",
        "offtake",
        ("{company} offtake agreement counterparties volumes", "{company} offtake agreement"),
        _DAYS_18M,
        ("offtake", "agreement", "counterparties", "volumes"),
        fields=("commercial:offtake",),
    ),
    GapTopic(
        "permits",
        "permits",
        ("{company} permits approvals status", "{company} permit approval granted"),
        _DAYS_18M,
        ("permit", "permits", "approval", "approvals", "status"),
        patterns=(
            r"\bpermits?\b",
            r"\bpermitting\b",
            r"\blicen[cs]es?\b",
            r"\benvironmental\s+(?:approval|impact)\b",
        ),
    ),
    GapTopic(
        "financing",
        "financing",
        ("{company} project financing committed conditional", "{company} funding facility"),
        _DAYS_12M,
        ("financing", "funding", "facility", "committed", "conditional"),
        patterns=(
            r"\bproject\s+financ\w+",
            r"\bdebt\s+facilit\w+",
            r"\bfunding\b",
            r"\bfinancing\b",
        ),
    ),
    GapTopic(
        "cash_runway",
        "runway",
        ("{company} cash runway funding", "{company} capital raise funding position"),
        _DAYS_6M,
        ("cash", "runway", "funding", "raise", "position"),
        fields=("metric:cash_runway",),
    ),
    GapTopic(
        "project_economics",
        "economics",
        ("{company} feasibility study NPV IRR", "{company} project economics study"),
        _DAYS_3Y,
        ("feasibility", "study", "npv", "irr", "economics"),
        fields=("metric:npv", "metric:irr"),
    ),
    GapTopic(
        "resource_reserve",
        "resource",
        ("{company} mineral resource estimate", "{company} reserve estimate update"),
        _DAYS_3Y,
        ("resource", "reserve", "estimate", "mineral", "update"),
        fields=("metric:mineral_resource",),
    ),
)

#: A contradiction between two sources is followed up by the FIELD it is about.
CONTRADICTION_TOPIC = GapTopic(
    "contradiction",
    "contradiction",
    ("{company} {field} {year}", "{company} {field} reported figure"),
    _DAYS_12M,
    ("reported", "figure", "annual", "results", "update"),
)

TOPIC_BY_KEY: dict[str, GapTopic] = {t.key: t for t in (*TOPICS, CONTRADICTION_TOPIC)}

_FIELD_TOPICS: dict[str, tuple[str, ...]] = {}
for _topic in TOPICS:
    for _field_key in _topic.fields:
        _FIELD_TOPICS[_field_key] = (*_FIELD_TOPICS.get(_field_key, ()), _topic.key)

_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    t.key: tuple(re.compile(p) for p in t.patterns) for t in TOPICS
}

#: concept → language → phrase. The company's legal name stays in its local form.
#: Versioned data, reviewed like code (``FOLLOWUP_GLOSSARY_VERSION``).
GAP_GLOSSARY: dict[str, dict[str, str]] = {
    "margin": {
        "de": "Marge Prognose",
        "fr": "marge prévisions",
        "it": "margine previsioni",
        "es": "margen previsiones",
        "pt": "margem previsões",
        "nl": "marge vooruitzichten",
        "sv": "marginal prognos",
        "da": "margin forventninger",
        "no": "margin prognose",
        "fi": "kate ennuste",
        "pl": "marża prognoza",
        "ja": "利益率 見通し",
        "zh": "利润率 展望",
        "ko": "마진 전망",
    },
    "backlog": {
        "de": "Auftragsbestand Profitabilität",
        "fr": "carnet de commandes rentabilité",
        "it": "portafoglio ordini redditività",
        "es": "cartera de pedidos rentabilidad",
        "pt": "carteira de encomendas rentabilidade",
        "nl": "orderportefeuille winstgevendheid",
        "sv": "orderstock lönsamhet",
        "da": "ordrebeholdning rentabilitet",
        "no": "ordrereserve lønnsomhet",
        "fi": "tilauskanta kannattavuus",
        "pl": "portfel zamówień rentowność",
        "ja": "受注残 収益性",
        "zh": "在手订单 盈利能力",
        "ko": "수주잔고 수익성",
    },
    "capacity": {
        "de": "Kapazität Erweiterung Kosten",
        "fr": "capacité extension coût",
        "it": "capacità ampliamento costi",
        "es": "capacidad ampliación coste",
        "pt": "capacidade expansão custo",
        "nl": "capaciteit uitbreiding kosten",
        "sv": "kapacitet utbyggnad kostnad",
        "da": "kapacitet udvidelse omkostning",
        "no": "kapasitet utvidelse kostnad",
        "fi": "kapasiteetti laajennus kustannus",
        "pl": "moce produkcyjne rozbudowa koszt",
        "ja": "生産能力 増強 費用",
        "zh": "产能 扩建 成本",
        "ko": "생산능력 증설 비용",
    },
    "customer": {
        "de": "größter Kunde",
        "fr": "principal client",
        "it": "principale cliente",
        "es": "mayor cliente",
        "pt": "maior cliente",
        "nl": "grootste klant",
        "sv": "största kund",
        "da": "største kunde",
        "no": "største kunde",
        "fi": "suurin asiakas",
        "pl": "największy klient",
        "ja": "主要顧客",
        "zh": "最大客户",
        "ko": "최대 고객",
    },
    "capex": {
        "de": "Investitionen Capex",
        "fr": "investissements capex",
        "it": "investimenti capex",
        "es": "inversiones capex",
        "pt": "investimentos capex",
        "nl": "investeringen capex",
        "sv": "investeringar capex",
        "da": "investeringer capex",
        "no": "investeringer capex",
        "fi": "investoinnit capex",
        "pl": "nakłady inwestycyjne",
        "ja": "設備投資",
        "zh": "资本支出",
        "ko": "설비투자",
    },
    "milestone": {
        "de": "Produktionsstart Zeitplan",
        "fr": "démarrage production calendrier",
        "it": "avvio produzione calendario",
        "es": "inicio producción calendario",
        "pt": "início produção cronograma",
        "nl": "start productie planning",
        "sv": "produktionsstart tidsplan",
        "da": "produktionsstart tidsplan",
        "no": "produksjonsstart tidsplan",
        "fi": "tuotannon aloitus aikataulu",
        "pl": "start produkcji harmonogram",
        "ja": "生産開始 スケジュール",
        "zh": "投产 时间表",
        "ko": "생산 개시 일정",
    },
    "offtake": {
        "de": "Abnahmevertrag",
        "fr": "contrat d'enlèvement",
        "it": "contratto di offtake",
        "es": "contrato de compra offtake",
        "pt": "contrato de offtake",
        "nl": "offtake-overeenkomst",
        "sv": "offtake-avtal",
        "da": "offtake-aftale",
        "no": "offtake-avtale",
        "fi": "offtake-sopimus",
        "pl": "umowa offtake",
        "ja": "オフテイク契約",
        "zh": "包销协议",
        "ko": "오프테이크 계약",
    },
    "permits": {
        "de": "Genehmigung Status",
        "fr": "permis autorisation statut",
        "it": "permessi autorizzazione stato",
        "es": "permisos autorización estado",
        "pt": "licenças autorização estado",
        "nl": "vergunning status",
        "sv": "tillstånd status",
        "da": "tilladelse status",
        "no": "tillatelse status",
        "fi": "lupa tila",
        "pl": "pozwolenie status",
        "ja": "許認可 状況",
        "zh": "许可 审批 状态",
        "ko": "인허가 현황",
    },
    "financing": {
        "de": "Projektfinanzierung",
        "fr": "financement du projet",
        "it": "finanziamento del progetto",
        "es": "financiación del proyecto",
        "pt": "financiamento do projeto",
        "nl": "projectfinanciering",
        "sv": "projektfinansiering",
        "da": "projektfinansiering",
        "no": "prosjektfinansiering",
        "fi": "hankerahoitus",
        "pl": "finansowanie projektu",
        "ja": "プロジェクトファイナンス",
        "zh": "项目融资",
        "ko": "프로젝트 금융",
    },
    "runway": {
        "de": "Liquiditätsreichweite Finanzierung",
        "fr": "trésorerie financement horizon",
        "it": "liquidità finanziamento",
        "es": "liquidez financiación",
        "pt": "liquidez financiamento",
        "nl": "liquiditeit financiering",
        "sv": "likviditet finansiering",
        "da": "likviditet finansiering",
        "no": "likviditet finansiering",
        "fi": "kassavarat rahoitus",
        "pl": "płynność finansowanie",
        "ja": "資金繰り 資金調達",
        "zh": "现金 融资",
        "ko": "현금 자금조달",
    },
    "economics": {
        "de": "Machbarkeitsstudie Kapitalwert",
        "fr": "étude de faisabilité valeur actuelle nette",
        "it": "studio di fattibilità valore attuale netto",
        "es": "estudio de viabilidad valor actual neto",
        "pt": "estudo de viabilidade valor presente",
        "nl": "haalbaarheidsstudie netto contante waarde",
        "sv": "förstudie nuvärde",
        "da": "forundersøgelse nutidsværdi",
        "no": "mulighetsstudie nåverdi",
        "fi": "toteutettavuustutkimus nettonykyarvo",
        "pl": "studium wykonalności NPV",
        "ja": "フィージビリティスタディ 正味現在価値",
        "zh": "可行性研究 净现值",
        "ko": "타당성 조사 순현재가치",
    },
    "resource": {
        "de": "Ressourcenschätzung",
        "fr": "estimation des ressources",
        "it": "stima delle risorse",
        "es": "estimación de recursos",
        "pt": "estimativa de recursos",
        "nl": "resourceschatting",
        "sv": "resursuppskattning",
        "da": "ressourceopgørelse",
        "no": "ressursestimat",
        "fi": "resurssiarvio",
        "pl": "szacunek zasobów",
        "ja": "資源量 推定",
        "zh": "资源量 估算",
        "ko": "자원량 추정",
    },
}


# --------------------------------------------------------------------------- #
# Gaps → topics
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FollowupGap:
    """An open gap the web rung may target, reduced to platform-owned vocabulary."""

    gap_id: str
    question_key: str | None
    gap_type: str
    kind: str
    #: Closed topic keys (``TOPICS``). No word of the gap's description is kept.
    topics: tuple[str, ...]
    #: Closed ``research_fields`` keys the gap's own text names.
    fields: tuple[str, ...] = ()
    blocking: bool = False

    @property
    def provable(self) -> bool:
        """Could track B close this gap with a finding? Only an absence gap whose OWN
        text names a research field can be proven closed (``gap_text`` source)."""
        return (
            self.kind == KIND_ABSENCE and self.gap_type in ABSENCE_GAP_TYPES and bool(self.fields)
        )


def topics_for_text(text: str | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(topic keys, field keys)`` a piece of text concerns. Never returns text."""
    raw = (text or "").lower()
    if not raw.strip():
        return (), ()
    fields = tuple(f for f in rf.fields_mentioned(text) if f in rf.FIELDS_BY_KEY)
    topics: list[str] = []
    for field_key in fields:
        for topic_key in _FIELD_TOPICS.get(field_key, ()):
            if topic_key not in topics:
                topics.append(topic_key)
    for topic in TOPICS:
        if topic.key in topics:
            continue
        if any(p.search(raw) for p in _PATTERNS[topic.key]):
            topics.append(topic.key)
    return tuple(topics), fields


def classify_gap(
    *,
    gap_id: Any,
    gap_type: str,
    description: str | None,
    question_key: str | None = None,
    question_text: str | None = None,
    blocking: bool = False,
) -> FollowupGap | None:
    """The web rung's view of one gap, or ``None`` when no web search could answer it."""
    if gap_type not in FOLLOWUP_GAP_TYPES:
        return None
    topics, fields = topics_for_text(description)
    if gap_type == "conflicting_sources":
        # The disagreement is about a research FIELD; no field, no safe query.
        named = tuple(fields[:2])
        if not named:
            return None
        return FollowupGap(
            str(gap_id),
            question_key,
            gap_type,
            KIND_CONTRADICTION,
            ("contradiction",),
            named,
            blocking,
        )
    own_fields = fields
    if not topics and question_text:
        # The question is platform-owned (a playbook wrote it); the gap's own words named
        # nothing recognisable, so the question's topic stands in — and the gap is not
        # provable (its OWN text has no field), which only affects the "answered" stop.
        topics, _question_fields = topics_for_text(question_text)
    if not topics:
        return None
    return FollowupGap(
        str(gap_id),
        question_key,
        gap_type,
        KIND_ABSENCE,
        tuple(topics[:2]),
        own_fields,
        blocking,
    )


def select_gaps(
    gaps: Sequence[Any],
    *,
    handled: Collection[str] = (),
    question_texts: Mapping[str, str] | None = None,
    blocking_keys: Collection[str] = (),
    limit: int = MAX_GAPS_PER_ROUND,
) -> tuple[list[FollowupGap], list[FollowupGap]]:
    """``(this round's gaps, every web-answerable gap not yet handled)``.

    Pure. A gap is handled ONCE (``handled`` holds its id), so a contradiction yields
    exactly one follow-up. Blocking questions first, then contradictions (they cost one
    query pair and unblock a disagreement), then the oldest gap.
    """
    texts = question_texts or {}
    candidates: list[FollowupGap] = []
    for gap in gaps:
        gap_id = str(getattr(gap, "id", ""))
        if not gap_id or gap_id in handled:
            continue
        if getattr(gap, "status", "open") != "open" or not getattr(gap, "closable", True):
            continue
        key = getattr(gap, "question_key", None)
        classified = classify_gap(
            gap_id=gap_id,
            gap_type=str(getattr(gap, "gap_type", "")),
            description=getattr(gap, "description", None),
            question_key=key,
            question_text=texts.get(key or ""),
            blocking=bool(key and key in blocking_keys),
        )
        if classified is not None:
            candidates.append(classified)
    # ``open_gaps`` returns newest first; the ordering below is stable on that.
    candidates.sort(
        key=lambda g: (0 if g.blocking else 1, 0 if g.kind == KIND_CONTRADICTION else 1)
    )
    return candidates[: max(0, limit)], candidates


# --------------------------------------------------------------------------- #
# Query building (pure, deterministic)
# --------------------------------------------------------------------------- #


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _fill(pattern: str, slots: Mapping[str, str]) -> str:
    return " ".join(re.sub(r"\{(\w+)\}", lambda m: slots.get(m.group(1), ""), pattern).split())


def _slots(facts: CompanyFacts, today: date, field_label: str = "") -> dict[str, str]:
    return {
        "company": facts.short_name,
        "year": str(today.year),
        "field": field_label,
        "theme": facts.themes[0] if facts.themes else "",
        "industry": (facts.industry or "").strip(),
    }


@dataclass
class BuiltQueries:
    queries: list[PlannedQuery] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    #: Queries not asked because an identical one already ran (or repeats within the set).
    deduped: int = 0
    trimmed: int = 0


def _gap_request(
    text: str,
    topic: GapTopic,
    key: str,
    *,
    today: date,
    private_tokens: Collection[str],
    language: str | None = None,
    country: str | None = None,
) -> tuple[SearchRequest | None, str | None]:
    clean = sanitise_query(text, private_tokens=private_tokens)
    if not clean.ok:
        return None, clean.refusal
    return (
        SearchRequest(
            query=clean.text,
            family=QueryFamily.GAP,
            max_results=RESULTS_PER_QUERY,
            date_from=today - timedelta(days=topic.window),
            date_to=today,
            country=country,
            language=language,
            topic="general",
            origin="template",
            template_version=f"{FOLLOWUP_TEMPLATE_VERSION}:{key}"[:40],
        ),
        None,
    )


def build_gap_queries(
    gaps: Sequence[FollowupGap],
    facts: CompanyFacts,
    *,
    today: date,
    executed: Collection[str] = (),
    max_queries: int = 6,
    max_locale: int = MAX_LOCALE_QUERIES_PER_ROUND,
    private_tokens: Collection[str] = (),
) -> BuiltQueries:
    """The GAP queries for ``gaps``: English first (one per gap before any gap's second),
    then local-language variants, deduplicated against ``executed`` and each other.

    Same inputs, same queries, same order.
    """
    built = BuiltQueries()
    seen = {_norm(q) for q in executed}
    english: list[PlannedQuery] = []
    local: list[PlannedQuery] = []

    def add(pool: list[PlannedQuery], query: PlannedQuery) -> None:
        marker = _norm(query.request.query)
        if marker in seen:
            built.deduped += 1
            return
        seen.add(marker)
        pool.append(query)

    for depth in range(MAX_QUERIES_PER_GAP):
        for index, gap in enumerate(gaps):
            for topic_key in gap.topics:
                topic = TOPIC_BY_KEY[topic_key]
                if depth >= len(topic.templates):
                    continue
                labels = (
                    [rf.label_of(f) for f in gap.fields[:1]]
                    if gap.kind == KIND_CONTRADICTION
                    else [""]
                )
                for label in labels:
                    text = _fill(topic.templates[depth], _slots(facts, today, label))
                    key = f"gap.{topic.key}.{depth}"
                    request, refusal = _gap_request(
                        text, topic, key, today=today, private_tokens=private_tokens
                    )
                    if request is None:
                        built.refused.append((key, refusal or "refused"))
                        continue
                    add(
                        english,
                        PlannedQuery(
                            request, QueryFamily.GAP, key, 3, depth * 10 + index, topic.terms
                        ),
                    )
    locale = locale_for_venue(facts.venue)
    if locale and max_locale > 0:
        seen_topics: set[str] = set()
        for gap in gaps:
            for topic_key in gap.topics:
                topic = TOPIC_BY_KEY[topic_key]
                phrase = GAP_GLOSSARY.get(topic.concept, {}).get(locale[0])
                if not phrase or topic.key in seen_topics or gap.kind == KIND_CONTRADICTION:
                    continue
                seen_topics.add(topic.key)
                key = f"gap.{topic.key}.{locale[0]}"
                request, refusal = _gap_request(
                    f"{facts.legal_name} {phrase}",
                    topic,
                    key,
                    today=today,
                    private_tokens=private_tokens,
                    language=locale[0],
                    country=locale[1],
                )
                if request is None:
                    built.refused.append((key, refusal or "refused"))
                    continue
                add(
                    local,
                    PlannedQuery(
                        request, QueryFamily.GAP, key, 3, 50, topic.terms, locale=locale[0]
                    ),
                )
    first_pass = [q for q in english if q.priority < 10]
    rest = [q for q in english if q.priority >= 10]
    ordered = first_pass + local[: max(0, max_locale)] + rest
    built.trimmed = max(0, len(ordered) - max_queries)
    built.queries = ordered[: max(0, max_queries)]
    return built


@dataclass(frozen=True)
class RiskTemplate:
    key: str
    pattern: str
    #: Slots the pattern needs; a template whose slot is empty is skipped.
    needs: tuple[str, ...] = ()
    concept: str | None = None
    terms: tuple[str, ...] = ()
    #: A counter-thesis query about a technology, industry or commodity (one slot is
    #: always held for the first available one).
    counter: bool = False
    #: Litigation gets the longer window.
    window: int = _DAYS_12M


#: Neutral wording only — never loaded ("fraud", "scam", "collapse" are not here).
RISK_TEMPLATES: tuple[RiskTemplate, ...] = (
    RiskTemplate("delay", "{company} delay", terms=("delay", "delayed", "postponed")),
    RiskTemplate(
        "lawsuit", "{company} lawsuit", terms=("lawsuit", "litigation", "court"), window=_DAYS_18M
    ),
    RiskTemplate(
        "profit_warning",
        "{company} profit warning",
        terms=("profit", "warning", "guidance", "downgrade"),
    ),
    RiskTemplate(
        "customer_loss", "{company} customer loss", terms=("customer", "lost", "loss", "cancelled")
    ),
    RiskTemplate(
        "permit_problem",
        "{company} permit problem",
        terms=("permit", "permits", "problem", "denied", "appeal"),
    ),
    RiskTemplate(
        "project_cancellation",
        "{company} project cancellation",
        terms=("project", "cancellation", "cancelled", "suspended"),
    ),
    RiskTemplate(
        "financing_risk",
        "{company} financing risk",
        terms=("financing", "funding", "risk", "refinancing"),
    ),
    RiskTemplate(
        "cost_overrun", "{company} cost overrun", terms=("cost", "overrun", "budget", "inflation")
    ),
    RiskTemplate(
        "production_issue",
        "{company} production issue",
        terms=("production", "issue", "outage", "shortfall"),
    ),
    RiskTemplate(
        "technology_disadvantages",
        "{theme} disadvantages",
        needs=("theme",),
        terms=("disadvantages", "limitations", "drawbacks"),
        counter=True,
        window=_DAYS_3Y,
    ),
    RiskTemplate(
        "industry_oversupply",
        "{industry} oversupply",
        needs=("industry",),
        terms=("oversupply", "glut", "surplus"),
        counter=True,
    ),
    RiskTemplate(
        "commodity_substitute",
        "{theme} substitute",
        needs=("theme",),
        terms=("substitute", "substitution", "alternative"),
        counter=True,
        window=_DAYS_3Y,
    ),
)

_RISK_BY_KEY = {t.key: t for t in RISK_TEMPLATES}
#: Development-stage issuers meet their risks in their projects; those templates first.
_DEV_FIRST = (
    "permit_problem",
    "project_cancellation",
    "financing_risk",
    "cost_overrun",
    "production_issue",
)


def build_challenge_queries(
    facts: CompanyFacts,
    *,
    today: date,
    executed: Collection[str] = (),
    max_queries: int = CHALLENGE_QUERIES,
    private_tokens: Collection[str] = (),
) -> BuiltQueries:
    """The RISK queries the W5 plan did not already run, one slot kept for a
    counter-thesis query (technology disadvantages · industry oversupply · commodity
    substitute) whenever one exists. Neutral wording; the thesis does not matter."""
    built = BuiltQueries()
    seen = {_norm(q) for q in executed}
    slots = _slots(facts, today)
    ordered = list(RISK_TEMPLATES)
    if facts.development_stage:
        ordered.sort(key=lambda t: (0 if t.key in _DEV_FIRST else 1,))
    company_pool: list[PlannedQuery] = []
    counter_pool: list[PlannedQuery] = []
    for index, template in enumerate(ordered):
        if any(not slots.get(need) for need in template.needs):
            continue
        text = _fill(template.pattern, slots)
        clean = sanitise_query(text, private_tokens=private_tokens)
        key = f"risk.{template.key}"
        if not clean.ok:
            built.refused.append((key, clean.refusal or "refused"))
            continue
        marker = _norm(clean.text)
        if marker in seen:
            built.deduped += 1
            continue
        seen.add(marker)
        request = SearchRequest(
            query=clean.text,
            family=QueryFamily.RISK,
            max_results=RESULTS_PER_QUERY,
            date_from=today - timedelta(days=template.window),
            date_to=today,
            topic="general",
            origin="template",
            template_version=f"{FOLLOWUP_TEMPLATE_VERSION}:{key}"[:40],
        )
        query = PlannedQuery(request, QueryFamily.RISK, key, 3, index, template.terms)
        (counter_pool if template.counter else company_pool).append(query)
    room = max(0, max_queries)
    chosen: list[PlannedQuery] = []
    if counter_pool and room:
        chosen.append(counter_pool[0])
    for query in company_pool:
        if len(chosen) >= room:
            break
        chosen.append(query)
    for query in counter_pool[1:]:
        if len(chosen) >= room:
            break
        chosen.append(query)
    built.trimmed = max(0, len(company_pool) + len(counter_pool) - len(chosen))
    built.queries = sorted(chosen, key=lambda q: q.priority)
    return built


# --------------------------------------------------------------------------- #
# Rounds
# --------------------------------------------------------------------------- #


@dataclass
class DocOutcome:
    """One fetched candidate, as the round saw it. Secret-free: no URL, no text."""

    state: str
    new: bool = False
    relevant: bool = False
    version_id: uuid.UUID | None = None
    canonical_url: str | None = None
    source_class: str | None = None
    reason: str | None = None


@dataclass
class FollowupRound:
    """What one web round did. ``to_dict`` is what the run record keeps."""

    index: int
    kind: str = "gap"
    state: str = STATE_OK
    query_keys: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    executed: int = 0
    failed: int = 0
    not_issued: int = 0
    deduped: int = 0
    candidates: int = 0
    selected: int = 0
    fetched: int = 0
    ingested: int = 0
    reused: int = 0
    not_ingested: int = 0
    not_retrievable: int = 0
    #: Newly stored documents whose result relevance reached the threshold.
    new_relevant: int = 0
    gap_ids: list[str] = field(default_factory=list)
    #: Gaps whose own text names a research field: the ones "answered" is judged on.
    provable_gap_ids: list[str] = field(default_factory=list)
    #: Every targeted ABSENCE gap (a contradiction is resolved by the disagreement
    #: machinery, not by a finding): ``answered`` requires ALL of them closed.
    absence_gap_ids: list[str] = field(default_factory=list)
    question_keys: list[str] = field(default_factory=list)
    followup_queries: dict[str, list[str]] = field(default_factory=dict)
    version_ids: list[uuid.UUID] = field(default_factory=list)
    budget_stop: str | None = None
    bytes_downloaded: int = 0

    @property
    def saturated(self) -> bool:
        return self.new_relevant == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.index,
            "kind": self.kind,
            "state": self.state,
            "queries": list(self.queries),
            "query_keys": list(self.query_keys),
            "executed": self.executed,
            "failed": self.failed,
            "not_issued": self.not_issued,
            "deduped": self.deduped,
            "candidates": self.candidates,
            "selected": self.selected,
            "fetched": self.fetched,
            "ingested": self.ingested,
            "reused": self.reused,
            "not_ingested": self.not_ingested,
            "not_retrievable": self.not_retrievable,
            "new_relevant": self.new_relevant,
            "gaps_targeted": len(self.gap_ids),
            "budget_stop": self.budget_stop,
            "bytes_downloaded": self.bytes_downloaded,
        }


@dataclass
class CandidateFetchResult:
    """What the Investigator's deterministic candidate step stored."""

    fetch_calls: int = 0
    ingested: int = 0
    reused: int = 0
    skipped: int = 0
    canonical_urls: tuple[str, ...] = ()
    budget_stop: str | None = None


# --------------------------------------------------------------------------- #
# The red team's evidence and the claim-type rule for a challenge
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RiskEvidence:
    """One fetched RISK document span, as the Red Team sees it.

    ``text`` is untrusted third-party text (rendered for a prompt, capped); everything
    else is platform-derived: the stored class, the origin DISPLAY, the document's own
    date, and whether it counts as an independent origin.
    """

    evidence_id: str
    text: str
    source_class: str | None
    origin: str
    published_at: date | None
    independent: bool
    support: Any

    def to_prompt(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_class": self.source_class,
            "origin": self.origin,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "independent_origin": self.independent,
            "text": self.text,
        }


@dataclass(frozen=True)
class BasisVerdict:
    carries: bool
    reason: str
    corroboration: str | None = None
    label: str | None = None


REASON_PLATFORM_EVIDENCE = "platform_evidence"
REASON_CORROBORATED = "independently_corroborated"
REASON_ISSUER_ADMISSION = "issuer_admission"
REASON_SINGLE_RELIABLE = "single_source_reliable_class"
REASON_LOW_TRUST = "single_low_trust_source"
REASON_NO_BASIS = "no_resolvable_basis"


def assess_challenge_basis(items: Sequence[Any], issuer_key: Any = None) -> BasisVerdict:
    """Can the cited evidence carry a Red Team challenge? (spec §7.4, §13.3)

    * any non-web (platform) item: yes — a filing or typed fact is not a web claim;
    * two independent origins: yes (``independently_corroborated``);
    * the issuer's own statement of a risk: yes — an admission against interest (labelled);
    * ONE independent, non-weak-class source: yes, but labelled ``single source``;
    * anything else — one weak-class, unknown-origin, wire-hosted or aggregator page —
      **no**: a single low-trust source cannot carry a challenge.

    ``items`` are ``trust.SupportItem``. Nothing here reads page text.
    """
    from app.services.web_research import trust

    support = list(items)
    if not support:
        return BasisVerdict(False, REASON_NO_BASIS)
    if any(not getattr(i, "web", False) for i in support):
        return BasisVerdict(True, REASON_PLATFORM_EVIDENCE)
    state = trust.corroboration_for_items(support, issuer_key=issuer_key)
    if state == trust.INDEPENDENTLY_CORROBORATED:
        return BasisVerdict(True, REASON_CORROBORATED, state)
    if state == trust.ISSUER_ONLY:
        return BasisVerdict(True, REASON_ISSUER_ADMISSION, state, trust.LABEL_COMPANY_SAYS)
    if state == trust.SINGLE_SOURCE and any(
        trust.bears_independence(i, issuer_key)
        and not trust.is_verified_issuer(i.origin_key, issuer_key)
        and i.source_class not in trust.WEAK_CLASSES
        for i in support
    ):
        return BasisVerdict(True, REASON_SINGLE_RELIABLE, state, trust.LABEL_SINGLE_SOURCE)
    return BasisVerdict(False, REASON_LOW_TRUST, state)


# --------------------------------------------------------------------------- #
# The loop's port, and the implementation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunRemaining:
    searches: int
    fetches: int
    bytes: int


class WebFollowup:
    """Everything the web rung needs for ONE company run. Build with :meth:`create`.

    Holds no page text. State: the queries already run, the budgets of each round, the
    round records and the final ``stopped_by`` the Director loop reports.
    """

    def __init__(
        self,
        session: Any,
        company: Any,
        run_ctx: WebRunContext,
        *,
        cfg: Any,
        facts: CompanyFacts,
        provider: Any,
        issuer_domains: tuple[str, ...] = (),
        deps: StageDeps | None = None,
        search_budget: Any = None,
    ) -> None:
        self.session = session
        self.company = company
        self.run_ctx = run_ctx
        self.cfg = cfg
        self.facts = facts
        self.provider = provider
        self.issuer_domains = issuer_domains
        self.deps = deps or StageDeps()
        #: An optional ceiling (``ExternalSearchBudget``: ``remaining``, ``take()``,
        #: ``give_back()``) taken from before every query. The pipeline passes none: W5's
        #: stage plans up to the mode's whole ``max_web_searches``, so sharing it would leave
        #: the follow-up nothing (decision W7-D2). Its own bounds are the "followup" profile
        #: per round, the per-mode round cap and the operator/daily caps.
        self.search_budget = search_budget
        self.mode = run_ctx.mode
        self.max_rounds = WEB_ROUNDS_BY_MODE.get(run_ctx.mode, 2)
        self.rounds: list[FollowupRound] = []
        self.units = ConsumptionUnits()
        self.stopped_by: str | None = None
        self.handled: set[str] = set()
        self._executed: set[str] | None = None
        self._budgets: dict[Any, WebResearchBudget] = {}
        self._profiles: dict[Any, Any] = {}
        self._challenge: FollowupRound | None = None
        self._fetch_calls_by_question: dict[tuple[Any, str], int] = {}
        self._counted: dict[Any, tuple[int, int]] = {}
        #: This instance's own fetch/byte totals (the fallback when no job id scopes the
        #: ``web_fetch_attempts`` rows).
        self._spent_fetches = 0
        self._spent_bytes = 0
        self._started = datetime.now(timezone.utc)

    # -- construction ------------------------------------------------------- #

    @classmethod
    def create(
        cls,
        session: Any,
        company: Any,
        run_ctx: WebRunContext,
        *,
        cfg: Any,
        deps: StageDeps | None = None,
        search_budget: Any = None,
    ) -> "WebFollowup | None":
        """``None`` unless every precondition holds: the W7 flag, the company web stage
        flag, web search enabled with a provider, and fetching enabled. A follow-up that
        cannot search or fetch would only pretend to research."""
        if not getattr(cfg, "v3_web_followup_enabled", False):
            return None
        if not getattr(cfg, "v3_company_web_research_enabled", False):
            return None
        if not getattr(cfg, "v3_web_search_enabled", False):
            return None
        if not getattr(cfg, "v3_web_fetch_enabled", False):
            return None
        deps = deps or StageDeps()
        if deps.provider is _UNSET:
            from app.integrations.search import web_search_provider_from_settings

            provider = web_search_provider_from_settings(cfg)
        else:
            provider = deps.provider
        if provider is None:
            return None
        verified = _verified_issuer(company)
        domains = _issuer_domains(verified)
        facts = facts_from_company(
            company,
            industry=run_ctx.industry,
            themes=run_ctx.themes,
            development_stage=run_ctx.development_stage,
            official_domains=domains,
        )
        return cls(
            session,
            company,
            run_ctx,
            cfg=cfg,
            facts=facts,
            provider=provider,
            issuer_domains=domains,
            deps=deps,
            search_budget=search_budget,
        )

    # -- small helpers ------------------------------------------------------ #

    @property
    def today(self) -> date:
        return self.deps.today or (self.deps.now or datetime.now(timezone.utc)).date()

    @property
    def now(self) -> datetime:
        return self.deps.now or datetime.now(timezone.utc)

    @property
    def company_id(self) -> Any:
        return getattr(self.company, "id", None)

    async def _budget(self, key: Any, *, wall_seconds: float | None = None) -> WebResearchBudget:
        """The round's ``followup`` budget, CLAMPED to what the run may still spend.

        The clamp is re-applied on every use: the run-level ceiling (``_run_remaining``)
        counts everything this job's searches and fetches already used — the W5 stage's,
        earlier rounds', the Investigator's — so no round, candidate step or challenge wave
        can add to a total the operator capped. ``wall_seconds`` (the Director's time left)
        narrows the round's own wall limit.
        """
        from dataclasses import replace

        budget = self._budgets.get(key)
        if budget is None:
            budget = await budget_for_run(
                self.session, BUDGET_PROFILE, cfg=self.cfg, now=self.now, clock=self.deps.clock
            )
            self._budgets[key] = budget
            self._profiles[key] = budget.limits
        profile = self._profiles[key]
        remaining = await self._run_remaining()
        wall = profile.max_wall_seconds
        if wall_seconds is not None:
            wall = min(wall, max(0.0, float(wall_seconds)) + budget.elapsed_seconds)
        budget.limits = replace(
            profile,
            max_queries=min(profile.max_queries, budget.queries_reserved + remaining.searches),
            max_fetches=min(profile.max_fetches, budget.fetches + remaining.fetches),
            max_bytes=min(profile.max_bytes, budget.bytes_downloaded + remaining.bytes),
            max_wall_seconds=wall,
        )
        return budget

    async def _run_remaining(self) -> "RunRemaining":
        """What the RUN may still spend on the web, searches / fetches / bytes.

        The ceiling is the mode's company-stage profile plus the follow-up allowance
        (web rounds x the profile + the challenge wave), and the operator's
        ``V3_RUN_MAX_WEB_SEARCHES`` caps the SUM when set. Usage is read from the
        provenance rows of this job (or, without a job id, this company's searches in the
        cache horizon and this instance's own fetches), so a fresh instance sees what an
        earlier one spent. Fail closed: a count that cannot be read leaves nothing.
        """
        from app.services.research_mode import web_profile_for
        from app.services.web_research.budget import PROFILES, limits_for

        stage = limits_for(web_profile_for(self.mode), self.cfg)
        profile = PROFILES[BUDGET_PROFILE]
        searches_cap = stage.max_queries + self.max_rounds * profile.max_queries + CHALLENGE_QUERIES
        fetches_cap = stage.max_fetches + (self.max_rounds + 1) * profile.max_fetches
        bytes_cap = stage.max_bytes + (self.max_rounds + 1) * profile.max_bytes
        operator = int(getattr(self.cfg, "v3_run_max_web_searches", 0) or 0)
        if operator > 0:
            searches_cap = min(searches_cap, operator)
        try:
            searches, fetches, nbytes = await self._run_usage()
        except Exception:  # noqa: BLE001 - doubt means nothing left
            return RunRemaining(0, 0, 0)
        return RunRemaining(
            max(0, searches_cap - searches),
            max(0, fetches_cap - fetches),
            max(0, bytes_cap - nbytes),
        )

    async def _run_usage(self) -> tuple[int, int, int]:
        from app.models.web_research import WebFetchAttempt as F
        from app.models.web_research import WebSearchQuery as Q

        job = self.run_ctx.research_job_id
        stmt = sa.select(sa.func.coalesce(sa.func.sum(Q.network_call_count), 0))
        if job is not None:
            stmt = stmt.where(Q.research_job_id == job)
        else:
            stmt = stmt.where(
                Q.company_id == self.company_id, Q.created_at >= self.now - timedelta(days=1)
            )
        searches = int((await self.session.scalar(stmt)) or 0)
        if job is None:
            return searches, self._spent_fetches, self._spent_bytes
        row = (
            await self.session.execute(
                sa.select(sa.func.count(), sa.func.coalesce(sa.func.sum(F.bytes), 0)).where(
                    F.research_job_id == job, F.status.in_(("fetched", "fetched_partial"))
                )
            )
        ).one()
        return searches, int(row[0] or 0), int(row[1] or 0)

    async def executed_queries(self) -> set[str]:
        """Queries already EXECUTED for this company in the cache horizon (the stage's,
        earlier rounds', the Investigator's) — normalised. Loaded once, then kept."""
        if self._executed is not None:
            return self._executed
        from app.models.web_research import WebSearchQuery as Q

        stmt = sa.select(Q.query_text).where(Q.executed.is_(True))
        if self.run_ctx.research_job_id is not None:
            stmt = stmt.where(Q.research_job_id == self.run_ctx.research_job_id)
        else:
            stmt = stmt.where(
                Q.company_id == self.company_id,
                Q.created_at >= self.now - timedelta(days=1),
            )
        try:
            rows = (await self.session.execute(stmt.limit(500))).scalars().all()
        except Exception:  # noqa: BLE001 - dedupe is best effort; the cache still bounds spend
            rows = []
        self._executed = {_norm(str(r)) for r in rows if r}
        return self._executed

    # -- the loop's port ---------------------------------------------------- #

    def select_gaps(
        self,
        gaps: Sequence[Any],
        *,
        question_texts: Mapping[str, str] | None = None,
        blocking_keys: Collection[str] = (),
    ) -> tuple[list[FollowupGap], list[FollowupGap]]:
        return select_gaps(
            gaps,
            handled=self.handled,
            question_texts=question_texts,
            blocking_keys=blocking_keys,
        )

    def mark_handled(self, gap_ids: Sequence[str]) -> None:
        self.handled.update(str(g) for g in gap_ids)

    @property
    def gap_rounds(self) -> list[FollowupRound]:
        return [r for r in self.rounds if r.kind == "gap"]

    def rounds_exhausted(self) -> bool:
        return len(self.gap_rounds) >= self.max_rounds

    async def budget_refusal(self) -> str | None:
        """Which web limit would stop the next round before it starts, or ``None``."""
        if self.search_budget is not None and int(self.search_budget.remaining) <= 0:
            return "run_search_budget_exhausted"
        budget = await self._budget(("gap", len(self.gap_rounds)))
        return budget.query_refusal()

    async def run_round(
        self,
        gaps: Sequence[FollowupGap],
        *,
        round_index: int,
        wall_seconds: float | None = None,
    ) -> FollowupRound:
        """One web round for ``gaps``. **Never raises** (cancellation excepted).

        ``wall_seconds`` is the Director's wall time left: the round's own limit is
        clamped to it, so a web round cannot run past the run's wall."""
        record = FollowupRound(round_index, "gap")
        record.gap_ids = [g.gap_id for g in gaps]
        record.provable_gap_ids = [g.gap_id for g in gaps if g.provable]
        record.absence_gap_ids = [g.gap_id for g in gaps if g.kind == KIND_ABSENCE]
        record.question_keys = sorted({g.question_key for g in gaps if g.question_key})
        try:
            async with self.session.begin_nested():
                await self._run_round(gaps, record, wall_seconds)
        except Exception as exc:  # noqa: BLE001 - the web rung never fails the run
            record.state = STATE_FAILED
            log_event(
                logger,
                "web_followup_round_failed",
                level=logging.WARNING,
                error_type=type(exc).__name__,
            )
        self.mark_handled(record.gap_ids)
        self.rounds.append(record)
        log_event(
            logger,
            "web_followup_round",
            state=record.state,
            kind="gap",
            executed=record.executed,
            fetched=record.fetched,
            ingested=record.ingested,
            new_relevant=record.new_relevant,
            company_id=self.company_id,
        )
        return record

    async def _run_round(
        self, gaps: Sequence[FollowupGap], record: FollowupRound, wall_seconds: float | None
    ) -> None:
        key = ("gap", len(self.gap_rounds))
        budget = await self._budget(key, wall_seconds=wall_seconds)
        built = build_gap_queries(
            gaps,
            self.facts,
            today=self.today,
            executed=await self.executed_queries(),
            max_queries=budget.limits.max_queries,
            private_tokens=self.run_ctx.private_tokens,
        )
        record.deduped = built.deduped
        if not built.queries:
            record.state = STATE_NO_NEW_QUERIES
            return
        await self._search_fetch_ingest(
            built.queries, budget, record, family=QueryFamily.GAP, key=key
        )
        by_question: dict[str, list[str]] = {}
        for gap in gaps:
            if not gap.question_key:
                continue
            for query in built.queries:
                if any(query.key.startswith(f"gap.{t}.") for t in gap.topics):
                    by_question.setdefault(gap.question_key, [])
                    if query.request.query not in by_question[gap.question_key]:
                        by_question[gap.question_key].append(query.request.query)
        record.followup_queries = {k: v[:2] for k, v in by_question.items()}

    async def challenge_wave(self, *, wall_seconds: float | None = None) -> FollowupRound:
        """The Red Team's search (spec §7.4): RISK queries the W5 plan did not run.

        Always runs once when the follow-up is on, whatever the thesis says. **Never
        raises.** The result is recorded like a gap round (kind ``challenge``).
        """
        if self._challenge is not None:
            return self._challenge
        record = FollowupRound(len(self.rounds), "challenge")
        if wall_seconds is not None and wall_seconds <= 0:
            # The Director's wall time is spent: the wave is not started, and says so.
            record.state = STATE_WALL
            record.budget_stop = "max_wall_seconds"
            self._challenge = record
            self.rounds.append(record)
            return record
        try:
            async with self.session.begin_nested():
                budget = await self._budget("challenge", wall_seconds=wall_seconds)
                built = build_challenge_queries(
                    self.facts,
                    today=self.today,
                    executed=await self.executed_queries(),
                    max_queries=min(CHALLENGE_QUERIES, budget.limits.max_queries),
                    private_tokens=self.run_ctx.private_tokens,
                )
                record.deduped = built.deduped
                if not built.queries:
                    record.state = STATE_NO_NEW_QUERIES
                else:
                    await self._search_fetch_ingest(
                        built.queries,
                        budget,
                        record,
                        family=QueryFamily.RISK,
                        key="challenge",
                    )
        except Exception as exc:  # noqa: BLE001 - the challenge search never fails the run
            record.state = STATE_FAILED
            log_event(
                logger,
                "web_followup_challenge_failed",
                level=logging.WARNING,
                error_type=type(exc).__name__,
            )
        self._challenge = record
        self.rounds.append(record)
        return record

    # -- search → select → fetch → ingest ----------------------------------- #

    async def _search_fetch_ingest(
        self,
        queries: list[PlannedQuery],
        budget: WebResearchBudget,
        record: FollowupRound,
        *,
        family: QueryFamily,
        key: Any = None,
    ) -> None:
        # An optional ceiling (see ``__init__``), taken from before each query.
        allowed: list[PlannedQuery] = []
        for query in queries:
            if self.search_budget is not None and not self.search_budget.take():
                record.budget_stop = "run_search_budget_exhausted"
                break
            allowed.append(query)
        if not allowed:
            record.state = STATE_BUDGET
            return
        persist = (
            persist_factory_for(self.session)
            if self.deps.persist_session_factory is _UNSET
            else self.deps.persist_session_factory
        )
        run = await run_searches(
            self.session,
            [q.request for q in allowed],
            SearchContext(
                research_job_id=self.run_ctx.research_job_id,
                agent_run_id=self.run_ctx.agent_run_id,
                company_id=self.company_id,
                stage="followup",
                private_tokens=frozenset(self.run_ctx.private_tokens),
                budget=budget,
                budget_profile=BUDGET_PROFILE,
            ),
            provider=self.provider,
            cfg=self.cfg,
            now=self.now,
            persist_session_factory=persist,
        )
        self.units = self.units + run.consumption
        self._count_queries(run, allowed, record)
        if run.state == STATE_DISABLED:
            record.state = STATE_UNAVAILABLE_WEB
            return
        if run.state == STATE_UNAVAILABLE:
            record.state = STATE_UNAVAILABLE_WEB
            return
        outcomes = [o for o in run.outcomes if o.execution.executed]
        rows = await _result_rows(self.session, [o.query_id for o in outcomes])
        by_request = {id(q.request): q for q in allowed}
        candidates: list[SearchCandidate] = []
        for outcome in outcomes:
            planned = by_request.get(id(outcome.request))
            for item in outcome.results:
                row = rows.get((outcome.query_id, item.rank))
                candidates.append(
                    SearchCandidate(
                        family=family,
                        item=item,
                        query_key=planned.key if planned else "",
                        query_id=outcome.query_id,
                        result_id=getattr(row, "id", None),
                    )
                )
        record.candidates = len(candidates)
        rows_pre = {r.id: r for r in rows.values()}
        unsafe = [c for c in candidates if not candidate_url_allowed(c.item.url)]
        for candidate in unsafe:
            _disposition(rows_pre.get(candidate.result_id), DISPOSITION_SKIPPED, "unsafe_url")
        candidates = [c for c in candidates if c not in unsafe]
        terms = tuple(dict.fromkeys(t for q in allowed for t in q.terms))
        selection = await self._select(candidates, family, terms, budget.limits.max_fetches)
        record.selected = len(selection.selected)
        rows_by_id = {r.id: r for r in rows.values()}
        for scored in selection.selected:
            _disposition(rows_by_id.get(scored.candidate.result_id), DISPOSITION_SELECTED, None)
        for candidate, reason in selection.skipped:
            _disposition(rows_by_id.get(candidate.result_id), DISPOSITION_SKIPPED, reason)
        await self.session.flush()
        outcomes_docs = await self._fetch_and_ingest(
            selection.selected, rows_by_id, budget, family, terms
        )
        self._tally(record, outcomes_docs)
        record.bytes_downloaded = budget.bytes_downloaded
        self._account_fetches(key, budget)

    async def _select(
        self,
        candidates: Sequence[SearchCandidate],
        family: QueryFamily,
        terms: tuple[str, ...],
        total: int,
    ) -> Any:
        held = await _held_urls(self.session, self.company_id)
        return select_results(
            list(candidates),
            SelectionContext(
                today=self.today,
                issuer_domains=self.issuer_domains,
                identity_terms=_identity_terms(self.facts),
                held_urls=held,
                family_terms={family: terms},
                mode=self.mode,
                quotas={family: max(0, total)},
                total=max(0, total),
            ),
        )

    def _count_queries(
        self, run: SearchRunResult, allowed: list[PlannedQuery], record: FollowupRound
    ) -> None:
        executed = self._executed if self._executed is not None else set()
        for outcome in run.outcomes:
            planned = next((q for q in allowed if q.request is outcome.request), None)
            record.query_keys.append(planned.key if planned else "")
            record.queries.append(outcome.request.query)
            ex = outcome.execution
            if ex.executed:
                record.executed += 1
                executed.add(_norm(outcome.request.query))
            elif ex.network_call_count:
                record.failed += 1
            else:
                # Refused before any provider call: it spent nothing, so the shared
                # ceiling gets the slot back.
                record.not_issued += 1
                if self.search_budget is not None:
                    self.search_budget.give_back()
        self._executed = executed

    async def _fetch_and_ingest(
        self,
        selected: Sequence[Scored],
        rows_by_id: Mapping[Any, Any],
        budget: WebResearchBudget,
        family: QueryFamily,
        terms: tuple[str, ...],
    ) -> list[DocOutcome]:
        from app.services.web_research import fetch as fetch_mod
        from app.services.web_research import ingest as ingest_mod

        out: list[DocOutcome] = []
        if not bool(getattr(self.cfg, "v3_web_fetch_enabled", False)):
            for scored in selected:
                _disposition(
                    rows_by_id.get(scored.candidate.result_id),
                    DISPOSITION_NOT_INGESTED,
                    fetch_mod.FAILURE_FETCH_DISABLED,
                )
                out.append(
                    DocOutcome(DISPOSITION_NOT_INGESTED, reason=fetch_mod.FAILURE_FETCH_DISABLED)
                )
            return out
        fetch_fn = self.deps.fetch or fetch_mod.open_web_fetch
        context = fetch_mod.WebFetchContext(research_job_id=self.run_ctx.research_job_id)
        entity = _candidate_entity(self.company, self.facts, self.issuer_domains)
        depth = "deep" if self.mode in ("deep", "max") else "standard"
        stopped = False
        for scored in selected:
            row = rows_by_id.get(scored.candidate.result_id)
            refusal = budget.fetch_refusal()
            if refusal is not None or stopped:
                _disposition(row, DISPOSITION_SKIPPED, refusal or "stopped")
                stopped = True
                out.append(DocOutcome(DISPOSITION_SKIPPED, reason=refusal))
                continue
            item = scored.candidate.item
            try:
                fetched = await fetch_fn(
                    self.session,
                    item.url,
                    context=context,
                    budget=budget,
                    origin=fetch_mod.ORIGIN_SEARCH,
                    search_result_id=scored.candidate.result_id,
                    **dict(self.deps.fetch_kwargs),
                )
            except Exception as exc:  # noqa: BLE001 - one bad URL costs that URL
                _disposition(row, DISPOSITION_FETCH_FAILED, type(exc).__name__[:60])
                out.append(DocOutcome(DISPOSITION_FETCH_FAILED, reason=type(exc).__name__))
                continue
            if fetched.status == fetch_mod.STATUS_NOT_RETRIEVABLE:
                reason = fetched.failure_code or "not_retrievable"
                _disposition(row, DISPOSITION_NOT_RETRIEVABLE, reason)
                out.append(DocOutcome(DISPOSITION_NOT_RETRIEVABLE, reason=reason))
                continue
            if not fetched.ok:
                reason = fetched.failure_code or fetched.status
                _disposition(row, DISPOSITION_FETCH_FAILED, reason)
                out.append(DocOutcome(DISPOSITION_FETCH_FAILED, reason=reason))
                continue
            try:
                prepared = await ingest_mod.prepare_web_document(
                    fetched,
                    cfg=self.cfg,
                    provider=None,
                    company_id=self.company_id,
                    candidates=(entity,),
                    issuer_domains=self.issuer_domains,
                    query_terms=terms,
                    depth=depth,
                    pool=self.deps.pool,
                    store=self.deps.store,
                    now=self.deps.now,
                )
                if isinstance(prepared, ingest_mod.WebIngestResult):
                    result = prepared
                else:
                    async with self.session.begin_nested():
                        result = await ingest_mod.store_web_document(
                            self.session,
                            prepared,
                            cfg=self.cfg,
                            store=self.deps.store,
                            backend=self.run_ctx.search_backend,
                            now=self.deps.now,
                        )
            except Exception as exc:  # noqa: BLE001 - a document that fails to store costs itself
                _disposition(row, DISPOSITION_NOT_INGESTED, type(exc).__name__[:60])
                out.append(DocOutcome(DISPOSITION_NOT_INGESTED, reason=type(exc).__name__))
                continue
            if getattr(result, "stored", False):
                new = result.state == ingest_mod.STATE_INGESTED
                _disposition(row, DISPOSITION_INGESTED if new else DISPOSITION_REUSED, None)
                relevant = new and (
                    scored.components.get("relevance", 0.0) / (W_RELEVANCE or 1.0)
                    >= SATURATION_RELEVANCE_THRESHOLD
                )
                out.append(
                    DocOutcome(
                        DISPOSITION_INGESTED if new else DISPOSITION_REUSED,
                        new=new,
                        relevant=relevant,
                        version_id=result.version_id,
                        canonical_url=scored.candidate.url,
                        source_class=result.source_class,
                    )
                )
            else:
                reason = getattr(result, "reason", None) or "not_ingested"
                _disposition(row, DISPOSITION_NOT_INGESTED, reason)
                out.append(DocOutcome(DISPOSITION_NOT_INGESTED, reason=reason))
        await self.session.flush()
        return out

    def _tally(self, record: FollowupRound, docs: Sequence[DocOutcome]) -> None:
        for doc in docs:
            if doc.state == DISPOSITION_INGESTED:
                record.ingested += 1
                record.fetched += 1
            elif doc.state == DISPOSITION_REUSED:
                record.reused += 1
                record.fetched += 1
            elif doc.state == DISPOSITION_NOT_INGESTED:
                record.not_ingested += 1
                record.fetched += 1
            elif doc.state == DISPOSITION_NOT_RETRIEVABLE:
                record.not_retrievable += 1
            if doc.relevant:
                record.new_relevant += 1
            if doc.version_id is not None and doc.new:
                record.version_ids.append(doc.version_id)

    def _account_fetches(self, key: Any, budget: WebResearchBudget) -> None:
        """Add the fetches/bytes a budget has spent SINCE it was last counted to the units."""
        done_fetches, done_bytes = self._counted.get(key, (0, 0))
        self.units = self.units + ConsumptionUnits(
            url_fetch_calls=max(0, budget.fetches - done_fetches),
            bytes_downloaded=max(0, budget.bytes_downloaded - done_bytes),
            instrumented=frozenset({"url_fetch_calls", "bytes_downloaded"}),
        )
        self._spent_fetches += max(0, budget.fetches - done_fetches)
        self._spent_bytes += max(0, budget.bytes_downloaded - done_bytes)
        self._counted[key] = (budget.fetches, budget.bytes_downloaded)

    # -- the Investigator's deterministic candidate step -------------------- #

    async def fetch_candidates(
        self,
        *,
        question_key: str,
        candidates: Sequence[Mapping[str, Any]],
        round_index: int = 0,
        max_fetches: int = MAX_CANDIDATE_FETCHES_PER_QUESTION,
        terms: Sequence[str] = (),
    ) -> CandidateFetchResult:
        """Fetch and ingest the best of a ``search_web`` call's candidates. **Never raises.**

        The PLATFORM chooses what is fetched: https only, not denylisted, not already
        held, ranked by the W5 selector from the URL's host class, its rank and the
        provider's own date hint — a candidate's title is compared to platform-owned
        terms and discarded. At most ``MAX_CANDIDATE_FETCHES_PER_QUESTION`` per question
        and the round's ``followup`` fetch budget. A document is stored through the one
        web write path (so its chunks are ``ev:c:`` evidence); nothing is minted here.
        """
        result = CandidateFetchResult()
        cap = max(0, min(int(max_fetches), MAX_CANDIDATE_FETCHES_PER_QUESTION))
        done = self._fetch_calls_by_question.get((round_index, question_key), 0)
        cap = max(0, cap - done)
        if cap == 0 or not candidates:
            return result
        try:
            async with self.session.begin_nested():
                budget = await self._budget(("candidates", round_index))
                items = [_item_from_candidate(c, i) for i, c in enumerate(candidates)]
                search_candidates = [
                    SearchCandidate(family=QueryFamily.GAP, item=item, query_key="investigator")
                    for item in items
                    if item is not None and candidate_url_allowed(item.url)
                ]
                selection = await self._select(
                    search_candidates, QueryFamily.GAP, tuple(terms), cap
                )
                result.skipped = len(selection.skipped)
                docs = await self._fetch_and_ingest(
                    selection.selected, {}, budget, QueryFamily.GAP, tuple(terms)
                )
        except Exception as exc:  # noqa: BLE001 - the step costs itself, never the run
            log_event(
                logger,
                "web_followup_candidates_failed",
                level=logging.WARNING,
                error_type=type(exc).__name__,
            )
            return result
        result.fetch_calls = sum(
            1
            for d in docs
            if d.state
            in (
                DISPOSITION_INGESTED,
                DISPOSITION_REUSED,
                DISPOSITION_NOT_INGESTED,
                DISPOSITION_FETCH_FAILED,
                DISPOSITION_NOT_RETRIEVABLE,
            )
        )
        result.ingested = sum(1 for d in docs if d.state == DISPOSITION_INGESTED)
        result.reused = sum(1 for d in docs if d.state == DISPOSITION_REUSED)
        from app.services.web_research.canonical import canonical_url

        stored_urls: list[str] = []
        for d in docs:
            if d.canonical_url and d.state in (DISPOSITION_INGESTED, DISPOSITION_REUSED):
                stored_urls.append(canonical_url(d.canonical_url) or d.canonical_url)
        result.canonical_urls = tuple(dict.fromkeys(stored_urls))
        self._account_fetches(("candidates", round_index), budget)
        refusal = budget.fetch_refusal()
        result.budget_stop = refusal
        self._fetch_calls_by_question[(round_index, question_key)] = done + result.fetch_calls
        return result

    # -- answered? ----------------------------------------------------------- #

    async def answered(self, run: Any, gap_ids: Sequence[str]) -> set[str]:
        """Of ``gap_ids``, those the run's findings NOW close, by track B's own rules.

        A pure read: it runs ``gap_reconciliation.reconcile`` over the run's findings and
        changes nothing, so the final reconciliation (which persists) is the one record.
        """
        return await gaps_answered(self.session, run, gap_ids, as_of=self.today)

    # -- the Red Team's evidence --------------------------------------------- #

    async def risk_evidence(self) -> list[RiskEvidence]:
        """RISK documents this company's searches stored (stage wave 3 + the challenge
        wave), as labelled evidence spans. **Never raises**; ``[]`` on any failure."""
        try:
            async with self.session.begin_nested():
                return await collect_risk_evidence(
                    self.session,
                    company_id=self.company_id,
                    job_id=self.run_ctx.research_job_id,
                    since=self.now - timedelta(days=1),
                )
        except Exception as exc:  # noqa: BLE001 - the Red Team just gets less
            log_event(
                logger,
                "web_followup_risk_evidence_failed",
                level=logging.WARNING,
                error_type=type(exc).__name__,
            )
            return []

    # -- the record ----------------------------------------------------------- #

    def to_dict(self) -> dict[str, Any]:
        gap_rounds = self.gap_rounds
        queries = [q for r in self.rounds for q in r.queries]
        return {
            "version": SUMMARY_VERSION,
            "template_version": FOLLOWUP_TEMPLATE_VERSION,
            "glossary_version": FOLLOWUP_GLOSSARY_VERSION,
            "profile": BUDGET_PROFILE,
            "max_web_rounds": self.max_rounds,
            "rounds": [r.to_dict() for r in self.rounds],
            "followup_rounds": len(gap_rounds),
            "queries": queries,
            "stopped_by": self.stopped_by,
            "gaps_handled": len(self.handled),
            "challenge": self._challenge.to_dict() if self._challenge else None,
        }


# --------------------------------------------------------------------------- #
# Module-level helpers (also used by tests)
# --------------------------------------------------------------------------- #


def _disposition(row: Any, disposition: str, reason: str | None) -> None:
    if row is None:
        return
    row.disposition = disposition[:30]
    row.disposition_reason = (reason or "")[:120] or None


def combine_units(stage_units: Any, followup_units: Any) -> Any:
    """The run's web units: the stage's plus the follow-up's, whichever exist.

    Both are ``ConsumptionUnits``; their ``unreported`` sets union (a paid search whose
    credit figure the vendor did not return keeps the run's cost NULL, never zero). When
    the stage produced none (it died before writing units) the follow-up's are the whole
    web spend and are returned as they are.
    """
    if stage_units is None:
        return followup_units
    return stage_units + followup_units


def candidate_url_allowed(url: str | None) -> bool:
    """A cheap, network-free pre-filter for a search-returned URL (W0 shape + host rules).

    https only, port 443, no userinfo, parsers agree, and a public-looking host: no IP
    literal in any encoding, no internal suffix, no single-label host. It is NOT the SSRF
    boundary — ``open_web_fetch`` re-checks every hop and resolves DNS — it keeps an
    obviously hostile result from ever reaching selection or consuming a fetch.
    """
    from app.services.sources.safe_web_fetcher import check_url_shape, is_safe_public_host

    reason, host = check_url_shape(url)
    return reason is None and is_safe_public_host(host)


def _item_from_candidate(raw: Mapping[str, Any], index: int) -> SearchResultItem | None:
    """A ``search_web`` candidate dict as a ``SearchResultItem`` for the W5 selector."""
    url = str(raw.get("url") or "").strip()
    if not url:
        return None
    hint = raw.get("published_hint")
    published: datetime | None = None
    if isinstance(hint, str) and hint:
        try:
            published = datetime.fromisoformat(hint)
        except ValueError:
            published = None
    try:
        rank = int(raw.get("rank") or index + 1)
    except (TypeError, ValueError):
        rank = index + 1
    return SearchResultItem(
        rank=rank,
        url=url,
        canonical_url=url,
        domain=str(raw.get("domain") or domain_of(url) or host_of(url)),
        title=str(raw.get("title") or "")[:200] or None,
        published_hint=published,
    )


async def gaps_answered(
    session: Any, run: Any, gap_ids: Sequence[str], *, as_of: date | None = None
) -> set[str]:
    """Gap ids (of ``gap_ids``) that the run's findings close, per track B. Read-only."""
    ids = [str(g) for g in gap_ids if g]
    if not ids:
        return set()
    from app.models.ledger import ResearchFinding, ResearchGap
    from app.services.ledger import store as ledger
    from app.services.pipeline import gap_reconciliation as gr

    findings = (
        (
            await session.execute(
                sa.select(ResearchFinding).where(ResearchFinding.research_run_id == run.id)
            )
        )
        .scalars()
        .all()
    )
    gap_rows = (
        (
            await session.execute(
                sa.select(ResearchGap).where(
                    ResearchGap.research_run_id == run.id,
                    ResearchGap.id.in_([uuid.UUID(i) for i in ids]),
                )
            )
        )
        .scalars()
        .all()
    )
    verdicts = gr.reconcile(
        [
            gr.GapFacts(
                gap_id=str(g.id),
                gap_type=g.gap_type,
                description=g.description,
                question_key=g.question_key,
                status=g.status,
                closed_by_finding_id=(
                    str(g.closed_by_finding_id) if g.closed_by_finding_id else None
                ),
            )
            for g in gap_rows
        ],
        [gr.FindingFacts.from_row(f) for f in findings],
        as_of=as_of or date.today(),
    )
    return {v.gap_id for v in verdicts if v.status == ledger.RECONCILED_CLOSED}


async def collect_risk_evidence(
    session: Any,
    *,
    company_id: Any,
    job_id: Any = None,
    since: datetime | None = None,
    limit: int = MAX_RISK_EVIDENCE_ITEMS,
) -> list[RiskEvidence]:
    """RISK-family documents stored for ``company_id``, as labelled evidence spans.

    Which documents: the ``risk`` search rows (stage wave 3 and the challenge wave) whose
    results ended ``ingested`` / ``reused``, matched to the company's stored versions by
    canonical URL. Which span: the chunk of each version that shares the most RISK terms
    (else its first), rendered for a prompt and capped. The class, origin and date are
    read from what the platform STORED (``trust.resolve_support``), never from the page.
    """
    from app.models.research_chunk import ResearchDocumentChunk as C
    from app.models.research_document import ResearchDocumentVersion as V
    from app.models.web_research import WebSearchQuery as Q
    from app.models.web_research import WebSearchResult as R
    from app.services.corpus.retrieval import evidence_id_for
    from app.services.web_research import trust
    from app.services.web_research.selection import fold_tokens
    from app.services.web_research.text_safety import render_for_prompt

    stmt = (
        sa.select(R.canonical_url, R.url, Q.query_text)
        .join(Q, Q.id == R.query_id)
        .where(
            Q.family == QueryFamily.RISK.value,
            Q.executed.is_(True),
            R.disposition.in_((DISPOSITION_INGESTED, DISPOSITION_REUSED)),
        )
    )
    if job_id is not None:
        stmt = stmt.where(Q.research_job_id == job_id)
    else:
        stmt = stmt.where(Q.company_id == company_id)
        if since is not None:
            stmt = stmt.where(Q.created_at >= since)
    rows = (await session.execute(stmt.limit(200))).all()
    urls = list(dict.fromkeys(str(r[0] or r[1]) for r in rows if (r[0] or r[1])))
    if not urls:
        return []
    terms = {t for template in RISK_TEMPLATES for term in template.terms for t in fold_tokens(term)}
    from app.models.research_document import ResearchDocument as D
    from app.models.research_document import ResearchDocumentSubject as Subject

    # Scoped to THIS company (its own document, or one that names it as a subject), and
    # never a page flagged as an injection attempt: such text must not reach the Red Team
    # prompt, and a flagged page does not count as an independent origin.
    versions = (
        await session.execute(
            sa.select(V.id, V.canonical_url)
            .join(D, D.id == V.research_document_id)
            .where(
                V.canonical_url.in_(urls),
                V.injection_suspect.is_not(True),
                sa.or_(
                    D.company_id == company_id,
                    sa.exists().where(
                        Subject.research_document_id == D.id, Subject.company_id == company_id
                    ),
                ),
            )
        )
    ).all()
    out_chunks: list[tuple[str, str]] = []
    for version_id, _url in versions:
        chunks = (
            await session.execute(
                sa.select(C.chunk_id, C.text, C.ordinal)
                .where(C.research_document_version_id == version_id, C.indexable.is_(True))
                .order_by(C.ordinal)
                .limit(12)
            )
        ).all()
        if not chunks:
            continue
        scored = sorted(chunks, key=lambda c: (-len(terms & fold_tokens(c[1])), c[2]))[
            :MAX_CHUNKS_PER_RISK_DOCUMENT
        ]
        out_chunks.extend((evidence_id_for(c[0]), c[1]) for c in scored)
        if len(out_chunks) >= limit:
            break
    out_chunks = out_chunks[:limit]
    if not out_chunks:
        return []
    support = {
        s.evidence_id: s
        for s in await trust.resolve_support(
            session, [e for e, _t in out_chunks], company_id=company_id
        )
    }
    items: list[RiskEvidence] = []
    for evidence_id, text in out_chunks:
        item = support.get(evidence_id)
        if item is None:
            continue
        items.append(
            RiskEvidence(
                evidence_id=evidence_id,
                text=render_for_prompt(" ".join(text.split()))[:RISK_EXCERPT_CHARS],
                source_class=item.source_class,
                origin=trust.origin_display(item.origin_key),
                published_at=item.published_at,
                independent=trust.bears_independence(item, company_id),
                support=item,
            )
        )
    return items


__all__ = [
    "ABSENCE_GAP_TYPES",
    "BUDGET_PROFILE",
    "BasisVerdict",
    "BuiltQueries",
    "CHALLENGE_QUERIES",
    "CONTRADICTION_TOPIC",
    "CandidateFetchResult",
    "RunRemaining",
    "candidate_url_allowed",
    "combine_units",
    "DocOutcome",
    "FOLLOWUP_GAP_TYPES",
    "FOLLOWUP_GLOSSARY_VERSION",
    "FOLLOWUP_TEMPLATE_VERSION",
    "FollowupGap",
    "FollowupRound",
    "GAP_GLOSSARY",
    "GapTopic",
    "MAX_CANDIDATE_FETCHES_PER_QUESTION",
    "RISK_TEMPLATES",
    "RiskEvidence",
    "RiskTemplate",
    "SATURATION_RELEVANCE_THRESHOLD",
    "STOP_BUDGET",
    "TOPICS",
    "TOPIC_BY_KEY",
    "WEB_ROUNDS_BY_MODE",
    "WebFollowup",
    "assess_challenge_basis",
    "build_challenge_queries",
    "build_gap_queries",
    "classify_gap",
    "collect_risk_evidence",
    "gaps_answered",
    "select_gaps",
    "topics_for_text",
]
