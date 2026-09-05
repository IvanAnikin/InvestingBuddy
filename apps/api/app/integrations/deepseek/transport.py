"""DeepSeek transport — V3.4 Slice 4.3.

One narrow seam between the platform and DeepSeek's HTTP API, so everything above it is
testable without a network and everything vendor-specific is in one file a reviewer can
read in full.

WHAT IS VERIFIED HERE AND WHAT IS NOT — READ THIS BEFORE ENABLING
================================================================
DeepSeek's API is **OpenAI-compatible chat completions**, and that shape is used below
without ceremony because it is the convention the platform already speaks
(``azure_openai_client`` wraps the same shape).

**The exact wire shape of DeepSeek's server-side web search is NOT verified against the
live API in this campaign.** It is modelled as a tool declaration on a chat-completions
call, which is the OpenAI-compatible convention, and the request/response mapping is
confined to :meth:`HttpDeepSeekTransport.search` and
:func:`app.integrations.deepseek.providers.parse_search_payload`.

That is stated plainly rather than glossed because the platform's rule about unverified
claims applies to its own code as much as to a provider's: the FIGI check digit is not
validated for the same reason, and a wrong parser that *looks* right is worse than an
honest seam. Two consequences follow, and both are enforced:

* ``V3_DEEPSEEK_SEARCH_ENABLED`` defaults **off**, so nothing calls it by accident.
* The parser is written to **tolerate a shape it does not recognise** and return
  ``[]`` with a warning rather than raise or invent candidates — an unrecognised payload
  is "no candidates and a note saying why", never a guess.

Confirming the shape is a small change to one method and one parser, and the opt-in live
contract test (``ENABLE_INTEGRATION_TESTS`` plus a real key) is what confirms it.

NOTHING HERE LOGS A PROMPT, A COMPLETION, AN ENDPOINT OR A CREDENTIAL
====================================================================
Same rule the existing clients follow, and for the same recorded reason: root-level INFO
logging once leaked an EODHD ``api_token`` in this repository.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

#: DeepSeek's documented API root. Overridable by configuration, never hardcoded at a
#: call site.
DEFAULT_BASE_URL = "https://api.deepseek.com"

#: The tool name used for server-side web search. Configurable precisely because the
#: exact contract is unverified — see the module docstring.
DEFAULT_SEARCH_TOOL_NAME = "web_search"


class DeepSeekUnavailableError(RuntimeError):
    """DeepSeek cannot be reached or is not configured.

    Transient by default so the worker may retry: a missing key is permanent but a
    connection failure is not, and the caller distinguishes them by *why* it was raised
    rather than by the type. Where the distinction matters — an unconfigured provider —
    the factory returns ``None`` instead of raising at all, exactly as
    ``get_llm_client`` does.
    """

    job_transient = True


@dataclass
class DeepSeekResponse:
    """One raw response, already reduced to what the platform needs.

    ``raw`` is retained because provider behaviour is data — it is what makes a benchmark
    reproducible six months later when the vendor's defaults have changed and nobody
    remembers what they were.
    """

    text: str | None = None
    tool_payloads: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DeepSeekTransport(Protocol):
    """The seam. Everything above this is testable without a network."""

    model: str

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> DeepSeekResponse:
        ...  # pragma: no cover - protocol

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        domains: Sequence[str] | None,
        timeout: int,
    ) -> DeepSeekResponse:
        ...  # pragma: no cover - protocol


@dataclass
class FakeDeepSeekTransport:
    """The only transport the unit suite touches.

    Scriptable down to the failures that matter — a malformed payload, an unrecognised
    shape, a timeout — because those are the paths the parsing and promotion logic exists
    for and the ones a healthy live API would never produce.
    """

    model: str = "deepseek-chat"
    completion_text: str | None = '{"ok": true}'
    tool_payloads: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 120
    completion_tokens: int = 60
    cached_tokens: int = 0
    finish_reason: str | None = "stop"
    raises: Exception | None = None
    completions: list[tuple[str, str]] = field(default_factory=list)
    searches: list[str] = field(default_factory=list)

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> DeepSeekResponse:
        self.completions.append((system, user))
        if self.raises is not None:
            raise self.raises
        return DeepSeekResponse(
            text=self.completion_text,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cached_tokens=self.cached_tokens,
            finish_reason=self.finish_reason,
            raw={"fake": True},
        )

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        domains: Sequence[str] | None,
        timeout: int,
    ) -> DeepSeekResponse:
        self.searches.append(query)
        if self.raises is not None:
            raise self.raises
        return DeepSeekResponse(
            tool_payloads=list(self.tool_payloads),
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            finish_reason=self.finish_reason,
            raw={"fake": True},
        )


@dataclass
class HttpDeepSeekTransport:
    """The real transport. **Never constructed by the unit suite.**

    Deliberately small: an OpenAI-compatible POST, a usage block, and a tool-call
    payload. Everything interpretive lives in the providers, so the part of this slice
    that is unverified against the live API is a handful of lines rather than a layer.
    """

    api_key: str
    model: str
    base_url: str = DEFAULT_BASE_URL
    search_tool_name: str = DEFAULT_SEARCH_TOOL_NAME
    #: Injected so the SDK import stays lazy and a test can supply a stub without a
    #: network — the same pattern the corpus artifact store uses for its Azure client.
    client_factory: Any = None

    def _client(self, timeout: int) -> Any:  # pragma: no cover - needs a real key
        if self.client_factory is not None:
            return self.client_factory(timeout=timeout)
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise DeepSeekUnavailableError("httpx is not installed.") from exc
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def _post(
        self, payload: dict[str, Any], timeout: int
    ) -> dict[str, Any]:  # pragma: no cover - needs a real key
        client = self._client(timeout)
        try:
            response = await client.post("/chat/completions", json=payload)
            if response.status_code >= 400:
                # The status code, never the body: a provider error body can echo the
                # prompt, and this message reaches logs.
                raise DeepSeekUnavailableError(
                    f"DeepSeek returned HTTP {response.status_code}."
                )
            body = response.json()
        except DeepSeekUnavailableError:
            raise
        except Exception as exc:
            raise DeepSeekUnavailableError(
                f"DeepSeek request failed: {type(exc).__name__}"
            ) from exc
        finally:
            closer = getattr(client, "aclose", None)
            if closer is not None:
                await closer()
        if not isinstance(body, dict):
            raise DeepSeekUnavailableError("DeepSeek returned a non-object response.")
        return body

    @staticmethod
    def _reduce(body: dict[str, Any]) -> DeepSeekResponse:  # pragma: no cover
        choices = body.get("choices") or []
        first = choices[0] if isinstance(choices, list) and choices else {}
        message = first.get("message") or {} if isinstance(first, dict) else {}
        usage = body.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        tool_calls = message.get("tool_calls") or []
        payloads = [tc for tc in tool_calls if isinstance(tc, dict)]
        return DeepSeekResponse(
            text=message.get("content"),
            tool_payloads=payloads,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            cached_tokens=int(details.get("cached_tokens") or 0),
            finish_reason=(first.get("finish_reason") if isinstance(first, dict) else None),
            raw=body,
        )

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> DeepSeekResponse:  # pragma: no cover - needs a real key
        return self._reduce(
            await self._post(
                {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "response_format": {"type": "json_object"},
                },
                timeout,
            )
        )

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        domains: Sequence[str] | None,
        timeout: int,
    ) -> DeepSeekResponse:  # pragma: no cover - needs a real key
        # THE UNVERIFIED PART. A tool declaration on a chat-completions call is the
        # OpenAI-compatible convention; whether DeepSeek's server-side search is
        # requested exactly this way is not confirmed in this campaign, which is why the
        # tool name is configurable and the parser tolerates an unknown shape.
        arguments: dict[str, Any] = {"query": query, "top_k": top_k}
        if domains:
            arguments["domains"] = list(domains)
        return self._reduce(
            await self._post(
                {
                    "model": self.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Find authoritative primary sources for: " + query
                            ),
                        }
                    ],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": self.search_tool_name,
                                "parameters": arguments,
                            },
                        }
                    ],
                    "tool_choice": "auto",
                },
                timeout,
            )
        )


def transport_from_settings(
    cfg: "Settings | None" = None,
) -> HttpDeepSeekTransport | None:
    """The configured transport, or ``None`` when DeepSeek is not configured.

    ``None`` rather than an exception, matching ``get_llm_client``: an unconfigured
    provider is a degradation the caller handles, not a crash. That is what makes the
    whole DeepSeek path optional in practice as well as in principle.
    """
    if cfg is None:
        from app.core.config import settings as cfg  # noqa: PLW0127
    key = str(getattr(cfg, "deepseek_api_key", "") or "").strip()
    model = str(getattr(cfg, "deepseek_model", "") or "").strip()
    if not key or not model:
        return None
    return HttpDeepSeekTransport(
        api_key=key,
        model=model,
        base_url=str(getattr(cfg, "deepseek_base_url", "") or DEFAULT_BASE_URL),
        search_tool_name=str(
            getattr(cfg, "deepseek_search_tool_name", "") or DEFAULT_SEARCH_TOOL_NAME
        ),
    )


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_SEARCH_TOOL_NAME",
    "DeepSeekResponse",
    "DeepSeekTransport",
    "DeepSeekUnavailableError",
    "FakeDeepSeekTransport",
    "HttpDeepSeekTransport",
    "transport_from_settings",
]
