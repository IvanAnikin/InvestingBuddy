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

#: The models `GET /models` actually served on 2026-09-06. `deepseek-chat` — the name
#: this adapter shipped with, taken from documentation — is NOT among them. It is
#: accepted and silently served as `deepseek-v4-flash`, so the response's `model` field
#: differs from the request's. Recorded because cost attribution and reproducibility both
#: depend on knowing which model actually ran.
SERVED_MODELS: frozenset[str] = frozenset(
    {"deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"}
)

#: The tool name used for server-side web search. Configurable precisely because the
#: exact contract is unverified — see the module docstring.
DEFAULT_SEARCH_TOOL_NAME = "web_search"


#: HTTP statuses a retry cannot fix. A wrong key will not become right by being asked
#: again, and a malformed request will not become well-formed — so retrying either is
#: three times the spend for the same failure. 408 and 429 are deliberately ABSENT:
#: a timeout and a rate limit are exactly the cases a retry exists for.
PERMANENT_HTTP_STATUSES: frozenset[int] = frozenset({400, 401, 403, 404, 405, 422})


class DeepSeekUnavailableError(RuntimeError):
    """DeepSeek cannot be reached, is not configured, or refused the request.

    ``job_transient`` decides whether the durable worker may retry, and it is set **per
    raise** rather than per class. The class attribute is the default for a connection
    failure — the case a retry exists for — and an authentication or request error
    overrides it to ``False``.

    This was a defect until V3.10.1: the attribute was an unconditional class-level
    ``True``, so a 401 was retried to the attempt limit. That contradicted this docstring,
    which already said "a missing key is permanent", and it is the shape of bug that only
    costs money once it is in front of a real credential.
    """

    #: Default: a connection failure, which a retry may well fix.
    job_transient = True

    def __init__(self, *args: Any, transient: bool = True) -> None:
        super().__init__(*args)
        self.job_transient = transient


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
    ) -> DeepSeekResponse: ...  # pragma: no cover - protocol

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        domains: Sequence[str] | None,
        timeout: int,
    ) -> DeepSeekResponse: ...  # pragma: no cover - protocol


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


#: The API requires the word "json" to appear in the prompt whenever
#: ``response_format: json_object`` is set. Verified live 2026-09-06.
_JSON_LITERAL = "json"


def _json_hinted(system: str) -> str:
    """``system`` guaranteed to contain the literal the API demands."""
    if _JSON_LITERAL in (system or "").lower():
        return system
    suffix = "Respond with a single valid json object."
    return f"{system}\n{suffix}" if system else suffix


@dataclass
class HttpDeepSeekTransport:
    """The real transport. **Never constructed by the unit suite.**

    Deliberately small: an OpenAI-compatible POST, a usage block, and a tool-call
    payload. Everything interpretive lives in the providers, so the part of this slice
    that is unverified against the live API is a handful of lines rather than a layer.
    """

    #: ``repr=False`` is load-bearing. A dataclass repr prints every field, and this
    #: object appears in pytest failure output, exception chains and any log line that
    #: formats it — so the default repr published the live API key the first time the
    #: contract test failed. Redaction belongs on the type, not on each call site that
    #: might format it.
    api_key: str = field(repr=False)
    model: str = ""
    base_url: str = DEFAULT_BASE_URL
    search_tool_name: str = DEFAULT_SEARCH_TOOL_NAME
    #: Injected so the SDK import stays lazy and a test can supply a stub without a
    #: network — the same pattern the corpus artifact store uses for its Azure client.
    client_factory: Any = None

    def _client(self, timeout: int) -> Any:  # pragma: no cover - needs a real key
        if self.client_factory is not None:
            return self.client_factory(timeout=timeout)
        if not self.api_key:
            # Otherwise httpx raises LocalProtocolError on the illegal header
            # `Bearer ` — a confusing client-side failure for a plain misconfiguration.
            raise DeepSeekUnavailableError("No DeepSeek API key is configured.", transient=False)
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
                    f"DeepSeek returned HTTP {response.status_code}.",
                    transient=response.status_code not in PERMANENT_HTTP_STATUSES,
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
            # Not transient: a provider returning the wrong content type is returning it
            # again in four seconds.
            raise DeepSeekUnavailableError(
                "DeepSeek returned a non-object response.", transient=False
            )
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
        json_mode: bool = False,
    ) -> DeepSeekResponse:  # pragma: no cover - needs a real key
        """One chat completion.

        ``json_mode`` is OPT-IN. This adapter previously sent
        ``response_format: json_object`` on **every** call, which the live API rejects:

            HTTP 400 — "Prompt must contain the word 'json' in some form to use
            'response_format' of type 'json_object'."

        So every ordinary completion failed. When JSON is genuinely wanted the literal
        the API demands is guaranteed here rather than left to each caller's prompt
        wording, because a caller who omits it gets a 400 instead of prose.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _json_hinted(system) if json_mode else system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return self._reduce(await self._post(payload, timeout))

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        domains: Sequence[str] | None,
        timeout: int,
    ) -> DeepSeekResponse:
        """**DeepSeek has no server-side web search.** Verified live, 2026-09-06.

        This adapter shipped assuming it did — that was the premise for designating
        DeepSeek the primary external research runtime. The live API disproves it:

        * ``tools[0].type`` accepts **only** ``"function"``. Every builtin spelling —
          ``web_search``, ``web_search_preview``, ``search``, ``browser``,
          ``retrieval`` — is rejected with *"unknown variant"*.
        * A ``function`` tool named ``web_search`` merely makes the model **ask the
          caller** to run a search and hand back results. It cannot retrieve anything
          itself.
        * Asked directly, the model reports no live browsing and a **June 2024**
          knowledge cutoff.

        So a "search" here could only return the model's *recollection* dressed as
        retrieval, with URLs it composed from memory. That is precisely the failure the
        ``ResearchLead`` promotion path exists to catch — and generating such leads on
        purpose, at cost, to have them rejected downstream is worse than not searching.

        Refusing is the honest implementation. The caller gets a clear, permanent error
        instead of plausible fabrications; the general-web leg belongs to the safe
        fetcher and bounded issuer traversal, which retrieve documents that exist.
        """
        raise DeepSeekUnavailableError(
            "DeepSeek exposes no server-side web search: 'function' is the only "
            "accepted tool type and the model has no live browsing. Use the safe "
            "fetcher or issuer traversal for general-web retrieval.",
            transient=False,
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
