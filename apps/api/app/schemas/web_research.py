"""Read shapes for the web research audit — open-web W1 (spec §22.2).

Admin only. Titles and snippets are untrusted third-party text and are labelled as such;
they are for an operator reading the audit, never for a prompt or a citation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WebSearchResultRead(BaseModel):
    id: uuid.UUID
    rank: int
    url: str
    canonical_url: str | None = None
    domain: str | None = None
    #: Untrusted.
    title: str | None = None
    #: Untrusted.
    snippet: str | None = None
    published_hint: datetime | None = None
    language_hint: str | None = None
    provider_score: float | None = None
    disposition: str
    disposition_reason: str | None = None


class WebSearchQueryRead(BaseModel):
    id: uuid.UUID
    created_at: datetime | None = None
    stage: str | None = None
    family: str
    origin: str
    template_version: str | None = None
    #: ``[withheld: <code>]`` when the query was refused for carrying private data.
    query_text: str
    request_hash: str
    filters: dict[str, Any] | None = None
    # ── the network fact (spec §22.3) ──
    provider: str
    executed: bool
    provider_request_id: str | None = None
    http_status: int | None = None
    network_call_count: int
    latency_ms: int | None = None
    result_count: int
    from_cache: bool = False
    #: The row whose network call this one re-serves (cache or in-batch duplicate).
    served_from_query_id: uuid.UUID | None = None
    cost_units: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    results: list[WebSearchResultRead] = Field(default_factory=list)


class WebFetchAttemptRead(BaseModel):
    id: uuid.UUID
    created_at: datetime | None = None
    web_search_result_id: uuid.UUID | None = None
    parent_attempt_id: uuid.UUID | None = None
    origin: str
    requested_url: str
    final_url: str | None = None
    canonical_url: str | None = None
    #: Hops as ``{"url", "status"}``; the LAST entry may carry ``"meta"`` (validators,
    #: rel=canonical, charset, js_required, MIME mismatch, TDM signals — W2).
    redirect_chain: list[dict[str, Any]] = Field(default_factory=list)
    policy_decision: str | None = None
    robots_decision: str | None = None
    tdm_decision: str | None = None
    http_status: int | None = None
    mime_served: str | None = None
    mime_sniffed: str | None = None
    bytes: int | None = None
    truncated: bool | None = None
    content_hash: str | None = None
    fetch_ms: int | None = None
    status: str
    failure_code: str | None = None


class WebFetchMetrics(BaseModel):
    """Spec §22.1 fetch metrics for one run, derived from ``web_fetch_attempts`` rows.

    ``attempts`` counts LOGICAL page fetches that were actually tried. Not in it: a
    retried physical attempt (``retries``), a negative-cache hit (``negative_cached``),
    a budget refusal (``budget_refused``) and robots.txt/TDMRep requests
    (``policy_file_requests``). Rates are over ``attempts``; 0.0 when there were none.
    """

    attempts: int = 0
    fetched: int = 0
    partial: int = 0
    success_rate: float = 0.0
    http_403: int = 0
    http_403_rate: float = 0.0
    #: 402 + ``paywall_jsonld`` + ``login_wall`` + ``consent_wall``.
    paywall: int = 0
    paywall_rate: float = 0.0
    captcha: int = 0
    #: ``robots_disallowed`` + ``robots_unavailable``.
    robots: int = 0
    robots_rate: float = 0.0
    tdm_reserved: int = 0
    tdm_rate: float = 0.0
    policy_denied: int = 0
    policy_deny_rate: float = 0.0
    negative_cached: int = 0
    budget_refused: int = 0
    policy_file_requests: int = 0
    retries: int = 0
    redirects: int = 0
    bytes: int = 0
    js_required: int = 0
    js_required_rate: float = 0.0
    mime_mismatch: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    by_failure_code: dict[str, int] = Field(default_factory=dict)


class WebResearchTotals(BaseModel):
    queries: int = 0
    executed: int = 0
    from_cache: int = 0
    not_executed: int = 0
    network_call_count: int = 0
    results: int = 0
    fetch_attempts: int = 0
    errors_by_code: dict[str, int] = Field(default_factory=dict)
    #: Summed per unit over executed network calls. A unit absent here was not reported
    #: by any call — absent is not zero, and this never prices anything.
    cost_units: dict[str, float] = Field(default_factory=dict)
    fetch_metrics: WebFetchMetrics = Field(default_factory=WebFetchMetrics)


class WebResearchAuditRead(BaseModel):
    scope: str
    scope_id: uuid.UUID
    queries: list[WebSearchQueryRead] = Field(default_factory=list)
    fetch_attempts: list[WebFetchAttemptRead] = Field(default_factory=list)
    totals: WebResearchTotals = Field(default_factory=WebResearchTotals)
    #: True when the page stopped at its row limit: there are more rows than shown, and
    #: the totals above count only what is shown.
    queries_truncated: bool = False
    fetch_attempts_truncated: bool = False
    notice: str = (
        "INTERNAL ADMIN ONLY. Titles and snippets are untrusted third-party text: never "
        "cited, never evidence. Not investment advice."
    )
