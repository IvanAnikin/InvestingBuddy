"""
LLM client abstraction for the Phase 28A analysis council.

A thin, provider-pluggable interface. The base class owns the parts every
provider shares — JSON parsing with a single repair attempt, and treating a
malformed reply as a recoverable error — while each concrete client implements
only ``_complete_raw`` (one string in, one string out).

Providers:
  fake          — deterministic, offline, no credentials. The ONLY client tests
                  use. See fake_client.py.
  azure_openai  — Azure OpenAI via langchain (thin, lazy import). See
                  azure_openai_client.py.
  openai        — OpenAI-compatible fallback (thin, lazy import). See
                  azure_openai_client.py.

Rules:
  - Never log prompts, completions, or credentials.
  - Never raise raw provider errors past ``complete_json`` — wrap them so the
    council can mark a single agent failed without crashing the whole report.
  - When the flag is off or no provider resolves, ``get_llm_client`` returns
    None and the caller keeps the deterministic path (report says LLM not used).
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

from app.core.config import Settings
from app.core.config import settings as default_settings

# Providers that are wired up. Anything else resolves to None (disabled).
_KNOWN_PROVIDERS = frozenset({"fake", "azure_openai", "openai"})

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class LLMError(Exception):
    """Base class for recoverable council-client errors."""


class LLMJsonError(LLMError):
    """The model did not return usable JSON, even after one repair attempt."""


class LLMTimeoutError(LLMError):
    """The provider call exceeded the configured timeout."""


class LLMUnavailableError(LLMError):
    """The provider could not be constructed (missing deps or credentials)."""


def _extract_json(raw: str) -> dict[str, Any]:
    """Parse a JSON object out of a raw completion.

    Tolerates a leading/trailing prose or a ```json code fence, but requires a
    single top-level object. Raises LLMJsonError on failure.
    """
    if not raw or not raw.strip():
        raise LLMJsonError("empty completion")

    candidates: list[str] = [raw]
    fence = _CODE_FENCE_RE.search(raw)
    if fence:
        candidates.insert(0, fence.group(1))
    block = _JSON_BLOCK_RE.search(raw)
    if block:
        candidates.append(block.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise LLMJsonError("no JSON object in completion")


class LLMClient(ABC):
    """Abstract council LLM client. Subclasses implement ``_complete_raw``."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Identifier for this backend ('fake' | 'azure_openai' | 'openai')."""

    @property
    def model_name(self) -> str | None:
        return None

    @property
    def deployment_name(self) -> str | None:
        return None

    @property
    def is_fake(self) -> bool:
        return False

    @abstractmethod
    async def _complete_raw(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        """Return the raw string completion for one system+user turn."""

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 1200,
        temperature: float = 0.1,
        timeout: int = 40,
        repair_instruction: str | None = None,
    ) -> dict[str, Any]:
        """Return a parsed JSON object, repairing one malformed reply if needed.

        Raises LLMJsonError / LLMTimeoutError / LLMUnavailableError — never a raw
        provider exception — so the council can isolate a single failed agent.
        """
        raw = await self._complete_raw(
            system,
            user,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
        try:
            return _extract_json(raw)
        except LLMJsonError:
            pass

        # One repair attempt. Append a blunt "JSON only" instruction.
        repair = repair_instruction or (
            "Your previous reply was not a single valid JSON object. Reply again "
            "with ONLY the JSON object, no prose, no code fences."
        )
        raw2 = await self._complete_raw(
            f"{system}\n\n{repair}",
            user,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
        return _extract_json(raw2)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_llm_client(settings: Settings | None = None) -> LLMClient | None:
    """Resolve a council client, or None when the council is disabled/unavailable.

    Returns None (never raises) when:
      - ``llm_council_enabled`` is False, or
      - the configured provider is unknown, or
      - the provider's credentials/dependencies are missing.

    A None return is the signal to keep the deterministic path and report that
    the LLM was not used.
    """
    cfg = settings or default_settings
    if not cfg.llm_council_enabled:
        return None

    provider = (cfg.llm_provider_council or "fake").strip().lower()
    if provider not in _KNOWN_PROVIDERS:
        return None

    if provider == "fake":
        from app.services.llm.fake_client import FakeLLMClient

        return FakeLLMClient()

    try:
        if provider == "azure_openai":
            from app.services.llm.azure_openai_client import AzureOpenAILLMClient

            return AzureOpenAILLMClient(cfg)
        if provider == "openai":
            from app.services.llm.azure_openai_client import OpenAILLMClient

            return OpenAILLMClient(cfg)
    except LLMUnavailableError:
        # Missing credentials or the optional langchain-openai dependency.
        # Preserve the deterministic path rather than crash the report.
        return None

    return None
