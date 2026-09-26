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

_SIZE_RE = re.compile(r"\b(micro|small|mid|large|mega)[\s-]?caps?\b", re.IGNORECASE)
_SIZE_ADJ_RE = re.compile(
    r"\b(?:(small(?:er)?|large(?:r)?|big(?:ger)?|tiny|mega)\s+(?:compan(?:y|ies)|firms?|names"
    r"|issuers?|groups?|houses?|players?))\b",
    re.IGNORECASE,
)
_GROWTH_RE = re.compile(
    r"\b(?:high[\s-]growth|fast[\s-]growing|rapidly[\s-]growing|growing|growth\s+"
    r"(?:compan(?:y|ies)|names|stocks|businesses|stor(?:y|ies)))\b",
    re.IGNORECASE,
)
#: The sentence talks about what was ASKED FOR, not what a company is.
_REQUESTED_MARKERS = re.compile(
    r"\b(?:request\w*|ask\w*|sought|seek\w*|want\w*|thesis|query|brief|user|intent"
    r"|criteri\w+|constraint\w*|filter\w*|target\w*)\b",
    re.IGNORECASE,
)
#: The sentence QUALIFIES the attribute rather than asserting it.
#: Only NEGATIVE qualifiers: "verified" or "established" never exempt a sentence — "C1 is
#: a verified small-cap" is exactly the assertion that must be checked.
_QUALIFIERS = re.compile(
    r"\b(?:not|no|unverified|unknown|unestablished|uncertain|unclear|lack\w*|missing"
    r"|cannot|can't|could\s+not|whether|mismatch\w*|outside|exceed\w*|fail\w*"
    r"|only\s+\d+\s+of)\b",
    re.IGNORECASE,
)
_COHORT = re.compile(
    r"\b(?:all|these|those|every|each|the\s+(?:candidates|cohort|set|group|companies|names"
    r"|universe|shortlist)|cohort|candidate\s+set|most)\b",
    re.IGNORECASE,
)
_CREF = re.compile(r"\bC(\d{1,3})\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+")

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


class CandidateAttributes:
    """What may be said about each candidate, keyed by every handle the council uses."""

    def __init__(self, entries: Iterable[Mapping[str, Any]]):
        self.by_key: dict[str, dict[str, Any]] = {}
        self.all: list[dict[str, Any]] = []
        for index, entry in enumerate(entries, start=1):
            attrs = dict(entry.get("verified_attributes") or {})
            record = {"ref": f"C{index}", "attrs": attrs,
                      "ticker": str(entry.get("ticker") or "")}
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


def _subjects(
    sentence: str, attrs: CandidateAttributes, own: dict[str, Any] | None
) -> list[dict[str, Any]]:
    named = [attrs.by_key[f"C{m.group(1)}"] for m in _CREF.finditer(sentence)
             if f"C{m.group(1)}" in attrs.by_key]
    for record in attrs.all:
        ticker = record["ticker"]
        if ticker and len(ticker) >= 2 and re.search(rf"\b{re.escape(ticker)}\b", sentence):
            if record not in named:
                named.append(record)
    if named:
        return named
    if _COHORT.search(sentence) or own is None:
        return list(attrs.all)
    return [own]


def check_sentence(
    sentence: str, attrs: CandidateAttributes, own: dict[str, Any] | None
) -> str | None:
    """None when the sentence may stand; otherwise the reason it may not."""
    size_bands: set[str] = set()
    for match in _SIZE_RE.finditer(sentence):
        size_bands.add(_BUCKET_ALIASES[match.group(1).lower()])
    for match in _SIZE_ADJ_RE.finditer(sentence):
        size_bands.add(_BUCKET_ALIASES[match.group(1).lower()])
    growth = _GROWTH_RE.search(sentence) is not None
    if not size_bands and not growth:
        return None
    if _QUALIFIERS.search(sentence):
        return None
    # A sentence about what was ASKED FOR may stand — unless it also names a candidate
    # or the cohort: "all candidates meet the thesis's small-cap filter" is an assertion.
    named = bool(_CREF.search(sentence)) or bool(_COHORT.search(sentence)) or any(
        r["ticker"] and len(r["ticker"]) >= 2
        and re.search(rf"\b{re.escape(r['ticker'])}\b", sentence)
        for r in attrs.all
    )
    if _REQUESTED_MARKERS.search(sentence) and not named:
        return None
    subjects = _subjects(sentence, attrs, own)
    if not subjects:
        return None
    for band in size_bands:
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
    kept: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        reason = check_sentence(sentence, attrs, own)
        if reason is None:
            kept.append(sentence)
        else:
            removed.append({"where": where, "sentence": sentence[:400], "reason": reason})
    return " ".join(kept).strip()


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
