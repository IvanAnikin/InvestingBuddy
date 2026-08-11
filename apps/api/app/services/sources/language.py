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

# Distinctive English function words, used ONLY so a confident English content
# signal can be told apart from "no signal at all" (Phase 32A Problem F). Never
# used to gate anything — purely a confidence signal for detect_language.
_EN_HINTS: tuple[str, ...] = (
    " the ", " and ", " of ", " to ", " in ", " is ", " for ", " with ",
    " on ", " that ", " company ", " group ",
)

# Minimum distinctive-stopword hits before content alone flips off the English
# default. Preserves the original extractor threshold.
_MIN_CONTENT_HITS = 3

# How much leading text the content scan considers (matches the original bound).
_SCAN_CHARS = 4000

# A content scan below this many characters is too thin to trust either way —
# treated as "content-based detection could not determine a language" rather
# than silently assumed English (Phase 32A Problem F).
_MIN_CONTENT_CHARS_FOR_DETECTION = 40

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


def _content_language(text: str) -> str | None:
    """Best-effort language purely from CONTENT, or None when inconclusive.

    None means the text is too short, or scored below the distinctive-stopword
    threshold for every language INCLUDING English — a genuine "cannot tell",
    never a guess. This is what lets a caller (Phase 32A Problem F) treat a
    domicile-derived ``hint`` as a weak fallback instead of a pre-emptive
    override: a confident content result (English or not) always wins.
    """
    stripped = (text or "").strip()
    if len(stripped) < _MIN_CONTENT_CHARS_FOR_DETECTION:
        return None
    low = f" {stripped[:_SCAN_CHARS].lower()} "
    scores: dict[str, int] = {"en": sum(1 for h in _EN_HINTS if h in low)}
    for lang, hints in _LANG_HINTS.items():
        scores[lang] = sum(1 for h in hints if h in low)
    best_lang = max(scores, key=lambda lang: scores[lang])
    if scores[best_lang] >= _MIN_CONTENT_HITS:
        return best_lang
    return None


def detect_language_with_confidence(
    text: str, *, hint: str | None = None
) -> tuple[str, bool]:
    """Return ``(code, is_content_based)`` for ``text`` (default code ``"en"``).

    Priority (Phase 32A Problem F): CONTENT-based detection runs first and wins
    whenever it is confident (``is_content_based=True``) — an English document
    from a non-English-domicile issuer is never re-labelled by a domicile guess.
    Only when content is genuinely inconclusive (too short / no distinctive
    signal either way) does an explicit ``hint`` (e.g. an issuer's registered
    country) act as a WEAK fallback (``is_content_based=False``); with no usable
    hint either, the honest final default is English, also marked NOT
    content-based so a caller can tell a real detection from a bare guess.
    """
    content = _content_language(text)
    if content is not None:
        return content, True

    if hint:
        code = hint.strip().lower()[:2]
        if code in _HINT_SUPPORTED:
            return code, False

    return "en", False


def detect_language(text: str, *, hint: str | None = None) -> str:
    """Return a 2-letter language code for ``text`` (default ``"en"``).

    Thin wrapper over :func:`detect_language_with_confidence` for callers that
    only need the code. See that function for the content-first/hint-fallback
    priority. This never blocks anything — it only labels.
    """
    code, _confident = detect_language_with_confidence(text, hint=hint)
    return code


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
