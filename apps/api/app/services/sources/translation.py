"""
Bounded, honest translation-provider abstraction — Phase 30A (foundation).

Parallel in shape to ``app.services.llm.client.LLMClient``: a small,
provider-pluggable interface for turning ONE bounded, non-English evidence
excerpt into a bounded English rendering for research context. This is the
foundation only — nothing here is wired into the council / report / evidence
collection yet (that is Phase 30A Task 2), and it is OFF by default.

Hard product invariants enforced here:
  * **Bounded, per-excerpt only.** Both the input and the output are capped at
    ``max_chars``; a whole document is never translated. There is no batch or
    full-document path.
  * **Original preserved.** Every result carries the (bounded) ``original_text``
    alongside the translation so a citation always keeps its source language and
    the caller keeps the source URL.
  * **Never "official".** Every result is machine-assisted and needs human
    review; it carries an explicit warning. The platform never presents a
    translation as authoritative.
  * **Never a real translation when it can't produce one.** The DEFAULT provider
    is a deterministic *fake* that emits a clearly-marked placeholder — never
    fabricated fluent English that could be mistaken for a real translation.
  * **Secret-free + text-free logs.** The optional LLM-backed provider logs ONLY
    counts and language codes via ``log_event`` — never the prompt, the original
    text, or the translated text. It composes ``get_llm_client`` (it does not
    re-implement the Azure client) and relies on the existing no-log guards.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from pydantic import BaseModel

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.structured_logging import log_event
from app.services.llm.client import LLMClient, LLMError, get_llm_client
from app.services.sources.language import language_name

_logger = logging.getLogger("app.services.sources.translation")

# The single honest disclaimer every result carries. Deliberately free of any
# rating / valuation vocabulary so it passes the report safety gate unchanged.
MACHINE_TRANSLATION_WARNING = (
    "Machine-assisted translation, NOT an official translation; "
    "human review required."
)

# Clearly-marked prefix the fake provider stamps so its output can never be
# mistaken for a real English translation.
FAKE_TRANSLATION_MARKER = "[machine translation unavailable — fake provider]"

# Conservative default per-excerpt bound (mirrors the evidence ``EXCERPT_MAX``).
TRANSLATION_MAX_CHARS = 400

TARGET_DEFAULT = "en"


def _bound(text: str, max_chars: int) -> str:
    """Trim ``text`` to at most ``max_chars`` characters (ellipsis when cut)."""
    limit = max(1, max_chars)
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def _norm_code(code: str | None) -> str:
    """Normalize a language code to a lowercase 2-letter code (``und`` fallback)."""
    c = (code or "").strip().lower()[:2]
    return c or "und"


class TranslationResult(BaseModel):
    """One bounded, machine-assisted rendering of a single excerpt.

    Never a whole document. Always carries the original text so the source
    language and citation are preserved, and always flags that it is
    machine-assisted and needs human review.
    """

    original_text: str
    translated_text: str
    source_language: str
    target_language: str = TARGET_DEFAULT
    provider_name: str = "fake"
    is_machine_assisted: bool = True
    needs_human_review: bool = True
    warning: str = MACHINE_TRANSLATION_WARNING


class TranslationProvider(ABC):
    """Abstract per-excerpt translation provider (parallel to ``LLMClient``)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Identifier for this backend ('fake' | 'llm')."""

    @property
    def is_fake(self) -> bool:
        return False

    @abstractmethod
    async def translate(
        self,
        text: str,
        source_language: str,
        *,
        target_language: str = TARGET_DEFAULT,
        max_chars: int = TRANSLATION_MAX_CHARS,
    ) -> TranslationResult:
        """Translate ONE bounded excerpt; bound both input and output to
        ``max_chars`` and never claim an official translation."""


class FakeTranslationProvider(TranslationProvider):
    """Deterministic, offline default provider (parallel to ``FakeLLMClient``).

    Produces an HONEST placeholder — a clearly-marked stub prefixed with
    :data:`FAKE_TRANSLATION_MARKER` — never fabricated fluent English. Used by
    default and in every test; needs no credentials and makes no network call.
    """

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def is_fake(self) -> bool:
        return True

    async def translate(
        self,
        text: str,
        source_language: str,
        *,
        target_language: str = TARGET_DEFAULT,
        max_chars: int = TRANSLATION_MAX_CHARS,
    ) -> TranslationResult:
        original = _bound(text, max_chars)
        # A clearly-marked, deterministic stub. The original tail is included so
        # a reviewer sees what was NOT translated, then re-bounded so the result
        # never exceeds one excerpt worth of characters.
        stub = f"{FAKE_TRANSLATION_MARKER} {original}".strip()
        translated = _bound(stub, max_chars)
        return TranslationResult(
            original_text=original,
            translated_text=translated,
            source_language=_norm_code(source_language),
            target_language=_norm_code(target_language),
            provider_name="fake",
            warning=MACHINE_TRANSLATION_WARNING,
        )


class LLMTranslationProvider(TranslationProvider):
    """Optional LLM-backed provider (OFF by default).

    COMPOSES an ``LLMClient`` (via ``get_llm_client`` in the factory) to translate
    ONE bounded excerpt to English. It never logs the prompt, the original text,
    or the translated text — only counts and language codes via ``log_event``.
    A provider error degrades to an empty (but honest) result rather than raising,
    so a translation failure can never crash a caller.
    """

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    @property
    def provider_name(self) -> str:
        return "llm"

    async def translate(
        self,
        text: str,
        source_language: str,
        *,
        target_language: str = TARGET_DEFAULT,
        max_chars: int = TRANSLATION_MAX_CHARS,
    ) -> TranslationResult:
        original = _bound(text, max_chars)
        src = _norm_code(source_language)
        tgt = _norm_code(target_language)

        system = (
            "You are a careful translation assistant. Translate the user's text "
            f"from {language_name(src)} into {language_name(tgt)}. Translate only "
            "what is present; never add, infer, or omit facts, numbers, or units. "
            'Reply with ONLY a JSON object: {"translation": "<text>"}.'
        )
        user = original

        translated_raw = ""
        try:
            data = await self._client.complete_json(
                system,
                user,
                max_tokens=512,
                temperature=0.0,
            )
            translated_raw = str(data.get("translation") or "")
        except LLMError:
            # Honest degradation: no fabricated translation, no crash.
            translated_raw = ""

        translated = _bound(translated_raw, max_chars)

        # Text-free telemetry: ONLY counts + language codes ever leave this line.
        # Never the prompt, the original text, or the translated text.
        log_event(
            _logger,
            "translation_performed",
            provider="llm",
            source_lang=src,
            target_lang=tgt,
            char_count=len(original),
            translated_char_count=len(translated),
        )

        return TranslationResult(
            original_text=original,
            translated_text=translated,
            source_language=src,
            target_language=tgt,
            provider_name="llm",
            warning=MACHINE_TRANSLATION_WARNING,
        )


def get_translation_provider(
    cfg: Settings | None = None,
    *,
    client: LLMClient | None = None,
) -> TranslationProvider:
    """Resolve a translation provider — the fake by default.

    Returns :class:`LLMTranslationProvider` ONLY when ``cfg.translation_provider``
    selects ``"llm"`` AND ``cfg.source_translation_enabled`` is True AND an
    ``LLMClient`` is available (an injected ``client`` or one from
    ``get_llm_client``). In every other case — including "llm" requested but no
    client available — it falls back to the honest :class:`FakeTranslationProvider`.
    """
    cfg = cfg or default_settings
    provider = (cfg.translation_provider or "fake").strip().lower()
    if provider == "llm" and cfg.source_translation_enabled:
        resolved = client or get_llm_client(cfg)
        if resolved is not None:
            return LLMTranslationProvider(resolved)
    return FakeTranslationProvider()


__all__ = [
    "MACHINE_TRANSLATION_WARNING",
    "FAKE_TRANSLATION_MARKER",
    "TRANSLATION_MAX_CHARS",
    "TARGET_DEFAULT",
    "TranslationResult",
    "TranslationProvider",
    "FakeTranslationProvider",
    "LLMTranslationProvider",
    "get_translation_provider",
]
