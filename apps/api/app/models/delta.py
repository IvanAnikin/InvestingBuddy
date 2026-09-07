"""The ResearchDelta — V3.8 Slice 8.2.

THE QUESTION A RETURNING INVESTOR ACTUALLY HAS
==============================================
> **What changed since the previous analysis, and which prior conclusions should be
> revisited?**

A refresh that re-derives everything answers a different question. This row is the answer
to that one, computed between two runs and **persisted**, so the answer is still available
next quarter when somebody asks why a conclusion was dropped.

WHY THE PRIOR FINDING IS NOT MODIFIED
=====================================
An invalidated finding stays exactly as it was written. Invalidation is a property of the
**relationship between two runs**, not of the old finding — it was a correct conclusion
from the evidence available then, and rewriting it would make the earlier run's record a
lie in service of the later one's convenience.

So the delta row carries the invalidation and the old row is untouched. That also means a
delta can be recomputed, disputed or superseded without any archaeology.

COUNTS ARE COLUMNS, DETAIL IS JSONB
===================================
"How much changed" is a query — over time, across issuers, per playbook — and a query that
has to parse a JSON payload is one nobody runs. The lists live in JSONB beside the counts,
and the counts are derived in one place so they cannot drift from the lists.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchDelta(Base):
    """What changed between two runs of one issuer."""

    __tablename__ = "research_deltas"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id", ondelete="SET NULL", name="fk_research_deltas_company_id"
        ),
        nullable=True,
    )
    #: The earlier run. ``SET NULL`` because a delta outlives the runs it compares —
    #: "this conclusion was invalidated in September" stays true when the run record is
    #: eventually pruned.
    from_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_runs.id",
            ondelete="SET NULL",
            name="fk_research_deltas_from_run_id",
        ),
        nullable=True,
    )
    to_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_runs.id",
            ondelete="SET NULL",
            name="fk_research_deltas_to_run_id",
        ),
        nullable=True,
    )

    #: True when nothing the new run found undermines the old thesis. **Never a
    #: default**: it is computed, and a delta with no comparison basis is not "unchanged".
    unchanged_core_thesis: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)

    new_evidence_ids_json: Mapped[list | None] = mapped_column(JSONB)
    changed_facts_json: Mapped[list | None] = mapped_column(JSONB)
    resolved_question_keys_json: Mapped[list | None] = mapped_column(JSONB)
    new_gap_ids_json: Mapped[list | None] = mapped_column(JSONB)
    closed_gap_ids_json: Mapped[list | None] = mapped_column(JSONB)
    #: Prior findings the new evidence undermines. The old rows are **not modified**.
    invalidated_finding_ids_json: Mapped[list | None] = mapped_column(JSONB)

    new_evidence_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    changed_fact_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    resolved_question_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    new_gap_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    closed_gap_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    invalidated_finding_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    computed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index("ix_research_deltas_company_computed", "company_id", "computed_at"),
        sa.Index("ix_research_deltas_to_run_id", "to_run_id"),
        # One delta per ordered pair of runs. Two deltas between the same two runs would
        # be two answers to "what changed", and nothing could say which was current.
        sa.Index(
            "ix_research_deltas_run_pair",
            "from_run_id",
            "to_run_id",
            unique=True,
        ),
        # There is deliberately NO "must name a run" CHECK here, and the reason is a
        # defect a real DELETE found: with both foreign keys `SET NULL`, deleting both
        # runs nulls both columns, and such a CHECK would REFUSE that update — making the
        # very pruning the cascade exists for impossible. The rule it was reaching for
        # ("a delta compares a run with something") is about CREATION, and
        # `delta.persist_delta` enforces it there. After the runs are pruned the delta
        # still carries its counts and its lists, which is exactly the durable record
        # this table is for.
        # An unchanged thesis with invalidated findings is the row contradicting itself:
        # a conclusion the new evidence undermines IS a change to the thesis.
        sa.CheckConstraint(
            "unchanged_core_thesis = false OR invalidated_finding_count = 0",
            name="ck_research_deltas_unchanged_means_nothing_invalidated",
        ),
        sa.CheckConstraint(
            "new_evidence_count >= 0 AND changed_fact_count >= 0 "
            "AND resolved_question_count >= 0 AND new_gap_count >= 0 "
            "AND closed_gap_count >= 0 AND invalidated_finding_count >= 0",
            name="ck_research_deltas_counts_are_not_negative",
        ),
    )


__all__ = ["ResearchDelta"]
