"""Optional model expansion of free-form segment terms — V3.21.1 (ADR-107).

A thesis outside the fixed themes names its segment in the user's own words
("cybersecurity"). Recall and the exposure check are better with the words the industry
itself uses ("cyber security", "endpoint security"). One bounded JSON call to the research
model proposes them; nothing it says is trusted:

* only the sanitised segment TERMS are sent, never the thesis text;
* every proposal passes the same sanitiser as the terms (letters, digits, spaces and
  hyphens; 3–40 characters);
* a proposal is kept only when it shares a word with the terms or the thesis text (equal,
  or one containing the other at four letters or more: "cyber security" and
  "cybersecurity"), so the model cannot move the run to another segment;
* at most 8 synonyms and 3 adjacent industry names are kept;
* the call is recorded with its prompt version and model; a failed call means terms only,
  never a failed run.

``V3_SEGMENT_EXPANSION_ENABLED`` (default on) switches it; without a configured research
provider it is simply not run, and the record says so.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from app.services.discovery.intent import DiscoveryIntent
from app.services.market_thesis_parser import sanitise_segment_term

PROMPT_VERSION = "segment_expansion/1"
MAX_SYNONYMS = 8
MAX_ADJACENT = 3
#: Words too common to tie a proposal to the thesis.
_WEAK_TOKENS = frozenset(
    {"and", "the", "for", "of", "in", "or", "with", "to", "a", "an", "companies", "company",
     "industry", "sector", "market", "services", "solutions", "products", "systems"}
)

SEGMENT_EXPANSION_SYSTEM_PROMPT = (
    "You help an evidence-first research platform search for listed companies in a market "
    "segment. You are given segment terms. Propose the words companies and the trade press "
    "use for the SAME segment, so a search finds them.\n"
    'Return JSON: {"synonyms": [str], "adjacent_industries": [str]}.\n'
    "Rules: at most 8 synonyms and 3 adjacent industry names; each 3 to 40 characters, "
    "plain words only (letters, digits, spaces, hyphens); no company names, no tickers, no "
    "opinions, no investment language. An empty list is an acceptable answer."
)


@dataclass
class SegmentExpansion:
    """What the expansion did — stored beside the intent, shown on the run."""

    status: str  # completed | failed | disabled | unavailable | skipped
    prompt_version: str = PROMPT_VERSION
    model: str | None = None
    synonyms: list[str] = field(default_factory=list)
    adjacent_industries: list[str] = field(default_factory=list)
    dropped: int = 0
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def kept(self) -> list[str]:
        return list(dict.fromkeys([*self.synonyms, *self.adjacent_industries]))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.split(r"[^a-z0-9]+", (text or "").lower())
        if len(t) >= 3 and t not in _WEAK_TOKENS
    }


def _shares_a_word(words: set[str], anchor: set[str]) -> bool:
    """A shared word: equal, or one contains the other when the shorter has at least four
    letters — "cyber security" shares "cyber" with "cybersecurity"."""
    for word in words:
        for other in anchor:
            if word == other:
                return True
            short, long_ = sorted((word, other), key=len)
            if len(short) >= 4 and short in long_:
                return True
    return False


def sanitise_proposals(
    raw: Any, *, terms: list[str] | tuple[str, ...], text: str, limit: int
) -> tuple[list[str], int]:
    """Sanitised proposals that share a word with the terms or the text, and the count
    dropped. Untrusted input; never raises."""
    if not isinstance(raw, list):
        return [], 0
    anchor = _tokens(" ".join(terms)) | _tokens(text)
    term_set = set(terms)
    kept: list[str] = []
    dropped = 0
    for item in raw[: limit * 3]:
        clean = sanitise_segment_term(item)
        if not clean or clean in term_set or clean in kept:
            dropped += 1
            continue
        if not _shares_a_word(_tokens(clean), anchor):
            dropped += 1
            continue
        # A fragment of a term is a BROADER word, not a synonym: "security" for
        # "cybersecurity" would pass every security company.
        if any(clean != t and clean in t for t in terms):
            dropped += 1
            continue
        kept.append(clean)
        if len(kept) >= limit:
            break
    return kept, dropped


async def expand_segment_terms(
    terms: list[str] | tuple[str, ...],
    *,
    text: str,
    cfg: Any,
    provider: Any = None,
) -> SegmentExpansion:
    """One bounded JSON call proposing synonyms for ``terms``. Never raises."""
    if not terms:
        return SegmentExpansion(status="skipped")
    if not getattr(cfg, "v3_segment_expansion_enabled", True):
        return SegmentExpansion(status="disabled")
    if provider is None:
        try:
            from app.services.agents.routing import research_provider_for

            provider = research_provider_for(cfg)
        except Exception as exc:  # noqa: BLE001 - an absent provider is a state
            return SegmentExpansion(status="unavailable", error=type(exc).__name__)
    transport = getattr(provider, "transport", None) if provider is not None else None
    if transport is None:
        return SegmentExpansion(status="unavailable",
                                error="no research-model provider is configured and enabled")
    model = getattr(transport, "model", None)
    from app.integrations.deepseek.providers import _json_object

    timeout = int(getattr(cfg, "v3_external_search_timeout_seconds", 30) or 30)
    try:
        response = await transport.complete(
            system=SEGMENT_EXPANSION_SYSTEM_PROMPT + "\nReply in json.",
            user="Segment terms: " + "; ".join(terms),
            max_tokens=400,
            temperature=0.0,
            timeout=min(max(5, timeout), 30),
            json_mode=True,
            thinking=False,
        )
    except Exception as exc:  # noqa: BLE001 - a failed call means terms only
        return SegmentExpansion(status="failed", model=model, error=type(exc).__name__)
    parsed = _json_object(getattr(response, "text", None))
    if parsed is None:
        return SegmentExpansion(
            status="failed", model=getattr(response, "served_model", None) or model,
            error="unparseable_json",
            prompt_tokens=int(getattr(response, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(response, "completion_tokens", 0) or 0),
        )
    synonyms, dropped_s = sanitise_proposals(parsed.get("synonyms"), terms=terms, text=text,
                                             limit=MAX_SYNONYMS)
    adjacent, dropped_a = sanitise_proposals(parsed.get("adjacent_industries"), terms=terms,
                                             text=text, limit=MAX_ADJACENT)
    adjacent = [a for a in adjacent if a not in synonyms]
    return SegmentExpansion(
        status="completed",
        model=getattr(response, "served_model", None) or model,
        synonyms=synonyms,
        adjacent_industries=adjacent,
        dropped=dropped_s + dropped_a,
        prompt_tokens=int(getattr(response, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(response, "completion_tokens", 0) or 0),
    )


async def expand_intent(
    intent: DiscoveryIntent, *, cfg: Any, provider: Any = None
) -> tuple[DiscoveryIntent, SegmentExpansion]:
    """The intent with its ``segment_synonyms`` set from the expansion (terms unchanged)."""
    expansion = await expand_segment_terms(
        intent.segment_terms, text=intent.text, cfg=cfg, provider=provider
    )
    if expansion.kept:
        intent = replace(intent, segment_synonyms=tuple(expansion.kept))
    return intent, expansion


__all__ = [
    "MAX_ADJACENT",
    "MAX_SYNONYMS",
    "PROMPT_VERSION",
    "SEGMENT_EXPANSION_SYSTEM_PROMPT",
    "SegmentExpansion",
    "expand_intent",
    "expand_segment_terms",
    "sanitise_proposals",
]
