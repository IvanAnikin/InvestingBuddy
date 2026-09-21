"""The bounded investigation loop — V3.5 Slice 5.3.

WHAT THE LOOP IS FOR
====================
V2's council receives a frozen evidence pack and cannot go and look. It can report a gap
beautifully; it cannot close one. The loop is the mechanism that turns a gap into a
follow-up task — and the *bounds* are what stop that from becoming an agent that researches
indefinitely.

    plan → investigate → persist findings → gap review → enough? → follow-up or Council

EVERY LIMIT IS ENFORCED BEFORE THE SPEND, AND THE RUN SAYS WHICH ONE STOPPED IT
==============================================================================
``max_rounds`` · ``max_tasks`` · ``max_tool_calls`` · ``max_wall_seconds``, all from
``ModeLimits`` (4.11), plus the playbook's completion rules. A limit is checked **before**
the work it would authorise, because a budget satisfied by noticing afterwards is a budget
that was exceeded.

When a limit stops the loop the run records **which** limit and **what was still open**.
"We stopped because the search budget ran out with three questions unanswered" is useful;
"analysis complete" would be a lie, and ``ck_research_runs_stopped_names_a_limit`` makes
the lie unstorable.

THE COMPLETION TEST IS NOT "DID THE AGENTS FINISH"
=================================================
It is: *are the completion rules satisfied, or is the budget exhausted?* A run whose
agents all returned successfully but whose blocking question is unanswered is **not**
ready for the Council, and ``LedgerSummary.council_may_convene`` is what says so.

A GAP IS NOT AUTOMATICALLY A FOLLOW-UP
======================================
Only a **closable** gap is. An unclosable one — no source publishes the figure — would
consume a whole round to fail again, so it is *accepted* instead: the run finishes with it
open and says so. That is the difference between a bounded loop and a loop that happens to
have a counter.

NO INVESTIGATION HAPPENS HERE
=============================
``Investigator`` is a Protocol the caller supplies. The loop owns the *control flow* — the
rounds, the limits, the gap review, the termination reason — and nothing about how a
specialist does its work. That keeps the property this module exists to guarantee testable
without a model, a network or a database of documents.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.services.director.planner import PlannedQuestion, ResearchPlan
from app.services.ledger import store as ledger
from app.services.research_mode import ModeLimits

#: Why the loop stopped. Closed, and every member is either a limit or a completion
#: state — there is no "other".
STOPPED_COMPLETE = "completion_rules_satisfied"
STOPPED_NOTHING_LEFT = "no_closable_gaps"
STOPPED_MAX_ROUNDS = "max_rounds"
STOPPED_MAX_TASKS = "max_tasks"
STOPPED_MAX_TOOL_CALLS = "max_tool_calls"
STOPPED_MAX_WALL_SECONDS = "max_wall_seconds"

LOOP_STOP_REASONS: frozenset[str] = frozenset(
    {
        STOPPED_COMPLETE,
        STOPPED_NOTHING_LEFT,
        STOPPED_MAX_ROUNDS,
        STOPPED_MAX_TASKS,
        STOPPED_MAX_TOOL_CALLS,
        STOPPED_MAX_WALL_SECONDS,
    }
)

#: The stop reasons that mean a **limit** ended the run rather than the work finishing.
LIMIT_STOP_REASONS: frozenset[str] = frozenset(
    {
        STOPPED_MAX_ROUNDS,
        STOPPED_MAX_TASKS,
        STOPPED_MAX_TOOL_CALLS,
        STOPPED_MAX_WALL_SECONDS,
    }
)


@dataclass
class FindingDraft:
    """What an investigator produces. **Support is not optional.**

    The ledger refuses a finding with neither an evidence id nor a calculation id, so an
    investigator that returns one gets an error rather than a silently dropped result —
    which is the difference between a contract and a convention.
    """

    statement: str
    evidence_ids: tuple[str, ...] = ()
    calculation_ids: tuple[str, ...] = ()
    question_key: str | None = None
    mechanism: str | None = None
    direction: str | None = None
    confidence: float | None = None
    period_key: str | None = None
    scope_key: str | None = None
    #: V3.18.2 — source kinds of the cited evidence, for the finding's provenance.
    source_kinds: tuple[str, ...] = ()


@dataclass
class GapDraft:
    """Something the investigator could not establish, and whether it is worth retrying."""

    gap_type: str
    description: str
    question_key: str | None = None
    why_it_matters: str | None = None
    sources_tried: tuple[str, ...] = ()
    #: ``False`` means another round would fail the same way. That judgement is the
    #: investigator's — it knows which sources it tried — and it is what stops the loop
    #: spending a whole round to fail again.
    closable: bool = True
    blocks_council: bool = False


@dataclass
class TaskOutcome:
    """One task's result, including how much of its budget it used."""

    findings: list[FindingDraft] = field(default_factory=list)
    gaps: list[GapDraft] = field(default_factory=list)
    answered_question_keys: tuple[str, ...] = ()
    tool_calls: int = 0
    #: Set when the task stopped early. A partial task must say what stopped it.
    stopped_by: str | None = None
    failed: bool = False
    detail: str | None = None
    #: V3.18.2 — every citable item retrieved, per question, as the evidence contract
    #: sees it. Evaluated by the loop, never by the investigator, so the verdict on a
    #: question cannot be written by the same code that wrote its answer.
    question_evidence: dict[str, list[Any]] = field(default_factory=dict)
    #: Per question, what each rung of acquisition did, in order.
    acquisition_steps: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@runtime_checkable
class Investigator(Protocol):
    """How one role works one task. Supplied by the caller; never implemented here.

    The loop owns the control flow and nothing about how a specialist does its work,
    which is what makes the loop's guarantees testable without a model or a network.
    """

    async def investigate(
        self,
        *,
        role_id: str,
        questions: "Sequence[PlannedQuestion]",
        round_index: int,
        remaining_tool_calls: int,
    ) -> TaskOutcome:
        ...  # pragma: no cover - protocol


@dataclass
class RoundRecord:
    """What one round did. Kept so "why did this run cost that" is answerable."""

    index: int
    tasks_run: int = 0
    findings: int = 0
    gaps_opened: int = 0
    gaps_closed: int = 0
    tool_calls: int = 0
    elapsed_seconds: float = 0.0


@dataclass
class LoopResult:
    """How the run ended, and what was still open when it did."""

    stopped_by: str
    rounds: list[RoundRecord] = field(default_factory=list)
    tasks_run: int = 0
    tool_calls: int = 0
    findings: int = 0
    gaps_open: int = 0
    gaps_accepted: int = 0
    open_question_keys: tuple[str, ...] = ()
    blocking_open_question_keys: tuple[str, ...] = ()
    council_may_convene: bool = False
    elapsed_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.stopped_by not in LOOP_STOP_REASONS:
            raise ValueError(
                f"{self.stopped_by!r} is not a loop stop reason. Every member is either "
                "a limit or a completion state; there is no 'other', because a run that "
                "stopped for an unnamed reason is one nobody can widen a budget for."
            )

    @property
    def stopped_by_a_limit(self) -> bool:
        return self.stopped_by in LIMIT_STOP_REASONS

    @property
    def is_complete_analysis(self) -> bool:
        """True only when the work finished AND the Council may convene.

        Not "did the agents finish". A run whose agents all returned successfully but
        whose blocking question is unanswered is not a complete analysis, and calling it
        one is the specific lie this property exists to prevent.
        """
        return not self.stopped_by_a_limit and self.council_may_convene

    def to_dict(self) -> dict[str, Any]:
        return {
            "stopped_by": self.stopped_by,
            "stopped_by_a_limit": self.stopped_by_a_limit,
            "is_complete_analysis": self.is_complete_analysis,
            "rounds": len(self.rounds),
            "tasks_run": self.tasks_run,
            "tool_calls": self.tool_calls,
            "findings": self.findings,
            "gaps_open": self.gaps_open,
            "gaps_accepted": self.gaps_accepted,
            "open_question_keys": list(self.open_question_keys),
            "blocking_open_question_keys": list(self.blocking_open_question_keys),
            "council_may_convene": self.council_may_convene,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


async def run_investigation(
    session: Any,
    run: Any,
    plan: ResearchPlan,
    *,
    investigator: Investigator,
    limits: ModeLimits | None = None,
    completion_rules: "Sequence[str]" = (),
    now: Any = None,
) -> LoopResult:
    """Execute the plan within its bounds. Never raises; always names its reason."""
    limits = limits or plan.limits
    clock = now or time.monotonic
    started = clock()

    questions_by_key = {q.key: q for q in plan.questions}
    answered: set[str] = set()
    tool_calls = 0
    tasks_run = 0
    findings_total = 0
    rounds: list[RoundRecord] = []
    stop_reason: str | None = None

    pending: list[tuple[str, list[str]]] = [
        (task.role_id, list(task.question_keys)) for task in plan.tasks
    ]
    #: V3.18.2 — everything retrieved per question across ALL rounds, so a contract is
    #: judged over the question's whole evidence and not only its latest round's.
    evidence_so_far: dict[str, dict[str, Any]] = {}
    #: V3.18.3 — external searches already spent per question, so a follow-up uses the
    #: NEXT search intent and a question's own cap binds across rounds.
    searches_so_far: dict[str, int] = {}

    for round_index in range(limits.max_rounds):
        if not pending:
            stop_reason = STOPPED_NOTHING_LEFT if round_index else STOPPED_COMPLETE
            break
        record = RoundRecord(index=round_index)
        round_started = clock()

        for role_id, question_keys in pending:
            # Checked BEFORE the work each limit would authorise. A budget satisfied by
            # noticing afterwards is a budget that was exceeded.
            if tasks_run >= limits.max_tasks:
                stop_reason = STOPPED_MAX_TASKS
                break
            if tool_calls >= limits.max_tool_calls:
                stop_reason = STOPPED_MAX_TOOL_CALLS
                break
            if (clock() - started) >= limits.max_wall_seconds:
                stop_reason = STOPPED_MAX_WALL_SECONDS
                break

            task = await ledger.add_task(
                session,
                run,
                role=role_id,
                question_keys=question_keys,
                round_index=round_index,
            )
            questions = [
                questions_by_key[key] for key in question_keys if key in questions_by_key
            ]
            try:
                kwargs: dict[str, Any] = {
                    "role_id": role_id,
                    "questions": questions,
                    "round_index": round_index,
                    "remaining_tool_calls": max(0, limits.max_tool_calls - tool_calls),
                }
                # V3.18.3 — what earlier rounds established, so a follow-up starts where
                # the last round ended. Offered only to an investigator that accepts it:
                # the Protocol predates it and every existing implementer stays valid.
                if _accepts(investigator.investigate, "question_context"):
                    kwargs["question_context"] = _question_context(
                        question_keys, evidence_so_far, searches_so_far
                    )
                outcome = await investigator.investigate(**kwargs)
            except Exception as exc:  # noqa: BLE001 - one role must not end the run
                await ledger.finish_task(
                    session,
                    task,
                    status=ledger.TASK_FAILED,
                    detail=f"{type(exc).__name__}",
                )
                tasks_run += 1
                record.tasks_run += 1
                continue

            tasks_run += 1
            record.tasks_run += 1
            tool_calls += max(0, int(outcome.tool_calls))
            record.tool_calls += max(0, int(outcome.tool_calls))

            for draft in outcome.findings:
                question_def = questions_by_key.get(draft.question_key or "")
                domain = _domain_of(question_def, role_id)
                try:
                    await ledger.record_finding(
                        session,
                        run,
                        statement=draft.statement,
                        evidence_ids=draft.evidence_ids,
                        calculation_ids=draft.calculation_ids,
                        task=task,
                        question_key=draft.question_key,
                        mechanism=draft.mechanism,
                        direction=draft.direction,
                        confidence=draft.confidence,
                        period_key=draft.period_key,
                        scope_key=draft.scope_key,
                        originating_role=role_id,
                        # V3.18.2 — ownership. The finding belongs to its question's
                        # domain, so the report can place it exactly once.
                        domain=domain,
                        topic_key=(
                            f"{domain}.{draft.question_key}"
                            if domain and draft.question_key
                            else None
                        ),
                        source_kinds=draft.source_kinds,
                    )
                except ledger.UnsupportedFindingError:
                    # An investigator that returned a statement with no support has
                    # produced a claim, not a finding. It becomes a GAP with the reason,
                    # so the run records what was asserted and why it was not accepted.
                    await ledger.record_gap(
                        session,
                        run,
                        gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
                        description=(
                            f"{role_id} stated {draft.statement[:200]!r} with no "
                            "evidence or calculation behind it."
                        ),
                        question_key=draft.question_key,
                        why_it_matters=(
                            "An unsupported statement cannot enter the record, so the "
                            "question it addressed is still open."
                        ),
                    )
                    record.gaps_opened += 1
                    continue
                findings_total += 1
                record.findings += 1

            for gap in outcome.gaps:
                await ledger.record_gap(
                    session,
                    run,
                    gap_type=gap.gap_type,
                    description=gap.description,
                    question_key=gap.question_key,
                    why_it_matters=gap.why_it_matters,
                    sources_tried=gap.sources_tried,
                    closable=gap.closable,
                    blocks_council=gap.blocks_council,
                )
                record.gaps_opened += 1

            answered.update(outcome.answered_question_keys)
            await _mark_answered(session, run, outcome.answered_question_keys)
            await _judge_contracts(
                session,
                run,
                outcome,
                questions_by_key,
                evidence_so_far,
            )
            for key, steps in outcome.acquisition_steps.items():
                searches_so_far[key] = searches_so_far.get(key, 0) + sum(
                    1 for step in steps if step.get("rung") == "external_search"
                    and step.get("query")
                )

            if outcome.failed:
                await ledger.finish_task(
                    session,
                    task,
                    status=ledger.TASK_FAILED,
                    detail=outcome.detail,
                )
            elif outcome.stopped_by:
                await ledger.finish_task(
                    session,
                    task,
                    status=ledger.TASK_PARTIAL,
                    stopped_by=outcome.stopped_by,
                    detail=outcome.detail,
                )
            else:
                await ledger.finish_task(
                    session, task, status=ledger.TASK_COMPLETE, detail=outcome.detail
                )

        record.elapsed_seconds = clock() - round_started
        rounds.append(record)
        run.rounds_completed = len(rounds)

        if stop_reason is not None:
            break

        summary = await ledger.summarise(session, run)
        if _rules_satisfied(summary, completion_rules):
            stop_reason = STOPPED_COMPLETE
            break

        # Gap review. Only a CLOSABLE gap becomes a follow-up: an unclosable one would
        # consume a whole round to fail again.
        follow_ups = await _follow_up_tasks(
            session,
            run,
            plan,
            answered_keys=answered,
            improvable_keys=_improvable(
                questions_by_key, evidence_so_far, searches_so_far
            ),
        )
        if not follow_ups:
            stop_reason = STOPPED_NOTHING_LEFT
            break
        pending = follow_ups
    else:
        # The `for` ran to completion without breaking: the round budget is the reason.
        if stop_reason is None:
            stop_reason = STOPPED_MAX_ROUNDS

    if stop_reason is None:
        stop_reason = STOPPED_NOTHING_LEFT

    # Whatever remains open and unclosable is ACCEPTED — the run finished with it open
    # and says so, which is the honest outcome for a gap no source can close.
    accepted = 0
    for open_gap in await ledger.open_gaps(session, run, limit=500):
        if not open_gap.closable:
            await ledger.accept_gap(session, open_gap)
            accepted += 1

    await _record_unresolved(
        session,
        run,
        plan,
        stop_reason=stop_reason,
        evidence_so_far=evidence_so_far,
    )

    summary = await ledger.summarise(session, run)
    open_keys = await _open_question_keys(session, run)
    result = LoopResult(
        stopped_by=stop_reason,
        rounds=rounds,
        tasks_run=tasks_run,
        tool_calls=tool_calls,
        findings=findings_total,
        gaps_open=summary.gaps_open,
        gaps_accepted=accepted,
        open_question_keys=tuple(open_keys),
        blocking_open_question_keys=tuple(
            key for key in open_keys if questions_by_key.get(key, None) and
            questions_by_key[key].blocking
        ),
        council_may_convene=summary.council_may_convene,
        elapsed_seconds=clock() - started,
    )
    await ledger.close_run(
        session,
        run,
        status=(
            ledger.RUN_STOPPED if result.stopped_by_a_limit else ledger.RUN_REVIEWING
        ),
        stopped_by=stop_reason if result.stopped_by_a_limit else None,
        consumption={"tool_calls": tool_calls, "tasks": tasks_run},
    )
    return result


def _domain_of(question: Any, role_id: str) -> str | None:
    """A question's domain, or its owner role's default one for a pre-V3.18 question."""
    from app.services.director.domains import DOMAIN_SPECS

    declared = getattr(question, "domain", None)
    if declared:
        return declared
    owner = getattr(question, "owner_role", None) or role_id
    return next((spec.domain for spec in DOMAIN_SPECS if spec.default_owner == owner), None)


async def _judge_contracts(
    session: Any,
    run: Any,
    outcome: TaskOutcome,
    questions_by_key: dict[str, Any],
    evidence_so_far: dict[str, dict[str, Any]],
) -> None:
    """Judge each worked question's evidence contract and record the verdict.

    The verdict is the LOOP's, over what the tools returned. It is recorded beside the
    question with the missing dimension named, so "partially answered — no independent
    statistical source" is a fact the next round and a reader can both act on.

    Never raises: a record that fails must not cost the run its findings.
    """
    from app.services.director.contracts import evaluate_contract

    worked = set(outcome.question_evidence) | set(outcome.acquisition_steps)
    for key in worked:
        pool = evidence_so_far.setdefault(key, {})
        for ref in outcome.question_evidence.get(key, ()):
            if ref is not None:
                pool.setdefault(ref.citation_id, ref)
        question = questions_by_key.get(key)
        evaluation = evaluate_contract(
            getattr(question, "evidence_contract", None), pool.values()
        )
        detail = evaluation.to_dict()
        detail["citation_ids"] = sorted(pool)[:60]
        try:
            await ledger.update_question_graph_state(
                session,
                run,
                key,
                contract_status=evaluation.status,
                contract_detail=detail,
                acquisition_steps=outcome.acquisition_steps.get(key, ()),
                clear_unresolved=evaluation.satisfied,
            )
        except Exception:  # noqa: BLE001 - the audit record must not end the run
            continue


async def _record_unresolved(
    session: Any,
    run: Any,
    plan: ResearchPlan,
    *,
    stop_reason: str,
    evidence_so_far: dict[str, dict[str, Any]],
) -> None:
    """Name why every question that did not reach its contract is still open.

    Four answers a reader needs told apart: nothing citable was acquired; something was
    acquired but not enough of the right kind; a budget ended the run first; or no tool
    could answer it at all. They call for different fixes, and "open" alone says none.
    """
    from sqlalchemy import select

    from app.models.ledger import ResearchQuestion

    unassignable = {
        key: reason for key, reason in plan.unassignable
    }
    rows = (
        await session.execute(
            select(ResearchQuestion).where(ResearchQuestion.research_run_id == run.id)
        )
    ).scalars().all()
    limited = stop_reason in LIMIT_STOP_REASONS
    for row in rows:
        if row.contract_status == "satisfied":
            continue
        if row.question_key in unassignable:
            # A question dropped because the MODE's task cap was reached is a budget
            # outcome, not a capability one — widening the mode answers it.
            reason = (
                ledger.UNRESOLVED_BUDGET_EXHAUSTED
                if "budget" in unassignable[row.question_key]
                else ledger.UNRESOLVED_TOOL_UNAVAILABLE
            )
        elif evidence_so_far.get(row.question_key):
            reason = ledger.UNRESOLVED_CONTRACT_UNMET
        elif row.question_key not in evidence_so_far and limited:
            reason = ledger.UNRESOLVED_BUDGET_EXHAUSTED
        elif row.question_key not in evidence_so_far and row.contract_status is None:
            reason = ledger.UNRESOLVED_NOT_REACHED
        else:
            reason = ledger.UNRESOLVED_NOT_ACQUIRED
        if row.contract_status is None:
            row.contract_status = "unmet"
        row.unresolved_reason = reason
    await session.flush()


def _rules_satisfied(
    summary: ledger.LedgerSummary, rules: "Sequence[str]"
) -> bool:
    """Whether the playbook's completion rules are met.

    ``all_blocking_questions_answered`` is understood; an unrecognised rule is treated as
    **not satisfied**, because a rule the platform cannot evaluate must not be able to
    declare a run complete by being ignored.
    """
    if not rules:
        return summary.council_may_convene and summary.questions_open == 0
    for rule in rules:
        if rule == "all_blocking_questions_answered":
            if summary.questions_blocking_open:
                return False
        elif rule == "no_council_blocking_gaps":
            if summary.gaps_blocking_council:
                return False
        else:
            return False
    return True


async def _mark_answered(
    session: Any, run: Any, question_keys: "Sequence[str]"
) -> None:
    if not question_keys:
        return
    from sqlalchemy import update

    from app.models.ledger import ResearchQuestion

    await session.execute(
        update(ResearchQuestion)
        .where(
            ResearchQuestion.research_run_id == run.id,
            ResearchQuestion.question_key.in_(list(question_keys)),
        )
        .values(resolution_status=ledger.QUESTION_ANSWERED)
    )
    await session.flush()


async def _open_question_keys(session: Any, run: Any) -> list[str]:
    from sqlalchemy import select

    from app.models.ledger import ResearchQuestion

    stmt = (
        select(ResearchQuestion.question_key)
        .where(
            ResearchQuestion.research_run_id == run.id,
            ResearchQuestion.resolution_status == ledger.QUESTION_OPEN,
        )
        .order_by(ResearchQuestion.question_key)
    )
    return [row[0] for row in (await session.execute(stmt)).all()]


async def _follow_up_tasks(
    session: Any,
    run: Any,
    plan: ResearchPlan,
    *,
    answered_keys: "set[str]",
    improvable_keys: "set[str] | None" = None,
) -> list[tuple[str, list[str]]]:
    """A closable gap becomes a follow-up task for a role that can address it.

    The gap's own question decides the role, reusing the plan's assignment rather than
    re-deriving one — so a follow-up goes to the specialist that already has the tools,
    and a question nobody could answer in round one is not re-assigned in round two to
    fail identically.
    """
    gaps = await ledger.open_gaps(session, run, closable_only=True, limit=100)
    improvable = set(improvable_keys or ())
    if not gaps and not improvable:
        return []
    role_for_question: dict[str, str] = {}
    for task in plan.tasks:
        for key in task.question_keys:
            role_for_question.setdefault(key, task.role_id)

    by_role: dict[str, list[str]] = {}

    def _add(question_key: str) -> None:
        role_id = role_for_question.get(question_key)
        if role_id is None:
            return
        keys = by_role.setdefault(role_id, [])
        if question_key not in keys:
            keys.append(question_key)

    for gap in gaps:
        question_key = gap.question_key
        if not question_key or question_key in answered_keys:
            continue
        _add(question_key)
    # V3.18.3 — a question ANSWERED with a partial contract is still worth a round when
    # acquisition can still improve it. "Has a finding" used to end a question's
    # research, so one issuer excerpt settled the industry question for good.
    for question_key in sorted(improvable):
        _add(question_key)
    return sorted(by_role.items())


def _accepts(fn: Any, parameter: str) -> bool:
    try:
        return parameter in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _question_context(
    question_keys: "Sequence[str]",
    evidence_so_far: dict[str, dict[str, Any]],
    searches_so_far: dict[str, int],
) -> dict[str, Any]:
    from app.services.agents.investigator import QuestionContext

    return {
        key: QuestionContext(
            prior_evidence=tuple(evidence_so_far.get(key, {}).values()),
            external_searches_done=searches_so_far.get(key, 0),
        )
        for key in question_keys
    }


def _improvable(
    questions_by_key: dict[str, Any],
    evidence_so_far: dict[str, dict[str, Any]],
    searches_so_far: dict[str, int],
) -> set[str]:
    """Questions whose contract is unmet and whose ladder still has a rung to climb."""
    from app.services.director.contracts import evaluate_contract

    out: set[str] = set()
    for key, question in questions_by_key.items():
        contract = getattr(question, "evidence_contract", None)
        if contract is None or not contract.allow_external:
            continue
        if searches_so_far.get(key, 0) >= contract.max_external_searches:
            continue
        if key not in evidence_so_far and key not in searches_so_far:
            # Never worked (unassigned, or dropped for capacity): not a follow-up.
            continue
        if evaluate_contract(contract, evidence_so_far.get(key, {}).values()).satisfied:
            continue
        out.add(key)
    return out


__all__ = [
    "LIMIT_STOP_REASONS",
    "LOOP_STOP_REASONS",
    "STOPPED_COMPLETE",
    "STOPPED_MAX_ROUNDS",
    "STOPPED_MAX_TASKS",
    "STOPPED_MAX_TOOL_CALLS",
    "STOPPED_MAX_WALL_SECONDS",
    "STOPPED_NOTHING_LEFT",
    "FindingDraft",
    "GapDraft",
    "Investigator",
    "LoopResult",
    "RoundRecord",
    "TaskOutcome",
    "run_investigation",
]
