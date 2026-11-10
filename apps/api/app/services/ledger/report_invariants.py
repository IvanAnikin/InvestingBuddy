"""Report-status invariants — ADR-082.

WHAT THIS CHECKS
================
Sentences a report states about THE RESEARCH ITSELF — how many findings it has, which
document is the newest, whether the current period is held, whether the council ran —
against the run's canonical state (:mod:`app.services.ledger.canonical_state`). It does
not judge claims about the company; that is the investigator's and the chair's job.

At minimum these are made impossible (QA campaign 2026-11, P-2 / P-4):

* a finding count that differs from the ledger's;
* "latest quarter / period unavailable / not acquired" while the current period is held;
* "no recent developments" while a current issuer disclosure is cited;
* "no cash flow" while cash-flow findings exist;
* "evidence adequate" while the current-period extraction failed;
* an older document named as the latest / newest disclosure;
* "no issuer filing / release is in evidence" while one is held and cited;
* a chair disagreement id the ledger does not record (dropped), and a chair sentence
  narrating a RESEARCH disagreement ("unresolved disagreement", "the red team
  disputed …", a disagreement id) when none is recorded (withheld) — a sentence about
  the world disagreeing ("conflicting data on FY2026 operating profit") is kept;
* "N agents completed" while the council was degraded.

REPAIR, OR WITHHOLD WITH A REASON — NEVER SILENTLY KEEP
=======================================================
A count or a fallback reason is repaired from the canonical state. A free-text claim
cannot be repaired (the platform does not know what the writer meant to say), so the
offending SENTENCE is withheld; a text left with no sentence is replaced by a notice
naming the reason. Every action is recorded in ``state_invariants``.

THE PHRASE TABLE IS CONSERVATIVE
================================
A false positive deletes a true sentence, which is worse than a miss. Each pattern is
anchored on the inventory claim itself ("no issuer filing ... is in evidence"), refuses
qualified claims ("no issuer release FOR Q4 ..."), and is tested in both directions
(``tests/test_qa_closure_ledger_state.py``). No issuer is named here.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.services.ledger import claim_checks
from app.services.ledger.canonical_state import (
    FALLBACK_CHAIR_FAILED,
    FALLBACK_NO_MODEL,
    FALLBACK_NOT_CONVENED,
    REFUSAL_WORDS,
    as_date,
)

INVARIANTS_VERSION = 1

RULE_FINDING_COUNT = "finding_count_differs_from_ledger"
RULE_COUNCIL_COUNTS = "council_block_counts_differ_from_ledger"
RULE_FALLBACK_REASON = "fallback_reason_misstated"
RULE_DEGRADED_REASON = "degraded_reason_misstated"
RULE_LATEST_PERIOD = "latest_period_denied_while_held"
RULE_NO_RECENT = "no_recent_developments_while_current_disclosure_cited"
RULE_NO_CASH_FLOW = "no_cash_flow_while_cash_flow_findings"
RULE_EVIDENCE_ADEQUATE = "evidence_adequate_while_current_period_extraction_failed"
RULE_STALE_LATEST = "older_document_named_as_newest"
RULE_NO_ISSUER_DOC = "issuer_document_denied_while_held_and_cited"
RULE_UNKNOWN_DISAGREEMENT_ID = "chair_disagreement_id_not_in_ledger"
RULE_UNBACKED_DISAGREEMENT = "disagreement_narrated_with_none_recorded"
RULE_COUNCIL_STATUS = "council_status_overstated"
#: ADR-086 — a chair / council claim that misstates the period or scope of the finding
#: it cites, or presents a platform-derived ratio as reported (``claim_checks``).
RULE_CLAIM_PERIOD = claim_checks.RULE_CLAIM_PERIOD
RULE_CLAIM_SCOPE = claim_checks.RULE_CLAIM_SCOPE
RULE_DERIVED_AS_REPORTED = claim_checks.RULE_DERIVED_AS_REPORTED
#: ADR-093 — a business line called a segment, or the company's main revenue driver.
RULE_LINE_AS_SEGMENT = claim_checks.RULE_LINE_AS_SEGMENT
RULE_LINE_AS_MAIN_DRIVER = claim_checks.RULE_LINE_AS_MAIN_DRIVER
#: ADR-086 — the V2 "no primary issuer document ingested" line, while the ledger holds
#: issuer documents.
RULE_PRIMARY_DOC_GROUP = "primary_document_group_denied_while_held"
#: ADR-091 — a status line (a count, "not sourced", "not in this report") that
#: contradicts the canonical state.
RULE_STATUS_LINE = "status_line_contradicts_canonical_state"
#: ADR-091 — a chair sentence citing a finding id the run's ledger does not hold.
RULE_CITED_FINDING_UNKNOWN = "chair_cited_finding_not_in_ledger"
#: ADR-091 — a chair sentence narrating a disagreement / conflict / contradiction
#: between cited findings that no recorded disagreement pairs.
RULE_PHANTOM_CONFLICT = "conflict_narrated_between_unpaired_findings"
#: ADR-094 — a chair sentence saying a metric or topic is not disclosed / missing while
#: a finding it cites states it, or the gap queue located it in a held document.
RULE_ABSENCE_CONTRADICTED = "absence_stated_while_found"
#: ADR-095 — a count of unresolved disagreements / conflicts that is not the number of
#: conflicts the platform computed between the run's findings.
RULE_CONFLICT_COUNT = "conflict_count_differs_from_ledger"
#: ADR-095 — a report search count that is not the one search-status producer's.
RULE_SEARCH_COUNT = "search_count_differs_from_search_status"
#: ADR-095 review — "the red team disputed …" while no Red Team challenge is unresolved.
RULE_UNBACKED_CHALLENGE = "red_team_dispute_narrated_with_none_unresolved"

RULES: tuple[str, ...] = (
    RULE_FINDING_COUNT,
    RULE_COUNCIL_COUNTS,
    RULE_FALLBACK_REASON,
    RULE_DEGRADED_REASON,
    RULE_LATEST_PERIOD,
    RULE_NO_RECENT,
    RULE_NO_CASH_FLOW,
    RULE_EVIDENCE_ADEQUATE,
    RULE_STALE_LATEST,
    RULE_NO_ISSUER_DOC,
    RULE_UNKNOWN_DISAGREEMENT_ID,
    RULE_UNBACKED_DISAGREEMENT,
    RULE_COUNCIL_STATUS,
    RULE_CLAIM_PERIOD,
    RULE_CLAIM_SCOPE,
    RULE_DERIVED_AS_REPORTED,
    RULE_PRIMARY_DOC_GROUP,
    RULE_STATUS_LINE,
    RULE_CITED_FINDING_UNKNOWN,
    RULE_PHANTOM_CONFLICT,
    RULE_LINE_AS_SEGMENT,
    RULE_LINE_AS_MAIN_DRIVER,
    RULE_ABSENCE_CONTRADICTED,
    RULE_CONFLICT_COUNT,
    RULE_SEARCH_COUNT,
    RULE_UNBACKED_CHALLENGE,
)

#: Where a text sits. The chair is checked for everything; other texts are checked for
#: inventory claims only (they do not state counts or narrate the council).
SITE_CHAIR = "chair"
SITE_RECORD = "record"
SITE_LEGACY = "legacy"
#: ADR-093 — an editor-written synthesis sentence or section lead that cites its findings
#: by label: checked like a record text, and held to what it cites like a chair claim.
SITE_SYNTHESIS = "synthesis"
#: ADR-095 — professional-report prose that cites nothing: checked like a record text,
#: and for disagreements / conflicts the ledger's conflict list does not back.
SITE_REPORT_PROSE = "report_prose"
#: ADR-093 — findings read when a sentence citing nothing is held to the whole ledger.
MAX_LEDGER_SOURCES = 120

#: Days by which a held document must be newer than the one named as newest before a
#: sentence is called stale (a margin in the direction of keeping a sentence: same-week
#: documents are not a contradiction).
DOCUMENT_MARGIN_DAYS = 3

#: Sentences longer than this are not checked (kept as written). A sentence about the
#: research inventory is short; the cap bounds what any one sentence can cost.
MAX_SENTENCE_CHARS = 600

#: A sentence boundary inside one line. Lines are split first (``_Enforcer.text``), so a
#: bullet list is never treated as one sentence.
_SENTENCE_RE = re.compile(r"(?<=[.!?])[ \t]+(?=[A-Z\"'(\[])")
_BULLET_RE = re.compile(r"^[ \t]*(?:[-*•+]|\d{1,3}[.)])[ \t]+")


# ---------------------------------------------------------------------------
# The reviewed phrase table
#
# LINEAR BY CONSTRUCTION (QA review B1): the inventory claims ("no issuer filings,
# financial statements, or company disclosures are present") are recognised by a token
# scan with a closed vocabulary and MANDATORY list separators, not by a regex with a
# repeated optional word — that shape backtracked exponentially on "No issuer press
# releases press releases …". Every remaining regex has disjoint adjacent quantifiers
# or a fixed bound; ``tests/test_qa_closure_ledger_state.py`` times each one on
# degenerate input.
#
# UNQUALIFIED CLAIMS ONLY (QA review N2): a sentence is withheld only when it makes a
# claim about the WHOLE evidence inventory. "No issuer press release is available ON
# the acquisition", "no quarterly report is present FOR the December quarter", "lack
# of primary filings FOR the subsidiary" are claims about a subset and are kept.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?(?:-[a-z0-9]+)*|[^\sa-z0-9]")
#: Words that may describe an issuer DOCUMENT (and make it the issuer's).
_ISSUER_MODS: frozenset[str] = frozenset({
    "issuer", "issuer's", "issuers", "issuer-own", "own", "company", "company's",
    "company-owned", "company-own", "primary", "regulatory",
})
#: Other words allowed inside a listed item. A closed set: "official GUIDANCE
#: document", "FORTHCOMING company disclosures" are not inventory items, and "no
#: VERIFIED / AUDITED company disclosures" is a claim about a subset (the ledger may
#: hold the issuer's documents and still have verified none of them).
_OTHER_MODS: frozenset[str] = frozenset({
    "any", "official", "fundamental", "financial", "operational", "operating",
    "corporate", "press",
})
_MODS = _ISSUER_MODS | _OTHER_MODS
_DOC_HEADS: frozenset[str] = frozenset({
    "filing", "filings", "disclosure", "disclosures", "document", "documents",
    "announcement", "announcements", "release", "releases",
})
#: Nouns that may share a list with documents but are not documents themselves.
_DATA_HEADS: frozenset[str] = frozenset({"statements", "data", "information"})
_HEADS = _DOC_HEADS | _DATA_HEADS
_LIST_JOINERS: frozenset[str] = frozenset({"or", "and", "nor"})
_MAX_LIST_ITEMS = 8
_MAX_ITEM_MODS = 4
_COPULAS: frozenset[str] = frozenset({"is", "are", "was", "were"})
_INVENTORY_PREDICATES: frozenset[str] = frozenset({
    "present", "included", "available", "found", "held", "acquired",
})
_HAS_VERBS: frozenset[str] = frozenset({"includes", "contains", "has", "shows", "holds"})
_INVENTORY_NOUNS: frozenset[str] = frozenset({"evidence", "record", "pack"})

#: What may follow a complete inventory claim for it still to be UNQUALIFIED.
_TAIL_STOPS: frozenset[str] = frozenset({".", ";", ":", ")", "!", "?", "—", "–", "-"})
_TAIL_CONNECTIVES: frozenset[str] = frozenset({
    "so", "and", "which", "thus", "therefore", "limiting", "leaving", "preventing",
    "restricting", "constraining", "hampering", "hindering", "meaning", "making",
})
#: "… are present TO PROVIDE context" is a purpose, not a scope; "… is available TO
#: CONFIRM the Q3 date" is a scope. Only purpose verbs are accepted.
_TO_VERBS: frozenset[str] = frozenset({
    "provide", "identify", "assess", "evaluate", "inform", "offer", "give",
})
#: After "absence of X": the verb of the sentence, or an adverb before it.
_ABSENCE_VERBS: frozenset[str] = frozenset({
    "limits", "limit", "limited", "severely", "significantly", "substantially",
    "greatly", "seriously", "restricts", "prevents", "constrains", "hampers", "hinders",
    "precludes", "undermines", "makes", "leaves", "means", "is", "remains", "reduces",
    "weakens", "impairs",
})
#: A word after a comma that turns the claim into a scoped one.
_QUALIFIERS: frozenset[str] = frozenset({
    "for", "on", "about", "regarding", "concerning", "covering", "in", "from", "of",
    "since", "after", "before", "with", "confirming", "because", "as", "given",
    "beyond", "other", "except", "outside", "until", "prior", "besides", "apart",
    "dated", "relating", "related", "addressing", "mentioning", "discussing",
    "describing", "showing", "containing", "specifically", "specific", "that", "due",
    "owing", "under", "at", "by", "via", "through", "within", "per",
})


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower().replace("’", "'"))


def _at(tokens: list[str], k: int) -> str | None:
    return tokens[k] if 0 <= k < len(tokens) else None


def _item(tokens: list[str], i: int) -> tuple[int, bool] | None:
    """One listed item from ``tokens[i]``: up to four modifiers and a head noun.
    Returns (index after it, whether it is an issuer DOCUMENT)."""
    j, issuer = i, False
    while j < len(tokens) and j - i < _MAX_ITEM_MODS and tokens[j] in _MODS:
        issuer = issuer or tokens[j] in _ISSUER_MODS
        j += 1
    head = _at(tokens, j)
    if head not in _HEADS:
        return None
    return j + 1, issuer and head in _DOC_HEADS


def _item_list(tokens: list[str], i: int) -> tuple[int, bool] | None:
    """A list of items separated by a comma and/or a conjunction (MANDATORY between
    items, so the scan is linear). Returns (index after the list, whether any item is an
    issuer document)."""
    end: int | None = None
    issuer = False
    pos = i
    for _ in range(_MAX_LIST_ITEMS):
        parsed = _item(tokens, pos)
        if parsed is None:
            break
        end, is_doc = parsed
        issuer = issuer or is_doc
        k = end
        if _at(tokens, k) == ",":
            k += 1
        if _at(tokens, k) in _LIST_JOINERS:
            k += 1
        if k == end:
            break
        pos = k
    return (end, issuer) if end is not None else None


def _in_evidence(tokens: list[str], k: int) -> int | None:
    """``in (the) evidence (pack)`` / ``in the pack`` from ``k``: the index after it."""
    if _at(tokens, k) != "in":
        return None
    k += 1
    if _at(tokens, k) == "the":
        k += 1
    if _at(tokens, k) == "evidence":
        k += 1
        return k + 1 if _at(tokens, k) == "pack" else k
    return k + 1 if _at(tokens, k) == "pack" else None


def _tail_unqualified(tokens: list[str], k: int, verbs: frozenset[str] = frozenset()) -> bool:
    """True when what follows a complete claim leaves it a claim about the whole
    inventory: the end, a stop, a connective, a purpose clause — not a scope."""
    tok = _at(tokens, k)
    if tok is None or tok in _TAIL_STOPS or tok in _TAIL_CONNECTIVES or tok in verbs:
        return True
    if tok == ",":
        nxt = _at(tokens, k + 1)
        return nxt is None or nxt not in _QUALIFIERS
    if tok == "to":
        return _at(tokens, k + 1) in _TO_VERBS
    return False


#: Tokens that end a clause for the prefix check (a comma does not: "For X, no …").
_HARD_STOPS: frozenset[str] = frozenset({".", ";", ":", "!", "?", "—", "–", "-", "(", ")"})
#: A clause that OPENS with one of these scopes what follows it (QA re-review X3):
#: "For the December quarter, no …", "Before the IPO no …", "Unlike the peer, for which
#: no …", "On the litigation, there are no …".
_PREFIX_QUALIFIERS: frozenset[str] = frozenset({
    "for", "on", "in", "before", "until", "till", "since", "unlike", "regarding",
    "during", "after", "about", "concerning", "apart", "except", "besides", "beyond",
    "outside", "within", "from", "prior", "excluding", "without", "as", "at", "by",
    "under", "per", "when", "if", "where", "whereas", "unless", "although", "though",
    "absent", "among", "between", "across", "over", "through", "throughout", "pending",
})
#: Words skipped before judging how a clause opens.
_LEADING_SKIP: frozenset[str] = frozenset({
    "and", "but", "so", "yet", "however", "also", "thus", "therefore", "moreover",
    "furthermore", "additionally", "notably", "overall", "currently", "still",
})
_PREFIX_WINDOW = 40
#: "<subject> has no …": the claim is about the subject's documents only when the
#: subject is the research itself or the company under research.
_SUBJECT_VERBS: frozenset[str] = frozenset({
    "has", "have", "had", "holds", "hold", "contains", "contain", "includes", "include",
    "shows", "show", "with",
})
_OWN_SUBJECTS: frozenset[str] = frozenset({
    "evidence", "record", "pack", "report", "research", "ledger", "platform", "dataset",
    "company", "issuer", "it",
})
_SUBJECT_DETERMINERS: frozenset[str] = frozenset({"the", "this", "our", ",", "and", "but"})


#: Openers that structure the argument without scoping the claim (QA final review R2).
_DISCOURSE_OPENERS: tuple[tuple[str, ...], ...] = (
    ("in", "summary"), ("in", "particular"), ("in", "short"), ("in", "conclusion"),
    ("in", "addition"), ("in", "general"), ("in", "total"), ("as", "a", "result"),
    ("as", "such"), ("on", "balance"), ("if", "anything"), ("overall",),
)
#: A segment about the research itself or about NOW is not a scope: "In this report,
#: no …", "In the evidence pack, no …", "As of today, no …", "At present, no …".
_RESEARCH_OR_NOW: frozenset[str] = frozenset({
    "evidence", "pack", "record", "report", "research", "analysis", "dataset",
    "ledger", "platform", "today", "present", "now", "date", "currently", "moment",
})
#: "Since / as / because no issuer filings are present, …" is causal: it asserts the
#: claim. "Since the merger, no …" is a scope.
_CAUSAL: frozenset[str] = frozenset({"since", "as", "because"})
_CLAUSE_CONJUNCTIONS: frozenset[str] = frozenset({"and", "but"})


def _strip_openers(segment: list[str]) -> list[str]:
    """The segment without leading skip words and discourse openers."""
    k = 0
    while k < len(segment):
        if segment[k] in _LEADING_SKIP:
            k += 1
            continue
        opener = next(
            (o for o in _DISCOURSE_OPENERS if tuple(segment[k: k + len(o)]) == o), None
        )
        if opener is None:
            break
        k += len(opener)
    return segment[k:]


def _segment_scopes(segment: list[str]) -> bool:
    """A segment that scopes what follows it: it opens with a qualifier and is not
    about the research itself or about now."""
    words = _strip_openers(segment)
    if not words or words[0] not in _PREFIX_QUALIFIERS:
        return False
    return not any(t in _RESEARCH_OR_NOW for t in words)


def _prefix_qualified(tokens: list[str], i: int) -> bool:
    """True when the clause before ``tokens[i]`` scopes the claim: the claim's own comma
    segment, or the one before it, opens with a qualifier ("For the December quarter,
    no …", "Before the IPO no …"), or the claim itself is negated ("it is not the case
    that no …"). A conjunction ("…, and no …") starts a new clause: only what follows
    it counts. Looks back at most ``_PREFIX_WINDOW`` tokens."""
    lo = max(0, i - _PREFIX_WINDOW)
    start = lo
    for k in range(i - 1, lo - 1, -1):
        if tokens[k] in _HARD_STOPS:
            start = k + 1
            break
    clause = tokens[start:i]
    if clause[-1:] == ["that"] and "not" in clause[-6:]:
        return True
    segments: list[list[str]] = [[]]
    for tok in clause:
        if tok == ",":
            segments.append([])
        else:
            segments[-1].append(tok)
    last = segments[-1]
    cut = max((k for k, t in enumerate(last) if t in _CLAUSE_CONJUNCTIONS), default=None)
    if cut is not None:
        return _segment_scopes(last[cut + 1:])
    if len(_strip_openers(last)) == 1 and _strip_openers(last)[0] in _CAUSAL:
        last = []
    candidates = [last, segments[-2]] if len(segments) > 1 else [last]
    return any(_segment_scopes(segment) for segment in candidates)


def _prefix_qualified_text(text: str, start: int) -> bool:
    """The prefix check for a regex-matched claim starting at ``start``."""
    tokens = _tokens(text[max(0, start - 300): start])
    return _prefix_qualified(tokens, len(tokens))


def _own_subject(tokens: list[str], k: int) -> bool:
    """``tokens[k]`` is the subject of "<subject> has no …": the research or the
    company under research ("the company", "the evidence pack"), not another entity
    ("Iluka", "the JV partner", "the peer company")."""
    if _at(tokens, k) not in _OWN_SUBJECTS:
        return False
    before = _at(tokens, k - 1)
    if _at(tokens, k) == "pack" and before == "evidence":
        before = _at(tokens, k - 2)
    return before is None or before in _SUBJECT_DETERMINERS


def _denies_issuer_documents(text: str) -> bool:
    """An UNQUALIFIED claim that no issuer document is in the evidence:

    * "no <list> [is/are/has been] present|included|… [in the evidence]" ;
    * "the evidence/record includes|contains|has no <list>" ;
    * "absence / lack of <list>" followed by the sentence's verb.

    ``<list>`` must name at least one issuer DOCUMENT (an issuer word + a document noun).
    """
    tokens = _tokens(text)
    for i, tok in enumerate(tokens):
        if tok == "no":
            parsed = _item_list(tokens, i + 1)
            if parsed is None or not parsed[1] or _prefix_qualified(tokens, i):
                continue
            k = parsed[0]
            prev = _at(tokens, i - 1)
            if prev in _HAS_VERBS and _at(tokens, i - 2) in _INVENTORY_NOUNS:
                if _tail_unqualified(tokens, k):
                    return True
                continue
            if prev in _SUBJECT_VERBS and not _own_subject(tokens, i - 2):
                # "Iluka has no regulatory filings", "the JV partner has no company
                # filings": a claim about ANOTHER entity's documents.
                continue
            if _at(tokens, k) in _COPULAS:
                k += 1
            elif _at(tokens, k) in ("has", "have") and _at(tokens, k + 1) == "been":
                k += 2
            after_in = _in_evidence(tokens, k)
            if after_in is not None:
                k = after_in
            elif _at(tokens, k) in _INVENTORY_PREDICATES:
                k += 1
                k = _in_evidence(tokens, k) or k
            else:
                continue
            if _tail_unqualified(tokens, k):
                return True
        elif tok in ("absence", "lack") and _at(tokens, i + 1) == "of":
            parsed = _item_list(tokens, i + 2)
            if parsed is None or not parsed[1] or _prefix_qualified(tokens, i):
                continue
            k = _in_evidence(tokens, parsed[0]) or parsed[0]
            if _tail_unqualified(tokens, k, _ABSENCE_VERBS):
                return True
    return False


def _unqualified_after(text: str, end: int, verbs: frozenset[str] = frozenset()) -> bool:
    """The tail check for a regex-matched claim ending at ``end``."""
    return _tail_unqualified(_tokens(text[end: end + 80]), 0, verbs)


_LATEST_DOC_RE = re.compile(
    r"(?<![\w-])(?:newest|latest|most\s+recent)\s+(?:dated\s+)?"
    r"(?:(?:issuer|issuer's|primary|results|official|company|company's|regulatory|own)\s+)"
    # Generic nouns only: "the latest PRESS RELEASE dated …" may truly be the latest
    # press release while a later 10-Q is held — a different kind is no contradiction.
    r"{0,2}(?:disclosure|filing|document|evidence)s?\b"
    # A QUALIFIED claim ("latest filing WITH period figures", "newest disclosure FOR
    # Q2", "latest disclosure IN the 10-K") is about a subset, and a newer document
    # outside it does not contradict it. "in (the) evidence / pack" is not a subset.
    r"(?!\s+(?:with|for|of|on|about|covering|containing|that|which|reporting|from)\b)"
    r"(?!\s+in\s+(?!(?:the\s+)?(?:evidence|pack|record)\b))",
    re.I,
)
_ORDINAL_BEFORE_RE = re.compile(r"(?:second|next|third|previous|prior|earlier)[\s-]*$", re.I)
#: A date is the DOCUMENT's only when the words before it say so ("dated", "filed on",
#: "published", "released") or it follows a form type ("the 10-Q (2026-10-27)") — never
#: a covered-period date ("covers 1 July 2026 to 30 September 2026", "share count on
#: 2026-09-30"). QA review B3.
_DOC_DATE_BEFORE_RE = re.compile(
    r"(?:\b(?:dated|filed|published|released|issued|lodged)(?:\s+on)?"
    r"|\b(?:10-K|10-Q|20-F|40-F|6-K(?:/A)?|8-K)\s*[(,]?)[ \t,]*$",
    re.I,
)

_LATEST_UNAVAILABLE_RES = (
    re.compile(
        r"\b(?:latest|current|most\s+recent|newest)\s+"
        r"(?:quarter(?:ly)?|interim|half[- ]year(?:ly)?|reporting\s+period|period)(?:'s)?"
        r"(?:\s+(?P<noun>report|results|figures|filing|statements?|accounts))?"
        r"(?::[ \t]*|\s+(?:is|are|was|were|has\s+been|have\s+been)\s+)"
        r"(?:not\s+(?:in\s+this\s+report|available|acquired|yet\s+acquired"
        r"|in\s+(?:the\s+)?evidence(?:\s+pack)?)|unavailable|missing|absent)\b",
        re.I,
    ),
    re.compile(
        r"\bno\s+(?:current[- ]period|interim|quarterly|half[- ]year(?:ly)?)\s+"
        r"(?P<noun>report|filing|results|statements?|figures|accounts)\s+"
        r"(?:is|are|was|were|has\s+been|have\s+been)\s+"
        r"(?:(?:acquired|available|present|found|held)(?:\s+in\s+(?:the\s+)?evidence(?:\s+pack)?)?"
        r"|in\s+(?:the\s+)?evidence(?:\s+pack)?)\b",
        re.I,
    ),
)
_FIGURE_NOUNS = frozenset({"results", "figures", "statement", "statements", "accounts"})

_NO_RECENT_RE = re.compile(
    r"\bno\s+(?:recent|current|new)\s+(?:developments|news|disclosures|announcements"
    r"|events)(?:\s+(?:were|was|have\s+been|has\s+been|are|is)\s+(?:found|identified"
    r"|reported|disclosed|sourced|recorded|available))?\b",
    re.I,
)

_NO_CASH_FLOW_RES = (
    re.compile(
        r"\bno\s+(?:operating\s+|free\s+)?cash[- ]flow(?:\s+(?:data|figures?|statements?"
        r"|information|disclosure))?\s+(?:(?:is|are|was|were)\s+)?(?:available|disclosed"
        r"|present|reported|provided|in\s+(?:the\s+)?evidence(?:\s+pack)?)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:absence|lack)\s+of\s+(?:any\s+)?(?:operating\s+|free\s+)?cash[- ]flow\s+"
        r"(?:data|figures|information|statements?)\b",
        re.I,
    ),
)

#: An UNQUALIFIED adequacy claim only: "the evidence is sufficient to assess X but
#: lacks Y" is a scoped judgement, not a statement that the evidence is adequate.
_EVIDENCE_ADEQUATE_RE = re.compile(
    r"\bevidence(?:\s+base|\s+pack)?\s+(?:is|was|appears|remains)\s+"
    r"(?:adequate|sufficient|complete)"
    r"(?:\s+for\s+(?:a|the)\s+(?:full|complete)\s+(?:analysis|assessment))?"
    r"(?=[ \t]*(?:[.;]|$))",
    re.I,
)

#: A disagreement the LEDGER would record (QA review B2): "unresolved disagreement", a
#: disagreement id. A sentence about the WORLD disagreeing
#: — "conflicting data on FY2026 operating profit", "analysts disagree", "guidance
#: contradicts the consensus" — is a claim about the company, not about the research.
#: Challenge wording is NOT a disagreement: the ledger records Red Team challenges
#: separately (CFR: 5 challenges, 0 disagreements — "the red team challenged five
#: findings" is true there). QA re-review X1. ADR-095 review: "the red team disputed /
#: contested …" narrates a CHALLENGE, held to the unresolved challenges
#: (``_RED_TEAM_DISPUTE_RE``), never to the conflicts between findings.
_LEDGER_DISAGREEMENT_RE = re.compile(
    r"\b(?:unresolved\s+(?:disagreements?|disputes?)"
    r"|disagreement\s+(?:id\s+)?[0-9a-f]{8}\b"
    # ADR-095 (HON4, CFR2) — the same claim about the research in other words, explicit
    # forms only: the conflict word must be "unresolved" AND sit in the evidence /
    # findings / sources / data ("unresolved conflicts in the evidence regarding capex"),
    # or the findings themselves must be called conflicting ("conflicting findings
    # exist"). "Unresolved conflicts in the region" is about the world and is kept.
    r"|unresolved[ \t]{1,3}(?:conflicts?|inconsistenc(?:y|ies)|discrepanc(?:y|ies)"
    r"|contradictions?)[ \t]{1,3}(?:in|between|among|within|across)[ \t]{1,3}"
    r"(?:the[ \t]{1,3})?(?:evidence|findings|sources|data|research|record)\b"
    r"|(?:conflicting|contradictory)[ \t]{1,3}findings\b"
    r"|disagreements?[ \t]{1,3}(?:between|among)[ \t]{1,3}(?:the[ \t]{1,3})?findings\b)",
    re.I,
)
_RED_TEAM_DISPUTE_RE = re.compile(
    r"\bred[- \t]team[ \t]{1,3}(?:disputed|disputes|contested|contests)\b", re.I)
_NEGATED_DISAGREEMENT_RE = re.compile(
    r"\b(?:no|without|nor|0|zero|none)\s+(?:\w+\s+){0,2}(?:disagreements?|disputes?"
    r"|challenges?|conflicts?|conflicting|contradictory|inconsistenc(?:y|ies)"
    r"|discrepanc(?:y|ies)|contradictions?)\b",
    re.I,
)
#: ADR-095 — "N unresolved disagreement(s)" / "N unresolved conflicts": a count the
#: deterministic conflict list must back.
_CONFLICT_COUNT_RE = re.compile(
    r"\b(\d{1,4})[ \t]{1,3}unresolved[ \t]{1,3}(disagreement|conflict)(s|\(s\))?(?![\w(])",
    re.I,
)

_DET_STATE_RE = re.compile(
    r"^Deterministic synthesis:\s*(\d+)\s+finding\(s\),\s*(\d+)\s+verified,\s*(\d+)\s+"
    r"open\s+gap\(s\),\s*(\d+)\s+unresolved\s+disagreement\(s\)\.$"
)
_NO_MODEL_RE = re.compile(r"\bNo model was available\b")
_DEGRADED_NO_FINDINGS = "(no findings to synthesise)"
_DEGRADED_NO_MODEL = "no chair model was available"
_DEGRADED_CHAIR_FAILED = "the chair model did not return a usable reply"

_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}
_MONTH = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
_DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_DMY_RE = re.compile(rf"\b(\d{{1,2}})\s+{_MONTH}\s+(\d{{4}})\b", re.I)
_DATE_MDY_RE = re.compile(rf"\b{_MONTH}\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I)
_ACCESSION_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
_FORM_RE = re.compile(r"\b(10-K|10-Q|20-F|40-F|6-K(?:/A)?|8-K)\b", re.I)
#: Where the clause that names THE newest document ends.
_ANCHOR_CLAUSE_END_RE = re.compile(
    # "(?<!\s)" lets the "and" branch start only at the FIRST character of a whitespace
    # run; without it every position inside a long run restarts "\s+" (quadratic).
    r";|,\s*(?:which|unlike|while|whereas)\b|(?<!\s),?\s+and\s+(?:the|its|an?)\b"
    r"|\b(?:earlier|prior|previous|older|unlike"
    r"|superseded|supersedes|supersede|replaced|replaces)\b",
    re.I,
)


def _document_dates(text: str) -> list[tuple[int, date]]:
    """The dates a text ties to a DOCUMENT (see ``_DOC_DATE_BEFORE_RE``), with their
    positions, in the order they appear."""
    out: list[tuple[int, date]] = []

    def put(m: re.Match[str], value: date) -> None:
        if _DOC_DATE_BEFORE_RE.search(text[max(0, m.start() - 32): m.start()]):
            out.append((m.start(), value))

    for m in _DATE_ISO_RE.finditer(text):
        try:
            put(m, date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            continue
    for m in _DATE_DMY_RE.finditer(text):
        try:
            put(m, date(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1))))
        except (ValueError, KeyError):
            continue
    for m in _DATE_MDY_RE.finditer(text):
        try:
            put(m, date(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2))))
        except (ValueError, KeyError):
            continue
    return sorted(out, key=lambda item: item[0])


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class Violation:
    """One broken invariant and what was done about it."""

    rule: str
    path: str
    text: str
    action: str
    reason: str
    replacement: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "path": self.path,
            "text": _safe(self.text[:400]),
            "action": self.action,
            "reason": _safe(self.reason),
            "replacement": self.replacement[:400] if self.replacement else None,
        }


@dataclass
class InvariantResult:
    v3: dict[str, Any]
    llm_council: dict[str, Any] | None
    violations: list[Violation] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.violations:
            out[v.rule] = out.get(v.rule, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        actions: dict[str, int] = {}
        for v in self.violations:
            actions[v.action] = actions.get(v.action, 0) + 1
        return {
            "version": INVARIANTS_VERSION,
            "violations_total": len(self.violations),
            "by_rule": self.counts(),
            "by_action": actions,
            "violations": [v.to_dict() for v in self.violations[:200]],
        }


# ---------------------------------------------------------------------------
# Free-text rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Hit:
    rule: str
    reason: str
    replacement: str | None = None


def _g(state: Mapping[str, Any], *keys: str) -> Any:
    node: Any = state
    for key in keys:
        node = node.get(key) if isinstance(node, Mapping) else None
    return node


def _newest(state: Mapping[str, Any]) -> tuple[date | None, str]:
    doc = _g(state, "documents", "newest_issuer_document") or {}
    published = as_date(doc.get("date"))
    what = doc.get("form") or str(doc.get("kind") or "document").replace("_", " ")
    return published, f"{what} dated {published.isoformat()}" if published else ""


def _rule_no_issuer_doc(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    held = int(_g(state, "documents", "issuer_documents_held") or 0)
    cited = int(_g(state, "findings", "primary_source") or 0)
    if not (held and cited) or not _denies_issuer_documents(sentence):
        return None
    _, newest = _newest(state)
    return _Hit(
        RULE_NO_ISSUER_DOC,
        f"it says no issuer filing or release is in evidence, while {held} issuer "
        f"document(s) are recorded (newest: {newest or 'undated'}) and {cited} finding(s) "
        "cite the issuer's own documents",
    )


def _names_newest_form(text: str, state: Mapping[str, Any]) -> bool:
    """The text names the newest held document's form type ("10-Q") or its label
    ("September Quarterly Report", or "Quarterly Report" without the period word)."""
    label = str((_g(state, "documents", "newest_issuer_document") or {}).get("form") or "")
    label = " ".join(label.split()).lower()
    if not label:
        return False
    if any(f.lower() == label for f in _FORM_RE.findall(text)):
        return True
    lowered = " ".join(text.split()).lower()
    words = label.split()
    candidates = {label} | ({" ".join(words[1:])} if len(words) >= 3 else set())
    return any(c and c in lowered for c in candidates)


def _rule_stale_latest(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    """An older DOCUMENT named as the newest. Only a date the sentence ties to the
    document it names (after the "newest/latest" anchor), or an accession the inventory
    dates, is compared — never a covered period (QA review B3)."""
    match = None
    for candidate in _LATEST_DOC_RE.finditer(sentence):
        before = sentence[max(0, candidate.start() - 24): candidate.start()]
        if not _ORDINAL_BEFORE_RE.search(before):
            match = candidate
            break
    if match is None:
        return None
    newest_date, newest_text = _newest(state)
    if newest_date is None:
        return None
    margin = timedelta(days=DOCUMENT_MARGIN_DAYS)
    # Only the anchor's own clause names THE newest document: "the latest filing is the
    # Q3 10-Q, which supersedes the 10-K filed 2026-02-04" dates an older document on
    # purpose (QA re-review X2).
    tail = sentence[match.end():]
    cut = _ANCHOR_CLAUSE_END_RE.search(tail)
    clause = sentence[match.start(): match.end() + (cut.start() if cut else len(tail))]
    dated = _document_dates(clause)
    first_date_at = dated[0][0] if dated else len(clause)
    if _names_newest_form(clause[:first_date_at], state):
        # It names the newest held document's form or label before any date: not stale.
        return None
    named_date: date | None = dated[0][1] if dated else None
    if named_date is None:
        accession = _ACCESSION_RE.search(clause)
        inventory = _g(state, "documents", "inventory") or []
        if accession is not None:
            named_date = next(
                (
                    d
                    for entry in inventory
                    if isinstance(entry, Mapping) and entry.get("ref") == accession.group(0)
                    and (d := as_date(entry.get("date"))) is not None
                ),
                None,
            )
    if named_date is not None and named_date < newest_date - margin:
        return _Hit(
            RULE_STALE_LATEST,
            f"it names a document dated {named_date.isoformat()} as the newest, "
            f"while the issuer's {newest_text} is recorded",
        )
    return None


def _rule_latest_period(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    status = _g(state, "current_period", "status")
    if status not in ("facts_validated", "document_held_not_extracted"):
        return None
    for pattern in _LATEST_UNAVAILABLE_RES:
        m = pattern.search(sentence)
        if not m or not _unqualified_after(sentence, m.end()) or _prefix_qualified_text(
            sentence, m.start()
        ):
            continue
        noun = (m.groupdict().get("noun") or "").lower()
        if noun in _FIGURE_NOUNS and status != "facts_validated":
            # "latest quarter figures are not available" is TRUE when the report is held
            # but no figure was extracted from it.
            continue
        doc = _g(state, "current_period", "document") or {}
        held = (
            f"validated current-period facts ({_g(state, 'current_period', 'period')})"
            if status == "facts_validated"
            else f"the current-period report ({doc.get('form') or 'issuer report'}, "
            f"{doc.get('date') or 'undated'}) is recorded"
        )
        return _Hit(RULE_LATEST_PERIOD, f"it says the latest period is not available, while {held}")
    return None


def _rule_no_recent(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    if not _g(state, "evidence", "recent_issuer_disclosure_cited"):
        return None
    m = _NO_RECENT_RE.search(sentence)
    if (m is None or not _unqualified_after(sentence, m.end())
            or _prefix_qualified_text(sentence, m.start())):
        return None
    _, newest = _newest(state)
    return _Hit(
        RULE_NO_RECENT,
        f"it says there are no recent developments, while a current issuer disclosure "
        f"({newest or 'recent'}) is cited",
    )


def _rule_no_cash_flow(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    match = None
    for verbs, pattern in zip((frozenset(), _ABSENCE_VERBS), _NO_CASH_FLOW_RES, strict=True):
        m = pattern.search(sentence)
        if (m is not None and _unqualified_after(sentence, m.end(), verbs)
                and not _prefix_qualified_text(sentence, m.start())):
            match = m
            break
    if match is None:
        return None
    by_kind = _g(state, "evidence", "cash_flow_findings_by_kind") or {}
    claim = match.group(0).lower()
    # A claim about FREE cash flow is answered only by a free-cash-flow figure; an
    # operating one only by an operating one; a bare "cash flow" by either.
    kind = "free" if "free" in claim else "operating" if "operating" in claim else "any"
    n = int(by_kind.get(kind, _g(state, "evidence", "cash_flow_findings") or 0) or 0)
    if not n:
        return None
    label = "" if kind == "any" else f"{kind} "
    return _Hit(RULE_NO_CASH_FLOW, f"it says no {label}cash flow is available, while {n} "
                f"{label}cash-flow finding(s) or statement figure(s) are recorded")


def _rule_evidence_adequate(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    failed = bool(_g(state, "current_period", "extraction_failed"))
    empty = int(_g(state, "findings", "total") or 0) == 0
    if not (failed or empty) or not _EVIDENCE_ADEQUATE_RE.search(sentence):
        return None
    return _Hit(
        RULE_EVIDENCE_ADEQUATE,
        "it calls the evidence adequate, while "
        + ("the ledger records no findings" if empty else
           "no figure was extracted from the current-period issuer report"),
    )


#: A disagreement id prefix in a disagreement context. It must contain a letter a–f:
#: "disagreement 20261031" is a date, not an id (review P2-8).
_DISAGREEMENT_ID_RE = re.compile(
    r"\bdisagreement[ \t]{1,3}(?:id[ \t]{1,3})?(?P<id>(?=[0-9]{0,7}[a-f])[0-9a-f]{8})\b",
    re.I)


#: ADR-091 — wording that narrates a conflict between findings.
#:
#: Re-review round 3: CONSERVATIVE by design. A missed check is acceptable; a false
#: withhold is not. Only a sentence that explicitly sets two research subjects against
#: each other with a conflict verb counts:
#:
#: * "finding <A> and finding <B> disagree", "(finding_id: <A>) and (finding_id: <B>)
#:   conflict";
#: * "<A> vs <B>" between two finding ids;
#: * "(the) findings / sources / filings conflict | disagree | contradict" followed by
#:   "each other", "one another" or "on <x>";
#: * "conflict | disagreement | inconsistency | contradiction | discrepancy between (the)
#:   (two) findings / sources / filings", or between two finding ids;
#: * "the filing's <metric> … contradicts the release's".
#:
#: "Contested", "different … figures", "differ", "diverge" and "at odds with" are not
#: read: they describe as often as they conflict ("figures differ by region", "the
#: patent is contested in court").
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_ID_REF = rf"\(?(?:finding(?:_id)?[ \t]{{0,2}}:?[ \t]{{0,3}})?{_UUID}\)?"
_PLURAL_SUBJECTS = r"(?:findings|sources|filings)"
_DOC_NOUN = r"(?:filing|release|report|source|finding|document|statement)"
_CONFLICT_VERB = r"(?:conflict|disagree|contradict|are[ \t]{1,3}inconsistent)"
_CONFLICT_WORDS_RE = re.compile(
    rf"{_ID_REF}[ \t]{{1,3}}and[ \t]{{1,3}}{_ID_REF}[ \t]{{1,3}}{_CONFLICT_VERB}\b"
    rf"|\b{_UUID}[ \t]{{1,3}}(?:vs\.?|versus)[ \t]{{1,3}}[0-9a-f]{{8}}\b"
    rf"|\b{_PLURAL_SUBJECTS}[ \t]{{1,3}}(?:conflict|disagree|contradict)[ \t]{{1,3}}"
    r"(?:each[ \t]{1,3}other|one[ \t]{1,3}another|on)\b"
    r"|\b(?:conflicts?|disagreements?|inconsistency|inconsistencies|contradictions?|"
    r"discrepancy|discrepancies)[ \t]{1,3}between[ \t]{1,3}(?:the[ \t]{1,3})?"
    # Round 4: "conflicts between sources of supply" is not about research sources.
    rf"(?:two[ \t]{{1,3}})?(?:{_PLURAL_SUBJECTS}\b(?![ \t]{{1,3}}of\b)|{_ID_REF})"
    rf"|\b{_DOC_NOUN}['’]s[ \t]{{1,3}}(?:[\w$€£.,%/-]{{1,20}}[ \t]{{1,3}}){{0,5}}?"
    rf"contradicts[ \t]{{1,3}}the[ \t]{{1,3}}{_DOC_NOUN}['’]s\b",
    re.IGNORECASE,
)
_CONFLICT_NEGATION_RE = re.compile(
    r"\b(?:no|not|nor|never|without|nothing|none|neither)[ \t]{1,3}(?:\w{1,20}[ \t]{1,3}){0,2}$",
    re.IGNORECASE,
)
#: Re-review R-P1 / round 3: a negation INSIDE the matched words ("the filings do not
#: conflict", "don't disagree", "contradicts nothing").
_NEGATION_IN_SPAN_RE = re.compile(
    r"\b(?:no|not|never|cannot|nothing|none)\b|n['’]t\b", re.IGNORECASE)
#: Round 3: a negation or limiter right AFTER the match ("disagree on timing only",
#: "conflict on nothing", "differ … not in figures").
_NEGATION_AFTER_RE = re.compile(r"\b(?:nothing|not[ \t]{1,3}in|only)\b", re.IGNORECASE)


def _narrates_conflict(sentence: str) -> bool:
    """ADR-091 (review P1-1, re-review rounds 2–3) — a conflict explicitly narrated
    between research subjects, negated neither before, inside nor right after the
    matched words."""
    text = sentence[:MAX_SENTENCE_CHARS]
    for match in _CONFLICT_WORDS_RE.finditer(text):
        before = text[max(0, match.start() - 40): match.start()]
        after = text[match.end(): match.end() + 30]
        if (_CONFLICT_NEGATION_RE.search(before)
                or _NEGATION_IN_SPAN_RE.search(match.group())
                or _NEGATION_AFTER_RE.search(after)):
            continue
        return True
    return False


def _ledger_disagreement_ids(state: Mapping[str, Any]) -> list[str] | None:
    """ADR-086 (review P2-8) — every disagreement id the ledger holds, RESOLVED ones
    included, or ``None`` when that is not known. Only the unresolved ids known, and
    fewer of them than recorded disagreements: a named id may be a resolved one, so
    nothing can be said."""
    every = _g(state, "disagreements", "ids")
    if every is not None:
        return [str(i) for i in every]
    unresolved = _g(state, "disagreements", "unresolved_ids")
    total = _g(state, "disagreements", "total")
    if unresolved is not None and total is not None and int(total) == len(list(unresolved)):
        return [str(i) for i in unresolved]
    return None


def _rule_unbacked_disagreement(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    """A LEDGER disagreement narrated while the ledger records none. Only phrasing that
    refers to the research's own disagreements counts (QA review B2).

    ADR-086 (LIN-6): a sentence naming a disagreement BY ID is held to the ledger's ids
    whenever they are known, even when other disagreements are recorded."""
    if sentence.startswith("Deterministic synthesis:"):
        return None
    known = _ledger_disagreement_ids(state)
    if known is not None:
        named = [m.group("id").lower() for m in _DISAGREEMENT_ID_RE.finditer(sentence)]
        prefixes = {str(k).lower()[:8] for k in known}
        missing = [n for n in named if n not in prefixes]
        if missing:
            return _Hit(RULE_UNKNOWN_DISAGREEMENT_ID,
                        "it cites a disagreement id the research ledger does not hold")
    unresolved = _g(state, "disagreements", "unresolved")
    total = _g(state, "disagreements", "total")
    finding_conflicts = _g(state, "conflicts", "finding_count")
    if isinstance(finding_conflicts, int):
        # ADR-095 review — a conflict between findings backs any such sentence; a
        # statement-line conflict backs only one about ITS line (``slot_backs``).
        if finding_conflicts > 0:
            return None
    elif unresolved != 0 or (total not in (0, None)):
        return None
    if _NEGATED_DISAGREEMENT_RE.search(sentence):
        return None
    # ADR-095 — with no conflict computed, an explicit conflict narrated between
    # findings / sources (the ADR-091 conservative forms) is unbacked too.
    if _LEDGER_DISAGREEMENT_RE.search(sentence) is None and not _narrates_conflict(sentence):
        return None
    if slot_backs(sentence, state):
        return None
    return _Hit(RULE_UNBACKED_DISAGREEMENT,
                "it narrates a research disagreement, while the ledger records none for "
                "what it names")


#: ADR-095 re-review N1 — a statement line by the words a sentence names it with.
#: Bounded alternations. A line not listed backs nothing: missing beats wrong.
_LINE_CASH = "cash"
_LINE_NET_CASH = "net_cash"
_SLOT_LINES = {
    "cash_and_equivalents": _LINE_CASH,
    "cash": _LINE_CASH,
    "net_cash": _LINE_NET_CASH,
    "net_debt": "net_debt",
    "operating_profit": "operating_profit",
    "net_income": "net_income",
    "revenue": "revenue",
    "total_debt": "total_debt",
    "operating_cash_flow": "operating_cash_flow",
}
_LINE_WORDS: dict[str, re.Pattern[str]] = {
    # "cash", "cash and cash equivalents" — not "net cash", "cash flow", "cash burn".
    _LINE_CASH: re.compile(
        r"(?<!net[ \t])\bcash\b(?![ \t]{1,3}(?:flows?|burn|runway|generation|"
        r"position|returns?)\b)", re.IGNORECASE),
    _LINE_NET_CASH: re.compile(r"\bnet[ \t]{1,3}cash\b(?![ \t]{1,3}flows?\b)",
                               re.IGNORECASE),
    "net_debt": re.compile(r"\bnet[ \t]{1,3}debt\b", re.IGNORECASE),
    "operating_profit": re.compile(r"\boperating[ \t]{1,3}(?:profit|income|result)\b",
                                   re.IGNORECASE),
    "net_income": re.compile(r"\bnet[ \t]{1,3}(?:income|profit|earnings)\b",
                             re.IGNORECASE),
    "revenue": re.compile(r"\b(?:revenues?|net[ \t]{1,3}sales|turnover)\b", re.IGNORECASE),
    "total_debt": re.compile(r"\btotal[ \t]{1,3}(?:debt|borrowings)\b", re.IGNORECASE),
    "operating_cash_flow": re.compile(
        r"\b(?:operating[ \t]{1,3}cash[ \t]{1,3}flows?|cash[ \t]{1,3}flows?[ \t]{1,3}"
        r"from[ \t]{1,3}operations)\b", re.IGNORECASE),
}
#: The research fields a line covers: a sentence naming another research field
#: (capex, runway …) is about more than the conflicted lines.
_FIELD_LINES = {
    "metric:revenue": {"revenue"},
    "metric:cash": {_LINE_CASH, _LINE_NET_CASH},
    "metric:net_debt": {"net_debt", _LINE_NET_CASH, "total_debt"},
    "metric:operating_cash_flow": {"operating_cash_flow"},
}
#: "conflicting findings", "between findings": the sentence says FINDINGS conflict, and a
#: statement-line conflict is between the issuer's documents.
_FINDINGS_CONFLICT_RE = re.compile(r"\bfindings?\b", re.IGNORECASE)


def slot_backs(sentence: str, state: Mapping[str, Any]) -> bool:
    """ADR-095 — a sentence narrating a conflict is backed by the (cleaned) statement-line
    conflicts only when it names no finding as a party, names at least one statement
    line, every line it names is conflicted, every research field it names is covered by
    those lines, and a period it names is the conflicted line's period."""
    from app.services import research_fields as rf

    text = sentence[:MAX_SENTENCE_CHARS]
    if _FINDINGS_CONFLICT_RE.search(text):
        return False
    slots = _g(state, "conflicts", "statement_slots")
    lines: dict[str, set[str | None]] = {}
    for item in slots if isinstance(slots, list) else ():
        if not isinstance(item, Mapping):
            continue
        line = _SLOT_LINES.get(str(item.get("metric") or ""))
        if line:
            lines.setdefault(line, set()).add(rf.normalise_period(item.get("period")) or None)
    if not lines:
        return False
    named = {line for line, words in _LINE_WORDS.items() if words.search(text)}
    if not named or not named <= set(lines):
        return False
    for key in rf.fields_mentioned(text):
        if not (_FIELD_LINES.get(key, set()) & named):
            return False
    wanted = rf.normalise_period(rf.requested_period(text))
    return not wanted or all(wanted in lines[line] for line in named)


def _rule_unbacked_challenge(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    """ADR-095 review — "the red team disputed …" is backed by an UNRESOLVED Red Team
    challenge (``canonical_state.challenges``), not by a conflict between findings. With
    the count unknown nothing is said."""
    if _RED_TEAM_DISPUTE_RE.search(sentence[:MAX_SENTENCE_CHARS]) is None:
        return None
    unresolved = _g(state, "challenges", "unresolved")
    if not isinstance(unresolved, int) or unresolved > 0:
        return None
    if _NEGATED_DISAGREEMENT_RE.search(sentence):
        return None
    return _Hit(RULE_UNBACKED_CHALLENGE,
                "it says the Red Team disputed a finding, while no Red Team challenge "
                "is unresolved")


def _rule_conflict_count(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    """ADR-095 — "N unresolved disagreement(s)" is the number of conflicts the platform
    computed (the deterministic chair sentence has its own repair)."""
    if sentence.startswith("Deterministic synthesis:"):
        return None
    count = _g(state, "conflicts", "count")
    if not isinstance(count, int):
        return None
    for m in _CONFLICT_COUNT_RE.finditer(sentence[:MAX_SENTENCE_CHARS]):
        said = int(m.group(1))
        if said == count:
            continue
        fixed = sentence[: m.start(1)] + str(count) + sentence[m.end(1):]
        return _Hit(RULE_CONFLICT_COUNT,
                    f"it states {said} unresolved disagreement(s) while the platform "
                    f"computed {count} conflict(s) between the run's findings",
                    fixed)
    return None


def _rule_det_counts(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    m = _DET_STATE_RE.match(sentence.strip())
    if not m:
        return None
    total = int(_g(state, "findings", "total") or 0)
    verified = int(_g(state, "findings", "verified") or 0)
    gaps = int(_g(state, "gaps", "open") or 0)
    unresolved = _g(state, "disagreements", "unresolved")
    said_unresolved = int(m.group(4))
    if int(m.group(1)) == total and (unresolved is None or said_unresolved == unresolved):
        return None
    replacement = (
        f"Deterministic synthesis: {total} finding(s), {verified} verified, {gaps} open "
        f"gap(s), {unresolved if unresolved is not None else said_unresolved} unresolved "
        "disagreement(s)."
    )
    return _Hit(RULE_FINDING_COUNT,
                f"it states {m.group(1)} finding(s) while the ledger records {total}",
                replacement)


def _fallback_sentence(state: Mapping[str, Any]) -> str | None:
    reason = _g(state, "chair", "fallback_reason")
    if reason == FALLBACK_NOT_CONVENED:
        why = REFUSAL_WORDS.get(str(_g(state, "council", "refusal_reason")),
                                "it was refused")
        return (f"The council did not convene because {why}, so no synthesis was "
                "attempted; the findings stand in the research record as recorded.")
    if reason == FALLBACK_CHAIR_FAILED:
        return ("The chair model did not return a usable reply, so this is the ledger's "
                "own state rather than an interpretation of it.")
    return None


def _rule_fallback_reason(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    if not _NO_MODEL_RE.search(sentence):
        return None
    if _g(state, "chair", "model_available") is not True:
        return None
    if _g(state, "chair", "fallback_reason") in (FALLBACK_NO_MODEL, None):
        return None
    replacement = _fallback_sentence(state)
    if replacement is None:
        return None
    return _Hit(RULE_FALLBACK_REASON,
                "it says no model was available, while a chair model was routed and "
                f"the fallback reason is {_g(state, 'chair', 'fallback_reason')}",
                replacement)


#: ADR-091 — the V2 schema's count of required REPORT FIELDS left empty, worded as
#: research gaps ("Blocking research gaps: 27", "27 blocking research gaps identified").
_BLOCKING_GAPS_RE = re.compile(
    r"\b(?P<n1>\d{1,4})[ \t]{1,3}blocking[ \t]{1,3}research[ \t]{1,3}gaps\b"
    r"|\bBlocking[ \t]{1,3}research[ \t]{1,3}gaps:[ \t]{0,3}(?P<n2>\d{1,4})\b",
    re.IGNORECASE,
)


def _rule_blocking_gaps(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    """ADR-091 (CLLS6-3, PYPL4-6) — a V2 "N blocking research gaps" count that is not
    the ledger's open-gap count is a status line contradicting the canonical state. It
    counts required report fields (identity.isin, self_critique …), so it is repaired to
    say so, with the ledger's count beside it."""
    match = _BLOCKING_GAPS_RE.search(sentence)
    if match is None:
        return None
    open_gaps = _g(state, "gaps", "open")
    said = int(match.group("n1") or match.group("n2"))
    if not isinstance(open_gaps, int) or said == open_gaps:
        return None
    if match.group("n1"):
        words = (f"{said} required report field(s) not yet filled (the research ledger "
                 f"records {open_gaps} open research gaps)")
    else:
        words = (f"Required report fields not yet filled: {said} (the research ledger "
                 f"records {open_gaps} open research gaps)")
    return _Hit(
        RULE_STATUS_LINE,
        f"the V2 count is of required report fields, not research gaps; the ledger "
        f"records {open_gaps} open gaps",
        sentence[: match.start()] + words + sentence[match.end():],
    )


#: ADR-094 — a DISCLOSURE-absence claim: "not disclosed", "no segment split", "lacks a
#: breakdown". Deliberately not "no evidence of …" — "no evidence of deposit outflows" is
#: a statement about the company, not about what was disclosed. Bounded alternation.
_DISCLOSURE_ABSENCE_RE = re.compile(
    r"\b(?:not[ \t]{1,3}(?:separately[ \t]{1,3})?(?:disclosed|reported|provided|"
    r"broken[ \t]{1,3}out|quantified|given)|undisclosed|"
    r"(?:does|did|do)[ \t]{1,3}not[ \t]{1,3}(?:disclose|report|provide|break[ \t]{1,3}out)|"
    r"no[ \t]{1,3}(?:disclosed[ \t]{1,3})?(?:disclosure|breakdown|split|data|figures?)"
    r"[ \t]{1,3}(?:on|of|for|by|beyond)|"
    r"no[ \t]{1,3}\w{3,20}[ \t]{1,3}(?:split|breakdown)|"
    r"lacks?[ \t]{1,3}(?:an?[ \t]{1,3}|any[ \t]{1,3})?(?:\w{3,20}[ \t]{1,3})?"
    r"(?:disclosure|breakdown|split|figures?|data))\b",
    re.IGNORECASE,
)
#: Where one clause ends: punctuation, or a conjunction that starts another claim.
_CLAUSE_SPLIT_RE = re.compile(r"[;,:.()]|\b(?:and|but|while|whereas|although)\b",
                              re.IGNORECASE)
#: How far around an absence cue the denied topic must be named.
_ABSENCE_WINDOW_BEFORE = 80
_ABSENCE_WINDOW_AFTER = 120


def absence_claims(sentence: str, *, strict: bool = False) -> list[tuple[str, ...]]:
    """ADR-094 — what a sentence says is NOT disclosed: one tuple of lexicon topics per
    absence cue, read in the cue's own clause. ``strict``: only topics the clause NAMES
    by the lexicon's own mentions ("dividend policy", "segment revenue"), never a broad
    question cue ("board" → governance, "segment margins" → segment + margins).

    A tuple is the denied thing as a whole: "segment margins are not disclosed" denies
    segment × margin, and only a source carrying EVERY topic of it contradicts it."""
    from app.services.agents.document_reread import topics_asked, topics_named

    read = topics_named if strict else topics_asked
    out: list[tuple[str, ...]] = []
    text = sentence[:MAX_SENTENCE_CHARS]
    for match in _DISCLOSURE_ABSENCE_RE.finditer(text):
        # The cue's own CLAUSE only: "revenue rose 5%, capex was not disclosed" denies
        # capex, not revenue. A miss is better than withholding a true sentence.
        before = text[max(0, match.start() - _ABSENCE_WINDOW_BEFORE): match.start()]
        before = _CLAUSE_SPLIT_RE.split(before)[-1]
        after = _CLAUSE_SPLIT_RE.split(text[match.end(): match.end() + _ABSENCE_WINDOW_AFTER])[0]
        topics = tuple(read(f"{before} {match.group(0)} {after}"))
        if topics and topics not in out:
            out.append(topics)
    return out


def absence_topics(sentence: str) -> tuple[str, ...]:
    """Every topic any absence cue of the sentence denies (``()``: no absence claim)."""
    out: list[str] = []
    for claim in absence_claims(sentence):
        out.extend(t for t in claim if t not in out)
    return tuple(out)


def _rule_absence_located(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    """ADR-094 — "X is not disclosed" while the gap queue located X in a held primary
    document. Strict (review): X must be NAMED by the lexicon, located for the period the
    sentence speaks of (or for an unknown one), and every topic of the denied thing must
    be located."""
    located = _g(state, "gap_resolution", "located")
    if not isinstance(located, list) or not located:
        return None
    from app.services import research_fields as rf

    wanted = rf.period_rank(rf.requested_period(sentence[:MAX_SENTENCE_CHARS]))
    usable: set[str] = set()
    for entry in located:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("topic"), str):
            continue
        have = rf.period_rank(entry.get("period")) if entry.get("period") else None
        # Same year AND same kind of period (review B): an H1 2026 location does not
        # answer "FY2026 full-year revenue is not disclosed".
        if wanted is None or have is None or have == wanted:
            usable.add(str(entry["topic"]))
    for claim in absence_claims(sentence, strict=True):
        if set(claim) <= usable:
            from app.services.agents.document_reread import label_of

            return _Hit(RULE_ABSENCE_CONTRADICTED,
                        f"it says {label_of(claim[0])} is not disclosed, but the research "
                        "located it in the company's held documents")
    return None


_RECORD_RULES = (
    _rule_blocking_gaps,
    _rule_no_issuer_doc,
    _rule_stale_latest,
    _rule_latest_period,
    _rule_no_recent,
    _rule_no_cash_flow,
    _rule_evidence_adequate,
)
_CHAIR_RULES = (
    _rule_det_counts,
    _rule_fallback_reason,
    *_RECORD_RULES,
    _rule_conflict_count,
    _rule_unbacked_disagreement,
    _rule_unbacked_challenge,
    _rule_absence_located,
)
#: ADR-095 — editor-written professional prose (with or without citations): the record
#: rules and the conflict rules; counts and council narration stay chair-only.
_PROSE_RULES = (
    *_RECORD_RULES,
    _rule_conflict_count,
    _rule_unbacked_disagreement,
    _rule_unbacked_challenge,
)


def check_sentence(
    sentence: str, state: Mapping[str, Any], *, site: str = SITE_RECORD
) -> _Hit | None:
    """The first invariant one sentence breaks, or ``None``. Pure. A sentence longer
    than ``MAX_SENTENCE_CHARS`` is not checked."""
    if len(sentence) > MAX_SENTENCE_CHARS:
        return None
    rules = (_CHAIR_RULES if site == SITE_CHAIR
             else _PROSE_RULES if site in (SITE_SYNTHESIS, SITE_REPORT_PROSE)
             else _RECORD_RULES)
    for rule in rules:
        hit = rule(sentence, state)
        if hit is not None:
            return hit
    return None


# ---------------------------------------------------------------------------
# The enforcer
# ---------------------------------------------------------------------------


def _safe(text: str) -> str:
    """Reasons quote document headlines (issuer text); they pass the same
    neutralisation V2 applies to external headlines, since they are written into the
    report after its safety scan ran."""
    from app.schemas.catalyst import neutralize_forbidden_terms

    return neutralize_forbidden_terms(text) or ""


def withheld_notice(reason: str) -> str:
    return f"[Withheld at report assembly: {_safe(reason)}.]"


class _Enforcer:
    def __init__(
        self,
        state: Mapping[str, Any],
        sources: Mapping[str, claim_checks.ClaimSource] | None = None,
        hierarchy: Any = None,
    ) -> None:
        self.state = state
        #: ADR-093 — the issuer's segments and business lines (``None``: unknown).
        self.hierarchy = hierarchy
        self.violations: list[Violation] = []
        #: ADR-086 — the run's findings by id, for the chair claim check. ``None``: no
        #: claim is checked against what it cites.
        self.sources = sources
        #: ADR-091 — every finding id the ledger holds, when the payload lists them all
        #: (``None``: a cited id cannot be proved absent).
        self.finding_ids: set[str] | None = None

    def _cited_exist(self, ids: list[str]) -> _Hit | None:
        """ADR-091 — every finding id a chair sentence cites is in the ledger."""
        if self.finding_ids is not None and any(i not in self.finding_ids for i in ids):
            return _Hit(RULE_CITED_FINDING_UNKNOWN,
                        "it cites a finding the research ledger does not hold")
        return None

    def _conflict_backed(self, sentence: str, ids: list[str]) -> _Hit | None:
        """ADR-091 — a conflict narrated between cited findings is one the ledger
        records: some recorded disagreement pairs two of the cited findings."""
        pairs = _g(self.state, "disagreements", "finding_pairs")
        if not isinstance(pairs, list) or len(ids) < 2:
            return None
        if not _narrates_conflict(sentence):
            return None
        wanted = set(ids)
        for pair in pairs:
            if isinstance(pair, (list, tuple)) and len(pair) == 2 and {
                str(pair[0]).lower(), str(pair[1]).lower()
            } <= wanted:
                return None
        return _Hit(RULE_PHANTOM_CONFLICT,
                    "it narrates a conflict between findings no recorded disagreement pairs")

    @staticmethod
    def _absence_cited(
        sentence: str, found: "list[claim_checks.ClaimSource]"
    ) -> _Hit | None:
        """ADR-094 (CFR3) — "no segment split beyond X" while citing findings that state
        segment figures: the sentence denies what it cites."""
        claims = absence_claims(sentence)
        if not claims:
            return None
        from app.services.agents.document_reread import label_of, metrics_carried

        for claim in claims:
            for source in found:
                # Every topic of the denied thing: a finding stating segment REVENUE does
                # not contradict "segment margins are not disclosed".
                if set(claim) <= metrics_carried(source.text or "", claim):
                    return _Hit(RULE_ABSENCE_CONTRADICTED,
                                f"it says {label_of(claim[0])} is not disclosed while the "
                                "finding it cites states it")
        return None

    def _claim(self, sentence: str, cited: tuple[str, ...],
               site: str = SITE_CHAIR) -> _Hit | None:
        """ADR-086 — a chair claim checked against the findings it cites. ADR-093: an
        editor-written synthesis sentence too (``SITE_SYNTHESIS``), and both are held
        to the issuer's segment hierarchy."""
        ids = [i for i in dict.fromkeys([*cited, *claim_checks.cited_ids(sentence)]) if i]
        if site == SITE_CHAIR:
            hit = self._cited_exist(ids) or self._conflict_backed(sentence, ids)
            if hit is not None:
                return hit
        found = [self.sources[i] for i in ids if self.sources and i in self.sources]
        if found:
            # ADR-094 — a sentence denying what a finding it cites states.
            hit = self._absence_cited(sentence, found)
            if hit is not None:
                return hit
            verdict = claim_checks.check_claim(sentence, found, self.hierarchy)
        else:
            verdict = claim_checks.check_hierarchy(sentence, self.hierarchy)
            if (verdict is None and not ids and self.hierarchy and self.sources
                    and self.finding_ids is not None):
                # ADR-093 — a sentence citing nothing, when the payload lists EVERY
                # finding: a metric every finding states for one segment only is that
                # segment's, whatever the sentence leaves out ("rising nonperforming
                # assets" over one segment's figures).
                verdict = claim_checks.metric_scope(
                    sentence, list(self.sources.values()), self.hierarchy,
                    limit=MAX_LEDGER_SOURCES)
        if verdict is None:
            return None
        return _Hit(verdict.rule, verdict.reason, verdict.replacement)

    def _line(self, line: str, path: str, site: str, reasons: list[str],
              cited: tuple[str, ...] = ()) -> tuple[str | None, bool]:
        """One line: ``(the line as it stays, changed)``; the line is ``None`` when every
        sentence in it was withheld. Separators between kept sentences, the bullet marker
        and trailing whitespace are kept as written (QA review M1)."""
        bullet = _BULLET_RE.match(line)
        prefix = bullet.group(0) if bullet else line[: len(line) - len(line.lstrip())]
        body = line[len(prefix):]
        core = body.rstrip()
        if not core:
            return line, False
        pieces: list[tuple[str, str]] = []
        start = 0
        for m in _SENTENCE_RE.finditer(core):
            pieces.append((core[start: m.start()], m.group(0)))
            start = m.end()
        pieces.append((core[start:], ""))
        kept: list[tuple[str, str]] = []
        changed = False
        for sentence, separator in pieces:
            hit = check_sentence(sentence, self.state, site=site)
            if (hit is None and site in (SITE_CHAIR, SITE_SYNTHESIS)
                    and len(sentence) <= MAX_SENTENCE_CHARS):
                hit = self._claim(sentence, cited, site)
            elif hit is None and site == SITE_LEGACY and self.hierarchy:
                # ADR-093 — the legacy council's claims cite run-local evidence handles,
                # so only the hierarchy rules (no sources) apply to them.
                verdict = claim_checks.check_hierarchy(sentence, self.hierarchy)
                if verdict is not None:
                    hit = _Hit(verdict.rule, verdict.reason, verdict.replacement)
            if hit is None:
                kept.append((sentence, separator))
                continue
            changed = True
            if hit.replacement is not None:
                kept.append((hit.replacement, separator))
                self.violations.append(Violation(hit.rule, path, sentence, "repaired",
                                                 hit.reason, hit.replacement))
            else:
                reasons.append(hit.reason)
                self.violations.append(Violation(hit.rule, path, sentence, "withheld",
                                                 hit.reason))
        if not changed:
            return line, False
        if not kept:
            return None, True
        text = "".join(s + sep for s, sep in kept[:-1]) + kept[-1][0]
        return prefix + text + body[len(core):], True

    def text(self, value: Any, path: str, site: str, cited: tuple[str, ...] = ()) -> Any:
        """Check one text; return it repaired, with offending sentences withheld. Only
        the withheld span is removed: a bullet whose every sentence is withheld is
        dropped, its neighbours are not. A text left with nothing is replaced by a notice
        naming the reason."""
        if not isinstance(value, str) or not value.strip():
            return value
        lines: list[str] = []
        reasons: list[str] = []
        changed = False
        for line in value.split("\n"):
            kept_line, line_changed = self._line(line, path, site, reasons, cited)
            changed = changed or line_changed
            if kept_line is not None:
                lines.append(kept_line)
        if not changed:
            return value
        if not any(line.strip() for line in lines):
            return withheld_notice(reasons[0] if reasons else "it contradicted the record")
        return "\n".join(lines)


def _labelled_ids(item: Mapping[str, Any], by_label: Mapping[str, str]) -> tuple[str, ...]:
    """ADR-093 — the finding ids an editor-written sentence cites by label ("F4")."""
    labels = item.get("labels")
    if not isinstance(labels, list):
        return ()
    return tuple(dict.fromkeys(
        by_label[str(label)] for label in labels if str(label) in by_label))


def _walk_report(enforcer: _Enforcer, report: Any) -> None:
    if not isinstance(report, dict):
        return
    raw_labels = report.get("finding_labels")
    by_label = ({str(label): str(fid).lower() for fid, label in raw_labels.items()}
                if isinstance(raw_labels, Mapping) else {})
    for i, section in enumerate(report.get("sections") or ()):
        if not isinstance(section, dict):
            continue
        base = f"professional_research.sections[{i}]"
        lead = section.get("lead")
        if isinstance(lead, dict):
            # ADR-093: a lead that cites its findings is held to them, like the chair.
            cited = _labelled_ids(lead, by_label)
            lead["text"] = enforcer.text(lead.get("text"), f"{base}.lead",
                                         SITE_SYNTHESIS if cited else SITE_REPORT_PROSE,
                                         cited)
        for j, sentence in enumerate(section.get("sentences") or ()):
            if isinstance(sentence, dict):
                cited = _labelled_ids(sentence, by_label)
                sentence["text"] = enforcer.text(
                    sentence.get("text"), f"{base}.sentences[{j}]",
                    SITE_SYNTHESIS if cited else SITE_REPORT_PROSE, cited)
        for j, finding in enumerate(section.get("findings") or ()):
            if isinstance(finding, dict):
                finding["statement"] = enforcer.text(
                    finding.get("statement"), f"{base}.findings[{j}]", SITE_RECORD)


def _walk_legacy(enforcer: _Enforcer, council: Any) -> None:
    if not isinstance(council, dict):
        return
    for i, agent in enumerate(council.get("agents") or ()):
        if not isinstance(agent, dict):
            continue
        base = f"llm_council.agents[{i}]"
        for key in ("summary", "synthesis"):
            agent[key] = enforcer.text(agent.get(key), f"{base}.{key}", SITE_LEGACY)
        for list_key, text_key in (
            ("key_points", "claim"),
            ("implications", "statement"),
            ("risks_or_gaps", "item"),
        ):
            for j, item in enumerate(agent.get(list_key) or ()):
                if isinstance(item, dict):
                    item[text_key] = enforcer.text(
                        item.get(text_key), f"{base}.{list_key}[{j}]", SITE_LEGACY)
        claims = agent.get("unsupported_claims")
        if isinstance(claims, list):
            agent["unsupported_claims"] = [
                enforcer.text(c, f"{base}.unsupported_claims[{j}]", SITE_LEGACY)
                for j, c in enumerate(claims)
            ]


def _structured(enforcer: _Enforcer, v3: dict[str, Any], legacy: dict[str, Any] | None) -> None:
    state = enforcer.state
    council = v3.get("council")
    if isinstance(council, dict) and council:
        expected = {
            "finding_count": _g(state, "findings", "total"),
            "verified_finding_count": _g(state, "findings", "verified"),
            "primary_source_finding_count": _g(state, "findings", "primary_source"),
            "unresolved_disagreement_count": _g(state, "disagreements", "unresolved"),
        }
        for key, value in expected.items():
            if value is None or council.get(key) == value:
                continue
            enforcer.violations.append(Violation(
                RULE_COUNCIL_COUNTS, f"council.{key}", str(council.get(key)), "repaired",
                f"the council block said {council.get(key)}; the ledger records {value}",
                str(value),
            ))
            council.setdefault("council_" + key, council.get(key))
            council[key] = value

    chair = v3.get("chair")
    known = _g(state, "disagreements", "unresolved_ids")
    if known is None and _g(state, "conflicts", "finding_count") == 0:
        # ADR-095 — no conflict was computed between the run's findings, so no chair id
        # names one (a replay of a report whose ledger recorded phantom pairs).
        known = []
    if isinstance(chair, dict) and known is not None:
        ids = [str(i) for i in chair.get("unresolved_disagreement_ids") or ()]
        unknown = [i for i in ids if i not in set(known)]
        if unknown:
            chair["unresolved_disagreement_ids"] = [i for i in ids if i in set(known)]
            chair["dropped_disagreement_ids"] = unknown
            for item in unknown:
                enforcer.violations.append(Violation(
                    RULE_UNKNOWN_DISAGREEMENT_ID, "chair.unresolved_disagreement_ids", item,
                    "dropped", "the ledger records no unresolved disagreement with this id",
                ))

    degraded = v3.get("degraded")
    refusal = _g(state, "council", "refusal_reason")
    chair_failed = (
        _g(state, "chair", "fallback_reason") == FALLBACK_CHAIR_FAILED
        and _g(state, "chair", "model_available") is True
    )
    degraded_list: list[Any] = degraded if isinstance(degraded, list) else []
    for i, entry in enumerate(degraded_list):
        if not isinstance(entry, str):
            continue
        fixed, why_text = None, ""
        if _DEGRADED_NO_FINDINGS in entry and refusal and refusal != "no_findings":
            why = REFUSAL_WORDS.get(str(refusal), str(refusal))
            fixed = entry.replace(_DEGRADED_NO_FINDINGS, f"({why})")
            why_text = f"the council was refused because {why}, not for want of findings"
        elif chair_failed and entry.endswith(_DEGRADED_NO_MODEL):
            fixed = entry[: -len(_DEGRADED_NO_MODEL)] + _DEGRADED_CHAIR_FAILED
            why_text = "a chair model was routed; its reply was not usable"
        if fixed is not None:
            degraded_list[i] = fixed
            enforcer.violations.append(Violation(
                RULE_DEGRADED_REASON, f"degraded[{i}]", entry, "repaired", why_text, fixed,
            ))

    if isinstance(legacy, dict) and legacy.get("agents") and _g(state, "council", "degraded"):
        completed = legacy.get("agents_completed")
        normal = (
            not legacy.get("agents_failed")
            and not legacy.get("agents_skipped")
            and legacy.get("committee_label_basis") != "deterministic_fallback"
        )
        if normal:
            # Repaired where the page reads it: ``canonical_state.council.status_text``
            # replaces the legacy count in every status line (the V2 block itself is
            # left as V2 wrote it).
            text = _g(state, "council", "status_text")
            enforcer.violations.append(Violation(
                RULE_COUNCIL_STATUS, "llm_council.agents_completed",
                f"{completed} agents completed", "repaired",
                "the council was degraded: " + "; ".join(
                    _g(state, "council", "degraded_reasons") or ()),
                text,
            ))


def _status_violation(enforcer: _Enforcer, path: str, said: Any, value: Any,
                      reason: str) -> None:
    enforcer.violations.append(Violation(
        RULE_STATUS_LINE, path, str(said), "repaired", reason, str(value),
    ))


def _status_lines(enforcer: _Enforcer, v3: Mapping[str, Any]) -> None:
    """ADR-091 — a status line the page shows that contradicts the canonical state is a
    violation. Each is repaired WHERE THE PAGE READS IT: ``canonical_state.status``
    replaces the payload field in every status line (the field itself stays as its
    producer wrote it, e.g. the cost basis's investigator search count)."""
    status = _g(enforcer.state, "status")
    if not isinstance(status, Mapping):
        return
    consumption = v3.get("consumption")
    searches = _g(enforcer.state, "status", "web_searches", "total")
    if isinstance(consumption, Mapping) and isinstance(searches, int):
        said = consumption.get("web_search_calls")
        if isinstance(said, int) and said != searches:
            _status_violation(
                enforcer, "consumption.web_search_calls", said, searches,
                f"the line counted the investigators' searches only; the run made "
                f"{searches} web searches, "
                f"{_g(enforcer.state, 'status', 'web_searches', 'web_stage')} of them in "
                "the company web stage")
    _search_lines(enforcer, v3)
    statements = v3.get("financial_statements_state")
    interim = _g(enforcer.state, "status", "latest_interim", "label")
    periods = statements.get("reporting_periods") if isinstance(statements, Mapping) else None
    if isinstance(periods, Mapping) and interim and not periods.get("latest_interim"):
        _status_violation(
            enforcer, "financial_statements_state.reporting_periods.latest_interim",
            None, interim,
            "no half-year label is held, but a part-year period is: the latest interim "
            "is not absent")
    loop = v3.get("loop")
    gaps_open = _g(enforcer.state, "gaps", "open")
    if isinstance(loop, Mapping) and isinstance(gaps_open, int):
        said = loop.get("gaps_open")
        if isinstance(said, int) and said != gaps_open:
            _status_violation(
                enforcer, "loop.gaps_open", said, gaps_open,
                f"the loop counted every gap it recorded ({said}), closed ones included; "
                f"the ledger records {gaps_open} open")


def _search_lines(enforcer: _Enforcer, v3: Mapping[str, Any]) -> None:
    """ADR-095 — the professional report's "N searches ran" is the web stage's EXECUTED
    count in the one search-status block (``canonical_state.searches``). A stated count
    that differs is repaired IN THE PAYLOAD (ADR-095 review: every reader of the stored
    report sees the same count, not only the page), and so is the "(N of M searches
    ran)" label beside it."""
    stage = _g(enforcer.state, "searches", "web_stage_total")
    if not isinstance(stage, Mapping):
        return
    report = v3.get("professional_research")
    if not isinstance(report, Mapping):
        return
    for i, section in enumerate(report.get("sections") or ()):
        web = section.get("web_research") if isinstance(section, Mapping) else None
        if not isinstance(web, dict):
            continue
        repaired = False
        for key, truth in (("searches_run", stage.get("executed")),
                           ("searches_planned", stage.get("planned")),
                           ("searches_succeeded", stage.get("succeeded")),
                           ("searches_failed", stage.get("failed"))):
            said = web.get(key)
            if not isinstance(truth, int) or not isinstance(said, int) or said == truth:
                continue
            enforcer.violations.append(Violation(
                RULE_SEARCH_COUNT,
                f"professional_research.sections[{i}].web_research.{key}",
                str(said), "repaired",
                f"the web stage's {key.replace('searches_', '')} searches are "
                f"{truth} in the run's search status", str(truth),
            ))
            web[key] = truth
            repaired = True
        label = web.get("label")
        if repaired and isinstance(label, str):
            web["label"] = _SEARCHES_RAN_RE.sub(
                f"({stage.get('executed')} of {stage.get('planned')} searches ran)",
                label[:MAX_SENTENCE_CHARS], count=1)


#: ADR-095 review — the web stage's "(N of M searches ran)" label words.
_SEARCHES_RAN_RE = re.compile(r"\(\d{1,6}[ \t]of[ \t]\d{1,6}[ \t]searches[ \t]ran\)")


def enforce(
    v3: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    llm_council: Mapping[str, Any] | None = None,
) -> InvariantResult:
    """Check every report-status sentence against the canonical state, repairing what
    can be repaired and withholding the rest. Works on COPIES; never raises on a
    partial payload."""
    out = copy.deepcopy(dict(v3))
    legacy = copy.deepcopy(dict(llm_council)) if isinstance(llm_council, Mapping) else None
    from app.services.ledger.segment_hierarchy import SegmentHierarchy

    hierarchy = SegmentHierarchy.from_dict(out.get("segment_hierarchy"))
    enforcer = _Enforcer(state, sources=finding_sources(out.get("findings"), hierarchy),
                         hierarchy=hierarchy)
    enforcer.finding_ids = ledger_finding_ids(out.get("findings"), state)

    _structured(enforcer, out, legacy)
    _status_lines(enforcer, out)

    chair = out.get("chair")
    if isinstance(chair, dict):
        chair["synthesis"] = enforcer.text(chair.get("synthesis"), "chair.synthesis",
                                           SITE_CHAIR)
        for i, point in enumerate(chair.get("key_points") or ()):
            if isinstance(point, dict):
                # ADR-086: a key point cites its findings in a field and in its text.
                cited = tuple(dict.fromkeys(
                    [str(point.get("finding_id") or "").lower()]
                    + claim_checks.cited_ids(str(point.get("text") or ""))
                ))
                point["text"] = enforcer.text(point.get("text"), f"chair.key_points[{i}]",
                                              SITE_CHAIR, cited=cited)
    for i, finding in enumerate(out.get("findings") or ()):
        if isinstance(finding, dict):
            finding["statement"] = enforcer.text(finding.get("statement"),
                                                 f"findings[{i}]", SITE_RECORD)
    for i, gap in enumerate(out.get("gaps") or ()):
        if isinstance(gap, dict):
            gap["description"] = enforcer.text(gap.get("description"), f"gaps[{i}]",
                                               SITE_RECORD)
    _walk_report(enforcer, out.get("professional_research"))
    _walk_legacy(enforcer, legacy)
    return InvariantResult(v3=out, llm_council=legacy, violations=enforcer.violations)


def ledger_finding_ids(findings: Any, state: Mapping[str, Any]) -> set[str] | None:
    """ADR-091 — every finding id of the run, or ``None`` when the payload's list is not
    known to be complete (it holds fewer findings than the ledger counted)."""
    ids = {
        str(f.get("finding_id") or f.get("id") or "").lower()
        for f in findings or () if isinstance(f, Mapping)
    }
    ids.discard("")
    total = _g(state, "findings", "total")
    if not isinstance(total, int) or len(ids) < total:
        return None
    return ids


def finding_sources(findings: Any, hierarchy: Any = None
                    ) -> dict[str, claim_checks.ClaimSource]:
    """ADR-086 — the payload's findings by id, as claim-check sources (ADR-093: scoped
    by the issuer's segment hierarchy when one is known)."""
    out: dict[str, claim_checks.ClaimSource] = {}
    for finding in findings or ():
        if isinstance(finding, Mapping):
            source = claim_checks.source_of_finding(dict(finding), hierarchy)
            if source is not None:
                out[source.source_id.lower()] = source
    return out


#: Keys of the V2 report content whose values are identifiers or vocabulary, never
#: sentences about the research.
_CONTENT_SKIP_KEYS: frozenset[str] = frozenset({
    "type", "provenance", "classification", "url", "source_url", "id", "source_id",
    "citation_ids", "evidence_ids", "ticker", "exchange", "isin", "lei", "slug",
})


#: The V2 thin-evidence line about the primary issuer document
#: (``final_report_generator._thin_missing_groups``). V2 writes it from its OWN evidence
#: pack, before the research ledger runs.
PRIMARY_DOC_GROUP_CATEGORY = "Primary issuer document"


def primary_document_group(state: Mapping[str, Any]) -> dict[str, str] | None:
    """ADR-086 — the V2 "Primary issuer document" line DERIVED from the canonical
    state, or ``None`` when the ledger holds no issuer document (the V2 line stands)."""
    held = int(_g(state, "documents", "issuer_documents_held") or 0)
    if held <= 0:
        return None
    published, described = _newest(state)
    failed = bool(_g(state, "current_period", "extraction_failed"))
    detail = (
        f"The platform holds {held} issuer primary document(s)"
        + (f"; the newest is the {described}" if published else "")
        + "."
        + (" No statement figure was extracted from the current-period report."
           if failed else "")
    )
    return {
        "category": PRIMARY_DOC_GROUP_CATEGORY,
        "status": "held; figures not extracted" if failed else "held",
        "detail": _safe(detail),
    }


def _repair_primary_document_group(enforcer: "_Enforcer", content: dict[str, Any]) -> None:
    thin = content.get("thin_evidence_state")
    groups = thin.get("missing_evidence_groups") if isinstance(thin, dict) else None
    if not isinstance(groups, list):
        return
    derived = primary_document_group(enforcer.state)
    if derived is None:
        return
    for i, group in enumerate(groups):
        if not isinstance(group, dict) or group.get("category") != PRIMARY_DOC_GROUP_CATEGORY:
            continue
        if group.get("detail") == derived["detail"]:
            continue
        enforcer.violations.append(Violation(
            RULE_PRIMARY_DOC_GROUP,
            f"content.thin_evidence_state.missing_evidence_groups[{i}]",
            str(group.get("detail") or ""), "repaired",
            "the V2 evidence pack was assembled before the research ledger ran; the "
            "ledger holds issuer primary documents",
            derived["detail"],
        ))
        groups[i] = {**group, **derived}


#: ADR-091 — the V2 "not sourced" note on a report whose statements view holds figures.
_FUNDAMENTALS_NOT_SOURCED = "Financial-statement fundamentals not sourced"


def statement_facts_note(state: Mapping[str, Any]) -> str | None:
    """ADR-091 — the fundamentals line DERIVED from the canonical state, or ``None`` when
    the statements view holds no figure (the V2 line stands)."""
    facts = _g(state, "status", "statement_facts")
    if not isinstance(facts, Mapping) or not facts.get("held"):
        return None
    tagged = int(facts.get("sec_tagged_slots") or 0)
    return (
        f"Financial-statement figures are held: {int(facts.get('slots') or 0)} statement "
        f"line(s) read from the issuer's own documents"
        + (f", {tagged} of them from the XBRL tags in its SEC filings" if tagged else "")
        + " (see Key financials). This note was written before the research ledger ran."
    )


def _repair_statement_status(enforcer: "_Enforcer", content: dict[str, Any]) -> None:
    """ADR-091 (JPM2-D4, PYPL4-6, CLLS6-3) — V2 status lines written from the V2 pack
    before the ledger ran: "fundamentals not sourced", "Regulator structured financial
    facts (SEC XBRL): not sourced", "Issuer newsroom: not sourced". Repaired from the
    canonical state when it holds what the line denies."""
    note = statement_facts_note(enforcer.state)
    if note is not None:
        for path, holder, key in (
            ("content.data_availability_summary.fundamentals_note",
             content.get("data_availability_summary"), "fundamentals_note"),
            ("content.financial_snapshot.fundamentals_note.value",
             _g(content, "financial_snapshot", "fundamentals_note"), "value"),
        ):
            if not isinstance(holder, dict):
                continue
            said = holder.get(key)
            if isinstance(said, str) and said.startswith(_FUNDAMENTALS_NOT_SOURCED):
                enforcer.violations.append(Violation(
                    RULE_STATUS_LINE, path, said, "repaired",
                    "the statements view holds issuer statement figures", note))
                holder[key] = note
    channels = _g(content, "evidence_channels", "channels")
    if not isinstance(channels, list):
        return
    tagged = int(_g(enforcer.state, "status", "statement_facts", "sec_tagged_slots") or 0)
    releases = int(_g(enforcer.state, "status", "results_releases_read") or 0)
    for i, channel in enumerate(channels):
        if not isinstance(channel, dict) or channel.get("detail") != "not sourced":
            continue
        detail = None
        if channel.get("channel") == "regulator_structured_facts" and tagged:
            detail = (f"available — {tagged} statement line(s) read from the XBRL tags in "
                      "the issuer's SEC filings (financial statements view)")
        elif channel.get("channel") == "issuer_newsroom" and releases:
            detail = (f"available — {releases} issuer results release(s) read as primary "
                      "documents")
        if detail is None:
            continue
        enforcer.violations.append(Violation(
            RULE_STATUS_LINE, f"content.evidence_channels.channels[{i}].detail",
            "not sourced", "repaired",
            "the V2 channel inventory was assembled before the research ledger ran",
            detail))
        channels[i] = {**channel, "available": True, "detail": detail}


def enforce_content(
    content: Mapping[str, Any] | None, state: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, list[Violation]]:
    """The legacy V2 report content (the fenced JSON in ``content_markdown``): every
    sentence in it is checked as a LEGACY text — inventory claims only. Works on a copy.
    """
    if not isinstance(content, Mapping):
        return None, []
    out = copy.deepcopy(dict(content))
    enforcer = _Enforcer(state)
    _repair_primary_document_group(enforcer, out)
    _repair_statement_status(enforcer, out)

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _CONTENT_SKIP_KEYS:
                    continue
                if isinstance(value, str):
                    node[key] = enforcer.text(value, f"{path}.{key}", SITE_LEGACY)
                else:
                    walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                if isinstance(value, str):
                    node[i] = enforcer.text(value, f"{path}[{i}]", SITE_LEGACY)
                else:
                    walk(value, f"{path}[{i}]")

    walk(out, "content")
    return out, enforcer.violations


def check(
    v3: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    llm_council: Mapping[str, Any] | None = None,
) -> list[Violation]:
    """The violations alone (the payload passed in is never modified)."""
    return enforce(v3, state, llm_council=llm_council).violations


LEGACY_LABEL = (
    "Legacy council (V2). Written from the earlier evidence pack before the research "
    "ledger ran; the research record above is the canonical account of what this run "
    "found. Statements here that contradict it were withheld at report assembly."
)


def legacy_council_block(state: Mapping[str, Any]) -> dict[str, Any]:
    """How the page labels the V2 council beside a V3 run (``v3_research.legacy_council``):
    visibly legacy, with the TRUE council status in place of its own count."""
    return {
        "legacy": True,
        "label": LEGACY_LABEL,
        "status_text": _g(state, "council", "status_text"),
    }


__all__ = [
    "INVARIANTS_VERSION",
    "LEGACY_LABEL",
    "RULES",
    "InvariantResult",
    "Violation",
    "check",
    "check_sentence",
    "enforce",
    "enforce_content",
    "finding_sources",
    "legacy_council_block",
    "primary_document_group",
    "withheld_notice",
]
