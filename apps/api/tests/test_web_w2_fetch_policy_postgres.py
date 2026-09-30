"""Open-web W2 on real PostgreSQL — ``web_fetch_attempts`` rows from the fetch policy.

Skipped unless ``V3_TEST_POSTGRES_URL`` points at a PostgreSQL already at head (≥ 042).
SQLite runs with foreign keys off, so the lineage FKs (research job, search result,
parent attempt), the JSONB redirect chain and ``ON DELETE SET NULL`` are only proven
here. The network is the same offline mock as the SQLite suite: no real DNS, no socket.
"""

from __future__ import annotations

import os
import random
import uuid
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from app.services.sources import pinned_transport as pinned_module
from app.services.web_research.audit import web_research_audit
from app.services.web_research.fetch import (
    STATUS_FETCHED,
    STATUS_RETRIED,
    OpenWebFetchRuntime,
    WebFetchContext,
    open_web_fetch,
)
from tests.test_web_w2_fetch_policy import (
    FakeClock,
    FakeDNS,
    Web,
    _budget,
    _cfg,
    respond,
)

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head (>= 042)"
)


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> Web:
    network = Web()

    def _build(pins: dict[str, str] | None = None, **_kw: Any) -> Any:
        return pinned_module.PinnedAsyncHTTPTransport(
            pins=pins, transport_factory=lambda: httpx.MockTransport(network.handler)
        )

    monkeypatch.setattr(pinned_module, "build_pinned_transport", _build)
    return network


async def test_attempt_rows_carry_lineage_jsonb_and_survive_their_job(web: Web) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.research_job import ResearchJob
    from app.models.web_research import WebFetchAttempt, WebSearchQuery, WebSearchResult

    clock = FakeClock()
    runtime = OpenWebFetchRuntime.create(clock=clock, sleep=clock.sleep, rng=random.Random(1))
    dns = FakeDNS()
    budget = _budget(clock)
    cfg = _cfg()
    marker = uuid.uuid4().hex[:10]
    web.route("example.com", f"/{marker}/doc", [respond(503, b"x"), respond(etag='"e1"')])
    web.route("example.com", f"/{marker}/child", respond())

    engine = create_async_engine(POSTGRES_URL, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    job_id = uuid.uuid4()
    query_id = uuid.uuid4()
    result_id = uuid.uuid4()
    try:
        async with maker() as s:
            s.add(
                ResearchJob(
                    id=job_id,
                    job_type="company_research",
                    idempotency_key=f"w2-pg-{job_id}",
                    status="running",
                )
            )
            s.add(
                WebSearchQuery(
                    id=query_id,
                    research_job_id=job_id,
                    family="entity",
                    origin="planner",
                    query_text=f"w2 pg {marker}",
                    request_hash=marker,
                    provider="fake",
                )
            )
            await s.flush()
            s.add(
                WebSearchResult(
                    id=result_id,
                    query_id=query_id,
                    rank=1,
                    url=f"https://example.com/{marker}/doc",
                )
            )
            await s.flush()
            context = WebFetchContext(research_job_id=job_id)
            parent = await open_web_fetch(
                s,
                f"https://example.com/{marker}/doc?utm_source=x",
                context=context,
                budget=budget,
                origin="search",
                search_result_id=result_id,
                cfg=cfg,
                resolver=dns,
                runtime=runtime,
            )
            child = await open_web_fetch(
                s,
                f"https://example.com/{marker}/child",
                context=context,
                budget=budget,
                origin="crawl",
                parent_attempt_id=parent.attempt_id,
                cfg=cfg,
                resolver=dns,
                runtime=runtime,
            )
            await s.commit()
            assert parent.status == STATUS_FETCHED and parent.retries == 1
            assert child.status == STATUS_FETCHED

        async with maker() as s:
            rows = (
                (
                    await s.execute(
                        sa.select(WebFetchAttempt)
                        .where(WebFetchAttempt.research_job_id == job_id)
                        .order_by(WebFetchAttempt.created_at)
                    )
                )
                .scalars()
                .all()
            )
            assert [r.status for r in rows] == [STATUS_RETRIED, STATUS_FETCHED, STATUS_FETCHED]
            fetched = rows[1]
            assert fetched.web_search_result_id == result_id
            assert fetched.canonical_url == f"https://example.com/{marker}/doc"
            assert fetched.redirect_chain_json[-1]["meta"]["etag"] == '"e1"'
            assert rows[2].parent_attempt_id == fetched.id and rows[2].origin == "crawl"
            audit = await web_research_audit(s, research_job_id=job_id)
            metrics = audit.totals.fetch_metrics
            assert (metrics.attempts, metrics.fetched, metrics.retries) == (2, 2, 1)

            await s.execute(sa.delete(ResearchJob).where(ResearchJob.id == job_id))
            await s.commit()

        async with maker() as s:
            survivors = (
                (
                    await s.execute(
                        sa.select(WebFetchAttempt).where(
                            WebFetchAttempt.canonical_url.contains(marker)
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(survivors) == 3, "deleting a job never deletes its fetch audit"
            assert all(r.research_job_id is None for r in survivors)
    finally:
        async with maker() as s:
            await s.execute(
                sa.delete(WebFetchAttempt).where(WebFetchAttempt.canonical_url.contains(marker))
            )
            await s.execute(sa.delete(WebSearchQuery).where(WebSearchQuery.id == query_id))
            await s.execute(sa.delete(ResearchJob).where(ResearchJob.id == job_id))
            await s.commit()
        await engine.dispose()
