"""Add the agent tool-call audit record — V3.3 Slice 3.1.

PURPOSE
=======
One row per **attempted** tool call, including refusals and errors. It is
simultaneously the audit log (CLAUDE.md rule 9), the cost ledger in the units
``consumption.UNIT_NAMES`` already defines, and the debugging trace for a run nobody
can reproduce.

ADDITIVE ONLY. One new table, nothing altered, so ``release/v2-current`` runs unchanged
against a database with it applied. Nothing writes to it unless
``V3_AGENT_TOOLS_ENABLED`` is on, which is off by default.

WHY REFUSALS ARE STORED
=======================
A refused call is the most informative row here. ``tool_not_permitted`` aggregated per
role says the *Director* gave that role the wrong tools; ``budget_exceeded`` says which
limit bites first, which is the measurement OPEN DECISION #14 asks for. Keeping only
the calls that worked leaves the one population that can answer neither question — so
``ck_research_tool_calls_refusal_has_a_reason`` makes an unaggregatable refusal
unstorable.

WHY THE PAYLOAD IS NOT A COLUMN
===============================
``summary`` is one line and ``item_count`` is a number. Payloads can be large, and an
audit log that stores them stops being readable and becomes a second, unindexed copy of
the corpus. ``arguments_json`` *is* stored, because "which arguments produced this" is
what a debugging trace is for and arguments are bounded by the tool's own schema.
``error_type`` is the exception type and never its message: a message can carry a
fragment of fetched content, and this table is read by humans **and by prompts**.

FOREIGN KEYS
============
All three are ``SET NULL``. Every one is lineage, not composition: the audit record
outlives the job, the company and the entity it refers to — which is the entire point
of an audit record, and CLAUDE.md rule 15 applied to a trace rather than to a report.

INDEXES
=======
* ``ix_research_tool_calls_job_created`` — "every call in this job, in order": the
  debugging trace, and the only query with a natural sort.
* ``ix_research_tool_calls_tool_outcome`` — "how often is this tool refused": the
  planning report.
* ``ix_research_tool_calls_role`` — per-role aggregates.
* ``ix_research_tool_calls_company_id`` — "everything the platform did for this issuer".

NULLABILITY IS A STATEMENT
==========================
``consumption_json`` holds only the units the tool declared instrumented, and
``instrumented_units_json`` names them. A unit absent from that list is **unmeasured**,
not zero, and a reader that treats the two alike is reading fabricated zeros — the same
discipline ``research_run_consumption`` (020) established. ``estimated_cost_usd`` NULL
means unpriced, never free.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers
revision: str = "029"
down_revision: str | None = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_tool_calls",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("research_job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("legal_entity_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(length=60), nullable=False),
        sa.Column("tool_name", sa.String(length=60), nullable=False),
        sa.Column("task_ref", sa.String(length=120), nullable=True),
        sa.Column("arguments_json", JSONB, nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("refusal_reason", sa.String(length=60), nullable=True),
        sa.Column("limit_hit", sa.String(length=60), nullable=True),
        sa.Column("error_type", sa.String(length=120), nullable=True),
        sa.Column("summary", sa.String(length=500), nullable=True),
        sa.Column(
            "item_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "contains_untrusted_content",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("consumption_json", JSONB, nullable=True),
        sa.Column("instrumented_units_json", JSONB, nullable=True),
        sa.Column(
            "latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["research_job_id"],
            ["research_jobs.id"],
            name="fk_research_tool_calls_research_job_id_research_jobs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_research_tool_calls_company_id_companies",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entities.id"],
            name="fk_research_tool_calls_legal_entity_id_legal_entities",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "outcome IN ('ok', 'refused', 'error')",
            name="ck_research_tool_calls_outcome",
        ),
        sa.CheckConstraint(
            "outcome <> 'refused' OR refusal_reason IS NOT NULL",
            name="ck_research_tool_calls_refusal_has_a_reason",
        ),
        sa.CheckConstraint(
            "item_count >= 0 AND latency_ms >= 0",
            name="ck_research_tool_calls_counts_non_negative",
        ),
    )
    op.create_index(
        "ix_research_tool_calls_job_created",
        "research_tool_calls",
        ["research_job_id", "created_at"],
    )
    op.create_index(
        "ix_research_tool_calls_tool_outcome",
        "research_tool_calls",
        ["tool_name", "outcome"],
    )
    op.create_index("ix_research_tool_calls_role", "research_tool_calls", ["role"])
    op.create_index(
        "ix_research_tool_calls_company_id", "research_tool_calls", ["company_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_tool_calls_company_id", table_name="research_tool_calls"
    )
    op.drop_index("ix_research_tool_calls_role", table_name="research_tool_calls")
    op.drop_index(
        "ix_research_tool_calls_tool_outcome", table_name="research_tool_calls"
    )
    op.drop_index(
        "ix_research_tool_calls_job_created", table_name="research_tool_calls"
    )
    op.drop_table("research_tool_calls")
