"""Source class, document kind and injection taint for a web document — open-web W3.

SOURCE CLASS (spec §13.1) — DETERMINISTIC, NEVER AN LLM
=======================================================
Assigned in this order:

1. **Host rules** — the verified issuer domain; exchange hosts; the filing, regulator,
   government and specialist-agency hosts ``publisher_tiers`` already knows.
2. **Curated lists** (versioned data below) — trade press, associations, standards
   bodies, academic hosts, consultancies, aggregators, PR wires.
3. **Page signals** — JSON-LD ``@type``, ``citation_*`` meta, OpenGraph. A page speaks
   for itself, so page signals may set the document KIND but never raise a TIER: a
   content farm that prints ``citation_title`` does not become an academic source.
4. Fallback ``unknown_web`` (T5).

The class maps onto the EXISTING tier and access-class vocabularies; a web page is not
automatically T5 — a USGS PDF found by search is T3 because of its host.

INJECTION TAINT (threat model §3.3)
===================================
:func:`injection_assessment` is a heuristic SIGNAL: imperative-to-assistant phrases,
"ignore previous instructions" in several languages, role tags, fake evidence markers,
tool names, credential requests, internal addresses, base64 blobs near instructions,
invisible Unicode, and — weighted most — instructions inside HIDDEN text. A suspect
document is still stored (it is evidence of an attack) and is never deleted here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.services.corpus.policy import (
    ACCESS_PUBLIC_ISSUER,
    ACCESS_PUBLIC_OFFICIAL,
    ACCESS_PUBLIC_WEB,
)
from app.services.sources.document_discovery import (
    DOC_KIND_ACADEMIC_PAPER,
    DOC_KIND_ANNUAL_REPORT,
    DOC_KIND_CONSULTATION,
    DOC_KIND_FACTSHEET,
    DOC_KIND_GOVERNMENT_REPORT,
    DOC_KIND_INDUSTRY_REPORT,
    DOC_KIND_INTERIM_REPORT,
    DOC_KIND_NEWS_ARTICLE,
    DOC_KIND_OTHER,
    DOC_KIND_PRESENTATION,
    DOC_KIND_PRESS_RELEASE,
    DOC_KIND_RESULTS_RELEASE,
    DOC_KIND_WEB_PAGE,
    DOC_KIND_WHITEPAPER,
)
from app.services.sources.publisher_tiers import publisher_tier
from app.services.sources.taxonomy import (
    T1_PRIMARY_COMPANY_SOURCE,
    T1_PRIMARY_FILING,
    T2_REGULATOR_OR_GOV,
    T3_INDUSTRY_SPECIALIST,
    T4_QUALITY_MEDIA,
    T5_API_AGGREGATOR,
)
from app.services.web_research.source_policy import use_constraint_for
from app.services.web_research.text_safety import has_tag_characters, invisible_char_count

CURATED_LISTS_VERSION = "2026-10-07.1"

# ── Source classes (spec §13.1) ─────────────────────────────────────────── #

SC_ISSUER_FILING = "issuer_filing"
SC_REGULATORY_FILING = "regulatory_filing"
SC_EXCHANGE_ANNOUNCEMENT = "exchange_announcement"
SC_GOVERNMENT_PUBLICATION = "government_publication"
SC_REGULATOR_PUBLICATION = "regulator_publication"
SC_STATISTICAL_AGENCY = "statistical_agency"
SC_SPECIALIST_AGENCY = "specialist_agency"
SC_STANDARDS_BODY = "standards_body"
SC_ACADEMIC_PAPER = "academic_paper"
SC_INDUSTRY_ASSOCIATION = "industry_association"
SC_COMPANY_PRESS_RELEASE = "company_press_release"
SC_INVESTOR_PRESENTATION = "investor_presentation"
SC_COMPANY_WEB_PAGE = "company_web_page"
SC_MAJOR_FINANCIAL_PRESS = "major_financial_press"
SC_TRADE_PUBLICATION = "trade_publication"
SC_LOCAL_PRESS = "local_press"
SC_RESEARCH_CONSULTANCY = "research_consultancy"
SC_AGGREGATOR = "aggregator"
SC_UNKNOWN_WEB = "unknown_web"

#: class → (tier, access class). ``trade_publication`` / ``local_press`` /
#: ``research_consultancy`` are T4/T3 only when CURATED — see ``_curated_tier``.
CLASS_TABLE: dict[str, tuple[str, str]] = {
    SC_ISSUER_FILING: (T1_PRIMARY_FILING, ACCESS_PUBLIC_OFFICIAL),
    SC_REGULATORY_FILING: (T1_PRIMARY_FILING, ACCESS_PUBLIC_OFFICIAL),
    SC_EXCHANGE_ANNOUNCEMENT: (T1_PRIMARY_FILING, ACCESS_PUBLIC_OFFICIAL),
    SC_GOVERNMENT_PUBLICATION: (T2_REGULATOR_OR_GOV, ACCESS_PUBLIC_OFFICIAL),
    SC_REGULATOR_PUBLICATION: (T2_REGULATOR_OR_GOV, ACCESS_PUBLIC_OFFICIAL),
    SC_STATISTICAL_AGENCY: (T2_REGULATOR_OR_GOV, ACCESS_PUBLIC_OFFICIAL),
    SC_SPECIALIST_AGENCY: (T3_INDUSTRY_SPECIALIST, ACCESS_PUBLIC_OFFICIAL),
    SC_STANDARDS_BODY: (T3_INDUSTRY_SPECIALIST, ACCESS_PUBLIC_WEB),
    SC_ACADEMIC_PAPER: (T3_INDUSTRY_SPECIALIST, ACCESS_PUBLIC_WEB),
    SC_INDUSTRY_ASSOCIATION: (T3_INDUSTRY_SPECIALIST, ACCESS_PUBLIC_WEB),
    SC_COMPANY_PRESS_RELEASE: (T1_PRIMARY_COMPANY_SOURCE, ACCESS_PUBLIC_ISSUER),
    SC_INVESTOR_PRESENTATION: (T1_PRIMARY_COMPANY_SOURCE, ACCESS_PUBLIC_ISSUER),
    SC_COMPANY_WEB_PAGE: (T1_PRIMARY_COMPANY_SOURCE, ACCESS_PUBLIC_ISSUER),
    SC_MAJOR_FINANCIAL_PRESS: (T4_QUALITY_MEDIA, ACCESS_PUBLIC_WEB),
    SC_TRADE_PUBLICATION: (T4_QUALITY_MEDIA, ACCESS_PUBLIC_WEB),
    SC_LOCAL_PRESS: (T4_QUALITY_MEDIA, ACCESS_PUBLIC_WEB),
    SC_RESEARCH_CONSULTANCY: (T5_API_AGGREGATOR, ACCESS_PUBLIC_WEB),
    SC_AGGREGATOR: (T5_API_AGGREGATOR, ACCESS_PUBLIC_WEB),
    SC_UNKNOWN_WEB: (T5_API_AGGREGATOR, ACCESS_PUBLIC_WEB),
}
SOURCE_CLASSES: frozenset[str] = frozenset(CLASS_TABLE)

# ── Curated lists (versioned data) ──────────────────────────────────────── #

_REGULATOR_HOSTS: tuple[str, ...] = (
    "sec.gov", "fca.org.uk", "esma.europa.eu", "eba.europa.eu", "bafin.de", "amf-france.org",
    "consob.it", "cnmv.es", "finanstilsynet.no", "fi.se", "asic.gov.au", "osc.ca",
    "ferc.gov", "nrc.gov", "fda.gov", "ema.europa.eu", "ofgem.gov.uk", "ofcom.org.uk",
    "cma.gov.uk", "ftc.gov", "cftc.gov", "finra.org",
)
_STATISTICAL_HOSTS: tuple[str, ...] = (
    "bls.gov", "census.gov", "bea.gov", "ons.gov.uk", "destatis.de", "insee.fr",
    "istat.it", "ine.es", "cbs.nl", "scb.se", "ssb.no", "dst.dk", "abs.gov.au",
    "statcan.gc.ca", "stat.go.jp", "stats.gov.cn", "ec.europa.eu/eurostat",
)
_INDUSTRY_ASSOCIATIONS: tuple[str, ...] = (
    "world-aluminium.org", "worldsteel.org", "silverinstitute.org", "gold.org",
    "copperalliance.org", "internationalcopper.org", "semi.org", "acea.auto",
    "eurelectric.org", "windeurope.org", "solarpowereurope.org", "iai.org",
    "cobaltinstitute.org", "nickelinstitute.org", "lithium.org", "api.org", "nema.org",
    "t-d-europe.eu", "vdma.org", "zvei.org", "cefic.org", "worldnuclear.org",
    "world-nuclear.org", "icmm.com", "phrma.org", "bio.org",
)
_STANDARDS_BODIES: tuple[str, ...] = (
    "iso.org", "iec.ch", "ieee.org", "astm.org", "cen.eu", "cenelec.eu", "etsi.org",
    "itu.int", "w3.org", "ietf.org", "din.de", "bsigroup.com", "ansi.org",
)
_ACADEMIC_HOSTS: tuple[str, ...] = (
    "arxiv.org", "doi.org", "nature.com", "science.org", "sciencedirect.com",
    "springer.com", "link.springer.com", "wiley.com", "tandfonline.com", "mdpi.com",
    "ssrn.com", "nber.org", "researchgate.net", "pubmed.ncbi.nlm.nih.gov", "jstor.org",
    "acs.org", "iop.org", "cell.com", "plos.org",
)
_ACADEMIC_SUFFIXES: tuple[str, ...] = (".edu", ".ac.uk", ".ac.jp", ".edu.au", ".ac.at")
_MAJOR_FINANCIAL_PRESS: tuple[str, ...] = (
    "reuters.com", "ft.com", "bloomberg.com", "wsj.com", "economist.com", "nytimes.com",
    "apnews.com", "cnbc.com", "barrons.com", "handelsblatt.com", "lesechos.fr",
    "nikkei.com", "asia.nikkei.com", "faz.net", "ilsole24ore.com", "expansion.com",
)
_TRADE_PUBLICATIONS: tuple[str, ...] = (
    "mining.com", "mining-journal.com", "northernminer.com", "fastmarkets.com",
    "argusmedia.com", "bnamericas.com", "spglobal.com", "utilitydive.com",
    "power-technology.com", "tdworld.com", "electrive.com", "semiengineering.com",
    "eetimes.com", "fiercepharma.com", "fiercebiotech.com", "chemanager-online.com",
    "rechargenews.com", "renewableenergyworld.com", "offshore-energy.biz",
    # Mining and critical-minerals trade press that the first live critical-minerals Discovery
    # run fetched and could not use: all were ``unknown_web``, so rule A3 (a passage from an
    # acceptable class) could never pass for a mining company. Established titles only —
    # promotion-heavy junior-stock sites stay ``unknown_web``.
    "mining-technology.com", "miningweekly.com", "mineweb.com", "miningmx.com",
    "australianmining.com.au", "news.metal.com", "panorama-minero.com",
    "rareearthexchanges.com",
)
_RESEARCH_CONSULTANCIES: tuple[str, ...] = (
    "mckinsey.com", "bcg.com", "bain.com", "deloitte.com", "pwc.com", "ey.com",
    "kpmg.com", "woodmac.com", "rystadenergy.com", "bnef.com", "gartner.com",
    "idc.com", "iea-pvps.org",
)
_AGGREGATORS: tuple[str, ...] = (
    "finance.yahoo.com", "yahoo.com", "marketscreener.com", "seekingalpha.com",
    "investing.com", "msn.com", "news.google.com", "marketwatch.com", "fool.com",
    "benzinga.com", "zacks.com", "tipranks.com", "stocktitan.net", "finanznachrichten.de",
)
#: PR wires publish the ISSUER's words (spec §14.2: origin = issuer).
_PR_WIRES: tuple[str, ...] = (
    "globenewswire.com", "businesswire.com", "prnewswire.com", "prnewswire.co.uk",
    "accesswire.com", "newsfilecorp.com", "cision.com", "mynewsdesk.com", "ots.at",
    "dgap.de", "eqs-news.com", "newswire.ca", "einpresswire.com",
)

_PRESS_PATH_RE = re.compile(
    r"/(?:press|news|media|newsroom|pressemitteilung|communiques?|actualites)(?:/|-|$)", re.I
)
_INVESTOR_PATH_RE = re.compile(r"/(?:investors?|ir|investor-relations|presentations?)(?:/|$)", re.I)


def _host(url: str | None) -> str:
    try:
        host = (urlsplit(url or "").hostname or "").lower().strip(".")
    except ValueError:
        return ""
    return host.removeprefix("www.")


def trade_publication_hosts() -> tuple[str, ...]:
    """The curated trade-press hosts (``host`` entries only, no ``host/path`` ones), sorted.

    What a query may be RESTRICTED to when it wants results from sources the admission
    rules accept — the list the classifier itself uses, so a restricted query cannot return
    a page the classifier would not call a trade publication.
    """
    return tuple(sorted(h for h in _TRADE_PUBLICATIONS if "/" not in h))


def _on(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes if "/" not in s)


def _on_path(url: str, entries: tuple[str, ...]) -> bool:
    """A ``host/path`` entry matches only the URL's real HOST and the start of its real
    PATH — never a substring anywhere in the URL (W3 review B1: a query string or a
    path segment spelling ``ec.europa.eu/eurostat`` must not raise a tier)."""
    try:
        parts = urlsplit(url or "")
        host = (parts.hostname or "").lower().strip(".").removeprefix("www.")
        path = (parts.path or "/").lower()
    except ValueError:
        return False
    for entry in entries:
        if "/" not in entry:
            continue
        base, _, prefix = entry.lower().partition("/")
        if not (host == base or host.endswith("." + base)):
            continue
        wanted = "/" + prefix.strip("/")
        if path == wanted or path.startswith(wanted + "/"):
            return True
    return False


@dataclass(frozen=True)
class SourceClassification:
    source_class: str
    tier: str
    access_class: str
    use_constraint: str
    use_constraint_basis: str
    licence_id: str | None
    #: Which rule decided: ``issuer_domain`` | ``host_rule`` | ``curated_list`` |
    #: ``page_signal`` | ``fallback``.
    rule: str


def _classify_host(url: str, host: str, issuer_domains: tuple[str, ...]) -> tuple[str, str] | None:
    from app.services.discovery.identity import EXCHANGE_HOSTS

    path = urlsplit(url).path if url else ""
    if issuer_domains and _on(host, tuple(d.lower().removeprefix("www.") for d in issuer_domains)):
        if _INVESTOR_PATH_RE.search(path) and path.lower().endswith(".pdf"):
            return SC_INVESTOR_PRESENTATION, "issuer_domain"
        if _PRESS_PATH_RE.search(path):
            return SC_COMPANY_PRESS_RELEASE, "issuer_domain"
        return SC_COMPANY_WEB_PAGE, "issuer_domain"
    tier = publisher_tier(url)
    if tier == T1_PRIMARY_FILING:
        if _on(host, ("asx.com.au",)):
            return SC_EXCHANGE_ANNOUNCEMENT, "host_rule"
        return SC_REGULATORY_FILING, "host_rule"
    if _on(host, EXCHANGE_HOSTS):
        return SC_EXCHANGE_ANNOUNCEMENT, "host_rule"
    if tier == T3_INDUSTRY_SPECIALIST:
        return SC_SPECIALIST_AGENCY, "host_rule"
    if _on(host, _STATISTICAL_HOSTS) or _on_path(url, _STATISTICAL_HOSTS):
        return SC_STATISTICAL_AGENCY, "host_rule"
    if _on(host, _REGULATOR_HOSTS):
        return SC_REGULATOR_PUBLICATION, "host_rule"
    if tier == T2_REGULATOR_OR_GOV:
        return SC_GOVERNMENT_PUBLICATION, "host_rule"
    return None


def _classify_curated(host: str) -> str | None:
    for entries, cls in (
        (_PR_WIRES, SC_COMPANY_PRESS_RELEASE),
        (_INDUSTRY_ASSOCIATIONS, SC_INDUSTRY_ASSOCIATION),
        (_STANDARDS_BODIES, SC_STANDARDS_BODY),
        (_ACADEMIC_HOSTS, SC_ACADEMIC_PAPER),
        (_MAJOR_FINANCIAL_PRESS, SC_MAJOR_FINANCIAL_PRESS),
        (_TRADE_PUBLICATIONS, SC_TRADE_PUBLICATION),
        (_RESEARCH_CONSULTANCIES, SC_RESEARCH_CONSULTANCY),
        (_AGGREGATORS, SC_AGGREGATOR),
    ):
        if _on(host, entries):
            return cls
    if any(host.endswith(s) for s in _ACADEMIC_SUFFIXES):
        return SC_ACADEMIC_PAPER
    return None


def classify_source(
    url: str | None,
    *,
    issuer_domains: tuple[str, ...] = (),
    licence_signals: tuple[str, ...] = (),
) -> SourceClassification:
    """The §13.1 source class, tier, access class and use constraint of ``url``.

    ``issuer_domains`` must be VERIFIED issuer domains (``verified_issuer_sources``);
    a lookalike domain read as the company's own is the failure a tier exists to
    prevent, so nothing here guesses one.
    """
    link = url or ""
    host = _host(link)
    decided = _classify_host(link, host, issuer_domains) if host else None
    if decided is None:
        curated = _classify_curated(host) if host else None
        decided = (curated, "curated_list") if curated else (SC_UNKNOWN_WEB, "fallback")
    source_class, rule = decided
    tier, access_class = CLASS_TABLE[source_class]
    # PR wires carry the issuer's voice but the issuer is not verified here: they stay
    # "company says" material at the wire's access class, never a primary filing.
    if source_class == SC_COMPANY_PRESS_RELEASE and rule == "curated_list":
        access_class = ACCESS_PUBLIC_WEB
    use = use_constraint_for(host, licence_signals=licence_signals)
    return SourceClassification(
        source_class=source_class,
        tier=tier,
        access_class=access_class,
        use_constraint=use.use_constraint,
        use_constraint_basis=use.basis,
        licence_id=use.licence_id,
        rule=rule,
    )


# ── Document kind (spec §11.2) ──────────────────────────────────────────── #

_KIND_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (DOC_KIND_ANNUAL_REPORT, ("annual report", "geschaftsbericht", "geschäftsbericht",
                              "rapport annuel", "universal registration document",
                              "integrated report", "jahresbericht", "arsredovisning")),
    (DOC_KIND_INTERIM_REPORT, ("interim report", "half-year report", "half year report",
                               "quarterly report", "halbjahresbericht", "quartalsbericht",
                               "rapport semestriel")),
    (DOC_KIND_RESULTS_RELEASE, ("results announcement", "earnings release", "trading update",
                                "full-year results", "full year results")),
    (DOC_KIND_PRESENTATION, ("investor presentation", "capital markets day",
                             "results presentation", "investor day")),
    (DOC_KIND_WHITEPAPER, ("white paper", "whitepaper", "livre blanc", "weißbuch",
                           "weissbuch", "technical paper", "technical report")),
    (DOC_KIND_CONSULTATION, ("consultation", "call for evidence", "konsultation",
                             "request for comment", "public comment")),
    (DOC_KIND_FACTSHEET, ("factsheet", "fact sheet", "datasheet", "data sheet")),
    (DOC_KIND_PRESS_RELEASE, ("press release", "pressemitteilung", "communiqué de presse",
                              "communique de presse", "comunicato stampa")),
    (DOC_KIND_INDUSTRY_REPORT, ("market report", "industry report", "market outlook",
                                "outlook report", "market study", "state of the industry")),
)
_NEWS_TYPES = frozenset({"newsarticle", "reportagenewsarticle", "analysisnewsarticle",
                         "article", "blogposting", "backgroundnewsarticle"})
_SCHOLARLY_TYPES = frozenset({"scholarlyarticle", "medicalscholarlyarticle", "report"})
_ISSUER_DOCUMENT_KINDS = frozenset({DOC_KIND_ANNUAL_REPORT, DOC_KIND_INTERIM_REPORT,
                                    DOC_KIND_RESULTS_RELEASE, DOC_KIND_PRESENTATION})
_ISSUER_DOCUMENT_CLASSES = frozenset({SC_ISSUER_FILING, SC_REGULATORY_FILING,
                                      SC_EXCHANGE_ANNOUNCEMENT, SC_COMPANY_PRESS_RELEASE,
                                      SC_INVESTOR_PRESENTATION, SC_COMPANY_WEB_PAGE})
_GOV_CLASSES = frozenset({SC_GOVERNMENT_PUBLICATION, SC_REGULATOR_PUBLICATION,
                          SC_STATISTICAL_AGENCY, SC_SPECIALIST_AGENCY})


def classify_document_kind(
    *,
    url: str | None,
    title: str | None,
    source_class: str,
    content_class: str,
    jsonld_types: tuple[str, ...] = (),
    og_type: str | None = None,
    has_citation_meta: bool = False,
    headings: tuple[str, ...] = (),
) -> str:
    """The document kind from its title/URL words, page signals and class. Deterministic."""
    probe = " ".join(
        [title or "", (url or "").replace("-", " ").replace("_", " "), *headings[:3]]
    ).lower()
    issuer_voice = content_class == "pdf" or source_class in _ISSUER_DOCUMENT_CLASSES
    for kind, words in _KIND_KEYWORDS:
        if kind in _ISSUER_DOCUMENT_KINDS and not issuer_voice:
            # "X publishes its annual report" on a news site is a news article.
            continue
        if any(w in probe for w in words):
            return kind
    types = {t.lower() for t in jsonld_types}
    if has_citation_meta or types & _SCHOLARLY_TYPES and source_class == SC_ACADEMIC_PAPER:
        return DOC_KIND_ACADEMIC_PAPER
    if source_class == SC_ACADEMIC_PAPER:
        return DOC_KIND_ACADEMIC_PAPER
    if source_class == SC_COMPANY_PRESS_RELEASE:
        return DOC_KIND_PRESS_RELEASE
    if types & _NEWS_TYPES or (og_type or "").lower() == "article":
        return DOC_KIND_NEWS_ARTICLE
    if content_class == "pdf":
        if source_class in _GOV_CLASSES:
            return DOC_KIND_GOVERNMENT_REPORT
        if source_class in (SC_INDUSTRY_ASSOCIATION, SC_RESEARCH_CONSULTANCY,
                            SC_TRADE_PUBLICATION):
            return DOC_KIND_INDUSTRY_REPORT
        return DOC_KIND_OTHER
    if source_class in (SC_MAJOR_FINANCIAL_PRESS, SC_TRADE_PUBLICATION, SC_LOCAL_PRESS):
        return DOC_KIND_NEWS_ARTICLE
    return DOC_KIND_WEB_PAGE


# ── Injection taint (threat model §3.3) ─────────────────────────────────── #

_INSTRUCTION_PATTERNS: tuple[tuple[str, float, re.Pattern[str]], ...] = (
    ("ignore_previous", 0.6, re.compile(
        r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b(?:all|any|the)?\s*"
        r"(?:previous|prior|above|earlier|preceding|system)\b[^.\n]{0,20}"
        r"\b(?:instructions?|prompts?|rules|directions)", re.I)),
    ("ignore_previous_de", 0.6, re.compile(
        r"ignorier\w*\s+(?:alle\s+)?(?:vorherigen|bisherigen|obigen)\s+(?:anweisungen|befehle)",
        re.I)),
    ("ignore_previous_fr", 0.6, re.compile(
        r"ignore[rz]?\s+(?:toutes\s+)?les\s+instructions\s+(?:précédentes|precedentes)", re.I)),
    ("ignore_previous_ja", 0.6, re.compile(r"(?:以前|前)の(?:指示|命令)を無視")),
    ("assistant_address", 0.35, re.compile(
        r"\b(?:you are (?:now )?(?:an? )?(?:ai|assistant|language model|chatgpt|llm)|"
        r"as an ai (?:model|assistant)|dear (?:ai|assistant|model)|"
        r"(?:ai|assistant|model),? (?:you must|please) )", re.I)),
    ("role_tag", 0.4, re.compile(
        r"(?:<\|?(?:system|im_start|im_end|assistant)\|?>|\[/?INST\]|^\s*#{2,}\s*system\b|"
        r"\bsystem\s*prompt\b|\bsystem:\s)", re.I | re.M)),
    ("evidence_marker", 0.5, re.compile(r"(?:BEGIN|END)\s+EVIDENCE", re.I)),
    ("tool_name", 0.4, re.compile(
        r"\b(?:fetch_public_source|search_web|get_sec_statements|search_company_corpus|"
        r"get_financial_facts|call (?:the )?tool)\b", re.I)),
    ("credential_request", 0.4, re.compile(
        r"\b(?:api[_ ]?key|environment variables?|secret key|access token|password|"
        r"credentials?)\b[^.\n]{0,60}\b(?:print|reveal|send|output|show|share|return)\b|"
        r"\b(?:print|reveal|send|output|show|share|return)\b[^.\n]{0,60}"
        r"\b(?:api[_ ]?key|environment variables?|secret key|access token|credentials?)\b",
        re.I)),
    ("internal_address", 0.4, re.compile(
        r"(?:169\.254\.169\.254|metadata\.google\.internal|\blocalhost\b|127\.0\.0\.1|"
        r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\.internal\b)", re.I)),
    ("rating_instruction", 0.3, re.compile(
        r"\b(?:output|give|assign|issue|rate)\b[^.\n]{0,30}\b(?:buy|sell|strong buy)\b"
        r"[^.\n]{0,20}\brating\b", re.I)),
)
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")
_IMPERATIVE_NEAR_RE = re.compile(r"\b(?:decode|execute|run|follow|instructions?)\b", re.I)

SUSPECT_THRESHOLD = 0.5


@dataclass(frozen=True)
class InjectionAssessment:
    score: float
    suspect: bool
    signals: tuple[str, ...]


def injection_assessment(
    visible_text: str | None,
    *,
    hidden_text: str | None = "",
    metadata_text: str | None = "",
) -> InjectionAssessment:
    """Heuristic taint score in [0, 1]. A signal only — never a deletion."""
    visible = visible_text or ""
    hidden = hidden_text or ""
    meta = metadata_text or ""
    score = 0.0
    signals: list[str] = []
    for name, weight, pattern in _INSTRUCTION_PATTERNS:
        if pattern.search(visible):
            score += weight
            signals.append(name)
        if hidden and pattern.search(hidden):
            # Instructions a sighted reader cannot see are the attack itself.
            score += max(weight, 0.5)
            signals.append(f"hidden:{name}")
        if meta and pattern.search(meta):
            score += weight
            signals.append(f"metadata:{name}")
    for match in _BASE64_RE.finditer(visible[:200_000]):
        window = visible[max(0, match.start() - 200): match.start()]
        if _IMPERATIVE_NEAR_RE.search(window):
            score += 0.3
            signals.append("base64_near_instruction")
            break
    if has_tag_characters(visible) or has_tag_characters(meta):
        score += 0.5
        signals.append("unicode_tag_characters")
    elif invisible_char_count(visible) >= 20:
        score += 0.2
        signals.append("invisible_characters")
    score = min(1.0, round(score, 3))
    return InjectionAssessment(
        score=score, suspect=score >= SUSPECT_THRESHOLD, signals=tuple(dict.fromkeys(signals))
    )


__all__ = [
    "CLASS_TABLE",
    "CURATED_LISTS_VERSION",
    "SOURCE_CLASSES",
    "SUSPECT_THRESHOLD",
    "InjectionAssessment",
    "SourceClassification",
    "classify_document_kind",
    "classify_source",
    "injection_assessment",
]
