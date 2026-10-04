"""The company web stage — open-web W5 (spec §7.1, §19.1, §23).

``ensure_web_context(session, company, run_ctx)`` runs **after**
``ensure_core_disclosures`` and **before** ``ensure_company_indexed``, so by the time
``plan_research`` runs the web documents are ordinary ``ev:c:`` chunk evidence reachable
through ``search_company_corpus`` — the same road filings and NSM/ASX disclosures take.

THE STEPS
=========
1. **Plan** (``planner``) — a deterministic query set from verified company facts, wave 2
   (``COMPANY_DOCS`` · ``CATALYST`` · ``COMPETITIVE`` · ``INDUSTRY``) and wave 3
   (``RISK``), plus a bounded recorded model expansion. The mode picks the budget
   profile (``research_mode.WEB_PROFILE_BY_MODE``).
2. **Search** (``search.run_searches``, W1) — one batch per wave; every outcome is a
   provenance row (executed-only: a recall answer can never be written as search).
   ``ResearchBudget.check`` — its first caller — bounds the wave against the run's
   mode budget before it is issued.
3. **Select** (``selection``) — top K per family from titles/snippets/hosts; snippets
   rank and are discarded.
4. **Fetch** (``open_web_fetch``, W2) — sequentially, one session; robots, TDMRep, the
   denylist and the budget are the fetcher's.
5. **Ingest** (W3 ``prepare_web_document`` / ``store_web_document``) — extraction runs in
   the killable process pool, never on the event loop; the writes run in a SAVEPOINT.
6. **Crawl** (``crawl``) — bounded, from the fetched pages and the verified issuer's IR
   pages.
7. **Record** — every search result row ends in a disposition (``selected`` /
   ``skipped`` + reason, then ``ingested`` / ``reused`` / ``not_ingested`` /
   ``not_retrievable`` / ``fetch_failed``), and the stage returns a secret-free summary
   and the units it consumed.

INVARIANTS (spec §23.3)
=======================
* **Isolated.** The whole stage runs in a SAVEPOINT inside ``try``; any exception is
  caught and recorded as ``web_stage_failed``. It never fails the job and never touches
  the official-source steps that ran before it.
* **No masquerade.** An outage is ``web_search_unavailable``, labelled; nothing is
  substituted for it and no row says "search" for a call that was not made.
* **Cost unknown stays unknown.** Units come from the executed calls; a Tavily call
  with no credit figure marks the credits ``unreported`` (``derive_cost`` then answers
  ``None``).
* **Flag off = nothing.** With ``V3_COMPANY_WEB_RESEARCH_ENABLED`` off the stage returns
  before reading anything: no query, no fetch, no row, no summary key.
* **Web evidence is not a filing.** Key financials stay filings-only; what the stage
  ingests is labelled by W3/W4 (source class, origin, claim rules) like any web document.

Logs carry counts, codes and states — never a query, a URL or page text.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import sqlalchemy as sa

from app.core.structured_logging import log_event
from app.services.consumption import BudgetExceeded, ConsumptionUnits
from app.services.providers.contracts import QueryFamily
from app.services.research_mode import budget_for as run_budget_for
from app.services.research_mode import web_profile_for
from app.services.web_research import crawl as crawl_mod
from app.services.web_research.budget import WebResearchBudget, budget_for_run
from app.services.web_research.planner import (
    ORIGIN_LLM_EXPANSION,
    QUERY_TEMPLATE_VERSION,
    CompanyFacts,
    ExpansionResult,
    QueryPlan,
    build_plan,
    facts_from_company,
    propose_expansion,
)
from app.services.web_research.search import (
    STATE_DEGRADED,
    STATE_DISABLED,
    STATE_OK,
    STATE_UNAVAILABLE,
    SearchContext,
    SearchRunResult,
    run_searches,
)
from app.services.web_research.selection import (
    SELECTION_VERSION,
    SearchCandidate,
    SelectionContext,
    SelectionResult,
    host_of,
    select_results,
)

logger = logging.getLogger(__name__)

SUMMARY_VERSION = 1

#: The stage's own flag is off: nothing ran, nothing is recorded.
STATE_STAGE_DISABLED = "web_research_disabled"
#: An exception inside the stage (caught; the job continues).
STATE_STAGE_FAILED = "web_stage_failed"

STATE_LABELS: dict[str, str] = {
    STATE_UNAVAILABLE: (
        "Live web search unavailable — results come from official sources and model "
        "suggestions verified on exchange lists"
    ),
    STATE_STAGE_FAILED: "Web research could not be completed — results come from official sources",
}

DISPOSITION_SELECTED = "selected"
DISPOSITION_SKIPPED = "skipped"
DISPOSITION_INGESTED = "ingested"
DISPOSITION_REUSED = "reused"
DISPOSITION_NOT_INGESTED = "not_ingested"
DISPOSITION_NOT_RETRIEVABLE = "not_retrievable"
DISPOSITION_FETCH_FAILED = "fetch_failed"

#: Fraction of the fetch budget held back for the crawl (selection takes the rest).
CRAWL_RESERVE_FRACTION = 0.25
MIN_CRAWL_RESERVE = 2
#: Fetched pages used as crawl seeds, beside the verified issuer's IR pages.
MAX_RESULT_SEEDS = 3
MAX_NOT_RETRIEVABLE_LISTED = 20
MAX_CATALYST_EVIDENCE = 10

_UNSET: Any = object()

FetchFn = Callable[..., Awaitable[Any]]


@dataclass
class WebRunContext:
    """What the pipeline tells the stage about THIS run. Every field is platform-owned."""

    mode: str = "standard"
    research_job_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    #: The classification's industry label and the commodity names the company's own
    #: documents state (``subject_profile``) — closed vocabularies, never page text.
    industry: str | None = None
    themes: Sequence[str] = ()
    #: Track C stage signals: a development-stage issuer gets project-milestone queries.
    development_stage: bool = False
    #: Rule G1 tokens (portfolio holdings, uploads) a query must never contain.
    private_tokens: frozenset[str] = frozenset()
    #: The corpus search backend, so a stored document is indexed immediately.
    search_backend: Any = None


@dataclass
class StageDeps:
    """Test seams. Production leaves every field at its default."""

    provider: Any = _UNSET
    #: Replaces ``open_web_fetch`` (same signature).
    fetch: FetchFn | None = None
    #: Extra keyword arguments for ``open_web_fetch`` (``runtime``, ``resolver``, ``cfg``).
    fetch_kwargs: Mapping[str, Any] = field(default_factory=dict)
    #: A DeepSeek-shaped transport for the expansion; ``_UNSET`` builds it from settings.
    llm_transport: Any = _UNSET
    pool: Any = None
    store: Any = None
    persist_session_factory: Any = _UNSET
    today: date | None = None
    clock: Callable[[], float] = time.monotonic
    now: datetime | None = None


@dataclass
class WebStageResult:
    state: str
    summary: dict[str, Any]
    units: ConsumptionUnits = field(default_factory=ConsumptionUnits)

    @property
    def ran(self) -> bool:
        return self.state != STATE_STAGE_DISABLED


@dataclass
class _Box:
    """Units and budget as the run goes, so a failure after paid calls still reports them."""

    units: ConsumptionUnits = field(default_factory=ConsumptionUnits)
    budget: WebResearchBudget | None = None


@dataclass
class _Tally:
    """Mutable counters one stage run fills in (``summary`` is built from them)."""

    queries_planned: int = 0
    queries_executed: int = 0
    queries_cached: int = 0
    queries_failed: int = 0
    queries_refused: int = 0
    results_seen: int = 0
    selected: int = 0
    fetch_attempted: int = 0
    fetched: int = 0
    not_retrievable: int = 0
    fetch_failed: int = 0
    ingested: int = 0
    reused: int = 0
    not_ingested: dict[str, int] = field(default_factory=dict)
    pdf_pages: int = 0
    dispositions: dict[str, int] = field(default_factory=dict)
    source_classes: dict[str, int] = field(default_factory=dict)
    family_selected: dict[str, int] = field(default_factory=dict)
    family_ingested: dict[str, int] = field(default_factory=dict)
    not_retrievable_list: list[dict[str, str]] = field(default_factory=list)
    catalyst_versions: list[uuid.UUID] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    run_budget_limit_hit: str | None = None

    def disposition(self, code: str) -> None:
        self.dispositions[code] = self.dispositions.get(code, 0) + 1


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def stage_enabled(cfg: Any) -> bool:
    return bool(getattr(cfg, "v3_company_web_research_enabled", False))


def _verified_issuer(company: Any) -> Any:
    from app.services.sources.verified_issuer_sources import get_verified_issuer_source

    return get_verified_issuer_source(
        getattr(company, "ticker", None), getattr(company, "exchange", None)
    )


def _issuer_domains(verified: Any) -> tuple[str, ...]:
    if verified is None:
        return ()
    domains = [verified.official_website_domain, *verified.allowed_domains,
               *verified.document_domains]
    return tuple(dict.fromkeys(d.lower().removeprefix("www.") for d in domains if d))


def _ir_seed_urls(verified: Any) -> tuple[str, ...]:
    if verified is None:
        return ()
    return tuple(
        dict.fromkeys(
            u for u in (verified.investor_relations_url, verified.annual_reports_url,
                        verified.press_releases_url) if u
        )
    )


def _identity_terms(facts: CompanyFacts) -> tuple[str, ...]:
    terms = [facts.short_name, facts.legal_name]
    if facts.ticker and len(facts.ticker) >= 3:
        terms.append(facts.ticker)
    return tuple(dict.fromkeys(t for t in terms if t))


def _candidate_entity(company: Any, facts: CompanyFacts, domains: tuple[str, ...]) -> Any:
    from app.services.web_research.entities import CandidateEntity

    return CandidateEntity(
        name=facts.legal_name,
        company_id=getattr(company, "id", None),
        legal_entity_id=getattr(company, "legal_entity_id", None),
        short_names=(facts.short_name,) if facts.short_name != facts.legal_name else (),
        tickers=((facts.ticker, facts.venue),) if facts.ticker else (),
        domains=domains,
        sector_terms=(facts.industry,) if facts.industry else (),
    )


async def _held_urls(session: Any, company_id: Any) -> frozenset[str]:
    """Canonical URLs of the documents the company's corpus already holds."""
    from app.models.research_document import ResearchDocument, ResearchDocumentVersion

    rows = (
        await session.execute(
            sa.select(ResearchDocumentVersion.canonical_url)
            .join(
                ResearchDocument,
                ResearchDocument.id == ResearchDocumentVersion.research_document_id,
            )
            .where(ResearchDocument.company_id == company_id)
            .limit(5000)
        )
    ).scalars().all()
    return frozenset(str(u) for u in rows if u)


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


def persist_factory_for(session: Any) -> Callable[[], Any] | None:
    """Provenance in its OWN short committed transaction, on PostgreSQL only.

    Search rows are the record of paid calls (and of the platform daily cap), so they
    must survive a stage that fails or a job that is cancelled. The factory binds to the
    caller's engine; on any other dialect (the SQLite test engines) it is ``None`` and
    the rows join the caller's transaction.
    """
    try:
        bind = session.get_bind()
        if getattr(getattr(bind, "dialect", None), "name", "") != "postgresql":
            return None
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        maker = async_sessionmaker(bind=session.bind, class_=AsyncSession, expire_on_commit=False)
        return maker
    except Exception:  # noqa: BLE001 - provenance falls back to the caller's session
        return None


def _set_disposition(
    row: Any, disposition: str, reason: str | None, tally: _Tally
) -> None:
    tally.disposition(disposition if not reason else f"{disposition}:{reason}")
    if row is None:
        return
    row.disposition = disposition[:30]
    row.disposition_reason = (reason or "")[:120] or None


def _sum_units(a: ConsumptionUnits, b: ConsumptionUnits) -> ConsumptionUnits:
    return a + b


# --------------------------------------------------------------------------- #
# The stage
# --------------------------------------------------------------------------- #


async def ensure_web_context(
    session: Any,
    company: Any,
    run_ctx: WebRunContext,
    *,
    cfg: Any | None = None,
    deps: StageDeps | None = None,
) -> WebStageResult:
    """Run the company web stage. **Never raises** (cancellation excepted)."""
    if cfg is None:
        from app.core.config import settings as cfg  # noqa: PLW0127
    deps = deps or StageDeps()
    if not stage_enabled(cfg):
        return WebStageResult(STATE_STAGE_DISABLED, {})
    started = deps.clock()
    tally = _Tally()
    summary: dict[str, Any] = {
        "version": SUMMARY_VERSION,
        "state": STATE_OK,
        "mode": run_ctx.mode,
        "template_version": QUERY_TEMPLATE_VERSION,
        "selection_version": SELECTION_VERSION,
        "crawl_version": crawl_mod.CRAWL_SCORING_VERSION,
    }
    box = _Box()
    try:
        async with session.begin_nested():
            state = await _execute(
                session, company, run_ctx, cfg=cfg, deps=deps, tally=tally, summary=summary,
                box=box,
            )
        summary["state"] = state
    except Exception as exc:  # noqa: BLE001 - the stage never fails the job (spec §23.3)
        # Re-raise nothing: the SAVEPOINT already released this stage's writes.
        summary["state"] = STATE_STAGE_FAILED
        summary["error"] = type(exc).__name__
        tally.notes.append("the web stage raised and was isolated; official sources unaffected")
        log_event(
            logger, "web_stage_failed", level=logging.WARNING, error_type=type(exc).__name__
        )
    if box.budget is not None:
        # A paid call is spent whether or not its answer came back: when the stage died
        # inside ``run_searches`` the reservations are the only record of the calls made.
        units = _stage_units(box.units, box.budget, tally)
        reserved = box.budget.queries_reserved
        if reserved > units.web_search_calls:
            from dataclasses import replace

            unreported = (
                units.unreported | {"tavily_credits"}
                if summary.get("provider") == "tavily" else units.unreported
            )
            units = replace(
                units, web_search_calls=reserved, unreported=frozenset(unreported),
                instrumented=units.instrumented | {"web_search_calls"},
            )
    else:
        units = box.units
    if box.budget is not None:
        summary["budget"] = box.budget.to_dict()
    summary["cost_units"] = units.to_dict()
    summary["label"] = _label(summary)
    summary["notes"] = tally.notes[:12]
    summary["elapsed_seconds"] = round(deps.clock() - started, 3)
    summary["run_budget_limit_hit"] = tally.run_budget_limit_hit
    return WebStageResult(str(summary["state"]), summary, units)


def _label(summary: Mapping[str, Any]) -> str | None:
    state = summary.get("state")
    if state == STATE_DEGRADED:
        queries = summary.get("queries") or {}
        return (
            f"Web search incomplete ({queries.get('executed', 0)} of "
            f"{queries.get('planned', 0)} searches ran)"
        )
    return STATE_LABELS.get(str(state))


async def _execute(
    session: Any,
    company: Any,
    run_ctx: WebRunContext,
    *,
    cfg: Any,
    deps: StageDeps,
    tally: _Tally,
    summary: dict[str, Any],
    box: _Box,
) -> str:
    # 1. Flags and provider. A disabled search is a state, not an error.
    if not bool(getattr(cfg, "v3_web_search_enabled", False)):
        summary.update(provider=None)
        return STATE_DISABLED
    from app.integrations.search import web_search_provider_from_settings

    provider = (
        web_search_provider_from_settings(cfg) if deps.provider is _UNSET else deps.provider
    )
    if provider is None:
        return STATE_DISABLED
    summary["provider"] = getattr(provider, "name", None)

    # 2. Facts, budget, plan.
    verified = _verified_issuer(company)
    issuer_domains = _issuer_domains(verified)
    facts = facts_from_company(
        company, industry=run_ctx.industry, themes=run_ctx.themes,
        development_stage=run_ctx.development_stage, official_domains=issuer_domains,
    )
    profile = web_profile_for(run_ctx.mode)
    summary["profile"] = profile
    now = deps.now or datetime.now(timezone.utc)
    today = deps.today or now.date()
    budget = await budget_for_run(session, profile, cfg=cfg, now=now, clock=deps.clock)
    box.budget = budget
    limits = budget.limits
    private = frozenset(run_ctx.private_tokens)

    plan = build_plan(
        facts, mode=run_ctx.mode, max_queries=limits.max_queries, today=today,
        private_tokens=private,
    )
    expansion = await _expansion(
        cfg, deps, facts, plan, limits, private, tally,
        configured=bool(getattr(provider, "is_configured", True)),
    )
    if expansion is not None and expansion.queries:
        plan = build_plan(
            facts, mode=run_ctx.mode, max_queries=limits.max_queries, today=today,
            private_tokens=private, expansion=expansion.queries,
            expansion_origin=ORIGIN_LLM_EXPANSION,
        )
    tally.queries_planned = len(plan.queries)
    if expansion is not None:
        box.units = box.units + expansion.units

    # 3. Search, one batch per wave. ResearchBudget.check bounds each batch first.
    run_budget = run_budget_for(run_ctx.mode, cfg)
    persist = (
        persist_factory_for(session) if deps.persist_session_factory is _UNSET
        else deps.persist_session_factory
    )
    search_ctx = SearchContext(
        research_job_id=run_ctx.research_job_id,
        agent_run_id=run_ctx.agent_run_id,
        company_id=getattr(company, "id", None),
        stage="company_web",
        private_tokens=private,
        budget=budget,
        budget_profile=profile,
    )
    results: list[SearchRunResult] = []
    for wave in (2, 3):
        wave_queries = plan.by_wave(wave)
        if not wave_queries:
            continue
        wave_queries = _fit_to_run_budget(
            wave_queries, run_budget, box.units, started_at=budget.started_at,
            clock=deps.clock, tally=tally,
        )
        if not wave_queries:
            continue
        run = await run_searches(
            session, [q.request for q in wave_queries], search_ctx, provider=provider,
            cfg=cfg, now=now, persist_session_factory=persist,
        )
        results.append(run)
        box.units = box.units + run.consumption
    search_state = _combine_states(results, planned=tally.queries_planned)
    _count_queries(results, tally)
    summary["queries"] = _queries_summary(plan, tally, expansion)
    if search_state == STATE_UNAVAILABLE:
        return STATE_UNAVAILABLE

    # 4. Results → candidates (with their persisted row ids), then selection.
    outcomes = [o for r in results for o in r.outcomes]
    rows = await _result_rows(session, [o.query_id for o in outcomes])
    key_by_request = {id(q.request): q for q in plan.queries}
    candidates: list[SearchCandidate] = []
    for outcome in outcomes:
        if not outcome.execution.executed:
            continue
        planned = key_by_request.get(id(outcome.request))
        for item in outcome.results:
            row = rows.get((outcome.query_id, item.rank))
            candidates.append(
                SearchCandidate(
                    family=outcome.request.family, item=item,
                    query_key=planned.key if planned else "",
                    query_id=outcome.query_id,
                    result_id=getattr(row, "id", None),
                )
            )
    tally.results_seen = len(candidates)
    held = await _held_urls(session, getattr(company, "id", None))
    reserve = max(MIN_CRAWL_RESERVE, int(limits.max_fetches * CRAWL_RESERVE_FRACTION))
    selection = select_results(
        candidates,
        SelectionContext(
            today=today, issuer_domains=issuer_domains,
            identity_terms=_identity_terms(facts), held_urls=held,
            family_terms=plan.terms_by_family(), mode=run_ctx.mode,
            total=max(0, limits.max_fetches - reserve),
        ),
    )
    tally.selected = len(selection.selected)
    rows_by_id = {r.id: r for r in rows.values()}
    for scored in selection.selected:
        _set_disposition(rows_by_id.get(scored.candidate.result_id), DISPOSITION_SELECTED,
                         None, tally)
        fam = scored.candidate.family.value
        tally.family_selected[fam] = tally.family_selected.get(fam, 0) + 1
    for candidate, reason in selection.skipped:
        _set_disposition(rows_by_id.get(candidate.result_id), DISPOSITION_SKIPPED, reason, tally)
    await session.flush()
    summary["results"] = {
        "seen": tally.results_seen,
        "selected": tally.selected,
        "skipped_by_reason": selection.skipped_by_reason(),
        "selected_by_family": selection.selected_by_family(),
    }

    # 5. Fetch + ingest + crawl.
    fetch_state = await _fetch_and_ingest(
        session, company, run_ctx, facts, selection, rows_by_id, budget=budget,
        cfg=cfg, deps=deps, tally=tally, summary=summary, issuer_domains=issuer_domains,
        verified=verified, held=held, plan=plan, today=today,
    )
    summary["fetch"] = {
        "state": fetch_state,
        "attempted": tally.fetch_attempted,
        "fetched": tally.fetched,
        "not_retrievable": tally.not_retrievable,
        "failed": tally.fetch_failed,
        "bytes": budget.bytes_downloaded,
        "pdfs": budget.pdfs,
    }
    summary["ingest"] = {
        "ingested": tally.ingested,
        "reused": tally.reused,
        "not_ingested": dict(sorted(tally.not_ingested.items())),
        "pdf_pages": tally.pdf_pages,
    }
    summary["dispositions"] = dict(sorted(tally.dispositions.items()))
    summary["source_classes"] = dict(sorted(tally.source_classes.items()))
    summary["families"] = {
        fam: {
            "queries": plan.by_family().get(fam, 0),
            "selected": tally.family_selected.get(fam, 0),
            "ingested": tally.family_ingested.get(fam, 0),
        }
        for fam in sorted({q.family.value for q in plan.queries})
    }
    summary["not_retrievable"] = tally.not_retrievable_list[:MAX_NOT_RETRIEVABLE_LISTED]
    summary["catalyst_evidence"] = await _catalyst_evidence(session, tally.catalyst_versions)

    state = search_state
    if state == STATE_OK and (
        tally.run_budget_limit_hit
        or budget.query_refusal() is not None and tally.queries_refused
    ):
        state = STATE_DEGRADED
    log_event(
        logger,
        "web_stage_completed",
        state=state,
        queries=tally.queries_executed,
        selected=tally.selected,
        fetched=tally.fetched,
        ingested=tally.ingested,
        company_id=getattr(company, "id", None),
    )
    return state


# --------------------------------------------------------------------------- #
# Pieces of the run
# --------------------------------------------------------------------------- #


async def _expansion(
    cfg: Any,
    deps: StageDeps,
    facts: CompanyFacts,
    plan: QueryPlan,
    limits: Any,
    private: frozenset[str],
    tally: _Tally,
    configured: bool = True,
) -> ExpansionResult | None:
    if limits.max_expansion_queries <= 0 or limits.max_llm_tokens <= 0:
        return None
    if not configured:
        # An unconfigured search provider means no search will run; do not spend a model
        # call on queries nobody will issue (W5 review F5).
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
    result = await propose_expansion(
        transport, facts, plan, limit=limits.max_expansion_queries,
        max_tokens=min(600, limits.max_llm_tokens), private_tokens=private,
    )
    plan.expansion = result.to_dict()
    return result


def _fit_to_run_budget(
    queries: list[Any],
    run_budget: Any,
    spent: ConsumptionUnits,
    *,
    started_at: float,
    clock: Callable[[], float],
    tally: _Tally,
) -> list[Any]:
    """``ResearchBudget.check`` before the wave: trim it to what the run may still spend.

    The check is the budget's own (a projected total against every limit in force); a
    wave that would break one is cut to the searches that still fit, never issued whole.
    """
    spent_now = spent + ConsumptionUnits(elapsed_seconds=max(0.0, clock() - started_at))
    try:
        run_budget.check(
            spent_now, about_to_spend=ConsumptionUnits(web_search_calls=len(queries))
        )
        return queries
    except BudgetExceeded as exc:
        tally.run_budget_limit_hit = exc.limit
        if exc.limit != "max_web_searches":
            return []
        room = max(0, int(exc.allowed) - spent.web_search_calls)
        return queries[:room]


def _combine_states(results: Sequence[SearchRunResult], *, planned: int) -> str:
    if not results:
        return STATE_UNAVAILABLE if planned else STATE_OK
    states = [r.state for r in results]
    if all(s == STATE_DISABLED for s in states):
        return STATE_DISABLED
    live = [s for s in states if s != STATE_DISABLED]
    if all(s == STATE_UNAVAILABLE for s in live):
        return STATE_UNAVAILABLE
    if all(s == STATE_OK for s in live):
        return STATE_OK
    return STATE_DEGRADED


def _count_queries(results: Sequence[SearchRunResult], tally: _Tally) -> None:
    for run in results:
        for outcome in run.outcomes:
            ex = outcome.execution
            if ex.executed:
                tally.queries_executed += 1
                if outcome.from_cache:
                    tally.queries_cached += 1
            elif ex.network_call_count:
                tally.queries_failed += 1
            else:
                tally.queries_refused += 1


def _queries_summary(
    plan: QueryPlan, tally: _Tally, expansion: ExpansionResult | None
) -> dict[str, Any]:
    return {
        "planned": tally.queries_planned,
        "executed": tally.queries_executed,
        "from_cache": tally.queries_cached,
        "failed": tally.queries_failed,
        "not_issued": tally.queries_refused,
        "by_family": plan.by_family(),
        "template_keys": [q.key for q in plan.queries][:60],
        "locale_variants": sum(1 for q in plan.queries if q.locale),
        "refused_by_sanitiser": len(plan.refused),
        "expansion": expansion.to_dict() if expansion is not None else None,
    }


def _stage_units(
    units: ConsumptionUnits, budget: WebResearchBudget, tally: _Tally
) -> ConsumptionUnits:
    """The stage's consumption: search + expansion units, plus the fetches it made.

    ``elapsed_seconds`` is left out on purpose — the run record measures wall time once,
    for the whole run — and the fetch units are counted (zero is a measurement here).
    """
    from dataclasses import replace

    fetched = ConsumptionUnits(
        url_fetch_calls=budget.fetches,
        bytes_downloaded=budget.bytes_downloaded,
        pages_parsed=tally.pdf_pages,
        instrumented=frozenset({"url_fetch_calls", "bytes_downloaded", "pages_parsed"}),
    )
    return replace(units + fetched, elapsed_seconds=0.0)


def safe_url(url: str | None) -> str | None:
    """``url`` unless it contains a term the safety gate would reject (then ``None``)."""
    from app.services import safety_terms

    return url if url and safety_terms.is_safe(url) else None


async def _catalyst_evidence(
    session: Any, version_ids: Sequence[uuid.UUID]
) -> list[dict[str, Any]]:
    """Catalyst-family documents the stage stored, as controlled fields (spec §17.4).

    Read by the V2 ``news_catalyst_discovery`` section (additively): the stored
    document's own title, class, origin and date — never a search snippet.
    """
    from app.models.research_document import ResearchDocumentVersion as V
    from app.schemas.catalyst import neutralize_forbidden_terms

    ids = list(dict.fromkeys(version_ids))[:MAX_CATALYST_EVIDENCE]
    if not ids:
        return []
    rows = (
        await session.execute(sa.select(V).where(V.id.in_(ids)))
    ).scalars().all()
    order = {vid: i for i, vid in enumerate(ids)}
    out: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: order.get(r.id, 99)):
        out.append(
            {
                "document_version_id": str(row.id),
                # Third-party text: neutralised the way V2 neutralises external headlines.
                "title": neutralize_forbidden_terms(
                    " ".join(str(row.title or "").split())[:160] or None
                ),
                # A URL cannot be neutralised without changing the address, so a URL
                # that contains a gate term is DROPPED (domain and title stay): the
                # report JSON must never carry a term the safety gate rejects (W5 C-H2).
                "url": safe_url(row.canonical_url),
                "domain": neutralize_forbidden_terms(host_of(row.canonical_url)),
                "source_class": row.source_class,
                "origin_key": row.origin_key,
                "published_at": row.published_at.isoformat() if row.published_at else None,
                "published_at_source": row.published_at_source,
            }
        )
    return out


async def _fetch_and_ingest(
    session: Any,
    company: Any,
    run_ctx: WebRunContext,
    facts: CompanyFacts,
    selection: SelectionResult,
    rows_by_id: dict[Any, Any],
    *,
    budget: WebResearchBudget,
    cfg: Any,
    deps: StageDeps,
    tally: _Tally,
    summary: dict[str, Any],
    issuer_domains: tuple[str, ...],
    verified: Any,
    held: frozenset[str],
    plan: QueryPlan,
    today: date,
) -> str:
    """Fetch the selected results, ingest them, then crawl. Returns the fetch state."""
    from app.services.web_research import fetch as fetch_mod
    from app.services.web_research import ingest as ingest_mod

    if not bool(getattr(cfg, "v3_web_fetch_enabled", False)):
        tally.notes.append("open-web fetch is disabled; search results were not retrieved")
        for scored in selection.selected:
            _set_disposition(
                rows_by_id.get(scored.candidate.result_id), DISPOSITION_NOT_INGESTED,
                fetch_mod.FAILURE_FETCH_DISABLED, tally,
            )
        return fetch_mod.FAILURE_FETCH_DISABLED

    fetch_fn: FetchFn = deps.fetch or fetch_mod.open_web_fetch
    fetch_kwargs = dict(deps.fetch_kwargs)
    context = fetch_mod.WebFetchContext(research_job_id=run_ctx.research_job_id)
    entity = _candidate_entity(company, facts, issuer_domains)
    depth = "deep" if run_ctx.mode in ("deep", "max") else "standard"
    family_terms = plan.terms_by_family()
    company_id = getattr(company, "id", None)
    stopped = False
    seeds: list[crawl_mod.CrawlSeed] = []

    async def ingest_one(
        fetched: Any, *, family: QueryFamily, row: Any = None
    ) -> Any:
        """Prepare (pool, no DB) then store (SAVEPOINT). Returns the WebIngestResult."""
        prepared = await ingest_mod.prepare_web_document(
            fetched, cfg=cfg, provider=None, company_id=company_id,
            candidates=(entity,), issuer_domains=issuer_domains,
            query_terms=tuple(family_terms.get(family, ())), depth=depth,
            pool=deps.pool, store=deps.store, now=deps.now,
        )
        if isinstance(prepared, ingest_mod.WebIngestResult):
            return prepared
        async with session.begin_nested():
            return await ingest_mod.store_web_document(
                session, prepared, cfg=cfg, store=deps.store,
                backend=run_ctx.search_backend, now=deps.now,
            )

    def note_ingest(result: Any, family: QueryFamily, row: Any = None) -> bool:
        if getattr(result, "stored", False):
            if result.state == ingest_mod.STATE_INGESTED:
                tally.ingested += 1
            else:
                tally.reused += 1
            tally.pdf_pages += int(getattr(result, "pages", 0) or 0)
            if result.source_class:
                tally.source_classes[result.source_class] = (
                    tally.source_classes.get(result.source_class, 0) + 1
                )
            tally.family_ingested[family.value] = tally.family_ingested.get(family.value, 0) + 1
            if family is QueryFamily.CATALYST and result.version_id is not None:
                tally.catalyst_versions.append(result.version_id)
            _set_disposition(
                row, DISPOSITION_INGESTED if result.state == ingest_mod.STATE_INGESTED
                else DISPOSITION_REUSED, None, tally,
            )
            return result.state == ingest_mod.STATE_INGESTED
        reason = getattr(result, "reason", None) or "not_ingested"
        tally.not_ingested[reason] = tally.not_ingested.get(reason, 0) + 1
        _set_disposition(row, DISPOSITION_NOT_INGESTED, reason, tally)
        return False

    for scored in selection.selected:
        row = rows_by_id.get(scored.candidate.result_id)
        refusal = budget.fetch_refusal()
        if refusal is not None or stopped:
            _set_disposition(row, DISPOSITION_SKIPPED, refusal or "stopped", tally)
            if refusal and not stopped:
                tally.notes.append(f"fetching stopped: {refusal}")
            stopped = True
            continue
        tally.fetch_attempted += 1
        item = scored.candidate.item
        try:
            fetched = await fetch_fn(
                session, item.url, context=context, budget=budget,
                origin=fetch_mod.ORIGIN_SEARCH, search_result_id=scored.candidate.result_id,
                **fetch_kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - one bad URL costs that URL
            tally.fetch_failed += 1
            _set_disposition(row, DISPOSITION_FETCH_FAILED, type(exc).__name__[:60], tally)
            continue
        if fetched.status == fetch_mod.STATUS_NOT_RETRIEVABLE:
            tally.not_retrievable += 1
            reason = fetched.failure_code or "not_retrievable"
            _set_disposition(row, DISPOSITION_NOT_RETRIEVABLE, reason, tally)
            tally.not_retrievable_list.append({"domain": host_of(item.url), "reason": reason})
            continue
        if not fetched.ok:
            tally.fetch_failed += 1
            _set_disposition(
                row, DISPOSITION_FETCH_FAILED, fetched.failure_code or fetched.status, tally
            )
            continue
        tally.fetched += 1
        try:
            result = await ingest_one(fetched, family=scored.candidate.family, row=row)
        except Exception as exc:  # noqa: BLE001 - a document that fails to store costs itself
            tally.not_ingested[type(exc).__name__] = (
                tally.not_ingested.get(type(exc).__name__, 0) + 1
            )
            _set_disposition(row, DISPOSITION_NOT_INGESTED, type(exc).__name__[:60], tally)
            continue
        note_ingest(result, scored.candidate.family, row)
        if len(seeds) < MAX_RESULT_SEEDS and fetched.content_class == "html":
            seeds.append(
                crawl_mod.CrawlSeed(item.url, scored.candidate.family, fetched,
                                    scored.candidate.result_id)
            )
    await session.flush()

    # Crawl: the fetched pages' links, and the verified issuer's own IR pages.
    if budget.fetch_refusal() is None:
        for url in _ir_seed_urls(verified):
            seeds.append(crawl_mod.CrawlSeed(url, QueryFamily.COMPANY_DOCS))

        async def on_fetched(page: Any, link: crawl_mod.CrawlLink) -> bool | None:
            if page.content_class == "html" and not link.is_document:
                return None  # a navigation page: it adds no chunk by design
            try:
                result = await ingest_one(page, family=link.family)
            except Exception:  # noqa: BLE001
                return False
            return note_ingest(result, link.family)

        window_years = frozenset({today.year, today.year - 1, today.year - 2})
        flat_terms = tuple(
            dict.fromkeys(t for terms in family_terms.values() for t in terms)
        )
        crawl_result = await crawl_mod.crawl(
            seeds,
            session=session,
            context=context,
            budget=budget,
            ctx=crawl_mod.CrawlContext(
                today=today, issuer_domains=issuer_domains,
                identity_terms=_identity_terms(facts), query_terms=flat_terms,
                window_years=window_years, held_urls=held,
            ),
            on_fetched=on_fetched,
            bounds=crawl_mod.CrawlBounds(
                max_documents=20 if run_ctx.mode in ("deep", "max") else 10,
                max_pages_per_domain=min(8, budget.limits.max_per_domain),
            ),
            fetch=fetch_fn,
            fetch_kwargs=fetch_kwargs,
        )
        tally.fetched += len(crawl_result.fetches)
        tally.fetch_attempted += len(crawl_result.fetches)
        summary["crawl"] = crawl_result.to_dict()
        await session.flush()
    return "ok"


__all__ = [
    "DISPOSITION_FETCH_FAILED",
    "DISPOSITION_INGESTED",
    "DISPOSITION_NOT_INGESTED",
    "DISPOSITION_NOT_RETRIEVABLE",
    "DISPOSITION_REUSED",
    "DISPOSITION_SELECTED",
    "DISPOSITION_SKIPPED",
    "STATE_LABELS",
    "STATE_STAGE_DISABLED",
    "STATE_STAGE_FAILED",
    "SUMMARY_VERSION",
    "StageDeps",
    "WebRunContext",
    "persist_factory_for",
    "safe_url",
    "WebStageResult",
    "ensure_web_context",
    "stage_enabled",
]
