"""Origin, independence, corroboration and claim-type rules — open-web W4.

Spec §13.3 (claim type ↔ source class), §14.2 (origin), §14.3 (corroboration states),
§14.4 (contradictions) and §17.3 (web facts). Everything here is DETERMINISTIC: host
rules, versioned lists and regular expressions over text the platform stored. No LLM
decides an origin, a class, a claim type or a label.

ORIGIN (spec §14.2) — VERIFIED ORIGINS AND CLAIMS
=================================================
Two items are **independent** only when their independence keys differ. The design
rule (review round 1, F1/F2): *what a page says about itself is a CLAIM*. A claim may
only REDUCE independence; it never creates an issuer origin, merges with a
platform-verified origin, raises corroboration or hides a contradiction.

**Verified origins** (the platform decides): the verified issuer domain or a filing /
exchange-announcement host whose header names the run's issuer → ``issuer:<id>``;
a publisher-group registry entry → ``group:<id>``; otherwise the registrable domain the
bytes were served from. A PR-wire / RNS / ASX host is never an origin (it carries many
issuers); an unattributed wire release is ``unknown:<url hash>``.

**Claimed origins** (the page says): "(Reuters)", "laut dpa", a "Source:" line, an
"About <Company>" block with a contact block, a cross-domain ``rel=canonical`` →
``claimed:<what it says>@<publisher domain>``. Independence treats it as the claimed
origin (a page claiming Reuters is not independent of Reuters); contradiction checks key
on the PUBLISHER, so a forged claim cannot hide a real disagreement; labels and prompts
never show a page-derived name.

Near-duplicates (SimHash <= 3) are linked at ingest only when they are in the same
company/theme scope, carry IDENTICAL numbers, are not of lower authority than the new
document, and the text is long enough; stored origins are never rewritten.

ORIGIN KEY SHAPES
=================
``issuer:<company id>`` (verified; compared to the RUN's company every time) ·
``group:<id>`` · a bare registrable domain · ``unknown:<token>`` ·
``claimed:<body>@<publisher>``. Non-web platform evidence (typed facts, filings in the
corpus) uses ``issuer:<company id>``, so "the 10-K and the company's own page" is ONE
origin, ``issuer_only``.

CLAIM RULES (spec §13.3) — KEPT WITH A LABEL, NEVER DELETED
==========================================================
A finding whose support includes open-web evidence is checked at persistence
(``ledger.record_finding``). A finding that does not meet its claim type's rule is
stored with its label prefixed ("[company says] …", "[single source] …"); an
issuer-only superlative is never stated as fact ("[company describes itself as …]").
Findings resting only on platform evidence (typed facts, filings, the pre-W4 corpus)
are unchanged — W4 is inert until web documents exist.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from itertools import islice
from typing import Any
from urllib.parse import urlsplit

from app.services import research_fields as rf
from app.services.web_research.classify import (
    SC_ACADEMIC_PAPER,
    SC_AGGREGATOR,
    SC_COMPANY_PRESS_RELEASE,
    SC_COMPANY_WEB_PAGE,
    SC_EXCHANGE_ANNOUNCEMENT,
    SC_GOVERNMENT_PUBLICATION,
    SC_INDUSTRY_ASSOCIATION,
    SC_INVESTOR_PRESENTATION,
    SC_ISSUER_FILING,
    SC_LOCAL_PRESS,
    SC_MAJOR_FINANCIAL_PRESS,
    SC_REGULATOR_PUBLICATION,
    SC_REGULATORY_FILING,
    SC_RESEARCH_CONSULTANCY,
    SC_SPECIALIST_AGENCY,
    SC_STANDARDS_BODY,
    SC_STATISTICAL_AGENCY,
    SC_TRADE_PUBLICATION,
    SC_UNKNOWN_WEB,
)
from app.services.web_research.dedup import DedupMember, cluster_near_duplicates

TRUST_RULES_VERSION = "2026-10-04.1"
PUBLISHER_GROUPS_VERSION = "2026-10-02.1"

ISSUER_ORIGIN_PREFIX = "issuer:"
COMPANY_ORIGIN_PREFIX = "company:"
GROUP_ORIGIN_PREFIX = "group:"
CLAIMED_ORIGIN_PREFIX = "claimed:"
UNKNOWN_ORIGIN_PREFIX = "unknown:"
_ORIGIN_MAX = 255

# ── Origin rules ────────────────────────────────────────────────────────── #

RULE_CLUSTER = "near_duplicate_cluster"
RULE_ISSUER_SOURCE = "issuer_source_class"
RULE_PR_WIRE = "pr_wire_host"
RULE_WIRE = "wire_attribution"
RULE_SOURCE_LINE = "source_attribution"
RULE_BOILERPLATE = "press_release_boilerplate"
RULE_CANONICAL = "cross_domain_canonical"
RULE_DOMAIN = "registrable_domain"
RULE_UNKNOWN = "unknown"

#: Hosts that publish an ISSUER's words on its behalf (spec §14.2: origin = issuer).
#: PR wires, plus the RNS and ASX announcement feeds and their mirrors.
PR_WIRE_HOSTS: tuple[str, ...] = (
    "globenewswire.com", "businesswire.com", "prnewswire.com", "prnewswire.co.uk",
    "accesswire.com", "newsfilecorp.com", "cision.com", "mynewsdesk.com", "ots.at",
    "dgap.de", "eqs-news.com", "newswire.ca", "einpresswire.com", "mfn.se",
    # RNS (London Stock Exchange regulatory news) and mirrors.
    "londonstockexchange.com", "investegate.co.uk",
    # ASX Market Announcements Platform.
    "asx.com.au", "announcements.asx.com.au",
)

#: Wire services and the origin each one is (a domain, so the group registry applies).
WIRE_SERVICES: dict[str, str] = {
    "reuters": "reuters.com",
    "reuters breakingviews": "reuters.com",
    "bloomberg": "bloomberg.com",
    "bloomberg news": "bloomberg.com",
    "associated press": "apnews.com",
    "ap": "apnews.com",
    "afp": "afp.com",
    "agence france-presse": "afp.com",
    "agence france presse": "afp.com",
    "dpa": "dpa.com",
    "dpa-afx": "dpa.com",
    "ansa": "ansa.it",
    "kyodo": "kyodonews.net",
    "kyodo news": "kyodonews.net",
    "jiji": "jiji.com",
    "jiji press": "jiji.com",
    "efe": "efe.com",
    "europa press": "europapress.es",
    "apa": "apa.at",
    "anp": "anp.nl",
    "belga": "belga.be",
    "ritzau": "ritzau.dk",
    "tt": "tt.se",
    "ntb": "ntb.no",
    "pap": "pap.pl",
    "aap": "aap.com.au",
    "press trust of india": "ptinews.com",
    "pti": "ptinews.com",
    "yonhap": "yna.co.kr",
    "xinhua": "xinhuanet.com",
    "dow jones": "dowjones.com",
    "dow jones newswires": "dowjones.com",
    # Japanese wire names as printed in Japanese text.
    "ロイター": "reuters.com",
    "ブルームバーグ": "bloomberg.com",
    "共同通信": "kyodonews.net",
    "時事通信": "jiji.com",
}

#: Outlets under one owner count as ONE origin for corroboration (versioned data).
PUBLISHER_GROUPS: dict[str, tuple[str, ...]] = {
    "news_corp": (
        "wsj.com", "barrons.com", "marketwatch.com", "dowjones.com", "investors.com",
        "thetimes.co.uk", "thesun.co.uk", "theaustralian.com.au", "nypost.com",
    ),
    "nikkei": ("ft.com", "nikkei.com"),
    "axel_springer": (
        "businessinsider.com", "businessinsider.de", "politico.com", "politico.eu",
        "welt.de", "bild.de",
    ),
    "bloomberg_lp": ("bloomberg.com", "bloomberglaw.com", "bnef.com"),
    "gannett": ("usatoday.com",),
    "daily_mail_dmgt": ("dailymail.co.uk", "thisismoney.co.uk", "inews.co.uk"),
    "guardian_media": ("theguardian.com", "theguardian.co.uk"),
    "les_echos_lvmh": ("lesechos.fr", "leparisien.fr"),
    "handelsblatt_media": ("handelsblatt.com", "wiwo.de"),
    "gedi_exor": ("repubblica.it", "lastampa.it"),
    "nine_entertainment": ("smh.com.au", "afr.com", "theage.com.au"),
    "glacier_media": ("northernminer.com", "mining.com"),
}

_GROUP_BY_DOMAIN: dict[str, str] = {
    domain: group for group, domains in PUBLISHER_GROUPS.items() for domain in domains
}

#: Classes that ARE the issuer's own voice.
FILING_CLASSES: frozenset[str] = frozenset(
    {SC_ISSUER_FILING, SC_REGULATORY_FILING, SC_EXCHANGE_ANNOUNCEMENT}
)
ISSUER_CLASSES: frozenset[str] = FILING_CLASSES | frozenset(
    {SC_COMPANY_PRESS_RELEASE, SC_INVESTOR_PRESENTATION, SC_COMPANY_WEB_PAGE}
)
GOVERNMENT_CLASSES: frozenset[str] = frozenset(
    {SC_GOVERNMENT_PUBLICATION, SC_REGULATOR_PUBLICATION, SC_STATISTICAL_AGENCY}
)
REGULATOR_CLASSES: frozenset[str] = frozenset(
    {SC_GOVERNMENT_PUBLICATION, SC_REGULATOR_PUBLICATION}
)
SPECIALIST_CLASSES: frozenset[str] = frozenset(
    {SC_SPECIALIST_AGENCY, SC_STANDARDS_BODY, SC_ACADEMIC_PAPER, SC_INDUSTRY_ASSOCIATION}
)
PRESS_CLASSES: frozenset[str] = frozenset(
    {SC_MAJOR_FINANCIAL_PRESS, SC_TRADE_PUBLICATION, SC_LOCAL_PRESS}
)
#: Classes that are never sufficient alone (spec §13.1 "❌ alone").
WEAK_CLASSES: frozenset[str] = frozenset({SC_AGGREGATOR, SC_UNKNOWN_WEB})
#: Spec §13.1 "T2–T4": the independent classes a superlative or headline number needs.
INDEPENDENT_AUTHORITY_CLASSES: frozenset[str] = (
    GOVERNMENT_CLASSES | SPECIALIST_CLASSES | PRESS_CLASSES
)


# ── Small helpers ───────────────────────────────────────────────────────── #


def _host(url: str | None) -> str:
    try:
        host = (urlsplit(url or "").hostname or "").lower().strip(".")
    except ValueError:
        return ""
    return host.removeprefix("www.")


def _on(host: str, suffixes: Iterable[str]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def registrable(host_or_domain: str | None) -> str | None:
    """The registrable domain of a host (``ir.example.com`` → ``example.com``)."""
    from app.services.sources.public_suffix import registrable_domain

    clean = (host_or_domain or "").strip().lower().strip(".").removeprefix("www.")
    if not clean:
        return None
    return (registrable_domain(clean) or clean)[:_ORIGIN_MAX]


def name_slug(name: str | None) -> str | None:
    """A company name as an origin token: folded, legal suffixes removed, hyphenated."""
    from app.services.discovery.identity import normalised_name

    folded = unicodedata.normalize("NFKD", name or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    words = normalised_name(folded).split()
    slug = "-".join(words)
    return slug[:120] or None


def is_issuer_origin(origin_key: str | None) -> bool:
    """``issuer:<id>`` — platform-verified only (a claimed origin never starts so)."""
    return bool(origin_key) and str(origin_key).startswith(ISSUER_ORIGIN_PREFIX)


def group_origin(origin_key: str | None) -> str | None:
    """Spec §14.2 step 5: a domain under a known owner becomes the owner's group."""
    if not origin_key or ":" in origin_key:
        return origin_key
    group = _GROUP_BY_DOMAIN.get(origin_key)
    return f"{GROUP_ORIGIN_PREFIX}{group}" if group else origin_key


#: Brand → parent for the cross-brand aliases a claimed company name may use (a page that
#: says "Source: Google" and one that says "Source: Alphabet" are one source).
BRAND_ALIASES: dict[str, str] = {
    "google": "alphabet", "youtube": "alphabet", "waymo": "alphabet",
    "facebook": "meta", "instagram": "meta", "whatsapp": "meta", "meta-platforms": "meta",
    "aws": "amazon", "amazon-web-services": "amazon", "whole-foods": "amazon",
    "linkedin": "microsoft", "github": "microsoft", "azure": "microsoft",
    "volkswagen-group": "volkswagen", "audi": "volkswagen", "porsche-ag": "volkswagen",
    "rio-tinto-plc": "rio-tinto", "rio-tinto-limited": "rio-tinto",
    "bhp-billiton": "bhp", "bhp-group": "bhp",
}


def make_claimed(body: str, publisher: str | None) -> str:
    """``claimed:<what the page says>@<registrable publisher>`` — a CLAIM, never verified."""
    return f"{CLAIMED_ORIGIN_PREFIX}{body}@{publisher or 'unknown'}"[:_ORIGIN_MAX]


def is_claimed_origin(origin_key: str | None) -> bool:
    return bool(origin_key) and str(origin_key).startswith(CLAIMED_ORIGIN_PREFIX)


def claimed_body(origin_key: str | None) -> str | None:
    """What a claimed origin says the text came from, or None for a verified origin."""
    if not is_claimed_origin(origin_key):
        return None
    return str(origin_key)[len(CLAIMED_ORIGIN_PREFIX):].rpartition("@")[0] or None


def publisher_of(origin_key: str | None) -> str | None:
    """The PLATFORM-VERIFIED publisher behind an origin (the registrable domain the bytes
    were served from). For ``issuer:`` / ``group:`` / bare-domain keys, the key itself."""
    if not origin_key:
        return None
    if is_claimed_origin(origin_key):
        return str(origin_key).rpartition("@")[2] or None
    return str(origin_key)


def independence_key(origin_key: str | None) -> str | None:
    """What an origin counts as when independence is judged.

    A claimed origin counts as the thing it claims to depend on — a page that says
    "(Reuters)" is NOT independent of Reuters, one that says "About <the issuer>" is not
    independent of the issuer. That can only REDUCE the number of independent origins; a
    claim never creates an origin (spec §14.2, review F1). The claim is never used to
    decide whether two findings may contradict: that is keyed on :func:`publisher_of`.
    """
    if not origin_key:
        return None
    body = claimed_body(origin_key) if is_claimed_origin(origin_key) else str(origin_key)
    if not body:
        return publisher_of(origin_key)
    if body.startswith(COMPANY_ORIGIN_PREFIX):
        slug = body[len(COMPANY_ORIGIN_PREFIX):]
        return f"{COMPANY_ORIGIN_PREFIX}{BRAND_ALIASES.get(slug, slug)}"
    if body.startswith((ISSUER_ORIGIN_PREFIX, GROUP_ORIGIN_PREFIX, UNKNOWN_ORIGIN_PREFIX)):
        return body
    return group_origin(body)


def is_verified_issuer(origin_key: str | None, issuer_key: Any) -> bool:
    """The origin is the RUN's issuer, verified (never claimed, never another company's).

    ``issuer:<id>`` is stored on a version shared by content hash, so the same string can
    be one run's own voice and another run's third party: it is compared to the run's
    issuer here, every time (review H2). No issuer key → nothing is "mine" (fail closed).
    """
    return bool(issuer_key) and origin_key == f"{ISSUER_ORIGIN_PREFIX}{issuer_key}"


def origin_display(origin_key: str | None) -> str:
    """A short name for an origin, for labels and gap text.

    Only platform data is ever shown: the issuer, a registry group, a registrable domain.
    A name taken from page text (a claimed company) is never displayed (review F4): a
    page can title itself anything, and the label ends up beside a finding.
    """
    if not origin_key:
        return "an unidentified source"
    if is_issuer_origin(origin_key):
        return "the company"
    if is_claimed_origin(origin_key):
        publisher = publisher_of(origin_key)
        if not publisher:
            return "an unidentified source"
        return f"{publisher} (page claims another source)"
    if origin_key.startswith(GROUP_ORIGIN_PREFIX):
        return origin_key[len(GROUP_ORIGIN_PREFIX):].replace("_", " ")
    if origin_key.startswith((COMPANY_ORIGIN_PREFIX, UNKNOWN_ORIGIN_PREFIX)):
        return "an unidentified source"
    if re.fullmatch(r"[a-z0-9.-]{3,120}", origin_key):
        return origin_key
    return "an unidentified source"


# ── Issuer identity ─────────────────────────────────────────────────────── #


def _name_words(text: str | None) -> str:
    slug = name_slug(text) or ""
    return " ".join(slug.split("-"))


def _fold_text(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).casefold()
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


#: A legal-entity suffix: another company named earlier in a header.
_LEGAL_ENTITY_RE = re.compile(
    r"(?<![a-z0-9])(?:ltd|limited|inc|corp|corporation|plc|ag|gmbh|sa|nv|llc|lp|asa|ab|oyj|spa)"
    r"(?![a-z0-9])"
)


@dataclass(frozen=True)
class IssuerIdentity:
    """The company a run is about: its key, names and VERIFIED domains."""

    key: str
    #: Full registered / trading names. Authorship rules read only these.
    names: tuple[str, ...] = ()
    #: Short names ("Meta", "Target"): too ambiguous to prove authorship, so they are only
    #: ever used to recognise a CLAIM of the same company.
    short_names: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()

    @property
    def origin_key(self) -> str:
        return f"{ISSUER_ORIGIN_PREFIX}{self.key}"[:_ORIGIN_MAX]

    def is_named(self, name: str | None) -> bool:
        """EXACT slug equality with a full or short name. "Acme Rivals Inc" is not Acme;
        "Apple Hospitality REIT" is not Apple (review F1/M3)."""
        slug = name_slug(name)
        if not slug:
            return False
        own = {s for s in (name_slug(n) for n in (*self.names, *self.short_names)) if s}
        return slug in own

    def authored_in_lead(self, text: str | None, *, limit: int = 400) -> bool:
        """A FULL name of the issuer stands in the first ``limit`` characters (a filing's
        header) and no OTHER legal entity is named before it. Used only on hosts the
        platform already classifies as filings or exchange announcements — the host is
        the verification, the name picks the issuer. A peer's announcement that merely
        mentions the issuer names the peer first.
        """
        head = _fold_text((text or "")[:limit])
        for name in self.names:
            words = _name_words(name).split()
            if len(" ".join(words)) < 5:
                continue
            joined = r"[\W_]+".join(re.escape(w) for w in words)
            pattern = r"(?<![a-z0-9])" + joined + r"(?![a-z0-9])"
            match = re.search(pattern, head)
            if match is not None and not _LEGAL_ENTITY_RE.search(head[: match.start()]):
                return True
        return False

    @classmethod
    def build(
        cls,
        company_id: Any = None,
        *,
        names: Iterable[str | None] = (),
        short_names: Iterable[str | None] = (),
        domains: Iterable[str | None] = (),
    ) -> "IssuerIdentity | None":
        clean_names = tuple(dict.fromkeys(n.strip() for n in names if n and n.strip()))
        clean_short = tuple(dict.fromkeys(n.strip() for n in short_names if n and n.strip()))
        clean_domains = tuple(
            dict.fromkeys(d for d in (registrable(x) for x in domains) if d)
        )
        if company_id:
            key = str(company_id)
        elif clean_domains:
            key = clean_domains[0]
        elif clean_names and name_slug(clean_names[0]):
            key = str(name_slug(clean_names[0]))
        else:
            return None
        return cls(key=key, names=clean_names, short_names=clean_short, domains=clean_domains)


def issuer_from_candidates(
    company_id: Any, candidates: Iterable[Any], issuer_domains: Iterable[str] = ()
) -> IssuerIdentity | None:
    """The subject issuer from W3's entity candidates (``entities.CandidateEntity``)."""
    names: list[str] = []
    short: list[str] = []
    for candidate in candidates or ():
        if company_id is not None and getattr(candidate, "company_id", None) == company_id:
            names.append(getattr(candidate, "name", "") or "")
            short.extend(getattr(candidate, "short_names", ()) or ())
    if company_id is None and not issuer_domains:
        return None
    return IssuerIdentity.build(company_id, names=names, short_names=short, domains=issuer_domains)


# ── Text attribution (spec §14.2 steps 2–3): CLAIMS, never verified ─────── #
#
# Everything below reads PAGE TEXT. A page can say anything, so what it says is only a
# claim of dependence: it may reduce how independent a document counts as, and never
# creates an issuer origin, merges with a verified one, raises corroboration or hides a
# contradiction (review F1/F2). Every scan is bounded to a fixed window and every pattern
# is anchored per line, so no page can make an origin rule cost more than a constant
# (review F3).

_WIRE_NAMES = sorted(WIRE_SERVICES, key=len, reverse=True)
_LATIN_WIRES = [w for w in _WIRE_NAMES if w.isascii()]
_CJK_WIRES = [w for w in _WIRE_NAMES if not w.isascii()]
_WIRE_ALT = "|".join(re.escape(w) for w in _LATIN_WIRES)

#: Short agency names that are also ordinary words: only an ALL-CAPS spelling is the
#: agency ("(AP)", "TT", "PTI"). Longer unambiguous codes ("dpa", "afp") are accepted in
#: any case — a lowercase "(dpa)" is still dpa (review H1).
_CASE_SENSITIVE_WIRES = frozenset({"ap", "tt", "pti", "apa", "anp", "ntb", "pap", "aap", "efe"})

#: A dateline / byline: "LONDON (Reuters) -", "(dpa-AFX)", "(ANSA) - ROMA".
_DATELINE_RE = re.compile(r"\(\s*(" + _WIRE_ALT + r")\s*\)", re.IGNORECASE)
#: "© Reuters", "Copyright 2026 AFP", "(c) dpa".
_COPYRIGHT_RE = re.compile(
    r"(?:©|\(c\)|copyright)\s*(?:\d{4}\s*)?(" + _WIRE_ALT + r")\b", re.IGNORECASE
)
#: Attribution phrases, multilingual. Read in the LEAD only: an article quoting Reuters
#: for one fact deep in its body is not a Reuters rewrite.
_ATTRIBUTION_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(" + _WIRE_ALT + r")\s+(?:reported|reports|said|wrote|first reported)\b",
        r"\b(?:according to|reported by|as reported by|told)\s+(" + _WIRE_ALT + r")\b",
        r"\b(?:laut|berichtete|berichtet|meldete|meldet)\s+(?:die\s+|der\s+)?"
        r"(?:nachrichtenagentur\s+)?(" + _WIRE_ALT + r")\b",
        r"\b(?:selon|d'après|rapporte|a rapporté)\s+(?:l'|l’|le\s+|la\s+)?(?:agence\s+)?("
        + _WIRE_ALT + r")\b",
        r"\b(?:secondo|riporta|ha riportato)\s+(?:l'|l’|la\s+|il\s+)?(?:agenzia\s+)?("
        + _WIRE_ALT + r")\b",
        r"\b(?:según|informó|informa)\s+(?:la\s+agencia\s+|el\s+)?(" + _WIRE_ALT + r")\b",
        r"\b(?:volgens|meldt|meldde)\s+(?:persbureau\s+)?(" + _WIRE_ALT + r")\b",
        r"\b(?:enligt|ifølge|ifolge)\s+(" + _WIRE_ALT + r")\b",
    )
)
_CJK_ATTRIBUTION_RE = (
    re.compile(
        "(" + "|".join(re.escape(w) for w in _CJK_WIRES) + ")"
        + "(?:によると|が報じた|は報じた|通信)"
    )
    if _CJK_WIRES
    else None
)
#: A whole line naming the source: "Source: Acme Corp", "Quelle: dpa", "Fonte: ANSA".
#: ``[ \t]*`` (not ``\s*``) at the line start: ``\s*`` also eats newlines, which made a
#: page of blank lines quadratic.
_SOURCE_LINE_RE = re.compile(
    r"^[ \t]*(?:source|sources|quelle|fonte|fuente|bron|källa|kilde)[ \t]*[:：][ \t]*"
    r"(?P<name>[^\n]{2,80}?)[ \t]*\.?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_GENERIC_SOURCE_WORDS = frozenset(
    {
        "company", "company data", "company filings", "data", "estimates", "internal",
        "own calculations", "own research", "various", "the company", "annual report",
        "bloomberg data", "refinitiv", "factset", "s&p global", "statista", "wikipedia",
        "unternehmensangaben", "eigene berechnungen", "société",
    }
)
#: "About Acme Corp" as a heading line, multilingual.
_ABOUT_RE = re.compile(
    r"^[ \t]*(?:about|über|uber|à propos de|a propos de|a proposito di|informazioni su|"
    r"acerca de|sobre|over|om|tietoa)[ \t]+"
    r"(?P<name>[A-ZÀ-ÖØ-Þ0-9][^\n]{1,80}?)[ \t]*[:：]?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
#: "About us" / "Om oss" is the page talking about itself — it names nobody.
_SELF_REFERENCES = frozenset(
    {
        "us", "oss", "nous", "uns", "ons", "noi", "nosotros", "this site", "this website",
        "this company", "the company", "our company", "the author", "this page", "me",
    }
)
_CONTACT_RE = re.compile(
    r"(?:^[ \t]*(?:contacts?|media contacts?|press contacts?|investor contacts?|"
    r"investor relations|media relations|for further information|for more information|"
    r"kontakt|pressekontakt|contacts? presse|contatti|contacto|contactos)\b)"
    r"|[\w.+-]{1,64}@[\w-]{1,64}\.[\w.-]{1,64}"
    r"|\+?\d[\d ().-]{7,30}\d",
    re.IGNORECASE | re.MULTILINE,
)
#: How much of a document's start / end the attribution rules read, and how far past a
#: heading the contact block may sit.
LEAD_CHARS = 700
TAIL_CHARS = 1200
BOILERPLATE_WINDOW = 3000
CONTACT_WINDOW = 600
MAX_ATTRIBUTION_MATCHES = 20


def _wire_domain(name: str | None) -> str | None:
    key = re.sub(r"\s+", " ", (name or "").strip().lower())
    if key in WIRE_SERVICES:
        return WIRE_SERVICES[key]
    return WIRE_SERVICES.get((name or "").strip())


def _plausible_wire_surface(surface: str) -> bool:
    """Reject a short ordinary word posing as an agency ("The plant (ap) runs…")."""
    folded = surface.strip().lower()
    return not (folded in _CASE_SENSITIVE_WIRES and surface != surface.upper())


def wire_attribution(text: str | None) -> tuple[str, str] | None:
    """``(wire domain, matched surface)`` when the text CLAIMS a wire wrote it."""
    body = text or ""
    lead = body[:LEAD_CHARS]
    tail = body[-TAIL_CHARS:]
    for pattern, region in (
        (_DATELINE_RE, lead),
        (_COPYRIGHT_RE, lead + "\n" + tail),
        *((p, lead) for p in _ATTRIBUTION_RES),
    ):
        match = pattern.search(region)
        if match is None:
            continue
        surface = match.group(1)
        if not _plausible_wire_surface(surface):
            continue
        domain = _wire_domain(surface)
        if domain:
            return domain, match.group(0)
    if _CJK_ATTRIBUTION_RE is not None:
        match = _CJK_ATTRIBUTION_RE.search(lead)
        if match is not None:
            domain = _wire_domain(match.group(1))
            if domain:
                return domain, match.group(0)
    return None


def source_line(text: str | None) -> str | None:
    """The name on a trailing "Source: <name>" line, when it names a source (a claim)."""
    tail = (text or "")[-TAIL_CHARS:]
    for match in islice(_SOURCE_LINE_RE.finditer(tail), MAX_ATTRIBUTION_MATCHES):
        name = match.group("name").strip().strip(".")
        low = name.lower()
        if low in _GENERIC_SOURCE_WORDS or len(name.split()) > 6:
            continue
        if not name[:1].isupper() and _wire_domain(name) is None:
            continue
        return name
    return None


def boilerplate_company(text: str | None) -> str | None:
    """The company of an "About <Company>" block followed by a contact block (a claim)."""
    body = (text or "")[-BOILERPLATE_WINDOW:]
    for match in islice(_ABOUT_RE.finditer(body), MAX_ATTRIBUTION_MATCHES):
        name = match.group("name").strip()
        words = name.lower().split()
        if len(words) > 8 or name.lower() in _SELF_REFERENCES or words[0] in _SELF_REFERENCES:
            continue
        after = body[match.end(): match.end() + CONTACT_WINDOW]
        if _CONTACT_RE.search(after):
            return name
    return None


def _claimed_company(name: str, issuer: IssuerIdentity | None, publisher: str | None) -> str | None:
    """A claimed origin for a company NAME read from page text (never ``issuer:``)."""
    if issuer is not None and issuer.is_named(name):
        return make_claimed(issuer.origin_key, publisher)
    slug = name_slug(name)
    return make_claimed(f"{COMPANY_ORIGIN_PREFIX}{slug}", publisher) if slug else None


# ── The origin algorithm ────────────────────────────────────────────────── #


@dataclass(frozen=True)
class OriginInput:
    """One document as the origin algorithm reads it."""

    url: str | None
    text: str | None = None
    source_class: str | None = None
    #: The page's ``rel=canonical`` (W2 records it even when it did not honour it).
    rel_canonical: str | None = None


@dataclass(frozen=True)
class OriginDecision:
    origin_key: str
    rule: str
    detail: str | None = None
    #: Set when the publisher-group registry merged the origin.
    group: str | None = None

    @property
    def is_issuer(self) -> bool:
        return is_issuer_origin(self.origin_key)

    @property
    def is_claim(self) -> bool:
        return is_claimed_origin(self.origin_key)


def _grouped(origin: str, rule: str, detail: str | None) -> OriginDecision:
    grouped = group_origin(origin) or origin
    return OriginDecision(
        origin_key=grouped[:_ORIGIN_MAX],
        rule=rule,
        detail=detail,
        group=grouped if grouped != origin else None,
    )


def _url_token(url: str | None) -> str:
    return hashlib.sha256((url or "").encode("utf-8", "replace")).hexdigest()[:20]


def origin_for(doc: OriginInput, *, issuer: IssuerIdentity | None = None) -> OriginDecision:
    """Spec §14.2 steps 2–6 for ONE document (step 1, the cluster, is ``assign_origins``).

    Verified origins come from the PLATFORM only: the verified issuer domain, a
    filing/exchange host whose header names the run's issuer, a registry group, a
    registrable domain. Anything read from the page is a claim (``claimed:…@<publisher>``).
    """
    host = _host(doc.url)
    domain = registrable(host)
    text = doc.text or ""

    # Verified issuer voice 1: a page on the issuer's VERIFIED domain.
    if issuer is not None and issuer.domains and _on(host, issuer.domains):
        return OriginDecision(issuer.origin_key, RULE_ISSUER_SOURCE, domain)
    # Verified issuer voice 2: a filing / exchange-announcement host (the platform's own
    # classification) whose header names the issuer. Not any filing: a peer's ASX
    # announcement is the peer's (review H2).
    if (
        issuer is not None
        and doc.source_class in FILING_CLASSES
        and issuer.authored_in_lead(text)
    ):
        return OriginDecision(issuer.origin_key, RULE_ISSUER_SOURCE, domain)

    claim = _claimed_origin(doc, text, host, domain, issuer)

    # A PR wire / RNS / ASX feed carries many issuers' releases under ONE host: the host
    # is never the origin (it would merge two issuers). Claim or opaque.
    if host and _on(host, PR_WIRE_HOSTS):
        if claim is not None:
            return claim
        return OriginDecision(f"{UNKNOWN_ORIGIN_PREFIX}{_url_token(doc.url)}", RULE_UNKNOWN, host)

    if claim is not None:
        return claim
    if domain:
        return _grouped(domain, RULE_DOMAIN, None)
    return OriginDecision(f"{UNKNOWN_ORIGIN_PREFIX}{_url_token(doc.url)}", RULE_UNKNOWN, None)


def _claimed_origin(
    doc: OriginInput,
    text: str,
    host: str,
    domain: str | None,
    issuer: IssuerIdentity | None,
) -> OriginDecision | None:
    publisher = domain or None
    wire = wire_attribution(text)
    if wire is not None:
        return OriginDecision(make_claimed(wire[0], publisher), RULE_WIRE, wire[1][:60])
    named_source = source_line(text)
    if named_source:
        wire_domain = _wire_domain(named_source)
        if wire_domain:
            return OriginDecision(
                make_claimed(wire_domain, publisher), RULE_SOURCE_LINE, named_source[:60]
            )
        origin = _claimed_company(named_source, issuer, publisher)
        if origin:
            return OriginDecision(origin, RULE_SOURCE_LINE, None)
    about = boilerplate_company(text)
    if about:
        origin = _claimed_company(about, issuer, publisher)
        if origin:
            return OriginDecision(origin, RULE_BOILERPLATE, None)
    # A cross-domain rel=canonical is where the author SAYS the text lives — a claim.
    canonical_domain = registrable(_host(doc.rel_canonical))
    if canonical_domain and domain and canonical_domain != domain:
        return OriginDecision(
            make_claimed(canonical_domain, publisher), RULE_CANONICAL, canonical_domain
        )
    return None


def assign_origins(
    documents: Sequence[tuple[DedupMember, OriginInput]],
    *,
    issuer: IssuerIdentity | None = None,
) -> dict[str, OriginDecision]:
    """Origins for a set of documents, near-duplicate clusters first (spec §14.2 step 1).

    Every member of a cluster takes its representative's origin, so copies of one text
    are one origin however each copy is dressed. Used for analysis and tests; ingest
    links through ``dedup.find_linkable_duplicate``.
    """
    inputs = {member.key: doc for member, doc in documents}
    out: dict[str, OriginDecision] = {}
    for cluster in cluster_near_duplicates([member for member, _doc in documents]):
        lead = origin_for(inputs[cluster.representative.key], issuer=issuer)
        out[cluster.representative.key] = lead
        for member in cluster.linked:
            out[member.key] = OriginDecision(
                lead.origin_key, RULE_CLUSTER, cluster.representative.key, lead.group
            )
    return out


async def document_origin(
    session: Any,
    *,
    doc: OriginInput,
    simhash: int | None,
    text: str | None,
    company_id: Any = None,
    theme_key: str | None = None,
    issuer: IssuerIdentity | None = None,
) -> tuple[OriginDecision, Any]:
    """The origin of a document about to be stored, and a stored text it may link to.

    ``duplicate`` (a ``dedup.StoredDuplicate``) is returned only for a near-duplicate
    that is safe to link (``dedup.find_linkable_duplicate``: same scope, identical
    numbers, never to a lower-authority class). Linking NEVER rewrites a stored origin:
    the new document simply reports the representative's.
    """
    from app.services.web_research.dedup import find_linkable_duplicate

    own = origin_for(doc, issuer=issuer)
    duplicate = await find_linkable_duplicate(
        session,
        simhash=simhash,
        text=text,
        source_class=doc.source_class,
        company_id=company_id,
        theme_key=theme_key,
    )
    if duplicate is None:
        return own, None
    origin = duplicate.origin_key or own.origin_key
    return OriginDecision(origin, RULE_CLUSTER, str(duplicate.id)), duplicate


@dataclass(frozen=True)
class OriginSummary:
    """What the Council is told: "1 source (company), 4 republications"."""

    origins: int
    republications: int
    issuer_only: bool

    def describe(self) -> str:
        who = " (company)" if self.issuer_only else ""
        noun = "source" if self.origins == 1 else "sources"
        tail = (
            f", {self.republications} republication{'s' if self.republications != 1 else ''}"
            if self.republications
            else ""
        )
        return f"{self.origins} {noun}{who}{tail}"


def summarise_origins(
    origin_keys: Sequence[str | None], *, issuer_key: Any = None
) -> OriginSummary:
    keys = [independence_key(k) for k in origin_keys if k]
    distinct = set(keys)
    return OriginSummary(
        origins=len(distinct),
        republications=len(keys) - len(distinct),
        issuer_only=bool(distinct) and all(_is_issuer_key(k, issuer_key) for k in distinct),
    )


# ── Corroboration (spec §14.3) ──────────────────────────────────────────── #

SINGLE_SOURCE = "single_source"
ISSUER_ONLY = "issuer_only"
INDEPENDENTLY_CORROBORATED = "independently_corroborated"
CONFLICTING = "conflicting"
CORROBORATION_STATES: frozenset[str] = frozenset(
    {SINGLE_SOURCE, ISSUER_ONLY, INDEPENDENTLY_CORROBORATED, CONFLICTING}
)


def _is_issuer_key(key: str | None, issuer_key: Any) -> bool:
    """An independence key that IS the run's issuer. With no issuer key given, any
    ``issuer:`` key counts (a coarse view for summaries; the rules always pass one)."""
    if not key or not key.startswith(ISSUER_ORIGIN_PREFIX):
        return False
    return key == f"{ISSUER_ORIGIN_PREFIX}{issuer_key}" if issuer_key else True


def corroboration_state(
    origin_keys: Iterable[str | None],
    *,
    conflicting: bool = False,
    issuer_key: Any = None,
) -> str | None:
    """The §14.3 state of a claim supported by items with these origins.

    Independence is judged on :func:`independence_key`, so a page that CLAIMS another
    origin counts as that origin (fewer independent origins, never more). ``None`` when
    nothing has an origin — no state is better than an invented one.
    """
    if conflicting:
        return CONFLICTING
    distinct = {k for k in (independence_key(o) for o in origin_keys) if k}
    if not distinct:
        return None
    if all(_is_issuer_key(k, issuer_key) for k in distinct):
        return ISSUER_ONLY
    if len(distinct) >= 2:
        return INDEPENDENTLY_CORROBORATED
    return SINGLE_SOURCE


def corroboration_for_items(
    items: Sequence["SupportItem"], *, issuer_key: Any = None, conflicting: bool = False
) -> str | None:
    """The §14.3 state of a finding's support, from its ITEMS.

    As :func:`corroboration_state`, except that "independently corroborated" counts only
    origins of items whose source class is not weak (aggregators and unknown pages are
    never independent support: two scraper pages are not two sources — review M2).
    """
    if conflicting:
        return CONFLICTING
    keys = {independence_key(i.origin_key) for i in items if i.origin_key}
    keys.discard(None)
    if not keys:
        return None
    if all(_is_issuer_key(k, issuer_key) for k in keys):
        return ISSUER_ONLY
    strong = {
        independence_key(i.origin_key) for i in items
        if i.origin_key and i.source_class not in WEAK_CLASSES
    }
    strong.discard(None)
    if len(strong) >= 2 and any(not _is_issuer_key(k, issuer_key) for k in strong):
        return INDEPENDENTLY_CORROBORATED
    return SINGLE_SOURCE


# ── Support: what stands behind one cited id ────────────────────────────── #


@dataclass(frozen=True)
class SupportItem:
    """One cited evidence id with its class and origin."""

    evidence_id: str
    source_class: str | None = None
    origin_key: str | None = None
    published_at: date | None = None
    #: Open-web document or verified external lead (the rules apply only then).
    web: bool = False
    #: Admitted through a subject row (an article that MENTIONS the company): not the
    #: company's own document, so never the issuer's voice and never a filing for it.
    via_subject: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_class": self.source_class,
            "origin_key": self.origin_key,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "web": self.web,
            "via_subject": self.via_subject,
        }


def class_for_tier(tier: str | None) -> str | None:
    """A source class for NON-web evidence, from the tier the platform stored."""
    from app.services.sources.taxonomy import (
        T1_PRIMARY_COMPANY_SOURCE,
        T1_PRIMARY_FILING,
        T2_REGULATOR_OR_GOV,
        T3_INDUSTRY_SPECIALIST,
        T4_QUALITY_MEDIA,
    )

    return {
        T1_PRIMARY_FILING: SC_ISSUER_FILING,
        T1_PRIMARY_COMPANY_SOURCE: SC_COMPANY_PRESS_RELEASE,
        T2_REGULATOR_OR_GOV: SC_GOVERNMENT_PUBLICATION,
        T3_INDUSTRY_SPECIALIST: SC_SPECIALIST_AGENCY,
        T4_QUALITY_MEDIA: SC_MAJOR_FINANCIAL_PRESS,
    }.get(tier or "")


def platform_origin(
    *, source_class: str | None, company_id: Any, url: str | None
) -> str | None:
    """The origin of NON-web evidence: the issuer for its own material, else the host."""
    if source_class in ISSUER_CLASSES and company_id:
        return f"{ISSUER_ORIGIN_PREFIX}{company_id}"
    return registrable(_host(url))


#: Every unresolvable verified lead shares ONE origin: an id the platform cannot place is
#: not evidence of a second source (review M5).
UNRESOLVED_LEAD_ORIGIN = f"{UNKNOWN_ORIGIN_PREFIX}unresolved-lead"


async def resolve_support(
    session: Any, evidence_ids: Sequence[str], *, company_id: Any = None
) -> list[SupportItem]:
    """Support for ids already stored on findings (the conflict check's other side).

    ``ev:x:`` → the verified lead's stored version; ``ev:<chunk>`` → the chunk's version;
    anything else is a typed platform record (a validated fact, a calculation) — the
    issuer's filing data, never web. **Two queries at most, whatever the number of ids**:
    callers batch every prior finding's ids into one call (review H4).
    """
    from sqlalchemy import select

    from app.models.research_chunk import ResearchDocumentChunk as C
    from app.models.research_document import ResearchDocumentVersion as V
    from app.models.research_lead import ResearchLeadRecord as L
    from app.services.corpus.retrieval import chunk_id_from_evidence_id

    ids = list(dict.fromkeys(str(i) for i in evidence_ids if i))
    chunk_ids = [
        chunk_id_from_evidence_id(i) for i in ids
        if i.startswith("ev:") and not i.startswith("ev:x:")
    ]
    lead_ids = [i for i in ids if i.startswith("ev:x:")]
    rows: dict[str, Any] = {}
    if chunk_ids:
        for row in (
            await session.execute(
                select(
                    C.chunk_id, C.company_id, C.source_tier, C.published_at,
                    V.source_class, V.origin_key, V.canonical_url, V.web_extractor_version,
                )
                .join(V, V.id == C.research_document_version_id)
                .where(C.chunk_id.in_(chunk_ids))
            )
        ).all():
            rows[row[0]] = row
    leads: dict[str, Any] = {}
    if lead_ids:
        for row in (
            await session.execute(
                select(
                    L.promoted_evidence_id, V.source_class, V.origin_key, V.published_at,
                    V.canonical_url,
                )
                .join(V, V.id == L.research_document_version_id)
                .where(L.promoted_evidence_id.in_(lead_ids))
            )
        ).all():
            leads.setdefault(row[0], row)
    out: list[SupportItem] = []
    for evidence_id in ids:
        if evidence_id.startswith("ev:x:"):
            row = leads.get(evidence_id)
            if row is None:
                out.append(SupportItem(evidence_id, None, UNRESOLVED_LEAD_ORIGIN, None, True))
                continue
            origin = row[2] or registrable(_host(row[4])) or UNRESOLVED_LEAD_ORIGIN
            out.append(SupportItem(evidence_id, row[1], origin, row[3], True))
            continue
        if evidence_id.startswith("ev:"):
            row = rows.get(chunk_id_from_evidence_id(evidence_id))
            if row is None:
                out.append(SupportItem(evidence_id=evidence_id))
                continue
            web = row[7] is not None
            klass = row[4] if web else (row[4] or class_for_tier(row[2]))
            origin = row[5] if web else platform_origin(
                source_class=klass, company_id=row[1] or company_id, url=row[6]
            )
            out.append(SupportItem(evidence_id, klass, origin, row[3], web))
            continue
        out.append(
            SupportItem(
                evidence_id=evidence_id,
                source_class=SC_ISSUER_FILING,
                origin_key=f"{ISSUER_ORIGIN_PREFIX}{company_id}" if company_id else None,
            )
        )
    return out


# ── Claim types (spec §13.3) ────────────────────────────────────────────── #

CT_FINANCIAL_STATEMENT = "financial_statement"
CT_GUIDANCE = "guidance"
CT_CORPORATE_EVENT = "corporate_event"
CT_REGULATORY_STATUS = "regulatory_status"
CT_LITIGATION = "litigation"
CT_INDUSTRY_METRIC = "industry_metric"
CT_MARKET_SIZE = "market_size"
CT_SUPERLATIVE = "superlative"
CT_TECHNOLOGY = "technology"
CT_SENTIMENT = "sentiment"
CLAIM_TYPES: frozenset[str] = frozenset(
    {
        CT_FINANCIAL_STATEMENT, CT_GUIDANCE, CT_CORPORATE_EVENT, CT_REGULATORY_STATUS,
        CT_LITIGATION, CT_INDUSTRY_METRIC, CT_MARKET_SIZE, CT_SUPERLATIVE, CT_TECHNOLOGY,
        CT_SENTIMENT,
    }
)

#: Research fields (``research_fields``) that are financial-STATEMENT values: history a
#: filing reports, never guidance.
FINANCIAL_STATEMENT_FIELDS: frozenset[str] = frozenset(
    {
        "metric:revenue", "metric:operating_cash_flow", "metric:net_debt", "metric:cash",
        "metric:capex_period",
    }
)

_FIN_TERMS_RE = re.compile(
    r"\b(?:ebitda|ebit|net (?:income|profit|loss)|operating (?:profit|income|loss)|"
    r"earnings per share|eps|gross (?:profit|margin)|free cash flow|total assets|"
    r"total debt|turnover|sales)\b",
    re.IGNORECASE,
)
_GUIDANCE_RE = re.compile(
    r"\b(?:guidance|guided|outlook|expects?|expected to|targets?|targeting|plans? to|"
    r"aims? to|intends? to|anticipates?|forecasts?|projects?|on track to|"
    r"management (?:says|said|states|believes))\b",
    re.IGNORECASE,
)
_SUPERLATIVE_RE = re.compile(
    r"\b(?:largest|biggest|(?:a|the) leading (?:provider|producer|supplier|player|"
    r"company|manufacturer|developer|operator|maker|firm|vendor|miner|name)|"
    r"market leader|leader in|world'?s (?:first|only|top|"
    r"largest|biggest|leading)|first[- ]ever|number one|no\.\s?1|#1|top (?:three|five|"
    r"ten|\d+)|only (?:company|producer|supplier|player)|best-in-class|dominant|"
    r"most advanced|fastest[- ]growing|lowest[- ]cost|highest[- ]grade|unrivalled|"
    r"unrivaled|unmatched)\b",
    re.IGNORECASE,
)
_MARKET_SIZE_RE = re.compile(
    r"\b(?:market size|addressable market|\btam\b|cagr|compound annual growth|"
    r"market (?:is|was|will be) (?:valued|worth|expected|projected|forecast)|"
    r"market .{0,40}\b(?:reach|grow to|worth)\b)",
    re.IGNORECASE,
)
_LITIGATION_RE = re.compile(
    r"\b(?:lawsuit|litigation|sued|sues|suing|court|class action|arbitration|"
    r"injunction|indict(?:ed|ment)|plaintiffs?|defendants?|legal proceedings|"
    r"settle(?:d|ment) (?:with|of) (?:the )?(?:claim|suit|case))\b",
    re.IGNORECASE,
)
_REGULATORY_RE = re.compile(
    r"\b(?:approv(?:al|ed) by|regulatory approval|marketing authori[sz]ation|"
    r"clearance|cleared by|authori[sz]ed by|permit(?:s|ting)?|licen[cs]e (?:granted|"
    r"approved|application)|environmental (?:impact )?(?:approval|assessment)|"
    r"fda|ema|regulator)\b",
    re.IGNORECASE,
)
_EVENT_RE = re.compile(
    r"\b(?:contract|order|awarded|agreement|acquisition|acquire[sd]?|merger|"
    r"takeover|joint venture|partnership|financing|raised|placement|offering|"
    r"loan facility|factory|plant (?:opened|opens)|signed|offtake|tender|"
    r"commissioned|divest(?:ed|ment)?)\b",
    re.IGNORECASE,
)
_INDUSTRY_RE = re.compile(
    r"\b(?:lead times?|industry|global (?:demand|supply|production|capacity)|"
    r"spot price|prices? (?:of|for|rose|fell)|utili[sz]ation|shipments|"
    r"supply deficit|supply surplus|inventor(?:y|ies))\b",
    re.IGNORECASE,
)
_TECHNOLOGY_RE = re.compile(
    r"\b(?:technology|technologies|process|patent(?:s|ed)?|proprietary|chemistry|"
    r"efficiency|recovery rate|metallurg\w*|yield|throughput)\b",
    re.IGNORECASE,
)
_SENTIMENT_RE = re.compile(
    r"\b(?:sentiment|anecdotal|rumou?r(?:s|ed)?|speculat\w+|reportedly|bullish|"
    r"bearish|investors? (?:are|were) (?:optimistic|pessimistic|worried))\b",
    re.IGNORECASE,
)


def classify_claim(statement: str | None) -> str | None:
    """The §13.3 claim type of a finding statement, or None when no rule applies."""
    text = statement or ""
    if not text.strip():
        return None
    stated = set(rf.fields_stated(text))
    guidance_cue = bool(_GUIDANCE_RE.search(text))
    if _SUPERLATIVE_RE.search(text):
        return CT_SUPERLATIVE
    if _MARKET_SIZE_RE.search(text):
        return CT_MARKET_SIZE
    if not guidance_cue and (
        stated & FINANCIAL_STATEMENT_FIELDS
        or ("metric:capex" in stated and not stated & {
            "metric:capex_project", "metric:capex_sustaining"
        })
        or (_FIN_TERMS_RE.search(text) and rf.has_figure(text))
    ):
        return CT_FINANCIAL_STATEMENT
    if _LITIGATION_RE.search(text):
        return CT_LITIGATION
    if _REGULATORY_RE.search(text):
        return CT_REGULATORY_STATUS
    if guidance_cue or stated & rf.SUPERSEDABLE_FIELDS:
        return CT_GUIDANCE
    if "commercial:offtake" in stated or _EVENT_RE.search(text):
        return CT_CORPORATE_EVENT
    if _INDUSTRY_RE.search(text):
        return CT_INDUSTRY_METRIC
    if _TECHNOLOGY_RE.search(text):
        return CT_TECHNOLOGY
    if _SENTIMENT_RE.search(text):
        return CT_SENTIMENT
    return None


# ── Labels and the rule check ───────────────────────────────────────────── #

LABEL_COMPANY_SAYS = "company says"
LABEL_MANAGEMENT_SAYS = "management says"
LABEL_SINGLE_SOURCE = "single source"
LABEL_SINGLE_SOURCE_ESTIMATE = "single source estimate"
LABEL_SELF_DESCRIBED = "company describes itself as …"
LABEL_ISSUER_TECHNICAL = "issuer technical claim"
LABEL_ANECDOTAL = "anecdotal"
LABEL_PRESS_NOT_FILING = "reported in the press; not from a filing"
#: Stored on a web finding whose value differs from a filing's. Value-free on purpose:
#: the filing's figure lives in the ``conflicting_sources`` gap, so no second money
#: figure rides inside the statement for numeric readers to pick up (review H3).
LABEL_PRESS_FILING_DIFFERS = "reported in the press; a filing figure differs"

#: Labels that mean "this finding is context, not the answer": reconciliation never lets
#: a finding carrying one close a gap (review H3).
NON_CLOSING_LABELS: frozenset[str] = frozenset(
    {LABEL_PRESS_NOT_FILING, LABEL_PRESS_FILING_DIFFERS, LABEL_SELF_DESCRIBED, LABEL_ANECDOTAL}
)


def is_non_closing_label(label: str | None) -> bool:
    """A label that means "context, not the answer". Includes the earlier value-bearing
    relabel ("…; the filing says X") that rows stored before the value-free label carry."""
    return bool(label) and (
        label in NON_CLOSING_LABELS or str(label).startswith("reported in the press")
    )


def label_estimate_by(origin_key: str | None) -> str:
    return f"estimate by {origin_display(origin_key)}"


def label_press_vs_filing(filing_value: str) -> str:
    return f"reported in the press; the filing says {filing_value}"


@dataclass(frozen=True)
class ClaimVerdict:
    """The §13.3 check of one finding."""

    claim_type: str | None
    corroboration: str | None
    meets: bool
    label: str | None = None
    origins: tuple[str, ...] = ()
    classes: tuple[str, ...] = ()
    applies: bool = True
    rules_version: str = TRUST_RULES_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_type": self.claim_type,
            "corroboration": self.corroboration,
            "meets": self.meets,
            "label": self.label,
            "origins": list(self.origins),
            "source_classes": list(self.classes),
            "rules_version": self.rules_version,
        }


NOT_APPLICABLE = ClaimVerdict(None, None, True, applies=False)


def _independent_origins(
    items: Sequence[SupportItem], classes: frozenset[str], *, issuer_key: Any = None
) -> set[str]:
    """Distinct independence keys of items of ``classes`` — never the issuer's, never a
    weak class's, never a key that is only a claim of the issuer."""
    out: set[str] = set()
    for i in items:
        if not i.origin_key or i.source_class not in classes or i.source_class in WEAK_CLASSES:
            continue
        key = independence_key(i.origin_key)
        if key and not key.startswith(ISSUER_ORIGIN_PREFIX):
            out.add(key)
    return out


def assess_claim(
    statement: str | None,
    support: Sequence[SupportItem],
    *,
    conflicting: bool = False,
    issuer_key: Any = None,
) -> ClaimVerdict:
    """Spec §13.3 for one finding. Only applies when the support includes web evidence.

    ``issuer_key`` is the RUN's company: ``issuer:<id>`` is stored document-wide and is
    "mine" only when the id is this run's (review H2). Without one nothing is "mine".
    """
    items = list(support)
    if not any(item.web for item in items):
        return NOT_APPLICABLE
    claim_type = classify_claim(statement)
    origins = tuple(sorted({i.origin_key for i in items if i.origin_key}))
    classes = tuple(sorted({i.source_class for i in items if i.source_class}))
    state = corroboration_for_items(items, issuer_key=issuer_key, conflicting=conflicting)
    present = set(classes)
    # A mention-scope hit (an article NAMING the company) is not the company's voice and
    # not its filing: it never counts as issuer material or fills a filing slot.
    own = [i for i in items if not i.via_subject]
    # The issuer's VOICE: verified (never claimed), this run's, from its own material.
    issuer_present = any(is_verified_issuer(i.origin_key, issuer_key) for i in own)
    verified_filing = any(
        is_verified_issuer(i.origin_key, issuer_key) and i.source_class in FILING_CLASSES
        for i in own
    )
    only_issuer = state == ISSUER_ONLY

    def verdict(meets: bool, label: str | None) -> ClaimVerdict:
        return ClaimVerdict(claim_type, state, meets, label, origins, classes)

    if claim_type is None:
        return verdict(True, None)
    if claim_type == CT_FINANCIAL_STATEMENT:
        # A web value is context only; the filing path is canonical.
        if verified_filing:
            return verdict(True, None)
        return verdict(False, LABEL_PRESS_NOT_FILING)
    if claim_type == CT_GUIDANCE:
        return verdict(True, None) if issuer_present else verdict(False, LABEL_MANAGEMENT_SAYS)
    if claim_type == CT_SUPERLATIVE:
        if len(_independent_origins(items, INDEPENDENT_AUTHORITY_CLASSES)) >= 2:
            return verdict(True, None)
        # Issuer material (or a page CLAIMING the issuer's text) behind it and fewer than
        # two independent T2–T4 origins: the company's own description, never stated as
        # fact.
        if issuer_present or only_issuer:
            return verdict(False, LABEL_SELF_DESCRIBED)
        return verdict(False, LABEL_SINGLE_SOURCE)
    if claim_type == CT_MARKET_SIZE:
        estimator = next(
            (o for o in origins if independence_key(o) and not _is_issuer_key(
                independence_key(o), issuer_key)),
            origins[0] if origins else None,
        )
        acceptable = bool(present & (GOVERNMENT_CLASSES | SPECIALIST_CLASSES
                                     | {SC_RESEARCH_CONSULTANCY}))
        # Always an estimate, with its origin (spec §13.3).
        return verdict(acceptable, label_estimate_by(estimator))
    if claim_type == CT_SENTIMENT:
        return verdict(False, LABEL_ANECDOTAL)
    if claim_type == CT_CORPORATE_EVENT:
        if state == INDEPENDENTLY_CORROBORATED:
            return verdict(True, None)
        if only_issuer:
            return verdict(True, LABEL_COMPANY_SAYS)
        if present & (PRESS_CLASSES | GOVERNMENT_CLASSES):
            return verdict(True, LABEL_SINGLE_SOURCE)
        return verdict(False, LABEL_SINGLE_SOURCE)
    if claim_type == CT_REGULATORY_STATUS:
        if present & REGULATOR_CLASSES:
            return verdict(True, None)
        if issuer_present or only_issuer:
            return verdict(False, LABEL_COMPANY_SAYS)
        return verdict(False, LABEL_SINGLE_SOURCE)
    if claim_type == CT_LITIGATION:
        if present & (REGULATOR_CLASSES | PRESS_CLASSES):
            return verdict(True, None)
        if only_issuer:
            return verdict(False, LABEL_COMPANY_SAYS)
        return verdict(False, LABEL_SINGLE_SOURCE)
    if claim_type == CT_INDUSTRY_METRIC:
        qualifying = _independent_origins(
            items, GOVERNMENT_CLASSES | SPECIALIST_CLASSES | {SC_TRADE_PUBLICATION,
                                                              SC_MAJOR_FINANCIAL_PRESS}
        )
        needed = 2 if rf.has_figure(statement or "") else 1
        if len(qualifying) >= needed:
            return verdict(True, None)
        return verdict(False, LABEL_SINGLE_SOURCE_ESTIMATE)
    if claim_type == CT_TECHNOLOGY:
        if present & SPECIALIST_CLASSES:
            return verdict(True, None)
        if issuer_present:
            return verdict(True, LABEL_ISSUER_TECHNICAL)
        return verdict(False, LABEL_SINGLE_SOURCE)
    return verdict(True, None)


_LABEL_PREFIX_RE = re.compile(
    r"^\[(?:company says|management says|single source(?: estimate)?|"
    r"company describes itself as …|issuer technical claim|anecdotal|estimate by [^\]]{1,120}|"
    r"reported in the press[^\]]{0,200})\] "
)


def label_of_statement(statement: str | None) -> str | None:
    """The W4 label a stored statement carries, or None. The label is the finding's
    structured trust record for readers that only see the statement (review H3)."""
    match = _LABEL_PREFIX_RE.match(statement or "")
    return match.group(0)[1:-2] if match else None


def unlabelled_statement(statement: str | None) -> str:
    """The statement without a W4 label prefix (labels are never stacked)."""
    return _LABEL_PREFIX_RE.sub("", statement or "", count=1)


def labelled_statement(statement: str, label: str | None, *, limit: int = 2000) -> str:
    """The statement as stored: the label first, so it is never read as plain fact."""
    text = unlabelled_statement((statement or "").strip())
    if not label:
        return text[:limit]
    return (f"[{label}] " + text)[:limit]


# ── Contradictions (spec §14.4) and web facts (§17.3) ───────────────────── #


@dataclass(frozen=True)
class ClaimSide:
    """One side of a possible contradiction: a finding with its value and support."""

    finding_id: str
    statement: str
    fields: tuple[str, ...]
    period: str | None = None
    scope_key: str | None = None
    support: tuple[SupportItem, ...] = ()
    published_at: date | None = None

    @property
    def web(self) -> bool:
        return any(item.web for item in self.support)

    @property
    def origins(self) -> frozenset[str]:
        return frozenset(i.origin_key for i in self.support if i.origin_key)

    @property
    def publishers(self) -> frozenset[str]:
        """The platform-verified publishers behind the support. Two findings from one
        publisher are one voice; a page's CLAIMED origin never makes two voices one
        (review F1: a forged "(Reuters)" must not hide a real disagreement)."""
        return frozenset(
            p for p in (publisher_of(i.origin_key) for i in self.support) if p
        )

    @property
    def classes(self) -> frozenset[str]:
        return frozenset(i.source_class for i in self.support if i.source_class)

    def value_text(self, field_key: str) -> str | None:
        """The amount as the statement WROTE it (``field_clause`` lower-cases)."""
        clause = rf.field_clause(self.statement, field_key) or ""
        start = self.statement.lower().find(clause) if clause else -1
        region = self.statement[start: start + len(clause)] if start >= 0 else self.statement
        match = rf.MONEY_RE.search(region) or rf.MONEY_SUFFIX_RE.search(region)
        return match.group(0).strip() if match else None


@dataclass(frozen=True)
class Contradiction:
    field_key: str
    sides: tuple[ClaimSide, ClaimSide]
    #: The finding whose value is canonical — only ever a financial-statement value
    #: from the filing path (P4). None: no automatic winner.
    canonical_finding_id: str | None = None

    def ordered_sides(self) -> list[ClaimSide]:
        """Display order: regulator > issuer > press, then newer first (presentation)."""

        def rank(side: ClaimSide) -> tuple[int, int]:
            if side.classes & REGULATOR_CLASSES:
                tier = 0
            elif side.classes & ISSUER_CLASSES or any(is_issuer_origin(o) for o in side.origins):
                tier = 1
            else:
                tier = 2
            stamp = side.published_at.toordinal() if side.published_at else 0
            return (tier, -stamp)

        return sorted(self.sides, key=rank)

    def describe(self) -> str:
        label = rf.label_of(self.field_key)
        rows = []
        for side in self.ordered_sides():
            classes = ", ".join(sorted(side.classes)) or "unclassified"
            origins = ", ".join(origin_display(o) for o in sorted(side.origins)) or "unknown"
            when = side.published_at.isoformat() if side.published_at else "undated"
            value = side.value_text(self.field_key) or "a different value"
            rows.append(
                f"{value} (finding {side.finding_id[:8]}; {classes}; origin {origins}; {when})"
            )
        period = next((s.period for s in self.sides if s.period), None)
        head = f"Sources disagree on {label}" + (f" ({period})" if period else "")
        tail = (
            " The filing value is canonical; the web value is kept as context "
            "(web_reported_value)."
            if self.canonical_finding_id
            else " No automatic winner: both are retained."
        )
        return f"{head}: " + " vs ".join(rows) + "." + tail


def _money_disagree(a: ClaimSide, b: ClaimSide, field_key: str) -> bool:
    from app.services.pipeline.gap_reconciliation import DIFFERENT, _Value, compare_values

    va = rf.money_values(rf.field_clause(a.statement, field_key) or a.statement)
    vb = rf.money_values(rf.field_clause(b.statement, field_key) or b.statement)
    if not va or not vb:
        return False
    return (
        compare_values(_Value("money", tuple(sorted(va))), _Value("money", tuple(sorted(vb))))
        == DIFFERENT
    )


def _same_frame(a: ClaimSide, b: ClaimSide) -> bool:
    pa = rf.normalise_period(a.period) or rf.statement_period(a.statement)
    pb = rf.normalise_period(b.period) or rf.statement_period(b.statement)
    if not pa or not pb or pa != pb:
        return False
    return (a.scope_key or None) == (b.scope_key or None)


def find_contradictions(new: ClaimSide, existing: Sequence[ClaimSide]) -> list[Contradiction]:
    """Findings that state the same money field for the same period and scope, from
    DIFFERENT origins, with different values. Supersedable (guidance) fields are
    excluded — a newer guidance figure supersedes, it does not contradict (track B)."""
    out: list[Contradiction] = []
    for other in existing:
        if other.finding_id == new.finding_id:
            continue
        shared = [
            f for f in sorted(set(new.fields) & set(other.fields))
            if f not in rf.SUPERSEDABLE_FIELDS
            and rf.FIELDS_BY_KEY.get(f) is not None
            and rf.FIELDS_BY_KEY[f].needs == rf.NEEDS_MONEY
        ]
        if not shared or not _same_frame(new, other):
            continue
        if new.publishers and other.publishers and new.publishers & other.publishers:
            continue
        for field_key in shared:
            if not _money_disagree(new, other, field_key):
                continue
            canonical = None
            if field_key in FINANCIAL_STATEMENT_FIELDS or rf.family_of(field_key) == "metric:capex":
                # P4: the filing path is canonical; a web value never is.
                if new.web and not other.web:
                    canonical = other.finding_id
                elif other.web and not new.web:
                    canonical = new.finding_id
            out.append(Contradiction(field_key, (other, new), canonical))
            break
    return out


WEB_FACT_EVENT = "event"
WEB_FACT_ESTIMATE = "estimate"
WEB_FACT_REPORTED_VALUE = "web_reported_value"


@dataclass(frozen=True)
class WebFact:
    """Spec §17.3: what a web-supported finding becomes as a structured fact.

    Never canonical. A financial-statement value is ``web_reported_value`` context.
    """

    kind: str
    claim_type: str
    fields: tuple[str, ...]
    source_classes: tuple[str, ...]
    corroboration: str | None
    origins: tuple[str, ...]
    fact_origin: str = "web"
    canonical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "claim_type": self.claim_type,
            "fields": list(self.fields),
            "source_classes": list(self.source_classes),
            "corroboration": self.corroboration,
            "origins": list(self.origins),
            "fact_origin": self.fact_origin,
            "canonical": self.canonical,
        }


def web_fact_for(statement: str, verdict: ClaimVerdict) -> WebFact | None:
    """The §17.3 web fact a finding yields, or None (not web, or no structured kind)."""
    if not verdict.applies or verdict.claim_type is None:
        return None
    if verdict.claim_type == CT_FINANCIAL_STATEMENT:
        if verdict.meets:
            return None  # a filing stands behind it: the canonical path, not a web fact
        kind = WEB_FACT_REPORTED_VALUE
    elif verdict.claim_type in (CT_MARKET_SIZE, CT_INDUSTRY_METRIC):
        kind = WEB_FACT_ESTIMATE
    elif verdict.claim_type in (CT_CORPORATE_EVENT, CT_REGULATORY_STATUS):
        kind = WEB_FACT_EVENT
    else:
        return None
    return WebFact(
        kind=kind,
        claim_type=verdict.claim_type,
        fields=tuple(sorted(rf.fields_stated(statement))),
        source_classes=verdict.classes,
        corroboration=verdict.corroboration,
        origins=verdict.origins,
    )


@dataclass
class TrustOutcome:
    """What the ledger did with one finding's trust check (for the audit log)."""

    verdict: ClaimVerdict
    stored_statement: str
    web_fact: WebFact | None = None
    contradictions: list[str] = field(default_factory=list)
    conflict_gap_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.verdict.to_dict(),
            "web_fact": self.web_fact.to_dict() if self.web_fact else None,
            "contradictions": list(self.contradictions),
            "conflict_gap_ids": list(self.conflict_gap_ids),
        }


__all__ = [
    "origin_display",
    "LABEL_PRESS_FILING_DIFFERS",
    "NON_CLOSING_LABELS",
    "UNRESOLVED_LEAD_ORIGIN",
    "BRAND_ALIASES",
    "label_of_statement",
    "corroboration_for_items",
    "is_verified_issuer",
    "claimed_body",
    "is_claimed_origin",
    "make_claimed",
    "publisher_of",
    "independence_key",
    "CLAIM_TYPES",
    "CONFLICTING",
    "CORROBORATION_STATES",
    "FINANCIAL_STATEMENT_FIELDS",
    "INDEPENDENTLY_CORROBORATED",
    "ISSUER_ONLY",
    "PR_WIRE_HOSTS",
    "PUBLISHER_GROUPS",
    "PUBLISHER_GROUPS_VERSION",
    "SINGLE_SOURCE",
    "TRUST_RULES_VERSION",
    "WIRE_SERVICES",
    "ClaimSide",
    "ClaimVerdict",
    "Contradiction",
    "IssuerIdentity",
    "OriginDecision",
    "OriginInput",
    "SupportItem",
    "TrustOutcome",
    "WebFact",
    "assess_claim",
    "assign_origins",
    "boilerplate_company",
    "class_for_tier",
    "document_origin",
    "classify_claim",
    "corroboration_state",
    "find_contradictions",
    "group_origin",
    "is_issuer_origin",
    "is_non_closing_label",
    "issuer_from_candidates",
    "labelled_statement",
    "origin_for",
    "platform_origin",
    "resolve_support",
    "source_line",
    "summarise_origins",
    "unlabelled_statement",
    "web_fact_for",
    "wire_attribution",
]
