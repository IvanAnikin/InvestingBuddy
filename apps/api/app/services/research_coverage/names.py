"""Company name ↔ research-provider slug matching — V3.21 slice 21.7 (ADR-114).

CONSERVATIVE BY DESIGN
======================
A wrong match tells a reader that a company is covered when it is not, and lowers its
obscurity score. A missed match only leaves a coverage page unlisted. So the rule is
EQUALITY of one normalised key, never a prefix, a substring or a fuzzy score:

1. fold accents, lower-case, ``&`` → ``and``, drop dots (``S.A.`` → ``sa``);
2. split on anything that is not a letter or a digit;
3. drop a leading ``the`` and trailing legal-form words (``plc``, ``ltd``, ``holdings``,
   ``group``, ``ag``, ``asa`` …) — on BOTH sides, so ``Ceres Power Holdings plc`` and
   the slug ``ceres-power-holdings`` both become ``ceres power``;
4. for a provider slug only, drop a trailing ``-2`` style disambiguator (``artnet-2``);
5. the key is the tokens joined with nothing (``cerespower``), so a slug written without
   hyphens (``metlenenergyandmetals``) still equals ``Metlen Energy & Metals S.A.``.

A key that is too weak to identify a company — fewer than four characters, or a single
generic word such as ``power`` or ``energy`` — is ``None`` and matches nothing.
"""

from __future__ import annotations

import re
import unicodedata

#: Trailing words that name a legal form or a holding structure, not the business.
LEGAL_SUFFIX_TOKENS: frozenset[str] = frozenset({
    "plc", "ltd", "limited", "inc", "incorporated", "corp", "corporation", "co",
    "company", "ag", "sa", "se", "nv", "bv", "asa", "as", "ab", "oyj", "oy", "spa",
    "sarl", "sas", "gmbh", "kgaa", "llc", "lp", "llp", "pty", "bhd", "publ", "holdings",
    "holding", "group", "sca", "kk", "tbk", "saog", "saoc", "psc", "pjsc", "nl",
})
#: One of these alone does not identify a company.
GENERIC_TOKENS: frozenset[str] = frozenset({
    "energy", "power", "capital", "resources", "international", "global", "technology",
    "technologies", "industries", "industry", "solutions", "systems", "partners",
    "investments", "investment", "trust", "fund", "bank", "mining", "gold", "oil", "gas",
    "electric", "electricity", "renewables", "solar", "wind", "minerals", "metals",
    "pharma", "therapeutics", "biotech", "properties", "realty", "equity", "research",
})
MIN_KEY_CHARS = 4
MAX_KEY_CHARS = 200

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_TRAILING_DISAMBIGUATOR = re.compile(r"-\d{1,2}$")


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def name_tokens(text: str | None) -> tuple[str, ...]:
    """The normalised tokens of a company name (legal form and leading ``the`` dropped)."""
    if not text:
        return ()
    folded = _fold(str(text)).replace("&", " and ").replace(".", "")
    tokens = [t for t in _NON_ALNUM.split(folded) if t]
    if len(tokens) > 1 and tokens[0] == "the":
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1] in LEGAL_SUFFIX_TOKENS:
        tokens.pop()
    return tuple(tokens)


def key_from_tokens(tokens: tuple[str, ...]) -> str | None:
    """The match key, or ``None`` when the tokens are too weak to identify a company."""
    if not tokens:
        return None
    if len(tokens) == 1 and tokens[0] in GENERIC_TOKENS | LEGAL_SUFFIX_TOKENS:
        return None
    key = "".join(tokens)
    if len(key) < MIN_KEY_CHARS or key.isdigit():
        return None
    return key[:MAX_KEY_CHARS]


def name_key(name: str | None) -> str | None:
    """The match key of a company name (``Ceres Power Holdings plc`` → ``cerespower``)."""
    return key_from_tokens(name_tokens(name))


def slug_tokens(slug: str | None) -> tuple[str, ...]:
    if not slug:
        return ()
    return name_tokens(_TRAILING_DISAMBIGUATOR.sub("", str(slug).strip("/").lower()))


def slug_key(slug: str | None) -> str | None:
    """The match key of a provider slug (``ceres-power-holdings`` → ``cerespower``)."""
    return key_from_tokens(slug_tokens(slug))


def title_from_slug(slug: str | None) -> str | None:
    """A display title from a slug (``ceres-power-holdings`` → ``Ceres Power Holdings``)."""
    if not slug:
        return None
    bare = _TRAILING_DISAMBIGUATOR.sub("", str(slug).strip("/"))
    words = [w for w in re.split(r"[-_]+", bare) if w]
    return " ".join(w[:1].upper() + w[1:] for w in words)[:200] or None


def mentions(name_tokens_: tuple[str, ...], text: str | None) -> bool:
    """Whether ``text`` names the company: its tokens appear CONTIGUOUSLY in the text's
    tokens, or one of the text's path segments has the company's key. Used to accept a
    search hit; a title that merely shares one word is not a mention."""
    key = key_from_tokens(name_tokens_)
    if key is None or not text:
        return False
    folded = _fold(str(text)).replace("&", " and ").replace(".", "")
    hay = [t for t in _NON_ALNUM.split(folded) if t]
    n = len(name_tokens_)
    for i in range(0, len(hay) - n + 1):
        if tuple(hay[i:i + n]) == name_tokens_:
            return True
    for segment in re.split(r"[/?#=&]+", str(text).lower()):
        if segment and slug_key(segment) == key:
            return True
    return False


__all__ = [
    "GENERIC_TOKENS",
    "LEGAL_SUFFIX_TOKENS",
    "key_from_tokens",
    "mentions",
    "name_key",
    "name_tokens",
    "slug_key",
    "slug_tokens",
    "title_from_slug",
]
