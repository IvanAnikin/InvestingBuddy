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
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.services.llm.token_pacer import estimate_tokens

# Providers that are wired up. Anything else resolves to None (disabled).
_KNOWN_PROVIDERS = frozenset({"fake", "azure_openai", "openai"})

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


@dataclass
class LLMUsage:
    """Token usage accumulated across the raw calls of ONE ``complete_json``.

    ``estimated`` is True when any contributing call lacked provider usage
    metadata and fell back to the ~4-chars/token heuristic. Carries counts
    only — never text.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0
    estimated: bool = False


class LLMError(Exception):
    """Base class for recoverable council-client errors."""


class LLMJsonError(LLMError):
    """The model did not return usable JSON, even after one repair attempt.

    ``truncated`` is True when the provider reported a length/max-tokens finish
    reason for the attempt — i.e. the reply was cut off mid-object rather than
    being malformed for some other reason. Carried for DIAGNOSTICS only: a
    truncated reply is still a PERMANENT failure (retrying with the same output
    budget reproduces it exactly), so it must never be presented as an
    evidence-based judgement.
    """

    def __init__(self, message: str = "invalid json", *, truncated: bool = False) -> None:
        super().__init__(message)
        self.truncated = truncated


class LLMTimeoutError(LLMError):
    """The provider call exceeded the configured timeout."""


class LLMRateLimitError(LLMError):
    """The provider rate-limited the call (HTTP 429).

    Carries an optional ``retry_after`` (seconds) extracted from the provider's
    response — a bounded numeric hint only, never the raw header text. The
    council honors it (capped) when scheduling a retry. Transient/recoverable.
    """

    def __init__(
        self, message: str = "rate limited", *, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMServerError(LLMError):
    """A temporary provider-side server error (HTTP 5xx). Transient/recoverable."""


class LLMUnavailableError(LLMError):
    """The provider could not be constructed (missing deps or credentials)."""


# Errors the council may retry: a transient rate-limit, provider 5xx, or timeout.
# Everything else — malformed JSON after the single repair (LLMJsonError), a
# missing provider/credentials (LLMUnavailableError), a safety quarantine (a
# ``failed`` status, not an exception), or any other generic LLMError — is
# PERMANENT and must never be retried.
_TRANSIENT_LLM_ERRORS: tuple[type[LLMError], ...] = (
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
)


def is_transient_llm_error(exc: Exception) -> bool:
    """True when ``exc`` is a transient (retryable) council-client error."""
    return isinstance(exc, _TRANSIENT_LLM_ERRORS)


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

    # Usage accumulated since the last ``consume_usage`` (Phase 32A TPM slice).
    # Class-level default so subclasses need no __init__ change; instances
    # assign their own value on first record. One client instance is created
    # per council run and its agents run sequentially, so this is safe.
    _usage: LLMUsage | None = None
    # Provider finish reason of the most recent raw call (e.g. "stop",
    # "length"). Used only to explain a JSON failure; never logged as content.
    _last_finish_reason: str | None = None

    def _record_finish_reason(self, reason: str | None) -> None:
        self._last_finish_reason = str(reason) if reason else None

    @property
    def last_response_truncated(self) -> bool:
        """True when the last raw call stopped because it hit the token limit."""
        return (self._last_finish_reason or "").lower() in {"length", "max_tokens"}

    def _record_usage(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int | None = None,
        estimated: bool = False,
    ) -> None:
        """Accumulate one raw call's token usage (counts only, never text)."""
        usage = self._usage if self._usage is not None else LLMUsage()
        usage.prompt_tokens += max(0, int(prompt_tokens))
        usage.completion_tokens += max(0, int(completion_tokens))
        usage.total_tokens += max(
            0,
            int(
                total_tokens
                if total_tokens is not None
                else prompt_tokens + completion_tokens
            ),
        )
        usage.calls += 1
        usage.estimated = usage.estimated or estimated
        self._usage = usage

    def consume_usage(self) -> LLMUsage | None:
        """Pop the usage accumulated since the last consume (or None)."""
        usage = self._usage
        self._usage = None
        return usage

    async def _traced_complete_raw(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        """``_complete_raw`` + a guaranteed usage record for the call.

        A provider client that extracts real usage records it itself inside
        ``_complete_raw``; when it does not (fake client, providers without
        usage metadata), an ESTIMATED record is added here so the pacing and
        observability layers always have a number to work with. A raised
        provider error records nothing (a rate-limited call spends no quota).
        """
        calls_before = self._usage.calls if self._usage is not None else 0
        self._record_finish_reason(None)
        raw = await self._complete_raw(
            system,
            user,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
        calls_after = self._usage.calls if self._usage is not None else 0
        if calls_after == calls_before:
            self._record_usage(
                prompt_tokens=estimate_tokens(system) + estimate_tokens(user),
                completion_tokens=estimate_tokens(raw),
                estimated=True,
            )
        return raw

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
        raw = await self._traced_complete_raw(
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
        raw2 = await self._traced_complete_raw(
            f"{system}\n\n{repair}",
            user,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
        try:
            return _extract_json(raw2)
        except LLMJsonError as exc:
            # Attribute the failure when the provider says it ran out of output
            # budget. Still PERMANENT — the repair reused the same budget, so a
            # retry would truncate identically — but the operator now sees WHY.
            if self.last_response_truncated:
                raise LLMJsonError(
                    "completion truncated at the output-token limit",
                    truncated=True,
                ) from exc
            raise


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
