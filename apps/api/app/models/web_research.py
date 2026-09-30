"""Open-web search and fetch provenance — open-web W1 (spec §15.1, §22.3, §26.3).

Three tables, created by migration 042, that let an admin answer "was this found by
search?" from rows rather than prose:

* ``web_search_queries`` — one row per query the platform issued **or refused**, with the
  network fact: ``executed``, ``provider_request_id``, HTTP status, latency,
  ``network_call_count`` and cost units. A refusal (private token, budget, governance,
  missing key) is a row too, because "why did no search happen" is an audit question.
* ``web_search_results`` — one row per normalised result, with its rank and its
  disposition. Title and snippet are stored only when the provider's terms permit it
  (``result_storage``), and they are untrusted text: never cited, never prompted.
* ``web_fetch_attempts`` — one row per open-web fetch. Created here so W2 has a
  schema to write to; nothing writes it in W1.

WHY NOT REUSE ``research_tool_calls`` OR ``document_ingestion_attempts``
=======================================================================
Neither has a rank, a disposition or a result row, and the second is a V2 table whose
reshaping would touch what V2 reads (spec §26.3). New tables keep V2 untouched.

NEVER STORED
============
The provider API key, request headers, and — for a query refused because it carried a
private token or a credential — the query text itself (it is replaced with a marker).
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _lineage_fk(column: str, target: str, table: str) -> sa.ForeignKey:
    return sa.ForeignKey(
        f"{target}.id", ondelete="SET NULL", name=f"fk_{table}_{column}_{target}"
    )


class WebSearchQuery(Base):
    """One query issued to (or refused before) a web search provider."""

    __tablename__ = "web_search_queries"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    research_job_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        _lineage_fk("research_job_id", "research_jobs", "web_search_queries"),
        nullable=True,
    )
    discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        _lineage_fk("discovery_run_id", "discovery_runs", "web_search_queries"),
        nullable=True,
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        _lineage_fk("agent_run_id", "agent_runs", "web_search_queries"),
        nullable=True,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        _lineage_fk("company_id", "companies", "web_search_queries"),
        nullable=True,
    )
    stage: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    family: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    origin: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    template_version: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    query_text: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    request_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    filters_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    executed: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, default=False, server_default=sa.false()
    )
    provider_request_id: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    http_status: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    network_call_count: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, default=0, server_default=sa.text("0")
    )
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    result_count: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, default=0, server_default=sa.text("0")
    )
    cost_units_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(sa.String(60), nullable=True)
    #: Set only on a cache serve (or an in-batch duplicate): the row whose network call
    #: this row re-serves. Such a row has ``network_call_count = 0`` and no
    #: ``provider_request_id`` of its own — the request id lives on the original.
    served_from_query_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        _lineage_fk("served_from_query_id", "web_search_queries", "web_search_queries"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=sa.func.now(),
    )

    __table_args__ = (
        sa.Index("ix_web_search_queries_research_job_id", "research_job_id"),
        sa.Index("ix_web_search_queries_discovery_run_id", "discovery_run_id"),
        sa.Index(
            "ix_web_search_queries_cache_key", "request_hash", "provider", "created_at"
        ),
    )


class WebSearchResult(Base):
    """One normalised result of one query. A candidate URL, never evidence."""

    __tablename__ = "web_search_results"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    query_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "web_search_queries.id",
            ondelete="CASCADE",
            name="fk_web_search_results_query_id_web_search_queries",
        ),
        nullable=False,
    )
    rank: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    url: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    domain: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    #: Untrusted. NULL when the provider's result_storage forbids keeping it.
    title: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    #: Untrusted. NULL when the provider's result_storage forbids keeping it.
    snippet: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    published_hint: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    language_hint: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)
    provider_score: Mapped[float | None] = mapped_column(sa.Float(), nullable=True)
    #: ``candidate`` until selection (W2) marks it ``selected`` or ``skipped``.
    disposition: Mapped[str] = mapped_column(
        sa.String(30), nullable=False, default="candidate", server_default="candidate"
    )
    disposition_reason: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=sa.func.now(),
    )

    __table_args__ = (sa.Index("ix_web_search_results_query_rank", "query_id", "rank"),)


class WebFetchAttempt(Base):
    """One open-web fetch attempt (written from W2; the schema lands with 042)."""

    __tablename__ = "web_fetch_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    research_job_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        _lineage_fk("research_job_id", "research_jobs", "web_fetch_attempts"),
        nullable=True,
    )
    discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        _lineage_fk("discovery_run_id", "discovery_runs", "web_fetch_attempts"),
        nullable=True,
    )
    web_search_result_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        _lineage_fk("web_search_result_id", "web_search_results", "web_fetch_attempts"),
        nullable=True,
    )
    parent_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        _lineage_fk("parent_attempt_id", "web_fetch_attempts", "web_fetch_attempts"),
        nullable=True,
    )
    #: search | crawl | user | lead
    origin: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    requested_url: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    final_url: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    redirect_chain_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    policy_decision: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    robots_decision: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    tdm_decision: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    http_status: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    mime_served: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    mime_sniffed: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    bytes: Mapped[int | None] = mapped_column(sa.BigInteger(), nullable=True)
    truncated: Mapped[bool | None] = mapped_column(sa.Boolean(), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    fetch_ms: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(sa.String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=sa.func.now(),
    )

    __table_args__ = (
        sa.Index("ix_web_fetch_attempts_research_job_id", "research_job_id"),
        sa.Index("ix_web_fetch_attempts_discovery_run_id", "discovery_run_id"),
        sa.Index("ix_web_fetch_attempts_web_search_result_id", "web_search_result_id"),
    )


__all__ = ["WebFetchAttempt", "WebSearchQuery", "WebSearchResult"]
