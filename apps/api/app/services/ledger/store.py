"""Reading and writing the Research Ledger — V3.5 Slice 5.1.

ONE PLACE THAT DERIVES THE COUNTS
=================================
``record_finding`` is the only writer of ``evidence_count`` and ``calculation_count``, so
the CHECK that enforces *a finding without evidence ids or calculation ids is not a
finding* can never disagree with the lists it is about. Drift is unrepresentable rather
than checked for — the V3.2 rule, applied where a portable CHECK cannot read inside a
JSON column.

REFUSING IN PYTHON **AND** IN SQL
================================
Both, on purpose. The Python refusal gives a caller a message it can act on; the database
refusal survives a future writer that bypasses this module. Four campaigns' worth of
evidence says the second one is the load-bearing half.

VOCABULARIES ARE CLOSED
=======================
A gap type invented at a call site is a gap type nothing can aggregate on, and "which gap
class does this platform keep hitting" is a statement about the *pipeline* that free text
cannot make. Same reasoning as every other refusal vocabulary in this codebase.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.models.ledger import (
    ResearchDisagreement,
    ResearchFinding,
    ResearchGap,
    ResearchHypothesis,
    ResearchQuestion,
    ResearchRun,
    ResearchTask,
)

# ── Vocabularies ────────────────────────────────────────────────────────────── #

RUN_PLANNING = "planning"
RUN_INVESTIGATING = "investigating"
RUN_REVIEWING = "reviewing"
RUN_COMPLETE = "complete"
RUN_STOPPED = "stopped"
RUN_STATUSES: frozenset[str] = frozenset(
    {RUN_PLANNING, RUN_INVESTIGATING, RUN_REVIEWING, RUN_COMPLETE, RUN_STOPPED}
)

ORIGIN_PLAYBOOK = "playbook"
ORIGIN_DIRECTOR = "director"
ORIGIN_RED_TEAM = "red_team"
ORIGIN_PRIOR_GAP = "prior_gap"
QUESTION_ORIGINS: frozenset[str] = frozenset(
    {ORIGIN_PLAYBOOK, ORIGIN_DIRECTOR, ORIGIN_RED_TEAM, ORIGIN_PRIOR_GAP}
)

QUESTION_OPEN = "open"
QUESTION_ANSWERED = "answered"
QUESTION_UNANSWERABLE = "unanswerable"
QUESTION_STATUSES: frozenset[str] = frozenset(
    {QUESTION_OPEN, QUESTION_ANSWERED, QUESTION_UNANSWERABLE}
)

TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_COMPLETE = "complete"
TASK_PARTIAL = "partial"
TASK_FAILED = "failed"
TASK_SKIPPED = "skipped"
TASK_STATUSES: frozenset[str] = frozenset(
    {TASK_PENDING, TASK_RUNNING, TASK_COMPLETE, TASK_PARTIAL, TASK_FAILED, TASK_SKIPPED}
)

#: Closed, because an aggregate over gap types is a statement about the pipeline.
GAP_EVIDENCE_UNAVAILABLE = "evidence_unavailable"
GAP_SOURCE_UNREACHABLE = "source_unreachable"
GAP_PERIOD_MISSING = "period_missing"
GAP_SCOPE_UNKNOWN = "scope_unknown"
GAP_CONFLICTING_SOURCES = "conflicting_sources"
GAP_ENTITY_AMBIGUOUS = "entity_ambiguous"
GAP_TRANSCRIPT_UNAVAILABLE = "transcript_unavailable"
GAP_CALCULATION_REFUSED = "calculation_refused"
GAP_TOOL_UNAVAILABLE = "tool_unavailable"
GAP_BUDGET_EXHAUSTED = "budget_exhausted"

GAP_TYPES: frozenset[str] = frozenset(
    {
        GAP_EVIDENCE_UNAVAILABLE,
        GAP_SOURCE_UNREACHABLE,
        GAP_PERIOD_MISSING,
        GAP_SCOPE_UNKNOWN,
        GAP_CONFLICTING_SOURCES,
        GAP_ENTITY_AMBIGUOUS,
        GAP_TRANSCRIPT_UNAVAILABLE,
        GAP_CALCULATION_REFUSED,
        GAP_TOOL_UNAVAILABLE,
        GAP_BUDGET_EXHAUSTED,
    }
)

GAP_OPEN = "open"
GAP_CLOSED = "closed"
GAP_ACCEPTED = "accepted"
GAP_STATUSES: frozenset[str] = frozenset({GAP_OPEN, GAP_CLOSED, GAP_ACCEPTED})

DISAGREEMENT_NATURES: frozenset[str] = frozenset(
    {"value", "period", "scope", "interpretation", "source_quality"}
)
RESOLVED = "resolved"
PARTIALLY_RESOLVED = "partially_resolved"
UNRESOLVED = "unresolved"
RESOLUTIONS: frozenset[str] = frozenset({RESOLVED, PARTIALLY_RESOLVED, UNRESOLVED})

DIRECTIONS: frozenset[str] = frozenset({"supportive", "adverse", "neutral"})

_STATEMENT_MAX = 2000
_TEXT_MAX = 1000
_KEY_MAX = 80
_DETAIL_MAX = 500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


# ── Runs ────────────────────────────────────────────────────────────────────── #


async def open_run(
    session: Any,
    *,
    mode: str,
    company_id: uuid.UUID | None = None,
    legal_entity_id: uuid.UUID | None = None,
    research_job_id: uuid.UUID | None = None,
    budget: dict | None = None,
    playbook_versions: dict | None = None,
    now: datetime | None = None,
) -> ResearchRun:
    run = ResearchRun(
        id=uuid.uuid4(),
        company_id=company_id,
        legal_entity_id=legal_entity_id,
        research_job_id=research_job_id,
        mode=mode,
        status=RUN_PLANNING,
        budget_json=budget,
        playbook_versions_json=playbook_versions,
        started_at=now or _utcnow(),
    )
    session.add(run)
    await session.flush()
    return run


async def close_run(
    session: Any,
    run: ResearchRun,
    *,
    status: str,
    stopped_by: str | None = None,
    consumption: dict | None = None,
    now: datetime | None = None,
) -> ResearchRun:
    """Finish a run. **A stopped run must name the limit that stopped it.**

    "We stopped because the search budget ran out with three questions unanswered" is
    useful. "Analysis complete" would be a lie, and the schema refuses to store it.
    """
    if status not in RUN_STATUSES:
        raise ValueError(f"{status!r} is not a run status.")
    if status == RUN_STOPPED and not stopped_by:
        raise ValueError(
            "a stopped run must name the limit that stopped it: a run that stopped for "
            "an unnamed reason is one nobody can widen a budget for."
        )
    run.status = status
    run.stopped_by = _clip(stopped_by, 40)
    if consumption is not None:
        run.consumption_json = consumption
    run.completed_at = now or _utcnow()
    await session.flush()
    return run


# ── Questions and tasks ─────────────────────────────────────────────────────── #


async def add_question(
    session: Any,
    run: ResearchRun,
    *,
    question_key: str,
    text: str,
    origin: str,
    priority: int = 3,
    blocking: bool = False,
    required_evidence_classes: Sequence[str] = (),
) -> ResearchQuestion:
    if origin not in QUESTION_ORIGINS:
        raise ValueError(f"{origin!r} is not a question origin.")
    question = ResearchQuestion(
        id=uuid.uuid4(),
        research_run_id=run.id,
        question_key=_clip(question_key, _KEY_MAX) or "",
        text=_clip(text, _TEXT_MAX) or "",
        origin=origin,
        priority=int(priority),
        blocking=bool(blocking),
        required_evidence_classes_json=list(required_evidence_classes) or None,
    )
    session.add(question)
    await session.flush()
    return question


async def add_task(
    session: Any,
    run: ResearchRun,
    *,
    role: str,
    question_keys: Sequence[str] = (),
    round_index: int = 0,
    max_iterations: int = 4,
    tool_budget: dict | None = None,
) -> ResearchTask:
    if max_iterations < 1:
        raise ValueError(
            "a task with no iteration cap is an agent that need never stop."
        )
    task = ResearchTask(
        id=uuid.uuid4(),
        research_run_id=run.id,
        role=_clip(role, 60) or "",
        round_index=int(round_index),
        status=TASK_PENDING,
        question_keys_json=list(question_keys) or None,
        max_iterations=int(max_iterations),
        tool_budget_json=tool_budget,
    )
    session.add(task)
    await session.flush()
    return task


async def finish_task(
    session: Any,
    task: ResearchTask,
    *,
    status: str,
    stopped_by: str | None = None,
    detail: str | None = None,
    now: datetime | None = None,
) -> ResearchTask:
    if status not in TASK_STATUSES:
        raise ValueError(f"{status!r} is not a task status.")
    if status == TASK_PARTIAL and not stopped_by:
        raise ValueError(
            "a partial task must name what stopped it. Reporting a thin answer as "
            "complete is the failure `partial` exists to prevent, and a partial with "
            "no reason is the same failure one step later."
        )
    task.status = status
    task.stopped_by = _clip(stopped_by, 40)
    task.detail = _clip(detail, _DETAIL_MAX)
    task.completed_at = now or _utcnow()
    await session.flush()
    return task


# ── Findings ────────────────────────────────────────────────────────────────── #


class UnsupportedFindingError(ValueError):
    """Raised for a finding with neither evidence ids nor calculation ids.

    Named after the rule rather than the field, because the rule is what a reader needs:
    **a finding without evidence ids or calculation ids is not a finding**, and there is
    no "trust me" state.
    """


async def record_finding(
    session: Any,
    run: ResearchRun,
    *,
    statement: str,
    evidence_ids: Sequence[str] = (),
    calculation_ids: Sequence[str] = (),
    task: ResearchTask | None = None,
    question_key: str | None = None,
    mechanism: str | None = None,
    direction: str | None = None,
    confidence: float | None = None,
    period_key: str | None = None,
    period_type: str | None = None,
    scope_type: str | None = None,
    scope_key: str | None = None,
    originating_role: str | None = None,
    provider: str | None = None,
    verification_status: str = "unverified",
) -> ResearchFinding:
    """Persist one finding. **The counts are derived here and nowhere else.**"""
    evidence = [str(value).strip() for value in evidence_ids if str(value).strip()]
    calculations = [
        str(value).strip() for value in calculation_ids if str(value).strip()
    ]
    if not evidence and not calculations:
        raise UnsupportedFindingError(
            "a finding needs at least one evidence id or calculation id. A statement "
            "with neither is a model's assertion, and the ledger has no state for one."
        )
    if direction is not None and direction not in DIRECTIONS:
        raise ValueError(f"{direction!r} is not a finding direction.")
    if confidence is not None and not (0.0 <= float(confidence) <= 1.0):
        raise ValueError("confidence is a probability, so it lies in [0, 1].")
    finding = ResearchFinding(
        id=uuid.uuid4(),
        research_run_id=run.id,
        research_task_id=task.id if task is not None else None,
        question_key=_clip(question_key, _KEY_MAX),
        statement=_clip(statement, _STATEMENT_MAX) or "",
        mechanism=_clip(mechanism, _STATEMENT_MAX),
        direction=direction,
        confidence=float(confidence) if confidence is not None else None,
        evidence_ids_json=evidence or None,
        calculation_ids_json=calculations or None,
        evidence_count=len(evidence),
        calculation_count=len(calculations),
        period_key=_clip(period_key, 20),
        period_type=_clip(period_type, 20),
        scope_type=_clip(scope_type, 20),
        scope_key=_clip(scope_key, 220),
        originating_role=_clip(originating_role, 60),
        provider=_clip(provider, 40),
        verification_status=verification_status,
    )
    session.add(finding)
    await session.flush()
    return finding


# ── Gaps ────────────────────────────────────────────────────────────────────── #


async def record_gap(
    session: Any,
    run: ResearchRun,
    *,
    gap_type: str,
    description: str,
    question_key: str | None = None,
    why_it_matters: str | None = None,
    sources_tried: Sequence[str] = (),
    closable: bool = True,
    blocks_council: bool = False,
) -> ResearchGap:
    if gap_type not in GAP_TYPES:
        raise ValueError(
            f"{gap_type!r} is not a gap type. A type invented at a call site is one "
            "nothing can aggregate on, and 'which gap class does this platform keep "
            f"hitting' is the question that matters. Recognised: "
            f"{', '.join(sorted(GAP_TYPES))}."
        )
    gap = ResearchGap(
        id=uuid.uuid4(),
        research_run_id=run.id,
        question_key=_clip(question_key, _KEY_MAX),
        gap_type=gap_type,
        description=_clip(description, _TEXT_MAX) or "",
        why_it_matters=_clip(why_it_matters, _TEXT_MAX),
        sources_tried_json=list(sources_tried) or None,
        closable=bool(closable),
        blocks_council=bool(blocks_council),
        status=GAP_OPEN,
    )
    session.add(gap)
    await session.flush()
    return gap


async def close_gap(
    session: Any, gap: ResearchGap, *, finding: ResearchFinding
) -> ResearchGap:
    """Close a gap **by naming the finding that answered it.**

    "Closed" with nothing behind it is the gap disappearing rather than being answered,
    and a database CHECK refuses that row.
    """
    gap.status = GAP_CLOSED
    gap.closed_by_finding_id = finding.id
    await session.flush()
    return gap


async def accept_gap(session: Any, gap: ResearchGap) -> ResearchGap:
    """The run finished with this gap open, and says so. The honest outcome for a gap
    no source can close."""
    gap.status = GAP_ACCEPTED
    await session.flush()
    return gap


# ── Disagreements and hypotheses ────────────────────────────────────────────── #


async def record_disagreement(
    session: Any,
    run: ResearchRun,
    *,
    finding_a: ResearchFinding,
    finding_b: ResearchFinding,
    nature: str,
    description: str | None = None,
    resolution: str = UNRESOLVED,
    resolution_note: str | None = None,
) -> ResearchDisagreement:
    if nature not in DISAGREEMENT_NATURES:
        raise ValueError(f"{nature!r} is not a disagreement nature.")
    if resolution not in RESOLUTIONS:
        raise ValueError(f"{resolution!r} is not a resolution.")
    if finding_a.id == finding_b.id:
        raise ValueError("a finding cannot disagree with itself.")
    if resolution != UNRESOLVED and not resolution_note:
        raise ValueError(
            "a resolved disagreement must say how it was resolved. Otherwise "
            "'resolved' is the conflict being closed rather than answered, which is "
            "exactly the laundering the Chair rule forbids."
        )
    row = ResearchDisagreement(
        id=uuid.uuid4(),
        research_run_id=run.id,
        finding_a_id=finding_a.id,
        finding_b_id=finding_b.id,
        nature=nature,
        description=_clip(description, _TEXT_MAX),
        resolution=resolution,
        resolution_note=_clip(resolution_note, _TEXT_MAX),
    )
    session.add(row)
    await session.flush()
    return row


async def record_hypothesis(
    session: Any,
    run: ResearchRun,
    *,
    statement: str,
    supporting_finding_ids: Sequence[uuid.UUID] = (),
    contradicting_finding_ids: Sequence[uuid.UUID] = (),
    status: str = "open",
) -> ResearchHypothesis:
    row = ResearchHypothesis(
        id=uuid.uuid4(),
        research_run_id=run.id,
        statement=_clip(statement, _STATEMENT_MAX) or "",
        status=status,
        supporting_finding_ids_json=[str(v) for v in supporting_finding_ids] or None,
        contradicting_finding_ids_json=[str(v) for v in contradicting_finding_ids]
        or None,
    )
    session.add(row)
    await session.flush()
    return row


# ── Reading ─────────────────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class LedgerSummary:
    """What one run knows, and what it does not. Every count names its population."""

    run_id: uuid.UUID
    status: str
    stopped_by: str | None
    rounds_completed: int
    questions_total: int
    questions_open: int
    questions_blocking_open: int
    findings_total: int
    findings_verified: int
    gaps_open: int
    gaps_blocking_council: int
    disagreements_unresolved: int

    @property
    def council_may_convene(self) -> bool:
        """False while any blocking question is open or any gap blocks the Council.

        The run then reports insufficient evidence rather than analysing around the
        hole — which is the whole point of a `blocking` flag existing.
        """
        return self.questions_blocking_open == 0 and self.gaps_blocking_council == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "status": self.status,
            "stopped_by": self.stopped_by,
            "rounds_completed": self.rounds_completed,
            "questions": {
                "total": self.questions_total,
                "open": self.questions_open,
                "blocking_open": self.questions_blocking_open,
            },
            "findings": {
                "total": self.findings_total,
                "verified": self.findings_verified,
            },
            "gaps": {
                "open": self.gaps_open,
                "blocking_council": self.gaps_blocking_council,
            },
            "disagreements_unresolved": self.disagreements_unresolved,
            "council_may_convene": self.council_may_convene,
        }


async def summarise(session: Any, run: ResearchRun) -> LedgerSummary:
    """Aggregate the run, in the database rather than in Python."""

    async def _count(model: Any, *where: Any) -> int:
        stmt = select(func.count()).select_from(model).where(*where)
        return int((await session.execute(stmt)).scalar_one() or 0)

    return LedgerSummary(
        run_id=run.id,
        status=run.status,
        stopped_by=run.stopped_by,
        rounds_completed=run.rounds_completed,
        questions_total=await _count(
            ResearchQuestion, ResearchQuestion.research_run_id == run.id
        ),
        questions_open=await _count(
            ResearchQuestion,
            ResearchQuestion.research_run_id == run.id,
            ResearchQuestion.resolution_status == QUESTION_OPEN,
        ),
        questions_blocking_open=await _count(
            ResearchQuestion,
            ResearchQuestion.research_run_id == run.id,
            ResearchQuestion.resolution_status == QUESTION_OPEN,
            ResearchQuestion.blocking.is_(True),
        ),
        findings_total=await _count(
            ResearchFinding, ResearchFinding.research_run_id == run.id
        ),
        findings_verified=await _count(
            ResearchFinding,
            ResearchFinding.research_run_id == run.id,
            ResearchFinding.verification_status == "verified",
        ),
        gaps_open=await _count(
            ResearchGap,
            ResearchGap.research_run_id == run.id,
            ResearchGap.status == GAP_OPEN,
        ),
        gaps_blocking_council=await _count(
            ResearchGap,
            ResearchGap.research_run_id == run.id,
            ResearchGap.status == GAP_OPEN,
            ResearchGap.blocks_council.is_(True),
        ),
        disagreements_unresolved=await _count(
            ResearchDisagreement,
            ResearchDisagreement.research_run_id == run.id,
            ResearchDisagreement.resolution == UNRESOLVED,
        ),
    )


async def open_gaps(
    session: Any, run: ResearchRun, *, closable_only: bool = False, limit: int = 100
) -> list[ResearchGap]:
    """Open gaps, newest first. The bounded loop's input.

    ``closable_only`` is what stops a loop spending another round on the one thing no
    source has. Every filter is in the same statement as the LIMIT.
    """
    stmt = select(ResearchGap).where(
        ResearchGap.research_run_id == run.id, ResearchGap.status == GAP_OPEN
    )
    if closable_only:
        stmt = stmt.where(ResearchGap.closable.is_(True))
    stmt = stmt.order_by(ResearchGap.created_at.desc()).limit(max(1, min(limit, 500)))
    return list((await session.execute(stmt)).scalars().all())


__all__ = [
    "DIRECTIONS",
    "DISAGREEMENT_NATURES",
    "GAP_ACCEPTED",
    "GAP_BUDGET_EXHAUSTED",
    "GAP_CALCULATION_REFUSED",
    "GAP_CLOSED",
    "GAP_CONFLICTING_SOURCES",
    "GAP_ENTITY_AMBIGUOUS",
    "GAP_EVIDENCE_UNAVAILABLE",
    "GAP_OPEN",
    "GAP_PERIOD_MISSING",
    "GAP_SCOPE_UNKNOWN",
    "GAP_SOURCE_UNREACHABLE",
    "GAP_STATUSES",
    "GAP_TOOL_UNAVAILABLE",
    "GAP_TRANSCRIPT_UNAVAILABLE",
    "GAP_TYPES",
    "ORIGIN_DIRECTOR",
    "ORIGIN_PLAYBOOK",
    "ORIGIN_PRIOR_GAP",
    "ORIGIN_RED_TEAM",
    "PARTIALLY_RESOLVED",
    "QUESTION_ANSWERED",
    "QUESTION_OPEN",
    "QUESTION_ORIGINS",
    "QUESTION_STATUSES",
    "QUESTION_UNANSWERABLE",
    "RESOLUTIONS",
    "RESOLVED",
    "RUN_COMPLETE",
    "RUN_INVESTIGATING",
    "RUN_PLANNING",
    "RUN_REVIEWING",
    "RUN_STATUSES",
    "RUN_STOPPED",
    "TASK_COMPLETE",
    "TASK_FAILED",
    "TASK_PARTIAL",
    "TASK_PENDING",
    "TASK_RUNNING",
    "TASK_SKIPPED",
    "TASK_STATUSES",
    "UNRESOLVED",
    "LedgerSummary",
    "UnsupportedFindingError",
    "accept_gap",
    "add_question",
    "add_task",
    "close_gap",
    "close_run",
    "finish_task",
    "open_gaps",
    "open_run",
    "record_disagreement",
    "record_finding",
    "record_gap",
    "record_hypothesis",
    "summarise",
]
