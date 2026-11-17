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
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "ru": "Russian",
    "el": "Greek",
    "ar": "Arabic",
    "he": "Hebrew",
    "th": "Thai",
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


def script_language(text: str, *, scan_chars: int = _SCAN_CHARS) -> str | None:
    """A language from the WRITING SYSTEM alone, or None — open-web W3.

    The stopword heuristic above only knows Latin-script languages, so a Japanese or
    Russian page would be labelled English by default. Script is a strong, cheap
    signal for exactly those: kana means Japanese, Hangul Korean, Han without kana
    Chinese, Cyrillic Russian (the common case; a label, never a gate), Greek, Arabic,
    Hebrew, Thai. Latin script says nothing here and returns None. Additive: no
    existing caller uses it.
    """
    counts: dict[str, int] = {}
    letters = 0
    for ch in (text or "")[:scan_chars]:
        code = ord(ch)
        if not ch.isalpha():
            continue
        letters += 1
        if 0x3040 <= code <= 0x30FF:
            key = "ja"
        elif 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF:
            key = "ko"
        elif 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
            key = "han"
        elif 0x0400 <= code <= 0x04FF:
            key = "ru"
        elif 0x0370 <= code <= 0x03FF:
            key = "el"
        elif 0x0600 <= code <= 0x06FF:
            key = "ar"
        elif 0x0590 <= code <= 0x05FF:
            key = "he"
        elif 0x0E00 <= code <= 0x0E7F:
            key = "th"
        else:
            continue
        counts[key] = counts.get(key, 0) + 1
    if not letters:
        return None
    if counts.get("ja", 0) >= 5:
        return "ja"  # kana beside kanji is Japanese, never Chinese
    non_latin = sum(counts.values())
    if non_latin < max(5, letters // 3):
        return None
    best = max(counts, key=lambda k: counts[k])
    return "zh" if best == "han" else best


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


# ── V3.20 slice 10.1 — document-level detection for the translation corpus ──────── #
#
# A SEPARATE function so ``detect_language`` (and every Phase-30 caller that relies on
# its FR/DE/IT/DA-or-English behaviour) is unchanged. Spec §3.12 languages: DE IT FR ES
# NL SV NO DA FI PL CS (Latin script: distinctive function words + letters) and JA KO
# ZH (writing system, ``script_language``).

#: Languages a held document is translated FROM (spec §3.12, D6).
TRANSLATABLE_LANGUAGES: frozenset[str] = frozenset(
    {"de", "it", "fr", "es", "nl", "sv", "no", "da", "fi", "pl", "cs", "ja", "ko", "zh"})

_DOC_HINTS: dict[str, tuple[str, ...]] = {
    "en": (" the ", " and ", " of ", " to ", " is ", " for ", " with ", " that ",
           " which ", " were ", " has been "),
    "de": (" der ", " die ", " und ", " das ", " nicht ", " wird ", " mit ", " für ",
           " geschäftsjahr", " umsatz", " gesellschaft", " konzern"),
    "fr": (" le ", " les ", " des ", " et ", " est ", " pour ", " dans ", " une ",
           " société", " exercice", " chiffre d'affaires", " qui "),
    "it": (" il ", " gli ", " che ", " della ", " delle ", " sono ", " per ", " ricavi",
           " società", " esercizio", " anche ", " nel "),
    "es": (" el ", " los ", " las ", " del ", " que ", " por ", " una ", " ingresos",
           " sociedad", " ejercicio", " según ", " también "),
    "nl": (" het ", " een ", " van ", " voor ", " niet ", " zijn ", " wordt ", " omzet",
           " vennootschap", " boekjaar", " deze ", " onze "),
    "sv": (" och ", " är ", " för ", " att ", " som ", " inte ", " av ", " till ",
           " bolaget", " omsättning", " under ", " också "),
    "no": (" og ", " er ", " av ", " på ", " ikke ", " til ", " for ", " selskapet",
           " omsetning", " driftsinntekter", " konsernet", " også "),
    "da": (" og ", " er ", " af ", " på ", " ikke ", " til ", " for ", " selskabet",
           " omsætning", " årsrapport", " koncernen", " også "),
    "fi": (" ja ", " on ", " oli ", " että ", " vuonna", " liikevaihto", " yhtiö",
           " miljoonaa", " euroa", " sekä ", " kanssa", " mukaan"),
    "pl": (" się ", " jest ", " oraz ", " przez ", " spółka", " spółki", " przychody",
           " roku", " który ", " które ", " na ", " w "),
    "cs": (" se ", " je ", " jsou ", " které ", " společnost", " tržby", " roce", " pro ",
           " byla ", " bylo ", " na ", " v "),
}
#: Letters that only (or overwhelmingly) one of the candidates uses: one point each.
_DOC_LETTERS: dict[str, str] = {
    "de": "ß", "pl": "łąęśźżń", "cs": "řěůťď", "sv": "", "da": "", "no": "",
    "es": "ñ¿¡", "fr": "çœ", "it": "", "nl": "ĳ", "fi": "",
}
_DOC_MIN_HITS = 4


def detect_document_language(text: str) -> str | None:
    """The language of a held DOCUMENT (or chunk), or ``None`` when it cannot tell.

    Writing system first (JA/KO/ZH and other non-Latin scripts), then distinctive
    function words and letters over the first ``_SCAN_CHARS`` characters. ``None`` —
    never a guess — when the text is short or no language clears ``_DOC_MIN_HITS`` with
    a margin over the runner-up: an unknown language is not translated.
    """
    script = script_language(text)
    if script is not None:
        return script
    stripped = (text or "").strip()
    if len(stripped) < _MIN_CONTENT_CHARS_FOR_DETECTION:
        return None
    low = f" {' '.join(stripped[:_SCAN_CHARS].lower().split())} "
    scores: dict[str, int] = {}
    for lang, hints in _DOC_HINTS.items():
        score = sum(low.count(h) for h in hints)
        letters = _DOC_LETTERS.get(lang, "")
        if letters:
            score += min(10, sum(low.count(ch) for ch in letters))
        scores[lang] = score
    # Scandinavian: å/ø/æ/ä/ö split the three (Swedish ä ö; Danish/Norwegian æ ø).
    sv_letters = low.count("ä") + low.count("ö")
    dano_letters = low.count("æ") + low.count("ø")
    if sv_letters > dano_letters:
        scores["sv"] += min(10, sv_letters)
    elif dano_letters:
        scores["da"] += min(10, dano_letters)
        scores["no"] += min(10, dano_letters)
    if "ä" in low and "ö" in low and ("y" in low) and scores["fi"] >= _DOC_MIN_HITS:
        scores["fi"] += 2
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    best, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if best_score < _DOC_MIN_HITS or best_score <= runner_up:
        return None
    return best


__all__ = [
    "TRANSLATABLE_LANGUAGES",
    "detect_document_language",
    "detect_language",
    "language_name",
    "script_language",
    "LANGUAGE_NAMES",
]
