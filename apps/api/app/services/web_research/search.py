"""The web search orchestrator — open-web W1 (spec §8, §15.1, §18, §19, §22.3, §23).

``run_searches(session, requests, context)`` is the one entry point that sends queries
to a web search provider. For every request, in order:

1. **Gate** — :func:`~app.services.web_research.queries.validate_outgoing_query`
   (operators, URLs, length, rule G1 private tokens, blocklist). Refused → a row with
   the refusal code and **no query text** when the refusal was a private token.
2. **Governance** (rule G4, spec §8.3) — ``ProviderGovernance.assert_permitted(provider,
   "public_web")`` and ``assert_no_credentials`` on the query and its filters. This is
   the first runtime call site the governance matrix has; the adapter repeats both
   checks on the exact wire payload.
3. **Cache** (spec §18) — when the provider's ``result_storage`` is ``full``, an
   executed call for the same ``(provider, request_hash)`` in today's UTC date bucket
   (≤24h) is re-served from its rows with zero network calls.
4. **Budget** (spec §19) — the run's query cap, wall time and the platform daily cap.
   Reserved before the call; a failed call keeps its reservation.
5. **Call** — at most ``concurrency`` (4) provider calls in flight. The adapter never
   raises by contract; if it does anyway, the error is recorded, not propagated.

Every outcome — executed, failed, refused, cached — is persisted as a
``web_search_queries`` row with its results, so a run can be reconstructed from rows
(spec §22.2). Rows are flushed, not committed: the caller owns the transaction.

STATES (spec §23.1)
===================
* ``web_search_disabled`` — flag off, or provider ``none``. Nothing is written.
* ``web_search_unavailable`` — at least one query was requested and none executed (no
  key, auth failure, outage, or every query refused). Never masked by recall.
* ``web_search_degraded`` — some executed, some did not (failure, budget, refusal).
* ``ok`` — every query executed or was served from cache.

Logs carry counts, codes and a 12-character request-hash prefix. Never query text, never
result text, never the key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa

from app.core.structured_logging import log_event
from app.integrations.search import web_search_provider_from_settings
from app.models.web_research import WebSearchQuery, WebSearchResult
from app.services.consumption import ConsumptionUnits
from app.services.corpus.policy import ACCESS_PUBLIC_WEB
from app.services.providers.contracts import (
    RESULT_STORAGE_FULL,
    RESULT_STORAGE_TRANSIENT,
    RESULT_STORAGE_URL_ONLY,
    SEARCH_ERROR_ADAPTER,
    SEARCH_ERROR_CREDENTIAL,
    SEARCH_ERROR_GOVERNANCE,
    SearchExecution,
    SearchProvider,
    SearchRequest,
    SearchResultItem,
)
from app.services.providers.governance import (
    CredentialInPayloadError,
    ProviderGovernance,
    ProviderNotPermittedError,
    assert_no_credentials,
    default_governance,
)
from app.services.web_research.budget import (
    WebResearchBudget,
    budget_for_run,
    utc_day_start,
)
from app.services.web_research.queries import (
    REFUSAL_PRIVATE_TOKEN,
    validate_outgoing_query,
)

logger = logging.getLogger(__name__)

STATE_DISABLED = "web_search_disabled"
STATE_UNAVAILABLE = "web_search_unavailable"
STATE_DEGRADED = "web_search_degraded"
STATE_OK = "ok"
SEARCH_STATES: frozenset[str] = frozenset(
    {STATE_DISABLED, STATE_UNAVAILABLE, STATE_DEGRADED, STATE_OK}
)

DEFAULT_CONCURRENCY = 4
CACHE_TTL = timedelta(hours=24)
DISPOSITION_CANDIDATE = "candidate"
DISPOSITION_SKIPPED = "skipped"

#: Refusals whose query text must never be stored: it carried private data or a secret.
_WITHHOLD_TEXT: frozenset[str] = frozenset({REFUSAL_PRIVATE_TOKEN, SEARCH_ERROR_CREDENTIAL})

_UNSET: Any = object()


@dataclass(frozen=True)
class SearchContext:
    """Lineage and per-run guards for one batch of searches."""

    research_job_id: uuid.UUID | None = None
    discovery_run_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    stage: str | None = None
    #: Rule G1: the run's private tokens (portfolio, uploads, identity).
    private_tokens: frozenset[str] = frozenset()
    #: A budget the caller already holds; otherwise one is built from ``budget_profile``.
    budget: WebResearchBudget | None = None
    budget_profile: str = "company_standard"


@dataclass
class QueryOutcome:
    request: SearchRequest
    execution: SearchExecution
    #: Results admitted under the run's result budget, in rank order.
    results: list[SearchResultItem] = field(default_factory=list)
    query_id: uuid.UUID | None = None

    @property
    def from_cache(self) -> bool:
        return self.execution.cached_from is not None


@dataclass
class SearchRunResult:
    state: str
    provider: str | None
    outcomes: list[QueryOutcome] = field(default_factory=list)
    consumption: ConsumptionUnits = field(default_factory=ConsumptionUnits)

    @property
    def requested(self) -> int:
        return len(self.outcomes)

    @property
    def executed(self) -> int:
        return sum(1 for o in self.outcomes if o.execution.executed)

    @property
    def network_call_count(self) -> int:
        return sum(o.execution.network_call_count for o in self.outcomes)

    def summary(self) -> dict[str, Any]:
        """Counts and codes only — safe to log and to show."""
        errors: dict[str, int] = {}
        for o in self.outcomes:
            if o.execution.error_code:
                errors[o.execution.error_code] = errors.get(o.execution.error_code, 0) + 1
        return {
            "state": self.state,
            "provider": self.provider,
            "requested": self.requested,
            "executed": self.executed,
            "from_cache": sum(1 for o in self.outcomes if o.from_cache),
            "network_call_count": self.network_call_count,
            "errors": errors,
            "results": sum(len(o.results) for o in self.outcomes),
        }


def _state_for(outcomes: Sequence[QueryOutcome]) -> str:
    if not outcomes:
        return STATE_OK
    executed = sum(1 for o in outcomes if o.execution.executed)
    if executed == 0:
        return STATE_UNAVAILABLE
    if executed < len(outcomes):
        return STATE_DEGRADED
    return STATE_OK


def _governance_refusal(
    governance: ProviderGovernance, provider: str, request: SearchRequest
) -> str | None:
    try:
        governance.assert_permitted(provider, ACCESS_PUBLIC_WEB)
    except ProviderNotPermittedError:
        return SEARCH_ERROR_GOVERNANCE
    try:
        assert_no_credentials(
            request.query + " " + json.dumps(request.filters(), sort_keys=True)
        )
    except CredentialInPayloadError:
        return SEARCH_ERROR_CREDENTIAL
    return None


async def _cached(
    session: Any, provider: SearchProvider, request: SearchRequest, now: datetime
) -> tuple[SearchExecution, list[SearchResultItem]] | None:
    """An executed network call for the same question today, re-served from its rows."""
    if provider.capabilities.result_storage != RESULT_STORAGE_FULL:
        return None
    since = max(utc_day_start(now), now - CACHE_TTL)
    row = (
        await session.execute(
            sa.select(WebSearchQuery)
            .where(
                WebSearchQuery.provider == provider.name,
                WebSearchQuery.request_hash == request.request_hash(),
                WebSearchQuery.executed.is_(True),
                WebSearchQuery.network_call_count > 0,
                WebSearchQuery.created_at >= since,
            )
            .order_by(WebSearchQuery.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    result_rows = (
        await session.execute(
            sa.select(WebSearchResult)
            .where(WebSearchResult.query_id == row.id)
            .order_by(WebSearchResult.rank)
        )
    ).scalars().all()
    items = [
        SearchResultItem(
            rank=r.rank,
            url=r.url,
            canonical_url=r.canonical_url or r.url,
            domain=r.domain or "",
            title=r.title,
            snippet=r.snippet,
            published_hint=r.published_hint,
            language_hint=r.language_hint,
            provider_score=r.provider_score,
            metadata={},
        )
        for r in result_rows
    ]
    filters = row.filters_json if isinstance(row.filters_json, dict) else {}
    execution = SearchExecution(
        provider=provider.name,
        executed=True,
        provider_request_id=row.provider_request_id,
        http_status=row.http_status,
        latency_ms=0,
        result_count=len(items),
        cost_units={},
        error_code=None,
        filters_enforced_by=dict(filters.get("enforced_by") or {}),
        network_call_count=0,
        client_filtered_count=int(filters.get("client_filtered_count") or 0),
        cached_from=str(row.id),
    )
    return execution, items


async def _call(
    provider: SearchProvider, request: SearchRequest, semaphore: asyncio.Semaphore
) -> tuple[SearchExecution, list[SearchResultItem]]:
    async with semaphore:
        try:
            return await provider.search(request)
        except Exception as exc:  # noqa: BLE001 - an adapter must not take the run down
            log_event(
                logger,
                "web_search_adapter_error",
                level=logging.WARNING,
                provider=provider.name,
                error_type=type(exc).__name__,
            )
            return SearchExecution.not_executed(provider.name, SEARCH_ERROR_ADAPTER), []


def _persist(
    session: Any,
    outcome: QueryOutcome,
    context: SearchContext,
    budget: WebResearchBudget,
    storage: str,
) -> None:
    request, execution = outcome.request, outcome.execution
    code = execution.error_code
    withhold = code in _WITHHOLD_TEXT
    filters: dict[str, Any] = {
        "requested": request.filters(),
        "enforced_by": dict(execution.filters_enforced_by),
        "client_filtered_count": execution.client_filtered_count,
    }
    if execution.cached_from:
        filters["served_from_query_id"] = execution.cached_from
    row = WebSearchQuery(
        id=uuid.uuid4(),
        research_job_id=context.research_job_id,
        discovery_run_id=context.discovery_run_id,
        agent_run_id=context.agent_run_id,
        company_id=context.company_id,
        stage=context.stage,
        family=request.family.value,
        origin=request.origin,
        template_version=request.template_version,
        query_text=f"[withheld: {code}]" if withhold else request.query,
        # A hash of withheld text is still derived from it: a dictionary of candidate
        # tickers would recover the private token. Withheld means withheld.
        request_hash=f"withheld:{code}"[:64] if withhold else request.request_hash(),
        filters_json=None if withhold else filters,
        provider=execution.provider,
        executed=execution.executed,
        provider_request_id=execution.provider_request_id,
        http_status=execution.http_status,
        network_call_count=execution.network_call_count,
        latency_ms=execution.latency_ms,
        result_count=execution.result_count,
        cost_units_json=dict(execution.cost_units),
        error_code=code,
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    outcome.query_id = row.id

    returned = outcome.results
    admitted = budget.record_results(len(returned))
    outcome.results = returned[:admitted]
    if storage == RESULT_STORAGE_TRANSIENT:
        return
    keep_text = storage == RESULT_STORAGE_FULL
    for index, item in enumerate(returned):
        over_budget = index >= admitted
        session.add(
            WebSearchResult(
                id=uuid.uuid4(),
                query_id=row.id,
                rank=item.rank,
                url=item.url,
                canonical_url=item.canonical_url,
                domain=item.domain or None,
                title=item.title if keep_text else None,
                snippet=item.snippet if keep_text else None,
                published_hint=item.published_hint,
                language_hint=item.language_hint,
                provider_score=item.provider_score,
                disposition=DISPOSITION_SKIPPED if over_budget else DISPOSITION_CANDIDATE,
                disposition_reason="budget:max_results" if over_budget else None,
                created_at=datetime.now(timezone.utc),
            )
        )


def _consumption(provider: SearchProvider, outcomes: Sequence[QueryOutcome]) -> ConsumptionUnits:
    """Units for the run record. Credits are instrumented only when every executed
    network call reported them — one unreported call makes the credit total unknown."""
    calls = sum(o.execution.network_call_count for o in outcomes)
    credits = 0.0
    all_reported = True
    for o in outcomes:
        ex = o.execution
        if not ex.executed or ex.network_call_count == 0:
            continue
        value = ex.cost_units.get("tavily_credits")
        if value is None:
            all_reported = False
        else:
            credits += float(value)
    instrumented = {"web_search_calls"}
    if provider.name == "tavily" and all_reported:
        instrumented.add("tavily_credits")
    return ConsumptionUnits(
        web_search_calls=calls,
        tavily_credits=credits if "tavily_credits" in instrumented else 0.0,
        instrumented=frozenset(instrumented),
    )


async def run_searches(
    session: Any,
    requests: Sequence[SearchRequest],
    context: SearchContext,
    *,
    provider: SearchProvider | None = _UNSET,
    cfg: Any | None = None,
    governance: ProviderGovernance | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    now: datetime | None = None,
) -> SearchRunResult:
    """Run a batch of searches with full provenance. See the module docstring."""
    if cfg is None:
        from app.core.config import settings as cfg  # noqa: PLW0127
    if not bool(getattr(cfg, "v3_web_search_enabled", False)):
        return SearchRunResult(state=STATE_DISABLED, provider=None)
    chosen = web_search_provider_from_settings(cfg) if provider is _UNSET else provider
    if chosen is None:
        return SearchRunResult(state=STATE_DISABLED, provider=None)

    gov = governance or default_governance()
    moment = now or datetime.now(timezone.utc)
    budget = context.budget or await budget_for_run(
        session, context.budget_profile, cfg=cfg, now=moment
    )

    # An unconfigured provider (no key, unknown name) answers without a network call, so
    # asking it spends no budget: the refusal it returns is the record.
    configured = bool(getattr(chosen, "is_configured", True))
    outcomes: list[QueryOutcome] = []
    pending: list[QueryOutcome] = []
    for request in requests:
        refusal = validate_outgoing_query(request.query, context.private_tokens)
        if refusal is None:
            refusal = _governance_refusal(gov, chosen.name, request)
        if refusal is not None:
            outcomes.append(
                QueryOutcome(request, SearchExecution.not_executed(chosen.name, refusal))
            )
            continue
        cached = await _cached(session, chosen, request, moment)
        if cached is not None:
            outcomes.append(QueryOutcome(request, cached[0], list(cached[1])))
            continue
        refusal = budget.reserve_query() if configured else None
        if refusal is not None:
            outcomes.append(
                QueryOutcome(request, SearchExecution.not_executed(chosen.name, refusal))
            )
            continue
        # Placeholder execution, replaced below once the call returns.
        outcome = QueryOutcome(
            request, SearchExecution.not_executed(chosen.name, SEARCH_ERROR_ADAPTER)
        )
        outcomes.append(outcome)
        pending.append(outcome)

    if pending:
        semaphore = asyncio.Semaphore(max(1, int(concurrency)))
        answers = await asyncio.gather(*(_call(chosen, o.request, semaphore) for o in pending))
        for outcome, (execution, items) in zip(pending, answers, strict=True):
            outcome.execution = execution
            outcome.results = list(items) if execution.executed else []

    storage = chosen.capabilities.result_storage
    if storage not in (RESULT_STORAGE_FULL, RESULT_STORAGE_URL_ONLY, RESULT_STORAGE_TRANSIENT):
        storage = RESULT_STORAGE_TRANSIENT
    for outcome in outcomes:
        _persist(session, outcome, context, budget, storage)
        ex = outcome.execution
        log_event(
            logger,
            "web_search_query",
            provider=ex.provider,
            family=outcome.request.family.value,
            origin=outcome.request.origin,
            executed=ex.executed,
            from_cache=outcome.from_cache,
            error_code=ex.error_code,
            http_status=ex.http_status,
            latency_ms=ex.latency_ms,
            result_count=ex.result_count,
            network_call_count=ex.network_call_count,
            request_hash=outcome.request.request_hash()[:12],
        )
    await session.flush()

    result = SearchRunResult(
        state=_state_for(outcomes),
        provider=chosen.name,
        outcomes=outcomes,
        consumption=_consumption(chosen, outcomes),
    )
    summary = result.summary()
    log_event(
        logger,
        "web_search_run_completed",
        state=summary["state"],
        provider=summary["provider"],
        requested=summary["requested"],
        executed=summary["executed"],
        from_cache=summary["from_cache"],
        network_call_count=summary["network_call_count"],
        research_job_id=context.research_job_id,
        discovery_run_id=context.discovery_run_id,
    )
    return result


__all__ = [
    "SEARCH_STATES",
    "STATE_DEGRADED",
    "STATE_DISABLED",
    "STATE_OK",
    "STATE_UNAVAILABLE",
    "QueryOutcome",
    "SearchContext",
    "SearchRunResult",
    "run_searches",
]
