"""Material corporate events need a vetted source before a summary states them (ADR-078).

WHY THIS EXISTS
===============
QA campaign 2026-11, PayPal rerun 39905de8. The executive synthesis said "a recent
acquisition offer was rejected by the board as inadequate". Its only finding cited ONE
unclassified web page (source kind ``secondary_web``, class ``unknown_web``): no 8-K, no
SC 14D9, nothing in either 10-Q, no press. The editor's checks are on citations,
figures and prohibited language — a sentence with a label, no digit and no rating word
passed all of them — and the chair's on citation handles and labels. Nothing asked
whether the SOURCE could carry the claim.

THE RULE
========
A *material corporate event* about the subject — a takeover approach, offer or bid for
it (and its acceptance or rejection), a pending merger, going private, insolvency or
administration, delisting, the CEO leaving, the CFO leaving, fraud, a restatement or a
regulatory enforcement action — may be stated in the executive synthesis, a section
lead, or the chair's synthesis and key points only when a finding resting on a VETTED
source states an event of the SAME type: an issuer filing or the issuer's own release
(``issuer_filing``, ``issuer_ir``), a regulator (``government_or_regulator``), a filing
on an official filing system (``third_party_filing``) or established press
(``quality_media`` — the T4 tier: major financial, trade and local press). Otherwise the
sentence or key point is EXCLUDED — the same "dropped, not repaired" rule the editor
already uses. A sentence citing ANY finding that states an event on unvetted sources only
is excluded too, unless another cited, vetted finding states that same event type.

The finding itself stays in the ledger and in its section, with whatever trust label it
carries: the research is not deleted, the summary just does not promote it to fact.

DETECTION IS DETERMINISTIC AND BOUNDED
======================================
A word-bounded vocabulary of event PHRASES, never a bare noun. "Offer", "acquired" and
"fraud" alone are ordinary words in a payments or bank report ("offers Buy Now Pay
Later", "acquired 440M active accounts", "customer acquisition cost", "fraud losses");
only "offer FOR <subject>", "acquisition offer", "rejected … offer", "to be acquired",
"filed for bankruptcy", "CEO … stepped down", "enforcement action" and the like count.
An acquisition BY the subject ("PayPal acquired Xoom in 2015", "the company completed
its merger with X") is not an event about the subject. For insolvency, delisting and
going private the subject must be the one acting ("PayPal filed for Chapter 11"), not a
customer or supplier the statement mentions. Every window is bounded, so the cost is
linear in the statement's length.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

#: Source kinds (``director.contracts``) that can carry a material event: the issuer's own
#: word, a regulator, an official filing system, T4 press.
VETTED_SOURCE_KINDS: frozenset[str] = frozenset(
    {
        "issuer_filing",
        "issuer_ir",
        "government_or_regulator",
        "third_party_filing",
        "quality_media",
    }
)

EV_TAKEOVER = "takeover"
EV_GOING_PRIVATE = "going_private"
EV_INSOLVENCY = "insolvency"
EV_DELISTING = "delisting"
#: The CEO and the CFO are different events: a filed CEO succession does not vouch for a
#: rumoured CFO ouster.
EV_CEO_DEPARTURE = "ceo_departure"
EV_CFO_DEPARTURE = "cfo_departure"
EV_MISCONDUCT = "fraud_restatement_or_enforcement"
EVENT_TYPES: frozenset[str] = frozenset(
    {
        EV_TAKEOVER, EV_GOING_PRIVATE, EV_INSOLVENCY, EV_DELISTING, EV_CEO_DEPARTURE,
        EV_CFO_DEPARTURE, EV_MISCONDUCT,
    }
)

#: The reason a summary sentence or key point was dropped under this rule.
REASON = "material_event_unvetted_source"
MAX_SCAN_CHARS = 8_000

_I = re.IGNORECASE

# ── The subject ─────────────────────────────────────────────────────────── #

#: Legal suffixes and words too generic to identify a company on their own: "Southern
#: Copper" is the subject, "copper" in "copper smelters" is not.
_GENERIC_NAME_WORDS: frozenset[str] = frozenset(
    {
        "inc", "incorporated", "corp", "corporation", "co", "company", "plc", "ltd",
        "limited", "holdings", "holding", "group", "sa", "ag", "nv", "se", "spa", "llc",
        "lp", "asa", "ab", "oyj", "gmbh", "the", "and", "of", "class", "ordinary",
        "shares", "adr", "compagnie", "financiere", "financière", "international",
        "global", "national", "american", "southern", "northern", "eastern", "western",
        "united", "general", "first", "resources", "resource", "mining", "minerals",
        "metals", "materials", "copper", "gold", "silver", "lithium", "nickel", "iron",
        "rare", "earths", "earth", "energy", "power", "petroleum", "oil", "gas", "bank",
        "financial", "capital", "partners", "industries", "technologies", "technology",
        "systems", "therapeutics", "pharmaceuticals", "biosciences", "health", "services",
        "solutions", "brands", "foods",
    }
)
_NAME_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ][\wÀ-ÿ\-]*")


def subject_names(subject: Mapping[str, Any] | None) -> tuple[tuple[str, ...], str | None]:
    """``(names, ticker)``: the subject's core name (legal suffixes removed) and its
    distinctive words (at least four letters, not generic), plus its ticker."""
    if not subject:
        return (), None
    words = _NAME_WORD_RE.findall(str(subject.get("name") or ""))
    core = [w for w in words if w.casefold() not in {
        "inc", "incorporated", "corp", "corporation", "co", "plc", "ltd", "limited",
        "holdings", "holding", "sa", "ag", "nv", "se", "spa", "llc", "lp", "asa", "ab",
        "oyj", "gmbh", "class", "adr",
    }]
    names: list[str] = []
    if len(core) >= 2:
        names.append(" ".join(core))
    names += [w for w in core if len(w) >= 4 and w.casefold() not in _GENERIC_NAME_WORDS]
    ticker = str(subject.get("ticker") or "").strip() or None
    return tuple(dict.fromkeys(names)), ticker


#: Who a statement may be about, as a regex: the subject's names, its ticker (case
#: kept), or a phrase that stands for it.
_PRONOUN = r"the\s+(?:company|group|issuer|firm)"
_BOARD = r"(?:the|its)\s+board"
_NOT_POSSESSIVE = r"\b(?!['’])"
_COUNTERPARTY_RE = re.compile(
    r"\b(?:customers?|clients?|merchants?|suppliers?|vendors?|counterpart(?:y|ies)|"
    r"borrowers?|tenants?|lessees?|partners?|competitors?|peers?|rivals?|smelters?|"
    r"refiners?|retailers?|distributors?|lenders?|banks?|units?|subsidiar(?:y|ies)|"
    r"divisions?|ventures?|investees?|airlines?|operators?)\b",
    _I,
)


@dataclass(frozen=True)
class _Subject:
    names: tuple[str, ...]
    ticker: str | None
    #: The subject itself (names, ticker, "the company"), possessive or not.
    ref: re.Pattern[str]
    #: The subject or its board, as the one that rejects or accepts.
    actor: re.Pattern[str]
    alt: str


@lru_cache(maxsize=256)
def _compile(names: tuple[str, ...], ticker: str | None) -> _Subject:
    parts = [r"\b" + re.escape(n).replace(r"\ ", r"\s+") + r"\b" for n in names]
    if ticker and len(ticker) >= 2:
        parts.append(r"(?<![A-Za-z0-9])(?-i:" + re.escape(ticker) + r")(?![A-Za-z0-9])")
    named = "|".join(parts) if parts else r"(?!x)x"
    alt = rf"(?:{named}|{_PRONOUN})"
    return _Subject(
        names=names,
        ticker=ticker,
        ref=re.compile(alt, _I),
        actor=re.compile(rf"(?:(?:{named})['’]s\s+board|{_BOARD}|{alt})", _I),
        alt=alt,
    )


def _subject(subject: Mapping[str, Any] | None) -> _Subject:
    return _compile(*subject_names(subject))


def _names_subject(text: str, s: _Subject) -> bool:
    """Does ``text`` NAME the subject (a name or the ticker, not "the company")?"""
    return any(
        not re.match(_PRONOUN, match.group(0), _I) for match in s.ref.finditer(text)
    )


def _about_subject(text: str, s: _Subject) -> bool:
    return bool(s.actor.search(text))


def _actor_before(text: str, start: int, s: _Subject) -> bool:
    """Is the subject the one acting at ``start``? The nearest subject reference in the
    60 characters before, in the same clause, not possessive, with no customer, supplier
    or peer between it and the event."""
    window = text[max(0, start - 60): start]
    cut = max(window.rfind(";"), window.rfind(". "))
    if cut >= 0:
        window = window[cut + 1:]
    last = None
    for match in s.actor.finditer(window):
        last = match
    if last is None:
        return False
    if re.match(r"['’]s\b", window[last.end():]) and not last.group(0).lower().endswith(
        "board"
    ):
        return False
    return not _COUNTERPARTY_RE.search(window[last.end():])


# ── Vocabulary ─────────────────────────────────────────────────────────── #

#: An offer for a PART of the company — a unit, a stake, receivables — is not a takeover.
_PART_SALE_RE = re.compile(
    r"\bfor\s+(?:its|the|a|their)\s+(?:[\w-]+\s+){0,3}?(?:units?|business(?:es)?|"
    r"divisions?|subsidiar(?:y|ies)|stake|assets?|portfolio|receivables|notes|bonds|"
    r"debt|loans?|operations?|brand)\b",
    _I,
)
_OFFER_NOUN = r"(?:offer|bid|approach(?!\s+to\s+(?!acquire|buy|purchase|take)))"
_VERDICT = r"(?:rejected|rebuffed|spurned|turned\s+down|accepted|refused|rejects|accepts|rebuffs)"
_TAKEOVER_MODIFIER_RE = re.compile(
    r"\b(?:takeover|take-over|acquisition|buyout|buy-out|merger|unsolicited|hostile|"
    r"all-cash|per-share|indicative|non-binding)\b|[$€£]\s?\d|\bbillion\b|\bper[\s-]share\b",
    _I,
)
#: Phrases that are takeover events when the statement is about the subject.
_TAKEOVER_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:takeover|take-over)\s+(?:target|interest|speculation|talks|battle|approach|"
        r"offer|bid|proposal)\b|\bscheme\s+of\s+arrangement\b", _I,
    ),
    re.compile(
        r"\b(?:unsolicited|hostile|non-binding|indicative|all-cash|per-share|acquisition|"
        r"merger|buyout|buy-out)\s+(?:offer|bid|proposal|approach)\b", _I,
    ),
    # A PENDING merger. "Completed its merger with X" is the subject acquiring X.
    re.compile(
        r"(?<!its\s)\bmerger\s+(?:agreement|of\s+equals|talks|proposal)\b|"
        r"\b(?:agreed|agrees|plans?|proposes?|proposed)\s+to\s+merge\b|"
        r"\b(?:to|will|would)\s+merge\s+with\b|\bmerging\s+with\b|"
        r"\b(?:proposed|potential|pending|planned|possible|announced|rumou?red|reported)\s+"
        r"merger\b", _I,
    ),
    re.compile(
        r"\b(?:explor\w*|weigh\w*|consider\w*|review\w*|evaluat\w*)\s+(?:a\s+)?"
        r"(?:(?:potential|possible)\s+)?(?:sale\b(?!\s+of\s+(?:its|the|a|their)\b)|"
        r"strategic\s+alternatives)|\bsell\s+itself\b", _I,
    ),
)
#: Phrases whose OBJECT must be the subject: "offer for PayPal", "talks to buy PayPal".
_TARGETED_TEMPLATES: tuple[str, ...] = (
    r"\b(?:offer|bid|approach|proposal)\s+(?:for|to\s+(?:acquire|buy|purchase|take\s+over))"
    r"\s+{S}",
    r"(?<!its\s)(?<!their\s)\b(?:acquisition|takeover|purchase|buyout|sale)\s+of\s+{S}",
    r"\b(?:acquire|acquiring|take\s+over|taking\s+over|take\s+private|taking\s+private)\s+"
    r"{S}",
    r"\b(?:in\s+talks|seeks?|seeking|sought|plans?|planning|planned|bids?|bidding|"
    r"offer(?:ed|s|ing)?|propos(?:ed|es|ing)|move[sd]?|wants?|wanted|aims?|aimed|"
    r"considering|exploring|agreed|agrees|tried|trying|attempt(?:s|ed|ing)?)\s+to\s+"
    r"(?:buy|acquire|take\s+over|purchase|take\s+private)\s+{S}",
    r"\btak(?:e|es|en|ing)\s+{S}\s+private\b",
    r"\bdelisting\s+of\s+{S}",
)
#: "offered $53B for PayPal": an offer with a price, for the subject.
_OFFERED_FOR = r"\boffer(?:ed|s|ing)?\s+(?P<gap>.{0,60}?)\bfor\s+{S}"
_MONEY_RE = re.compile(r"[$€£]|\d|\b(?:billion|million|cash|per\s+share|premium)\b", _I)
_PASSIVE_ACQUIRED_RE = re.compile(
    r"\b(?P<aux>to\s+be|will\s+be|would\s+be|could\s+be|may\s+be|is\s+being|being|was|"
    r"has\s+been|had\s+been)\s+(?:acquired|bought|taken\s+over|taken\s+private)\b"
    r"(?:\s+by\s+(?P<agent>[^,;]{1,60}))?",
    _I,
)
_GOING_PRIVATE_RE = re.compile(
    r"\b(?:go(?:es|ing)?|went|gone|tak(?:e|es|en|ing)|took)[\s-]+private\b"
    r"(?![\s-]+(?:label|equity|credit|sector|markets?|banking|bank|placement|clients?|"
    r"wealth|cloud|network|investors?|compan(?:y|ies)|lenders?|debt|capital|brands?))|"
    r"\b(?:leveraged|management)\s+buy-?out\b|\btake-private\b",
    _I,
)
_INSOLVENCY_RE = re.compile(
    r"\b(?:fil(?:ed|es|ing|e)\s+for\s+(?:bankruptcy|chapter\s+(?:7|11|15)|insolvency|"
    r"creditor\s+protection)|"
    r"(?:declared|declares|is|was|went|became|becomes|become)\s+(?:bankrupt|insolvent)|"
    r"(?:entered|enters|entering|placed\s+(?:in|into)|went\s+into|go(?:es|ing)?\s+into|"
    r"put\s+into|seeking|sought)\s+(?:voluntary\s+)?(?:administration|liquidation|"
    r"receivership|bankruptcy\s+protection|chapter\s+11(?:\s+protection)?)|"
    r"(?:administrators?|liquidators?|receivers?)\s+(?:were\s+|have\s+been\s+|has\s+been\s+"
    r"|was\s+)?appointed)\b",
    _I,
)
_DELISTING_RE = re.compile(
    r"\b(?:de-?list(?:ed|ing|s)?)\b(?!\s+(?:its|the|their)\s+(?:[\w-]+\s+){0,2}?"
    r"(?:preferred|notes|bonds|debentures|warrants|debt|depositary|series))|"
    r"\b(?:cancellation|cancelation)\s+of\s+(?:the\s+|its\s+)?(?:listing|admission)\b",
    _I,
)
_CEO_RE = re.compile(r"\b(?:CEO|chief\s+executive(?:\s+officer)?|managing\s+director)\b", _I)
_CFO_RE = re.compile(r"\b(?:CFO|chief\s+financial\s+officer|finance\s+director)\b", _I)
_DEPARTURE_RE = re.compile(
    r"\b(?:resign(?:s|ed|ing|ation)?|depart(?:s|ed|ing|ure)|step(?:s|ped|ping)?\s+down|"
    r"ousted|oust(?:s|ing)?|fired|dismissed|sacked|"
    r"(?:to|will|would|is|was)\s+(?:leave|leaving)|left\s+the\s+(?:company|group|board)|"
    r"retire(?:s|d)?|retirement|quit(?:s)?|(?:was|been|be)\s+replaced|"
    r"succeed(?:s|ed|ing)|successor)\b",
    _I,
)
#: "Stripe's CEO" — a role owned by a NAMED company, checked against the subject.
_ROLE_OWNER_RE = re.compile(r"([A-Z][\w&.\-]*)['’]s\s+(?:\w+\s+)?$")
_MISCONDUCT_RE = re.compile(
    r"\b(?:(?:accounting|securities|wire|financial|corporate)\s+fraud|"
    r"fraud\s+(?:charges?|allegations?|investigation|probe|indictment|conviction)|"
    r"charged\s+with\s+fraud|(?:accused|alleged|allegedly)\s+(?:of\s+)?(?:committing\s+)?"
    r"fraud|"
    r"restatement|(?:will|to|must|had\s+to|agreed\s+to|plans\s+to)\s+restate|"
    r"non-reliance|"
    r"enforcement\s+(?:action|proceedings?)|consent\s+order|cease[\s-]and[\s-]desist|"
    r"deferred\s+prosecution\s+agreement|civil\s+(?:money\s+)?penalty|"
    r"(?:SEC|FCA|CFPB|DOJ|FTC|FINRA|OCC|ASIC|ESMA|BaFin|AMF|Department\s+of\s+Justice|"
    r"regulators?|prosecutors?)\b.{0,60}\b(?:charged|fined|sued|penali[sz]ed|sanctioned|"
    r"subpoena(?:ed|s)?|launched\s+an?\s+(?:investigation|probe)|opened\s+an?\s+"
    r"(?:investigation|probe)))\b",
    _I,
)


@lru_cache(maxsize=256)
def _targeted(
    alt: str, actor: str
) -> tuple[tuple[re.Pattern[str], ...], re.Pattern[str], re.Pattern[str]]:
    subject = alt + _NOT_POSSESSIVE
    templates = tuple(re.compile(t.replace("{S}", subject), _I) for t in _TARGETED_TEMPLATES)
    offered = re.compile(_OFFERED_FOR.replace("{S}", subject), _I)
    # The subject, "the company" or its board as the one that rejects or accepts.
    rejecter = re.compile(
        actor + r"[^.;]{0,40}?\b" + _VERDICT + r"\b.{0,60}?\b" + _OFFER_NOUN + r"\b", _I
    )
    return templates, offered, rejecter


_PASSIVE_VERDICT_RE = re.compile(
    r"\b" + _OFFER_NOUN + r"\b.{0,80}?\b(?:was|were|been|is|being)\s+" + _VERDICT + r"\b",
    _I,
)


def _takeover(text: str, s: _Subject) -> bool:
    templates, offered, rejecter = _targeted(s.alt, s.actor.pattern)
    if any(t.search(text) for t in templates):
        return True
    if any(_MONEY_RE.search(m.group("gap") or "") for m in offered.finditer(text)):
        return True
    for match in _PASSIVE_ACQUIRED_RE.finditer(text):
        agent = match.group("agent")
        if agent is None and match.group("aux").lower() in {"was", "has been", "had been"}:
            continue  # "the portfolio was acquired" — history, no acquirer named
        if agent is not None and _names_subject(agent, s):
            continue  # "Xoom was acquired by PayPal": an acquisition BY the subject
        if _actor_before(text, match.start(), s):
            return True
    part_sale = bool(_PART_SALE_RE.search(text))
    if not part_sale and rejecter.search(text):
        return True
    if not part_sale and (
        (re.search(r"\bboard\b", text, _I) or _TAKEOVER_MODIFIER_RE.search(text))
        and _PASSIVE_VERDICT_RE.search(text)
    ):
        return True
    return any(r.search(text) for r in _TAKEOVER_RES) and _about_subject(text, s)


def _departures(text: str, s: _Subject) -> set[str]:
    out: set[str] = set()
    for role_re, event in ((_CEO_RE, EV_CEO_DEPARTURE), (_CFO_RE, EV_CFO_DEPARTURE)):
        for role in role_re.finditer(text):
            window = text[max(0, role.start() - 80): role.end() + 80]
            if not _DEPARTURE_RE.search(window):
                continue
            owner = _ROLE_OWNER_RE.search(text[max(0, role.start() - 40): role.start()])
            if owner and not _names_subject(owner.group(1), s):
                continue  # another company's CEO
            out.add(event)
            break
    return out


def _acting(pattern: re.Pattern[str], text: str, s: _Subject) -> bool:
    return any(_actor_before(text, m.start(), s) for m in pattern.finditer(text))


def event_types(text: str | None, subject: Mapping[str, Any] | None = None) -> frozenset[str]:
    """Every material-event type ``text`` asserts about the subject."""
    # Statements are stored at most 2,000 characters and a chair synthesis 4,000: the
    # bound keeps every scan linear AND small.
    body = str(text or "")[:MAX_SCAN_CHARS]
    if not body.strip():
        return frozenset()
    s = _subject(subject)
    out: set[str] = set()
    if _takeover(body, s):
        out.add(EV_TAKEOVER)
    if _acting(_GOING_PRIVATE_RE, body, s):
        out.add(EV_GOING_PRIVATE)
    if _acting(_INSOLVENCY_RE, body, s):
        out.add(EV_INSOLVENCY)
    if _acting(_DELISTING_RE, body, s):
        out.add(EV_DELISTING)
    out |= _departures(body, s)
    if _MISCONDUCT_RE.search(body) and _about_subject(body, s):
        out.add(EV_MISCONDUCT)
    return frozenset(out)


def material_event(text: str | None, subject: Mapping[str, Any] | None = None) -> str | None:
    """One material-event type ``text`` asserts about the subject (the first by name),
    or ``None``."""
    types = event_types(text, subject)
    return min(types) if types else None


def is_vetted(source_kinds: Iterable[str] | None) -> bool:
    """Does at least one source behind a finding carry a material event?"""
    return bool(set(source_kinds or ()) & VETTED_SOURCE_KINDS)


def unvetted_event(
    statement: str | None,
    source_kinds: Iterable[str] | None,
    subject: Mapping[str, Any] | None = None,
) -> str | None:
    """An event type a finding states on unvetted support only, else ``None``."""
    if is_vetted(source_kinds):
        return None
    return material_event(statement, subject)


def sentence_blocked(
    sentence: str,
    cited: Iterable[Mapping[str, Any] | Any],
    subject: Mapping[str, Any] | None = None,
) -> bool:
    """Must a summary sentence (or key point) citing ``cited`` findings be excluded?

    Every event type the sentence states, and every event type ANY cited finding states
    on unvetted sources only, must be stated by a cited finding on a vetted source.
    ``cited`` items are finding dicts or objects with ``statement`` and ``source_kinds``.
    """
    vetted: set[str] = set()
    needed: set[str] = set(event_types(sentence, subject))
    for item in cited:
        types = event_types(str(_get(item, "statement") or ""), subject)
        if is_vetted(_get(item, "source_kinds")):
            vetted |= types
        else:
            needed |= types
    return bool(needed - vetted)


def vetted_event_types(
    findings: Iterable[Mapping[str, Any] | Any], subject: Mapping[str, Any] | None = None
) -> set[str]:
    """The event types some finding on a vetted source states."""
    out: set[str] = set()
    for finding in findings:
        if is_vetted(_get(finding, "source_kinds")):
            out |= event_types(str(_get(finding, "statement") or ""), subject)
    return out


def _get(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


__all__ = [
    "EVENT_TYPES",
    "REASON",
    "VETTED_SOURCE_KINDS",
    "event_types",
    "is_vetted",
    "material_event",
    "sentence_blocked",
    "subject_names",
    "unvetted_event",
    "vetted_event_types",
]
