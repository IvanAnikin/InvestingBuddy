"""Research decisions — V3.17.1.

WHAT THIS CLOSES
================
The discovery council has been emitting ``research_next`` per candidate for several
phases, into
``discovery_runs.config_json["discovery_council"]["review"]["candidates_to_research_next"]``.
**Backend consumers of that field: zero.** It is rendered to a human and goes no further —
no column, no status, no index, nothing a query can reach. See
``docs/v3.17-research-escalation-design.md`` §1.1.

A decision that lives only inside a JSON blob cannot be asked "how many rounds has this
company had?", "what did the last round improve?" or "when do we stop?". This table is
where a decision gets a lifecycle of its own.

WHY NOT JUST USE ``research_jobs``
==================================
Because a job is one execution and a decision outlives its executions. ``research_jobs``
(migration 019) keeps its idempotency, leases, retries and dead-lettering and remains the
queue; V3.17 does **not** build a second one beside it. The decision is the *reason* the
jobs exist, and putting a reason into ``payload_json`` would make it unqueryable — which
is the mistake this migration exists to stop repeating.

ADDITIVE ONLY
=============
One new table. No column added, altered or dropped on any existing table. No backfill, no
data migration, no default that rewrites a row. Every foreign key is ``SET NULL``, never
``CASCADE`` (CLAUDE.md #15): deleting a discovery run must not delete the record that
research happened because of it.

Downgrade drops the table and its indexes and nothing else. Verify upgrade → downgrade →
upgrade on real PostgreSQL 16 before this goes near production.

THE PARTIAL UNIQUE INDEX IS THE POINT
=====================================
``ux_research_decisions_one_open`` makes "one open decision per company" a **database**
constraint. A read-then-write check in the controller loses under concurrency, and two
councils reviewing the same company concurrently is exactly the case that produces
duplicate *paid* research. The precedent is
``ix_research_document_versions_one_current`` (migration 022).

The predicate lists the five open states explicitly. It is generated below from
``OPEN_STATUSES`` so that the index and the state machine cannot drift apart — a prompt
and a constant drifting apart is precisely what V3.16.1b was about.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.research_decision import DEFAULT_MAX_ROUNDS, OPEN_STATUSES

# revision identifiers
revision: str = "040"
down_revision: str | None = "039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``status IN ('research_required', 'queued', …)`` — built from the same constant the
#: application reads, never typed out twice.
_OPEN_PREDICATE = "status IN (" + ", ".join(f"'{s}'" for s in OPEN_STATUSES) + ")"


def upgrade() -> None:
    op.create_table(
        "research_decisions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        # Lineage — all SET NULL.
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("discovery_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("discovery_candidate_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("last_job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("decision", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        # NOT NULL: a decision nobody can audit is not a decision.
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "priority", sa.Integer(), nullable=False, server_default="100"
        ),
        sa.Column(
            "escalation_round", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "max_rounds",
            sa.Integer(),
            nullable=False,
            server_default=str(DEFAULT_MAX_ROUNDS),
        ),
        sa.Column(
            "evidence_before_json",
            sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column(
            "evidence_after_json",
            sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column(
            "improvement_json",
            sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        # NULL = unpriced. NEVER 0 — a stored zero asserts the work was free.
        sa.Column("cost_usd_total", sa.Numeric(12, 4), nullable=True),
        sa.Column("terminal_reason", sa.String(80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_research_decisions_company_id_companies",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["discovery_run_id"],
            ["discovery_runs.id"],
            name="fk_research_decisions_discovery_run_id_discovery_runs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["discovery_candidate_id"],
            ["discovery_candidates.id"],
            name="fk_research_decisions_candidate_id_discovery_candidates",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_job_id"],
            ["research_jobs.id"],
            name="fk_research_decisions_last_job_id_research_jobs",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_research_decisions_status_priority",
        "research_decisions",
        ["status", "priority", "created_at"],
    )
    op.create_index(
        "ix_research_decisions_company_id", "research_decisions", ["company_id"]
    )
    op.create_index(
        "ix_research_decisions_discovery_run_id",
        "research_decisions",
        ["discovery_run_id"],
    )
    # One open decision per company — the database's job, not the controller's.
    op.create_index(
        "ux_research_decisions_one_open",
        "research_decisions",
        ["company_id"],
        unique=True,
        postgresql_where=sa.text(_OPEN_PREDICATE),
        sqlite_where=sa.text(_OPEN_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index("ux_research_decisions_one_open", table_name="research_decisions")
    op.drop_index(
        "ix_research_decisions_discovery_run_id", table_name="research_decisions"
    )
    op.drop_index("ix_research_decisions_company_id", table_name="research_decisions")
    op.drop_index(
        "ix_research_decisions_status_priority", table_name="research_decisions"
    )
    op.drop_table("research_decisions")
