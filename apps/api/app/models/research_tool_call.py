"""The agent tool-call audit record — V3.3 Slice 3.1.

One row per attempted tool call, **including refusals and errors** — which are the
interesting ones. It is simultaneously three things
(``AGENTIC_RESEARCH_ARCHITECTURE.md`` §4):

* the audit log (CLAUDE.md rule 9: log every agent run and every agent step);
* the cost ledger, in the units ``consumption.UNIT_NAMES`` already defines;
* the debugging trace for a run nobody can reproduce.

WHY REFUSALS ARE STORED, NOT DISCARDED
======================================
A refused call is the most informative row in the table. ``tool_not_permitted``
aggregated per role says the *Director* gave that role the wrong tools;
``budget_exceeded`` says which limit bites first, which is the measurement OPEN
DECISION #14 asks for. Discarding them would leave only the calls that worked, which
is the one population that cannot answer either question.

WHY THE PAYLOAD IS NOT HERE
===========================
``summary`` is one short line and ``item_count`` is a number. Payloads can be large,
and an audit log that stores them stops being readable and starts being a second,
unindexed copy of the corpus. What is stored is enough to answer "what was asked, what
came back, what did it cost" — and the payload's *contents* are traceable through the
evidence ids a finding cites.

``arguments_json`` IS stored, because "which arguments produced this" is the question a
debugging trace exists to answer, and arguments are bounded by the tool's own schema.

NULLS ARE STATEMENTS
====================
``consumption_json`` carries only the units the tool declared instrumented, and
``instrumented_units_json`` names them. A unit absent from that list is **not zero** —
it is unmeasured, and a reader that treats the two alike is reading fabricated zeros.
The same discipline ``research_run_consumption`` established.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchToolCall(Base):
    """One attempted tool call by one role."""

    __tablename__ = "research_tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: The durable job this call belongs to, when there is one. ``SET NULL`` — the
    #: audit record outlives the job, which is the point of an audit record.
    research_job_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_jobs.id",
            ondelete="SET NULL",
            name="fk_research_tool_calls_research_job_id_research_jobs",
        ),
        nullable=True,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id",
            ondelete="SET NULL",
            name="fk_research_tool_calls_company_id_companies",
        ),
        nullable=True,
    )
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "legal_entities.id",
            ondelete="SET NULL",
            name="fk_research_tool_calls_legal_entity_id_legal_entities",
        ),
        nullable=True,
    )
    #: The agent role that called. Not a FK: roles are configuration (a playbook
    #: supplies them in V3.6), and a FK to configuration makes the configuration
    #: undeletable.
    role: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    #: A member of ``contracts.TOOL_NAMES``. Stored even for ``unknown_tool``, because
    #: what an agent *tried* to call is exactly what an audit needs.
    tool_name: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    #: Free-form reference to the task or question this served, for the ledger in V3.5
    #: to join on without this table needing to know its schema yet.
    task_ref: Mapped[str | None] = mapped_column(sa.String(120))
    arguments_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: ``ok`` | ``refused`` | ``error``.
    outcome: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    #: A member of ``contracts.REFUSAL_REASONS`` when ``outcome`` is ``refused``.
    refusal_reason: Mapped[str | None] = mapped_column(sa.String(60))
    #: Which budget limit refused it, for ``budget_exceeded``.
    limit_hit: Mapped[str | None] = mapped_column(sa.String(60))
    #: The exception TYPE for ``error``. Never the message: a message can carry a
    #: fragment of fetched content, and this table is read by humans and by prompts.
    error_type: Mapped[str | None] = mapped_column(sa.String(120))
    summary: Mapped[str | None] = mapped_column(sa.String(500))
    item_count: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, default=0, server_default=sa.text("0")
    )
    #: True when the payload could contain text from outside the platform, so a prompt
    #: builder fences it rather than guessing.
    contains_untrusted_content: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, default=False, server_default=sa.false()
    )
    consumption_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: Which units the tool actually measured. Absent ≠ zero.
    instrumented_units_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, default=0, server_default=sa.text("0")
    )
    estimated_cost_usd: Mapped[float | None] = mapped_column(sa.Float())
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        # "Every call in this job, in order" — the debugging trace.
        sa.Index("ix_research_tool_calls_job_created", "research_job_id", "created_at"),
        # "How often is this tool refused, and to which role" — the planning report.
        sa.Index("ix_research_tool_calls_tool_outcome", "tool_name", "outcome"),
        sa.Index("ix_research_tool_calls_role", "role"),
        sa.Index("ix_research_tool_calls_company_id", "company_id"),
        sa.CheckConstraint(
            "outcome IN ('ok', 'refused', 'error')",
            name="ck_research_tool_calls_outcome",
        ),
        # A refusal with no reason cannot be aggregated, which is the only thing a
        # stored refusal is for.
        sa.CheckConstraint(
            "outcome <> 'refused' OR refusal_reason IS NOT NULL",
            name="ck_research_tool_calls_refusal_has_a_reason",
        ),
        sa.CheckConstraint(
            "item_count >= 0 AND latency_ms >= 0",
            name="ck_research_tool_calls_counts_non_negative",
        ),
    )
