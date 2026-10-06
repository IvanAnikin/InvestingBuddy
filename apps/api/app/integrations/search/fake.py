"""The web search test double — open-web W1 (spec §8.2; acceptance plan §3).

Serves recorded (for now: synthetic) Tavily-shaped fixtures keyed by the normalised
query, and runs them through the **same** parser and client-side filters as the real
adapter, so a fixture test exercises the live normalisation path. It is deterministic,
opens no socket, and can misbehave on purpose: an outage, a 429, an auth failure, a
timeout, an unparseable body, or an empty answer.

It still goes through governance (rule G4) exactly as a real adapter does, so a test of
a refusal is a test of the real check rather than of a bypass.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.integrations.search.base import (
    apply_client_filters,
    enforcement_plan,
    finalize_enforcement,
    govern_payload,
)
from app.integrations.search.tavily import parse_response
from app.services.providers.contracts import (
    RESULT_STORAGE_FULL,
    SEARCH_ERROR_AUTH,
    SEARCH_ERROR_HTTP_5XX,
    SEARCH_ERROR_HTTP_429,
    SEARCH_ERROR_PARSE,
    SEARCH_ERROR_TIMEOUT,
    SearchCapabilities,
    SearchExecution,
    SearchRequest,
    SearchResultItem,
)
from app.services.providers.governance import ProviderGovernance

PROVIDER_NAME = "fake_web_search"

MODE_OK = "ok"
MODE_EMPTY = "empty"
MODE_OUTAGE = "outage"
MODE_HTTP_429 = "http_429"
MODE_AUTH = "auth"
MODE_TIMEOUT = "timeout"
MODE_PARSE = "parse"
MODES: frozenset[str] = frozenset(
    {MODE_OK, MODE_EMPTY, MODE_OUTAGE, MODE_HTTP_429, MODE_AUTH, MODE_TIMEOUT, MODE_PARSE}
)

#: The fake enforces no filter itself: everything checkable is enforced client-side and
#: everything else is recorded as unsupported, which is the honest description.
CAPABILITIES = SearchCapabilities(
    max_results=20,
    provider_filters=frozenset(),
    max_include_domains=300,
    max_exclude_domains=150,
    result_storage=RESULT_STORAGE_FULL,
)

#: ``apps/api/tests/fixtures/web`` — only present in a source checkout.
DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "web"


def fixture_key(query: str) -> str:
    """The key a fixture is served under: whitespace-collapsed and case-folded."""
    return " ".join((query or "").split()).casefold()


@dataclass
class FakeWebSearchProvider:
    """Deterministic ``SearchProvider``. Records every request it was asked."""

    fixtures: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    mode: str = MODE_OK
    #: Per-query overrides of ``mode``, keyed by :func:`fixture_key`.
    mode_by_query: dict[str, str] = field(default_factory=dict)
    governance: ProviderGovernance | None = field(default=None, repr=False)
    name: str = PROVIDER_NAME
    capabilities: SearchCapabilities = CAPABILITIES
    is_configured: bool = True
    requests: list[SearchRequest] = field(default_factory=list)

    @classmethod
    def from_fixture_dir(
        cls, directory: Path | None = None, **kwargs: Any
    ) -> "FakeWebSearchProvider":
        """Load every ``tavily_*.json`` that names its ``_fixture_query``."""
        fixtures: dict[str, Mapping[str, Any]] = {}
        root = directory or DEFAULT_FIXTURE_DIR
        if root.is_dir():
            for path in sorted(root.glob("tavily_*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                query = data.get("_fixture_query") if isinstance(data, dict) else None
                if isinstance(query, str) and query.strip():
                    fixtures[fixture_key(query)] = data
        return cls(fixtures=fixtures, **kwargs)

    @property
    def network_call_count(self) -> int:
        """Simulated provider calls (requests that passed governance)."""
        return len(self.requests)

    async def search(
        self, request: SearchRequest
    ) -> tuple[SearchExecution, list[SearchResultItem]]:
        plan = enforcement_plan(request, self.capabilities)
        payload = json.dumps(
            {"query": request.query, "filters": request.filters()}, sort_keys=True
        )
        refusal = govern_payload(self.name, payload, self.governance)
        if refusal is not None:
            return SearchExecution.not_executed(self.name, refusal, filters_enforced_by=plan), []

        self.requests.append(request)
        key = fixture_key(request.query)
        mode = self.mode_by_query.get(key, self.mode)
        failures = {
            MODE_OUTAGE: (SEARCH_ERROR_HTTP_5XX, 503),
            MODE_HTTP_429: (SEARCH_ERROR_HTTP_429, 429),
            MODE_AUTH: (SEARCH_ERROR_AUTH, 401),
            MODE_TIMEOUT: (SEARCH_ERROR_TIMEOUT, None),
            MODE_PARSE: (SEARCH_ERROR_PARSE, 200),
        }
        if mode in failures:
            code, status = failures[mode]
            return (
                SearchExecution.not_executed(
                    self.name,
                    code,
                    http_status=status,
                    latency_ms=1,
                    network_call_count=1,
                    filters_enforced_by=plan,
                ),
                [],
            )

        fixture = self.fixtures.get(key) if mode == MODE_OK else None
        if fixture is None:
            fixture = {
                "results": [],
                "request_id": "fake-" + hashlib.sha256(key.encode()).hexdigest()[:16],
            }
        parsed = parse_response(fixture)
        if parsed is None:
            return (
                SearchExecution.not_executed(
                    self.name,
                    SEARCH_ERROR_PARSE,
                    http_status=200,
                    latency_ms=1,
                    network_call_count=1,
                    filters_enforced_by=plan,
                ),
                [],
            )
        # The fixture's ``usage`` block is Tavily's, not the fake's: the fake bills
        # nothing, and reports no cost unit rather than a zero (absent is not zero).
        request_id, items, _cost, _malformed = parsed
        kept, removed, unchecked = apply_client_filters(request, items)
        finalize_enforcement(plan, self.capabilities, unchecked)
        return (
            SearchExecution(
                provider=self.name,
                executed=True,
                provider_request_id=request_id,
                http_status=200,
                latency_ms=1,
                result_count=len(kept),
                cost_units={},
                error_code=None,
                filters_enforced_by=plan,
                network_call_count=1,
                client_filtered_count=removed,
                date_unchecked_count=unchecked,
            ),
            kept,
        )


__all__ = [
    "CAPABILITIES",
    "DEFAULT_FIXTURE_DIR",
    "MODES",
    "MODE_AUTH",
    "MODE_EMPTY",
    "MODE_HTTP_429",
    "MODE_OK",
    "MODE_OUTAGE",
    "MODE_PARSE",
    "MODE_TIMEOUT",
    "PROVIDER_NAME",
    "FakeWebSearchProvider",
    "fixture_key",
]
