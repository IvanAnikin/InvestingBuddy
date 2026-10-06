"""Shared offline fixtures for the open-web W6b (Discovery live search) tests.

* ``Net`` — the ``open_web_fetch`` stand-in: serves pages, accounts against the SAME
  ``WebResearchBudget`` the real fetcher does, and writes a real ``web_fetch_attempts``
  row so ``fetch_attempt_id`` is a real id (A1 needs one).
* ``directory_fetcher`` — the exchange directories the identity step reads (ASX CSV here).
* ``Harness`` — builds the Discovery stage's deps and serves search fixtures keyed by the
  PLANNER's own query text, so a test names a query by its template key, not its string.

Nothing here opens a socket.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Any

from app.integrations.search.fake import FakeWebSearchProvider, fixture_key
from app.models.web_research import WebFetchAttempt
from app.services.providers.contracts import QueryFamily
from app.services.sources.document_fetcher import DocumentFetchResult
from app.services.web_research import discovery_planner as dp
from app.services.web_research.fetch import (
    STATUS_FETCHED,
    STATUS_NOT_RETRIEVABLE,
    STATUS_REFUSED,
    OpenWebFetchResult,
)

TODAY = date(2026, 10, 4)
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
GALLIUM = "gallium producers in Australia"

#: The ASX directory the identity step reads. Obscure names the curated registry has never
#: heard of — and one whose ticker is REAL but belongs to a different company (APX).
ASX_CSV = (
    '"ASX code","Company name","GICs industry group","Listing date","Market Cap"\n'
    '"ALG","ALPHA GALLIUM LIMITED","Materials","01/01/2018",85000000\n'
    '"BGM","BETA GERMANIUM LIMITED","Materials","01/01/2019",40000000\n'
    '"APX","APEX FISHERIES LIMITED","Consumer Staples","01/01/2015",50000000\n'
    '"ZGL","ZETA GALLIUM LIMITED","Materials","01/01/2020",30000000\n'
)


def page(title: str, paragraphs: list[str], *, host: str = "www.mining.com") -> bytes:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{title}</title>"
        f"<meta property='og:type' content='article'>"
        f"<meta property='article:published_time' content='2026-09-12T08:00:00Z'></head>"
        f"<body><main><article><h1>{title}</h1>{body}</article></main>"
        f"<footer><p>Copyright. Cookie settings.</p></footer></body></html>"
    ).encode()


GALLIUM_ARTICLE = page(
    "New gallium producers emerge as export curbs bite",
    [
        "Alpha Gallium Limited (ASX: ALG) is building a gallium recovery plant at an alumina "
        "refinery in Western Australia and has signed a non-binding offtake term sheet with a "
        "Japanese semiconductor materials buyer, the company said on Tuesday.",
        "Gallium prices outside China have roughly doubled since export licensing began, and "
        "buyers are looking for suppliers they can qualify within two years, according to "
        "procurement managers interviewed for this article.",
        "Separately, Beta Germanium Limited (ASX: BGM) is studying germanium recovery from "
        "zinc concentrate, although the company has not yet published a feasibility study and "
        "the weather in Perth was mild last week.",
        "Industry analysts expect more small developers to list as governments fund domestic "
        "critical minerals processing, and lead times for new capacity remain long.",
    ],
)
COLLISION_ARTICLE = page(
    "Gallium by-product recovery attracts small caps",
    [
        "Apex Metals Ltd (ASX: APX) said it is evaluating gallium recovery from bauxite residue "
        "and expects to complete a scoping study next year, according to the company's "
        "announcement.",
        "Gallium is a by-product of alumina refining and only a handful of producers outside "
        "China currently recover it at commercial scale, analysts at the trade publication said.",
    ],
)
SNIPPET_ONLY_NOTE = "Gamma Gallium Ltd (ASX: GGL) is a gallium explorer."


def tavily(results: list[dict[str, Any]], request_id: str) -> dict[str, Any]:
    return {
        "query": "q",
        "follow_up_questions": None,
        "answer": None,
        "images": [],
        "results": results,
        "response_time": 0.5,
        "usage": {"credits": 1},
        "request_id": request_id,
    }


def hit(
    url: str,
    title: str = "result",
    snippet: str = "Synthetic snippet.",
    published: str = "Sat, 12 Sep 2026 08:00:00 GMT",
) -> dict[str, Any]:
    return {
        "title": title,
        "url": url,
        "content": snippet,
        "score": 0.9,
        "published_date": published,
    }


class Net:
    """The ``open_web_fetch`` stand-in. Writes a real attempt row per fetch."""

    def __init__(
        self,
        pages: dict[str, bytes] | None = None,
        not_retrievable: dict[str, str] | None = None,
        redirects: dict[str, str] | None = None,
    ) -> None:
        self.pages = pages or {}
        self.not_retrievable = not_retrievable or {}
        #: result url -> the FINAL url the fetch ends on (a redirect chain's end).
        self.redirects = redirects or {}
        self.requested: list[str] = []
        self.discovery_run_ids: list[Any] = []

    async def __call__(
        self,
        session: Any,
        url: str,
        *,
        context: Any,
        budget: Any,
        origin: str,
        search_result_id: Any = None,
        **_kw: Any,
    ) -> OpenWebFetchResult:
        self.requested.append(url)
        self.discovery_run_ids.append(getattr(context, "discovery_run_id", None))
        refusal = budget.fetch_refusal()
        if refusal:
            return OpenWebFetchResult(
                status=STATUS_REFUSED, origin=origin, requested_url=url, failure_code=refusal
            )
        attempt = WebFetchAttempt(
            id=uuid.uuid4(),
            discovery_run_id=getattr(context, "discovery_run_id", None),
            web_search_result_id=search_result_id,
            origin=origin,
            requested_url=url,
            status="fetched",
        )
        if url in self.not_retrievable:
            attempt.status = "not_retrievable"
            session.add(attempt)
            await session.flush()
            return OpenWebFetchResult(
                status=STATUS_NOT_RETRIEVABLE,
                origin=origin,
                requested_url=url,
                failure_code=self.not_retrievable[url],
                attempt_id=attempt.id,
            )
        body = self.pages.get(url)
        if body is None:
            attempt.status = "failed"
            session.add(attempt)
            await session.flush()
            return OpenWebFetchResult(
                status="failed",
                origin=origin,
                requested_url=url,
                failure_code="http_404",
                attempt_id=attempt.id,
            )
        budget.record_fetch(byte_count=len(body), is_pdf=False)
        session.add(attempt)
        await session.flush()
        return OpenWebFetchResult(
            status=STATUS_FETCHED,
            origin=origin,
            requested_url=url,
            final_url=self.redirects.get(url, url),
            canonical_url=url,
            content=body,
            content_class="html",
            charset="utf-8",
            content_hash=hashlib.sha256(body).hexdigest(),
            bytes=len(body),
            tdm_decision="tdm_not_reserved",
            attempt_id=attempt.id,
        )


async def directory_fetcher(url: str, **_kw: Any) -> DocumentFetchResult:
    if "fredgraph" in url:
        return DocumentFetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/csv",
            document_type="text",
            content=b"observation_date,DEXUSAL\n2026-09-17,0.66\n2026-09-18,0.66\n",
        )
    if "markitdigital" in url:
        return DocumentFetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/csv",
            document_type="text",
            content=ASX_CSV.encode(),
        )
    return DocumentFetchResult(requested_url=url, error="nope", failure_code="http_404")


def serve(
    provider: FakeWebSearchProvider, plan: dp.DiscoveryPlan, by_key: dict[str, list[dict[str, Any]]]
) -> None:
    """Serve ``by_key`` (template key -> result hits) under the plan's own query texts."""
    for q in plan.queries:
        if q.key in by_key:
            provider.fixtures[fixture_key(q.request.query)] = tavily(
                by_key[q.key], f"synthetic-{q.key}"
            )


def plan_for(intent: Any, **kw: Any) -> dp.DiscoveryPlan:
    facts = dp.facts_from_intent(intent)
    kw.setdefault("today", TODAY)
    kw.setdefault("max_queries", 24)
    return dp.build_discovery_plan(facts, **kw)


def keys(plan: dp.DiscoveryPlan, family: QueryFamily) -> list[str]:
    return [q.key for q in plan.queries if q.family is family]
