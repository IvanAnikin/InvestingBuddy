"""The web search orchestrator — open-web W1 (spec §8, §15.1, §18, §19, §22.3, §23).

``run_searches(session, requests, context)`` is the one entry point that sends queries
to a web search provider. For every request, in order:

1. **Secrets first** — before anything else, and for EVERY request whatever else is
   wrong with it: ``assert_no_credentials`` over the query and its filters, and the rule
   G1 private-token check over the query and its filters. A hit is refused and the row
   keeps neither the text nor a hash of it (``[withheld: <code>]``).
2. **Gate** — :func:`~app.services.web_research.queries.validate_outgoing_query`
   (operators, URLs, length, invisible characters, blocklist) and
   :func:`~app.services.web_research.queries.validate_filters`. A refused query is
   stored with URL secrets stripped and truncated to ``MAX_QUERY_CHARS``.
3. **Governance** (rule G4, spec §8.3) — ``ProviderGovernance.assert_permitted(provider,
   "public_web")``. The adapter repeats this and the credential check on its exact wire
   payload.
4. **Duplicates** — a second identical request in one batch is not sent again; it is
   served from the first (review C8).
5. **Cache** (spec §18) — when the provider's ``result_storage`` is ``full``, an
   executed network call for the same ``(provider, request_hash)`` in today's UTC date
   bucket is re-served from its rows. The new row carries ``served_from_query_id`` and
   ``network_call_count = 0``.
6. **Budget** (spec §19) — the run's query cap, wall time and the platform daily cap.
   Reserved before the call; a failed call keeps its reservation. Wall time is checked
   again as each call starts (review C9).
7. **Call** — at most ``concurrency`` (4) provider calls in flight. The adapter never
   raises by contract; if it does anyway, the error is recorded, not propagated.

Every outcome — executed, failed, refused, cached, cancelled — is persisted as a
``web_search_queries`` row with its results, so a run can be reconstructed from rows
(spec §22.2). **Rows are written even if the run is cancelled mid-batch** (a call in
flight is recorded as ``cancelled`` with one network call, so the daily cap never
undercounts). Pass ``persist_session_factory`` to write provenance in its own short
committed transaction, independent of the caller's (review C2); without it the rows are
added to ``session`` and flushed, and the caller must commit even when its stage fails.

STATES (spec §23.1)
===================
* ``web_search_disabled`` — flag off, or provider ``none``. Nothing is written.
* ``web_search_unavailable`` — at least one query was requested and none executed (no
  key, auth failure, outage, or every query refused). Never masked by recall.
* ``web_search_degraded`` — some executed, some did not (failure, budget, refusal).
* ``ok`` — every query executed or was served from cache.

Logs carry counts, codes and a 12-character request-hash prefix — or ``withheld`` for a
query refused for a secret or a private token. Never query text, never result text, never
the key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa

from app.core.log_redaction import redact_text
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
    SEARCH_ERROR_CANCELLED,
    SEARCH_ERROR_CREDENTIAL,
    SEARCH_ERROR_DUPLICATE,
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
from app.services.sources.redaction import canonicalize_source_url, strip_url_secrets
from app.services.web_research.budget import (
    BUDGET_REFUSAL_PREFIX,
    LIMIT_WALL,
    WebResearchBudget,
    budget_for_run,
    utc_day_start,
)
from app.services.web_research.queries import (
    _URL_RE,
    MAX_QUERY_CHARS,
    REFUSAL_PRIVATE_TOKEN,
    find_private_token,
    validate_filters,
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

#: Refusals whose query text must never be stored or hashed: private data or a secret.
WITHHELD_CODES: frozenset[str] = frozenset({REFUSAL_PRIVATE_TOKEN, SEARCH_ERROR_CREDENTIAL})

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
    #: Assigned up front so an in-batch duplicate can point at it before persistence.
    query_id: uuid.UUID = field(default_factory=uuid.uuid4)
    #: The first identical request in this batch, when this one is a duplicate.
    duplicate_of: "QueryOutcome | None" = None
    call_started: bool = False
    call_finished: bool = False
    #: Everything the provider returned, and how many of those the run's result budget
    #: admitted — set once, so rebuilding the provenance rows (a persistence fallback)
    #: never admits twice.
    all_results: list[SearchResultItem] | None = None
    admitted: int | None = None

    @property
    def from_cache(self) -> bool:
        return self.execution.cached_from is not None

    @property
    def withheld(self) -> bool:
        return (self.execution.error_code or "") in WITHHELD_CODES


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


def _clip(value: str | None, limit: int) -> str | None:
    return None if value is None else str(value)[:limit]


def _filter_text(request: SearchRequest) -> str:
    return json.dumps(request.filters(), sort_keys=True, ensure_ascii=False)


def _secret_refusal(request: SearchRequest, private_tokens: frozenset[str]) -> str | None:
    """Credential or private token anywhere in what would leave the process (S1, S5)."""
    try:
        assert_no_credentials(request.query + " " + _filter_text(request))
    except CredentialInPayloadError:
        return SEARCH_ERROR_CREDENTIAL
    if find_private_token(request.query, private_tokens):
        return REFUSAL_PRIVATE_TOKEN
    filter_values = " ".join(
        [*request.include_domains, *request.exclude_domains]
        + [request.country or "", request.language or ""]
    )
    if find_private_token(filter_values, private_tokens):
        return REFUSAL_PRIVATE_TOKEN
    return None


def _gate_refusal(request: SearchRequest, private_tokens: frozenset[str]) -> str | None:
    return validate_outgoing_query(request.query, private_tokens) or validate_filters(
        include_domains=request.include_domains,
        exclude_domains=request.exclude_domains,
        country=request.country,
        language=request.language,
        private_tokens=private_tokens,
    )


def _storable_query(text: str) -> str:
    """A refused-but-not-secret query as it may be stored: URL secrets stripped, bounded."""
    stripped = _URL_RE.sub(lambda m: strip_url_secrets(m.group(0)) or "", text or "")
    return redact_text(stripped)[:MAX_QUERY_CHARS]


def _governance_refusal(governance: ProviderGovernance, provider: str) -> str | None:
    try:
        governance.assert_permitted(provider, ACCESS_PUBLIC_WEB)
    except ProviderNotPermittedError:
        return SEARCH_ERROR_GOVERNANCE
    return None


async def _cached(
    session: Any, provider: SearchProvider, request: SearchRequest, now: datetime
) -> tuple[SearchExecution, list[SearchResultItem]] | None:
    """An executed network call for the same question in today's UTC bucket, re-served.

    Only ORIGINAL executed calls qualify (``executed`` and ``network_call_count > 0``):
    a failed call is never a cache entry, and a cache serve is never the source of
    another one.
    """
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
                WebSearchQuery.created_at <= now,
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
        date_unchecked_count=int(filters.get("date_unchecked_count") or 0),
    )
    return execution, items


def _served_from(first: QueryOutcome) -> SearchExecution:
    """What an in-batch duplicate records: a serve of the first copy, or a refusal."""
    src = first.execution
    if not src.executed:
        return SearchExecution.not_executed(
            src.provider, SEARCH_ERROR_DUPLICATE, filters_enforced_by=src.filters_enforced_by
        )
    return SearchExecution(
        provider=src.provider,
        executed=True,
        provider_request_id=src.provider_request_id,
        http_status=src.http_status,
        latency_ms=0,
        result_count=src.result_count,
        cost_units={},
        error_code=None,
        filters_enforced_by=dict(src.filters_enforced_by),
        network_call_count=0,
        client_filtered_count=src.client_filtered_count,
        cached_from=str(first.query_id),
        date_unchecked_count=src.date_unchecked_count,
    )


async def _call(
    provider: SearchProvider,
    outcome: QueryOutcome,
    semaphore: asyncio.Semaphore,
    budget: WebResearchBudget,
) -> None:
    async with semaphore:
        if budget.wall_exhausted():
            outcome.execution = SearchExecution.not_executed(
                provider.name, BUDGET_REFUSAL_PREFIX + LIMIT_WALL
            )
            outcome.call_finished = True
            return
        outcome.call_started = True
        try:
            execution, items = await provider.search(outcome.request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - an adapter must not take the run down
            log_event(
                logger,
                "web_search_adapter_error",
                level=logging.WARNING,
                provider=provider.name,
                error_type=type(exc).__name__,
            )
            execution, items = SearchExecution.not_executed(provider.name, SEARCH_ERROR_ADAPTER), []
        outcome.execution = execution
        outcome.results = list(items) if execution.executed else []
        outcome.call_finished = True


def _rows(
    outcome: QueryOutcome,
    context: SearchContext,
    budget: WebResearchBudget,
    storage: str,
) -> list[Any]:
    request, execution = outcome.request, outcome.execution
    code = execution.error_code
    withhold = outcome.withheld
    served_from = uuid.UUID(execution.cached_from) if execution.cached_from else None
    filters: dict[str, Any] = {
        "requested": request.filters(),
        "enforced_by": dict(execution.filters_enforced_by),
        "client_filtered_count": execution.client_filtered_count,
        "date_unchecked_count": execution.date_unchecked_count,
    }
    row = WebSearchQuery(
        id=outcome.query_id,
        research_job_id=context.research_job_id,
        discovery_run_id=context.discovery_run_id,
        agent_run_id=context.agent_run_id,
        company_id=context.company_id,
        stage=_clip(context.stage, 40),
        family=request.family.value,
        origin=_clip(request.origin, 30) or "template",
        template_version=_clip(request.template_version, 40),
        query_text=f"[withheld: {code}]" if withhold else _storable_query(request.query),
        # A hash of withheld text is still derived from it: a dictionary of candidate
        # tickers would recover the private token. Withheld means withheld.
        request_hash=f"withheld:{code}"[:64] if withhold else request.request_hash(),
        filters_json=None if withhold else filters,
        provider=_clip(execution.provider, 40) or "unknown",
        executed=execution.executed,
        # A cache serve has no request id of its own; it lives on the original row.
        provider_request_id=None if served_from else _clip(execution.provider_request_id, 120),
        http_status=execution.http_status,
        network_call_count=execution.network_call_count,
        latency_ms=execution.latency_ms,
        result_count=execution.result_count,
        cost_units_json=dict(execution.cost_units),
        error_code=_clip(code, 60),
        served_from_query_id=served_from,
        created_at=datetime.now(timezone.utc),
    )
    rows: list[Any] = [row]

    if outcome.all_results is None:
        outcome.all_results = list(outcome.results)
        outcome.admitted = budget.record_results(len(outcome.all_results))
    returned = outcome.all_results
    admitted = int(outcome.admitted or 0)
    outcome.results = returned[:admitted]
    if storage == RESULT_STORAGE_TRANSIENT:
        return rows
    keep_text = storage == RESULT_STORAGE_FULL
    for index, item in enumerate(returned):
        over_budget = index >= admitted
        rows.append(
            WebSearchResult(
                id=uuid.uuid4(),
                query_id=row.id,
                rank=item.rank,
                # The stored form never carries a credential-bearing parameter (S9).
                url=strip_url_secrets(item.url) or item.url,
                canonical_url=canonicalize_source_url(item.canonical_url),
                domain=_clip(item.domain, 255) or None,
                title=item.title if keep_text else None,
                snippet=item.snippet if keep_text else None,
                published_hint=item.published_hint,
                language_hint=_clip(item.language_hint, 16),
                provider_score=item.provider_score,
                disposition=DISPOSITION_SKIPPED if over_budget else DISPOSITION_CANDIDATE,
                disposition_reason="budget:max_results" if over_budget else None,
                created_at=datetime.now(timezone.utc),
            )
        )
    return rows


async def _persist_all(
    session: Any,
    outcomes: Sequence[QueryOutcome],
    context: SearchContext,
    budget: WebResearchBudget,
    storage: str,
    persist_session_factory: Callable[[], Any] | None,
) -> None:
    rows: list[Any] = []
    for outcome in outcomes:
        rows.extend(_rows(outcome, context, budget, storage))
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
            request_hash="withheld" if outcome.withheld else outcome.request.request_hash()[:12],
        )
    if persist_session_factory is not None:
        try:
            async with persist_session_factory() as own:
                own.add_all(rows)
                await own.commit()
            return
        except Exception as exc:  # noqa: BLE001 - paid calls must still be recorded
            # Typically a foreign key to an agent run / job / company the caller has not
            # committed yet (W5 review C-M5). The calls were paid for: record them in the
            # caller's own transaction instead of losing them and failing the stage.
            log_event(
                logger, "web_search_provenance_fallback", level=logging.WARNING,
                error_type=type(exc).__name__,
            )
            rows = _all_rows(outcomes, context, budget, storage)
    await _persist_in_caller(session, rows, outcomes, context, budget, storage)


def _all_rows(
    outcomes: Sequence[QueryOutcome], context: SearchContext, budget: WebResearchBudget,
    storage: str,
) -> list[Any]:
    rows: list[Any] = []
    for outcome in outcomes:
        rows.extend(_rows(outcome, context, budget, storage))
    return rows


async def _persist_in_caller(
    session: Any,
    rows: list[Any],
    outcomes: Sequence[QueryOutcome],
    context: SearchContext,
    budget: WebResearchBudget,
    storage: str,
) -> None:
    """Add the rows to the caller's session; if a lineage FK still fails, write them with
    the job/agent-run link NULL (both columns are nullable, ``SET NULL`` on delete)."""
    try:
        async with session.begin_nested():
            session.add_all(rows)
            await session.flush()
        return
    except Exception as exc:  # noqa: BLE001
        log_event(
            logger, "web_search_provenance_unlinked", level=logging.WARNING,
            error_type=type(exc).__name__,
        )
    from dataclasses import replace

    unlinked = replace(context, research_job_id=None, agent_run_id=None)
    # ``_rows`` re-spends nothing: it only rebuilds ORM objects (result admission is
    # recorded on the outcome the first time).
    fresh = _all_rows(outcomes, unlinked, budget, storage)
    async with session.begin_nested():
        session.add_all(fresh)
        await session.flush()


def _consumption(provider: SearchProvider, outcomes: Sequence[QueryOutcome]) -> ConsumptionUnits:
    """Units for the run record (review C3).

    For Tavily the credit unit is ALWAYS instrumented, so the flat per-search price can
    never stand in for it. A call that reached the vendor and did not come back with a
    credit figure — an executed call with no ``usage``, a 2xx that did not parse, a
    timeout or a cancellation — marks the credits ``unreported``, which makes the cost
    unknown. Only a clear non-2xx HTTP refusal is taken as unbilled.
    """
    calls = sum(o.execution.network_call_count for o in outcomes)
    instrumented = {"web_search_calls"}
    unreported: set[str] = set()
    credits = 0.0
    if provider.name == "tavily":
        instrumented.add("tavily_credits")
        for o in outcomes:
            ex = o.execution
            if ex.network_call_count == 0:
                continue
            value = ex.cost_units.get("tavily_credits")
            if value is not None:
                credits += float(value)
                continue
            refused_by_http = ex.http_status is not None and not 200 <= ex.http_status < 300
            if not refused_by_http:
                unreported.add("tavily_credits")
    return ConsumptionUnits(
        web_search_calls=calls,
        tavily_credits=credits,
        instrumented=frozenset(instrumented),
        unreported=frozenset(unreported),
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
    persist_session_factory: Callable[[], Any] | None = None,
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
    tokens = frozenset(context.private_tokens)
    # An unconfigured provider (no key, unknown name, fake outside dev) answers without a
    # network call and without budget: the refusal it returns is the record.
    configured = bool(getattr(chosen, "is_configured", True))

    def refused(request: SearchRequest, code: str) -> QueryOutcome:
        return QueryOutcome(request, SearchExecution.not_executed(chosen.name, code))

    outcomes: list[QueryOutcome] = []
    pending: list[QueryOutcome] = []
    first_by_hash: dict[str, QueryOutcome] = {}
    for request in requests:
        code = _secret_refusal(request, tokens) or _gate_refusal(request, tokens)
        if code is not None:
            outcomes.append(refused(request, code))
            continue
        if not configured:
            execution, _ = await chosen.search(request)
            outcomes.append(QueryOutcome(request, execution))
            continue
        code = _governance_refusal(gov, chosen.name)
        if code is not None:
            outcomes.append(refused(request, code))
            continue
        key = request.request_hash()
        if key in first_by_hash:
            outcomes.append(
                QueryOutcome(
                    request,
                    SearchExecution.not_executed(chosen.name, SEARCH_ERROR_DUPLICATE),
                    duplicate_of=first_by_hash[key],
                )
            )
            continue
        cached = await _cached(session, chosen, request, moment)
        if cached is not None:
            outcome = QueryOutcome(request, cached[0], list(cached[1]))
            first_by_hash[key] = outcome
            outcomes.append(outcome)
            continue
        code = budget.reserve_query()
        if code is not None:
            outcomes.append(refused(request, code))
            continue
        # Until the call reports, the outcome is "cancelled": that is what it is if the
        # run stops before this call returns.
        outcome = QueryOutcome(
            request, SearchExecution.not_executed(chosen.name, SEARCH_ERROR_CANCELLED)
        )
        first_by_hash[key] = outcome
        outcomes.append(outcome)
        pending.append(outcome)

    storage = chosen.capabilities.result_storage
    if storage not in (RESULT_STORAGE_FULL, RESULT_STORAGE_URL_ONLY, RESULT_STORAGE_TRANSIENT):
        storage = RESULT_STORAGE_TRANSIENT

    try:
        if pending:
            semaphore = asyncio.Semaphore(max(1, int(concurrency)))
            await asyncio.gather(*(_call(chosen, o, semaphore, budget) for o in pending))
    finally:
        # Also on cancellation: every reserved call leaves a row (review C2). A call that
        # started and never reported is counted as one network call, so the daily cap
        # cannot undercount; one that never started made none.
        for outcome in pending:
            if not outcome.call_finished:
                outcome.execution = SearchExecution.not_executed(
                    chosen.name,
                    SEARCH_ERROR_CANCELLED,
                    network_call_count=1 if outcome.call_started else 0,
                )
                outcome.results = []
        for outcome in outcomes:
            if outcome.duplicate_of is not None:
                outcome.execution = _served_from(outcome.duplicate_of)
                outcome.results = (
                    list(outcome.duplicate_of.results) if outcome.execution.executed else []
                )
        await _persist_all(
            session, outcomes, context, budget, storage, persist_session_factory
        )

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
    "WITHHELD_CODES",
    "QueryOutcome",
    "SearchContext",
    "SearchRunResult",
    "run_searches",
]
