"""Passage-level subject attribution: the implicit-subject entity firewall (ADR-083).

WHY A PASSAGE, NOT A STATEMENT
==============================
The investigator's earlier firewalls (ADR-066, ADR-074, ADR-079) fire when the
STATEMENT names another company or when the PUBLISHER is another company. They are blind
to a writer who leaves the subject implicit ("The company…", "It generated…", "Capital
expenditure was…") about a passage whose subject is another entity. The cross-sector QA
campaign (2026-11) found that class on four issuers:

* a former subsidiary's risk factors inside the subject's own annual report, filed as the
  subject's counter-thesis;
* an offtaker's unit costs, from the offtaker's own page, answering the subject's
  unit-cost question;
* a regulated subsidiary's capital assessment stated as the Group's;
* a mine's capex, under a mine heading the excerpt does not repeat, stated as Group capex.

So this module reads the CITED PASSAGE, not the model's sentence: which sentence of the
passage the statement rests on, and whose that sentence is.

WHAT IT RETURNS, PER PASSAGE
============================
* the document owner (from the evidence's own metadata: the run's own filing, the
  subject's verified site, another company's own site, or unknown);
* the entities the passage names: the subject (name words, ticker, its own legal name),
  organisation names carrying a legal suffix (Ltd, Limited, plc, Inc, Corp, SE, AG, SA,
  NV, LLC, GmbH, Pty, SpA, N.A., Co), the platform's registered companies, separately
  registered namesakes, an acronym with its spelled-out expansion, and the aliases a
  passage defines for them ("J.P. Morgan SE (“JPMSE” or “the Bank”)");
* the matched sentence: the one sharing most figures and words with the statement (the
  statement's own entity words are not counted, so the writer's attribution never
  steers the match);
* the matched sentence's resolved subject: an explicit name, or "It / Its / the company /
  the Company / the group / This entity / We / Our" resolved to the nearest name in the
  sentence, the previous sentence's subject, the passage topic, the governing heading
  or the document owner;
* the relationship of that entity to the research subject, from a closed vocabulary
  (``RELATIONSHIPS``) and fixed cue tables;
* a reporting scope when the passage sits under a mine / segment heading in capitals
  ("CENTINELA") or belongs to a subsidiary or joint venture.

THE DIRECTION OF FAILURE
========================
Nothing here is a model judgement. Every rule is lexical and deterministic, and every
unresolved case is ``UNKNOWN``, which the caller keeps exactly as before. A drop needs
every cited passage to resolve, positively, to a NAMED other entity. In the subject's
own document only three things are another entity's: a former subsidiary's sentence, a
separately reporting namesake's, and a named entity's own reported results; a sentence
naming no one there is the subject's, whoever was named before it (PR #320 review).
"""

from __future__ import annotations

import bisect
import functools
import itertools
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

# ── Relationship vocabulary (closed) ─────────────────────────────────────── #

SUBJECT = "SUBJECT"
SUBSIDIARY = "SUBSIDIARY"
FORMER_SUBSIDIARY = "FORMER_SUBSIDIARY"
JOINT_VENTURE = "JOINT_VENTURE"
COUNTERPARTY = "COUNTERPARTY"
CUSTOMER = "CUSTOMER"
SUPPLIER = "SUPPLIER"
PEER = "PEER"
REGULATOR = "REGULATOR"
OTHER_ISSUER = "OTHER_ISSUER"
UNKNOWN = "UNKNOWN"

RELATIONSHIPS: frozenset[str] = frozenset(
    {
        SUBJECT, SUBSIDIARY, FORMER_SUBSIDIARY, JOINT_VENTURE, COUNTERPARTY, CUSTOMER,
        SUPPLIER, PEER, REGULATOR, OTHER_ISSUER, UNKNOWN,
    }
)
#: The subject's own perimeter, at a narrower scope: kept, but never read as the Group.
SCOPED_RELATIONSHIPS: frozenset[str] = frozenset({SUBSIDIARY, JOINT_VENTURE})
#: Another entity's fact. A former subsidiary is never the subject's current fact.
OTHER_ENTITY_RELATIONSHIPS: frozenset[str] = frozenset(
    {FORMER_SUBSIDIARY, COUNTERPARTY, CUSTOMER, SUPPLIER, PEER, REGULATOR, OTHER_ISSUER}
)
#: Roles described RELATIVE to the document's subject ("our offtaker", "a lender to the
#: Company"). Such an entity never becomes the topic that an unnamed later sentence
#: inherits: in the subject's annual report the sentence after "Iluka is our offtaker"
#: is still about the subject.
_RELATIONAL: frozenset[str] = frozenset({COUNTERPARTY, CUSTOMER, SUPPLIER, PEER, REGULATOR})

#: Human-readable relationship, for a gap. Platform vocabulary only.
RELATIONSHIP_LABELS: dict[str, str] = {
    SUBSIDIARY: "subsidiary",
    FORMER_SUBSIDIARY: "former subsidiary",
    JOINT_VENTURE: "joint venture",
    COUNTERPARTY: "counterparty",
    CUSTOMER: "customer",
    SUPPLIER: "supplier",
    PEER: "peer",
    REGULATOR: "regulator",
    OTHER_ISSUER: "another company",
    UNKNOWN: "unknown relationship",
    SUBJECT: "the issuer",
}

# ── Document owner ───────────────────────────────────────────────────────── #

OWNER_SUBJECT = "subject"
OWNER_OTHER = "other"
OWNER_UNKNOWN = "unknown"

# ── Verdicts ─────────────────────────────────────────────────────────────── #

#: The passage supports a statement about the subject (Group or unscoped).
VERDICT_SUBJECT = "subject"
#: The subject's fact at a narrower scope: a subsidiary, a joint venture, a mine/segment.
VERDICT_SCOPED = "scoped"
#: Another named entity's fact.
VERDICT_OTHER = "other"
#: The passage does not say whose it is. Kept: today's behaviour.
VERDICT_UNKNOWN = "unknown"

#: How long an entity name from a page may be when it is named in a gap.
NAME_MAX = 60


@dataclass(frozen=True)
class SubjectIdentity:
    """The research subject, and the entities the platform already knows by name."""

    name: str | None
    ticker: str | None = None
    #: Names of OTHER companies the platform holds (its registered-company table).
    known_entities: tuple[str, ...] = ()
    #: Separately reporting registrants that carry the subject's name (ADR-066):
    #: (folded phrase, full name), e.g. ("honeywell aerospace", "Honeywell Aerospace Inc.").
    separate_registrants: tuple[tuple[str, str], ...] = ()
    #: Namesake registrants that file WITH the subject (a consolidated subsidiary with its
    #: own registered debt): the subject's perimeter, at a narrower scope.
    family_registrants: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Passage:
    """One cited evidence item, reduced to what attribution reads. Plain data."""

    citation_id: str
    text: str = ""
    heading: str | None = None
    owner: str = OWNER_UNKNOWN
    owner_name: str | None = None
    #: False for a typed platform record (a fact, a statement line): no prose to read,
    #: so the owner alone decides.
    analysable: bool = True
    #: The verified publisher host of a web page that may be a company's own site; an
    #: entity the passage names whose name this host stands for becomes the owner.
    company_site_host: str | None = None


@dataclass(frozen=True)
class PassageAttribution:
    citation_id: str
    owner: str
    owner_name: str | None
    #: Every entity the passage names (display names, capped), the subject excluded.
    entities: tuple[str, ...]
    #: Index of the matched sentence in the passage's segments; None when none matched.
    matched_sentence: int | None
    #: Whose the matched sentence is: the entity's name (the subject's name for SUBJECT),
    #: None when unresolved.
    subject_entity: str | None
    relationship: str
    #: A narrower reporting scope (a subsidiary, a JV, a mine/segment heading).
    scope_name: str | None = None
    #: The sentence is about another entity, but framed by its relationship TO the
    #: subject in the subject's own context ("Vexmoor, our largest customer, accounted
    #: for 40% of revenue"; offtake or facility terms): the subject's fact. The
    #: relationship stays recorded (CUSTOMER, COUNTERPARTY…); the verdict is subject.
    framed: bool = False
    #: The sentence's grammatical subject is another entity, but nothing makes it that
    #: entity's own fact: in the subject's own document only a former subsidiary, a
    #: separately reporting namesake or the entity's own reported results are; on a
    #: page that does not name the subject, a transaction with an unnamed counterparty
    #: is not either. The relationship stays recorded; the verdict is unknown (kept).
    contained: bool = False

    @property
    def verdict(self) -> str:
        if self.framed:
            return VERDICT_SUBJECT
        if self.relationship == SUBJECT:
            return VERDICT_SCOPED if self.scope_name else VERDICT_SUBJECT
        if self.relationship in SCOPED_RELATIONSHIPS:
            return VERDICT_SCOPED
        if (
            self.relationship in OTHER_ENTITY_RELATIONSHIPS
            and self.subject_entity
            and not self.contained
        ):
            return VERDICT_OTHER
        return VERDICT_UNKNOWN


@dataclass(frozen=True)
class StatementAttribution:
    verdict: str
    passages: tuple[PassageAttribution, ...] = ()
    #: For ``scoped``: the one scope every scoped passage agrees on.
    scope_name: str | None = None
    #: For ``other``: (entity name, relationship) pairs, for a gap.
    others: tuple[tuple[str, str], ...] = ()
    #: Scoped passages that name DIFFERENT scopes: no one scope can be stamped, so the
    #: verdict is ``unknown`` and the statement is kept unscoped.
    conflict: bool = False

    @property
    def scope_key(self) -> str | None:
        """``segment:<casefolded name>`` — ``fact_scope``'s own key for a named non-Group
        scope, so every consumer that keeps segments out of Group slots does so here."""
        if not self.scope_name:
            return None
        from app.services.sources.fact_scope import SCOPE_TYPE_SEGMENT, FactScope

        return FactScope(scope_type=SCOPE_TYPE_SEGMENT, scope_name=self.scope_name).scope_key


# ── Passage text from an evidence item ───────────────────────────────────── #

_TEXT_KEYS: tuple[str, ...] = ("text", "source_excerpt", "excerpt", "content")
_HEADING_KEYS: tuple[str, ...] = ("section", "section_path", "heading", "section_title")
_JSON_STRING_RE = r'"{key}"\s*:\s*"((?:[^"\\]|\\.){{0,6000}})'


def _json_string(raw: str, key: str) -> str | None:
    """A string field read out of a (possibly truncated) JSON object."""
    match = re.search(_JSON_STRING_RE.format(key=re.escape(key)), raw)
    if not match:
        return None
    body = match.group(1)
    try:
        return str(json.loads(f'"{body}"'))
    except ValueError:
        # A cut mid-escape: drop the trailing partial escape and read what is left.
        body = re.sub(r"\\u?[0-9a-fA-F]{0,3}$|\\$", "", body)
        try:
            return str(json.loads(f'"{body}"'))
        except ValueError:
            return body


def passage_text(serialised: str | None) -> tuple[str, str | None]:
    """(prose, heading) of an evidence item as the investigator serialised it.

    Evidence text is a JSON object cut at 1,500 characters, so it is often not valid
    JSON. The prose and heading fields are read by key either way; anything else in
    the object (ids, URLs, scores) is never read as prose.
    """
    raw = serialised or ""
    text: str | None = None
    heading: str | None = None
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, dict):
        text = next((str(data[k]) for k in _TEXT_KEYS if isinstance(data.get(k), str)), None)
        heading = next(
            (str(data[k]) for k in _HEADING_KEYS if isinstance(data.get(k), str)), None
        )
    elif data is None:
        text = next((v for k in _TEXT_KEYS if (v := _json_string(raw, k))), None)
        heading = next((v for k in _HEADING_KEYS if (v := _json_string(raw, k))), None)
    return (text or "").strip(), (heading or "").strip() or None


def document_owner(
    *,
    web: bool,
    own_company: bool,
    via_subject: bool,
    verified_issuer_origin: bool,
    publisher_host: str | None,
    edgar_registrant: str | None,
    subject_name: str | None,
) -> str:
    """Who owns the document, from the evidence's own metadata (never its prose).

    * a web copy of an EDGAR filing: ADR-079's registrant verdict;
    * platform (non-web) text in the subject's own corpus, not admitted through a
      mention row: the subject's;
    * web text from the run's verified issuer origin, or served from the subject's own
      domain (``identity.issuer_domain_matches``, by equality): the subject's;
    * anything else: unknown (another company's own site is named later, from the
      entities the passage itself names).
    """
    if edgar_registrant == "own":
        return OWNER_SUBJECT
    if edgar_registrant == "other":
        return OWNER_OTHER
    if not web:
        return OWNER_SUBJECT if own_company and not via_subject else OWNER_UNKNOWN
    if verified_issuer_origin:
        return OWNER_SUBJECT
    if publisher_host and subject_name:
        from app.services.discovery.identity import issuer_domain_matches

        if issuer_domain_matches(publisher_host, subject_name):
            return OWNER_SUBJECT
    return OWNER_UNKNOWN


# ── Lexicon ──────────────────────────────────────────────────────────────── #

_LEGAL_SUFFIX = (
    r"Ltd|LTD|Limited|LIMITED|plc|PLC|Plc|Inc|INC|Incorporated|Corp|CORP|Corporation|"
    r"CORPORATION|S\.A|N\.V|LLC|GmbH|Pty|PTY|SpA|S\.p\.A|ASA|Oyj|B\.V|N\.A"
)
#: Two-letter forms double as US state / country codes ("Elko Springs, NV",
#: "Ridgeway, CO"): they only count without a comma ("Brindle AG", "Ostara Electronics Co").
_SHORT_SUFFIX = r"SE|AG|SA|NV|BV|Co|CO"
_SUFFIX_RE = re.compile(
    rf"(?:(?:,[ \t]{{0,3}}|[ \t]{{1,3}})(?:{_LEGAL_SUFFIX})|[ \t]{{1,3}}(?:{_SHORT_SUFFIX}))"
    r"(?:[ \t]{1,3}(?:Ltd|LTD|Limited|LIMITED))?\.?(?![\w-])"
)
#: A legal form a subsidiary typically carries and a parent of another form does not
#: ("Calderon AG" under "Calderon plc"): a namesake in that form is the perimeter, not the
#: subject. "N.A." (the operating bank) and plc/Limited pairs (dual listings) are not.
_SUBSIDIARY_FORMS: frozenset[str] = frozenset(
    {"ag", "se", "gmbh", "llc", "bv", "nv", "spa", "sa", "sas", "pty"}
)
_FORM_CLASSES: dict[str, str] = {
    "inc": "us", "incorporated": "us", "corp": "us", "corporation": "us", "co": "us",
    "company": "us", "ltd": "ltd", "limited": "ltd", "plc": "plc", "sa": "sa", "sas": "sa",
    "ag": "ag", "se": "se", "gmbh": "gmbh", "llc": "llc", "bv": "bv", "nv": "nv",
    "spa": "spa", "pty": "ltd", "na": "na", "asa": "asa", "oyj": "oyj",
}
#: Hard cap on the prose one passage reader scans (evidence is cut at 1,500 chars;
#: anything past this is never the matched sentence of a bounded excerpt).
_MAX_PASSAGE_CHARS = 6000
#: A "word" longer than this is not a name, a heading word or an alias ("A-A-A-…").
_MAX_TOKEN_CHARS = 60
#: At most this many legal-form names are read from one passage: a 6,000-character
#: excerpt naming more is a list, not prose, and nothing in it is dropped for want of
#: the rest (fail-safe: fewer entities means more UNKNOWN, never a drop).
_MAX_SUFFIX_NAMES = 200
#: A name token: capitalised word, digit-led word ("3M"), dotted initials, or "&".
_NAME_TOKEN_RE = re.compile(r"(?:[A-Z0-9À-Þ][\w&'’.\-]*|&)")
_NAME_CONNECTORS: frozenset[str] = frozenset(
    {"&", "de", "du", "des", "la", "le", "del", "y", "et", "von", "van", "der"}
)
#: Words that end a name when walking back from a legal suffix: function words and the
#: role nouns that introduce a name ("rival Kestrel32 Ltd", "Meanwhile Kestrel32 Ltd").
_NAME_STOP: frozenset[str] = frozenset(
    {
        "the", "a", "an", "our", "its", "their", "his", "her", "in", "on", "at", "for", "by",
        "from", "with", "to", "of", "and", "or", "as", "per", "via", "that", "which", "who",
        "while", "meanwhile", "according", "said", "says", "rival", "rivals", "peer", "peers",
        "competitor", "competitors", "customer", "customers", "client", "supplier",
        "partner", "lender", "offtaker", "subsidiary", "parent", "buyer", "seller",
        "former", "formerly", "between", "including", "include", "includes", "like",
        "both", "also", "but", "if", "when", "after", "before", "since", "during", "under",
        "is", "was", "are", "were", "has", "had", "have", "will", "would", "may", "near",
    }
)
#: Corporate words never used as an entity's short alias ("Compañía Minera Zaldívar
#: SpA" is "Zaldívar" in later sentences, not "Compañía").
_ALIAS_SKIP: frozenset[str] = frozenset(
    {
        "compania", "sociedad", "minera", "mineracao", "banco", "banque", "groupe",
        "gruppo", "company", "corporation", "holdings", "holding", "international",
        "the", "societe", "societa", "empresa", "industrias",
    }
)
#: Defined terms a passage can bind to one entity ("…(the “Bank”)").
_DEFINED_TERMS: frozenset[str] = frozenset(
    {
        "company", "group", "issuer", "bank", "entity", "consolidated entity", "business",
        "firm", "corporation",
    }
)
#: Defined terms that name the filer itself in its own document.
_SELF_TERMS: frozenset[str] = frozenset({"company", "group", "issuer", "firm", "corporation"})

_IMPLICIT_RE = re.compile(
    r"\b(?:(?P<first>[Ww]e|[Oo]urs?|us)"
    r"|(?P<pron>[Ii]ts?)"
    r"|(?P<this>[Tt]his\s+(?:entity|company|business))"
    r"|[Tt]he\s+(?P<term>[Cc]onsolidated\s+[Ee]ntity|[Cc]ompany|[Gg]roup|[Ii]ssuer|[Bb]ank|"
    r"[Bb]usiness|[Ee]ntity|[Ff]irm|[Cc]orporation)"
    r"|(?P<bare>Group|Firm(?:wide)?))\b"
)
#: An internal hint: the subject's own name in a legal form its name does not carry.
_FORM_NAMESAKE = "FORM_NAMESAKE"
_FIRST = "first_person"
_PRONOUN = "pronoun"
_DEFINED = "defined"
_DEFINITE = "definite"

_ROLE_NOUN = (
    r"(?:former\s+)?(?:(?:wholly|majority|minority|partly)[- ]owned\s+|"
    r"\d{1,3}(?:\.\d{1,2})?\s*%[- ]owned\s+)?"
    r"(?:subsidiar(?:y|ies)|parent(?:\s+company)?|customers?|clients?|suppliers?|"
    r"off-?takers?|lenders?|partners?|joint[- ]venture(?:\s+partners?)?|JV(?:\s+partners?)?|"
    r"competitors?|peers?|rivals?|regulators?|licensees?|licensors?|distributors?|"
    r"contractors?|buyers?|purchasers?)"
)
#: "our former subsidiary", "the Company's largest customer", "Acme's offtaker".
_POSSESSIVE_ROLE_RE = re.compile(
    r"(?:['’]s)?[ \t]{1,3}(?:[\w-]{1,20}[ \t]{1,3}){0,2}?" + _ROLE_NOUN + r"\b",
    re.IGNORECASE,
)
#: "a subsidiary of <X>", "lender to the Company": X is the anchor, not the subject.
_ROLE_OF_RE = re.compile(
    _ROLE_NOUN + r"[ \t]+(?:of|to|for|with|from)[ \t]+(?:the[ \t]+)?$", re.IGNORECASE
)

#: Relationship cues, nearest wins; a FORMER cue swallows the subsidiary cue inside it.
_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        FORMER_SUBSIDIARY,
        re.compile(
            r"\bformer(?:ly)?\b[\w \t-]{0,30}?\bsubsidiar(?:y|ies)\b|\bspun[- ]off\b|"
            r"\bspin[- ]?off\b|\bdeconsolidat\w*|\bdivest\w*|"
            r"\bsold[ \t]+(?:its|our|the)[ \t]+(?:entire[ \t]+|remaining[ \t]+)?"
            r"(?:stake|interest|shareholding|holding)\b",
            re.IGNORECASE,
        ),
    ),
    (COUNTERPARTY, re.compile(r"\b(?:joint[- ]venture|JV)[ \t]+partners?\b", re.IGNORECASE)),
    (
        JOINT_VENTURE,
        re.compile(
            r"\bjoint[- ]ventures?\b|\bJV\b|"
            r"\b\d{1,3}(?:\.\d{1,2})?[ \t]*%[ \t]+(?:equity[ \t]+)?(?:interest|stake)[ \t]+in\b|"
            r"\b\d{1,3}:\d{1,3}[ \t]+joint\b",
            re.IGNORECASE,
        ),
    ),
    (
        SUBSIDIARY,
        re.compile(
            r"(?<!former )(?<!its )(?<!and )(?<!their )\bsubsidiar(?:y|ies)\b|"
            r"\b(?:wholly|majority)[- ]owned\b|\boperating[ \t]+(?:company|subsidiary)\b",
            re.IGNORECASE,
        ),
    ),
    (CUSTOMER, re.compile(r"\bcustomers?\b|\bclients?\b|\bbuyers?\b|\bpurchasers?\b", re.I)),
    (
        SUPPLIER,
        re.compile(r"\bsuppliers?\b|\bvendors?\b|\bcontractors?\b|\bdistributors?\b", re.I),
    ),
    (
        COUNTERPARTY,
        re.compile(
            r"\boff-?take\w*|\blenders?\b|\bloan\b|\bcredit[ \t]+facility\b|\bfinancier\b|"
            r"\bpartners?(?:hip)?\b|\bcollaborat\w*|\blicens(?:ee|or)s?\b|"
            r"\bagreement[ \t]+with\b|\broyalty\b",
            re.IGNORECASE,
        ),
    ),
    (
        REGULATOR,
        re.compile(
            r"\bregulators?\b|\b(?:supervisory|regulatory)[ \t]+authority\b", re.IGNORECASE
        ),
    ),
    (
        PEER,
        re.compile(
            r"\bpeers?\b|\bcompetitors?\b|\brivals?\b|\bcompetes?\b|\bcomparables?\b", re.I
        ),
    ),
)
_CUE_WINDOW = 90

#: A word before which a name is an object, not the clause's subject.
_PREPOSITIONS: frozenset[str] = frozenset(
    {
        "from", "with", "to", "by", "for", "of", "at", "under", "between", "among",
        "against", "via", "per", "versus", "vs", "than", "toward", "towards", "into",
        "onto", "on", "in", "over", "about", "including", "like", "and", "or", "nor",
        "near",
    }
)
#: How far into a sentence its grammatical subject may begin.
_SUBJECT_WINDOW_WORDS = 10

#: Words of a capitals heading that make it a structural heading, not a mine, project
#: or segment name.
_GENERIC_HEADING_WORDS: frozenset[str] = frozenset(
    {
        "risk", "risks", "factors", "financial", "review", "group", "consolidated",
        "overview", "highlights", "capital", "expenditure", "revenue", "revenues", "notes",
        "note", "statement", "statements", "income", "balance", "sheet", "cash", "flow",
        "flows", "operations", "operating", "operational", "results", "summary", "outlook",
        "guidance", "production", "sales", "costs", "cost", "table", "contents", "annual",
        "report", "quarterly", "activities", "chairman", "chair", "letter", "strategy",
        "governance", "directors", "director", "sustainability", "safety", "health",
        "environment", "environmental", "people", "management", "discussion", "analysis",
        "part", "item", "section", "business", "properties", "legal", "proceedings",
        "market", "markets", "segment", "segments", "information", "key", "performance",
        "indicators", "exploration", "development", "projects", "project", "mining",
        "division", "transport", "corporate", "other", "total", "and", "of", "the", "for",
        "in", "to", "a", "an", "on", "at", "by", "with", "principal", "uncertainties",
        "forward-looking", "disclaimer", "important", "notice", "introduction", "appendix",
        "glossary", "definitions", "unaudited", "audited", "year", "half", "quarter", "full",
        "finance", "financing", "dividend", "dividends", "net", "debt", "earnings",
        "profit", "loss", "tax", "taxation", "accounting", "policies", "auditor",
        "independent", "remuneration", "committee", "board", "shareholder", "shareholders",
        "contact", "details", "update", "news", "release", "press", "announcement",
        "reviews", "new", "standards", "changes", "events", "subsequent", "after",
        "reporting", "period", "contingent", "liabilities", "commitments", "related",
        "party", "parties", "transactions", "employees", "workforce", "labour", "labor",
        "water", "energy", "climate", "change", "reserves", "resources", "mineral", "ore",
        "liquidity", "adequacy", "regulatory", "requirements", "copper", "gold", "silver",
        "molybdenum", "by-products", "products", "product", "services", "service",
        "industry", "competition", "overview", "description", "general", "selected", "data",
        "material", "quantitative", "qualitative", "disclosures", "controls", "procedures",
        "executive", "officers", "compensation", "security", "ownership", "exhibits",
        "signatures", "index", "page", "continued", "background", "purpose", "scope",
        "approach", "framework", "process", "internal", "external", "assessment",
        # Note / topic headings: a line item, not a mine, project or segment.
        "goodwill", "intangible", "intangibles", "borrowings", "provisions",
        "contingencies", "own", "funds", "leases", "impairment", "pensions", "pension",
        "derivatives", "inventories", "receivables", "payables", "litigation", "matters",
        "cybersecurity", "equity", "assets", "instruments", "hedging", "share",
    }
)
#: Financial-term words: an acronym spelled out in them is a measure, not a company.
_TERM_WORDS: frozenset[str] = frozenset(
    {
        "earnings", "interest", "taxes", "depreciation", "amortisation", "amortization",
        "return", "equity", "assets", "ratio", "value", "rate", "share", "price",
        "adequacy", "liquidity", "tier", "coverage", "requirement", "accepted",
        "principles", "generally", "international", "social", "common", "weighted",
        "average", "annual", "compound", "growth", "key", "indicator", "free", "adjusted",
        "basic", "diluted", "per", "loss", "given", "default", "exposure", "probability",
        "stress", "testing", "test", "supervisory", "evaluation", "recovery", "plan",
    }
)
#: Legal-form words: a capitals line carrying one is a company's name, not a scope.
_LEGAL_WORDS: frozenset[str] = frozenset(
    {
        "ltd", "limited", "plc", "inc", "incorporated", "corp", "corporation", "sa", "s.a",
        "se", "ag", "nv", "n.v", "llc", "gmbh", "pty", "spa", "asa", "oyj", "bv", "co",
    }
)
#: Trailing heading words after a "Risks Related to <Name>" name.
_HEADING_NAME_TAIL: frozenset[str] = frozenset(
    {
        "merger", "acquisition", "transaction", "transactions", "business", "spin-off",
        "separation", "investment", "stake", "shares", "ownership", "relationship",
        "agreement", "collaboration", "listing",
    }
)
_RELATED_TO_RE = re.compile(
    r"\b(?:Related|Relating|Relate)[ \t]+to[ \t]+(?:the[ \t]+|[Oo]ur[ \t]+)?"
    r"(?P<name>[A-Z][\w&.'’-]{2,40}(?:[ \t]+[A-Z][\w&.'’-]{2,40}){0,2})"
)
#: An acronym with its spelled-out name, either way round; the initials must agree.
_ACRONYM_LONG = r"(?P<l>[A-Z][\w-]{1,40}(?:[ \t]{1,3}[A-Z][\w-]{1,40}){1,5})"
_ACRONYM_FIRST_RE = re.compile(r"\b(?P<a>[A-Z]{2,6})[ \t]{0,3}\(" + _ACRONYM_LONG + r"\)")
_ACRONYM_LAST_RE = re.compile(r"\b" + _ACRONYM_LONG + r"[ \t]{0,3}\((?P<a>[A-Z]{2,6})\)")
_CAPS_WORD_RE = re.compile(r"[A-ZÀ-Þ][A-ZÀ-Þ&'’.\-]*")
_INLINE_HEADING_RE = re.compile(
    r"^[ \t]*(?P<head>[A-ZÀ-Þ][A-ZÀ-Þ&'’ .\-]{2,60}?)[ \t]*(?:—|–| - |:)[ \t]*"
)

_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "no", "nos", "mr", "mrs", "ms", "dr", "st", "vs", "v", "e.g", "i.e", "approx",
        "fig", "figs", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
        "oct", "nov", "dec", "est", "ref", "art", "para", "p", "pp", "u.s", "u.k", "a.m",
        "p.m", "cf",
    }
)
_SENTENCE_END_RE = re.compile(r"[.!?][\"”’)]?[ \t]+(?=[\"“‘'(A-Z0-9À-Þ])")
_CLAUSE_SPLIT_RE = re.compile(r";[ \t]+|[ \t]{1,10}\|[ \t]+|\t+|[ \t]{0,10}•[ \t]*")

_FIGURE_RE = re.compile(r"(?<![A-Za-z\d.,])\d[\d,]*(?:\.\d+)?")
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ][\w'’-]*")
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "were", "was", "are", "has",
        "have", "had", "its", "their", "which", "will", "would", "been", "being", "into",
        "than", "then", "also", "such", "over", "under", "about", "after", "before",
        "company", "group", "issuer", "says", "said", "states", "stated", "report",
        "reported", "reports", "per", "including", "includes", "while", "versus", "year",
        "years", "million", "billion", "thousand", "percent",
    }
)
#: A sentence whose predicate IS the relationship with the subject: concentration
#: ("accounted for 40% of revenue"), a copular role ("is our sole supplier"), contract
#: terms (offtake, facility, supply or purchase agreement, royalty, interest rate,
#: repayable, drawn), a commitment ("agreed to purchase / lend"), an interest held by
#: someone, a funding share. Read only where the subject anchors the sentence.
_ROLE_WORDS = (
    r"(?:customers?|clients?|suppliers?|off-?takers?|lenders?|financiers?|partners?|"
    r"joint[- ]venture\s+partners?|JV\s+partners?|distributors?|licensees?|licensors?|"
    r"contractors?|buyers?|purchasers?)"
)
_RELATIONAL_PREDICATE_RE = re.compile(
    r"\b(?:accounted|accounts|accounting|represented|represents|representing|comprised|"
    r"comprises|made[ \t]+up|contributed|contributes)[ \t]+(?:for[ \t]+)?"
    r"(?:(?:approximately|about|around|over|nearly|some|almost|roughly)[ \t]+)?"
    r"\d{1,3}(?:\.\d{1,2})?[ \t]*%"
    r"|\b\d{1,3}(?:\.\d{1,2})?[ \t]*%[ \t]+of[ \t]+(?:(?:our|its|the|total|consolidated|"
    r"group|net|annual|company['’]s|group['’]s)[ \t]+){0,3}(?:revenues?|sales|purchases|"
    r"turnover|receivables|cost[ \t]+of[ \t]+(?:sales|goods))\b"
    r"|\b(?:is|was|are|were|remains?|remained|became|has[ \t]+been|will[ \t]+be|acts?"
    r"[ \t]+as|serves?[ \t]+as)[ \t]+(?:(?:the|our|a|an|one[ \t]+of[ \t]+(?:our|the)|"
    r"the[ \t]+company['’]s|the[ \t]+group['’]s)[ \t]+)?(?:[\w-]{1,20}[ \t]+){0,2}?"
    + _ROLE_WORDS
    + r"\b"
    r"|\boff-?take\b|\b(?:loan|credit|debt|financing|bridge|revolving)[ \t]+facilit(?:y|ies)\b"
    r"|\b(?:supply|purchase|sales|offtake|streaming|royalty|licen[cs]e|collaboration|"
    r"shareholders['’]?)[ \t]+(?:agreement|contract)s?\b|\broyalty\b"
    r"|\b(?:agreed|agrees|committed|commits)[ \t]+to[ \t]+(?:purchase|buy|acquire|take|"
    r"lend|provide|advance|fund|supply)\b"
    r"|\binterest[ \t]+rate\b|\brepayable\b|\b(?:un)?drawn\b|\bsecured[ \t]+(?:by|against|over)\b"
    r"|\b\d{1,3}(?:\.\d{1,2})?[ \t]*%[ \t]+(?:equity[ \t]+)?(?:interest|stake|shareholding|"
    r"ownership)\b[^.;]{0,40}\b(?:held|owned)[ \t]+by\b"
    r"|\b(?:funds?|funded|funding|contribut\w*)\b[^.;]{0,40}?\d{1,3}(?:\.\d{1,2})?[ \t]*%",
    re.IGNORECASE,
)
#: The other entity's OWN facts: its results, costs, output, risks. A role label in
#: front of them ("Our largest customer, Vexmoor, reported revenue of $4.2bn") does not
#: make them the subject's.
_OWN_FACT_RE = re.compile(
    r"\b(?:reported|reports|posted|generated|recorded|produced|earned|booked|delivered)\b"
    r"|\bits[ \t]+(?:own[ \t]+)?(?:revenues?|sales|costs?|production|output|profits?|"
    r"earnings|net[ \t]+income|ebitda|debt|risks?|guidance|outlook|results|margins?)\b",
    re.IGNORECASE,
)
#: Relationships a frame back to the subject can admit. Not a former subsidiary (it left
#: the perimeter), a peer or a regulator (not a party to the subject's business), nor a
#: separately reporting namesake.
_FRAMEABLE: frozenset[str] = frozenset(
    {CUSTOMER, SUPPLIER, COUNTERPARTY, OTHER_ISSUER}
)

#: Wording that makes a sentence the Group's (or a note reference), so a heading above
#: it does not narrow its scope: "Group copper production", "Total company sales", "For
#: the Firm", "Our total sales", "Calderon's consolidated sales", "Net debt".
_GROUP_WORDING_RE = re.compile(
    r"\b(?:group|total|totals|totalling|consolidated|firm|firmwide|corporation|company|"
    r"net[ \t]+debt|dividends?|earnings[ \t]+per[ \t]+share)\b"
    r"|\bNote[ \t]+\d",
    re.IGNORECASE,
)
_FIRST_PERSON_RE = re.compile(r"\b(?:[Ww]e|[Oo]ur|us)\b")
#: Wording that is the Group's whatever else the sentence names: never scoped.
_GROUP_STRONG_RE = re.compile(
    r"\b(?:group|consolidated|firm|firmwide|corporation|net[ \t]+debt|dividends?|"
    r"earnings[ \t]+per[ \t]+share|total[ \t]+company|company-?wide)\b"
    r"|\bNote[ \t]+\d",
    re.IGNORECASE,
)
#: The separation event itself: the filer's accounting, even in a former subsidiary's
#: paragraph.
_SEPARATION_RE = re.compile(
    r"\bdeconsolidat\w*|\bdivest\w*|\bspun[- ]off\b|\bspin[- ]?off\b|\bdisposal\b|"
    r"\bproceeds\b|\b(?:gain|loss)[ \t]+on\b",
    re.IGNORECASE,
)
#: Relationships that put an entity positively OUTSIDE the perimeter: only their own
#: reported results, in the subject's document, are dropped (review R1). A cue-less
#: OTHER_ISSUER may be an operating company of the subject's.
_NON_PERIMETER: frozenset[str] = frozenset({PEER, CUSTOMER, SUPPLIER, COUNTERPARTY})
#: The other entity's OWN reported results, in the subject's own document: the only
#: other-entity sentence there that is dropped ("Our largest customer, Vexmoor, reported
#: revenue of $4.2bn"). Operational verbs ("produced") are not enough: in the
#: subject's report an operating company's output is the perimeter's.
_OWN_RESULTS_RE = re.compile(
    r"\b(?:reported|reports|posted|generated|earned|booked)\b"
    r"|\bits[ \t]+(?:own[ \t]+)?(?:revenues?|sales|costs?|production|output|profits?|"
    r"earnings|net[ \t]+income|ebitda|debt|results|margins?|guidance|outlook)\b",
    re.IGNORECASE,
)
#: A transaction whose counterparty is the subject or unnamed: "Brindle Pharma will invest
#: $140m", "Ostara paid a milestone", "Kestrel32 will purchase 100% of production".
_TRANSACTION_RE = re.compile(
    r"\b(?:invest(?:s|ed|ing)?|investment|paid|pays?|lends?|lent|lending|loaned|"
    r"fund(?:s|ed)?|purchases?|purchased|buys?|bought|subscribed|disbursed|"
    r"(?:will|to)[ \t]+(?:pay|purchase|buy|take|lend|provide|fund|invest|supply)|"
    r"holds?|held|owns?)\b",
    re.IGNORECASE,
)
#: Words a table row may carry beside a name and its figures.
_UNIT_WORDS: frozenset[str] = frozenset(
    {
        "million", "billion", "thousand", "usd", "eur", "gbp", "aud", "cad", "chf", "jpy",
        "mln", "bln", "tonnes", "ounces", "per", "cent",
    }
)
#: A subject named by initials ("J.K. Harrow" for "JKHarrow Holdings").
_INITIALS_NAME_RE = re.compile(
    r"(?<![\w&.])(?:[A-Z]\.){1,4}[ \t]?[A-Z][a-z]+\b|(?<![\w&.])[A-Z]{1,3}[ \t]+[A-Z][a-z]+\b"
)
#: "Calderon Bank Canada, a former subsidiary": a namesake with no legal suffix, placed
#: outside the perimeter by an appositive.
_NAMESAKE_FORMER_RE = re.compile(
    r"(?<![\w&])(?P<name>[A-Z][\w&.'’-]{0,40}(?:[ \t]{1,3}[A-Z][\w&.'’-]{0,40}){1,4}),"
    r"[ \t]{1,3}(?:a|an|our|its|the[ \t]{1,3}\w{1,40}['’]s)[ \t]{1,3}former(?:ly)?[ \t]{1,3}"
    r"(?:[\w-]{1,40}[ \t]{1,3}){0,2}?subsidiar(?:y|ies)\b"
)

#: The least a sentence must share with the statement to be "the" sentence.
_MIN_MATCH_SCORE = 2


# ── Internal model ───────────────────────────────────────────────────────── #


@dataclass(eq=False)
class _Entity:
    name: str
    hint: str  # SUBJECT, SUBSIDIARY (namesake), OTHER_ISSUER
    aliases: set[str] = field(default_factory=set)
    defined_terms: set[str] = field(default_factory=set)
    relationship: str = UNKNOWN
    #: A separately reporting registrant (ADR-066): never the subject's perimeter.
    separate: bool = False


@dataclass(eq=False)
class _Mention:
    start: int
    end: int
    entity: _Entity | None = None
    implicit: str | None = None
    term: str | None = None
    possessive: bool = False
    masked: bool = False


def _folded(text: str) -> str:
    from app.services.discovery.identity import _fold  # noqa: PLC2701

    return _fold(text)


def subject_aliases(subject_name: str | None) -> frozenset[str]:
    """The subject's distinctive name words, folded (generic and short words dropped)."""
    from app.services.discovery.identity import _GENERIC_TOKENS, name_tokens  # noqa: PLC2701

    return frozenset(
        t for t in name_tokens(subject_name or "") if t not in _GENERIC_TOKENS and len(t) >= 4
    )


def _clean_name(name: str) -> str:
    """A page-derived entity name made safe to show: letters, digits, a little
    punctuation, capped. Nothing else from the page ever reaches a gap."""
    cleaned = re.sub(r"[^\w&.,'’() -]", "", name, flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip(" ,.-")[:NAME_MAX]


def _classify_org(name: str, subject: SubjectIdentity, aliases: frozenset[str]) -> str | None:
    """SUBJECT, SUBSIDIARY (a namesake of the subject) or OTHER_ISSUER; None when the
    name has no distinctive word ("Mining Corp", "The Bank, N.A.")."""
    from app.services.discovery.identity import (  # noqa: PLC2701
        _GENERIC_TOKENS,
        name_tokens,
        normalised_name,
    )

    tokens = [t for t in name_tokens(name) if t not in _GENERIC_TOKENS]
    if not tokens:
        return None
    own_tokens = [t for t in name_tokens(subject.name or "") if t not in _GENERIC_TOKENS]
    own = set(name_tokens(subject.name or ""))
    squash = re.sub(r"[^a-z0-9]", "", normalised_name(name))
    own_squash = re.sub(r"[^a-z0-9]", "", normalised_name(subject.name or ""))
    if (set(tokens) <= own and set(tokens) & aliases) or (
        squash and squash == own_squash
    ):
        # The subject's own name, in any spelling ("J.K. Harrow & Co" for "JKHarrow &
        # Co.") — unless it carries a subsidiary's legal form the subject's name does
        # not ("Calderon AG" under "Calderon plc"): a namesake inside the perimeter.
        form = _legal_form(name)
        own_forms = {
            _FORM_CLASSES[w]
            for w in re.findall(r"[a-z]+", re.sub(r"\.", "", (subject.name or "").lower()))
            if w in _FORM_CLASSES
        }
        if (
            form in _SUBSIDIARY_FORMS
            and own_forms
            and _FORM_CLASSES[form] not in own_forms
            and set(tokens) == set(own_tokens)
        ):
            return _FORM_NAMESAKE
        return SUBJECT
    if tokens[0] in aliases or any(len(a) >= 5 and squash.startswith(a) for a in aliases):
        return SUBSIDIARY
    return OTHER_ISSUER


def _legal_form(name: str) -> str | None:
    """The folded legal form a name ends in ("ag", "sa", "plc"), dots removed."""
    words = re.findall(r"[A-Za-z.]+", name)
    if not words:
        return None
    last = re.sub(r"\.", "", words[-1]).lower()
    return last if last in _FORM_CLASSES else None


def _strip_form(name: str) -> str:
    """A name without its trailing legal form(s): "Brindle Beam Systems SA" → "Brindle Beam
    Systems"."""
    words = name.replace(",", " ").split()
    while words and re.sub(r"\.", "", words[-1]).lower() in _FORM_CLASSES:
        words.pop()
    return " ".join(words)


def _subject_initials(name: str | None) -> str | None:
    """The subject's initials when it is known by them ("NSMC" for Northgate Semiconductor
    Manufacturing Company Limited): three or more name words, the trailing legal form
    and connectors dropped."""
    words = [w for w in re.findall(r"[A-Za-z][\w'’.-]*", name or "")]
    while words and re.sub(r"\.", "", words[-1]).lower() in set(_FORM_CLASSES) - {"company"}:
        words.pop()
    words = [w for w in words if w.lower() not in {"of", "and", "the", "&"}]
    if len(words) < 3:
        return None
    initials = "".join(w[0] for w in words).upper()
    return initials if len(initials) >= 3 else None


@functools.lru_cache(maxsize=32)
def _registered_by_name(known: tuple[str, ...]) -> dict[str, str]:
    """Registered names without legal form, squashed → the registered name."""
    return {re.sub(r"[^a-z0-9]", "", _folded(_strip_form(k))): k for k in known}


@dataclass(frozen=True)
class _Registered:
    """One registered company, as the passage reader looks for it."""

    name: str
    #: Its name words, legal form dropped, folded: matched word by word.
    words: tuple[str, ...]
    #: Its first distinctive token: skipped when it is the subject's own word.
    distinctive: str


#: The legal-form tail after a registered name, compiled once.
_FORM_TAIL_RE = re.compile(
    rf",?[ \t]{{1,3}}(?:{_LEGAL_SUFFIX}|{_SHORT_SUFFIX})\.?(?![\w&-])"
)
_NEXT_WORD_RE = re.compile(r"[ \t]{1,3}([^\s,;:()]{1,60})")


@functools.lru_cache(maxsize=8)
def _known_index(known: tuple[str, ...]) -> dict[str, tuple[_Registered, ...]]:
    """The platform's registered companies, indexed once per name list (never per
    subject): first folded name word → entries. No regex is compiled per name.

    A company is named by its FULL registered name (legal form optional) or, for a
    one-word name, by name + legal form — never by its first word alone ("Southern
    region" for a registered "Southern Ore Corp", "Target ratio")."""
    from app.services.discovery.identity import _GENERIC_TOKENS  # noqa: PLC2701

    index: dict[str, list[_Registered]] = {}
    for registered in known:
        words = registered.replace(",", " ").split()
        while words and words[-1].replace(".", "").lower() in _FORM_CLASSES:
            words.pop()
        if words and words[0].lower() == "the":
            words.pop(0)
        if not words:
            continue
        folded = tuple(_folded(w) for w in words)
        distinctive = next(
            (
                t
                for t in (re.sub(r"[^a-z0-9]", "", f) for f in folded)
                if len(t) >= 4 and t not in _GENERIC_TOKENS
            ),
            None,
        )
        if distinctive is None:
            continue
        index.setdefault(folded[0], []).append(_Registered(registered, folded, distinctive))
    return {key: tuple(value) for key, value in index.items()}


def _match_registered(text: str, start: int, entry: _Registered) -> int | None:
    """End of ``entry``'s name at ``start`` (its full name words, then its legal form,
    which a one-word name requires); None when it is not there."""
    end = start + len(text[start:].split(None, 1)[0].rstrip(".,;:'’"))
    for expected in entry.words[1:]:
        found = _NEXT_WORD_RE.match(text, end)
        if found is None or _folded(found.group(1).rstrip(".,;:'’")) != expected:
            return None
        end = found.start(1) + len(found.group(1).rstrip(".,;:'’"))
    tail = _FORM_TAIL_RE.match(text, end)
    if tail is not None:
        return tail.end()
    return end if len(entry.words) >= 2 else None


class _Reader:
    """Reads one passage: entities, mentions, segments, headings."""

    def __init__(
        self, passage: Passage, subject: SubjectIdentity, aliases: frozenset[str]
    ) -> None:
        self.passage = passage
        self.subject = subject
        self.aliases = aliases
        self.text = passage.text
        self.subj = _Entity(name=subject.name or "the issuer", hint=SUBJECT, relationship=SUBJECT)
        self.entities: list[_Entity] = []
        self.mentions: list[_Mention] = []
        self._starts: list[int] = []
        self._ends: list[int] = []
        self.heading_entity: _Entity | None = None
        self.owner = passage.owner
        self.owner_entity: _Entity | None = None

    # -- spans ---------------------------------------------------------------- #

    def _free(self, start: int, end: int) -> bool:
        """No mention overlaps [start, end). Mentions never overlap, so their spans
        sorted by start are sorted by end too: two neighbours decide (O(log n))."""
        i = bisect.bisect_right(self._starts, start)
        if i > 0 and self._ends[i - 1] > start:
            return False
        return not (i < len(self._starts) and self._starts[i] < end)

    def _add(self, mention: _Mention) -> None:
        if mention.end > mention.start and self._free(mention.start, mention.end):
            i = bisect.bisect_right(self._starts, mention.start)
            self._starts.insert(i, mention.start)
            self._ends.insert(i, mention.end)
            self.mentions.append(mention)

    def _entity_named(self, name: str, hint: str) -> _Entity:
        if hint == SUBJECT:
            return self.subj
        clean = _clean_name(name)
        key = _folded(clean)
        for entity in self.entities:
            if _folded(entity.name) == key:
                return entity
        entity = _Entity(name=clean, hint=hint)
        self.entities.append(entity)
        return entity

    # -- entity detection ----------------------------------------------------- #

    def _org_name_before(self, end: int, text: str) -> tuple[int, str] | None:
        """Walk back from a legal suffix over at most six name tokens."""
        window_start = max(0, end - 160)
        tokens = list(re.finditer(r"\S+", text[window_start:end]))
        kept: list[re.Match[str]] = []
        for token in reversed(tokens):
            raw = token.group(0)
            if raw[-1] in ",;:()[]\"“”" or "(" in raw:
                break
            if raw.endswith(".") and not re.fullmatch(r"(?:[A-Z]\.){1,4}", raw):
                break
            word = raw.strip("'’\"“”")
            if _folded(word) in _NAME_STOP:
                break
            if not (_NAME_TOKEN_RE.fullmatch(word) or word.lower() in _NAME_CONNECTORS):
                break
            kept.append(token)
            if len(kept) >= 6:
                break
        kept.reverse()
        while kept and kept[0].group(0).lower() in _NAME_CONNECTORS:
            kept.pop(0)
        if not kept:
            return None
        start = window_start + kept[0].start()
        return start, text[start:end]

    def read(self) -> None:
        text = self.text
        # 1. Separately reporting / family namesakes (ADR-066 phrases).
        for phrases, hint in (
            (self.subject.separate_registrants, OTHER_ISSUER),
            (self.subject.family_registrants, SUBSIDIARY),
        ):
            for phrase, name in phrases:
                words = [re.escape(w) for w in phrase.split() if w]
                if len(words) < 2:
                    continue
                for match in re.finditer(r"\b" + r"[ \t]+".join(words) + r"\b", text, re.I):
                    entity = self._entity_named(name, hint)
                    entity.separate = hint == OTHER_ISSUER
                    entity.aliases.add(match.group(0))
                    self._add(_Mention(match.start(), match.end(), entity=entity))
        # 2. Organisation names with a legal suffix.
        for match in itertools.islice(_SUFFIX_RE.finditer(text), _MAX_SUFFIX_NAMES):
            found = self._org_name_before(match.start(), text)
            if found is None:
                continue
            start, _ = found
            end = match.end()
            name = text[start:end]
            if not self._free(start, end):
                continue
            if re.fullmatch(r",?[ \t]*N\.A\.?", match.group(0).strip()) and not re.search(
                r"\bBank,?[ \t]*$", text[start: match.start() + 1]
            ):
                continue  # "N.A." is a national bank's form, else "not available"
            org_hint = _classify_org(name, self.subject, self.aliases)
            if org_hint is None:
                continue
            entity = self._entity_named(name, org_hint)
            self._add(_Mention(start, end, entity=entity))
            self._defined_after(entity, end)
            self._short_alias(entity, text[start:end])
        # 2b. A namesake with no legal form, put outside the perimeter by an appositive:
        #     "Calderon Bank Canada, a former subsidiary sold in 2024".
        for match in _NAMESAKE_FORMER_RE.finditer(text):
            words = match.group("name").split()
            first = next(
                (i for i, w in enumerate(words) if _folded(w.strip(".,")) in self.aliases), None
            )
            if first is None or len(words) - first < 2:
                continue
            start = match.start("name") + len(" ".join(words[:first])) + (1 if first else 0)
            end = match.end("name")
            if self._free(start, end):
                entity = self._entity_named(text[start:end], SUBSIDIARY)
                self._add(_Mention(start, end, entity=entity))
        # 3. An acronym with its spelled-out name ("Brindle Beam Systems (BBS)") is a
        #    company only when the spelled-out name is a registered company: otherwise
        #    it is a study, a segment, a disease or a regulator (DFS, BA, AML, PRA) and
        #    names no one (UNKNOWN). A legal-form name with a bare acronym after it
        #    ("Brindle Beam Systems SA (BBS)") was read in step 2.
        registered_by_name = _registered_by_name(self.subject.known_entities)
        for pattern in (_ACRONYM_FIRST_RE, _ACRONYM_LAST_RE):
            for match in pattern.finditer(text):
                acronym, long = match.group("a"), match.group("l")
                initials = "".join(w[0] for w in long.split()).upper()
                if initials != acronym or not self._free(match.start(), match.end()):
                    continue
                registered = registered_by_name.get(re.sub(r"[^a-z0-9]", "", _folded(long)))
                if registered is None:
                    continue
                entity = self._entity_named(registered, OTHER_ISSUER)
                entity.aliases.add(acronym)
                self._add(_Mention(match.start(), match.end(), entity=entity))
        # 4. The platform's registered companies, by their full registered name.
        if self.subject.known_entities:
            from app.services.discovery.identity import name_tokens  # noqa: PLC2701

            index = _known_index(self.subject.known_entities)
            own_tokens = set(name_tokens(self.subject.name or ""))
            for capital in re.finditer(r"(?<![\w&])[A-Z][\w'’-]*", text):
                key = _folded(capital.group(0).rstrip("'’"))
                for entry in index.get(key, ()):
                    if entry.distinctive in own_tokens:
                        continue
                    known_end = _match_registered(text, capital.start(), entry)
                    if known_end is None:
                        continue
                    entity = self._entity_named(entry.name, OTHER_ISSUER)
                    self._add(_Mention(capital.start(), known_end, entity=entity))
                    self._short_alias(entity, entry.name)
                    break
        # 5. A heading's entity ("Risks Related to Calyxt").
        self._heading_entity()
        # 6. Later mentions of an entity by its alias ("Kestrel32 said…", "JPMSE's").
        for entity in list(self.entities):
            for alias in sorted(entity.aliases, key=len, reverse=True):
                for match in re.finditer(
                    rf"(?<![\w&]){re.escape(alias)}(?![\w&])", text
                ):
                    self._add(_Mention(match.start(), match.end(), entity=entity))
        # 7. The subject, by its own words and ticker.
        ticker = (self.subject.ticker or "").strip()
        if len(ticker) >= 2:
            for match in re.finditer(
                rf"(?<![A-Za-z0-9.-]){re.escape(ticker)}(?![A-Za-z0-9-])", text
            ):
                self._add(_Mention(match.start(), match.end(), entity=self.subj))
        own_initials = _subject_initials(self.subject.name)
        if own_initials:
            for match in re.finditer(rf"(?<![\w&.-]){own_initials}(?![\w&-])", text):
                self._add(_Mention(match.start(), match.end(), entity=self.subj))
        for match in _INITIALS_NAME_RE.finditer(text):
            if re.sub(r"[^a-z0-9]", "", _folded(match.group(0))) in self.aliases:
                self._add(_Mention(match.start(), match.end(), entity=self.subj))
        for match in _WORD_RE.finditer(text):
            word = re.sub(r"['’]s$", "", match.group(0))
            if word[:1].isupper() and _folded(word) in self.aliases:
                self._add(_Mention(match.start(), match.start() + len(word), entity=self.subj))
        # 8. Implicit references.
        for match in _IMPLICIT_RE.finditer(text):
            if match.group("first"):
                kind, term = _FIRST, None
            elif match.group("pron"):
                kind, term = _PRONOUN, None
            elif match.group("this"):
                kind, term = _DEFINITE, None
            elif match.group("bare"):
                # "Group CET1 ratio", "Firmwide net income": the filer's own label.
                kind = _DEFINED
                term = "group" if match.group("bare") == "Group" else "firm"
            else:
                raw_term = re.sub(r"\s+", " ", match.group("term"))
                kind = _DEFINED if raw_term[0].isupper() else _DEFINITE
                term = raw_term.lower()
            rest = text[match.end(): match.end() + 3]
            possessive = match.group(0).lower() in {"our", "its", "ours"} or rest.startswith(
                ("'s", "’s")
            )
            self._add(
                _Mention(
                    match.start(), match.end(), implicit=kind, term=term, possessive=possessive
                )
            )
        self.mentions.sort(key=lambda m: m.start)
        self._mask()
        self._relationships()
        self._resolve_owner()

    def _defined_after(self, entity: _Entity, end: int) -> None:
        """Aliases a passage defines right after a name: (“JPMSE” or “the Bank”)."""
        match = re.match(r"[ \t]*\((?P<body>[^()]{1,80})\)", self.text[end: end + 90])
        if not match:
            return
        body = match.group("body")
        quoted = re.findall(r"[\"“‘']([^\"”’']{1,40})[\"”’']", body)
        if not quoted:
            bare = re.fullmatch(
                r"[ \t]*(?:the[ \t]+)?([A-Z][\w.&-]{1,30}(?:[ \t]+[A-Z][\w.&-]{1,30}){0,2})[ \t]*",
                body,
            )
            quoted = [bare.group(1)] if bare else []
        for alias in quoted:
            alias = alias.strip()
            low = re.sub(r"^the\s+", "", alias.lower()).strip()
            if low in _DEFINED_TERMS:
                entity.defined_terms.add(low)
            elif alias and alias[0].isupper() and len(alias) >= 2:
                entity.aliases.add(alias)

    def _short_alias(self, entity: _Entity, name: str) -> None:
        """The first distinctive word of a name, as later sentences call it."""
        if entity is self.subj:
            return
        from app.services.discovery.identity import _GENERIC_TOKENS  # noqa: PLC2701

        for raw in name.split():
            word = raw.strip(",.'’\"“”")
            folded = re.sub(r"[^a-z0-9]", "", _folded(word))
            if folded in _ALIAS_SKIP:
                continue
            if (
                4 <= len(folded) <= _MAX_TOKEN_CHARS
                and folded not in _GENERIC_TOKENS
                and folded not in _NAME_STOP
                and folded not in self.aliases
                and not re.fullmatch(r"(?:[A-Z]\.)+", word)
            ):
                entity.aliases.add(word)
                if word.isupper():
                    entity.aliases.add(word.capitalize())
            return

    def _heading_entity(self) -> None:
        heading = self.passage.heading or ""
        if not heading:
            return
        for part in reversed([p for p in re.split(r"\s*(?:>|›|»|\|)\s*", heading) if p]):
            for match in _SUFFIX_RE.finditer(part):
                found = self._org_name_before(match.start(), part)
                if found is None:
                    continue
                name = part[found[0]: match.end()]
                hint = _classify_org(name, self.subject, self.aliases)
                if hint is not None:
                    entity = self._entity_named(name, hint)
                    self._short_alias(entity, name)
                    self.heading_entity = entity
                    return
            words = [_folded(w) for w in _WORD_RE.findall(part)]
            if any(w in self.aliases for w in words):
                self.heading_entity = self.subj
                return
            related = _RELATED_TO_RE.search(part)
            if related:
                name_words = related.group("name").split()
                while name_words and _folded(name_words[-1]) in _HEADING_NAME_TAIL:
                    name_words.pop()
                if not name_words:
                    continue
                first = name_words[0]
                folded = _folded(first)
                named: _Entity | None = next(
                    (e for e in self.entities if first in e.aliases), None
                )
                if named is None and (
                    len(folded) >= 4
                    and folded not in _GENERIC_HEADING_WORDS
                    and folded not in self.aliases
                    and re.search(rf"(?<![\w&]){re.escape(first)}(?![\w&])", self.text)
                ):
                    named = self._entity_named(" ".join(name_words), OTHER_ISSUER)
                    named.aliases.add(first)
                if named is not None:
                    self.heading_entity = named
                    return

    def _mask(self) -> None:
        """A name or pronoun that only anchors a role phrase is not a reference:
        "our former subsidiary", "a subsidiary of <the subject>", "a lender to the
        Company"."""
        for mention in self.mentions:
            after = self.text[mention.end: mention.end + 60]
            before = self.text[max(0, mention.start - 60): mention.start]
            possessive = mention.possessive or after.startswith(("'s", "’s"))
            if possessive and _POSSESSIVE_ROLE_RE.match(after):
                mention.masked = True
            elif _ROLE_OF_RE.search(before):
                mention.masked = True

    def _relationships(self) -> None:
        """Each named entity's relationship to the subject, from the nearest cue."""
        for entity in self.entities:
            if entity.hint == SUBJECT:
                entity.relationship = SUBJECT
                continue
            best: tuple[int, int, str] | None = None
            for mention in self.mentions:
                if mention.entity is not entity:
                    continue
                lo = max(0, mention.start - _CUE_WINDOW)
                hi = min(len(self.text), mention.end + _CUE_WINDOW)
                window = self.text[lo:hi]
                hits: list[tuple[int, int, int, str]] = []
                for rank, (label, pattern) in enumerate(_CUES):
                    for hit in pattern.finditer(window):
                        hits.append((lo + hit.start(), lo + hit.end(), rank, label))
                formers = [(a, b) for a, b, _r, lab in hits if lab == FORMER_SUBSIDIARY]
                for a, b, rank, label in hits:
                    if label != FORMER_SUBSIDIARY and any(
                        fa <= a and b <= fb for fa, fb in formers
                    ):
                        continue  # "former subsidiary" is not also "subsidiary"
                    if b <= mention.start:
                        distance = mention.start - b
                    elif a >= mention.end:
                        distance = a - mention.end
                    else:
                        distance = 0
                    candidate = (distance, rank, label)
                    if best is None or candidate < best:
                        best = candidate
            cue = best[2] if best else None
            if entity.separate:
                # Reports on its own (ADR-066): never the subject's perimeter.
                entity.relationship = (
                    FORMER_SUBSIDIARY if cue == FORMER_SUBSIDIARY else OTHER_ISSUER
                )
            elif entity.hint == _FORM_NAMESAKE:
                # The subject's own name in another legal form: the subject itself
                # ("BASF AG" for "BASF SE") unless a cue makes it a subsidiary, a joint
                # venture or a former subsidiary ("Linde AG, the German operating
                # company").
                entity.relationship = (
                    cue if cue in (SUBSIDIARY, FORMER_SUBSIDIARY, JOINT_VENTURE) else SUBJECT
                )
            elif entity.hint == SUBSIDIARY:
                # A namesake is the subject's perimeter unless a cue says it left it.
                entity.relationship = (
                    cue if cue in (FORMER_SUBSIDIARY, JOINT_VENTURE) else SUBSIDIARY
                )
            else:
                entity.relationship = cue or OTHER_ISSUER

    def _resolve_owner(self) -> None:
        if self.owner == OWNER_SUBJECT:
            self.owner_entity = self.subj
            return
        if self.owner == OWNER_OTHER:
            name = self.passage.owner_name or "another registrant"
            self.owner_entity = self._entity_named(name, OTHER_ISSUER)
            self.owner_entity.relationship = OTHER_ISSUER
            return
        host = self.passage.company_site_host
        if not host:
            return
        from app.services.discovery.identity import issuer_domain_matches

        for entity in self.entities:
            if entity.hint != SUBJECT and issuer_domain_matches(host, entity.name):
                self.owner = OWNER_OTHER
                self.owner_entity = entity
                return
        # A registered company's own site that calls it by its first word only ("Iluka
        # half-year results" on iluka.com): the host is the evidence the full name is
        # not, so the first word names it HERE (never on any other page).
        if not self.subject.known_entities:
            return
        label = _folded(re.sub(r"^www\d?\.", "", host.lower()).split(".")[0])
        for entry in _known_index(self.subject.known_entities).get(label, ()):
            if not issuer_domain_matches(host, entry.name):
                continue
            first = entry.words[0]
            hits = [
                m for m in re.finditer(r"(?<![\w&])[A-Z][\w'’-]*", self.text)
                if _folded(m.group(0)) == first and self._free(m.start(), m.end())
            ]
            if not hits:
                continue
            entity = self._entity_named(entry.name, OTHER_ISSUER)
            entity.relationship = OTHER_ISSUER
            for hit in hits:
                self._add(_Mention(hit.start(), hit.end(), entity=entity))
            self.mentions.sort(key=lambda m: m.start)
            self.owner = OWNER_OTHER
            self.owner_entity = entity
            return

    # -- segments ------------------------------------------------------------- #

    def _entity_within(self, start: int, end: int) -> _Entity | None:
        for mention in self.mentions:
            if start <= mention.start < end and mention.entity is not None:
                return mention.entity
        return None

    def segments(
        self,
    ) -> tuple[list[tuple[int, int]], list[tuple[int, str | None, _Entity | None]]]:
        """Sentence/clause spans, and inline headings as (position, scope, entity).

        A capitals line is a heading, not a sentence. It sets the scope of what follows
        when it names a mine, project or segment, clears it when it is structural, and
        sets the topic when it names a company ("ILUKA RESOURCES LIMITED")."""
        text = self.text
        spans: list[tuple[int, int]] = []
        headings: list[tuple[int, str | None, _Entity | None]] = []
        offset = 0
        for line in text.split("\n"):
            line_start = offset
            offset += len(line) + 1
            stripped = line.strip()
            if not stripped:
                continue
            if _caps_heading_line(stripped):
                headings.append(
                    (
                        line_start,
                        _scope_heading(stripped, self.aliases, text),
                        self._entity_within(line_start, line_start + len(line)),
                    )
                )
                continue
            body_start = line_start
            prefix = _INLINE_HEADING_RE.match(line)
            if prefix and _caps_heading_line(prefix.group("head").strip()):
                headings.append(
                    (
                        line_start,
                        _scope_heading(prefix.group("head").strip(), self.aliases, text),
                        self._entity_within(line_start, line_start + prefix.end()),
                    )
                )
                body_start = line_start + prefix.end()
            for start, end in _split_sentences(text, body_start, line_start + len(line)):
                for c_start, c_end in _split_clauses(text, start, end):
                    if text[c_start:c_end].strip():
                        spans.append((c_start, c_end))
        return spans, headings


def _caps_heading_line(line: str) -> bool:
    """1-6 capitalised words with at least four letters: "CENTINELA", "RISK FACTORS"."""
    words = line.split()
    return (
        1 <= len(words) <= 6
        and all(len(w) <= _MAX_TOKEN_CHARS for w in words)
        and all(_CAPS_WORD_RE.fullmatch(w) for w in words)
        and sum(c.isalpha() for c in line) >= 4
    )


_NOTE_BEFORE_RE = re.compile(r"\bNote[ \t]+\d+[A-Za-z]?[ \t]*$")


def _scope_heading(heading: str, aliases: frozenset[str], text: str) -> str | None:
    """A capitals heading that names a mine, project or segment ("CENTINELA", "LOS
    PELAMBRES"); None for a structural heading ("GROUP FINANCIAL REVIEW"), the subject's
    own name, a company's name ("ILUKA RESOURCES LIMITED") or a short acronym ("ICAAP").

    A proper name, not a topic word: its first distinctive word must recur in the
    passage as a capitalised word inside a sentence ("...on the Centinela Second
    Concentrator"). "CYBERSECURITY" over "Our cybersecurity program..." is a topic."""
    words = [w.strip(".-'’") for w in heading.split() if w.strip(".-'’")]
    if not words or not _caps_heading_line(" ".join(words)):
        return None
    folded = [_folded(w) for w in words]
    if all(f in _GENERIC_HEADING_WORDS for f in folded):
        return None
    if any(f in aliases or f in _LEGAL_WORDS for f in folded):
        return None
    if len(words) == 1 and len(words[0]) < 6:
        return None
    key = next(w for w, f in zip(words, folded, strict=True) if f not in _GENERIC_HEADING_WORDS)
    proper = key.capitalize()
    recurs = any(
        not _NOTE_BEFORE_RE.search(text[max(0, hit.start() - 12): hit.start()])
        for hit in re.finditer(rf"(?<=[a-z0-9,;] ){re.escape(proper)}\b", text)
    )
    if not recurs:
        return None  # a topic word, or only a note reference ("Note 23 Borrowings")
    return " ".join(w.capitalize() for w in words)


def _section_scope(heading: str | None, aliases: frozenset[str], text: str) -> str | None:
    if not heading:
        return None
    for part in reversed([p for p in re.split(r"\s*(?:>|›|»|\|)\s*", heading) if p]):
        part = part.strip()
        if _caps_heading_line(part):
            scope = _scope_heading(part, aliases, text)
            if scope:
                return scope
    return None


def _split_sentences(text: str, start: int, end: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    cursor = start
    for match in _SENTENCE_END_RE.finditer(text, start, end):
        before = text[cursor: match.start() + 1]
        word = re.search(r"([\w.&'’-]+)[.!?]$", before)
        token = (word.group(1) if word else "").lower().rstrip(".")
        if token in _ABBREVIATIONS or re.fullmatch(r"(?:[a-z]\.){0,3}[a-z]", token):
            continue
        out.append((cursor, match.start() + 1))
        cursor = match.end()
    out.append((cursor, end))
    return out


def _split_clauses(text: str, start: int, end: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    cursor = start
    for match in _CLAUSE_SPLIT_RE.finditer(text, start, end):
        out.append((cursor, match.start()))
        cursor = match.end()
    out.append((cursor, end))
    return out


# ── Matching ─────────────────────────────────────────────────────────────── #


def _figures(text: str) -> list[str]:
    out = []
    for raw in _FIGURE_RE.findall(text):
        value = raw.replace(",", "").rstrip(".")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        if value:
            out.append(value)
    return out


def _words(text: str, exclude: "set[str]") -> set[str]:
    out = set()
    for raw in _WORD_RE.findall(text):
        word = re.sub(r"['’]s$", "", _folded(raw))
        if len(word) >= 4 and word not in _STOPWORDS and word not in exclude:
            out.add(word)
    return out


def _match_score(statement: str, segment: str, exclude: "set[str]") -> int:
    stated = _figures(statement)
    seen = set(_figures(segment))
    score = 0
    for figure in set(stated):
        if figure in seen:
            is_year = len(figure) == 4 and figure.isdigit() and 1900 <= int(figure) <= 2100
            score += 1 if is_year or len(figure) == 1 else 3
    score += len(_words(statement, exclude) & _words(segment, exclude))
    return score


# ── Resolution ───────────────────────────────────────────────────────────── #


def _owner_only(passage: Passage, reader: _Reader | None, scope: str | None) -> PassageAttribution:
    entity = reader.owner_entity if reader else None
    owner = reader.owner if reader else passage.owner
    names = tuple(e.name for e in reader.entities if e.hint != SUBJECT) if reader else ()
    if owner == OWNER_SUBJECT:
        return PassageAttribution(
            passage.citation_id, owner, passage.owner_name, names, None,
            _passage_subject_name(reader, passage), SUBJECT, scope,
        )
    if owner == OWNER_OTHER and entity is not None:
        return PassageAttribution(
            passage.citation_id, owner, entity.name, names, None, entity.name,
            entity.relationship if entity.relationship != UNKNOWN else OTHER_ISSUER,
        )
    return PassageAttribution(passage.citation_id, owner, None, names, None, None, UNKNOWN)


def _passage_subject_name(reader: _Reader | None, passage: Passage) -> str:
    return reader.subj.name if reader else (passage.owner_name or "the issuer")


def attribute_passage(
    statement: str, passage: Passage, subject: SubjectIdentity
) -> PassageAttribution:
    """Whose the passage sentence that a statement rests on is. Pure; the caller
    treats an exception as unknown."""
    aliases = subject_aliases(subject.name)
    if not aliases:
        # The subject cannot be told apart from anyone else: no verdict.
        return PassageAttribution(passage.citation_id, passage.owner, None, (), None, None, UNKNOWN)
    if not passage.analysable or not passage.text.strip():
        return _owner_only(passage, None, None)
    if len(passage.text) > _MAX_PASSAGE_CHARS:
        passage = replace(passage, text=passage.text[:_MAX_PASSAGE_CHARS])
    reader = _Reader(passage, subject, aliases)
    reader.read()
    section_scope = _section_scope(passage.heading, aliases, passage.text)
    spans, headings = reader.segments()
    if not spans:
        return _owner_only(passage, reader, section_scope)

    exclude = set(aliases)
    from app.services.discovery.identity import name_tokens

    for entity in reader.entities:
        exclude.update(name_tokens(entity.name))
        exclude.update(_folded(a) for a in entity.aliases)
    scores = [_match_score(statement, reader.text[s:e], exclude) for s, e in spans]
    best = max(range(len(spans)), key=lambda i: (scores[i], -i))
    if scores[best] < _MIN_MATCH_SCORE:
        return _owner_only(passage, reader, section_scope)

    # In the subject's own document a sentence that names no one is the subject's: an
    # entity named earlier never becomes the topic it inherits (the R&D line after an
    # investor's investment, or after "Plantyx, our former subsidiary, was
    # deconsolidated", is the filer's), unless a heading governs the section or the
    # entity reports separately (ADR-066). A former subsidiary still reaches a later
    # sentence through a pronoun ("its seed production techniques"), its defined alias
    # or a "Risks Related to <Name>" heading — never through a sentence with no
    # reference (re-review 2: missing data is acceptable, wrong data is not).
    own_document = reader.owner == OWNER_SUBJECT
    topic: _Entity | None = reader.heading_entity or reader.owner_entity
    last_subject: _Entity | None = None
    resolved_entity: _Entity | None = None
    explicit = False
    previous_start = -1
    topic_before: _Entity | None = topic
    anchored = False
    for index, (start, end) in enumerate(spans[: best + 1]):
        for pos, _scope, heading_entity in headings:
            if previous_start < pos <= start and heading_entity is not None:
                last_subject = heading_entity
                if heading_entity.relationship not in _RELATIONAL:
                    topic = heading_entity
        previous_start = start
        seg_mentions = [m for m in reader.mentions if start <= m.start < end and not m.masked]
        seg_entity, grammatical, named = _resolve_segment(
            reader, start, seg_mentions, topic, last_subject
        )
        if seg_entity is reader.subj:
            topic = last_subject = reader.subj
        elif grammatical is not None:
            last_subject = grammatical
            if grammatical.relationship not in _RELATIONAL and (
                not own_document or grammatical.separate
            ):
                topic = grammatical
        if index == best:
            resolved_entity = seg_entity if seg_entity is not None else topic
            if (
                own_document
                and seg_entity is None
                and resolved_entity is not None
                and resolved_entity.relationship == FORMER_SUBSIDIARY
                and _SEPARATION_RE.search(reader.text[start:end])
            ):
                # The separation itself ("A gain of $12 million was recognised on
                # deconsolidation") is the filer's accounting, not the leaver's.
                resolved_entity = reader.owner_entity
            explicit = named
            anchored = (
                own_document
                or topic_before is reader.subj
                or _subject_frame(reader, start, end)
            )
        topic_before = topic

    seg_start, seg_end = spans[best]
    sentence = reader.text[seg_start:seg_end]
    inline = [scope for pos, scope, _entity in headings if pos <= seg_start]
    scope = inline[-1] if inline else section_scope
    weak_group = bool(_GROUP_WORDING_RE.search(sentence) or _FIRST_PERSON_RE.search(sentence))
    if scope and (
        _GROUP_STRONG_RE.search(sentence)
        or (weak_group and not _scope_is_head(scope, sentence))
    ):
        # A heading never narrows a sentence that speaks for the Group ("Group copper
        # production", "Total company sales", "Calderon's consolidated sales", "Net
        # debt") or cites a note; "total / company / we / our" un-scope it unless the
        # heading's mine or segment is the sentence's locative or possessive head
        # ("Total copper production at Centinela", "Centinela's output", "Centinela
        # produced") — not a part of a total ("of which Centinela contributed",
        # "including Los Pelambres", "with the Americas contributing").
        scope = None
    names = tuple(e.name for e in reader.entities if e.hint != SUBJECT)
    if resolved_entity is None:
        return PassageAttribution(
            passage.citation_id, reader.owner, None, names, best, None, UNKNOWN
        )
    if resolved_entity is reader.subj:
        return PassageAttribution(
            passage.citation_id, reader.owner,
            reader.owner_entity.name if reader.owner_entity else None,
            names, best, reader.subj.name, SUBJECT, scope,
        )
    relationship = resolved_entity.relationship
    if relationship == UNKNOWN:
        relationship = OTHER_ISSUER
    framed = (
        anchored
        and relationship in _FRAMEABLE
        and not resolved_entity.separate
        and _framed_to_subject(reader, seg_start, seg_end)
    )
    contained = False
    if not framed and relationship not in SCOPED_RELATIONSHIPS:
        transaction = bool(_TRANSACTION_RE.search(sentence)) and not resolved_entity.separate
        if own_document:
            # The subject's own document: a counterparty, peer, regulator or other
            # company it discusses is context for the subject's fact. Only a former
            # subsidiary, a separately reporting namesake, or a named entity's own
            # reported results (a table row of its figures) are someone else's.
            own_results = (
                explicit
                and relationship in _NON_PERIMETER
                and (
                    bool(_OWN_RESULTS_RE.search(sentence))
                    or _table_row(reader, seg_start, seg_end, resolved_entity)
                )
            )
            if relationship in _FRAMEABLE | {PEER, REGULATOR} and transaction:
                framed = True
            elif not (
                relationship == FORMER_SUBSIDIARY or resolved_entity.separate or own_results
            ):
                contained = True
        elif reader.owner == OWNER_UNKNOWN and transaction and relationship != FORMER_SUBSIDIARY:
            # A page that names the subject, and another entity investing in, paying,
            # lending to or buying from someone it does not name: the subject (ADR-083
            # review M4). Without the subject's name it is nobody's we can tell.
            if _names_subject(reader) and not _other_named_besides(
                reader, seg_start, seg_end, resolved_entity
            ):
                framed = True
            else:
                contained = True
    return PassageAttribution(
        passage.citation_id, reader.owner,
        reader.owner_entity.name if reader.owner_entity else None,
        names, best, resolved_entity.name, relationship,
        resolved_entity.name if relationship in SCOPED_RELATIONSHIPS else None,
        framed=framed,
        contained=contained,
    )


#: The words before a heading noun that make it a part of a total, not the head.
_PART_OF_TOTAL_RE = re.compile(
    r"\b(?:of[ \t]+which|including|includes|included|with|excluding|excludes|"
    r"other[ \t]+than|except(?:[ \t]+for)?|besides|plus)[ \t]+(?:the[ \t]+)?$",
    re.IGNORECASE,
)
#: The words before a heading noun that make it the locative head ("at Centinela", "on
#: the Centinela Second Concentrator").
_LOCATIVE_RE = re.compile(r"\b(?:at|on|in|from|for)[ \t]+(?:the[ \t]+)?$", re.IGNORECASE)


def _scope_is_head(scope: str, sentence: str) -> bool:
    """The heading's mine / segment is the sentence's locative or possessive head:
    "…at Centinela…", "…on the Centinela Second Concentrator…", "Centinela's…", or the
    sentence's grammatical subject ("Centinela produced…")."""
    phrase = r"[ \t]+".join(re.escape(w) for w in scope.split())
    for hit in re.finditer(rf"(?<![\w&]){phrase}(?![\w&])", sentence, re.IGNORECASE):
        before = sentence[max(0, hit.start() - 30): hit.start()]
        if _PART_OF_TOTAL_RE.search(before):
            continue
        after = sentence[hit.end(): hit.end() + 3]
        if (
            _LOCATIVE_RE.search(before)
            or after.startswith(("'s", "’s"))
            or re.fullmatch(r"[ \t]*(?:[Tt]he[ \t]+)?", sentence[: hit.start()])
        ):
            return True
    return False


def _names_subject(reader: _Reader) -> bool:
    return any(m.entity is reader.subj for m in reader.mentions)


def _other_named_besides(reader: _Reader, start: int, end: int, entity: _Entity) -> bool:
    return any(
        start <= m.start < end and m.entity is not None and m.entity not in (entity, reader.subj)
        for m in reader.mentions
    )


def _table_row(reader: _Reader, start: int, end: int, entity: _Entity) -> bool:
    """A clause that is only a name and its figures ("Brindle Mining Corp US$3,120m"):
    that entity's own number, wherever the table is printed."""
    rest = reader.text[start:end]
    for mention in sorted(
        (m for m in reader.mentions if start <= m.start < end and m.entity is entity),
        key=lambda m: m.start,
        reverse=True,
    ):
        rest = rest[: mention.start - start] + " " + rest[mention.end - start:]
    if not _FIGURE_RE.search(rest):
        return False
    return all(w.lower() in _UNIT_WORDS for w in re.findall(r"[A-Za-z]{3,}", rest))


def _subject_frame(reader: _Reader, start: int, end: int) -> bool:
    """A masked reference to the subject in the sentence: the anchor of a role phrase
    ("our largest customer", "a lender to the Company", "Lindian's offtaker")."""
    for mention in reader.mentions:
        if not (start <= mention.start < end and mention.masked):
            continue
        if mention.entity is reader.subj:
            return True
        if mention.implicit == _DEFINED and mention.term in _SELF_TERMS:
            return reader.owner != OWNER_OTHER
        if mention.implicit == _FIRST and reader.owner == OWNER_SUBJECT:
            return True
    return False


def _framed_to_subject(reader: _Reader, start: int, end: int) -> bool:
    """The sentence states the relationship with the subject, not the other entity's own
    facts. A relational predicate admits it; failing that, a role frame back to the
    subject admits it unless the sentence reports the entity's own results."""
    sentence = reader.text[start:end]
    if _RELATIONAL_PREDICATE_RE.search(sentence):
        return True
    return _subject_frame(reader, start, end) and not _OWN_FACT_RE.search(sentence)


def _resolve_segment(
    reader: _Reader,
    start: int,
    mentions: "list[_Mention]",
    topic: _Entity | None,
    last_subject: _Entity | None,
) -> tuple[_Entity | None, _Entity | None, bool]:
    """(the segment's entity, its grammatical subject's entity, named explicitly).

    The segment is the subject's when ANY unmasked reference in it resolves to the
    subject ("Iluka will lend the Company A$140m" is the subject's loan). Otherwise it
    is its grammatical subject's: the first reference in its first words that does not
    follow a preposition ("Revenue from Vexmoor Inc." is not Vexmoor's sentence).
    """
    resolved: list[tuple[_Mention, _Entity | None]] = []
    for i, mention in enumerate(mentions):
        resolved.append(
            (mention, _resolve_mention(reader, mention, mentions[:i], topic, last_subject))
        )
    if any(entity is reader.subj for _m, entity in resolved):
        return reader.subj, reader.subj, True
    text = reader.text
    for mention, entity in resolved:
        words_before = re.findall(r"[\w'’-]+", text[start: mention.start])
        if len(words_before) > _SUBJECT_WINDOW_WORDS:
            break
        if words_before and words_before[-1].lower() in _PREPOSITIONS:
            continue
        if entity is not None:
            return entity, entity, mention.entity is not None
    return None, None, False


def _resolve_mention(
    reader: _Reader,
    mention: _Mention,
    earlier: "list[_Mention]",
    topic: _Entity | None,
    last_subject: _Entity | None,
) -> _Entity | None:
    if mention.entity is not None:
        return mention.entity
    if mention.implicit == _FIRST:
        return reader.owner_entity
    if mention.implicit == _DEFINED:
        for entity in [reader.subj, *reader.entities]:
            if mention.term in entity.defined_terms:
                return entity
        if reader.owner == OWNER_SUBJECT:
            # In the subject's own document "the Company" / "the Group" is the filer.
            return reader.subj
    named_before = [m.entity for m in earlier if m.entity is not None]
    if named_before:
        return named_before[-1]
    if mention.implicit == _PRONOUN and last_subject is not None:
        return last_subject
    return topic


# ── Statement level ──────────────────────────────────────────────────────── #


def attribute_statement(
    statement: str, passages: "Sequence[Passage]", subject: SubjectIdentity
) -> StatementAttribution:
    """The statement's attribution across every passage it cites.

    * any passage supports the subject → ``subject`` (the finding stands as today);
    * else any passage is the subject's perimeter at a narrower scope → ``scoped`` (one
      scope; two different ones are a ``conflict``: ``unknown``, kept unscoped);
    * else any passage is unresolved → ``unknown`` (kept, fail-safe);
    * else every passage is another named entity's → ``other`` (dropped by the caller).
    """
    results = tuple(attribute_passage(statement, p, subject) for p in passages)
    if not results:
        return StatementAttribution(VERDICT_UNKNOWN)
    verdicts = [r.verdict for r in results]
    if VERDICT_SUBJECT in verdicts:
        return StatementAttribution(VERDICT_SUBJECT, results)
    scoped: dict[str, str] = {}
    for result in results:
        if result.verdict == VERDICT_SCOPED and result.scope_name:
            scoped.setdefault(result.scope_name.casefold(), result.scope_name)
    if len(scoped) > 1:
        # Two narrower scopes (two mines, two subsidiaries): no one scope can be
        # stamped, and none of them is grounds to discard — kept, unscoped.
        return StatementAttribution(VERDICT_UNKNOWN, results, conflict=True)
    if scoped:
        (name,) = scoped.values()
        return StatementAttribution(VERDICT_SCOPED, results, scope_name=name)
    if VERDICT_UNKNOWN in verdicts:
        return StatementAttribution(VERDICT_UNKNOWN, results)
    others: dict[str, tuple[str, str]] = {}
    for result in results:
        name = _clean_name(result.subject_entity or "")
        if name:
            others.setdefault(name.casefold(), (name, result.relationship))
    if not others:
        return StatementAttribution(VERDICT_UNKNOWN, results)
    return StatementAttribution(
        VERDICT_OTHER, results, others=tuple(others[k] for k in sorted(others))
    )


__all__ = [
    "COUNTERPARTY",
    "CUSTOMER",
    "FORMER_SUBSIDIARY",
    "JOINT_VENTURE",
    "OTHER_ENTITY_RELATIONSHIPS",
    "OTHER_ISSUER",
    "OWNER_OTHER",
    "OWNER_SUBJECT",
    "OWNER_UNKNOWN",
    "PEER",
    "REGULATOR",
    "RELATIONSHIPS",
    "RELATIONSHIP_LABELS",
    "SCOPED_RELATIONSHIPS",
    "SUBJECT",
    "SUBSIDIARY",
    "SUPPLIER",
    "UNKNOWN",
    "VERDICT_OTHER",
    "VERDICT_SCOPED",
    "VERDICT_SUBJECT",
    "VERDICT_UNKNOWN",
    "Passage",
    "PassageAttribution",
    "StatementAttribution",
    "SubjectIdentity",
    "attribute_passage",
    "attribute_statement",
    "document_owner",
    "passage_text",
    "subject_aliases",
]
