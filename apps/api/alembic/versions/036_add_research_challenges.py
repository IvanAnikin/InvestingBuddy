"""The Red Team challenge record — V3.7 Slice 7.2.

PURPOSE
=======
`CURRENT`: the red team is one agent in a fixed sequence, reasoning over the same frozen
pack as everyone else, with **no mechanism for the challenged analyst to answer**. It can
say a claim looks weak; nothing responds, and nothing records what happened next.

This table makes a challenge a first-class record: it targets a ``finding_id`` — not a
paragraph — states a weakness class from a closed vocabulary, and has exactly one outcome.

ADDITIVE ONLY. One new table, nothing altered.

FOUR CHECK CONSTRAINTS
======================
* ``ck_research_challenges_one_round`` — **``round_index = 1``.** Multi-round debate
  between language models produces text, not truth: agents converge on whoever wrote last
  and the token cost grows with nothing to show for it. Allowing a second round later is a
  reviewed change to this constraint, not something a caller does by passing a 2.
* ``ck_research_challenges_resolution_has_a_response`` — "resolved" with no response is
  the challenge being dropped rather than met.
* ``ck_research_challenges_response_has_evidence`` — and the answer must carry evidence.
  A response without it is an assertion, and the round exists precisely to force the
  challenged claim either to acquire support or to be marked weak.
* ``ck_research_challenges_weakness_class`` / ``_outcome`` — closed vocabularies, so
  "which weakness does this platform keep producing" is answerable.

**An unresolved challenge is not a failure of the run.** It is one of its more valuable
outputs, and it reaches the Chair through this table and — when a counterpart finding
exists — as a ``ResearchDisagreement``.

FOREIGN KEYS
============
``CASCADE`` from run and finding: a challenge against a finding that is gone is
uninterpretable. ``SET NULL`` to the disagreement, which is lineage.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers
revision: str = "036"
down_revision: str | None = "035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_challenges",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "research_run_id",
            sa.Uuid(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "finding_id",
            sa.Uuid(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "weakness_class",
            sa.String(length=40),
            nullable=False,
        ),
        sa.Column(
            "challenge_text",
            sa.String(length=2000),
            nullable=False,
        ),
        sa.Column(
            "round_index",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "outcome",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'unresolved'"),
        ),
        sa.Column(
            "response_text",
            sa.String(length=2000),
            nullable=True,
        ),
        sa.Column(
            "response_evidence_ids_json",
            JSONB,
            nullable=True,
        ),
        sa.Column(
            "response_evidence_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "responding_role",
            sa.String(length=60),
            nullable=True,
        ),
        sa.Column(
            "finding_confidence_before",
            sa.Float(),
            nullable=True,
        ),
        sa.Column(
            "finding_confidence_after",
            sa.Float(),
            nullable=True,
        ),
        sa.Column(
            "withdrew_finding",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "disagreement_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "responded_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["disagreement_id"],
            ["research_disagreements.id"],
            name="fk_research_challenges_disagreement_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["research_findings.id"],
            name="fk_research_challenges_finding_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["research_runs.id"],
            name="fk_research_challenges_run_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "response_evidence_count >= 0",
            name="ck_research_challenges_evidence_count_not_negative",
        ),
        sa.CheckConstraint(
            "round_index = 1",
            name="ck_research_challenges_one_round",
        ),
        sa.CheckConstraint(
            "outcome IN ('resolved', 'partially_resolved', 'unresolved')",
            name="ck_research_challenges_outcome",
        ),
        sa.CheckConstraint(
            "outcome = 'unresolved' OR response_text IS NOT NULL",
            name="ck_research_challenges_resolution_has_a_response",
        ),
        sa.CheckConstraint(
            "outcome = 'unresolved' OR response_evidence_count > 0",
            name="ck_research_challenges_response_has_evidence",
        ),
        sa.CheckConstraint(
            "weakness_class IN ('unsupported_extrapolation', 'single_source', "
            "'period_mismatch', 'scope_mismatch', 'survivorship', 'stale_evidence', "
            "'contradicted_by_evidence')",
            name="ck_research_challenges_weakness_class",
        ),
    )
    op.create_index(
        "ix_research_challenges_finding_id",
        "research_challenges",
        ["finding_id"],
    )
    op.create_index(
        "ix_research_challenges_run_outcome",
        "research_challenges",
        ["research_run_id", "outcome"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_challenges_run_outcome", table_name="research_challenges")
    op.drop_index("ix_research_challenges_finding_id", table_name="research_challenges")
    op.drop_table("research_challenges")
