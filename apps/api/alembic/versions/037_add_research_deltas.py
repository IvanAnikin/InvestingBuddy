"""The ResearchDelta — V3.8 Slice 8.2.

PURPOSE
=======
The answer to the question a returning investor actually has:

> **What changed since the previous analysis, and which prior conclusions should be
> revisited?**

Computed between two runs and **persisted**, so the answer is still available next quarter
when somebody asks why a conclusion was dropped.

ADDITIVE ONLY. One new table, nothing altered.

THE PRIOR FINDING IS NOT MODIFIED
=================================
An invalidated finding stays exactly as it was written. Invalidation is a property of the
**relationship between two runs**, not of the old finding — it was a correct conclusion
from the evidence available then, and rewriting it would make the earlier run's record a
lie in service of the later one's convenience. So the delta row carries the invalidation
and the old row is untouched.

TWO CHECK CONSTRAINTS, AND ONE DELIBERATELY ABSENT
==================================================
* ``ck_research_deltas_unchanged_means_nothing_invalidated`` — an unchanged thesis with
  invalidated findings is the row contradicting itself: a conclusion the new evidence
  undermines **is** a change to the thesis.
There is deliberately **no** "must name a run" CHECK, and that is a defect a real DELETE
found rather than an omission: with both foreign keys ``SET NULL``, deleting both runs
nulls both columns, and such a CHECK would **refuse that update** — making the very pruning
the cascade exists for impossible. The rule it reached for is about *creation*, and
``memory.delta.persist_delta`` enforces it there.
* ``ck_research_deltas_counts_are_not_negative``.

And one unique index: ``ix_research_deltas_run_pair``. Two deltas between the same two runs
would be two answers to "what changed", and nothing could say which was current.

FOREIGN KEYS
============
All ``SET NULL``. A delta **outlives** the runs it compares: "this conclusion was
invalidated in September" stays true when a run record is eventually pruned.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers
revision: str = "037"
down_revision: str | None = "036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_deltas",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "from_run_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "to_run_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "unchanged_core_thesis",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "new_evidence_ids_json",
            JSONB,
            nullable=True,
        ),
        sa.Column(
            "changed_facts_json",
            JSONB,
            nullable=True,
        ),
        sa.Column(
            "resolved_question_keys_json",
            JSONB,
            nullable=True,
        ),
        sa.Column(
            "new_gap_ids_json",
            JSONB,
            nullable=True,
        ),
        sa.Column(
            "closed_gap_ids_json",
            JSONB,
            nullable=True,
        ),
        sa.Column(
            "invalidated_finding_ids_json",
            JSONB,
            nullable=True,
        ),
        sa.Column(
            "new_evidence_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "changed_fact_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "resolved_question_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "new_gap_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "closed_gap_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "invalidated_finding_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_research_deltas_company_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["from_run_id"],
            ["research_runs.id"],
            name="fk_research_deltas_from_run_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["to_run_id"],
            ["research_runs.id"],
            name="fk_research_deltas_to_run_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "new_evidence_count >= 0 AND changed_fact_count >= 0 "
            "AND resolved_question_count >= 0 AND new_gap_count >= 0 "
            "AND closed_gap_count >= 0 AND invalidated_finding_count >= 0",
            name="ck_research_deltas_counts_are_not_negative",
        ),
        sa.CheckConstraint(
            "unchanged_core_thesis = false OR invalidated_finding_count = 0",
            name="ck_research_deltas_unchanged_means_nothing_invalidated",
        ),
    )
    op.create_index(
        "ix_research_deltas_company_computed",
        "research_deltas",
        ["company_id", "computed_at"],
    )
    op.create_index(
        "ix_research_deltas_run_pair",
        "research_deltas",
        ["from_run_id", "to_run_id"],
        unique=True,
    )
    op.create_index(
        "ix_research_deltas_to_run_id",
        "research_deltas",
        ["to_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_deltas_to_run_id", table_name="research_deltas")
    op.drop_index("ix_research_deltas_run_pair", table_name="research_deltas")
    op.drop_index("ix_research_deltas_company_computed", table_name="research_deltas")
    op.drop_table("research_deltas")
