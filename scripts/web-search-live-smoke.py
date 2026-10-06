#!/usr/bin/env python3
"""Open-web W1 — live Tavily smoke test. OPT-IN, and it spends real credits.

WHAT IT DOES
============
Runs five fixed, public-context queries against the real Tavily ``/search`` endpoint
through the production adapter (``TavilySearchProvider``) and prints the network fact of
each one: ``executed``, ``provider_request_id``, HTTP status, ``network_call_count``,
latency, result count, cost units and error code. It prints **no key, no snippet and no
page text** — only facts and result domains.

With ``--persist`` it runs the same five queries through ``run_searches`` against
``DATABASE_URL`` (which must be at migration 042), so the admin endpoint
``GET /api/v1/admin/web-research/jobs/<job id>`` can show them. It never targets the live
database unless you point ``DATABASE_URL`` at it yourself — and that needs approval U9.

GATES
=====
Refuses to run unless BOTH are set in the environment:

* ``WEB_RESEARCH_LIVE=1``
* ``TAVILY_API_KEY`` (never pass it on the command line; it would land in shell history)

Requires decision U1 (Tavily approved, account and key created by the owner).

USAGE
=====
    WEB_RESEARCH_LIVE=1 TAVILY_API_KEY=... python scripts/web-search-live-smoke.py
    WEB_RESEARCH_LIVE=1 TAVILY_API_KEY=... DATABASE_URL=... \\
        python scripts/web-search-live-smoke.py --persist
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(ROOT))

#: Public-context queries only (rule G1): closed-vocabulary theme terms, no user data.
QUERIES: tuple[tuple[str, str, dict], ...] = (
    ("power transformer manufacturer listed company Europe", "entity", {}),
    ("grid equipment supplier AIM listed", "venue", {}),
    ("transformer market report", "document", {"filetype_pdf": True}),
    ("transformer factory expansion", "catalyst", {"topic": "news", "days": 90}),
    ("Transformatorenhersteller börsennotiert", "local_lang", {"country": "DE"}),
)


def _requests() -> list:
    from app.services.providers.contracts import QueryFamily, SearchRequest
    from app.services.web_research.queries import sanitise_query

    out = []
    for text, family, opts in QUERIES:
        cleaned = sanitise_query(text, filetype_pdf=bool(opts.get("filetype_pdf")))
        if not cleaned.ok:
            raise SystemExit(f"sanitiser refused a smoke query: {cleaned.refusal}")
        days = opts.get("days")
        out.append(
            SearchRequest(
                query=cleaned.text,
                family=QueryFamily(family),
                max_results=10,
                topic=opts.get("topic", "general"),
                country=opts.get("country"),
                date_from=(date.today() - timedelta(days=days)) if days else None,
                origin="template",
                template_version="w1-smoke-1",
            )
        )
    return out


def _print_fact(index: int, execution, items) -> None:  # noqa: ANN001
    print(
        f"[{index}] executed={execution.executed} "
        f"request_id={execution.provider_request_id or '-'} "
        f"http={execution.http_status} calls={execution.network_call_count} "
        f"latency_ms={execution.latency_ms} results={execution.result_count} "
        f"cost={dict(execution.cost_units)} error={execution.error_code or '-'} "
        f"enforced_by={dict(execution.filters_enforced_by)}"
    )
    if items:
        print("     domains: " + ", ".join(sorted({i.domain for i in items})[:10]))


async def _direct(key: str) -> int:
    from app.integrations.search.tavily import TavilySearchProvider

    provider = TavilySearchProvider(api_key=key)
    executed = 0
    for i, request in enumerate(_requests(), start=1):
        execution, items = await provider.search(request)
        executed += int(execution.executed)
        _print_fact(i, execution, items)
    print(f"executed {executed} of {len(QUERIES)}")
    return 0 if executed == len(QUERIES) else 1


async def _persisted(key: str) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.integrations.search.tavily import TavilySearchProvider
    from app.models.research_job import ResearchJob
    from app.services.web_research.search import SearchContext, run_searches

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SystemExit("--persist needs DATABASE_URL (a database at migration 042)")
    cfg = SimpleNamespace(
        v3_web_search_enabled=True,
        v3_web_search_provider="tavily",
        v3_web_search_max_queries_per_day=300,
        v3_run_max_web_searches=0,
    )
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    job_id = uuid.uuid4()
    try:
        async with maker() as session:
            session.add(
                ResearchJob(
                    id=job_id,
                    job_type="web_search_smoke",
                    idempotency_key=f"web_search_smoke:{job_id}",
                    status="succeeded",
                )
            )
            await session.flush()
            result = await run_searches(
                session,
                _requests(),
                SearchContext(research_job_id=job_id, stage="w1_smoke"),
                provider=TavilySearchProvider(api_key=key),
                cfg=cfg,
            )
            await session.commit()
    finally:
        await engine.dispose()
    for i, outcome in enumerate(result.outcomes, start=1):
        _print_fact(i, outcome.execution, outcome.results)
    print(f"state={result.state} summary={result.summary()}")
    print(f"admin: GET /api/v1/admin/web-research/jobs/{job_id}")
    return 0 if result.executed == len(QUERIES) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()
    if os.environ.get("WEB_RESEARCH_LIVE") != "1":
        print("refusing: set WEB_RESEARCH_LIVE=1 to spend real Tavily credits")
        return 2
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        print("refusing: TAVILY_API_KEY is not set")
        return 2
    return asyncio.run(_persisted(key) if args.persist else _direct(key))


if __name__ == "__main__":
    raise SystemExit(main())
