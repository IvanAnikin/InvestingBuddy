"""Tavily web search adapter — open-web W1 (spec §8.2; provider evaluation §4).

``POST https://api.tavily.com/search`` with a bearer key. Dark: nothing constructs it
unless ``V3_WEB_SEARCH_PROVIDER=tavily`` and a key is configured, and nothing calls it
unless ``V3_WEB_SEARCH_ENABLED=true``.

WHAT IS SENT
============
``query``, ``max_results`` (0–20), ``search_depth`` (``basic`` by default),
``topic`` (``general``/``news``), ``start_date``/``end_date``, ``include_domains``
(≤300) and ``exclude_domains`` (≤150), ``country`` (mapped from ISO 3166 for the markets
listed below; ``general`` topic only) and ``include_usage=true``. ``include_answer`` and
``include_raw_content`` are explicitly ``false``: a vendor-written answer is not a
source, and full page text arrives through the platform's own fetcher, never through
the search vendor. ``language`` is not sent (not a verified parameter) and is recorded
as ``unsupported``.

WHAT COUNTS AS EXECUTED
=======================
A 2xx whose body parses as a JSON object with a ``results`` list **and** a
``request_id`` (spec §22.3). Anything else — 3xx (redirects are never followed), 401/403,
429, other 4xx, 5xx, a timeout, a transport error, an over-size body, unparseable JSON,
a missing request id — is ``executed=False`` with an error code. No retries in W1: a
retry is another billed call, and the budget counts every attempt.

THE KEY
=======
Held in a ``repr=False`` field, sent only in the ``Authorization`` header, and never
placed in a log line, an exception message or a stored row. Exceptions from ``httpx``
are caught and reduced to an error code; their text (which can include the request)
is never propagated.

EGRESS
======
Only ``https://api.tavily.com`` (fixed host allowlist). ``trust_env=False`` so a proxy
variable cannot redirect the call; ``follow_redirects=False``; bounded timeouts and a
bounded response size.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.structured_logging import log_event
from app.integrations.search.base import (
    apply_client_filters,
    enforcement_plan,
    govern_payload,
    normalise_result,
)
from app.services.providers.contracts import (
    FILTER_UNSUPPORTED,
    RESULT_STORAGE_FULL,
    SEARCH_ERROR_AUTH,
    SEARCH_ERROR_HOST_NOT_ALLOWED,
    SEARCH_ERROR_HTTP_3XX,
    SEARCH_ERROR_HTTP_4XX,
    SEARCH_ERROR_HTTP_5XX,
    SEARCH_ERROR_HTTP_429,
    SEARCH_ERROR_NO_KEY,
    SEARCH_ERROR_PARSE,
    SEARCH_ERROR_TIMEOUT,
    SEARCH_ERROR_TOO_LARGE,
    SEARCH_ERROR_TRANSPORT,
    FilterEnforcement,
    SearchCapabilities,
    SearchExecution,
    SearchRequest,
    SearchResultItem,
)
from app.services.providers.governance import ProviderGovernance

logger = logging.getLogger(__name__)

PROVIDER_NAME = "tavily"
DEFAULT_BASE_URL = "https://api.tavily.com"
#: The only host this adapter will ever call.
ALLOWED_HOSTS: frozenset[str] = frozenset({"api.tavily.com"})
SEARCH_PATH = "/search"
MAX_RESULTS = 20
MAX_INCLUDE_DOMAINS = 300
MAX_EXCLUDE_DOMAINS = 150
DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024

CAPABILITIES = SearchCapabilities(
    max_results=MAX_RESULTS,
    provider_filters=frozenset(
        {"include_domains", "exclude_domains", "date_range", "country", "topic"}
    ),
    max_include_domains=MAX_INCLUDE_DOMAINS,
    max_exclude_domains=MAX_EXCLUDE_DOMAINS,
    result_storage=RESULT_STORAGE_FULL,
)

#: ISO 3166-1 alpha-2 → Tavily's ``country`` value (lower-case English name). Only the
#: markets the research covers; any other code is recorded as ``unsupported`` and not
#: sent, rather than guessed.
COUNTRY_NAMES: Mapping[str, str] = {
    "US": "united states",
    "GB": "united kingdom",
    "DE": "germany",
    "FR": "france",
    "IT": "italy",
    "ES": "spain",
    "NL": "netherlands",
    "BE": "belgium",
    "CH": "switzerland",
    "AT": "austria",
    "SE": "sweden",
    "DK": "denmark",
    "NO": "norway",
    "FI": "finland",
    "IE": "ireland",
    "PL": "poland",
    "PT": "portugal",
    "AU": "australia",
    "NZ": "new zealand",
    "CA": "canada",
    "JP": "japan",
    "KR": "south korea",
    "SG": "singapore",
    "IN": "india",
}


class _ResponseTooLarge(Exception):
    pass


def base_url_allowed(base_url: str) -> bool:
    """``https://api.tavily.com`` (optionally with a trailing ``/``), nothing else."""
    try:
        parts = urlsplit((base_url or "").strip())
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and (parts.hostname or "").lower() in ALLOWED_HOSTS
        and parts.port in (None, 443)
        and not parts.username
        and not parts.password
        and parts.path in ("", "/")
        and not parts.query
        and not parts.fragment
    )


def build_body(
    request: SearchRequest, *, search_depth: str = "basic"
) -> tuple[dict[str, Any], dict[str, FilterEnforcement]]:
    """The JSON body for one request, and an enforcement-plan override for filters the
    vendor cannot take in this form (e.g. an unmapped country)."""
    overrides: dict[str, FilterEnforcement] = {}
    body: dict[str, Any] = {
        "query": request.query,
        "max_results": max(0, min(MAX_RESULTS, int(request.max_results))),
        "search_depth": search_depth if search_depth in ("basic", "advanced") else "basic",
        "topic": request.topic if request.topic in ("general", "news") else "general",
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "include_usage": True,
    }
    if request.date_from:
        body["start_date"] = request.date_from.isoformat()
    if request.date_to:
        body["end_date"] = request.date_to.isoformat()
    if request.include_domains:
        body["include_domains"] = [d.lower() for d in request.include_domains][
            :MAX_INCLUDE_DOMAINS
        ]
    if request.exclude_domains:
        body["exclude_domains"] = [d.lower() for d in request.exclude_domains][
            :MAX_EXCLUDE_DOMAINS
        ]
    if request.country:
        name = COUNTRY_NAMES.get(request.country.upper())
        if name and body["topic"] == "general":
            body["country"] = name
        else:
            overrides["country"] = FILTER_UNSUPPORTED
    return body, overrides


def _error_for_status(status: int) -> str:
    if status in (401, 403):
        return SEARCH_ERROR_AUTH
    if status == 429:
        return SEARCH_ERROR_HTTP_429
    if status >= 500:
        return SEARCH_ERROR_HTTP_5XX
    if 300 <= status < 400:
        return SEARCH_ERROR_HTTP_3XX
    return SEARCH_ERROR_HTTP_4XX


def parse_response(
    raw: bytes | str | Mapping[str, Any],
) -> tuple[str, list[SearchResultItem], dict[str, float], int] | None:
    """Parse a Tavily ``/search`` body. ``None`` when it is not a usable response.

    Returns ``(request_id, items, cost_units, malformed_item_count)``. Items are ranked
    in vendor order before any client filter. Also used by the fake provider, so the
    fixtures exercise the same normalisation as a live response.
    """
    if isinstance(raw, Mapping):
        data: Any = raw
    else:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError, UnicodeDecodeError):
            return None
    if not isinstance(data, Mapping):
        return None
    results = data.get("results")
    request_id = data.get("request_id")
    if not isinstance(results, list) or not isinstance(request_id, str) or not request_id:
        return None
    items: list[SearchResultItem] = []
    malformed = 0
    for entry in results:
        if not isinstance(entry, Mapping):
            malformed += 1
            continue
        item = normalise_result(
            len(items) + 1,
            url=entry.get("url"),
            title=entry.get("title"),
            snippet=entry.get("content"),
            published=entry.get("published_date"),
            score=entry.get("score"),
            metadata={"provider_rank": len(items) + malformed + 1},
        )
        if item is None:
            malformed += 1
            continue
        items.append(item)
    cost: dict[str, float] = {}
    usage = data.get("usage")
    if isinstance(usage, Mapping):
        credits = usage.get("credits")
        if isinstance(credits, (int, float)) and not isinstance(credits, bool) and credits >= 0:
            cost["tavily_credits"] = float(credits)
    return request_id[:120], items, cost, malformed


async def _read_bounded(response: httpx.Response, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise _ResponseTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


@dataclass
class TavilySearchProvider:
    """The Tavily adapter. Implements ``SearchProvider``; never raises for a failure."""

    api_key: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    search_depth: str = "basic"
    timeout_seconds: float = 15.0
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    #: Tests inject ``httpx.MockTransport``; ``None`` is the real network.
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)
    governance: ProviderGovernance | None = field(default=None, repr=False)
    name: str = PROVIDER_NAME
    capabilities: SearchCapabilities = CAPABILITIES

    @property
    def is_configured(self) -> bool:
        return bool((self.api_key or "").strip())

    def _client(self) -> httpx.AsyncClient:
        timeout = httpx.Timeout(
            self.timeout_seconds, connect=min(5.0, self.timeout_seconds)
        )
        return httpx.AsyncClient(
            transport=self.transport,
            trust_env=False,
            follow_redirects=False,
            timeout=timeout,
        )

    async def search(
        self, request: SearchRequest
    ) -> tuple[SearchExecution, list[SearchResultItem]]:
        plan = enforcement_plan(request, self.capabilities)

        def failed(
            code: str, *, status: int | None = None, latency: int = 0, calls: int = 0
        ) -> tuple[SearchExecution, list[SearchResultItem]]:
            return (
                SearchExecution.not_executed(
                    self.name,
                    code,
                    http_status=status,
                    latency_ms=latency,
                    network_call_count=calls,
                    filters_enforced_by=plan,
                ),
                [],
            )

        if not self.is_configured:
            return failed(SEARCH_ERROR_NO_KEY)
        if not base_url_allowed(self.base_url):
            return failed(SEARCH_ERROR_HOST_NOT_ALLOWED)

        body, overrides = build_body(request, search_depth=self.search_depth)
        plan.update(overrides)
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False)
        refusal = govern_payload(self.name, payload, self.governance)
        if refusal is not None:
            return failed(refusal)

        url = self.base_url.rstrip("/") + SEARCH_PATH
        headers = {
            "Authorization": "Bearer " + self.api_key.strip(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        started = time.monotonic()

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        status: int | None = None
        raw = b""
        try:
            async with asyncio.timeout(self.timeout_seconds + 5.0):
                async with self._client() as client:
                    async with client.stream(
                        "POST", url, content=payload.encode("utf-8"), headers=headers
                    ) as response:
                        status = response.status_code
                        if not 200 <= status < 300:
                            return failed(
                                _error_for_status(status),
                                status=status,
                                latency=elapsed(),
                                calls=1,
                            )
                        raw = await _read_bounded(response, self.max_response_bytes)
        except (TimeoutError, httpx.TimeoutException):
            return failed(SEARCH_ERROR_TIMEOUT, status=status, latency=elapsed(), calls=1)
        except _ResponseTooLarge:
            return failed(SEARCH_ERROR_TOO_LARGE, status=status, latency=elapsed(), calls=1)
        except httpx.HTTPError as exc:
            log_event(
                logger,
                "web_search_transport_error",
                level=logging.WARNING,
                provider=self.name,
                error_type=type(exc).__name__,
            )
            return failed(SEARCH_ERROR_TRANSPORT, status=status, latency=elapsed(), calls=1)

        latency = elapsed()
        parsed = parse_response(raw)
        if parsed is None:
            return failed(SEARCH_ERROR_PARSE, status=status, latency=latency, calls=1)
        request_id, items, cost, _malformed = parsed
        kept, removed = apply_client_filters(request, items)
        return (
            SearchExecution(
                provider=self.name,
                executed=True,
                provider_request_id=request_id,
                http_status=status,
                latency_ms=latency,
                result_count=len(kept),
                cost_units=cost,
                error_code=None,
                filters_enforced_by=plan,
                network_call_count=1,
                client_filtered_count=removed,
            ),
            kept,
        )


__all__ = [
    "ALLOWED_HOSTS",
    "CAPABILITIES",
    "COUNTRY_NAMES",
    "DEFAULT_BASE_URL",
    "PROVIDER_NAME",
    "TavilySearchProvider",
    "base_url_allowed",
    "build_body",
    "parse_response",
]
