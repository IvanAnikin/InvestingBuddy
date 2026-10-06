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
    WebFetchMetrics,
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
        from_cache=row.served_from_query_id is not None,
        served_from_query_id=row.served_from_query_id,
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


_POLICY_FILE_ORIGINS = frozenset({"robots", "tdm"})
_PAYWALL_CODES = frozenset({"http_402", "paywall_jsonld", "login_wall", "consent_wall"})
_ROBOTS_CODES = frozenset({"robots_disallowed", "robots_unavailable"})


def _final_meta(chain: Any) -> dict[str, Any]:
    if isinstance(chain, list) and chain and isinstance(chain[-1], dict):
        meta = chain[-1].get("meta")
        if isinstance(meta, dict):
            return meta
    return {}


def fetch_metrics(rows: list[WebFetchAttemptRead]) -> WebFetchMetrics:
    """Spec §22.1 fetch metrics from attempt rows (W2). Counts and codes only."""
    m = WebFetchMetrics()
    for row in rows:
        m.by_status[row.status] = m.by_status.get(row.status, 0) + 1
        if row.origin in _POLICY_FILE_ORIGINS:
            # robots.txt / TDMRep requests are overhead, not page attempts.
            m.policy_file_requests += 1
            m.bytes += int(row.bytes or 0)
            continue
        if row.status == "retried":
            m.retries += 1
            m.bytes += int(row.bytes or 0)
            continue
        if row.status == "negative_cached":
            m.negative_cached += 1  # no request was made: not an attempt
            continue
        if row.policy_decision == "budget_refused":
            m.budget_refused += 1  # refused before any request: not an attempt
            continue
        m.attempts += 1
        code = row.failure_code or ""
        if code:
            m.by_failure_code[code] = m.by_failure_code.get(code, 0) + 1
        if row.status == "fetched":
            m.fetched += 1
        elif row.status == "fetched_partial":
            m.partial += 1
        m.http_403 += int(code == "http_403")
        m.paywall += int(code in _PAYWALL_CODES)
        m.captcha += int(code == "captcha")
        m.robots += int(code in _ROBOTS_CODES)
        m.tdm_reserved += int(row.tdm_decision == "tdm_reserved")
        m.policy_denied += int(row.policy_decision == "denied")
        m.bytes += int(row.bytes or 0)
        hops = [h for h in row.redirect_chain if isinstance(h, dict) and "status" in h]
        m.redirects += max(0, len(hops) - 1)
        meta = _final_meta(row.redirect_chain)
        m.js_required += int(bool(meta.get("js_required")))
        m.mime_mismatch += int(bool(meta.get("mime_mismatch")))
    if m.attempts:
        n = float(m.attempts)
        m.success_rate = round((m.fetched + m.partial) / n, 4)
        m.http_403_rate = round(m.http_403 / n, 4)
        m.paywall_rate = round(m.paywall / n, 4)
        m.robots_rate = round(m.robots / n, 4)
        m.tdm_rate = round(m.tdm_reserved / n, 4)
        m.policy_deny_rate = round(m.policy_denied / n, 4)
        m.js_required_rate = round(m.js_required / n, 4)
    return m


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
            .limit(MAX_QUERIES + 1)
        )
    ).scalars().all()
    queries_truncated = len(query_rows) > MAX_QUERIES
    query_rows = query_rows[:MAX_QUERIES]
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
            .limit(MAX_FETCH_ATTEMPTS + 1)
        )
    ).scalars().all()
    fetches_truncated = len(fetch_rows) > MAX_FETCH_ATTEMPTS
    fetch_rows = fetch_rows[:MAX_FETCH_ATTEMPTS]

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
            canonical_url=f.canonical_url,
            redirect_chain=[h for h in (f.redirect_chain_json or []) if isinstance(h, dict)],
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
    totals = _totals(queries, len(fetches))
    totals.fetch_metrics = fetch_metrics(fetches)
    return WebResearchAuditRead(
        scope=scope,
        scope_id=scope_id,
        queries=queries,
        fetch_attempts=fetches,
        totals=totals,
        queries_truncated=queries_truncated,
        fetch_attempts_truncated=fetches_truncated,
    )


__all__ = ["SCOPE_DISCOVERY_RUN", "SCOPE_RESEARCH_JOB", "fetch_metrics", "web_research_audit"]
