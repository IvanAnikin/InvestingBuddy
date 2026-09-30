"""The web research audit read model — open-web W1 (spec §22.2).

Reconstructs one run's search activity from rows alone: every query with its network
fact, every result with its disposition, every fetch attempt, and totals. Read-only.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa

from app.models.web_research import WebFetchAttempt, WebSearchQuery, WebSearchResult
from app.schemas.web_research import (
    WebFetchAttemptRead,
    WebResearchAuditRead,
    WebResearchTotals,
    WebSearchQueryRead,
    WebSearchResultRead,
)

SCOPE_RESEARCH_JOB = "research_job"
SCOPE_DISCOVERY_RUN = "discovery_run"

#: Bounds the page. A run's budget caps queries far below this (spec §19.1).
MAX_QUERIES = 500
MAX_FETCH_ATTEMPTS = 1000


def _query_read(row: WebSearchQuery, results: list[WebSearchResult]) -> WebSearchQueryRead:
    filters = row.filters_json if isinstance(row.filters_json, dict) else None
    return WebSearchQueryRead(
        id=row.id,
        created_at=row.created_at,
        stage=row.stage,
        family=row.family,
        origin=row.origin,
        template_version=row.template_version,
        query_text=row.query_text,
        request_hash=row.request_hash,
        filters=filters,
        provider=row.provider,
        executed=bool(row.executed),
        provider_request_id=row.provider_request_id,
        http_status=row.http_status,
        network_call_count=int(row.network_call_count or 0),
        latency_ms=row.latency_ms,
        result_count=int(row.result_count or 0),
        from_cache=bool(filters and filters.get("served_from_query_id")),
        cost_units=dict(row.cost_units_json or {}),
        error_code=row.error_code,
        results=[
            WebSearchResultRead(
                id=r.id,
                rank=r.rank,
                url=r.url,
                canonical_url=r.canonical_url,
                domain=r.domain,
                title=r.title,
                snippet=r.snippet,
                published_hint=r.published_hint,
                language_hint=r.language_hint,
                provider_score=r.provider_score,
                disposition=r.disposition,
                disposition_reason=r.disposition_reason,
            )
            for r in results
        ],
    )


def _totals(queries: list[WebSearchQueryRead], fetches: int) -> WebResearchTotals:
    errors: dict[str, int] = {}
    cost: dict[str, float] = {}
    for q in queries:
        if q.error_code:
            errors[q.error_code] = errors.get(q.error_code, 0) + 1
        if q.executed and q.network_call_count > 0:
            for unit, value in q.cost_units.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    cost[unit] = cost.get(unit, 0.0) + float(value)
    return WebResearchTotals(
        queries=len(queries),
        executed=sum(1 for q in queries if q.executed),
        from_cache=sum(1 for q in queries if q.from_cache),
        not_executed=sum(1 for q in queries if not q.executed),
        network_call_count=sum(q.network_call_count for q in queries),
        results=sum(len(q.results) for q in queries),
        fetch_attempts=fetches,
        errors_by_code=errors,
        cost_units=cost,
    )


async def web_research_audit(
    session: Any,
    *,
    research_job_id: uuid.UUID | None = None,
    discovery_run_id: uuid.UUID | None = None,
) -> WebResearchAuditRead:
    """The audit for exactly one scope: a research job or a discovery run."""
    if (research_job_id is None) == (discovery_run_id is None):
        raise ValueError("pass exactly one of research_job_id or discovery_run_id")
    if research_job_id is not None:
        scope, scope_id = SCOPE_RESEARCH_JOB, research_job_id
        q_filter = WebSearchQuery.research_job_id == research_job_id
        f_filter = WebFetchAttempt.research_job_id == research_job_id
    else:
        assert discovery_run_id is not None
        scope, scope_id = SCOPE_DISCOVERY_RUN, discovery_run_id
        q_filter = WebSearchQuery.discovery_run_id == discovery_run_id
        f_filter = WebFetchAttempt.discovery_run_id == discovery_run_id

    query_rows = (
        await session.execute(
            sa.select(WebSearchQuery)
            .where(q_filter)
            .order_by(WebSearchQuery.created_at, WebSearchQuery.id)
            .limit(MAX_QUERIES)
        )
    ).scalars().all()
    by_query: dict[uuid.UUID, list[WebSearchResult]] = {row.id: [] for row in query_rows}
    if by_query:
        result_rows = (
            await session.execute(
                sa.select(WebSearchResult)
                .where(WebSearchResult.query_id.in_(list(by_query)))
                .order_by(WebSearchResult.query_id, WebSearchResult.rank)
            )
        ).scalars().all()
        for r in result_rows:
            by_query.setdefault(r.query_id, []).append(r)
    fetch_rows = (
        await session.execute(
            sa.select(WebFetchAttempt)
            .where(f_filter)
            .order_by(WebFetchAttempt.created_at, WebFetchAttempt.id)
            .limit(MAX_FETCH_ATTEMPTS)
        )
    ).scalars().all()

    queries = [_query_read(row, by_query.get(row.id, [])) for row in query_rows]
    fetches = [
        WebFetchAttemptRead(
            id=f.id,
            created_at=f.created_at,
            web_search_result_id=f.web_search_result_id,
            parent_attempt_id=f.parent_attempt_id,
            origin=f.origin,
            requested_url=f.requested_url,
            final_url=f.final_url,
            policy_decision=f.policy_decision,
            robots_decision=f.robots_decision,
            tdm_decision=f.tdm_decision,
            http_status=f.http_status,
            mime_served=f.mime_served,
            mime_sniffed=f.mime_sniffed,
            bytes=f.bytes,
            truncated=f.truncated,
            content_hash=f.content_hash,
            fetch_ms=f.fetch_ms,
            status=f.status,
            failure_code=f.failure_code,
        )
        for f in fetch_rows
    ]
    return WebResearchAuditRead(
        scope=scope,
        scope_id=scope_id,
        queries=queries,
        fetch_attempts=fetches,
        totals=_totals(queries, len(fetches)),
    )


__all__ = ["SCOPE_DISCOVERY_RUN", "SCOPE_RESEARCH_JOB", "web_research_audit"]
