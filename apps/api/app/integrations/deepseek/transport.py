"""DeepSeek transport — V3.4 Slice 4.3.

One narrow seam between the platform and DeepSeek's HTTP API, so everything above it is
testable without a network and everything vendor-specific is in one file a reviewer can
read in full.

TWO ENDPOINTS, TWO CONTRACTS — BOTH VERIFIED LIVE
=================================================
DeepSeek serves **two different APIs with different capabilities**, and conflating them
is what produced this module's previous, wrong conclusion.

Measured 2026-09-06 (the model leg) and 2026-09-07 (the search leg).

``POST /chat/completions``
    OpenAI-compatible chat. **Strict**: an unknown ``tools[].type`` is a hard 400
    (*"unknown variant `web_search`, expected `function`"*). Used by :meth:`complete`.

``POST /responses``
    OpenAI-compatible Responses API. **Built-in server-side web search lives here and
    only here.** ``tools: [{"type": "web_search"}]`` is accepted, and the model really
    does search, open pages and read them. Used by :meth:`search`.

V3.11.1.1 probed only ``/chat/completions``, got the 400 above for every builtin
spelling, and concluded "DeepSeek has no server-side web search". The endpoint had no
such tool; the provider does. The lesson is recorded rather than quietly fixed: **an
absence measured on one endpoint is not an absence.**

``/responses`` IS PERMISSIVE, SO A 200 IS NOT A CAPABILITY
==========================================================
Unlike ``/chat/completions``, this endpoint **silently drops what it does not
understand** and still returns 200:

* ``tools: [{"type": "browser"}]`` → 200, ``tools`` echo is ``[]``, zero searches run.
* ``include: ["nonsense.value"]`` → 200.
* An unknown field inside an accepted tool → 200, field absent from the echo.

So the request being accepted proves nothing. Two things prove the search actually ran,
and :meth:`search` checks both: the response's ``tools`` **echo** contains the tool, and
the output carries ``web_search_call`` items. Anything else is reported as a warning
rather than read as an empty web.

WHAT THE SEARCH RESPONSE ACTUALLY CONTAINS, AND WHAT IT DOES NOT
================================================================
Measured over eight live runs:

* ``output[]`` items are ``reasoning``, ``web_search_call`` and ``message``.
* ``web_search_call.action.type`` is ``search`` (carrying ``queries[]`` and **no URLs**),
  ``open_page`` (carrying ``url``) or ``find_in_page`` (``url`` + ``pattern``). Item
  ``status`` is ``completed`` or ``failed`` — a failed open retrieved nothing.
* Every ``action.url`` carries a ``#ws_call_id=...`` fragment the provider appends. It is
  stripped here; leaving it on would make the same page look like a different URL to the
  fetcher, the corpus and the de-duplicator.
* **There are no structured citations.** ``message.content[].annotations`` was ``[]`` in
  every run, and ``include: ["web_search_call.action.sources"]`` is accepted and inert —
  no ``sources`` key ever appears. The retrieval trace is the only machine-readable
  record of where the model went.

TWO REQUEST CONTROLS ARE ACCEPTED AND DO NOTHING
================================================
Recorded because trusting either would be a silent, billable failure:

* ``max_tool_calls`` — echoed back as ``null``; a request sending ``1`` still made two
  search calls. **It does not bound spend.** One observed request made **eight** search
  calls and consumed ~41k tokens.

WHAT IS, AND IS NOT, BOUNDED
============================
``max_output_tokens`` and the timeout are honoured, and both are set here. Neither
bounds the larger half of the bill: **per-call input tokens are unbounded.** Every page
the model opens is fed back to it as input, so the ~41k-token request above sat behind
a 4,000-token *output* ceiling — over 90% of that spend was in a term no request
parameter touches.

Stated rather than glossed, because this module's own rule applies to its own
documentation: a bound that is not enforced is worse than no bound, since it gets
trusted. The effective ceiling on one search is the timeout; the ceiling on a *run*
belongs to ``ResearchBudget``, above this layer.
* ``filters.allowed_domains`` — dropped from the echo entirely, and off-domain pages were
  opened anyway. Domain restriction is therefore enforced **client-side**, in the
  provider, and is never described as a provider guarantee.

NOTHING HERE LOGS A PROMPT, A COMPLETION, AN ENDPOINT OR A CREDENTIAL
====================================================================
Same rule the existing clients follow, and for the same recorded reason: root-level INFO
logging once leaked an EODHD ``api_token`` in this repository.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

#: DeepSeek's documented API root. Overridable by configuration, never hardcoded at a
#: call site.
DEFAULT_BASE_URL = "https://api.deepseek.com"

#: Chat completions. Strict about tool types; carries :meth:`complete`.
CHAT_COMPLETIONS_PATH = "/chat/completions"

#: The Responses API. Permissive, and the **only** endpoint that serves built-in
#: server-side web search; carries :meth:`search`.
RESPONSES_PATH = "/responses"

#: The models `GET /models` actually served on 2026-09-06. `deepseek-chat` — the name
#: this adapter shipped with, taken from documentation — is NOT among them. It is
#: accepted on both endpoints and silently served as `deepseek-v4-flash`, so the
#: response's `model` field differs from the request's. Recorded because cost
#: attribution and reproducibility both depend on knowing which model actually ran.
SERVED_MODELS: frozenset[str] = frozenset(
    {"deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"}
)

#: The default model. A **served** name, not a documented one: an unserved name still
#: returns 200 while a different model answers, which makes cost attribution wrong and
#: a benchmark unreproducible without anything ever failing.
DEFAULT_MODEL = "deepseek-v4-flash"

#: The built-in web-search tool type, verified live on ``/responses``. Still
#: configurable — a vendor may rename a builtin — but no longer a guess.
DEFAULT_SEARCH_TOOL_NAME = "web_search"

#: The output-token ceiling for one search request. It bounds what the model WRITES, not
#: what it reads: pages it opens return as input tokens, which no request parameter
#: bounds. ``max_tool_calls`` is accepted and ignored, so the timeout is the only other
#: control the API honours.
DEFAULT_SEARCH_MAX_OUTPUT_TOKENS = 4000

#: The provider appends this to every URL it reports. Stripped so the same page is the
#: same URL to the fetcher, the corpus and the de-duplicator.
_WS_CALL_FRAGMENT = "#ws_call_id="

#: A hostname, and nothing that could carry an instruction. The domain preference is
#: interpolated into the model's own instructions, so it is validated rather than
#: trusted — cheap now, and the difference between safe and not on the day a domain list
#: is derived from model output.
_DOMAIN_SAFE_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")

#: A ceiling on how much caller-supplied text reaches the prompt.
_MAX_PROMPTED_DOMAINS = 20


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
    #: On ``/chat/completions``: the message's ``tool_calls``. On ``/responses``: the
    #: ``web_search_call`` items verbatim — the retrieval trace, which is the only
    #: machine-readable record of where the model actually went.
    tool_payloads: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    finish_reason: str | None = None
    #: The ``tools`` array the API echoes back. Load-bearing on ``/responses``, which
    #: accepts an unknown tool with a 200 and silently drops it: this is how a caller
    #: distinguishes "the web had nothing" from "the tool never ran".
    tools_echo: list[dict[str, Any]] = field(default_factory=list)
    #: The model that actually answered, which is not always the one requested.
    served_model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DeepSeekTransport(Protocol):
    """The seam. Everything above this is testable without a network."""

    model: str
    #: Declared because the provider reads it to check the ``tools`` echo against the
    #: tool actually requested. It was reached for with ``getattr`` on a concrete class
    #: while the Protocol did not declare it, so mypy could not check it and any other
    #: implementation silently fell back to the default name.
    search_tool_name: str

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

    model: str = DEFAULT_MODEL
    search_tool_name: str = DEFAULT_SEARCH_TOOL_NAME
    completion_text: str | None = '{"ok": true}'
    tool_payloads: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 120
    completion_tokens: int = 60
    cached_tokens: int = 0
    finish_reason: str | None = "stop"
    #: Defaults to the tool having been honoured, because that is the ordinary case. A
    #: test that wants the silent-drop path sets this to ``[]`` — which is exactly what
    #: the live API returns for a tool type it does not recognise.
    tools_echo: list[dict[str, Any]] = field(
        default_factory=lambda: [{"type": DEFAULT_SEARCH_TOOL_NAME}]
    )
    #: The prose a search response ends with. Separate from ``completion_text`` because
    #: the two endpoints return different things and one fake serves both.
    search_text: str | None = None
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
            text=self.search_text,
            tool_payloads=list(self.tool_payloads),
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cached_tokens=self.cached_tokens,
            finish_reason=self.finish_reason,
            tools_echo=list(self.tools_echo),
            served_model=self.model,
            raw={"fake": True},
        )


#: The API requires the word "json" to appear in the prompt whenever
#: ``response_format: json_object`` is set. Verified live 2026-09-06.
_JSON_LITERAL = "json"


def canonical_search_url(url: str | None) -> str:
    """A DeepSeek-reported URL reduced to the page it actually names.

    The provider appends ``#ws_call_id=<id>`` to every URL it reports. That fragment
    identifies *the tool call*, not the document, and leaving it attached would make one
    page look like N different URLs to the fetcher, the corpus and the de-duplicator —
    N cache misses, N stored copies, and a citation nobody can match to another run's.

    Only the provider's own fragment is removed. Any other fragment is part of the URL
    the model chose and is left alone.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    head, sep, _tail = text.partition(_WS_CALL_FRAGMENT)
    return head if sep else text


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
    #: One of the two bounds that actually work. ``max_tool_calls`` is accepted by the
    #: API and ignored by it, so this and the timeout are what stop a runaway search.
    search_max_output_tokens: int = DEFAULT_SEARCH_MAX_OUTPUT_TOKENS
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
        self, payload: dict[str, Any], timeout: int, path: str = CHAT_COMPLETIONS_PATH
    ) -> dict[str, Any]:  # pragma: no cover - needs a real key
        client = self._client(timeout)
        try:
            response = await client.post(path, json=payload)
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
            served_model=(str(body.get("model")) if body.get("model") else None),
            raw=body,
        )

    @staticmethod
    def _reduce_responses(body: dict[str, Any]) -> DeepSeekResponse:  # pragma: no cover
        """Reduce a ``/responses`` body. A different endpoint, a different shape.

        Three things are pulled out and nothing is interpreted here:

        * the ``web_search_call`` items, verbatim — the retrieval trace;
        * the ``tools`` echo, which is the only way to know the tool was honoured;
        * the final-answer prose and the usage block, whose field names differ from
          chat completions (``input_tokens``/``output_tokens``, not ``prompt``/
          ``completion``) — reading the wrong names would report every search as free.
        """
        output = body.get("output")
        items = [i for i in output if isinstance(i, dict)] if isinstance(output, list) else []

        texts: list[str] = []
        for item in items:
            if item.get("type") != "message":
                continue
            # Only the final answer. A `commentary` message is the model narrating its
            # own progress ("Let me verify that"), and treating it as an answer would
            # put process chatter into a research record.
            if str(item.get("phase") or "final_answer") != "final_answer":
                continue
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text = part.get("text")
                    if text:
                        texts.append(str(text))

        # `output_tokens_details.reasoning_tokens` is reported and deliberately not read
        # separately: it is a SUBSET of `output_tokens` (measured 212 of 654), so adding
        # it would double-count. Recorded here because "we did not look" and "it is
        # already included" are different statements about a cost figure.
        usage = body.get("usage") or {}
        in_details = usage.get("input_tokens_details") or {}
        tools_echo = body.get("tools")
        incomplete = body.get("incomplete_details") or {}

        return DeepSeekResponse(
            text="\n".join(texts) or None,
            tool_payloads=[i for i in items if i.get("type") == "web_search_call"],
            prompt_tokens=int(usage.get("input_tokens") or 0),
            completion_tokens=int(usage.get("output_tokens") or 0),
            cached_tokens=int(in_details.get("cached_tokens") or 0),
            # `incomplete` plus a reason is the truncation signal on this endpoint;
            # there is no `finish_reason` field.
            finish_reason=(
                str(incomplete.get("reason"))
                if incomplete.get("reason")
                else (str(body.get("status")) if body.get("status") else None)
            ),
            tools_echo=[t for t in tools_echo if isinstance(t, dict)]
            if isinstance(tools_echo, list)
            else [],
            served_model=(str(body.get("model")) if body.get("model") else None),
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

    #: The instruction that goes with a search request. It tells the model to search
    #: rather than recall — the distinction that matters, because this model's own
    #: knowledge cutoff is June 2024 and an unretrieved answer would still read fluently.
    SEARCH_INSTRUCTIONS = (
        "Use web search to answer. Do not answer from memory. "
        "Open the most authoritative pages you find, prefer the issuer's own "
        "publications and official filings, and state the source URL for anything you "
        "report. If the web does not tell you, say so instead of estimating."
    )

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        domains: Sequence[str] | None,
        timeout: int,
    ) -> DeepSeekResponse:  # pragma: no cover - needs a real key
        """One server-side web search, on ``/responses``. Verified live 2026-09-07.

        This method previously refused outright, on the strength of a probe that only
        ever asked ``/chat/completions`` — which serves no builtin tools at all. The
        capability exists; it lives on the other endpoint. See the module docstring.

        Two request controls that look like safety are **not** sent, because the live
        API accepts and ignores them, and a bound that is not enforced is worse than no
        bound — it gets trusted:

        * ``max_tool_calls`` — sending ``1`` still produced two search calls.
        * ``filters.allowed_domains`` — dropped from the echo; off-domain pages were
          opened anyway. ``domains`` is therefore passed to the model as a *preference*
          and enforced for real by the provider above, on the results.

        What bounds this call is ``max_output_tokens`` and ``timeout``, both set here —
        but only partly. **Input tokens are unbounded**: every page the model opens is
        fed back to it as input, which is where most of an observed ~41k-token request
        actually went. The timeout is the real ceiling on one call.

        ``top_k`` has **no wire equivalent** — the builtin tool takes no result count —
        so it is not sent, and the provider above applies it to the candidates instead.
        It stays in the signature because it is part of the transport Protocol and
        because a caller asking for five results should not have to know which of the
        two layers honours that.
        """
        wanted = self.search_tool_name or DEFAULT_SEARCH_TOOL_NAME
        instructions = self.SEARCH_INSTRUCTIONS
        if domains:
            # A preference, and named as one. The API drops `filters.allowed_domains`
            # silently, so the only real enforcement is client-side; saying so here
            # keeps the request honest about which of the two is load-bearing.
            # Bounded and character-restricted before it reaches the prompt. `domains`
            # is operator-supplied today; the day a domain list is derived from model or
            # user output, unsanitised interpolation here is a prompt-injection surface.
            safe = sorted(
                {
                    d
                    for d in (str(x).strip().lower() for x in domains)
                    if d and len(d) <= 253 and _DOMAIN_SAFE_RE.fullmatch(d)
                }
            )[:_MAX_PROMPTED_DOMAINS]
            if safe:
                instructions = (
                    f"{instructions} Prefer pages on these domains: {', '.join(safe)}."
                )
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": query,
            "tools": [{"type": wanted}],
            # Forced. Verified accepted as both {"type": <tool>} and "required"; the
            # explicit form says which tool, which matters the day a second one exists.
            "tool_choice": {"type": wanted},
            "max_output_tokens": max(256, int(self.search_max_output_tokens)),
        }
        return self._reduce_responses(await self._post(payload, timeout, RESPONSES_PATH))


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
        search_max_output_tokens=int(
            getattr(cfg, "deepseek_search_max_output_tokens", 0)
            or DEFAULT_SEARCH_MAX_OUTPUT_TOKENS
        ),
    )


__all__ = [
    "CHAT_COMPLETIONS_PATH",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_SEARCH_MAX_OUTPUT_TOKENS",
    "DEFAULT_SEARCH_TOOL_NAME",
    "RESPONSES_PATH",
    "SERVED_MODELS",
    "DeepSeekResponse",
    "DeepSeekTransport",
    "DeepSeekUnavailableError",
    "FakeDeepSeekTransport",
    "HttpDeepSeekTransport",
    "canonical_search_url",
    "transport_from_settings",
]
