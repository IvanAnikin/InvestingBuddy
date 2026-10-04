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

import hashlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
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
    STATE_DISABLED,
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
    return "deep" if str(getattr(cfg, "v3_discovery_web_depth", "standard")).lower() == "deep" \
        else "standard"


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


def _units_of(tally: _Tally, budget: WebResearchBudget, units: ConsumptionUnits) -> ConsumptionUnits:
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
        await session.execute(
            sa.select(Q)
            .where(
                Q.discovery_run_id == run_id,
                Q.stage == STAGE_NAME,
                Q.request_hash.in_(list(hashes)),
            )
            .order_by(Q.created_at, Q.id)
        )
    ).scalars().all()
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
            await session.execute(
                sa.select(R).where(R.query_id == row.id).order_by(R.rank)
            )
        ).scalars().all():
            results.append(
                SearchResultItem(
                    rank=int(r.rank), url=r.url, canonical_url=r.canonical_url or r.url,
                    domain=r.domain or host_of(r.url), title=r.title, snippet=r.snippet,
                    published_hint=r.published_hint, language_hint=r.language_hint,
                    provider_score=r.provider_score,
                )
            )
    if row.executed:
        cached = str(row.served_from_query_id) if row.served_from_query_id else None
        execution = SearchExecution(
            provider=row.provider, executed=True,
            provider_request_id=row.provider_request_id or "reused",
            http_status=row.http_status or 200, latency_ms=int(row.latency_ms or 0),
            result_count=int(row.result_count or 0), cost_units=dict(row.cost_units_json or {}),
            network_call_count=0 if cached else max(1, int(row.network_call_count or 1)),
            cached_from=cached,
        )
    else:
        execution = SearchExecution.not_executed(
            row.provider, row.error_code or "failed", http_status=row.http_status,
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
        if row.network_call_count and box.budget is not None:
            # The run's per-run query ceiling carries across attempts.
            box.budget.queries_reserved += int(row.network_call_count)
    if todo:
        persist = (
            _persist_factory(session) if deps.persist_session_factory is _UNSET
            else deps.persist_session_factory
        )
        run = await run_searches(
            session, [q.request for q in todo], search_ctx, provider=provider, cfg=cfg,
            now=now, persist_session_factory=persist,
        )
        box.units = box.units + run.consumption
        by_request = {id(q.request): q for q in todo}
        for outcome in run.outcomes:
            pairs.append((by_request[id(outcome.request)], outcome))
    order = {id(q): i for i, q in enumerate(queries)}
    pairs.sort(key=lambda p: order[id(p[0])])
    return pairs


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
    ordered = sorted(
        candidates, key=lambda c: (order.get(c.family, 99), c.item.rank, c.url)
    )
    seen: set[str] = set()
    pools: dict[QueryFamily, list[Scored]] = {}
    ctx = SelectionContext(today=today, family_terms=dict(terms_by_family), mode=mode,
                           total=total)
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
            Scored(candidate, round(scored.score + adj, 6),
                   {**scored.components, "domain_novelty": adj}, scored.source_class)
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
                -(s.score - DUPLICATE_DOMAIN_PENALTY
                  * per_domain.get(domain_of(s.candidate.url), 0)),
                s.candidate.item.rank, s.candidate.url,
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
            session, intent, ctx, cfg=cfg, deps=deps, tally=tally, summary=summary, box=box,
            progress=progress,
        )
    except _Abort as abort:
        raise abort.original from None
    except Exception as exc:  # noqa: BLE001 - the stage never fails the run (spec §23.3)
        state = STATE_STAGE_FAILED
        leads, executed_ids = [], frozenset()
        summary["error"] = type(exc).__name__
        tally.notes.append("the web stage raised and was isolated; no web leads were produced")
        log_event(logger, "discovery_web_stage_failed", level=logging.WARNING,
                  error_type=type(exc).__name__)
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
        return (f"Web search incomplete ({q.get('executed', 0)} of "
                f"{q.get('planned', 0)} searches ran)")
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

    provider = (
        web_search_provider_from_settings(cfg) if deps.provider is _UNSET else deps.provider
    )
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
    today = deps.today or now.date()
    budget = await budget_for_run(session, profile, cfg=cfg, now=now, clock=deps.clock)
    box.budget = budget
    limits = budget.limits
    private = frozenset(ctx.private_tokens)
    plan = dp.build_discovery_plan(
        facts, mode=depth, max_queries=limits.max_queries, today=today, private_tokens=private,
    )
    if not plan.queries:
        summary["reason"] = "intent_names_nothing_a_search_can_be_built_from"
        return STATE_PLAN_EMPTY, [], frozenset()
    expansion = await _expansion(cfg, deps, facts, plan, limits, private)
    if expansion is not None:
        box.units = box.units + expansion.units
        if expansion.queries:
            plan = dp.build_discovery_plan(
                facts, mode=depth, max_queries=limits.max_queries, today=today,
                private_tokens=private, expansion=expansion.queries,
            )
            plan.expansion = expansion.to_dict()
    tally.planned = len(plan.queries)

    # 3. Search (wave 1), then saturation follow-ups. Every call is a provenance row.
    await _progress(progress, PROGRESS_SEARCH)
    search_ctx = SearchContext(
        discovery_run_id=ctx.run_id, stage=STAGE_NAME, private_tokens=private,
        budget=budget, budget_profile=profile,
    )
    pairs = await _search_batch(
        session, plan.queries, ctx=ctx, search_ctx=search_ctx, provider=provider, cfg=cfg,
        deps=deps, now=now, tally=tally, box=box,
    )
    saturated = [
        q for q, o in pairs
        if o.execution.executed and dp.is_saturated(_domains_of(o), ctx.known_domains)
    ]
    if saturated and ctx.known_domains:
        followups = dp.build_followups(
            plan, saturated, ctx.known_domains,
            limit=max(0, limits.max_queries - len(plan.queries)),
            result_domains={q.key: _domains_of(o) for q, o in pairs},
        )
        tally.saturated = [q.key for q in saturated]
        tally.followups = len(followups)
        tally.excluded_domains = (
            len(followups[0].request.exclude_domains) if followups else 0
        )
        extra = await _search_batch(
            session, followups, ctx=ctx, search_ctx=search_ctx, provider=provider, cfg=cfg,
            deps=deps, now=now, tally=tally, box=box,
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
                SearchCandidate(family=q.family, item=item, query_key=q.key,
                                query_id=outcome.query_id, result_id=getattr(row, "id", None))
            )
    tally.results_seen = len(candidates)
    selection = select_discovery_results(
        candidates, today=today, known_domains=ctx.known_domains,
        terms_by_family={f: tuple(dict.fromkeys(t for q, _ in pairs if q.family is f
                                                for t in q.terms)) or dp.FAMILY_TERMS[f]
                         for f in dp.DISCOVERY_FAMILIES},
        total=limits.max_fetches, mode=depth,
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
            session, ctx, selection, meta, rows_by_id, vocab, facts, pages,
            budget=budget, cfg=cfg, deps=deps, tally=tally, summary=summary,
            theme_key=theme_key_for_run(ctx.run_id), provider_name=summary.get("provider"),
            progress=progress, depth=depth,
        )
    if ctx.commit is not None:
        await ctx.commit()

    # 6. Leads.
    await _progress(progress, PROGRESS_VERIFY)
    leads = await _build_leads(
        session, pages, ctx, deps=deps, cfg=cfg, tally=tally, summary=summary,
        provider_name=summary.get("provider"),
    )
    summary["results"] = {
        "seen": tally.results_seen, "selected": tally.selected,
        "skipped_by_reason": dict(sorted(tally.skipped.items())),
        "selected_by_family": dict(sorted(tally.family_selected.items())),
    }
    summary["fetch"] = {
        "attempted": tally.fetch_attempted, "fetched": tally.fetched,
        "not_retrievable": tally.not_retrievable, "failed": tally.fetch_failed,
        "bytes": budget.bytes_downloaded, "pdfs": budget.pdfs,
    }
    summary["pages"] = {
        "extracted": tally.pages_extracted,
        "with_mentions": tally.pages_with_mentions,
        "injection_suspect": tally.pages_suspect,
        "ingested": tally.ingested, "reused": tally.reused_docs,
        "not_ingested": dict(sorted(tally.not_ingested.items())),
        "source_classes": dict(sorted(tally.source_classes.items())),
    }
    summary["not_retrievable"] = tally.not_retrievable_list[:20]
    log_event(
        logger, "discovery_web_stage_completed", state=state, queries=tally.executed,
        selected=tally.selected, fetched=tally.fetched, leads=len(leads),
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
        await session.execute(
            sa.select(WebSearchResult).where(WebSearchResult.query_id.in_(list(query_ids)))
        )
    ).scalars().all()
    return {(r.query_id, int(r.rank)): r for r in rows}


async def _expansion(
    cfg: Any, deps: DiscoveryWebDeps, facts: dp.DiscoveryFacts, plan: dp.DiscoveryPlan,
    limits: Any, private: frozenset[str],
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
        transport, facts, plan, limit=limits.max_expansion_queries,
        max_tokens=min(600, limits.max_llm_tokens), private_tokens=private,
    )


# --------------------------------------------------------------------------- #
# Fetch, extract, mentions
# --------------------------------------------------------------------------- #


def _blocks_and_tables(extraction: Any) -> tuple[list[str], list[list[list[str]]]]:
    body = getattr(extraction, "extraction", None)
    blocks = [str(getattr(b, "text", "") or "") for b in (getattr(body, "blocks", None) or [])]
    tables = [
        [[str(c or "") for c in row] for row in (getattr(t, "rows", None) or [])]
        for t in (getattr(body, "tables", None) or [])
    ]
    if not any(b.strip() for b in blocks):
        blocks = ce.paragraphs_of(getattr(extraction, "main_text", ""))
    return blocks, tables


async def _chunk_evidence_id(
    session: Any, version_id: uuid.UUID | None, mention: ce.RawMention
) -> str | None:
    """The corpus chunk holding the passage, when the page was stored (``ev:c:`` id)."""
    if version_id is None:
        return None
    from app.models.research_chunk import ResearchDocumentChunk as C

    rows = (
        await session.execute(
            sa.select(C.chunk_id, C.text)
            .where(C.research_document_version_id == version_id)
            .order_by(C.ordinal).limit(2000)
        )
    ).all()
    needle = fold(mention.passage)[:60]
    name = fold(mention.name)
    for chunk_id, text in rows:
        folded = fold(str(text or ""))
        if needle and needle in folded:
            return str(chunk_id)[:120]
    for chunk_id, text in rows:
        if name and name in fold(str(text or "")):
            return str(chunk_id)[:120]
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
            _disposition(rows_by_id.get(scored.candidate.result_id), "not_ingested",
                         fetch_mod.FAILURE_FETCH_DISABLED)
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
                session, item.url, context=fetch_ctx, budget=budget,
                origin=fetch_mod.ORIGIN_SEARCH, search_result_id=scored.candidate.result_id,
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
                    fetched, cfg=cfg, provider=None, subject_scope="theme",
                    theme_key=theme_key, candidates=(), issuer_domains=(),
                    query_terms=terms, depth=depth, pool=deps.pool, store=deps.store,
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
                            session, prepared, cfg=cfg, store=deps.store,
                            backend=ctx.search_backend, now=deps.now,
                        )
                    if stored.stored:
                        version_id = stored.version_id
                        if stored.state == ingest_mod.STATE_INGESTED:
                            tally.ingested += 1
                        else:
                            tally.reused_docs += 1
                        _disposition(row, "ingested" if stored.state ==
                                     ingest_mod.STATE_INGESTED else "reused", None)
                except Exception as exc:  # noqa: BLE001 - the passages are still usable
                    tally.not_ingested[type(exc).__name__] = (
                        tally.not_ingested.get(type(exc).__name__, 0) + 1
                    )
            else:
                from app.services.web_research import robots

                if (getattr(fetched, "tdm_decision", None) == robots.TDM_RESERVED
                        or not getattr(fetched, "complete", False)):
                    _disposition(row, "not_ingested", "refused_for_analysis")
                    continue
                extraction = await extract_web_document(
                    raw=fetched.content, content_class=fetched.content_class, cfg=cfg,
                    url=fetched.final_url or fetched.requested_url, charset=fetched.charset,
                    js_required=bool(getattr(fetched, "js_required", False)),
                    query_terms=terms, depth=depth, pool=deps.pool, candidates=(),
                )
                if not extraction.extracted:
                    reason = extraction.failure_code or "extraction_failed"
                    tally.not_ingested[reason] = tally.not_ingested.get(reason, 0) + 1
                    _disposition(row, "not_ingested", reason)
                    continue
                source_class = classify_source(
                    fetched.final_url or fetched.requested_url, issuer_domains=(),
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
        q, _outcome = meta[scored.candidate.query_id]
        attempt = getattr(fetched, "attempt_id", None)
        page = _Page(
            url=fetched.canonical_url or fetched.final_url or item.url,
            domain=host_of(item.url), candidate=scored.candidate, query_key=q.key,
            query_origin=q.origin, template_version=q.request.template_version,
            provider=str(provider_name) if provider_name else None,
            attempt_id=str(attempt) if attempt else None, source_class=source_class,
            version_id=version_id, suspect=bool(extraction.injection_suspect),
        )
        if page.suspect:
            # Hostile or manipulated text names nobody (its passages could never be A3
            # evidence either): the page is stored as evidence of the attack, not mined.
            tally.pages_suspect += 1
            continue
        paragraphs, tables = _blocks_and_tables(extraction)
        mentions, stats = ce.extract_mentions(paragraphs, tables, vocab)
        tally.mentions += len(mentions)
        tally.name_only_dropped += stats.name_only_dropped
        tally.rejected_names += stats.rejected_names
        if mentions:
            tally.pages_with_mentions += 1
        for mention in mentions[:MAX_MENTIONS_PER_PAGE]:
            ref = passage_ref(page.attempt_id, mention.passage)
            evidence_id = await _chunk_evidence_id(session, version_id, mention) or ref
            page.mentions.append((mention, evidence_id, ref))
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
    from app.services.discovery.identity import normalised_name, normalise_venue

    accs: dict[str, _Acc] = {}
    for page in pages:
        sighting = {
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
            acc.mentions.append({
                "evidence_id": evidence_id,
                "passage_ref": ref,
                "kind": mention.passage_kind,
                "method": mention.method,
                "source_class": page.source_class,
                "url": page.url,
                "domain": page.domain,
                "dimensions": _dimensions(mention),
                "theme_terms": list(mention.theme_terms)[:6],
                "catalyst_terms": list(mention.catalyst_terms)[:6],
                "risk_terms": list(mention.risk_terms)[:6],
                "passage": mention.passage,
                "injection_suspect": page.suspect,
                "query_id": sighting["query_id"],
            })

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
        ticker, venue_raw = acc.ticker, acc.venue_raw
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
            code = normalise_venue(venue_raw)
            row, _reason = await finder(name=acc.name, ticker=None, venue=code, isin=acc.isin,
                                        cfg=cfg)
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
                name=acc.name, ticker=ticker.upper() if ticker else None,
                exchange_raw=venue_raw, country=None, listing_source_url=None,
                evidence_url=(sightings[0]["url"] if sightings else None), why=None,
                source="external_search", discovery_query=None,
                provider=str(provider_name) if provider_name else None,
                discovery_mode="search", web=block,
            )
        )
    summary["mentions"] = {
        "found": tally.mentions, "leads": len(leads),
        "name_only_dropped": tally.name_only_dropped,
        "rejected_names": tally.rejected_names,
    }
    return leads


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
    "passage_ref",
    "run_discovery_web_stage",
    "select_discovery_results",
    "stage_enabled",
    "theme_key_for_candidate",
    "theme_key_for_run",
    "theme_vocabulary",
]
