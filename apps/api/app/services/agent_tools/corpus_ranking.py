"""Candidate shaping for a company-scoped corpus search — pure functions, no I/O.

WHY THIS EXISTS (found on the first live Rainbow Rare Earths run, 2026-10-06)
==============================================================================
The issuer's own presentation was fetched, chunked and indexed, and stated the facts the
report then listed as open gaps — an 85% project interest, ~1,850 t/yr of planned output,
a US$295.5m capex. The investigator never saw them: for every one of its questions the
chunks that held those figures ranked 12th–24th, and it reads one search of 8 hits per
question. Two generic causes, both in how a company-scoped query is ranked:

1. **The subject's own name is the loudest term.** A search already filtered to one
   company by ``company_ids`` still carried "Rainbow Rare Earths" in its query, and a
   lexical score rewards a term for appearing often — so every announcement that repeats
   the issuer's name outranked the slide that answers "planned annual output". The name
   adds nothing the ``company_ids`` filter has not already said. Removing it moved the
   key chunks from ranks 18/22/12 to 7/5/4 on the live corpus.
2. **One document can fill the whole page.** Five of the first eight hits were chunks of
   a single regulatory filing. Each document's best two chunks now come first and the rest
   of that document is demoted — never dropped, so a one-filing company still gets a full
   page. A page of 12 then holds every key chunk the replay found.

Neither change widens what may be returned: the same ``company_ids`` filter, source
filters and access rules apply, and every hit is still a corpus chunk with its evidence
id. This module only decides which of the candidates the backend already returned reach
the caller, and in what order — it never invents, merges or rewrites a hit.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any, TypeVar

T = TypeVar("T")

#: Chunks of one document a single page may carry. Two keeps a document's best passage
#: and its runner-up (a figure and the sentence that qualifies it) without letting one
#: filing fill the page.
PER_DOCUMENT_CAP = 2

#: How many candidates to ask the backend for, per hit the caller will receive, before the
#: per-document cap thins them.
POOL_FACTOR = 3
POOL_CEILING = 40

#: A name stripped from a query must leave at least this many words, or the query is kept
#: as written: "Rainbow Rare Earths" alone is a request for the company, not noise.
MIN_WORDS_AFTER_STRIP = 2

#: Words that end a company name without being part of what it is called.
_LEGAL_FORMS = frozenset(
    {
        "ltd", "limited", "plc", "inc", "incorporated", "corp", "corporation", "co",
        "company", "llc", "lp", "llp", "sa", "ag", "nv", "bv", "se", "ab", "asa", "oyj",
        "spa", "srl", "gmbh", "pty", "pte", "kk", "nl",
    }
)

#: A word: Unicode letters/digits, with "&" allowed INSIDE it ("AT&T"). Underscore is not
#: a word character here. Unicode-aware on purpose: an ASCII-only class turns "Nestlé" into
#: "Nestl" and a stripped query into one that begins with a stray "é".
_WORD = re.compile(r"[^\W_]+(?:&[^\W_]+)*")

#: Between two words of a name, any run of punctuation or space: "Cleveland-Cliffs",
#: "Pandora Group A/S" and "Pandora Group A S" are one phrase.
_SEPARATOR = r"[\W_]+"

#: A legal form written with dots or a slash — "A/S", "S.A.", "N.V.", "S.p.A." — which
#: tokenises into single letters no word list would recognise.
_DOTTED_LEGAL_TAIL = re.compile(r"[\s,]+(?:[^\W\d_]{1,2}[./]){1,3}[^\W\d_]{0,2}\.?\s*$")

#: Words a keyword reducer drops from the middle of a name ("Bank of America" reaches the
#: index as "Bank America"), so the name must also be recognised without them.
_FILLER_WORDS = frozenset(
    {"of", "the", "and", "for", "de", "du", "la", "le", "los", "del", "van", "von"}
)

def _words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def name_variants(*names: str | None) -> tuple[str, ...]:
    """The phrases that name a company: each name as given, and without trailing legal
    forms ("RAINBOW RARE EARTHS LIMITED" → also "RAINBOW RARE EARTHS"). Longest first,
    so a longer variant is removed before the shorter one it contains."""
    variants: set[str] = set()
    for name in names:
        words = _words(name or "")
        if not words:
            continue
        variants.add(" ".join(words))
        untailed = _words(_DOTTED_LEGAL_TAIL.sub("", name or ""))
        if untailed:
            variants.add(" ".join(untailed))
        trimmed = list(untailed or words)
        while trimmed and trimmed[-1].lower() in _LEGAL_FORMS:
            trimmed.pop()
        if trimmed:
            variants.add(" ".join(trimmed))
            reduced = [w for w in trimmed if w.lower() not in _FILLER_WORDS]
            if reduced:
                variants.add(" ".join(reduced))
    # A one-word name ("Pandora") is kept: it is still the subject. The MIN_WORDS rule
    # protects a query that is only the name.
    return tuple(sorted(variants, key=lambda v: (-len(v), v)))


def strip_subject_name(query: str, variants: Iterable[str]) -> tuple[str, bool]:
    """``(query, stripped)``: ``query`` without the subject's own name phrase.

    Whole-phrase, case-insensitive, word-bounded. If removing the name would leave fewer
    than ``MIN_WORDS_AFTER_STRIP`` words, the original query is returned unchanged — a
    query that is little more than the name is asking for the company itself.
    """
    original = (query or "").strip()
    out = original
    for variant in variants:
        parts = variant.split()
        phrase = _SEPARATOR.join(re.escape(w) for w in parts)
        pattern = re.compile(rf"(?<![^\W_]){phrase}(?![^\W_])", re.IGNORECASE)
        if len(parts) == 1:
            # A one-word name is often an ordinary word ("Target", "Block", "Shell"). It
            # is the subject only where it is written like a name, so a lower-case
            # occurrence is left in the query.
            out = pattern.sub(lambda m: " " if m.group(0)[:1].isupper() else m.group(0), out)
        else:
            out = pattern.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip(" ,;:-()[]")
    if out.lower() == original.lower():
        return original, False
    if len(_words(out)) < MIN_WORDS_AFTER_STRIP:
        return original, False
    return out, True


def cap_per_document(
    items: Sequence[T],
    key: Callable[[T], Any],
    *,
    cap: int = PER_DOCUMENT_CAP,
) -> list[T]:
    """``items`` in their given order, at most ``cap`` per document.

    ``key`` returns the document a hit belongs to. A hit whose key is ``None`` is its own
    document: an unidentifiable hit is never grouped with another and never dropped.
    """
    if cap < 1:
        raise ValueError("cap must be at least 1.")
    counts: dict[Any, int] = {}
    kept: list[T] = []
    for index, item in enumerate(items):
        k = key(item)
        if k is None:
            k = ("__own__", index)
        counts[k] = counts.get(k, 0) + 1
        if counts[k] <= cap:
            kept.append(item)
    return kept


def diversify(
    items: Sequence[T],
    key: Callable[[T], Any],
    *,
    limit: int,
    cap: int = PER_DOCUMENT_CAP,
) -> list[T]:
    """At most ``limit`` items: each document's best ``cap`` chunks first, in rank order,
    then — only if the page is still short — the overflow, in rank order.

    A DEMOTION, not a drop. A company whose corpus is one large filing (a 10-K of several
    hundred chunks) must still get a full page; the cap only stops one document from
    crowding out the others when others exist.
    """
    if limit < 1:
        return []
    kept = cap_per_document(items, key, cap=cap)
    if len(kept) >= limit:
        return kept[:limit]
    chosen = {id(item) for item in kept}
    overflow = [item for item in items if id(item) not in chosen]
    return kept + overflow[: limit - len(kept)]


def pool_size(top_k: int) -> int:
    """How many candidates to request so that, after the per-document cap, ``top_k``
    distinct hits usually remain."""
    return max(top_k, min(top_k * POOL_FACTOR, POOL_CEILING))


def document_key(reference: Any) -> Any:
    """The document a corpus hit belongs to: its version, else its URL, else None."""
    return (
        getattr(reference, "research_document_version_id", None)
        or getattr(reference, "canonical_url", None)
        or None
    )


__all__ = [
    "MIN_WORDS_AFTER_STRIP",
    "PER_DOCUMENT_CAP",
    "POOL_CEILING",
    "POOL_FACTOR",
    "cap_per_document",
    "diversify",
    "document_key",
    "name_variants",
    "pool_size",
    "strip_subject_name",
]
