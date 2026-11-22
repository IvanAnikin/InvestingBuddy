"""Coverage for one company: the report block and the obscurity input — V3.21 slice 21.7.

WHAT A REPORT GETS (``evidence_quality_and_gaps.independent_research``)
=======================================================================
* The coverage pages whose name key EQUALS the company's (``names.name_key``): Edison
  ``/equity/`` pages from the sitemap index, and search-only provider pages stored by an
  earlier search. Each with provider, URL, date and how it was found.
* The note that sponsored research is paid for by the issuer.
* Absence phrased as what was looked at — "none found in the indexed providers (Edison
  sitemap; search-only providers not searched)" — never "no coverage exists".
* ``state: index_not_built`` when the Edison refresh has never stored a page: then the
  obscurity component is UNKNOWN, not zero.

THE SEARCH-ONLY PROVIDERS (Redeye, Research Tree, mwb research)
===============================================================
One ``include_domains`` web search per company per :data:`SEARCH_INTERVAL_DAYS`, and
only with ``V3_WEB_SEARCH_ENABLED``. The throttle is a ``source_poll_state`` row keyed
``research_coverage_search.<company id>``: ``last_ok_at`` = the last search that
executed; ``last_started_at`` = the last attempt (a failed attempt waits a day). A hit is
kept only when it is on a provider host AND its title or URL names the company (all of its
name tokens, contiguously); a kept hit is stored in ``research_coverage_pages`` with
``discovered_via = search``. Search titles are untrusted display text, clipped; a search
snippet is never stored. With search off, or unavailable (no key, quota), the block says
"search unavailable" / "not searched" and nothing fails.

OPTIONAL INGEST
===============
With ``V3_WEB_FETCH_ENABLED``, at most :data:`MAX_INGESTS_PER_RUN` (1) Edison equity
page per run goes through ``open_web_fetch`` (robots.txt, TDM, denylist, pinned
transport) and ``ingest_web_document``: a T5 ``research_consultancy`` document in the
corpus. Its claims are never company facts without a primary source.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa

from app.core.structured_logging import log_event
from app.models.research_coverage import ResearchCoveragePage
from app.models.source_poll_state import SourcePollState
from app.services.research_coverage.names import key_from_tokens, mentions, name_tokens
from app.services.research_coverage.providers import (
    BY_KEY,
    EDISON,
    SEARCH_DOMAINS,
    SEARCH_ONLY,
    SPONSORED_NOTE,
    provider_for_host,
)
from app.services.research_coverage.sitemap import CoverageEntry, upsert_pages

logger = logging.getLogger(__name__)

VERSION = "2026-11-22.1"
SEARCH_INTERVAL_DAYS = 30
SEARCH_RETRY_HOURS = 24
SEARCH_TASK_PREFIX = "research_coverage_search."
MAX_SEARCH_RESULTS = 5
MAX_PAGES_PER_PROVIDER = 5
MAX_INGESTS_PER_RUN = 1
_TITLE_MAX = 200

STATE_FOUND = "found"
STATE_NONE_FOUND = "none_found"
STATE_INDEX_NOT_BUILT = "index_not_built"

SEARCH_DISABLED = "disabled"          # V3_WEB_SEARCH_ENABLED off
SEARCH_THROTTLED = "throttled"        # searched within 30 days (or failed within a day)
SEARCH_SEARCHED = "searched"          # one search ran now
SEARCH_UNAVAILABLE = "unavailable"    # asked, nothing executed (no key, quota, outage)
SEARCH_NOT_SEARCHABLE = "not_searchable"  # the name is too weak to search or match on

_SEARCH_LABEL = {
    SEARCH_DISABLED: "search-only providers not searched, web search is off",
    SEARCH_THROTTLED: "search-only providers searched within the last 30 days",
    SEARCH_SEARCHED: "search-only providers searched",
    SEARCH_UNAVAILABLE: "search-only providers not searched, search unavailable",
    SEARCH_NOT_SEARCHABLE: "search-only providers not searched, name too generic to match",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat() if value is not None else None


def _clip_title(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"[\x00-\x1f\x7f<>]", " ", str(value))
    return " ".join(text.split())[:_TITLE_MAX] or None


# ── reads ────────────────────────────────────────────────────────────────── #


async def index_status(session: Any) -> dict[str, Any]:
    """Whether the Edison sitemap index has ever been stored, and when last."""
    row = (await session.execute(
        sa.select(sa.func.count(ResearchCoveragePage.id),
                  sa.func.max(ResearchCoveragePage.last_seen_at))
        .where(ResearchCoveragePage.provider == EDISON.key,
               ResearchCoveragePage.discovered_via == "sitemap"))).one()
    pages, latest = int(row[0] or 0), _aware(row[1])
    return {"provider": EDISON.key, "name": EDISON.name, "method": "sitemap",
            "state": "ready" if pages else "never_refreshed",
            "refreshed_at": _iso(latest), "pages_indexed": pages}


async def pages_for_key(session: Any, key: str) -> list[ResearchCoveragePage]:
    """Coverage pages for one name key: Edison company pages and search-found pages."""
    rows = await session.execute(
        sa.select(ResearchCoveragePage)
        .where(ResearchCoveragePage.name_key == key,
               sa.or_(ResearchCoveragePage.page_kind == "equity",
                      ResearchCoveragePage.discovered_via == "search"))
        .order_by(ResearchCoveragePage.provider, ResearchCoveragePage.lastmod.desc(),
                  ResearchCoveragePage.url)
        .limit(100))
    return list(rows.scalars().all())


# ── search-only providers ────────────────────────────────────────────────── #


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str | None = None
    published: datetime | None = None


@dataclass
class SearchAnswer:
    executed: bool
    state: str
    hits: list[SearchHit] = field(default_factory=list)


#: ``(session, query, include_domains, company_id, cfg) -> SearchAnswer`` — the seam.
Searcher = Callable[[Any, str, tuple[str, ...], uuid.UUID | None, Any],
                    Awaitable[SearchAnswer]]


async def default_searcher(session: Any, query: str, domains: tuple[str, ...],
                           company_id: uuid.UUID | None, cfg: Any) -> SearchAnswer:
    """One host-restricted search through ``run_searches`` (gate, budget, provenance)."""
    from app.services.providers.contracts import QueryFamily, SearchRequest
    from app.services.web_research.search import SearchContext, run_searches
    from app.services.web_research.stage import persist_factory_for

    request = SearchRequest(query=query, family=QueryFamily.COMPETITIVE,
                            max_results=MAX_SEARCH_RESULTS, include_domains=domains,
                            origin="research_coverage", template_version=VERSION)
    run = await run_searches(
        session, [request], SearchContext(company_id=company_id, stage="research_coverage"),
        cfg=cfg, persist_session_factory=persist_factory_for(session))
    outcome = run.outcomes[0] if run.outcomes else None
    if outcome is None or not outcome.execution.executed:
        return SearchAnswer(executed=False, state=run.state)
    return SearchAnswer(executed=True, state=run.state, hits=[
        SearchHit(url=r.url, title=r.title, published=r.published_hint)
        for r in outcome.results])


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _search_page_kind(url: str) -> str:
    path = urlsplit(url).path.lower()
    return "equity" if re.search(r"/(company|companies|equity|aktie|unternehmen)/",
                                 path) else "note"


def _search_slug(url: str) -> str | None:
    segments = [s for s in urlsplit(url).path.split("/") if s]
    for seg in reversed(segments):
        if re.fullmatch(r"[a-z0-9][a-z0-9-]{2,150}", seg.lower()) and not seg.isdigit():
            return seg.lower()
    return None


def accept_hits(hits: Sequence[SearchHit], tokens: tuple[str, ...], key: str
                ) -> list[CoverageEntry]:
    """The hits that are on a search-only provider's host and name the company."""
    out: list[CoverageEntry] = []
    for hit in hits:
        url = str(hit.url or "")
        if not url.startswith("https://") or len(url) > 2048:
            continue
        provider = provider_for_host(_host(url))
        if provider is None or provider not in SEARCH_ONLY:
            continue
        if not (mentions(tokens, hit.title) or mentions(tokens, urlsplit(url).path)):
            continue
        out.append(CoverageEntry(
            provider=provider.key, url=url, page_kind=_search_page_kind(url),
            company_slug=_search_slug(url), name_key=key,
            title=_clip_title(hit.title), lastmod=_aware(hit.published)))
    return out


def _search_task_key(company_id: Any) -> str:
    return f"{SEARCH_TASK_PREFIX}{company_id}"


async def search_only_providers(
    session: Any, company: Any, *, tokens: tuple[str, ...], key: str | None, cfg: Any,
    searcher: Searcher | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    """At most one host-restricted search per company per 30 days. Never raises."""
    stamp = now or _utcnow()
    summary: dict[str, Any] = {
        "providers": [p.name for p in SEARCH_ONLY], "domains": list(SEARCH_DOMAINS),
        "interval_days": SEARCH_INTERVAL_DAYS, "searched_at": None, "hits_kept": 0}
    if key is None:
        return {**summary, "state": SEARCH_NOT_SEARCHABLE}
    if not getattr(cfg, "v3_web_search_enabled", False):
        return {**summary, "state": SEARCH_DISABLED}
    company_id = getattr(company, "id", None)
    task_key = _search_task_key(company_id)
    row = await session.get(SourcePollState, task_key)
    last_ok, last_try = (_aware(row.last_ok_at), _aware(row.last_started_at)) if row else (
        None, None)
    if last_ok is not None and stamp - last_ok < timedelta(days=SEARCH_INTERVAL_DAYS):
        return {**summary, "state": SEARCH_THROTTLED, "searched_at": _iso(last_ok)}
    if (last_try is not None and (last_ok is None or last_try > last_ok)
            and stamp - last_try < timedelta(hours=SEARCH_RETRY_HOURS)):
        return {**summary, "state": SEARCH_UNAVAILABLE, "searched_at": _iso(last_ok),
                "retry_after": _iso(last_try + timedelta(hours=SEARCH_RETRY_HOURS))}
    if row is None:
        row = SourcePollState(task_key=task_key)
        session.add(row)
    row.last_started_at = stamp
    row.updated_at = stamp
    await session.flush()
    from app.services.web_research.queries import sanitise_query

    sanitised = sanitise_query(f"{' '.join(tokens)} equity research")
    if not sanitised.ok:
        row.last_error = "query_refused"
        await session.flush()
        return {**summary, "state": SEARCH_NOT_SEARCHABLE}
    try:
        answer = await (searcher or default_searcher)(
            session, sanitised.text, SEARCH_DOMAINS,
            company_id if isinstance(company_id, uuid.UUID) else None, cfg)
    except Exception as exc:  # noqa: BLE001 - a failed search is "unavailable"
        answer = SearchAnswer(executed=False, state=f"error:{type(exc).__name__}")
    if not answer.executed:
        row.last_error = str(answer.state)[:300]
        await session.flush()
        return {**summary, "state": SEARCH_UNAVAILABLE, "searched_at": _iso(last_ok),
                "search_state": answer.state}
    kept = accept_hits(answer.hits, tokens, key)
    if kept:
        await upsert_pages(session, kept, now=stamp, discovered_via="search")
    row.last_ok_at = stamp
    row.last_error = None
    row.detail_json = {"hits": len(answer.hits), "kept": len(kept), "version": VERSION}
    await session.flush()
    log_event(logger, "v3_research_coverage_search", hits=len(answer.hits),
              kept=len(kept))
    return {**summary, "state": SEARCH_SEARCHED, "searched_at": _iso(stamp),
            "hits_kept": len(kept)}


# ── optional ingest of one public page ───────────────────────────────────── #


#: ``(session, company, page, cfg) -> {state, url, reason}`` — the seam.
Ingester = Callable[[Any, Any, ResearchCoveragePage, Any], Awaitable[dict[str, Any]]]


async def default_ingester(session: Any, company: Any, page: ResearchCoveragePage,
                           cfg: Any) -> dict[str, Any]:
    """``open_web_fetch`` (robots, TDM, pinned) then ``ingest_web_document`` (T5)."""
    from app.services.web_research.budget import WebBudgetLimits, WebResearchBudget
    from app.services.web_research.fetch import ORIGIN_LEAD, WebFetchContext, open_web_fetch
    from app.services.web_research.ingest import ingest_web_document

    budget = WebResearchBudget(
        limits=WebBudgetLimits(0, 0, 1, 0, 3 * 1024 * 1024, 30.0, 1), daily_cap=0)
    fetched = await open_web_fetch(session, page.url, context=WebFetchContext(),
                                   budget=budget, origin=ORIGIN_LEAD, cfg=cfg)
    if not getattr(fetched, "ok", False) or not getattr(fetched, "complete", False):
        return {"state": "not_ingested", "url": page.url,
                "reason": getattr(fetched, "failure_code", None) or fetched.status}
    done = await ingest_web_document(session, fetched, cfg=cfg, provider=page.provider,
                                     company_id=getattr(company, "id", None))
    return {"state": "ingested" if done.version_id else "not_ingested", "url": page.url,
            "reason": done.reason, "source_class": done.source_class, "tier": done.tier}


async def _maybe_ingest(session: Any, company: Any, pages: Sequence[ResearchCoveragePage],
                        cfg: Any, ingester: Ingester | None) -> dict[str, Any] | None:
    if not getattr(cfg, "v3_web_fetch_enabled", False):
        return {"state": "disabled",
                "reason": "web fetch is off; no coverage page was read"}
    target = next((p for p in pages if p.provider == EDISON.key
                   and p.page_kind == "equity"), None)
    if target is None:
        return None
    try:
        async with session.begin_nested():
            return await (ingester or default_ingester)(session, company, target, cfg)
    except Exception as exc:  # noqa: BLE001 - the ingest never costs the block
        return {"state": "not_ingested", "url": target.url,
                "reason": f"error:{type(exc).__name__}"}


# ── the block (pure) ─────────────────────────────────────────────────────── #


def _page_entry(page: Any, latest: str | None) -> dict[str, Any]:
    last_seen = _iso(getattr(page, "last_seen_at", None))
    return {
        "url": page.url, "title": page.title, "page_kind": page.page_kind,
        "date": _iso(page.lastmod), "date_basis": (
            "sitemap lastmod" if page.discovered_via == "sitemap" else
            "search provider's published date"),
        "first_seen_at": _iso(page.first_seen_at), "last_seen_at": last_seen,
        "discovered_via": page.discovered_via,
        "in_latest_index": (last_seen == latest) if page.discovered_via == "sitemap"
        else None,
    }


def independent_research_block(
    *, company_name: str | None, key: str | None, pages: Sequence[Any],
    index: Mapping[str, Any], search: Mapping[str, Any],
    ingest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The report block (pure)."""
    latest = index.get("refreshed_at")
    by_provider: dict[str, list[Any]] = {}
    for page in pages:
        by_provider.setdefault(page.provider, []).append(page)
    providers = []
    for pkey, rows in sorted(by_provider.items()):
        meta = BY_KEY.get(pkey)
        entries = [_page_entry(p, latest) for p in rows[:MAX_PAGES_PER_PROVIDER]]
        dates = [e["date"] for e in entries if e["date"]]
        providers.append({
            "provider": pkey, "name": meta.name if meta else pkey,
            "method": meta.method if meta else None,
            "note": meta.note if meta else None,
            "latest_date": max(dates) if dates else None,
            "pages": entries, "pages_total": len(rows)})
    searched = _SEARCH_LABEL.get(str(search.get("state")), "search-only providers not searched")
    looked_at = f"Edison sitemap; {searched}"
    if index.get("state") != "ready":
        state = STATE_INDEX_NOT_BUILT
        absence = ("Unknown: the Edison sitemap index has never been refreshed, so "
                   f"coverage was not checked; {searched}.") if not providers else None
    elif providers:
        state, absence = STATE_FOUND, None
    else:
        state = STATE_NONE_FOUND
        absence = f"None found in the indexed providers ({looked_at})."
    return {
        "version": VERSION,
        "state": state,
        "provider_count": len(providers),
        "providers": providers,
        "absence": absence,
        "looked_at": looked_at,
        "indexed_providers": [dict(index)],
        "search_only_providers": dict(search),
        "match": {"basis": "normalised company name equals the provider's page slug",
                  "company_name": company_name, "name_key": key},
        "ingested": dict(ingest) if ingest else None,
        "sponsored_note": SPONSORED_NOTE,
        "note": ("Coverage pages are listed, not read: their estimates and opinions are "
                 "the provider's, not this report's. Absence means none was found in the "
                 "providers named here, not that no research exists."),
    }


def obscurity_input(block: Mapping[str, Any]) -> dict[str, Any]:
    """What the obscurity score's ``independent_research_coverage`` component reads."""
    index = (block.get("indexed_providers") or [{}])[0]
    known = block.get("state") != STATE_INDEX_NOT_BUILT
    return {
        "state": "known" if known else "unknown",
        "provider_count": int(block.get("provider_count") or 0) if known else None,
        "providers": [p.get("name") for p in block.get("providers") or []],
        "as_of": (index.get("refreshed_at") or "")[:10] or None,
        "source": "research coverage index (Edison sitemap; search-only providers)",
        "looked_at": block.get("looked_at"),
    }


# ── the step ─────────────────────────────────────────────────────────────── #


async def research_coverage_step(
    session: Any, company: Any, *, cfg: Any, searcher: Searcher | None = None,
    ingester: Ingester | None = None, now: datetime | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(independent_research block, obscurity input)``; ``(None, None)`` when off or on
    failure — the report is then unchanged. Never raises."""
    if not getattr(cfg, "v3_research_coverage_enabled", False):
        return None, None
    try:
        name = getattr(company, "name", None)
        tokens = name_tokens(name)
        key = key_from_tokens(tokens)
        async with session.begin_nested():
            index = await index_status(session)
        try:
            async with session.begin_nested():
                search = await search_only_providers(
                    session, company, tokens=tokens, key=key, cfg=cfg, searcher=searcher,
                    now=now)
        except Exception as exc:  # noqa: BLE001 - one provider path, never the block
            search = {"state": SEARCH_UNAVAILABLE, "error": type(exc).__name__}
        pages: list[ResearchCoveragePage] = []
        if key is not None:
            async with session.begin_nested():
                pages = await pages_for_key(session, key)
        ingest = await _maybe_ingest(session, company, pages, cfg, ingester) if pages else None
        block = independent_research_block(company_name=name, key=key, pages=pages,
                                           index=index, search=search, ingest=ingest)
        return block, obscurity_input(block)
    except Exception:  # noqa: BLE001 - an additive block never costs the report
        logger.warning("research coverage block failed")
        return None, None


__all__ = [
    "MAX_INGESTS_PER_RUN",
    "SEARCH_DISABLED",
    "SEARCH_INTERVAL_DAYS",
    "SEARCH_NOT_SEARCHABLE",
    "SEARCH_SEARCHED",
    "SEARCH_THROTTLED",
    "SEARCH_UNAVAILABLE",
    "STATE_FOUND",
    "STATE_INDEX_NOT_BUILT",
    "STATE_NONE_FOUND",
    "VERSION",
    "SearchAnswer",
    "SearchHit",
    "accept_hits",
    "default_ingester",
    "default_searcher",
    "independent_research_block",
    "index_status",
    "obscurity_input",
    "pages_for_key",
    "research_coverage_step",
    "search_only_providers",
]
