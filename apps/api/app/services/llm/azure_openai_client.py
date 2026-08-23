"""
Real LLM clients for the analysis council: Azure OpenAI and OpenAI-compatible.

Both are intentionally THIN wrappers over langchain-openai (already an optional
dependency used by app.integrations.llm_provider). They implement only
``_complete_raw``; the base class handles JSON parsing + repair.

Neither client is ever constructed in tests — the fake client is. Both raise
``LLMUnavailableError`` (never a bare error) when credentials or the optional
dependency are missing, so the factory can fall back to the deterministic path.

Nothing here logs prompts, completions, endpoints or credentials.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import Settings
from app.services.llm.client import (
    LLMClient,
    LLMError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    LLMUnavailableError,
)


def _require_langchain_openai():  # pragma: no cover - exercised only with real creds
    try:
        import langchain_openai  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise LLMUnavailableError(
            "langchain-openai is not installed; council LLM provider unavailable."
        ) from exc
    return langchain_openai


class AzureOpenAILLMClient(LLMClient):
    """Azure OpenAI council client (langchain AzureChatOpenAI)."""

    def __init__(self, cfg: Settings) -> None:  # pragma: no cover - needs real creds
        if not cfg.azure_openai_endpoint or not cfg.azure_openai_api_key:
            raise LLMUnavailableError("Azure OpenAI endpoint/key not configured.")
        if not cfg.azure_openai_deployment_name:
            raise LLMUnavailableError("Azure OpenAI deployment name not configured.")

        langchain_openai = _require_langchain_openai()
        self._deployment = cfg.azure_openai_deployment_name
        self._model = cfg.llm_model or cfg.azure_openai_deployment_name
        self._timeout_default = cfg.llm_request_timeout_seconds
        self._llm = langchain_openai.AzureChatOpenAI(
            azure_endpoint=cfg.azure_openai_endpoint,
            api_key=cfg.azure_openai_api_key,  # type: ignore[arg-type]
            api_version=cfg.azure_openai_api_version,
            azure_deployment=cfg.azure_openai_deployment_name,
            temperature=cfg.llm_temperature,
            max_tokens=cfg.llm_max_output_tokens,
            timeout=cfg.llm_request_timeout_seconds,
            # Fast-fail: no internal backoff/retry. The council runs 8 agents
            # sequentially behind a synchronous HTTP gateway with a fixed
            # timeout; a per-agent rate-limit backoff (tens of seconds each)
            # would blow that budget. A rate-limited agent instead fails fast and
            # is isolated, and the council still returns with the agents that did
            # complete. (Retries belong to a future async execution model.)
            max_retries=0,
        )

    @property
    def provider_name(self) -> str:
        return "azure_openai"

    @property
    def model_name(self) -> str | None:
        return self._model

    @property
    def deployment_name(self) -> str | None:
        return self._deployment

    async def _complete_raw(  # pragma: no cover - needs real creds
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        return await _ainvoke_chat(
            self._llm,
            system,
            user,
            timeout,
            record_usage=self._record_usage,
            record_finish_reason=self._record_finish_reason,
            max_tokens=max_tokens,
        )


class OpenAILLMClient(LLMClient):
    """OpenAI-compatible council client (langchain ChatOpenAI)."""

    def __init__(self, cfg: Settings) -> None:  # pragma: no cover - needs real creds
        if not cfg.openai_api_key:
            raise LLMUnavailableError("OPENAI_API_KEY not configured.")
        if not cfg.llm_model:
            raise LLMUnavailableError("LLM_MODEL not configured for openai provider.")

        langchain_openai = _require_langchain_openai()
        self._model = cfg.llm_model
        self._llm = langchain_openai.ChatOpenAI(
            api_key=cfg.openai_api_key,  # type: ignore[arg-type]
            model=cfg.llm_model,
            temperature=cfg.llm_temperature,
            max_tokens=cfg.llm_max_output_tokens,
            timeout=cfg.llm_request_timeout_seconds,
            # Fast-fail: no internal backoff/retry. The council runs 8 agents
            # sequentially behind a synchronous HTTP gateway with a fixed
            # timeout; a per-agent rate-limit backoff (tens of seconds each)
            # would blow that budget. A rate-limited agent instead fails fast and
            # is isolated, and the council still returns with the agents that did
            # complete. (Retries belong to a future async execution model.)
            max_retries=0,
        )

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str | None:
        return self._model

    async def _complete_raw(  # pragma: no cover - needs real creds
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        return await _ainvoke_chat(
            self._llm,
            system,
            user,
            timeout,
            record_usage=self._record_usage,
            record_finish_reason=self._record_finish_reason,
            max_tokens=max_tokens,
        )


def _extract_usage(result: object) -> tuple[int, int, int] | None:
    """Best-effort (prompt, completion, total) token counts from a response.

    Reads langchain's ``usage_metadata`` (input/output/total_tokens) first,
    then the raw provider ``response_metadata['token_usage']``. Returns None
    when neither carries integer counts — the caller then falls back to the
    estimated record. Never raises, never reads or returns text.
    """
    try:
        um = getattr(result, "usage_metadata", None)
        if isinstance(um, dict):
            pt, ct = um.get("input_tokens"), um.get("output_tokens")
            if isinstance(pt, int) and isinstance(ct, int):
                tt = um.get("total_tokens")
                return pt, ct, tt if isinstance(tt, int) else pt + ct
        rm = getattr(result, "response_metadata", None)
        if isinstance(rm, dict):
            tu = rm.get("token_usage") or rm.get("usage")
            if isinstance(tu, dict):
                pt, ct = tu.get("prompt_tokens"), tu.get("completion_tokens")
                if isinstance(pt, int) and isinstance(ct, int):
                    tt = tu.get("total_tokens")
                    return pt, ct, tt if isinstance(tt, int) else pt + ct
    except (AttributeError, TypeError, ValueError):
        return None
    return None


def _extract_finish_reason(result: object) -> str | None:
    """Best-effort provider finish reason (e.g. "stop", "length").

    Read from langchain's ``response_metadata``. Returns None when absent.
    Never raises and never returns model content — only the short reason token.
    """
    try:
        rm = getattr(result, "response_metadata", None)
        if isinstance(rm, dict):
            reason = rm.get("finish_reason") or rm.get("stop_reason")
            if isinstance(reason, str) and reason:
                return reason
    except (AttributeError, TypeError):
        return None
    return None


def _coerce_retry_after(exc: Exception) -> float | None:
    """Best-effort, NEVER-logged extraction of a numeric retry-after (seconds).

    Reads only NUMBERS from a provider exception — a ``retry_after`` attribute or
    a ``retry-after`` / ``retry-after-ms`` response header. A non-numeric value
    (e.g. an HTTP-date) or a missing header yields ``None``. Fully guarded: never
    raises, never logs, and never returns the raw header text.
    """
    try:
        direct = getattr(exc, "retry_after", None)
        if isinstance(direct, (int, float)):
            return float(direct)
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        getter = getattr(headers, "get", None)
        if callable(getter):
            ms = getter("retry-after-ms")
            if ms is not None:
                return float(ms) / 1000.0
            secs = getter("retry-after")
            if secs is not None:
                return float(secs)
    except (ValueError, TypeError, AttributeError):
        return None
    return None


def _classify_provider_error(exc: Exception) -> LLMError:
    """Duck-type a raw provider exception into a recoverable ``LLMError``.

    Never imports the provider SDK (it is optional) and never logs: it reads only
    a status code and the exception class name, plus a bounded numeric
    retry-after. A 429 or a ``*RateLimitError`` class → ``LLMRateLimitError``; a
    status >= 500 or a server-error class name → ``LLMServerError``; everything
    else → a generic (permanent) ``LLMError`` carrying only the type name.
    """
    name = type(exc).__name__
    status: int | None = None
    try:
        raw_status = getattr(exc, "status_code", None)
        if raw_status is None:
            response = getattr(exc, "response", None)
            raw_status = getattr(response, "status_code", None)
        if isinstance(raw_status, int):
            status = raw_status
    except (AttributeError, TypeError):
        status = None

    if status == 429 or name.endswith("RateLimitError"):
        return LLMRateLimitError(
            f"provider rate limited ({name})", retry_after=_coerce_retry_after(exc)
        )
    if (status is not None and status >= 500) or name.endswith(
        ("APIError", "InternalServerError", "ServiceUnavailableError")
    ):
        return LLMServerError(f"provider server error ({name})")
    return LLMError(f"provider call failed ({name})")


async def _ainvoke_chat(
    llm,
    system: str,
    user: str,
    timeout: int,
    record_usage=None,
    record_finish_reason=None,
    max_tokens: int | None = None,
) -> str:
    """Invoke a langchain chat model with a hard timeout; return text content.

    Any provider error (rate limit, API error, connection failure) is wrapped as
    a recoverable ``LLMError`` — never allowed to escape raw — so the council can
    isolate (and, when enabled, retry) a single failed agent instead of crashing
    the whole run. A 429 becomes ``LLMRateLimitError`` (with a bounded numeric
    retry-after), a 5xx becomes ``LLMServerError``, a timeout becomes
    ``LLMTimeoutError``. Only the error *type name* (and, for a rate limit, a
    numeric retry-after) is carried forward — never the message, headers, or URL;
    nothing here is logged.
    """
    messages = [("system", system), ("human", user)]
    # Per-CALL output budget, overriding the construction-time default. Councils
    # size each call from their own contract (``field_review_max_output_tokens``
    # / ``discovery_max_output_tokens``); without this every real call silently
    # used the constructor's value and those budgets were inert.
    #
    # The parameter MUST be ``max_completion_tokens``, not ``max_tokens``:
    # langchain-openai (>=1.x) already translates the constructor's
    # ``max_tokens`` into ``max_completion_tokens`` in the request payload, so
    # sending ``max_tokens`` here puts BOTH in the payload and Azure rejects the
    # call outright — HTTP 400 ``invalid_parameter_combination``, "Setting
    # 'max_tokens' and 'max_completion_tokens' at the same time is not
    # supported" (verified live against the staging deployment).
    invoke_kwargs: dict[str, Any] = {}
    if max_tokens is not None and int(max_tokens) > 0:
        invoke_kwargs["max_completion_tokens"] = int(max_tokens)
    try:
        result = await asyncio.wait_for(
            llm.ainvoke(messages, **invoke_kwargs), timeout=timeout
        )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise LLMTimeoutError("LLM call timed out") from exc
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 - any provider error -> recoverable
        raise _classify_provider_error(exc) from exc
    # Phase 32A TPM slice: record REAL provider token usage when present (counts
    # only). A missing/None extraction leaves the base class estimated record to
    # fill the gap; a failed call records nothing (no quota was spent).
    if record_finish_reason is not None:
        record_finish_reason(_extract_finish_reason(result))
    if record_usage is not None:
        usage = _extract_usage(result)
        if usage is not None:
            prompt_tokens, completion_tokens, total_tokens = usage
            record_usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated=False,
            )
    content = getattr(result, "content", result)
    if isinstance(content, list):
        # Some providers return a list of content blocks.
        content = " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)
