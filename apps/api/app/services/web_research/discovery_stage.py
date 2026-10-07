"""The Discovery web stage (wave 1) — open-web W6b (spec §3.2, §4.4, §6, §19.1, §23).

``run_discovery_web_stage(session, intent, ctx, ...)`` turns a Discovery Intent into LEADS
that a REAL search surfaced and a FETCHED page named. It runs inside the dynamic stage,
before the per-ticker scan, and is resume-safe.

THE STEPS
=========
1. **Plan** (``discovery_planner``) — a deterministic query set from the intent's closed
   vocabularies and the versioned glossary, plus a bounded recorded model expansion.
2. **Search** (``search.run_searches``, W1) — every outcome is a provenance row
   (``discovery_run_id`` + ``stage="discovery_web"``), executed-only. A query this run
   already recorded is REUSED, never re-issued (a retried durable job spends no search
   twice), and the run's query count carries across attempts.
3. **Saturation follow-ups** — a query whose results are ≥ 70 % known names' sites gets
   its family's next variant with those IR domains excluded (page 2 for ``ENTITY``).
4. **Select** — round-robin over the families, scored by source class, relevance and
   novelty; a known name's own IR pages rank DOWN (context, not discovery).
5. **Fetch + extract** (``open_web_fetch`` W2; W3 extraction in the killable pool). Pages
   enter the corpus as THEME documents (``subject_scope="theme"``) when W3 ingest is on.
6. **Mentions** (``candidate_extract``) — listed-company names beside a ticker + venue /
   ISIN, in paragraphs and table rows, with the passage each sits in.
7. **Leads** — one ``CompanyLead`` per company, ``discovery_mode="search"`` ONLY when its
   provenance names an executed query row and a fetched page; ``lead.web`` carries the
   sightings (query id, result id, fetch attempt id, rank, URL) and the mention passages
   (evidence ids). The existing ``verify_identity`` then verifies the listing.

INVARIANTS
==========
* **No masquerade.** An outage is ``web_search_unavailable``; the stage returns NO leads
  and the pipeline falls back to V3.19 recall, labelled ``model_recall``. Nothing here
  can create a ``search`` lead without bytes the platform fetched.
* **Isolated.** The fetch phase runs in a SAVEPOINT inside ``try``; any exception is
  caught and recorded as ``web_stage_failed`` (no web leads, the run continues). A lost
  lease or a cancellation raised by the ``progress`` hook is NOT swallowed.
* **Flag off = nothing.** With ``V3_DISCOVERY_WEB_SEARCH_ENABLED`` off the stage returns
  before reading anything.
* **Pages are data.** A page's words never become a query, a prompt or an instruction: the
  planner takes no page text, and extraction is regex-only. A page the injection taint
  flags yields no mentions.

Logs carry counts, codes and states — never a query, a URL or page text.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Any

import sqlalchemy as sa

from app.core.structured_logging import log_event
from app.services.consumption import ConsumptionUnits
from app.services.discovery.leads import CompanyLead
from app.services.providers.contracts import (
    QueryFamily,
    SearchExecution,
    SearchRequest,
    SearchResultItem,
)
from app.services.web_research import candidate_extract as ce
from app.services.web_research import discovery_planner as dp
from app.services.web_research.budget import WebResearchBudget, budget_for_run
from app.services.web_research.domain_policy import denylisted
from app.services.web_research.entities import fold
from app.services.web_research.search import (
    STATE_DEGRADED,
    STATE_OK,
    STATE_UNAVAILABLE,
    QueryOutcome,
    SearchContext,
    run_searches,
)
from app.services.web_research.selection import (
    DUPLICATE_DOMAIN_PENALTY,
    SKIP_DENYLISTED,
    SKIP_DUPLICATE_URL,
    SKIP_NO_URL,
    SKIP_NOT_HTTPS,
    SKIP_OVER_BUDGET,
    Scored,
    SearchCandidate,
    SelectionContext,
    SelectionResult,
    domain_of,
    host_of,
    score_candidate,
)

logger = logging.getLogger(__name__)

SUMMARY_VERSION = 1
STAGE_NAME = "discovery_web"
LEAD_SCHEMA = "discovery_web_lead/1"

STATE_STAGE_DISABLED = "web_research_disabled"
STATE_STAGE_FAILED = "web_stage_failed"
STATE_PLAN_EMPTY = "web_plan_empty"

#: The banner when live search did not run (spec §23.1). One source of truth.
LABEL_UNAVAILABLE = (
    "Live web search unavailable — results come from official sources and model "
    "suggestions verified on exchange lists"
)

PROGRESS_SEARCH = "discovery_web_search"
PROGRESS_FETCH = "discovery_web_fetch"
PROGRESS_VERIFY = "discovery_web_verify"

#: A selection ranks a KNOWN name's own site down and an unfamiliar domain up.
UNKNOWN_DOMAIN_BONUS = 0.5
KNOWN_DOMAIN_PENALTY = 0.5
MAX_WEB_LEADS = 60
MAX_SIGHTINGS_PER_LEAD = 8
MAX_MENTIONS_PER_LEAD = 12
MAX_MENTIONS_PER_PAGE = 40
MAX_TICKER_RESOLUTIONS = 20
EURONEXT_VENUES = ("PA", "MI", "AS", "BR", "LS", "OL", "IR")

_UNSET: Any = object()
FetchFn = Callable[..., Awaitable[Any]]
ProgressHook = Callable[[str | None], Awaitable[None]]


# --------------------------------------------------------------------------- #
# Inputs and outputs
# --------------------------------------------------------------------------- #


@dataclass
class DiscoveryWebContext:
    """What the pipeline tells the stage about THIS run. Every field is platform-owned."""

    run_id: uuid.UUID | None = None
    #: IR / site domains of KNOWN names (curated registry, held companies, candidates).
    known_domains: tuple[str, ...] = ()
    #: ``venue:ticker`` keys of the same names (the novelty metric).
    known_keys: frozenset[str] = frozenset()
    #: Rule G1 tokens a query must never contain.
    private_tokens: frozenset[str] = frozenset()
    search_backend: Any = None
    #: ``await commit()`` after the search phase, so a crash keeps the recorded rows.
    commit: Callable[[], Awaitable[None]] | None = None
    #: The date the query set is planned for (date windows, the year slot). The run's
    #: CREATION date, so a retry after midnight UTC plans the SAME queries and reuses the
    #: recorded rows instead of paying for them again.
    plan_date: date | None = None
    #: The model expansion is persisted on the RUN before any search is paid for, and
    #: reloaded on a retry: a different proposal after a recycle would change the request
    #: hashes and spend the expansion share of the ceiling a second time.
    expansion_loader: Callable[[], Awaitable[list[str] | None]] | None = None
    expansion_saver: Callable[[list[str]], Awaitable[None]] | None = None


@dataclass
class DiscoveryWebDeps:
    """Test seams. Production leaves every field at its default."""

    provider: Any = _UNSET
    fetch: FetchFn | None = None
    fetch_kwargs: Mapping[str, Any] = field(default_factory=dict)
    llm_transport: Any = _UNSET
    pool: Any = None
    store: Any = None
    persist_session_factory: Any = _UNSET
    today: date | None = None
    clock: Callable[[], float] = time.monotonic
    now: datetime | None = None
    #: Replaces ``find_listing`` for ticker resolution (same keyword signature).
    find_listing: Any = None
    #: The corpus search backend a stored page is indexed into (default: the configured one).
    search_backend: Any = None


@dataclass
class DiscoveryWebResult:
    state: str
    summary: dict[str, Any]
    leads: list[CompanyLead] = field(default_factory=list)
    #: ``web_search_queries.id`` of every EXECUTED query this run holds (A1 checks these).
    executed_query_ids: frozenset[str] = frozenset()
    units: ConsumptionUnits = field(default_factory=ConsumptionUnits)
    theme_key: str | None = None

    @property
    def ran(self) -> bool:
        return self.state != STATE_STAGE_DISABLED


@dataclass
class _Box:
    units: ConsumptionUnits = field(default_factory=ConsumptionUnits)
    budget: WebResearchBudget | None = None


@dataclass
class _Tally:
    planned: int = 0
    executed: int = 0
    from_cache: int = 0
    failed: int = 0
    not_issued: int = 0
    reused: int = 0
    followups: int = 0
    saturated: list[str] = field(default_factory=list)
    excluded_domains: int = 0
    results_seen: int = 0
    selected: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    fetch_attempted: int = 0
    fetched: int = 0
    not_retrievable: int = 0
    fetch_failed: int = 0
    ingested: int = 0
    reused_docs: int = 0
    pages_extracted: int = 0
    pages_suspect: int = 0
    pages_with_mentions: int = 0
    not_ingested: dict[str, int] = field(default_factory=dict)
    mentions: int = 0
    name_only_dropped: int = 0
    rejected_names: int = 0
    source_classes: dict[str, int] = field(default_factory=dict)
    family_selected: dict[str, int] = field(default_factory=dict)
    not_retrievable_list: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class _Page:
    """One fetched, extracted page and what it named."""

    url: str
    domain: str
    candidate: SearchCandidate
    query_key: str
    query_origin: str
    template_version: str | None
    provider: str | None
    attempt_id: str | None
    source_class: str | None
    version_id: uuid.UUID | None
    suspect: bool
    mentions: list[tuple[ce.RawMention, str, str]] = field(default_factory=list)
    other_hosts: tuple[str, ...] = ()


class _Abort(Exception):
    """Wraps an exception from the ``progress`` hook so no ``except Exception`` eats it."""

    def __init__(self, original: BaseException) -> None:
        super().__init__(type(original).__name__)
        self.original = original


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def stage_enabled(cfg: Any) -> bool:
    return bool(getattr(cfg, "v3_discovery_web_search_enabled", False))


def theme_key_for_run(run_id: uuid.UUID | str | None) -> str | None:
    """The corpus theme key of a Discovery run's documents (``search_theme_corpus``)."""
    return f"discovery:{run_id}" if run_id else None


def _depth(cfg: Any) -> str:
    return (
        "deep"
        if str(getattr(cfg, "v3_discovery_web_depth", "standard")).lower() == "deep"
        else "standard"
    )


async def _progress(hook: ProgressHook | None, stage: str) -> None:
    if hook is None:
        return
    try:
        await hook(stage)
    except BaseException as exc:  # noqa: BLE001 - re-raised by the caller, unchanged
        raise _Abort(exc) from exc


def passage_ref(attempt_id: str | None, passage: str) -> str:
    """A stable id for one fetched passage: the fetch attempt and the passage's own hash."""
    digest = hashlib.sha1(fold(passage).encode("utf-8", "replace")).hexdigest()[:12]  # noqa: S324
    return f"wp:{(attempt_id or 'na')[:8]}:{digest}"


def theme_vocabulary(facts: dp.DiscoveryFacts) -> ce.ThemeVocabulary:
    return ce.ThemeVocabulary(dp.theme_vocabulary_phrases(facts))


def _units_of(
    tally: _Tally, budget: WebResearchBudget, units: ConsumptionUnits
) -> ConsumptionUnits:
    from dataclasses import replace

    fetched = ConsumptionUnits(
        url_fetch_calls=budget.fetches,
        bytes_downloaded=budget.bytes_downloaded,
        instrumented=frozenset({"url_fetch_calls", "bytes_downloaded"}),
    )
    return replace(units + fetched, elapsed_seconds=0.0)


# --------------------------------------------------------------------------- #
# Search with resume
# --------------------------------------------------------------------------- #


async def _recorded_rows(
    session: Any, run_id: uuid.UUID | None, hashes: Sequence[str]
) -> dict[str, Any]:
    """This run's earlier query rows that made a network call (or served one), by hash."""
    from app.models.web_research import WebSearchQuery as Q

    if run_id is None or not hashes:
        return {}
    rows = (
        (
            await session.execute(
                sa.select(Q)
                .where(
                    Q.discovery_run_id == run_id,
                    Q.stage == STAGE_NAME,
                    Q.request_hash.in_(list(hashes)),
                )
                .order_by(Q.created_at, Q.id)
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, Any] = {}
    for row in rows:
        # Reused: anything that reached the provider, or re-served a call that did.
        # A refusal or a cancellation that never started a call is retried.
        if not (row.executed or row.network_call_count > 0 or row.served_from_query_id):
            continue
        current = out.get(row.request_hash)
        if current is None or (row.executed and not current.executed):
            out[row.request_hash] = row
    return out


async def _outcome_from_row(session: Any, row: Any, request: SearchRequest) -> QueryOutcome:
    from app.models.web_research import WebSearchResult as R

    results: list[SearchResultItem] = []
    if row.executed:
        for r in (
            (await session.execute(sa.select(R).where(R.query_id == row.id).order_by(R.rank)))
            .scalars()
            .all()
        ):
            results.append(
                SearchResultItem(
                    rank=int(r.rank),
                    url=r.url,
                    canonical_url=r.canonical_url or r.url,
                    domain=r.domain or host_of(r.url),
                    title=r.title,
                    snippet=r.snippet,
                    published_hint=r.published_hint,
                    language_hint=r.language_hint,
                    provider_score=r.provider_score,
                )
            )
    if row.executed:
        cached = str(row.served_from_query_id) if row.served_from_query_id else None
        execution = SearchExecution(
            provider=row.provider,
            executed=True,
            provider_request_id=row.provider_request_id or "reused",
            http_status=row.http_status or 200,
            latency_ms=int(row.latency_ms or 0),
            result_count=int(row.result_count or 0),
            cost_units=dict(row.cost_units_json or {}),
            network_call_count=0 if cached else max(1, int(row.network_call_count or 1)),
            cached_from=cached,
        )
    else:
        execution = SearchExecution.not_executed(
            row.provider,
            row.error_code or "failed",
            http_status=row.http_status,
            network_call_count=int(row.network_call_count or 0),
        )
    return QueryOutcome(request, execution, results, query_id=row.id)


async def _search_batch(
    session: Any,
    queries: Sequence[dp.PlannedQuery],
    *,
    ctx: DiscoveryWebContext,
    search_ctx: SearchContext,
    provider: Any,
    cfg: Any,
    deps: DiscoveryWebDeps,
    now: datetime,
    tally: _Tally,
    box: _Box,
) -> list[tuple[dp.PlannedQuery, QueryOutcome]]:
    """Run ``queries``, REUSING any this run already recorded."""
    if not queries:
        return []
    recorded = await _recorded_rows(
        session, ctx.run_id, [q.request.request_hash() for q in queries]
    )
    pairs: list[tuple[dp.PlannedQuery, QueryOutcome]] = []
    todo: list[dp.PlannedQuery] = []
    for q in queries:
        row = recorded.get(q.request.request_hash())
        if row is None:
            todo.append(q)
            continue
        pairs.append((q, await _outcome_from_row(session, row, q.request)))
        tally.reused += 1
    if todo:
        persist = (
            _persist_factory(session)
            if deps.persist_session_factory is _UNSET
            else deps.persist_session_factory
        )
        run = await run_searches(
            session,
            [q.request for q in todo],
            search_ctx,
            provider=provider,
            cfg=cfg,
            now=now,
            persist_session_factory=persist,
        )
        box.units = box.units + run.consumption
        by_request = {id(q.request): q for q in todo}
        for outcome in run.outcomes:
            pairs.append((by_request[id(outcome.request)], outcome))
    order = {id(q): i for i, q in enumerate(queries)}
    pairs.sort(key=lambda p: order[id(p[0])])
    return pairs


async def _prime_budget(session: Any, budget: WebResearchBudget, run_id: uuid.UUID | None) -> None:
    """Carry what EARLIER attempts of this run already spent into this attempt's budget.

    Every ``web_search_queries`` row the run holds counts against the query ceiling —
    orphans too (a query an earlier attempt planned and this one no longer does, e.g. a
    different model expansion) — and every fetch an earlier attempt completed counts against
    the fetch and byte ceilings. Without this a crash-and-retry loop could spend the
    ceiling again on each attempt.
    """
    if run_id is None:
        return
    from app.models.web_research import WebFetchAttempt as F
    from app.models.web_research import WebSearchQuery as Q
    from app.services.web_research.fetch import STATUS_FETCHED, STATUS_PARTIAL

    calls = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(Q.network_call_count), 0)).where(
            Q.discovery_run_id == run_id, Q.stage == STAGE_NAME
        )
    )
    budget.queries_reserved = max(budget.queries_reserved, int(calls or 0))
    fetched = (
        await session.execute(
            sa.select(sa.func.count(), sa.func.coalesce(sa.func.sum(F.bytes), 0)).where(
                F.discovery_run_id == run_id, F.status.in_((STATUS_FETCHED, STATUS_PARTIAL))
            )
        )
    ).one()
    budget.fetches = max(budget.fetches, int(fetched[0] or 0))
    budget.bytes_downloaded = max(budget.bytes_downloaded, int(fetched[1] or 0))


def _persist_factory(session: Any) -> Callable[[], Any] | None:
    """Provenance in its own short committed transaction, on PostgreSQL only."""
    try:
        bind = session.get_bind()
        if getattr(getattr(bind, "dialect", None), "name", "") != "postgresql":
            return None
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        return async_sessionmaker(bind=session.bind, class_=AsyncSession, expire_on_commit=False)
    except Exception:  # noqa: BLE001 - provenance falls back to the caller's session
        return None


def _domains_of(outcome: QueryOutcome) -> list[str]:
    return [host_of(item.url) for item in outcome.results if item.url]


def _count(pairs: Sequence[tuple[dp.PlannedQuery, QueryOutcome]], tally: _Tally) -> None:
    for _q, outcome in pairs:
        ex = outcome.execution
        if ex.executed:
            tally.executed += 1
            if outcome.from_cache:
                tally.from_cache += 1
        elif ex.network_call_count:
            tally.failed += 1
        else:
            tally.not_issued += 1


def _search_state(pairs: Sequence[tuple[dp.PlannedQuery, QueryOutcome]]) -> str:
    if not pairs:
        return STATE_UNAVAILABLE
    executed = sum(1 for _q, o in pairs if o.execution.executed)
    if executed == 0:
        return STATE_UNAVAILABLE
    return STATE_OK if executed == len(pairs) else STATE_DEGRADED


# --------------------------------------------------------------------------- #
# Selection (discovery flavour: round-robin over families, novelty-aware)
# --------------------------------------------------------------------------- #


def select_discovery_results(
    candidates: Sequence[SearchCandidate],
    *,
    today: date,
    known_domains: Sequence[str],
    terms_by_family: Mapping[QueryFamily, tuple[str, ...]],
    total: int,
    mode: str = "standard",
) -> SelectionResult:
    """Pick the URLs worth fetching. Pure and deterministic.

    The score is the W5 candidate score (source class, relevance, freshness) plus a bonus
    for a domain that is NOT a known name's site and a penalty for one that is: a famous
    company's own pages are context, and evidence being plentiful for it must not outrank
    an unfamiliar company's page that fits the theme. Families are served round-robin.
    """
    result = SelectionResult(version=f"{dp.DISCOVERY_TEMPLATE_VERSION}")
    known = {d.lower().removeprefix("www.") for d in known_domains}

    def is_known(host: str) -> bool:
        return host in known or any(host.endswith("." + k) for k in known)

    order = {f: i for i, f in enumerate(dp.DISCOVERY_FAMILIES)}
    ordered = sorted(candidates, key=lambda c: (order.get(c.family, 99), c.item.rank, c.url))
    seen: set[str] = set()
    pools: dict[QueryFamily, list[Scored]] = {}
    ctx = SelectionContext(today=today, family_terms=dict(terms_by_family), mode=mode, total=total)
    for candidate in ordered:
        url = candidate.url
        if not url:
            result.skipped.append((candidate, SKIP_NO_URL))
            continue
        if not url.lower().startswith("https://"):
            result.skipped.append((candidate, SKIP_NOT_HTTPS))
            continue
        if denylisted(host_of(url)):
            result.skipped.append((candidate, SKIP_DENYLISTED))
            continue
        if url in seen:
            result.skipped.append((candidate, SKIP_DUPLICATE_URL))
            continue
        seen.add(url)
        scored = score_candidate(candidate, ctx)
        adj = -KNOWN_DOMAIN_PENALTY if is_known(host_of(url)) else UNKNOWN_DOMAIN_BONUS
        pools.setdefault(candidate.family, []).append(
            Scored(
                candidate,
                round(scored.score + adj, 6),
                {**scored.components, "domain_novelty": adj},
                scored.source_class,
            )
        )
    per_domain: dict[str, int] = {}
    chosen: set[int] = set()

    def best(pool: list[Scored]) -> Scored | None:
        remaining = [s for s in pool if id(s) not in chosen]
        if not remaining:
            return None
        return min(
            remaining,
            key=lambda s: (
                -(
                    s.score
                    - DUPLICATE_DOMAIN_PENALTY * per_domain.get(domain_of(s.candidate.url), 0)
                ),
                s.candidate.item.rank,
                s.candidate.url,
            ),
        )

    cap = max(0, int(total))
    progressed = True
    while len(result.selected) < cap and progressed:
        progressed = False
        for family in dp.DISCOVERY_FAMILIES:
            if len(result.selected) >= cap:
                break
            choice = best(pools.get(family, []))
            if choice is None:
                continue
            chosen.add(id(choice))
            result.selected.append(choice)
            d = domain_of(choice.candidate.url)
            per_domain[d] = per_domain.get(d, 0) + 1
            progressed = True
    for pool in pools.values():
        for scored in pool:
            if id(scored) not in chosen:
                result.skipped.append((scored.candidate, SKIP_OVER_BUDGET))
    return result


# --------------------------------------------------------------------------- #
# The stage
# --------------------------------------------------------------------------- #


async def run_discovery_web_stage(
    session: Any,
    intent: Any,
    ctx: DiscoveryWebContext,
    *,
    cfg: Any | None = None,
    deps: DiscoveryWebDeps | None = None,
    progress: ProgressHook | None = None,
) -> DiscoveryWebResult:
    """Run wave 1. **Never raises** (a ``progress`` abort and cancellation excepted)."""
    if cfg is None:
        from app.core.config import settings as cfg  # noqa: PLW0127
    deps = deps or DiscoveryWebDeps()
    if not stage_enabled(cfg):
        return DiscoveryWebResult(STATE_STAGE_DISABLED, {})
    started = deps.clock()
    tally = _Tally()
    box = _Box()
    theme_key = theme_key_for_run(ctx.run_id)
    summary: dict[str, Any] = {
        "version": SUMMARY_VERSION,
        "state": STATE_OK,
        "depth": _depth(cfg),
        "template_version": dp.DISCOVERY_TEMPLATE_VERSION,
        "glossary_version": dp.loc.GLOSSARY_VERSION,
        "extractor_version": ce.EXTRACTOR_VERSION,
        "theme_key": theme_key,
    }
    leads: list[CompanyLead] = []
    executed_ids: frozenset[str] = frozenset()
    state: str
    try:
        state, leads, executed_ids = await _execute(
            session,
            intent,
            ctx,
            cfg=cfg,
            deps=deps,
            tally=tally,
            summary=summary,
            box=box,
            progress=progress,
        )
    except _Abort as abort:
        raise abort.original from None
    except Exception as exc:  # noqa: BLE001 - the stage never fails the run (spec §23.3)
        state = STATE_STAGE_FAILED
        leads, executed_ids = [], frozenset()
        summary["error"] = type(exc).__name__
        tally.notes.append("the web stage raised and was isolated; no web leads were produced")
        log_event(
            logger,
            "discovery_web_stage_failed",
            level=logging.WARNING,
            error_type=type(exc).__name__,
        )
    summary["state"] = state
    units = _units_of(tally, box.budget, box.units) if box.budget is not None else box.units
    if box.budget is not None:
        summary["budget"] = box.budget.to_dict()
    summary["cost_units"] = units.to_dict()
    summary["label"] = _label(summary)
    summary["notes"] = tally.notes[:12]
    summary["elapsed_seconds"] = round(deps.clock() - started, 3)
    return DiscoveryWebResult(state, summary, leads, executed_ids, units, theme_key)


def _label(summary: Mapping[str, Any]) -> str | None:
    state = summary.get("state")
    if state == STATE_DEGRADED:
        q = summary.get("queries") or {}
        return (
            f"Web search incomplete ({q.get('executed', 0)} of {q.get('planned', 0)} searches ran)"
        )
    if state in (STATE_UNAVAILABLE, STATE_STAGE_FAILED):
        return LABEL_UNAVAILABLE
    return None


async def _execute(
    session: Any,
    intent: Any,
    ctx: DiscoveryWebContext,
    *,
    cfg: Any,
    deps: DiscoveryWebDeps,
    tally: _Tally,
    summary: dict[str, Any],
    box: _Box,
    progress: ProgressHook | None,
) -> tuple[str, list[CompanyLead], frozenset[str]]:
    # 1. Flags and provider. Discovery's own flag is on, so a missing search capability is
    #    a LABELLED unavailability — never silence, never a fake "search".
    if not bool(getattr(cfg, "v3_web_search_enabled", False)):
        summary["reason"] = "search_disabled"
        return STATE_UNAVAILABLE, [], frozenset()
    from app.integrations.search import web_search_provider_from_settings

    provider = web_search_provider_from_settings(cfg) if deps.provider is _UNSET else deps.provider
    if provider is None:
        summary["reason"] = "no_provider"
        return STATE_UNAVAILABLE, [], frozenset()
    summary["provider"] = getattr(provider, "name", None)

    # 2. Facts, budget, plan.
    facts = dp.facts_from_intent(intent)
    depth = _depth(cfg)
    profile = f"discovery_{depth}"
    summary["profile"] = profile
    now = deps.now or datetime.now(timezone.utc)
    today = deps.today or ctx.plan_date or now.date()
    budget = await budget_for_run(session, profile, cfg=cfg, now=now, clock=deps.clock)
    await _prime_budget(session, budget, ctx.run_id)
    box.budget = budget
    limits = budget.limits
    private = frozenset(ctx.private_tokens)
    plan = dp.build_discovery_plan(
        facts,
        mode=depth,
        max_queries=limits.max_queries,
        today=today,
        private_tokens=private,
    )
    if not plan.queries:
        summary["reason"] = "intent_names_nothing_a_search_can_be_built_from"
        return STATE_PLAN_EMPTY, [], frozenset()
    expansion = await _resolve_expansion(session, ctx, cfg, deps, facts, plan, limits, private)
    if expansion is not None:
        box.units = box.units + expansion.units
        if expansion.queries:
            plan = dp.build_discovery_plan(
                facts,
                mode=depth,
                max_queries=limits.max_queries,
                today=today,
                private_tokens=private,
                expansion=expansion.queries,
            )
            plan.expansion = expansion.to_dict()
    tally.planned = len(plan.queries)

    # 3. Search (wave 1), then saturation follow-ups. Every call is a provenance row.
    await _progress(progress, PROGRESS_SEARCH)
    search_ctx = SearchContext(
        discovery_run_id=ctx.run_id,
        stage=STAGE_NAME,
        private_tokens=private,
        budget=budget,
        budget_profile=profile,
    )
    # In a SAVEPOINT: an error in the search phase releases only its own writes, so the
    # run's transaction stays usable (the provenance rows of PAID calls are committed by the
    # independent session on PostgreSQL either way).
    async with session.begin_nested():
        pairs = await _search_batch(
            session,
            plan.queries,
            ctx=ctx,
            search_ctx=search_ctx,
            provider=provider,
            cfg=cfg,
            deps=deps,
            now=now,
            tally=tally,
            box=box,
        )
        saturated = [
            q
            for q, o in pairs
            if o.execution.executed and dp.is_saturated(_domains_of(o), ctx.known_domains)
        ]
        if saturated and ctx.known_domains:
            followups = dp.build_followups(
                plan,
                saturated,
                ctx.known_domains,
                limit=max(0, limits.max_queries - len(plan.queries)),
                result_domains={q.key: _domains_of(o) for q, o in pairs},
            )
            tally.saturated = [q.key for q in saturated]
            tally.followups = len(followups)
            tally.excluded_domains = len(followups[0].request.exclude_domains) if followups else 0
            extra = await _search_batch(
                session,
                followups,
                ctx=ctx,
                search_ctx=search_ctx,
                provider=provider,
                cfg=cfg,
                deps=deps,
                now=now,
                tally=tally,
                box=box,
            )
            pairs.extend(extra)
            tally.planned += len(followups)
    _count(pairs, tally)
    if ctx.commit is not None:
        await ctx.commit()
    summary["queries"] = {
        "planned": tally.planned,
        "executed": tally.executed,
        "from_cache": tally.from_cache,
        "failed": tally.failed,
        "not_issued": tally.not_issued,
        "reused_on_resume": tally.reused,
        "by_family": _by_family(pairs),
        "locales": plan.locales,
        "locale_variants": sum(1 for q, _ in pairs if q.locale),
        "followups": tally.followups,
        "saturated_queries": tally.saturated[:20],
        "excluded_domains": tally.excluded_domains,
        "template_keys": [q.key for q, _ in pairs][:80],
        "refused_by_sanitiser": len(plan.refused),
        "expansion": expansion.to_dict() if expansion is not None else None,
    }
    state = _search_state(pairs)
    executed_ids = frozenset(str(o.query_id) for _q, o in pairs if o.execution.executed)
    if state == STATE_UNAVAILABLE:
        return state, [], executed_ids

    # 4. Results -> candidates (with their persisted row ids), then selection.
    rows = await _result_rows(session, [o.query_id for _q, o in pairs if o.execution.executed])
    candidates: list[SearchCandidate] = []
    meta: dict[uuid.UUID, tuple[dp.PlannedQuery, QueryOutcome]] = {}
    for q, outcome in pairs:
        if not outcome.execution.executed:
            continue
        meta[outcome.query_id] = (q, outcome)
        for item in outcome.results:
            row = rows.get((outcome.query_id, item.rank))
            candidates.append(
                SearchCandidate(
                    family=q.family,
                    item=item,
                    query_key=q.key,
                    query_id=outcome.query_id,
                    result_id=getattr(row, "id", None),
                )
            )
    tally.results_seen = len(candidates)
    selection = select_discovery_results(
        candidates,
        today=today,
        known_domains=ctx.known_domains,
        terms_by_family={
            f: tuple(dict.fromkeys(t for q, _ in pairs if q.family is f for t in q.terms))
            or dp.FAMILY_TERMS[f]
            for f in dp.DISCOVERY_FAMILIES
        },
        total=limits.max_fetches,
        mode=depth,
    )
    tally.selected = len(selection.selected)
    rows_by_id = {r.id: r for r in rows.values()}
    for scored in selection.selected:
        _disposition(rows_by_id.get(scored.candidate.result_id), "selected", None)
        fam = scored.candidate.family.value
        tally.family_selected[fam] = tally.family_selected.get(fam, 0) + 1
    for candidate, reason in selection.skipped:
        tally.skipped[reason] = tally.skipped.get(reason, 0) + 1
        _disposition(rows_by_id.get(candidate.result_id), "skipped", reason)
    await session.flush()

    # 5. Fetch + extract + mentions, in a SAVEPOINT (isolated from the run).
    await _progress(progress, PROGRESS_FETCH)
    pages: list[_Page] = []
    vocab = theme_vocabulary(facts)
    async with session.begin_nested():
        await _fetch_phase(
            session,
            ctx,
            selection,
            meta,
            rows_by_id,
            vocab,
            facts,
            pages,
            budget=budget,
            cfg=cfg,
            deps=deps,
            tally=tally,
            summary=summary,
            theme_key=theme_key_for_run(ctx.run_id),
            provider_name=summary.get("provider"),
            progress=progress,
            depth=depth,
        )
    if ctx.commit is not None:
        await ctx.commit()

    # 6. Leads.
    await _progress(progress, PROGRESS_VERIFY)
    leads = await _build_leads(
        session,
        pages,
        ctx,
        deps=deps,
        cfg=cfg,
        tally=tally,
        summary=summary,
        provider_name=summary.get("provider"),
    )
    summary["results"] = {
        "seen": tally.results_seen,
        "selected": tally.selected,
        "skipped_by_reason": dict(sorted(tally.skipped.items())),
        "selected_by_family": dict(sorted(tally.family_selected.items())),
    }
    summary["fetch"] = {
        "attempted": tally.fetch_attempted,
        "fetched": tally.fetched,
        "not_retrievable": tally.not_retrievable,
        "failed": tally.fetch_failed,
        "bytes": budget.bytes_downloaded,
        "pdfs": budget.pdfs,
    }
    summary["pages"] = {
        "extracted": tally.pages_extracted,
        "with_mentions": tally.pages_with_mentions,
        "injection_suspect": tally.pages_suspect,
        "ingested": tally.ingested,
        "reused": tally.reused_docs,
        "not_ingested": dict(sorted(tally.not_ingested.items())),
        "source_classes": dict(sorted(tally.source_classes.items())),
    }
    summary["not_retrievable"] = tally.not_retrievable_list[:20]
    log_event(
        logger,
        "discovery_web_stage_completed",
        state=state,
        queries=tally.executed,
        selected=tally.selected,
        fetched=tally.fetched,
        leads=len(leads),
        run_id=ctx.run_id,
    )
    return state, leads, executed_ids


def _by_family(pairs: Sequence[tuple[dp.PlannedQuery, QueryOutcome]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for q, _o in pairs:
        out[q.family.value] = out.get(q.family.value, 0) + 1
    return out


def _disposition(row: Any, disposition: str, reason: str | None) -> None:
    if row is None:
        return
    row.disposition = disposition[:30]
    row.disposition_reason = (reason or "")[:120] or None


async def _result_rows(session: Any, query_ids: Sequence[uuid.UUID]) -> dict[tuple[Any, int], Any]:
    from app.models.web_research import WebSearchResult

    if not query_ids:
        return {}
    rows = (
        (
            await session.execute(
                sa.select(WebSearchResult).where(WebSearchResult.query_id.in_(list(query_ids)))
            )
        )
        .scalars()
        .all()
    )
    return {(r.query_id, int(r.rank)): r for r in rows}


async def _resolve_expansion(
    session: Any,
    ctx: DiscoveryWebContext,
    cfg: Any,
    deps: DiscoveryWebDeps,
    facts: dp.DiscoveryFacts,
    plan: dp.DiscoveryPlan,
    limits: Any,
    private: frozenset[str],
) -> Any:
    """The run's model expansion: persisted, else recorded, else asked once and persisted.

    On a retry the SAME queries come back, from the run's own record, and the model is not
    called again (it answers at temperature 0.2, so a second answer differs). Order:
    (1) the proposal persisted on the run; (2) the expansion-origin query rows the run
    already holds (a crash between the proposal and its persistence); (3) the model.
    """
    from app.models.web_research import WebSearchQuery as Q
    from app.services.web_research.planner import ExpansionResult

    if limits.max_expansion_queries <= 0 or limits.max_llm_tokens <= 0:
        return None
    reused: list[str] | None = None
    source = None
    if ctx.expansion_loader is not None:
        reused = await ctx.expansion_loader()
        source = "persisted" if reused is not None else None
    if reused is None and ctx.run_id is not None:
        rows = (
            await session.execute(
                sa.select(Q.query_text)
                .where(
                    Q.discovery_run_id == ctx.run_id,
                    Q.stage == STAGE_NAME,
                    Q.origin == dp.ORIGIN_LLM_EXPANSION,
                )
                .order_by(Q.created_at, Q.id)
            )
        ).scalars().all()
        if rows:
            reused = list(dict.fromkeys(str(r) for r in rows))
            source = "recorded"
    if reused is not None:
        result = ExpansionResult(queries=tuple(reused[: limits.max_expansion_queries]))
        result.from_cache = True
        result.model = source
        return result
    result = await _expansion(cfg, deps, facts, plan, limits, private)
    if result is not None and result.error is None and ctx.expansion_saver is not None:
        await ctx.expansion_saver(list(result.queries))
    return result


async def _expansion(
    cfg: Any,
    deps: DiscoveryWebDeps,
    facts: dp.DiscoveryFacts,
    plan: dp.DiscoveryPlan,
    limits: Any,
    private: frozenset[str],
) -> Any:
    if limits.max_expansion_queries <= 0 or limits.max_llm_tokens <= 0:
        return None
    transport = deps.llm_transport
    if transport is _UNSET:
        from app.integrations.deepseek.transport import transport_from_settings

        try:
            transport = transport_from_settings(cfg)
        except Exception:  # noqa: BLE001 - expansion is optional
            transport = None
    if transport is None:
        return None
    return await dp.propose_discovery_expansion(
        transport,
        facts,
        plan,
        limit=limits.max_expansion_queries,
        max_tokens=min(600, limits.max_llm_tokens),
        private_tokens=private,
    )


# --------------------------------------------------------------------------- #
# Fetch, extract, mentions
# --------------------------------------------------------------------------- #


def _blocks_and_tables(extraction: Any) -> tuple[list[str], list[list[list[str]]]]:
    """Paragraphs and tables of an extraction, as BOUNDED copies (the page may be huge)."""
    body = getattr(extraction, "extraction", None)
    cap = ce.MAX_PARAGRAPH_CHARS
    blocks = [
        str(getattr(b, "text", "") or "")[:cap]
        for b in (getattr(body, "blocks", None) or [])[: ce.MAX_PARAGRAPHS]
    ]
    tables = [
        [
            [str(c or "")[: ce.MAX_CELL_CHARS] for c in row[:40]]
            for row in (getattr(t, "rows", None) or [])[: ce.MAX_TABLE_ROWS]
        ]
        for t in (getattr(body, "tables", None) or [])[:50]
    ]
    if not any(b.strip() for b in blocks):
        main = str(getattr(extraction, "main_text", "") or "")[: ce.MAX_TOTAL_CHARS]
        blocks = ce.paragraphs_of(main)[: ce.MAX_PARAGRAPHS]
    return blocks, tables


_MAX_CHUNKS_INDEXED = 2000
_MAX_CHUNK_CHARS = 20_000


async def _chunk_index(
    session: Any, version_id: uuid.UUID | None
) -> list[tuple[str, str]]:
    """``(chunk id, folded text)`` of a stored page, loaded and folded ONCE per page (a
    mention used to reload and re-fold every chunk — 40 mentions x 400 kB was ~7 s)."""
    if version_id is None:
        return []
    from app.models.research_chunk import ResearchDocumentChunk as C

    rows = (
        await session.execute(
            sa.select(C.chunk_id, C.text)
            .where(C.research_document_version_id == version_id)
            .order_by(C.ordinal)
            .limit(_MAX_CHUNKS_INDEXED)
        )
    ).all()
    return await asyncio.to_thread(
        lambda: [(str(cid)[:120], fold(str(text or "")[:_MAX_CHUNK_CHARS])) for cid, text in rows]
    )


def _find_chunk(index: Sequence[tuple[str, str]], mention: ce.RawMention) -> str | None:
    """The chunk that holds THIS PASSAGE — or None. Never a chunk that merely names the
    company: the evidence id must point at the passage that carries the theme term."""
    needle = fold(mention.passage)[:60]
    if not needle:
        return None
    for chunk_id, folded in index:
        if needle in folded:
            return chunk_id
    return None


async def _fetch_phase(
    session: Any,
    ctx: DiscoveryWebContext,
    selection: SelectionResult,
    meta: Mapping[uuid.UUID, tuple[dp.PlannedQuery, QueryOutcome]],
    rows_by_id: Mapping[Any, Any],
    vocab: ce.ThemeVocabulary,
    facts: dp.DiscoveryFacts,
    pages: list[_Page],
    *,
    budget: WebResearchBudget,
    cfg: Any,
    deps: DiscoveryWebDeps,
    tally: _Tally,
    summary: dict[str, Any],
    theme_key: str | None,
    provider_name: Any,
    progress: ProgressHook | None,
    depth: str,
) -> None:
    from app.services.web_research import fetch as fetch_mod
    from app.services.web_research import ingest as ingest_mod
    from app.services.web_research.classify import classify_source
    from app.services.web_research.extract import extract_web_document

    if not bool(getattr(cfg, "v3_web_fetch_enabled", False)):
        tally.notes.append("open-web fetch is disabled; search results were not retrieved")
        for scored in selection.selected:
            _disposition(
                rows_by_id.get(scored.candidate.result_id),
                "not_ingested",
                fetch_mod.FAILURE_FETCH_DISABLED,
            )
        return
    fetch_fn: FetchFn = deps.fetch or fetch_mod.open_web_fetch
    fetch_ctx = fetch_mod.WebFetchContext(discovery_run_id=ctx.run_id)
    terms = tuple(dp.theme_terms(facts))
    ingest_on = ingest_mod.ingest_enabled(cfg)
    stopped = False
    for scored in selection.selected:
        row = rows_by_id.get(scored.candidate.result_id)
        refusal = budget.fetch_refusal()
        if refusal is not None or stopped:
            _disposition(row, "skipped", refusal or "stopped")
            if refusal and not stopped:
                tally.notes.append(f"fetching stopped: {refusal}")
            stopped = True
            continue
        await _progress(progress, PROGRESS_FETCH)
        tally.fetch_attempted += 1
        item = scored.candidate.item
        try:
            fetched = await fetch_fn(
                session,
                item.url,
                context=fetch_ctx,
                budget=budget,
                origin=fetch_mod.ORIGIN_SEARCH,
                search_result_id=scored.candidate.result_id,
                **dict(deps.fetch_kwargs),
            )
        except Exception as exc:  # noqa: BLE001 - one bad URL costs that URL
            tally.fetch_failed += 1
            _disposition(row, "fetch_failed", type(exc).__name__[:60])
            continue
        if fetched.status == fetch_mod.STATUS_NOT_RETRIEVABLE:
            tally.not_retrievable += 1
            reason = fetched.failure_code or "not_retrievable"
            _disposition(row, "not_retrievable", reason)
            tally.not_retrievable_list.append({"domain": host_of(item.url), "reason": reason})
            continue
        if not fetched.ok:
            tally.fetch_failed += 1
            _disposition(row, "fetch_failed", fetched.failure_code or fetched.status)
            continue
        tally.fetched += 1

        version_id: uuid.UUID | None = None
        try:
            if ingest_on:
                prepared = await ingest_mod.prepare_web_document(
                    fetched,
                    cfg=cfg,
                    provider=None,
                    subject_scope="theme",
                    theme_key=theme_key,
                    candidates=(),
                    issuer_domains=(),
                    query_terms=terms,
                    depth=depth,
                    pool=deps.pool,
                    store=deps.store,
                    now=deps.now,
                )
                if isinstance(prepared, ingest_mod.WebIngestResult):
                    reason = prepared.reason or "not_ingested"
                    tally.not_ingested[reason] = tally.not_ingested.get(reason, 0) + 1
                    _disposition(row, "not_ingested", reason)
                    continue
                extraction = prepared.extraction
                source_class = prepared.classification.source_class
                try:
                    async with session.begin_nested():
                        stored = await ingest_mod.store_web_document(
                            session,
                            prepared,
                            cfg=cfg,
                            store=deps.store,
                            backend=ctx.search_backend or deps.search_backend,
                            now=deps.now,
                        )
                    if stored.stored:
                        version_id = stored.version_id
                        if stored.state == ingest_mod.STATE_INGESTED:
                            tally.ingested += 1
                        else:
                            tally.reused_docs += 1
                        _disposition(
                            row,
                            "ingested" if stored.state == ingest_mod.STATE_INGESTED else "reused",
                            None,
                        )
                except Exception as exc:  # noqa: BLE001 - the passages are still usable
                    tally.not_ingested[type(exc).__name__] = (
                        tally.not_ingested.get(type(exc).__name__, 0) + 1
                    )
            else:
                from app.services.web_research import robots

                if getattr(fetched, "tdm_decision", None) == robots.TDM_RESERVED or not getattr(
                    fetched, "complete", False
                ):
                    _disposition(row, "not_ingested", "refused_for_analysis")
                    continue
                extraction = await extract_web_document(
                    raw=fetched.content,
                    content_class=fetched.content_class,
                    cfg=cfg,
                    url=fetched.final_url or fetched.requested_url,
                    charset=fetched.charset,
                    js_required=bool(getattr(fetched, "js_required", False)),
                    query_terms=terms,
                    depth=depth,
                    pool=deps.pool,
                    candidates=(),
                )
                if not extraction.extracted:
                    reason = extraction.failure_code or "extraction_failed"
                    tally.not_ingested[reason] = tally.not_ingested.get(reason, 0) + 1
                    _disposition(row, "not_ingested", reason)
                    continue
                source_class = classify_source(
                    fetched.final_url or fetched.requested_url,
                    issuer_domains=(),
                    licence_signals=extraction.metadata.licence_signals,
                ).source_class
        except Exception as exc:  # noqa: BLE001 - a page that fails costs itself
            name = type(exc).__name__
            tally.not_ingested[name] = tally.not_ingested.get(name, 0) + 1
            _disposition(row, "not_ingested", name[:60])
            continue

        tally.pages_extracted += 1
        if source_class:
            tally.source_classes[source_class] = tally.source_classes.get(source_class, 0) + 1
        if scored.candidate.query_id is None or scored.candidate.query_id not in meta:
            continue
        q, _outcome = meta[scored.candidate.query_id]
        attempt = getattr(fetched, "attempt_id", None)
        page = _Page(
            url=fetched.canonical_url or fetched.final_url or item.url,
            # The page actually READ is on the FINAL url's host (after redirects); the host
            # the search returned and the page's own canonical claim are kept too, so an open
            # wire or user-content host reached by redirect cannot hide behind a clean result.
            domain=host_of(fetched.final_url or item.url),
            other_hosts=tuple(
                dict.fromkeys(
                    h
                    for h in (host_of(item.url), host_of(fetched.canonical_url))
                    if h
                )
            ),
            candidate=scored.candidate,
            query_key=q.key,
            query_origin=q.origin,
            template_version=q.request.template_version,
            provider=str(provider_name) if provider_name else None,
            attempt_id=str(attempt) if attempt else None,
            source_class=source_class,
            version_id=version_id,
            suspect=bool(extraction.injection_suspect),
        )
        if page.suspect:
            # Hostile or manipulated text names nobody (its passages could never be A3
            # evidence either): the page is stored as evidence of the attack, not mined.
            tally.pages_suspect += 1
            continue
        paragraphs, tables = _blocks_and_tables(extraction)
        # Off the event loop: the work is bounded (extract_mentions caps it), and a worker
        # that shares the API's process must keep answering while a page is read.
        mentions, stats = await asyncio.to_thread(ce.extract_mentions, paragraphs, tables, vocab)
        tally.mentions += len(mentions)
        tally.name_only_dropped += stats.name_only_dropped
        tally.rejected_names += stats.rejected_names
        if mentions:
            tally.pages_with_mentions += 1
        chunk_index = await _chunk_index(session, version_id) if mentions else []
        kept = mentions[:MAX_MENTIONS_PER_PAGE]
        # The substring scans over every chunk are CPU work proportional to the page: off the
        # loop, like the extraction itself.
        found = await asyncio.to_thread(lambda: [_find_chunk(chunk_index, m) for m in kept])
        for mention, chunk_id in zip(kept, found, strict=True):
            ref = passage_ref(page.attempt_id, mention.passage)
            page.mentions.append((mention, chunk_id or ref, ref))
        pages.append(page)
    await session.flush()


# --------------------------------------------------------------------------- #
# Leads
# --------------------------------------------------------------------------- #


@dataclass
class _Acc:
    key: str
    name: str
    ticker: str | None
    venue_raw: str | None
    isin: str | None
    sightings: list[dict[str, Any]] = field(default_factory=list)
    mentions: list[dict[str, Any]] = field(default_factory=list)
    domains: set[str] = field(default_factory=set)
    pages: set[str] = field(default_factory=set)
    first_query_text: str | None = None


def _identity_evidence_url(
    name: str, ticker: str | None, venue_raw: str | None, sightings: Sequence[dict[str, Any]]
) -> str | None:
    """The one page identity verification may read for a venue with no directory.

    ONLY an official page: a sighting on an exchange or regulator host, else the platform's
    own verified-issuer registry entry. A page the search returned on some other host — a
    domain that merely spells the company's name included — never verifies the listing of
    the company it names, so it is not offered (security review B1).
    """
    from app.services.discovery.identity import (
        SOURCE_EXCHANGE,
        SOURCE_REGULATOR,
        normalise_venue,
        publisher_kind,
    )
    from app.services.sources.verified_issuer_sources import get_verified_issuer_source

    for sighting in sightings:
        if publisher_kind(sighting.get("url"), name) in (SOURCE_EXCHANGE, SOURCE_REGULATOR):
            return str(sighting["url"])
    verified = get_verified_issuer_source(ticker, normalise_venue(venue_raw) or venue_raw)
    if verified is not None and verified.investor_relations_url:
        return str(verified.investor_relations_url)
    return None


def _dimensions(m: ce.RawMention) -> list[str]:
    dims: list[str] = []
    if m.theme_terms:
        dims.append("theme_relevance")
    if m.catalyst_terms:
        dims.append("catalysts")
    if m.risk_terms:
        dims.append("principal_downside")
    return dims


def _better_name(a: str, b: str) -> str:
    def rank(n: str) -> tuple[int, int]:
        has_form = any(w.lower().strip(".") in ce._LEGAL_FORM_WORDS for w in n.split())
        return (1 if has_form else 0, len(n))

    return a if rank(a) >= rank(b) else b


def _sighting_of(page: _Page) -> dict[str, Any]:
    return {
        "query_id": str(page.candidate.query_id) if page.candidate.query_id else None,
        "result_id": str(page.candidate.result_id) if page.candidate.result_id else None,
        "fetch_attempt_id": page.attempt_id,
        "provider": page.provider,
        "family": page.candidate.family.value,
        "query_key": page.query_key,
        "query_origin": page.query_origin,
        "template_version": page.template_version,
        "rank": page.candidate.item.rank,
        "url": page.url,
        "domain": page.domain,
        "source_class": page.source_class,
        "document_version_id": str(page.version_id) if page.version_id else None,
    }


def _mention_entry(
    page: _Page, mention: ce.RawMention, evidence_id: str, ref: str, sighting: dict[str, Any]
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "passage_ref": ref,
        "kind": mention.passage_kind,
        "method": mention.method,
        "source_class": page.source_class,
        "url": page.url,
        "domain": page.domain,
        "hosts": list(page.other_hosts),
        "dimensions": _dimensions(mention),
        "theme_terms": list(mention.theme_terms)[:6],
        "catalyst_terms": list(mention.catalyst_terms)[:6],
        "risk_terms": list(mention.risk_terms)[:6],
        "passage": mention.passage,
        "injection_suspect": page.suspect,
        "query_id": sighting["query_id"],
    }


async def _build_leads(
    session: Any,
    pages: Sequence[_Page],
    ctx: DiscoveryWebContext,
    *,
    deps: DiscoveryWebDeps,
    cfg: Any,
    tally: _Tally,
    summary: dict[str, Any],
    provider_name: Any,
) -> list[CompanyLead]:
    from app.services.discovery.identity import normalise_venue, normalised_name

    accs: dict[str, _Acc] = {}
    for page in pages:
        sighting = _sighting_of(page)
        for mention, evidence_id, ref in page.mentions:
            acc = accs.get(mention.key)
            if acc is None:
                acc = accs[mention.key] = _Acc(
                    mention.key, mention.name, mention.ticker, mention.venue_raw, mention.isin
                )
            else:
                acc.name = _better_name(acc.name, mention.name)
                acc.isin = acc.isin or mention.isin
            if page.url not in acc.pages:
                acc.pages.add(page.url)
                acc.sightings.append(sighting)
            acc.domains.add(page.domain)
            acc.mentions.append(_mention_entry(page, mention, evidence_id, ref, sighting))

    # An ISIN-only or name+venue mention joins the ticker-keyed lead of the same company.
    by_name = {normalised_name(a.name): a for a in accs.values() if a.ticker}
    merged: dict[str, _Acc] = {}
    for key, acc in accs.items():
        target = by_name.get(normalised_name(acc.name)) if not acc.ticker else None
        if target is not None and target is not acc:
            target.isin = target.isin or acc.isin
            target.mentions.extend(acc.mentions)
            for s in acc.sightings:
                if s["url"] not in target.pages:
                    target.pages.add(s["url"])
                    target.sightings.append(s)
            target.domains |= acc.domains
            continue
        merged[key] = acc

    def score(a: _Acc) -> tuple[Any, ...]:
        a3 = sum(1 for m in a.mentions if m["theme_terms"])
        return (-(1 if a3 else 0), -len(a.domains), -a3, fold(a.name))

    ordered = sorted(merged.values(), key=score)[:MAX_WEB_LEADS]
    finder = deps.find_listing
    if finder is None:
        from app.services.discovery.directories import find_listing as finder

    leads: list[CompanyLead] = []
    resolutions = 0
    for acc in ordered:
        ticker: str | None = acc.ticker
        venue_raw: str | None = acc.venue_raw
        if not venue_raw and acc.isin:
            venue_raw = ce.ISIN_COUNTRY_VENUE.get(acc.isin[:2])
        # A generic "Euronext"/"Euronext Growth" mention does not say WHICH Euronext
        # market: the directory of each decides, deterministically, in a fixed order.
        low = (venue_raw or "").lower()
        if ticker and venue_raw and low in ("euronext", "euronext growth"):
            for code in EURONEXT_VENUES:
                row, _reason = await finder(name=acc.name, ticker=ticker, venue=code, cfg=cfg)
                if row is not None:
                    venue_raw = code
                    break
        if not ticker and venue_raw and resolutions < MAX_TICKER_RESOLUTIONS:
            resolutions += 1
            venue_code = normalise_venue(venue_raw)
            row, _reason = await finder(
                name=acc.name, ticker=None, venue=venue_code, isin=acc.isin, cfg=cfg
            )
            if row is not None:
                ticker = row.ticker
        if not ticker and not venue_raw:
            tally.name_only_dropped += 1
            continue
        sightings = acc.sightings[:MAX_SIGHTINGS_PER_LEAD]
        mentions = sorted(
            acc.mentions,
            key=lambda m: (not m["theme_terms"], m["source_class"] is None, m["url"]),
        )[:MAX_MENTIONS_PER_LEAD]
        known = f"{(normalise_venue(venue_raw) or '')}:{(ticker or '').upper()}"
        block = {
            "schema": LEAD_SCHEMA,
            "extractor_version": ce.EXTRACTOR_VERSION,
            "discovery_mode": "search",
            "provider": str(provider_name) if provider_name else None,
            "sightings": sightings,
            "mentions": mentions,
            "query_ids": sorted({s["query_id"] for s in acc.sightings if s["query_id"]}),
            "families": sorted({s["family"] for s in acc.sightings}),
            "domains": sorted(acc.domains)[:12],
            "novel": known not in ctx.known_keys,
        }
        leads.append(
            CompanyLead(
                name=acc.name,
                ticker=ticker.upper() if ticker else None,
                exchange_raw=venue_raw,
                country=None,
                listing_source_url=None,
                evidence_url=_identity_evidence_url(acc.name, ticker, venue_raw, sightings),
                why=None,
                source="external_search",
                discovery_query=None,
                provider=str(provider_name) if provider_name else None,
                discovery_mode="search",
                web=block,
            )
        )
    summary["mentions"] = {
        "found": tally.mentions,
        "leads": len(leads),
        "name_only_dropped": tally.name_only_dropped,
        "rejected_names": tally.rejected_names,
    }
    return leads


# --------------------------------------------------------------------------- #
# Recall corroboration (owner rule: an LLM naming a company admits nothing)
# --------------------------------------------------------------------------- #

MAX_RECALL_VERIFICATIONS = 6
RESULTS_FETCHED_PER_RECALL = 3
ORIGIN_RECALL_VERIFICATION = "recall_verification"


@dataclass
class RecallCorroboration:
    """What the targeted verification searches found, per recalled lead."""

    #: ``web_search_queries.id`` of every EXECUTED verification query.
    executed_query_ids: frozenset[str] = frozenset()
    #: lead_id -> the lead's search provenance (``discovery_web_lead/1`` shape).
    by_lead: dict[str, dict[str, Any]] = field(default_factory=dict)
    attempted: int = 0
    notes: list[str] = field(default_factory=list)
    units: ConsumptionUnits = field(default_factory=ConsumptionUnits)


def _same_company(lead: CompanyLead, mention: ce.RawMention) -> bool:
    """A passage belongs to a recalled lead only if it is about THAT LISTING: the same
    ``venue:ticker`` when both sides have one; the same normalised name only when a side
    has no ticker to compare (a passage about another listing that shares the name is not
    corroboration)."""
    from app.services.discovery.identity import normalise_venue, normalised_name

    if mention.ticker and lead.ticker:
        return mention.ticker.upper() == lead.ticker.upper() and (
            normalise_venue(mention.venue_raw) or ""
        ) == (normalise_venue(lead.exchange_raw) or "")
    mine = normalised_name(mention.name)
    return bool(mine) and mine == normalised_name(lead.name)


async def corroborate_recall_leads(
    session: Any,
    intent: Any,
    leads: Sequence[CompanyLead],
    ctx: DiscoveryWebContext,
    *,
    cfg: Any,
    deps: DiscoveryWebDeps | None = None,
    progress: ProgressHook | None = None,
) -> RecallCorroboration:
    """Targeted verification searches for recalled (model-named) leads. **Never raises**
    (a ``progress`` abort excepted).

    A recalled company may be a final candidate only if an EXECUTED search surfaced it and a
    FETCHED page names it. Each lead (at most :data:`MAX_RECALL_VERIFICATIONS`) gets one
    budgeted query through the normal search path (``origin="recall_verification"``; the
    run's query ceiling and the daily cap apply, and a recorded query is reused on resume).
    The pages it returns are fetched and mined like any other, and only mentions of THAT
    company are kept: the model's name steers which page is read, never what the page says.
    """
    deps = deps or DiscoveryWebDeps()
    out = RecallCorroboration()
    todo = [lead for lead in leads if lead.name][:MAX_RECALL_VERIFICATIONS]
    if not todo or not stage_enabled(cfg):
        return out
    try:
        return await _corroborate(session, intent, todo, ctx, cfg, deps, progress, out)
    except _Abort as abort:
        raise abort.original from None
    except Exception as exc:  # noqa: BLE001 - uncorroborated leads are demoted, never lost
        out.notes.append(f"recall verification raised {type(exc).__name__} and was isolated")
        return out


def _grant_corroboration_allowance(budget: WebResearchBudget, leads: int, cfg: Any) -> None:
    """Room for the verification searches ON TOP of the main plan.

    Recall corroboration is the only step that can admit a model-named company, and it runs
    LAST on the run's own ceilings. On the first live critical-minerals run the main plan
    used 20 of 24 queries and 36 of 40 fetches, so only 4 of 6 verification queries could be
    issued and 4 fetches remained for 12 wanted pages: ``corroborated: 0``, and every recalled
    company was demoted for want of a budget, not for want of evidence. The allowance is one
    query per recalled lead and ``RESULTS_FETCHED_PER_RECALL`` fetches per lead — bounded, and
    never past an operator's ``V3_RUN_MAX_WEB_SEARCHES`` (which stays a hard ceiling) or the
    platform's daily cap (which the budget checks on every call).
    """
    if leads <= 0:
        return
    queries = budget.limits.max_queries + leads
    operator_cap = int(getattr(cfg, "v3_run_max_web_searches", 0) or 0)
    if operator_cap > 0:
        queries = min(queries, max(operator_cap, 0))
    budget.limits = replace(
        budget.limits,
        max_queries=max(budget.limits.max_queries, queries),
        max_fetches=budget.limits.max_fetches + RESULTS_FETCHED_PER_RECALL * leads,
    )


async def _corroborate(
    session: Any,
    intent: Any,
    leads: Sequence[CompanyLead],
    ctx: DiscoveryWebContext,
    cfg: Any,
    deps: DiscoveryWebDeps,
    progress: ProgressHook | None,
    out: RecallCorroboration,
) -> RecallCorroboration:
    from app.integrations.search import web_search_provider_from_settings
    provider = (
        web_search_provider_from_settings(cfg) if deps.provider is _UNSET else deps.provider
    )
    if provider is None or not bool(getattr(cfg, "v3_web_search_enabled", False)):
        return out
    facts = dp.facts_from_intent(intent)
    depth = _depth(cfg)
    profile = f"discovery_{depth}"
    now = deps.now or datetime.now(timezone.utc)
    today = deps.today or ctx.plan_date or now.date()
    budget = await budget_for_run(session, profile, cfg=cfg, now=now, clock=deps.clock)
    await _prime_budget(session, budget, ctx.run_id)
    _grant_corroboration_allowance(budget, len(leads), cfg)
    noun = (facts.nouns() or [("", "")])[0]
    subject = dp.noun_text(*noun) if noun[1] else ""
    planned: list[tuple[CompanyLead, dp.PlannedQuery]] = []
    for index, lead in enumerate(leads):
        request, refusal = dp._make_request(
            f"{lead.name} {subject}".strip(), None, family=QueryFamily.ENTITY, today=today,
            private_tokens=ctx.private_tokens, origin=ORIGIN_RECALL_VERIFICATION,
            version=f"{dp.DISCOVERY_TEMPLATE_VERSION}:recall_verify",
        )
        if request is None:
            out.notes.append(f"a verification query was refused ({refusal})")
            continue
        planned.append((lead, dp.PlannedQuery(request, QueryFamily.ENTITY,
                                              f"recall_verify.{index}", 1, 2000 + index,
                                              dp.FAMILY_TERMS[QueryFamily.ENTITY])))
    if not planned:
        return out
    await _progress(progress, PROGRESS_SEARCH)
    tally, box = _Tally(), _Box(budget=budget)
    search_ctx = SearchContext(
        discovery_run_id=ctx.run_id, stage=STAGE_NAME, private_tokens=ctx.private_tokens,
        budget=budget, budget_profile=profile,
    )
    async with session.begin_nested():
        pairs = await _search_batch(
            session, [q for _l, q in planned], ctx=ctx, search_ctx=search_ctx,
            provider=provider, cfg=cfg, deps=deps, now=now, tally=tally, box=box,
        )
    out.units = box.units
    out.attempted = len(pairs)
    if ctx.commit is not None:
        await ctx.commit()
    out.executed_query_ids = frozenset(
        str(o.query_id) for _q, o in pairs if o.execution.executed
    )
    rows = await _result_rows(session, [o.query_id for _q, o in pairs if o.execution.executed])
    candidates: list[SearchCandidate] = []
    meta: dict[uuid.UUID, tuple[dp.PlannedQuery, QueryOutcome]] = {}
    for q, outcome in pairs:
        if not outcome.execution.executed:
            continue
        meta[outcome.query_id] = (q, outcome)
        for item in outcome.results[:RESULTS_FETCHED_PER_RECALL]:
            row = rows.get((outcome.query_id, item.rank))
            candidates.append(SearchCandidate(
                family=q.family, item=item, query_key=q.key, query_id=outcome.query_id,
                result_id=getattr(row, "id", None)))
    selection = select_discovery_results(
        candidates, today=today, known_domains=ctx.known_domains,
        terms_by_family={QueryFamily.ENTITY: dp.FAMILY_TERMS[QueryFamily.ENTITY]},
        total=RESULTS_FETCHED_PER_RECALL * len(planned), mode=depth,
    )
    rows_by_id = {r.id: r for r in rows.values()}
    for scored in selection.selected:
        _disposition(rows_by_id.get(scored.candidate.result_id), "selected", None)
    for candidate, reason in selection.skipped:
        _disposition(rows_by_id.get(candidate.result_id), "skipped", reason)
    await session.flush()
    await _progress(progress, PROGRESS_FETCH)
    pages: list[_Page] = []
    async with session.begin_nested():
        await _fetch_phase(
            session, ctx, selection, meta, rows_by_id, theme_vocabulary(facts), facts, pages,
            budget=budget, cfg=cfg, deps=deps, tally=tally, summary={},
            theme_key=theme_key_for_run(ctx.run_id), provider_name=getattr(provider, "name", None),
            progress=progress, depth=depth,
        )
    out.units = out.units + _units_of(tally, budget, ConsumptionUnits())
    if ctx.commit is not None:
        await ctx.commit()
    for lead, _q in planned:
        sightings: list[dict[str, Any]] = []
        mentions: list[dict[str, Any]] = []
        for page in pages:
            sighting = _sighting_of(page)
            hit_here = False
            for mention, evidence_id, ref in page.mentions:
                if _same_company(lead, mention):
                    mentions.append(_mention_entry(page, mention, evidence_id, ref, sighting))
                    hit_here = True
            if hit_here:
                sightings.append(sighting)
        if sightings:
            out.by_lead[lead.lead_id] = {
                "schema": LEAD_SCHEMA,
                "extractor_version": ce.EXTRACTOR_VERSION,
                "discovery_mode": "model_recall",
                "corroborated_by": ORIGIN_RECALL_VERIFICATION,
                "provider": getattr(provider, "name", None),
                "sightings": sightings[:MAX_SIGHTINGS_PER_LEAD],
                "mentions": mentions[:MAX_MENTIONS_PER_LEAD],
                "query_ids": sorted({s["query_id"] for s in sightings if s["query_id"]}),
                "surfaced_by": sorted({s["result_id"] for s in sightings if s["result_id"]}),
            }
    return out


# --------------------------------------------------------------------------- #
# Theme key of a candidate's run (so ``search_theme_corpus`` works for its research)
# --------------------------------------------------------------------------- #


async def theme_key_for_candidate(session: Any, candidate_id: Any) -> str | None:
    """The theme key of the Discovery run a candidate came from — only when that run's web
    stage recorded one (so a flag-off or ticker run has no theme scope). Never raises."""
    try:
        from app.models.discovery import DiscoveryCandidate, DiscoveryRun

        cid = candidate_id if isinstance(candidate_id, uuid.UUID) else uuid.UUID(str(candidate_id))
        run_id = await session.scalar(
            sa.select(DiscoveryCandidate.discovery_run_id).where(DiscoveryCandidate.id == cid)
        )
        if run_id is None:
            return None
        universe = await session.scalar(
            sa.select(DiscoveryRun.universe_json).where(DiscoveryRun.id == run_id)
        )
        web = ((universe or {}).get("dynamic") or {}).get("web") or {}
        key = web.get("theme_key")
        ran = web.get("state") in (STATE_OK, STATE_DEGRADED)
        return str(key) if key and ran else None
    except Exception:  # noqa: BLE001 - a theme scope is an enhancement, never a failure
        return None


__all__ = [
    "LABEL_UNAVAILABLE",
    "LEAD_SCHEMA",
    "PROGRESS_FETCH",
    "PROGRESS_SEARCH",
    "PROGRESS_VERIFY",
    "STAGE_NAME",
    "STATE_PLAN_EMPTY",
    "STATE_STAGE_DISABLED",
    "STATE_STAGE_FAILED",
    "DiscoveryWebContext",
    "DiscoveryWebDeps",
    "DiscoveryWebResult",
    "RecallCorroboration",
    "corroborate_recall_leads",
    "passage_ref",
    "run_discovery_web_stage",
    "select_discovery_results",
    "stage_enabled",
    "theme_key_for_candidate",
    "theme_key_for_run",
    "theme_vocabulary",
]
