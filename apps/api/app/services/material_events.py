"""Material corporate events need a vetted source before a summary states them (ADR-078).

WHY THIS EXISTS
===============
QA campaign 2026-11, PayPal rerun 39905de8. The executive synthesis said "a recent
acquisition offer was rejected by the board as inadequate". Its only finding cited ONE
unclassified web page (source kind ``secondary_web``, class ``unknown_web``): no 8-K, no
SC 14D9, nothing in either 10-Q, no curated press. The editor's checks are on
citations, figures and prohibited language — a sentence with a label, no digit and no
rating word passed all of them — and the chair's on citation handles and labels. Nothing
asked whether the SOURCE could carry the claim.

THE RULE
========
A *material corporate event* about the subject — a takeover approach, offer or bid for
it (and the board's acceptance or rejection), a merger, going private, insolvency or
administration, delisting, the CEO or CFO leaving, fraud, a restatement or a regulatory
enforcement action — may be stated in the executive synthesis, a section lead, or the
chair's synthesis and key points only when a finding resting on a VETTED source states
it: an issuer filing or the issuer's own release (``issuer_filing``, ``issuer_ir``), a
regulator (``government_or_regulator``), a filing on an official filing system
(``third_party_filing``) or curated press (``quality_media``). Otherwise the sentence or
key point is EXCLUDED — the same "dropped, not repaired" rule the editor already uses.

The finding itself stays in the ledger and in its section, with whatever trust label it
carries: the research is not deleted, the summary just does not promote it to fact.

DETECTION IS DETERMINISTIC AND BOUNDED
======================================
A word-bounded vocabulary of event PHRASES, never a bare noun. "Offer", "acquired" and
"fraud" alone are ordinary words in a payments or bank report ("offers Buy Now Pay
Later", "acquired 440M active accounts", "customer acquisition cost", "fraud losses");
only "offer FOR <subject>", "acquisition offer", "rejected by the board", "to be
acquired by", "filed for bankruptcy", "CEO … stepped down", "enforcement action" and the
like count. An acquisition BY the subject ("PayPal acquired Xoom in 2015", "the company
completed its acquisition of X") is not an event about the subject, and an event about
another company the statement names instead of the subject is not either.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

#: Source kinds (``director.contracts``) that can carry a material event: the issuer's own
#: word, a regulator, an official filing system, curated press.
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
EV_LEADERSHIP = "leadership_departure"
EV_MISCONDUCT = "fraud_restatement_or_enforcement"
EVENT_TYPES: frozenset[str] = frozenset(
    {EV_TAKEOVER, EV_GOING_PRIVATE, EV_INSOLVENCY, EV_DELISTING, EV_LEADERSHIP, EV_MISCONDUCT}
)

#: The reason a summary sentence or key point was dropped under this rule.
REASON = "material_event_unvetted_source"

_I = re.IGNORECASE

# ── Vocabulary ─────────────────────────────────────────────────────────── #

_SUBJECT = "__SUBJECT__"

#: Takeover phrases that need no named target: the phrase itself is about the company
#: whose board or shareholders face it.
_TAKEOVER_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:takeover|take-over)\s+(?:target|interest|speculation|talks|battle|approach|"
        r"offer|bid|proposal)|\bscheme\s+of\s+arrangement\b", _I,
    ),
    re.compile(
        r"\b(?:unsolicited|hostile|non-binding|indicative|all-cash|per-share|acquisition|"
        r"merger|buyout|buy-out)\s+(?:offer|bid|proposal|approach)\b", _I,
    ),
    re.compile(
        r"\b(?:merger\s+(?:with|agreement|of\s+equals|talks)|agreed\s+to\s+merge|"
        r"(?:to|will|would)\s+merge\s+with|merging\s+with)\b", _I,
    ),
)
#: The board accepting or rejecting an offer, bid or approach.
_BOARD_RE = re.compile(r"\bboard\b", _I)
_BOARD_VERDICT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:offer|bid|approach)\b.{0,80}\b(?:rejected|accepted|rebuffed|spurned|"
        r"turned\s+down|recommended)\b", _I,
    ),
    re.compile(
        r"\b(?:rejected|accepted|rebuffed|spurned|turned\s+down|rejects|accepts)\b.{0,60}"
        r"\b(?:offer|bid|approach)\b", _I,
    ),
)
#: Phrases whose OBJECT must be the subject: "offer for PayPal", "acquisition of PayPal".
_TARGETED_TAKEOVER_TEMPLATES: tuple[str, ...] = (
    r"\b(?:offer|bid|approach|proposal)\s+(?:for|to\s+(?:acquire|buy|purchase|take\s+over))"
    r"\s+(?:" + _SUBJECT + r")",
    r"\b(?:acquisition|takeover|purchase|buyout|sale)\s+of\s+(?:" + _SUBJECT + r")",
    r"\b(?:acquire|acquiring|take\s+over|taking\s+over|take\s+private|taking\s+private)\s+"
    r"(?:" + _SUBJECT + r")",
)
#: The subject as the object of a passive acquisition; the agent must not be the subject
#: ("Xoom was acquired by PayPal" is an acquisition BY the subject).
_PASSIVE_ACQUIRED_RE = re.compile(
    r"\b(?:to\s+be|agreed\s+to\s+be|will\s+be|would\s+be|was|is\s+being|being|has\s+been|"
    r"had\s+been)\s+(?:acquired|bought|taken\s+over|taken\s+private)\s+by\s+"
    r"(?P<agent>[^,;]{1,60})",
    _I,
)
_GOING_PRIVATE_RE = re.compile(
    r"\b(?:go(?:es|ing)?|went|gone|tak(?:e|es|en|ing)|took)[\s-]+private\b|"
    r"\b(?:leveraged\s+buy-?out|management\s+buy-?out|take-private)\b",
    _I,
)
_INSOLVENCY_RE = re.compile(
    r"\b(?:fil(?:ed|es|ing|e)\s+for\s+(?:bankruptcy|chapter\s+(?:7|11|15)|insolvency|"
    r"creditor\s+protection)|chapter\s+11\s+(?:protection|filing|proceedings?|bankruptcy)|"
    r"(?:declared|declares|is|was|went|became|becomes|become)\s+(?:bankrupt|insolvent)|"
    r"bankruptcy\s+(?:protection|filing|proceedings?|petition)|"
    r"insolvency\s+proceedings?|receivership|"
    r"(?:entered|enters|entering|placed\s+(?:in|into)|went\s+into|go(?:es|ing)?\s+into|"
    r"put\s+into)\s+(?:voluntary\s+)?(?:administration|liquidation|receivership)|"
    r"administrators?\s+(?:were\s+|have\s+been\s+|has\s+been\s+)?appointed|"
    r"liquidators?\s+(?:were\s+|have\s+been\s+|has\s+been\s+)?appointed|"
    r"winding[\s-]up\s+(?:order|petition))\b",
    _I,
)
_DELISTING_RE = re.compile(
    r"\b(?:delist(?:ed|ing|s)?|de-list(?:ed|ing|s)?|"
    r"(?:cancellation|cancelation)\s+of\s+(?:the\s+|its\s+)?(?:listing|admission)|"
    r"suspended\s+from\s+(?:trading|the\s+(?:exchange|NYSE|Nasdaq|ASX|LSE)))\b",
    _I,
)
_ROLE_RE = re.compile(
    r"\b(?:CEO|CFO|chief\s+executive(?:\s+officer)?|chief\s+financial\s+officer|"
    r"managing\s+director|finance\s+director)\b",
    re.IGNORECASE,
)
_DEPARTURE_RE = re.compile(
    r"\b(?:resign(?:s|ed|ing|ation)?|depart(?:s|ed|ing|ure)|step(?:s|ped|ping)?\s+down|"
    r"ousted|oust(?:s|ing)?|fired|dismissed|sacked|"
    r"(?:to|will|would|is|was)\s+(?:leave|leaving)|left\s+the\s+(?:company|group|board)|"
    r"retire(?:s|d)?|retirement|quit(?:s)?|(?:was|been|be)\s+replaced)\b",
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
#: A pronoun phrase that stands for the subject in a statement about it.
_SUBJECT_PRONOUN_RE = re.compile(
    r"\b(?:the|its)\s+(?:company|group|issuer|firm|board|business|shares|stock|"
    r"management|chief\s+executive|CEO|CFO)\b",
    _I,
)
_LEGAL_WORDS: frozenset[str] = frozenset(
    {
        "inc", "incorporated", "corp", "corporation", "co", "company", "plc", "ltd",
        "limited", "holdings", "holding", "group", "sa", "ag", "nv", "se", "spa", "llc",
        "lp", "asa", "ab", "oyj", "gmbh", "the", "and", "of", "resources", "international",
        "compagnie", "financiere", "financière", "class", "ordinary", "shares", "adr",
    }
)


def subject_names(subject: Mapping[str, Any] | None) -> tuple[tuple[str, ...], str | None]:
    """``(name words, ticker)`` for the subject: its distinctive name words (legal
    suffixes and generic words removed, at least four letters) and its ticker."""
    if not subject:
        return (), None
    name = str(subject.get("name") or "")
    words = tuple(
        dict.fromkeys(
            w for w in re.findall(r"[A-Za-zÀ-ÿ][\wÀ-ÿ\-]*", name)
            if len(w) >= 4 and w.casefold() not in _LEGAL_WORDS
        )
    )
    ticker = str(subject.get("ticker") or "").strip() or None
    return words, ticker


def _subject_alternation(names: Iterable[str], ticker: str | None) -> str:
    parts = [re.escape(n) for n in names]
    if ticker and len(ticker) >= 2:
        parts.append(r"(?-i:" + re.escape(ticker) + r")")
    parts.append(r"the\s+(?:company|group|issuer|firm)|it")
    return "|".join(parts)


def _names_subject(text: str, names: Iterable[str], ticker: str | None) -> bool:
    for name in names:
        if re.search(r"\b" + re.escape(name) + r"\b", text, _I):
            return True
    return bool(ticker and len(ticker) >= 2 and re.search(
        r"(?<![A-Za-z0-9])" + re.escape(ticker) + r"(?![A-Za-z0-9])", text))


def _about_subject(text: str, names: tuple[str, ...], ticker: str | None) -> bool:
    return _names_subject(text, names, ticker) or bool(_SUBJECT_PRONOUN_RE.search(text))


def _takeover(text: str, names: tuple[str, ...], ticker: str | None) -> bool:
    subject_alt = _subject_alternation(names, ticker)
    for template in _TARGETED_TAKEOVER_TEMPLATES:
        # Not "sale of the company's products": a possessive is not the subject itself.
        if re.search(template.replace(_SUBJECT, subject_alt) + r"\b(?!['’])", text, _I):
            return True
    for match in _PASSIVE_ACQUIRED_RE.finditer(text):
        agent = match.group("agent")
        before = text[: match.start()]
        if not _names_subject(agent, names, ticker) and _about_subject(before, names, ticker):
            return True
    if _BOARD_RE.search(text) and any(r.search(text) for r in _BOARD_VERDICT_RES):
        return True
    return any(r.search(text) for r in _TAKEOVER_RES) and _about_subject(text, names, ticker)


def _leadership(text: str, names: tuple[str, ...], ticker: str | None) -> bool:
    for role in _ROLE_RE.finditer(text):
        window = text[max(0, role.start() - 80): role.end() + 80]
        if not _DEPARTURE_RE.search(window):
            continue
        owner = _ROLE_OWNER_RE.search(text[max(0, role.start() - 40): role.start()])
        if owner and not _names_subject(owner.group(1), names, ticker):
            continue  # another company's CEO
        return True
    return False


def material_event(text: str | None, subject: Mapping[str, Any] | None = None) -> str | None:
    """The material-event type ``text`` asserts about the subject, or ``None``."""
    body = str(text or "")
    if not body.strip():
        return None
    names, ticker = subject_names(subject)
    if _takeover(body, names, ticker):
        return EV_TAKEOVER
    if _GOING_PRIVATE_RE.search(body) and _about_subject(body, names, ticker):
        return EV_GOING_PRIVATE
    if _INSOLVENCY_RE.search(body) and _about_subject(body, names, ticker):
        return EV_INSOLVENCY
    if _DELISTING_RE.search(body) and _about_subject(body, names, ticker):
        return EV_DELISTING
    if _leadership(body, names, ticker):
        return EV_LEADERSHIP
    if _MISCONDUCT_RE.search(body) and _about_subject(body, names, ticker):
        return EV_MISCONDUCT
    return None


def is_vetted(source_kinds: Iterable[str] | None) -> bool:
    """Does at least one source behind a finding carry a material event?"""
    return bool(set(source_kinds or ()) & VETTED_SOURCE_KINDS)


def unvetted_event(
    statement: str | None,
    source_kinds: Iterable[str] | None,
    subject: Mapping[str, Any] | None = None,
) -> str | None:
    """The event type a finding states on unvetted support only, else ``None``."""
    if is_vetted(source_kinds):
        return None
    return material_event(statement, subject)


def sentence_blocked(
    sentence: str,
    cited: Iterable[Mapping[str, Any] | Any],
    subject: Mapping[str, Any] | None = None,
) -> bool:
    """Must a summary sentence (or key point) citing ``cited`` findings be excluded?

    Yes when the sentence states a material event and no cited finding on a vetted
    source states an event of that type; and yes when EVERY cited finding is an
    unvetted material event, whatever words the sentence chose to restate it in.
    ``cited`` items are finding dicts or objects with ``statement`` and ``source_kinds``.
    """
    items = [
        (str(_get(c, "statement") or ""), tuple(_get(c, "source_kinds") or ()))
        for c in cited
    ]
    event = material_event(sentence, subject)
    if event is not None and not any(
        is_vetted(kinds) and material_event(statement, subject) == event
        for statement, kinds in items
    ):
        return True
    return bool(items) and all(
        unvetted_event(statement, kinds, subject) is not None for statement, kinds in items
    )


def vetted_event_types(
    findings: Iterable[Mapping[str, Any] | Any], subject: Mapping[str, Any] | None = None
) -> set[str]:
    """The event types some finding on a vetted source states."""
    out: set[str] = set()
    for finding in findings:
        if not is_vetted(_get(finding, "source_kinds")):
            continue
        event = material_event(str(_get(finding, "statement") or ""), subject)
        if event:
            out.add(event)
    return out


def _get(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


__all__ = [
    "EVENT_TYPES",
    "REASON",
    "VETTED_SOURCE_KINDS",
    "is_vetted",
    "material_event",
    "sentence_blocked",
    "subject_names",
    "unvetted_event",
    "vetted_event_types",
]
