"""
Shared, dependency-free language detection — Phase 30A (foundation).

A tiny, deterministic heuristic that generalizes the language hint that lived
inside ``document_text_extractor._detect_language``. It is used ONLY to *label*
evidence honestly (which language an excerpt is in, whether it plausibly needs a
translation) — never to translate, never to gate research, never to fabricate.

Design guarantees:
  * **Stdlib only.** No heavy language-id dependency; just script/charset checks
    plus small stopword sets. Cheap enough to run per excerpt.
  * **Honest + conservative.** Defaults to English. A non-English label is only
    returned when either an explicit hint names a supported language or enough
    distinctive stopwords are present.
  * **Deterministic.** No randomness, stable dict iteration order, so the same
    text always maps to the same code (safe for tests and citations).

Public API:
  ``detect_language(text, *, hint=None) -> str``  — a 2-letter code (default "en").
  ``language_name(code) -> str``                  — an honest human label.
  ``LANGUAGE_NAMES``                              — code → English name map.
"""

from __future__ import annotations

# Distinctive, space-delimited function words per language. Space-padding avoids
# matching these letter sequences inside longer words of another language.
# Kept small and high-signal; extended from the original FR/DE/IT set with a
# Danish/Nordic set (Phase 30A). Insertion order is the tie-break order.
_LANG_HINTS: dict[str, tuple[str, ...]] = {
    "fr": (" le ", " la ", " les ", " des ", " et ", " société", " exercice",
           " chiffre d'affaires"),
    "de": (" der ", " die ", " und ", " das ", " geschäftsjahr", " umsatz",
           " gesellschaft"),
    "it": (" il ", " la ", " che ", " gli ", " ricavi", " società", " esercizio"),
    "da": (" og ", " af ", " er ", " på ", " selskabet", " regnskabsår",
           " årsrapport", " omsætning"),
}

# Minimum distinctive-stopword hits before content alone flips off the English
# default. Preserves the original extractor threshold.
_MIN_CONTENT_HITS = 3

# How much leading text the content scan considers (matches the original bound).
_SCAN_CHARS = 4000

# Supported non-English codes an explicit hint is trusted for. An "en" or unknown
# hint falls through to the content scan (preserving the original behaviour where
# only FR/DE/IT hints short-circuited).
_HINT_SUPPORTED = frozenset(_LANG_HINTS.keys())

# Honest human labels for codes we may stamp. A superset of the detected codes so
# a hint carried from an issuer registry (e.g. "nl", "sv") is still labelled
# honestly rather than shown as a bare code. Unknown codes fall back to the code.
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "da": "Danish",
    "nl": "Dutch",
    "es": "Spanish",
    "pt": "Portuguese",
    "sv": "Swedish",
    "no": "Norwegian",
    "nb": "Norwegian",
    "fi": "Finnish",
}


def detect_language(text: str, *, hint: str | None = None) -> str:
    """Return a 2-letter language code for ``text`` (default ``"en"``).

    An explicit ``hint`` (e.g. an issuer's ``original_language``) is honoured
    first when it names a supported non-English language; otherwise the leading
    ``_SCAN_CHARS`` characters are scanned for distinctive stopwords and the
    language with the most hits (at least ``_MIN_CONTENT_HITS``) wins. English is
    the conservative default. This never blocks anything — it only labels.
    """
    if hint:
        code = hint.strip().lower()[:2]
        if code in _HINT_SUPPORTED:
            return code

    low = f" {text[:_SCAN_CHARS].lower()} "
    best_lang, best_hits = "en", 0
    for lang, hints in _LANG_HINTS.items():
        hits = sum(1 for h in hints if h in low)
        if hits > best_hits:
            best_lang, best_hits = lang, hits
    if best_hits >= _MIN_CONTENT_HITS:
        return best_lang
    return "en"


def language_name(code: str | None) -> str:
    """An honest human label for a language code (falls back to the code)."""
    if not code:
        return "Unknown"
    key = code.strip().lower()[:2]
    return LANGUAGE_NAMES.get(key, key.upper() or "Unknown")


__all__ = [
    "detect_language",
    "language_name",
    "LANGUAGE_NAMES",
]
