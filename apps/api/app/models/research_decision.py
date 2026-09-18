"""The research-decision record — V3.17.1.

WHY THIS TABLE EXISTS AT ALL
============================
The discovery council has been deciding ``research_next`` per candidate for phases, and
**nothing has ever read that decision.** It is written into
``discovery_runs.config_json["discovery_council"]["review"]["candidates_to_research_next"]``,
rendered to a human, and that is the end of it: no backend consumer, no column, no status,
nothing queryable. See ``docs/v3.17-research-escalation-design.md`` §1.1.

That is this repository's signature failure mode in its purest form — *metadata existed
but was never connected to execution* — and it is the same shape as the defect V3.16.1b
just closed one layer down.

WHY A DECISION IS NOT A JOB
===========================
``research_jobs`` (migration 019) already has idempotency, leases, heartbeats, attempts,
backoff, cancellation and dead-lettering, and V3.17 reuses it rather than building a
second queue beside a working one.

But a **job is one execution** and a **decision has a longer life**. Jobs are consumed and
retried; a decision has to outlive them to answer *"why is this company being researched,
how many rounds has it had, what did each round improve, and when do we stop?"* Putting
that in ``research_jobs.payload_json`` would make it unqueryable — which is precisely the
mistake described above, repeated.

WHAT THIS MODULE IS NOT
=======================
Only the shape and the vocabulary. No controller, no predicates, no enqueue: V3.17.1 adds
a table nothing writes to yet, deliberately, so the migration lands and is proven
reversible before any behaviour depends on it.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Vocabulary
#
# Plain strings, not database enums — the same choice `research_jobs.job_type`
# made, and for the same reason: adding a value must not require a migration.
# The allowlists below are what the application validates against.
# --------------------------------------------------------------------------- #

#: Where the decision came from. A decision the council proposed and a decision the
#: previous round's carried-forward gaps implied are different things: the first is a
#: model signal that a deterministic predicate accepted, the second is the platform's own
#: unfinished business. A reader who cannot tell them apart cannot tell "the model keeps
#: suggesting this company" from "this company keeps failing to answer".
SOURCE_DISCOVERY_COUNCIL = "discovery_council"
SOURCE_GAP_CARRY_FORWARD = "gap_carry_forward"

DECISION_SOURCES: frozenset[str] = frozenset(
    {SOURCE_DISCOVERY_COUNCIL, SOURCE_GAP_CARRY_FORWARD}
)

#: The council's coerced action. Mirrors the existing discovery vocabulary rather than
#: inventing a parallel one — `llm/discovery_council.py:_ACTION_TO_FIELD` is the origin,
#: and `discovery_citation_checker.py` is what coerces an out-of-vocabulary answer before
#: it ever reaches here. **The model's word is a signal, never an instruction.**
DECISION_RESEARCH_NEXT = "research_next"
DECISION_MONITOR = "monitor_for_evidence"
DECISION_REJECT = "reject_for_now"
DECISION_INSUFFICIENT_DATA = "insufficient_data"

DECISIONS: frozenset[str] = frozenset(
    {
        DECISION_RESEARCH_NEXT,
        DECISION_MONITOR,
        DECISION_REJECT,
        DECISION_INSUFFICIENT_DATA,
    }
)

# --- The state machine (design §3) ----------------------------------------- #

#: A deterministic predicate accepted the decision; no work has been queued yet.
STATUS_RESEARCH_REQUIRED = "research_required"
#: A `research_jobs` row exists, with `idempotency_key` = this decision's id.
STATUS_QUEUED = "queued"
#: A worker holds the lease.
STATUS_RUNNING = "running"
#: The round finished and its evidence delta has been measured.
STATUS_EVIDENCE_UPDATED = "evidence_updated"
#: The delta cleared the improvement threshold, so another round was authorised.
STATUS_REANALYSIS = "reanalysis"

#: Terminal. The loop ran and the question was answered.
STATUS_COMPLETED = "completed"
#: Terminal. **We stopped learning** — a round produced no measurable improvement.
STATUS_EXHAUSTED = "exhausted"
#: Terminal. **We stopped spending** — the round cap, the cost cap, or an operator.
STATUS_ABANDONED = "abandoned"
#: Terminal. The controller declined to research at all.
STATUS_MONITORED = "monitored"
STATUS_REJECTED = "rejected"
STATUS_INSUFFICIENT_EVIDENCE = "insufficient_evidence"

#: The states in which a decision is still live. This set is **load-bearing**: the partial
#: unique index below is defined on exactly it, so "one open decision per company" is a
#: database constraint rather than a convention a writer is trusted to keep.
OPEN_STATUSES: tuple[str, ...] = (
    STATUS_RESEARCH_REQUIRED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_EVIDENCE_UPDATED,
    STATUS_REANALYSIS,
)

#: Terminal states are terminal. `EXHAUSTED` and `ABANDONED` are deliberately distinct
#: from `COMPLETED` **and from each other**, because "we stopped learning" and "we stopped
#: spending" need different follow-ups, and a reader who cannot tell them apart will
#: retry the wrong one.
TERMINAL_STATUSES: tuple[str, ...] = (
    STATUS_COMPLETED,
    STATUS_EXHAUSTED,
    STATUS_ABANDONED,
    STATUS_MONITORED,
    STATUS_REJECTED,
    STATUS_INSUFFICIENT_EVIDENCE,
)

STATUSES: frozenset[str] = frozenset(OPEN_STATUSES + TERMINAL_STATUSES)

#: Why a decision stopped, in a closed vocabulary a UI can render verbatim. A terminal
#: state with no reason is a silent absence, which is the thing this platform does not do.
TERMINAL_EXHAUSTED_NO_IMPROVEMENT = "exhausted_no_improvement"
TERMINAL_MAX_ROUNDS = "max_rounds_reached"
TERMINAL_COST_CAP = "cost_cap_reached"
TERMINAL_COST_UNKNOWN = "cost_unknown"
TERMINAL_OPERATOR_CANCELLED = "operator_cancelled"
TERMINAL_JOB_DEAD_LETTERED = "job_dead_lettered"
TERMINAL_EVIDENCE_SUFFICIENT = "evidence_sufficient"
#: V3.17.4. **The one that must never be confused with `exhausted_no_improvement`.**
#: That reason is a claim about the WORLD — research ran and there was nothing new. This
#: one is a claim about the PLATFORM — we did not manage to look. Recording a provider
#: timeout as "no improvement" writes a false statement about the evidence into the
#: record, and it is the more dangerous error because it reads as a finished
#: investigation and nobody goes back.
TERMINAL_RESEARCH_DID_NOT_COMPLETE = "research_did_not_complete"

TERMINAL_REASONS: frozenset[str] = frozenset(
    {
        TERMINAL_RESEARCH_DID_NOT_COMPLETE,
        TERMINAL_EXHAUSTED_NO_IMPROVEMENT,
        TERMINAL_MAX_ROUNDS,
        TERMINAL_COST_CAP,
        TERMINAL_COST_UNKNOWN,
        TERMINAL_OPERATOR_CANCELLED,
        TERMINAL_JOB_DEAD_LETTERED,
        TERMINAL_EVIDENCE_SUFFICIENT,
    }
)

#: The default round cap. Two rounds, because the second is the one that demonstrates
#: whether escalation helps and the third has never yet been shown to.
DEFAULT_MAX_ROUNDS = 2


class ResearchDecision(Base):
    """One decision to research a company, and everything that decision caused."""

    __tablename__ = "research_decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # --- Lineage. Every FK is SET NULL, never CASCADE (CLAUDE.md #15): deleting a
    # discovery run must not delete the record that research happened because of it.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id",
            ondelete="SET NULL",
            name="fk_research_decisions_company_id_companies",
        ),
        nullable=True,
    )
    discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "discovery_runs.id",
            ondelete="SET NULL",
            name="fk_research_decisions_discovery_run_id_discovery_runs",
        ),
        nullable=True,
    )
    discovery_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "discovery_candidates.id",
            ondelete="SET NULL",
            name="fk_research_decisions_candidate_id_discovery_candidates",
        ),
        nullable=True,
    )
    #: The most recent job this decision produced. One decision may span several.
    last_job_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_jobs.id",
            ondelete="SET NULL",
            name="fk_research_decisions_last_job_id_research_jobs",
        ),
        nullable=True,
    )

    source: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    decision: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(40), nullable=False)

    #: **Which predicate fired, in words.** NOT NULL on purpose: a decision whose reason
    #: is absent is a decision nobody can audit, and this is the table that exists to
    #: answer "why is this company being researched?".
    reason: Mapped[str] = mapped_column(sa.Text, nullable=False)

    priority: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=100, server_default="100"
    )
    escalation_round: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    max_rounds: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=DEFAULT_MAX_ROUNDS,
        server_default=str(DEFAULT_MAX_ROUNDS),
    )

    # --- The evidence delta: what this round was handed, what it produced, and the
    # verdict on whether that was an improvement. Three columns rather than one, because
    # "before" and "after" are measurements and "improvement" is a judgement computed
    # from them — collapsing them would make the judgement unauditable.
    evidence_before_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    evidence_after_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    improvement_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    #: Cumulative spend. **NULL means unpriced, and NULL is NEVER 0** (CLAUDE.md #6): a
    #: stored zero asserts the work was free, and the controller must treat unknown cost
    #: as blocking rather than as budget remaining. Production currently reports
    #: `estimated_cost_usd` as NULL because no price book is configured, so this column
    #: will genuinely be NULL — which is the honest answer, not a gap to paper over.
    cost_usd_total: Mapped[float | None] = mapped_column(
        sa.Numeric(12, 4), nullable=True
    )

    terminal_reason: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        # The claim query: open decisions, highest priority first, oldest first.
        sa.Index(
            "ix_research_decisions_status_priority",
            "status",
            "priority",
            "created_at",
        ),
        sa.Index("ix_research_decisions_company_id", "company_id"),
        sa.Index("ix_research_decisions_discovery_run_id", "discovery_run_id"),
        # ONE OPEN DECISION PER COMPANY — enforced by the database.
        #
        # A read-then-write check in the controller loses under concurrency, and two
        # councils reviewing the same company is exactly the case that produces duplicate
        # paid research. The precedent is
        # `ix_research_document_versions_one_current` (migration 022).
        #
        # The predicate is built from OPEN_STATUSES rather than written out, so the index
        # cannot drift from the state machine the code uses — the drift that V3.16.1b was
        # entirely about.
        sa.Index(
            "ux_research_decisions_one_open",
            "company_id",
            unique=True,
            postgresql_where=sa.text(
                "status IN (" + ", ".join(f"'{s}'" for s in OPEN_STATUSES) + ")"
            ),
            sqlite_where=sa.text(
                "status IN (" + ", ".join(f"'{s}'" for s in OPEN_STATUSES) + ")"
            ),
        ),
    )
