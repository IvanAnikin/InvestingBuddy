"""The requested-vs-verified guard on discovery-council prose — V3.19.5.

THE DEFECT
==========
Asked for *"small cap growing european luxury companies"*, the discovery council wrote
that the candidates were *"small-cap European luxury companies"* — LVMH, Hermès and
Kering among them — while another agent in the same review called them *"large well-known
luxury firms"*. The user's words had reached the council as a description of the cohort,
and the council repeated them as fact.

THE RULE
========
A sentence may attribute a SIZE band or a GROWTH label to candidates only when every
candidate it applies to has that attribute VERIFIED (``thesis_match_json.v319
.verified_attributes``). A sentence that talks about the attribute as what was REQUESTED
("the user asked for small caps", "the thesis requested growth") is kept. A sentence that
qualifies it ("growth is not established", "size is unverified") is kept.

Everything else is removed — never rewritten — and recorded in
``review["attribute_guard"]["removed"]`` with the reason, so a reader can see what the
council said and why it is not shown. Deterministic; no model is involved.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_SIZE_RE = re.compile(
    r"\b(micro|small|mid|large|mega)[\s-]?cap(?:s|itali[sz]ation)?\b", re.IGNORECASE
)
_SIZE_ADJ_RE = re.compile(
    r"\b(?:(small(?:er)?|large(?:r)?|big(?:ger)?|tiny|mega)\s+(?:luxury\s+)?"
    r"(?:compan(?:y|ies)|firms?|names|issuers?|groups?|houses?|players?|brands?"
    r"|businesses|producers?|miners?))\b",
    re.IGNORECASE,
)
_GROWTH_RE = re.compile(
    r"\b(?:high[\s-]growth|fast[\s-]growing|rapidly[\s-]growing|growth[\s-]oriented|growing"
    r"|growth\s+(?:compan(?:y|ies)|names|stocks|businesses|stor(?:y|ies)))\b",
    re.IGNORECASE,
)
#: "a growing market", "growing demand": the growth of something that is not a company.
_NOT_COMPANY_GROWTH = re.compile(
    r"^\s*(?:\w+\s+)?(?:market|markets|demand|sector|industry|segment|category|middle"
    r"|class|population|interest|appetite|adoption|spending|consumption|region)\b",
    re.IGNORECASE,
)
#: "mid-cap peers", "larger players like …": a size phrase about OTHER companies.
_PEER_AFTER = re.compile(
    r"^\s*(?:peers?|players?|rivals?|competitors?|counterparts|incumbents)\b", re.IGNORECASE
)
#: The sentence talks about what was ASKED FOR, not what a company is.
_REQUESTED_MARKERS = re.compile(
    r"\b(?:request\w*|ask\w*|sought|seek\w*|want\w*|thesis|query|brief|user|intent"
    r"|criteri\w+|constraint\w*|filter\w*|target\w*)\b",
    re.IGNORECASE,
)
#: A NEGATIVE qualifier of the attribute itself, immediately around it: "not a small cap",
#: "no verified growth", "small-cap status is unverified", "growth is not established".
_QUALIFIER_BEFORE = re.compile(
    r"\b(?:not|no|non|never|nor|without|lacks?|lacking|unverified|unknown|unclear|uncertain"
    r"|isn't|aren't|whether|if|outside|beyond|above|below|exceeds?)\s+(?:\w+\s+){0,2}$",
    re.IGNORECASE,
)
_QUALIFIER_AFTER = re.compile(
    r"^\W{0,3}(?:\w+\s+){0,2}(?:(?:is|are|was|remains?|being)\s+)?(?:not|un)"
    r"(?:\s+(?:yet\s+)?)?(?:verified|established|known|confirmed|clear|shown|evidenced)\b"
    r"|^\W{0,3}(?:status\s+)?(?:cannot|could\s+not)\s+be\s+(?:verified|confirmed)",
    re.IGNORECASE,
)
_COUNTING = re.compile(r"\bonly\s+\d+\s+of\b|\b\d+\s+of\s+(?:the\s+)?\d+\b", re.IGNORECASE)
_COHORT = re.compile(
    r"\b(?:all|these|those|every|each|the\s+(?:candidates|cohort|set|group|companies|names"
    r"|universe|shortlist)|cohort|candidate\s+set)\b",
    re.IGNORECASE,
)
_CREF = re.compile(r"\bC(\d{1,3})\b")
#: Sentence boundaries, KEPT (captured) so the original separators — newlines, bullets —
#: survive: the chair synthesis is rendered whitespace-preserving.
_SENTENCE_SPLIT = re.compile(r"((?<=[.!?;])\s+|\n+)")
_HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
                          "\u2014": "-", "\u2212": "-", "\u00a0": " "})

_BUCKET_ALIASES = {
    "micro": "micro_cap", "tiny": "micro_cap", "small": "small_cap", "smaller": "small_cap",
    "mid": "mid_cap", "large": "large_cap", "larger": "large_cap", "big": "large_cap",
    "bigger": "large_cap", "mega": "mega_cap",
}
#: A candidate verified at ``key`` also satisfies a sentence naming these bands.
_SATISFIES = {
    "micro_cap": {"micro_cap", "small_cap"},  # "small" loosely covers micro
    "small_cap": {"small_cap"},
    "mid_cap": {"mid_cap"},
    "large_cap": {"large_cap"},
    "mega_cap": {"mega_cap", "large_cap"},
}


def _qualified(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 40) : start]
    after = text[end : end + 48]
    return bool(_QUALIFIER_BEFORE.search(before) or _QUALIFIER_AFTER.search(after))


class CandidateAttributes:
    """What may be said about each candidate, keyed by every handle the council uses."""

    def __init__(self, entries: Iterable[Mapping[str, Any]]):
        self.by_key: dict[str, dict[str, Any]] = {}
        self.all: list[dict[str, Any]] = []
        for index, entry in enumerate(entries, start=1):
            attrs = dict(entry.get("verified_attributes") or {})
            record = {"ref": f"C{index}", "attrs": attrs,
                      "ticker": str(entry.get("ticker") or ""),
                      "eligibility": entry.get("eligibility"),
                      "unknown_constraints": list(entry.get("unknown_constraints") or [])}
            self.all.append(record)
            for key in (f"C{index}", str(entry.get("candidate_id") or ""),
                        str(entry.get("ticker") or "").upper()):
                if key:
                    self.by_key[key] = record

    def lookup(self, handle: Any) -> dict[str, Any] | None:
        if handle is None:
            return None
        text = str(handle)
        return self.by_key.get(text) or self.by_key.get(text.upper())


def _named(sentence: str, attrs: CandidateAttributes) -> list[dict[str, Any]]:
    named = [attrs.by_key[f"C{m.group(1)}"] for m in _CREF.finditer(sentence)
             if f"C{m.group(1)}" in attrs.by_key]
    for record in attrs.all:
        ticker = record["ticker"]
        if ticker and len(ticker) >= 2 and re.search(
            rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])", sentence, re.IGNORECASE
        ):
            if record not in named:
                named.append(record)
    return named


def _subjects(
    sentence: str, attrs: CandidateAttributes, own: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Who the sentence is ABOUT: the candidates it names; else the cohort when it says so;
    else the note's own candidate. A sentence about nobody in particular is generic."""
    named = _named(sentence, attrs)
    if named:
        return named
    if _COHORT.search(sentence):
        return list(attrs.all)
    return [own] if own is not None else []


def _size_mentions(text: str) -> list[tuple[str, int, int]]:
    out = []
    for regex in (_SIZE_RE, _SIZE_ADJ_RE):
        for m in regex.finditer(text):
            if _PEER_AFTER.search(text[m.end() : m.end() + 24]):
                continue
            out.append((_BUCKET_ALIASES[m.group(1).lower()], m.start(), m.end()))
    return out


def _growth_mentions(text: str) -> list[tuple[int, int]]:
    return [
        (m.start(), m.end()) for m in _GROWTH_RE.finditer(text)
        if not _NOT_COMPANY_GROWTH.search(text[m.end() : m.end() + 32])
    ]


def check_sentence(
    sentence: str, attrs: CandidateAttributes, own: dict[str, Any] | None
) -> str | None:
    """None when the sentence may stand; otherwise the reason it may not."""
    text = sentence.translate(_HYPHENS)
    sizes = [(b, a, z) for b, a, z in _size_mentions(text) if not _qualified(text, a, z)]
    growth = [(a, z) for a, z in _growth_mentions(text) if not _qualified(text, a, z)]
    if not sizes and not growth:
        return None
    if _COUNTING.search(text):
        return None
    # A sentence about what was ASKED FOR may stand — unless it also names a candidate
    # or the cohort: "all candidates meet the thesis's small-cap filter" is an assertion.
    if _REQUESTED_MARKERS.search(text) and not (_named(text, attrs) or _COHORT.search(text)):
        return None
    subjects = _subjects(text, attrs, own)
    if not subjects:
        return None
    for band in {b for b, _a, _z in sizes}:
        unverified = [
            s["ref"] for s in subjects
            if band not in _SATISFIES.get(str(s["attrs"].get("size_bucket")), set())
        ]
        if unverified:
            return (
                f"attributes {band.replace('_', '-')} to {', '.join(unverified[:6])} "
                "without a verified market cap in that band"
            )
    if growth:
        unverified = [
            s["ref"] for s in subjects if s["attrs"].get("growth_status") != "established"
        ]
        if unverified:
            return (
                f"describes {', '.join(unverified[:6])} as growing without verified "
                "business growth"
            )
    return None


def _guard_text(
    text: str, attrs: CandidateAttributes, own: dict[str, Any] | None, where: str,
    removed: list[dict[str, Any]],
) -> str:
    """The text minus unsupported sentences, separators preserved; unchanged if none."""
    parts = _SENTENCE_SPLIT.split(text)
    kept: list[str] = []
    changed = False
    for index in range(0, len(parts), 2):
        sentence = parts[index]
        separator = parts[index + 1] if index + 1 < len(parts) else ""
        reason = check_sentence(sentence, attrs, own) if sentence.strip() else None
        if reason is None:
            kept.append(sentence + separator)
        else:
            changed = True
            removed.append({"where": where, "sentence": sentence[:400], "reason": reason})
    return "".join(kept).strip() if changed else text


def _own(node: Mapping[str, Any], attrs: CandidateAttributes) -> dict[str, Any] | None:
    for key in ("candidate_ref", "candidate_id", "ticker"):
        found = attrs.lookup(node.get(key))
        if found is not None:
            return found
    return None


def _walk(
    node: Any, attrs: CandidateAttributes, own: dict[str, Any] | None, where: str,
    removed: list[dict[str, Any]],
) -> Any:
    if isinstance(node, dict):
        mine = _own(node, attrs) or own
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in ("candidate_ref", "candidate_id", "ticker", "exchange", "agent_name",
                       "status", "internal_action", "confidence", "citation_ids",
                       "strongest_dimension", "attribute_guard"):
                out[key] = value
                continue
            out[key] = _walk(value, attrs, mine, f"{where}.{key}", removed)
        return out
    if isinstance(node, list):
        result = []
        for index, item in enumerate(node):
            guarded = _walk(item, attrs, own, f"{where}[{index}]", removed)
            if isinstance(item, str) and not guarded:
                continue  # a list item that was only the removed sentence is dropped
            result.append(guarded)
        return result
    if isinstance(node, str):
        return _guard_text(node, attrs, own, where, removed)
    return node


#: Top-level review keys whose strings are prose the council wrote.
_PROSE_KEYS = (
    "candidates_to_research_next", "candidates_to_monitor", "candidates_to_reject",
    "candidates_insufficient_data", "evidence_gaps", "next_source_tasks", "agent_outputs",
    "deterministic_discovery_chair",
)


def guard_review(
    review: dict[str, Any], candidates: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """Return the review with unsupported attribute sentences removed, and the record."""
    attrs = CandidateAttributes(candidates)
    removed: list[dict[str, Any]] = []
    out = dict(review)
    for key in _PROSE_KEYS:
        if key in out:
            out[key] = _walk(out[key], attrs, None, key, removed)
    # A council may prioritise an eligible_unverified candidate; the reader is told what
    # is still unverified about it, beside the council's own placement.
    for bucket in ("candidates_to_research_next", "candidates_to_monitor"):
        entries = out.get(bucket)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            record = _own(entry, attrs)
            if record and record.get("eligibility") == "eligible_unverified":
                entry["unverified_constraints"] = record.get("unknown_constraints") or []
    out["attribute_guard"] = {
        "version": 1,
        "rule": (
            "a size band or growth label may be attributed to a candidate only when that "
            "attribute is verified; sentences about what was REQUESTED are kept"
        ),
        "removed_count": len(removed),
        "removed": removed[:60],
    }
    return out


__all__ = ["CandidateAttributes", "check_sentence", "guard_review"]
