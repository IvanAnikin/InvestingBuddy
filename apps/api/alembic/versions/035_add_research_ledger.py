"""The Research Ledger — V3.5 Slice 5.1.

PURPOSE
=======
Seven tables that replace V2's coordination mechanism — a bounded free-text summary of
the previous agent (``council.py:664-704``) — with structured state a run can query,
challenge and remember.

ADDITIVE ONLY. Seven new tables, nothing altered. Nothing writes to them unless the V3
research loop runs, which nothing schedules.

THE CHECK THAT MATTERS
======================
``ck_research_findings_has_support``. **A finding without evidence ids or calculation ids
is not a finding**, and there is no "trust me" state — a model's confident sentence cannot
enter the ledger by any path, including one that bypasses the service layer.

It reads two DERIVED integer columns rather than the JSON arrays beside them, because a
portable CHECK cannot look inside a JSON column and because ``store.record_finding`` is
the only writer of both. Drift is unrepresentable rather than checked for, which is the
rule V3.2 established for exactly this shape of problem.

FIVE MORE CONSTRAINTS, EACH A SENTENCE THE SCHEMA REFUSES TO STORE
=================================================================
* ``ck_research_runs_stopped_names_a_limit`` — "we stopped because the search budget ran
  out with three questions unanswered" is useful; a stopped run with no named limit is one
  nobody can widen a budget for.
* ``ck_research_gaps_closed_names_a_finding`` — "closed" with nothing behind it is the gap
  disappearing rather than being answered.
* ``ck_research_disagreements_resolution_is_explained`` — a resolved conflict with no note
  is the conflict being closed rather than answered, which is precisely the laundering
  the Chair rule forbids.
* ``ck_research_disagreements_two_findings`` — a finding cannot disagree with itself.
* ``ck_research_findings_confidence_range`` — confidence is a probability.

FOREIGN KEYS
============
``CASCADE`` from a run to everything in it: a question, task, finding or gap belonging to
no run is uninterpretable, unlike an audit row whose company was deleted. ``SET NULL`` to
``companies``, ``legal_entities`` and ``research_jobs``, which are lineage.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers
revision: str = "035"
down_revision: str | None = "034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("legal_entity_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("research_job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("stopped_by", sa.String(length=40), nullable=True),
        sa.Column("rounds_completed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("playbook_versions_json", JSONB, nullable=True),
        sa.Column("budget_json", JSONB, nullable=True),
        sa.Column("consumption_json", JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_research_runs_company_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entities.id"],
            name="fk_research_runs_legal_entity_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["research_job_id"],
            ["research_jobs.id"],
            name="fk_research_runs_research_job_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('planning', 'investigating', 'reviewing', 'complete', 'stopped')",
            name="ck_research_runs_status",
        ),
        sa.CheckConstraint(
            "status <> 'stopped' OR stopped_by IS NOT NULL",
            name="ck_research_runs_stopped_names_a_limit",
        ),
    )
    op.create_index(
        "ix_research_runs_company_started",
        "research_runs",
        ["company_id", "started_at"],
    )
    op.create_index("ix_research_runs_status", "research_runs", ["status"])

    op.create_table(
        "research_questions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("research_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("question_key", sa.String(length=80), nullable=False),
        sa.Column("text", sa.String(length=1000), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("blocking", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("required_evidence_classes_json", JSONB, nullable=True),
        sa.Column(
            "resolution_status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["research_runs.id"],
            name="fk_research_questions_run_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "origin IN ('playbook', 'director', 'red_team', 'prior_gap')",
            name="ck_research_questions_origin",
        ),
        sa.CheckConstraint(
            "resolution_status IN ('open', 'answered', 'unanswerable')",
            name="ck_research_questions_resolution_status",
        ),
    )
    op.create_index(
        "ix_research_questions_run_key",
        "research_questions",
        ["research_run_id", "question_key"],
        unique=True,
    )
    op.create_index(
        "ix_research_questions_run_status",
        "research_questions",
        ["research_run_id", "resolution_status"],
    )

    op.create_table(
        "research_tasks",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("research_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=60), nullable=False),
        sa.Column("round_index", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("question_keys_json", JSONB, nullable=True),
        sa.Column("tool_budget_json", JSONB, nullable=True),
        sa.Column("max_iterations", sa.Integer(), nullable=False, server_default=sa.text("4")),
        sa.Column("stopped_by", sa.String(length=40), nullable=True),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["research_runs.id"],
            name="fk_research_tasks_run_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "max_iterations > 0",
            name="ck_research_tasks_iterations_bounded",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'complete', 'partial', 'failed', 'skipped')",
            name="ck_research_tasks_status",
        ),
    )
    op.create_index(
        "ix_research_tasks_run_round",
        "research_tasks",
        ["research_run_id", "round_index"],
    )
    op.create_index("ix_research_tasks_run_status", "research_tasks", ["research_run_id", "status"])

    op.create_table(
        "research_findings",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("research_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("research_task_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("question_key", sa.String(length=80), nullable=True),
        sa.Column("statement", sa.String(length=2000), nullable=False),
        sa.Column("mechanism", sa.String(length=2000), nullable=True),
        sa.Column("direction", sa.String(length=20), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("evidence_ids_json", JSONB, nullable=True),
        sa.Column("calculation_ids_json", JSONB, nullable=True),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("calculation_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("period_key", sa.String(length=20), nullable=True),
        sa.Column("period_type", sa.String(length=20), nullable=True),
        sa.Column("scope_type", sa.String(length=20), nullable=True),
        sa.Column("scope_key", sa.String(length=220), nullable=True),
        sa.Column("originating_role", sa.String(length=60), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column(
            "verification_status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'unverified'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["research_runs.id"],
            name="fk_research_findings_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["research_task_id"],
            ["research_tasks.id"],
            name="fk_research_findings_task_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_research_findings_confidence_range",
        ),
        sa.CheckConstraint(
            "evidence_count >= 0 AND calculation_count >= 0",
            name="ck_research_findings_counts_are_not_negative",
        ),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('supportive', 'adverse', 'neutral')",
            name="ck_research_findings_direction",
        ),
        sa.CheckConstraint(
            "evidence_count > 0 OR calculation_count > 0",
            name="ck_research_findings_has_support",
        ),
        sa.CheckConstraint(
            "verification_status IN ('verified', 'unverified', 'withdrawn')",
            name="ck_research_findings_verification_status",
        ),
    )
    op.create_index("ix_research_findings_run", "research_findings", ["research_run_id"])
    op.create_index(
        "ix_research_findings_run_question",
        "research_findings",
        ["research_run_id", "question_key"],
    )

    op.create_table(
        "research_gaps",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("research_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("question_key", sa.String(length=80), nullable=True),
        sa.Column("gap_type", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("why_it_matters", sa.String(length=1000), nullable=True),
        sa.Column("sources_tried_json", JSONB, nullable=True),
        sa.Column("closable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("blocks_council", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'open'")),
        sa.Column("closed_by_finding_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["closed_by_finding_id"],
            ["research_findings.id"],
            name="fk_research_gaps_closed_by_finding_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["research_runs.id"],
            name="fk_research_gaps_run_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status <> 'closed' OR closed_by_finding_id IS NOT NULL",
            name="ck_research_gaps_closed_names_a_finding",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'closed', 'accepted')",
            name="ck_research_gaps_status",
        ),
    )
    op.create_index("ix_research_gaps_run_status", "research_gaps", ["research_run_id", "status"])
    op.create_index("ix_research_gaps_type", "research_gaps", ["gap_type"])

    op.create_table(
        "research_disagreements",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("research_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("finding_a_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("finding_b_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("nature", sa.String(length=30), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column(
            "resolution",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'unresolved'"),
        ),
        sa.Column("resolution_note", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["finding_a_id"],
            ["research_findings.id"],
            name="fk_research_disagreements_finding_a_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["finding_b_id"],
            ["research_findings.id"],
            name="fk_research_disagreements_finding_b_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["research_runs.id"],
            name="fk_research_disagreements_run_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "nature IN ('value', 'period', 'scope', 'interpretation', 'source_quality')",
            name="ck_research_disagreements_nature",
        ),
        sa.CheckConstraint(
            "resolution IN ('resolved', 'partially_resolved', 'unresolved')",
            name="ck_research_disagreements_resolution",
        ),
        sa.CheckConstraint(
            "resolution = 'unresolved' OR resolution_note IS NOT NULL",
            name="ck_research_disagreements_resolution_is_explained",
        ),
        sa.CheckConstraint(
            "finding_a_id <> finding_b_id",
            name="ck_research_disagreements_two_findings",
        ),
    )
    op.create_index(
        "ix_research_disagreements_run_resolution",
        "research_disagreements",
        ["research_run_id", "resolution"],
    )

    op.create_table(
        "research_hypotheses",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("research_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("statement", sa.String(length=2000), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'open'")),
        sa.Column("supporting_finding_ids_json", JSONB, nullable=True),
        sa.Column("contradicting_finding_ids_json", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["research_runs.id"],
            name="fk_research_hypotheses_run_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'supported', 'contradicted', 'inconclusive')",
            name="ck_research_hypotheses_status",
        ),
    )
    op.create_index("ix_research_hypotheses_run", "research_hypotheses", ["research_run_id"])


def downgrade() -> None:
    op.drop_index("ix_research_hypotheses_run", table_name="research_hypotheses")
    op.drop_table("research_hypotheses")
    op.drop_index("ix_research_disagreements_run_resolution", table_name="research_disagreements")
    op.drop_table("research_disagreements")
    op.drop_index("ix_research_gaps_type", table_name="research_gaps")
    op.drop_index("ix_research_gaps_run_status", table_name="research_gaps")
    op.drop_table("research_gaps")
    op.drop_index("ix_research_findings_run_question", table_name="research_findings")
    op.drop_index("ix_research_findings_run", table_name="research_findings")
    op.drop_table("research_findings")
    op.drop_index("ix_research_tasks_run_status", table_name="research_tasks")
    op.drop_index("ix_research_tasks_run_round", table_name="research_tasks")
    op.drop_table("research_tasks")
    op.drop_index("ix_research_questions_run_status", table_name="research_questions")
    op.drop_index("ix_research_questions_run_key", table_name="research_questions")
    op.drop_table("research_questions")
    op.drop_index("ix_research_runs_status", table_name="research_runs")
    op.drop_index("ix_research_runs_company_started", table_name="research_runs")
    op.drop_table("research_runs")
