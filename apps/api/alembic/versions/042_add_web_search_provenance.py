"""Open-web search and fetch provenance — open-web W1 (spec §15.1, §26.3).

WHAT THIS CARRIES
=================
Three new tables and nothing else:

* ``web_search_queries`` — one row per query issued to (or refused before) a web search
  provider, carrying the network fact: ``executed``, ``provider_request_id``, HTTP
  status, ``network_call_count``, latency, result count, cost units and an error code.
* ``web_search_results`` — one row per normalised result: rank, URL, canonical URL,
  domain, title/snippet (nullable, subject to the provider's ``result_storage``),
  published and language hints, provider score and a disposition.
* ``web_fetch_attempts`` — one row per open-web fetch (written from W2).

ADDITIVE ONLY
=============
New tables only. No existing column is added, altered or dropped; no backfill. Every
lineage FK (research_jobs, discovery_runs, agent_runs, companies) is nullable with
``ON DELETE SET NULL``, so deleting a job never deletes its search audit, and no index is
unique. Downgrade drops exactly these three tables and their indexes.

DEPLOY ORDER
============
Apply before the code that WRITES these tables runs with ``V3_WEB_SEARCH_ENABLED=true``.
With the flag off (the default) nothing reads or writes them, so a deploy that carries
the W1 code on a database without 042 is inert; the admin read endpoint answers 503
naming this migration rather than an empty list.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers
revision: str = "042"
down_revision: str | None = "041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: What this migration owns, in creation order. Downgrade walks it backwards.
TABLES: tuple[str, ...] = ("web_search_queries", "web_search_results", "web_fetch_attempts")

#: ``(index name, table, columns)``. None is unique.
INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_web_search_queries_research_job_id", "web_search_queries", ("research_job_id",)),
    ("ix_web_search_queries_discovery_run_id", "web_search_queries", ("discovery_run_id",)),
    (
        "ix_web_search_queries_cache_key",
        "web_search_queries",
        ("request_hash", "provider", "created_at"),
    ),
    ("ix_web_search_results_query_rank", "web_search_results", ("query_id", "rank")),
    ("ix_web_fetch_attempts_research_job_id", "web_fetch_attempts", ("research_job_id",)),
    ("ix_web_fetch_attempts_discovery_run_id", "web_fetch_attempts", ("discovery_run_id",)),
    (
        "ix_web_fetch_attempts_web_search_result_id",
        "web_fetch_attempts",
        ("web_search_result_id",),
    ),
)


def _uuid(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.Uuid(as_uuid=True), nullable=nullable)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def _set_null_fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=f"fk_{table}_{column}_{target}",
        ondelete="SET NULL",
    )


def upgrade() -> None:
    op.create_table(
        "web_search_queries",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        _uuid("research_job_id"),
        _uuid("discovery_run_id"),
        _uuid("agent_run_id"),
        _uuid("company_id"),
        sa.Column("stage", sa.String(40), nullable=True),
        sa.Column("family", sa.String(30), nullable=False),
        sa.Column("origin", sa.String(30), nullable=False),
        sa.Column("template_version", sa.String(40), nullable=True),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("filters_json", JSONB(), nullable=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("executed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provider_request_id", sa.String(120), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column(
            "network_call_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_units_json", JSONB(), nullable=True),
        sa.Column("error_code", sa.String(60), nullable=True),
        _created_at(),
        _set_null_fk("web_search_queries", "research_job_id", "research_jobs"),
        _set_null_fk("web_search_queries", "discovery_run_id", "discovery_runs"),
        _set_null_fk("web_search_queries", "agent_run_id", "agent_runs"),
        _set_null_fk("web_search_queries", "company_id", "companies"),
    )
    op.create_table(
        "web_search_results",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        _uuid("query_id", nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("published_hint", sa.DateTime(timezone=True), nullable=True),
        sa.Column("language_hint", sa.String(16), nullable=True),
        sa.Column("provider_score", sa.Float(), nullable=True),
        sa.Column(
            "disposition", sa.String(30), nullable=False, server_default="candidate"
        ),
        sa.Column("disposition_reason", sa.String(120), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["query_id"],
            ["web_search_queries.id"],
            name="fk_web_search_results_query_id_web_search_queries",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "web_fetch_attempts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        _uuid("research_job_id"),
        _uuid("discovery_run_id"),
        _uuid("web_search_result_id"),
        _uuid("parent_attempt_id"),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("requested_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("redirect_chain_json", JSONB(), nullable=True),
        sa.Column("policy_decision", sa.String(40), nullable=True),
        sa.Column("robots_decision", sa.String(40), nullable=True),
        sa.Column("tdm_decision", sa.String(40), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("mime_served", sa.String(120), nullable=True),
        sa.Column("mime_sniffed", sa.String(120), nullable=True),
        sa.Column("bytes", sa.BigInteger(), nullable=True),
        sa.Column("truncated", sa.Boolean(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("fetch_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("failure_code", sa.String(60), nullable=True),
        _created_at(),
        _set_null_fk("web_fetch_attempts", "research_job_id", "research_jobs"),
        _set_null_fk("web_fetch_attempts", "discovery_run_id", "discovery_runs"),
        _set_null_fk("web_fetch_attempts", "web_search_result_id", "web_search_results"),
        _set_null_fk("web_fetch_attempts", "parent_attempt_id", "web_fetch_attempts"),
    )
    for name, table, columns in INDEXES:
        op.create_index(name, table, list(columns))


def downgrade() -> None:
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
    for table in reversed(TABLES):
        op.drop_table(table)
