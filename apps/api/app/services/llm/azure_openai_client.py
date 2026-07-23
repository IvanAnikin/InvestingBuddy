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

from app.core.config import Settings
from app.services.llm.client import (
    LLMClient,
    LLMError,
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
        return await _ainvoke_chat(self._llm, system, user, timeout)


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
        return await _ainvoke_chat(self._llm, system, user, timeout)


async def _ainvoke_chat(llm, system: str, user: str, timeout: int) -> str:
    """Invoke a langchain chat model with a hard timeout; return text content.

    Any provider error (rate limit, API error, connection failure) is wrapped as
    a recoverable ``LLMError`` — never allowed to escape raw — so the council can
    isolate a single failed agent instead of crashing the whole run. Only the
    error *type name* is carried forward (never the message, which could echo a
    URL/header); nothing here is logged.
    """
    messages = [("system", system), ("human", user)]
    try:
        result = await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise LLMTimeoutError("LLM call timed out") from exc
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 - any provider error -> recoverable
        raise LLMError(f"provider call failed ({type(exc).__name__})") from exc
    content = getattr(result, "content", result)
    if isinstance(content, list):
        # Some providers return a list of content blocks.
        content = " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)
