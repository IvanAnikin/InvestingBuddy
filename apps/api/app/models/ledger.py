"""The Research Ledger — V3.5 Slice 5.1.

WHAT IT REPLACES
================
V2's agents coordinate through a bounded free-text summary of the previous agent
(``council.py:664-704`` ``_prior_summaries`` / ``_compact_agent_line``). That is lossy by
construction and cannot be queried, challenged or remembered. The ledger is the shared
**structured** state of one research run.

THE RULE THE SCHEMA ENFORCES
============================
> **A finding without evidence ids or calculation ids is not a finding.**

``DATA_AND_EVIDENCE_ARCHITECTURE.md`` §7 states it and this is where it becomes
executable. There is no "trust me" state: ``ck_research_findings_has_support`` refuses the
row, so a model's confident sentence cannot enter the ledger by any path.

The counts are **derived columns**, not a second source of truth. The V3.2 lesson — make
drift unrepresentable rather than checking for it — applied to a constraint that would
otherwise need a JSON function no portable schema has. ``evidence_count`` is written from
``len(evidence_ids)`` in one place, and the CHECK reads the count.

WHY REFUSALS AND GAPS ARE ROWS
==============================
Same reason as ``research_tool_calls`` (029), ``calculation_records`` (030) and
``research_leads`` (031). A ``ResearchGap`` in V2 is a caveat printed in a report; here it
is an **actionable work item** the bounded loop consumes, and one that survives the run so
the next one can start from it (V3.8).

WHY A DISAGREEMENT IS A ROW
===========================
Because the Chair's job is to surface conflict, never to silently pick a number. A
disagreement that lived only in prose would be smoothed away by the next summarisation
step, which is precisely the failure the ledger exists to remove.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchRun(Base):
    """One investigation of one subject, with its budget and how it ended."""

    __tablename__ = "research_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id", ondelete="SET NULL", name="fk_research_runs_company_id"
        ),
        nullable=True,
    )
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "legal_entities.id",
            ondelete="SET NULL",
            name="fk_research_runs_legal_entity_id",
        ),
        nullable=True,
    )
    research_job_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_jobs.id",
            ondelete="SET NULL",
            name="fk_research_runs_research_job_id",
        ),
        nullable=True,
    )
    #: ``quick`` | ``standard`` | ``deep`` | ``max`` — a depth preset, never a price tier.
    mode: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    #: ``planning`` | ``investigating`` | ``reviewing`` | ``complete`` | ``stopped``.
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    #: **Which limit ended it**, from ``ModeLimits``. NULL only while it is still
    #: running or when the completion rules were satisfied. "We stopped because the
    #: search budget ran out with three questions unanswered" is useful; "analysis
    #: complete" would be a lie.
    stopped_by: Mapped[str | None] = mapped_column(sa.String(40))
    rounds_completed: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    #: Which playbook versions produced this run. A report states its methodology
    #: version, and that has to be a checkable fact about the run rather than a claim
    #: about a string that was in a context window.
    playbook_versions_json: Mapped[dict | None] = mapped_column(JSONB)
    budget_json: Mapped[dict | None] = mapped_column(JSONB)
    consumption_json: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index("ix_research_runs_company_started", "company_id", "started_at"),
        sa.Index("ix_research_runs_status", "status"),
        sa.CheckConstraint(
            "status IN ('planning', 'investigating', 'reviewing', 'complete', "
            "'stopped')",
            name="ck_research_runs_status",
        ),
        # A stopped run must say WHICH limit stopped it. A run that stopped for an
        # unnamed reason is one nobody can widen a budget for.
        sa.CheckConstraint(
            "status <> 'stopped' OR stopped_by IS NOT NULL",
            name="ck_research_runs_stopped_names_a_limit",
        ),
    )


class ResearchQuestion(Base):
    """One question the run set out to answer, and whether it did."""

    __tablename__ = "research_questions"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_runs.id",
            ondelete="CASCADE",
            name="fk_research_questions_run_id",
        ),
        nullable=False,
    )
    #: Stable within the run, so a playbook can name its own questions and a report can
    #: refer to one without a UUID.
    question_key: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    text: Mapped[str] = mapped_column(sa.String(1000), nullable=False)
    #: ``playbook`` | ``director`` | ``red_team`` | ``prior_gap``. Where a question came
    #: from decides how seriously an unanswered one is taken.
    origin: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    priority: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=3, server_default=sa.text("3")
    )
    #: A blocking question that cannot be answered stops the Council from convening.
    #: The run reports insufficient evidence instead of analysing around the hole.
    blocking: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    required_evidence_classes_json: Mapped[list | None] = mapped_column(JSONB)
    #: ``open`` | ``answered`` | ``unanswerable``. ``unanswerable`` is a real outcome:
    #: the sources were tried and the answer is not obtainable.
    resolution_status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default="open", server_default=sa.text("'open'")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index(
            "ix_research_questions_run_key",
            "research_run_id",
            "question_key",
            unique=True,
        ),
        sa.Index(
            "ix_research_questions_run_status", "research_run_id", "resolution_status"
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


class ResearchTask(Base):
    """One unit of assigned work: a role, some questions, and a bounded budget."""

    __tablename__ = "research_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_runs.id", ondelete="CASCADE", name="fk_research_tasks_run_id"
        ),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    #: Which round created it. Round 0 is the Director's initial plan; later rounds are
    #: follow-ups the gap review produced.
    round_index: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    #: ``pending`` | ``running`` | ``complete`` | ``partial`` | ``failed`` | ``skipped``.
    #: ``partial`` exists because a task that ran out of budget produced something, and
    #: reporting it as complete would present a thin answer as a finished one.
    status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default="pending", server_default=sa.text("'pending'")
    )
    question_keys_json: Mapped[list | None] = mapped_column(JSONB)
    tool_budget_json: Mapped[dict | None] = mapped_column(JSONB)
    max_iterations: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=4, server_default=sa.text("4")
    )
    #: Which limit stopped this task, when one did.
    stopped_by: Mapped[str | None] = mapped_column(sa.String(40))
    detail: Mapped[str | None] = mapped_column(sa.String(500))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.Index("ix_research_tasks_run_round", "research_run_id", "round_index"),
        sa.Index("ix_research_tasks_run_status", "research_run_id", "status"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'complete', 'partial', 'failed', "
            "'skipped')",
            name="ck_research_tasks_status",
        ),
        sa.CheckConstraint(
            "max_iterations > 0", name="ck_research_tasks_iterations_bounded"
        ),
    )


class ResearchFinding(Base):
    """One evidence-linked statement. **Never a claim without support.**"""

    __tablename__ = "research_findings"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_runs.id", ondelete="CASCADE", name="fk_research_findings_run_id"
        ),
        nullable=False,
    )
    research_task_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_tasks.id",
            ondelete="SET NULL",
            name="fk_research_findings_task_id",
        ),
        nullable=True,
    )
    question_key: Mapped[str | None] = mapped_column(sa.String(80))
    statement: Mapped[str] = mapped_column(sa.String(2000), nullable=False)
    #: *Why* it is true, not a restatement of it. The distinction ADR-041 established
    #: for council output: a finding with no mechanism is an observation.
    mechanism: Mapped[str | None] = mapped_column(sa.String(2000))
    #: ``supportive`` | ``adverse`` | ``neutral``. Never a rating.
    direction: Mapped[str | None] = mapped_column(sa.String(20))
    confidence: Mapped[float | None] = mapped_column(sa.Float)
    #: Stable evidence ids (``ev:<chunk_id>``) and calculation record ids.
    evidence_ids_json: Mapped[list | None] = mapped_column(JSONB)
    calculation_ids_json: Mapped[list | None] = mapped_column(JSONB)
    #: DERIVED from the lists above, in one place. The V3.2 rule: make drift
    #: unrepresentable rather than checking for it — and a portable CHECK cannot read
    #: inside a JSON column.
    evidence_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    calculation_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    period_key: Mapped[str | None] = mapped_column(sa.String(20))
    period_type: Mapped[str | None] = mapped_column(sa.String(20))
    scope_type: Mapped[str | None] = mapped_column(sa.String(20))
    scope_key: Mapped[str | None] = mapped_column(sa.String(220))
    originating_role: Mapped[str | None] = mapped_column(sa.String(60))
    provider: Mapped[str | None] = mapped_column(sa.String(40))
    #: ``verified`` | ``unverified`` | ``withdrawn``. A finding the Red Team withdrew
    #: is kept with its status, not deleted.
    verification_status: Mapped[str] = mapped_column(
        sa.String(20),
        nullable=False,
        default="unverified",
        server_default=sa.text("'unverified'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index("ix_research_findings_run", "research_run_id"),
        sa.Index("ix_research_findings_run_question", "research_run_id", "question_key"),
        # THE RULE. A finding without evidence ids or calculation ids is not a finding,
        # and there is no "trust me" state.
        sa.CheckConstraint(
            "evidence_count > 0 OR calculation_count > 0",
            name="ck_research_findings_has_support",
        ),
        sa.CheckConstraint(
            "evidence_count >= 0 AND calculation_count >= 0",
            name="ck_research_findings_counts_are_not_negative",
        ),
        sa.CheckConstraint(
            "verification_status IN ('verified', 'unverified', 'withdrawn')",
            name="ck_research_findings_verification_status",
        ),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('supportive', 'adverse', 'neutral')",
            name="ck_research_findings_direction",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_research_findings_confidence_range",
        ),
    )


class ResearchGap(Base):
    """Something the run could not establish. **A work item, not a caveat.**"""

    __tablename__ = "research_gaps"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_runs.id", ondelete="CASCADE", name="fk_research_gaps_run_id"
        ),
        nullable=False,
    )
    question_key: Mapped[str | None] = mapped_column(sa.String(80))
    #: From a closed vocabulary so gaps aggregate. "Which gap class does this platform
    #: keep hitting" is a statement about the *pipeline*, and free text cannot make it.
    gap_type: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    description: Mapped[str] = mapped_column(sa.String(1000), nullable=False)
    #: Why it matters. A gap nobody can weigh is a caveat again.
    why_it_matters: Mapped[str | None] = mapped_column(sa.String(1000))
    #: Which sources were tried. Without it, "we could not find it" is unfalsifiable.
    sources_tried_json: Mapped[list | None] = mapped_column(JSONB)
    #: Whether a follow-up task could plausibly close it. ``False`` means the loop
    #: should not spend another round on it — which is what stops a bounded loop from
    #: burning its budget on the one thing no source has.
    closable: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    #: True when this gap prevents the Council from convening.
    blocks_council: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    #: ``open`` | ``closed`` | ``accepted``. ``accepted`` = the run finished with the
    #: gap open and said so, which is the honest outcome for an unclosable gap.
    status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default="open", server_default=sa.text("'open'")
    )
    closed_by_finding_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_findings.id",
            ondelete="SET NULL",
            name="fk_research_gaps_closed_by_finding_id",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index("ix_research_gaps_run_status", "research_run_id", "status"),
        sa.Index("ix_research_gaps_type", "gap_type"),
        sa.CheckConstraint(
            "status IN ('open', 'closed', 'accepted')", name="ck_research_gaps_status"
        ),
        # A closed gap must name what closed it. "Closed" with nothing behind it is the
        # gap disappearing rather than being answered.
        sa.CheckConstraint(
            "status <> 'closed' OR closed_by_finding_id IS NOT NULL",
            name="ck_research_gaps_closed_names_a_finding",
        ),
    )


class ResearchDisagreement(Base):
    """Two findings that conflict. Persisted so the Chair cannot smooth it away."""

    __tablename__ = "research_disagreements"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_runs.id",
            ondelete="CASCADE",
            name="fk_research_disagreements_run_id",
        ),
        nullable=False,
    )
    finding_a_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_findings.id",
            ondelete="CASCADE",
            name="fk_research_disagreements_finding_a_id",
        ),
        nullable=False,
    )
    finding_b_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_findings.id",
            ondelete="CASCADE",
            name="fk_research_disagreements_finding_b_id",
        ),
        nullable=False,
    )
    #: What kind of conflict: ``value`` | ``period`` | ``scope`` | ``interpretation`` |
    #: ``source_quality``.
    nature: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.String(1000))
    #: ``resolved`` | ``partially_resolved`` | ``unresolved``. **An unresolved
    #: disagreement is not a failure of the run — it is one of its more valuable
    #: outputs**, and it reaches the Chair intact.
    resolution: Mapped[str] = mapped_column(
        sa.String(20),
        nullable=False,
        default="unresolved",
        server_default=sa.text("'unresolved'"),
    )
    resolution_note: Mapped[str | None] = mapped_column(sa.String(1000))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index(
            "ix_research_disagreements_run_resolution",
            "research_run_id",
            "resolution",
        ),
        sa.CheckConstraint(
            "resolution IN ('resolved', 'partially_resolved', 'unresolved')",
            name="ck_research_disagreements_resolution",
        ),
        sa.CheckConstraint(
            "nature IN ('value', 'period', 'scope', 'interpretation', "
            "'source_quality')",
            name="ck_research_disagreements_nature",
        ),
        # A finding cannot disagree with itself.
        sa.CheckConstraint(
            "finding_a_id <> finding_b_id",
            name="ck_research_disagreements_two_findings",
        ),
        # A resolved disagreement must say how. Otherwise "resolved" is the conflict
        # being closed rather than answered — the exact laundering the Chair rule
        # forbids.
        sa.CheckConstraint(
            "resolution = 'unresolved' OR resolution_note IS NOT NULL",
            name="ck_research_disagreements_resolution_is_explained",
        ),
    )


class ResearchHypothesis(Base):
    """A claim under test, with what supports and what contradicts it."""

    __tablename__ = "research_hypotheses"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_runs.id",
            ondelete="CASCADE",
            name="fk_research_hypotheses_run_id",
        ),
        nullable=False,
    )
    statement: Mapped[str] = mapped_column(sa.String(2000), nullable=False)
    #: ``open`` | ``supported`` | ``contradicted`` | ``inconclusive``.
    status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default="open", server_default=sa.text("'open'")
    )
    supporting_finding_ids_json: Mapped[list | None] = mapped_column(JSONB)
    contradicting_finding_ids_json: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index("ix_research_hypotheses_run", "research_run_id"),
        sa.CheckConstraint(
            "status IN ('open', 'supported', 'contradicted', 'inconclusive')",
            name="ck_research_hypotheses_status",
        ),
    )


__all__ = [
    "ResearchDisagreement",
    "ResearchFinding",
    "ResearchGap",
    "ResearchHypothesis",
    "ResearchQuestion",
    "ResearchRun",
    "ResearchTask",
]
