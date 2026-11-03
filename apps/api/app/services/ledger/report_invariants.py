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
  narrating a disagreement when none is recorded (withheld);
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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.services.ledger.canonical_state import (
    FALLBACK_CHAIR_FAILED,
    FALLBACK_NO_MODEL,
    FALLBACK_NOT_CONVENED,
    REFUSAL_WORDS,
    as_date,
    period_bounds,
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
)

#: Where a text sits. The chair is checked for everything; other texts are checked for
#: inventory claims only (they do not state counts or narrate the council).
SITE_CHAIR = "chair"
SITE_RECORD = "record"
SITE_LEGACY = "legacy"

#: Days by which a held document must be newer than the one named as newest, and a
#: held period later than the one named, before a sentence is called stale. Margins in
#: the direction of keeping a sentence: same-week documents and fiscal-vs-calendar
#: labelling are not contradictions.
DOCUMENT_MARGIN_DAYS = 3
PERIOD_MARGIN_DAYS = 90

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")


# ---------------------------------------------------------------------------
# The reviewed phrase table
# ---------------------------------------------------------------------------

_ISSUER_ADJ = (
    r"(?:issuer(?:'s)?(?:[- ]own)?|issuer's\s+own|company(?:[- ]owned)?|company's\s+own"
    r"|primary(?:\s+regulatory)?|official|regulatory)"
)
_DOC = r"(?:press\s+releases?|releases?|filings?|disclosures?|documents?|announcements?)"
_LIST_ITEM = rf"(?:{_ISSUER_ADJ}\s+)?(?:financial\s+statements|(?:\w+\s+)?{_DOC})"
_INVENTORY_PRED = (
    r"(?:(?:present|included|available|found|held|acquired)"
    r"(?:\s+in\s+(?:the\s+)?(?:evidence(?:\s+pack)?|pack))?"
    r"|in\s+(?:the\s+)?evidence(?:\s+pack)?|in\s+the\s+pack)"
)
#: "No issuer filing [, X, or Y] is in evidence" — the claim must END at the
#: predicate (or continue with a connective): "no issuer filing in evidence breaks out
#: revenue by segment" is a claim about segments, not about the inventory.
_NO_ISSUER_DOC_RE = re.compile(
    rf"\bno\s+{_ISSUER_ADJ}\s+(?:\w+\s+)?{_DOC}"
    rf"(?:\s*,?\s*(?:(?:or|and|nor)\s+)?{_LIST_ITEM})*"
    rf"\s*,?\s*(?:(?:is|are|was|were|has\s+been|have\s+been)\s+)?{_INVENTORY_PRED}"
    r"(?=\s*(?:[.,;:)—–-]|$)|\s+(?:so|to|and|which|limiting|leaving)\b)",
    re.I,
)
_EVIDENCE_HAS_NO_RE = re.compile(
    rf"\b(?:evidence(?:\s+pack)?|record)\s+(?:includes|contains|has|shows)\s+no\s+"
    rf"{_ISSUER_ADJ}\s+(?:\w+\s+)?{_DOC}(?:\s*,?\s*(?:or|and)\s+(?:\w+\s+)?{_DOC})*"
    r"(?!\s+(?:for|on|about|covering|of|from|dated|in|after|before|since)\b)",
    re.I,
)
#: Only descriptive words may sit between "absence of" and the documents: "lack of any
#: FORTHCOMING company disclosures" is about the future, not the inventory.
_ABSENCE_OF_FILINGS_RE = re.compile(
    r"\b(?:absence|lack)\s+of\s+(?:(?:any|fundamental|financial|primary|operational"
    r"|operating|audited|verified|data|information|statements?|and|or),?\s+){0,6}"
    r"(?:primary\s+(?:regulatory\s+)?filings|company\s+disclosures"
    r"|issuer\s+(?:filings|disclosures|documents))\b",
    re.I,
)

_LATEST_DOC_RE = re.compile(
    r"(?<![\w-])(?:newest|latest|most\s+recent)\s+(?:dated\s+)?"
    r"(?:(?:issuer|issuer's|primary|results|official|company|company's|regulatory|own)\s+)"
    r"{0,2}(?:press\s+release|disclosure|filing|release|document|evidence|announcement)s?\b"
    # A QUALIFIED claim ("latest filing WITH period figures", "newest release FOR Q2")
    # is about a subset, and a newer document outside it does not contradict it.
    r"(?!\s+(?:with|for|of|on|about|covering|containing|that|which|reporting|from)\b)",
    re.I,
)
#: A date that is a period END ("quarter ended 30 Sep 2026"), not a publication date.
_PERIOD_END_BEFORE_RE = re.compile(
    r"(?:ended|ending|as\s+(?:at|of)|at|through|to)\s*$", re.I
)
_ORDINAL_BEFORE_RE = re.compile(r"(?:second|next|third|previous|prior|earlier)[\s-]*$", re.I)

_LATEST_UNAVAILABLE_RES = (
    re.compile(
        r"\b(?:latest|current|most\s+recent|newest)\s+"
        r"(?:quarter(?:ly)?|interim|half[- ]year(?:ly)?|reporting\s+period|period)(?:'s)?"
        r"(?:\s+(?P<noun>report|results|figures|filing|statements?|accounts))?"
        r"\s*(?::\s*|\s(?:is|are|was|were|has\s+been|have\s+been)\s+)"
        r"(?:not\s+(?:in\s+this\s+report|available|acquired|yet\s+acquired"
        r"|in\s+(?:the\s+)?evidence)|unavailable|missing|absent)\b",
        re.I,
    ),
    re.compile(
        r"\bno\s+(?:current[- ]period|interim|quarterly|half[- ]year(?:ly)?)\s+"
        r"(?P<noun>report|filing|results|statements?|figures|accounts)\s+"
        r"(?:is|are|was|were|has\s+been|have\s+been)\s+"
        r"(?:acquired|available|present|found|held|in\s+(?:the\s+)?evidence)\b",
        re.I,
    ),
)
_FIGURE_NOUNS = frozenset({"results", "figures", "statement", "statements", "accounts"})

_NO_RECENT_RE = re.compile(
    r"\bno\s+(?:recent|current|new)\s+(?:developments|news|disclosures|announcements"
    r"|events)(?:\s+(?:were|was|have\s+been|has\s+been|are|is)\s+(?:found|identified"
    r"|reported|disclosed|sourced|recorded|available))?(?=\s*(?:[.;,]|$))",
    re.I,
)

_NO_CASH_FLOW_RES = (
    re.compile(
        r"\bno\s+(?:operating\s+|free\s+)?cash[- ]flow(?:\s+(?:data|figures?|statements?"
        r"|information|disclosure))?\s+(?:(?:is|are|was|were)\s+)?(?:available|disclosed"
        r"|present|reported|provided|in\s+(?:the\s+)?evidence)"
        r"(?=\s*(?:[.;,]|$)|\s+(?:so|and|to)\b)",
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
    r"(?=\s*(?:[.;]|$))",
    re.I,
)

_DISAGREEMENT_RE = re.compile(
    r"\b(?:disagreements?|disagree(?:s|d)?|contradict(?:s|ion|ions|ory)?"
    r"|conflicting\s+(?:evidence|findings|figures|data|sources|reports))\b",
    re.I,
)
_NEGATED_DISAGREEMENT_RE = re.compile(
    r"\b(?:no|without|nor|0)\s+(?:\w+\s+){0,2}(?:disagreements?|contradictions?"
    r"|conflict(?:s|ing)?)\b",
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
_PERIOD_TEXT_RES: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], str]], ...] = (
    (re.compile(r"\bFY\s?(\d{4})\b", re.I), lambda m: m.group(1)),
    (re.compile(r"\bfull[- ]year\s+(\d{4})\b", re.I), lambda m: m.group(1)),
    (re.compile(r"\b(\d{4})\s+annual\s+report\b", re.I), lambda m: m.group(1)),
    (re.compile(r"\bannual\s+report\s+(\d{4})\b", re.I), lambda m: m.group(1)),
    (re.compile(r"\b(\d{4})-([QH][1-4])\b", re.I), lambda m: f"{m.group(1)}-{m.group(2)}"),
    (re.compile(r"\b([QH][1-4])\s+(\d{4})\b", re.I), lambda m: f"{m.group(2)}-{m.group(1)}"),
    (re.compile(r"\b(first|second)\s+half\s+(?:of\s+)?(\d{4})\b", re.I),
     lambda m: f"{m.group(2)}-H{1 if m.group(1).lower() == 'first' else 2}"),
)


def _dates_in(text: str) -> tuple[list[date], list[date]]:
    """``(publication-like dates, period-end dates)`` named in a text. A date preceded
    by "ended", "as at" … is a period end and is compared with periods, not with the
    newest document's publication date."""
    published: list[date] = []
    ends: list[date] = []

    def put(m: re.Match[str], value: date) -> None:
        before = text[max(0, m.start() - 24): m.start()]
        (ends if _PERIOD_END_BEFORE_RE.search(before) else published).append(value)

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
    return published, ends


def _period_ends_in(text: str) -> list[date]:
    out: list[date] = []
    for pattern, key in _PERIOD_TEXT_RES:
        for m in pattern.finditer(text):
            bounds = period_bounds(key(m).upper())
            if bounds:
                out.append(bounds[1])
    return out


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
            "text": self.text[:400],
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
    if not (held and cited):
        return None
    if not (
        _NO_ISSUER_DOC_RE.search(sentence)
        or _EVIDENCE_HAS_NO_RE.search(sentence)
        or _ABSENCE_OF_FILINGS_RE.search(sentence)
    ):
        return None
    _, newest = _newest(state)
    return _Hit(
        RULE_NO_ISSUER_DOC,
        f"it says no issuer filing or release is in evidence, while {held} issuer "
        f"document(s) are recorded (newest: {newest or 'undated'}) and {cited} finding(s) "
        "cite the issuer's own documents",
    )


def _rule_stale_latest(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    match = None
    for candidate in _LATEST_DOC_RE.finditer(sentence):
        if not _ORDINAL_BEFORE_RE.search(sentence[: candidate.start()]):
            match = candidate
            break
    if match is None:
        return None
    newest_date, newest_text = _newest(state)
    margin = timedelta(days=DOCUMENT_MARGIN_DAYS)
    published, period_end_dates = _dates_in(sentence)
    if published:
        # The newest date the sentence names decides: "newest is X (2026-10-27) about
        # the quarter ended 30 Sep" is not stale because a period end is older.
        if newest_date and max(published) < newest_date - margin:
            return _Hit(
                RULE_STALE_LATEST,
                f"it names a document dated {max(published).isoformat()} as the newest, "
                f"while the issuer's {newest_text} is recorded",
            )
        return None
    inventory = _g(state, "documents", "inventory") or []
    named_dates = [
        as_date(d.get("date"))
        for accession in _ACCESSION_RE.findall(sentence)
        for d in inventory
        if d.get("ref") == accession
    ]
    named_dates_known = [d for d in named_dates if d]
    if named_dates_known:
        if newest_date and max(named_dates_known) < newest_date - margin:
            return _Hit(
                RULE_STALE_LATEST,
                f"it names a document dated {max(named_dates_known).isoformat()} as the "
                f"newest, while the issuer's {newest_text} is recorded",
            )
        return None
    ends = _period_ends_in(sentence) + period_end_dates
    latest_end = as_date(_g(state, "documents", "latest_reporting_period_end"))
    if ends and latest_end and latest_end - max(ends) >= timedelta(days=PERIOD_MARGIN_DAYS):
        return _Hit(
            RULE_STALE_LATEST,
            "it names an older period as the most recent disclosure, while findings are "
            f"about {_g(state, 'documents', 'latest_reporting_period')}",
        )
    if ends:
        return None
    forms = {f.upper() for f in _FORM_RE.findall(sentence)}
    if len(forms) == 1 and newest_date and not _ACCESSION_RE.search(sentence):
        form = next(iter(forms))
        same = [as_date(d.get("date")) for d in inventory
                if str(d.get("form") or "").upper() == form]
        same_dates = [d for d in same if d]
        if same_dates and max(same_dates) < newest_date - margin:
            return _Hit(
                RULE_STALE_LATEST,
                f"it names the {form} (dated {max(same_dates).isoformat()}) as the newest, "
                f"while the issuer's {newest_text} is recorded",
            )
    return None


def _rule_latest_period(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    status = _g(state, "current_period", "status")
    if status not in ("facts_validated", "document_held_not_extracted"):
        return None
    for pattern in _LATEST_UNAVAILABLE_RES:
        m = pattern.search(sentence)
        if not m:
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
    if not _NO_RECENT_RE.search(sentence):
        return None
    _, newest = _newest(state)
    return _Hit(
        RULE_NO_RECENT,
        f"it says there are no recent developments, while a current issuer disclosure "
        f"({newest or 'recent'}) is cited",
    )


def _rule_no_cash_flow(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    match = next((m for p in _NO_CASH_FLOW_RES if (m := p.search(sentence))), None)
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


def _rule_unbacked_disagreement(sentence: str, state: Mapping[str, Any]) -> _Hit | None:
    if sentence.startswith("Deterministic synthesis:"):
        return None
    unresolved = _g(state, "disagreements", "unresolved")
    total = _g(state, "disagreements", "total")
    if unresolved != 0 or (total not in (0, None)):
        return None
    if not _DISAGREEMENT_RE.search(sentence) or _NEGATED_DISAGREEMENT_RE.search(sentence):
        return None
    return _Hit(RULE_UNBACKED_DISAGREEMENT,
                "it narrates a disagreement, while the ledger records none")


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


_RECORD_RULES = (
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
    _rule_unbacked_disagreement,
)


def check_sentence(
    sentence: str, state: Mapping[str, Any], *, site: str = SITE_RECORD
) -> _Hit | None:
    """The first invariant one sentence breaks, or ``None``. Pure."""
    for rule in _CHAIR_RULES if site == SITE_CHAIR else _RECORD_RULES:
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
    def __init__(self, state: Mapping[str, Any]) -> None:
        self.state = state
        self.violations: list[Violation] = []

    def text(self, value: Any, path: str, site: str) -> Any:
        """Check one text; return it repaired, with offending sentences withheld."""
        if not isinstance(value, str) or not value.strip():
            return value
        sentences = [s for s in _SENTENCE_RE.split(value) if s.strip()]
        kept: list[str] = []
        reasons: list[str] = []
        changed = False
        for sentence in sentences:
            hit = check_sentence(sentence, self.state, site=site)
            if hit is None:
                kept.append(sentence)
                continue
            changed = True
            if hit.replacement is not None:
                kept.append(hit.replacement)
                self.violations.append(Violation(hit.rule, path, sentence, "repaired",
                                                 hit.reason, hit.replacement))
            else:
                reasons.append(hit.reason)
                self.violations.append(Violation(hit.rule, path, sentence, "withheld",
                                                 hit.reason))
        if not changed:
            return value
        if not kept:
            return withheld_notice(reasons[0] if reasons else "it contradicted the record")
        return " ".join(kept)


def _walk_report(enforcer: _Enforcer, report: Any) -> None:
    if not isinstance(report, dict):
        return
    for i, section in enumerate(report.get("sections") or ()):
        if not isinstance(section, dict):
            continue
        base = f"professional_research.sections[{i}]"
        lead = section.get("lead")
        if isinstance(lead, dict):
            lead["text"] = enforcer.text(lead.get("text"), f"{base}.lead", SITE_RECORD)
        for j, sentence in enumerate(section.get("sentences") or ()):
            if isinstance(sentence, dict):
                sentence["text"] = enforcer.text(
                    sentence.get("text"), f"{base}.sentences[{j}]", SITE_RECORD)
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
    enforcer = _Enforcer(state)

    _structured(enforcer, out, legacy)

    chair = out.get("chair")
    if isinstance(chair, dict):
        chair["synthesis"] = enforcer.text(chair.get("synthesis"), "chair.synthesis",
                                           SITE_CHAIR)
        for i, point in enumerate(chair.get("key_points") or ()):
            if isinstance(point, dict):
                point["text"] = enforcer.text(point.get("text"), f"chair.key_points[{i}]",
                                              SITE_CHAIR)
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


#: Keys of the V2 report content whose values are identifiers or vocabulary, never
#: sentences about the research.
_CONTENT_SKIP_KEYS: frozenset[str] = frozenset({
    "type", "provenance", "classification", "url", "source_url", "id", "source_id",
    "citation_ids", "evidence_ids", "ticker", "exchange", "isin", "lei", "slug",
})


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
    "legacy_council_block",
    "withheld_notice",
]
