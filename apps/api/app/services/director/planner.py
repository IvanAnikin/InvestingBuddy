"""The Research Director — V3.5 Slice 5.2.

WHAT IT DOES AND WHAT IT MUST NOT
=================================
The Director **plans**; it does not decide the investment view. It resolves the subject,
loads a playbook, inspects prior research, produces bounded prioritized questions,
assigns them to roles, allocates budget, and — after each round — decides whether there is
enough to convene the Council.

Step ten is the interesting one. **The completion test is not "did the agents finish"**;
it is "are the playbook's completion rules satisfied, or is the budget exhausted?" — and
if the latter, the run says so explicitly rather than presenting a thin analysis as a
complete one.

IT IS BOUNDED LIKE ANY OTHER AGENT
==================================
A fixed maximum number of questions, a fixed maximum number of tasks, and a plan type
that **cannot express "research everything about this company"**. The maxima come from
``ModeLimits`` (slice 4.11); the Director records them, it does not choose them.

THE DETERMINISTIC CORE IS THE PRODUCT
=====================================
Planning here is composition, not generation: playbook questions, then prior open gaps,
then a baseline set. A model may *refine* the result — reprioritise, add at most a few
questions — and when no model is available the plan is unchanged rather than absent. That
is the deterministic-chair-fallback property V3.0 already established, applied to
planning, and it is why the Director cannot become a single point of failure.

A model's contribution is also **bounded and attributed**: every question carries its
``origin``, so "the model added this" is queryable, and it can never exceed the question
cap or mark a question blocking. **Only a playbook may declare a question blocking**,
because a blocking question stops the Council convening and a model that could set one
could stop every run.

WHO CANNOT ANSWER IS A FACT, NOT A JUDGEMENT
============================================
A question declares the tools an answer needs. If no role holds them, the Director raises
a ``tool_unavailable`` gap instead of assigning it — the mechanism that keeps a confident
model from filling a hole with prose.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.services.agent_tools.contracts import EXTERNAL_TOOL_NAMES
from app.services.director.roles import (
    ALWAYS_PRESENT,
    RoleSpec,
    external_research_roles,
    role_for,
    roles_that_can_answer,
)
from app.services.ledger import store as ledger
from app.services.research_mode import ModeLimits, ResearchMode, limits_for, parse_mode

#: A hard ceiling on how many questions a plan may hold, independent of mode. A mode can
#: lower it and nothing can raise it: a plan with 400 questions is not a plan.
ABSOLUTE_MAX_QUESTIONS = 40

#: The baseline every run asks, whatever the industry. Deliberately short: a long generic
#: list is how a specialised playbook's questions get crowded out of the budget.
BASELINE_QUESTIONS: tuple[tuple[str, str, frozenset[str]], ...] = (
    (
        "identity",
        "Which legal entity is this, and which securities and listings does it have?",
        frozenset({"lookup_entity"}),
    ),
    (
        "revenue_trajectory",
        "How has Group revenue moved over the available reporting periods, and in "
        "which period type?",
        frozenset({"get_financial_series"}),
    ),
    (
        "profitability",
        "What are the reported margins, and what do the deterministic calculations make of them?",
        frozenset({"get_calculated_metrics"}),
    ),
    (
        "balance_sheet_risk",
        "What is the leverage position, and what does the filing say about covenants "
        "or refinancing?",
        frozenset({"get_financial_facts", "search_company_corpus"}),
    ),
    (
        "recent_disclosure",
        "What has the issuer disclosed most recently, and for which period?",
        frozenset({"search_company_corpus"}),
    ),
)

#: Asked only when the external tools are implemented — V3.12. Kept out of
#: ``BASELINE_QUESTIONS`` deliberately: with the external path off, a question requiring
#: ``search_web`` would be planned and then immediately marked unassignable in every
#: single run, which turns a configuration choice into permanent noise in the plan.
#:
#: It asks the one thing the platform's own holdings are worst at: what the issuer has
#: reported *since* whatever the corpus happens to contain. The wording is deliberately
#: concrete — an earlier draft asked for "what is NOT already in this platform's
#: holdings", and a live run showed why that fails: the provider cannot evaluate what
#: this platform holds, so it searched for 80 seconds and answered nothing. A question
#: only the asker can interpret is not a question.
EXTERNAL_QUESTIONS: tuple[tuple[str, str, frozenset[str]], ...] = (
    (
        "recent_external_developments",
        "What were the headline figures of this issuer's most recently reported period, "
        "and which primary source states them? Give the figure, the period it covers, "
        "and the issuer's own or the regulator's page.",
        frozenset({"search_web", "fetch_public_source"}),
    ),
)


@runtime_checkable
class PlaybookLike(Protocol):
    """The shape the Director needs from a playbook.

    A Protocol rather than an import, because the playbooks themselves are V3.6 and the
    Director must not be blocked on them — and because a Director that could only run
    with a playbook could not research a company no playbook covers.
    """

    playbook_id: str
    version: int

    def mandatory_questions(self) -> "Sequence[PlannedQuestion]": ...  # pragma: no cover - protocol

    def specialist_roles(self) -> "Sequence[str]": ...  # pragma: no cover - protocol

    def completion_rules(self) -> "Sequence[str]": ...  # pragma: no cover - protocol


@runtime_checkable
class PlanRefiner(Protocol):
    """An optional model that reprioritises a plan. Never required.

    Returns question keys in its preferred order, plus at most a few additions. It cannot
    remove a playbook question, cannot exceed the cap, and cannot mark anything blocking.
    """

    async def refine(
        self, *, subject: str, questions: "Sequence[PlannedQuestion]", max_new: int
    ) -> "tuple[Sequence[str], Sequence[PlannedQuestion]]": ...  # pragma: no cover - protocol


@dataclass(frozen=True)
class PlannedQuestion:
    """One question, with what an answer to it requires."""

    key: str
    text: str
    origin: str
    #: The tools an answer needs. If no role holds them all, this becomes a gap rather
    #: than an assignment — a set operation, not a judgement.
    required_tools: frozenset[str] = frozenset()
    priority: int = 3
    #: **Only a playbook may set this.** A blocking question stops the Council
    #: convening, so a model able to set one could stop every run.
    blocking: bool = False
    required_evidence_classes: tuple[str, ...] = ()
    #: The deterministic calculations an answer needs, straight from the playbook.
    #: This is the ONLY source of metric names for ``get_calculated_metrics``: the
    #: tool's vocabulary is closed and a model choosing from it would be a model
    #: choosing a filter. Dropping this on the way through the planner is what made
    #: every calculated-metrics call fail ``invalid_arguments``, which in turn made
    #: the luxury playbook's blocking question permanently unanswerable.
    required_calculations: tuple[str, ...] = ()


@dataclass
class PlannedTask:
    """One assignment: a role, its questions, and the bounds it runs under."""

    role_id: str
    question_keys: list[str] = field(default_factory=list)
    round_index: int = 0
    max_iterations: int = 4
    tool_budget: dict[str, int] = field(default_factory=dict)


@dataclass
class ResearchPlan:
    """What the Director decided, and what it could not assign."""

    subject: str
    mode: ResearchMode
    limits: ModeLimits
    questions: list[PlannedQuestion] = field(default_factory=list)
    tasks: list[PlannedTask] = field(default_factory=list)
    #: Questions no role can answer, as ``(question_key, reason)``. These become gaps.
    unassignable: list[tuple[str, str]] = field(default_factory=list)
    #: Questions dropped because the plan hit its cap, lowest priority first. Recorded
    #: rather than silently absent: a plan that quietly forgot a playbook's question
    #: would report a methodology it did not apply.
    dropped_for_capacity: list[str] = field(default_factory=list)
    playbook_versions: dict[str, int] = field(default_factory=dict)
    refined_by_model: bool = False

    @property
    def blocking_questions(self) -> list[PlannedQuestion]:
        return [q for q in self.questions if q.blocking]

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "mode": self.mode.value,
            "question_count": len(self.questions),
            "task_count": len(self.tasks),
            "blocking_question_count": len(self.blocking_questions),
            "unassignable": [
                {"question_key": key, "reason": reason} for key, reason in self.unassignable
            ],
            "dropped_for_capacity": list(self.dropped_for_capacity),
            "playbook_versions": dict(self.playbook_versions),
            "refined_by_model": self.refined_by_model,
            "limits": {
                "max_rounds": self.limits.max_rounds,
                "max_tasks": self.limits.max_tasks,
                "max_tool_calls": self.limits.max_tool_calls,
            },
        }


def implemented_tools(cfg: "Any | None" = None) -> frozenset[str]:
    """Tool names that actually have a handler, not merely a place in the vocabulary.

    ``TOOL_NAMES`` is the *vocabulary* — nineteen names, twelve of which V3.3 reserved
    for capabilities that did not exist yet. A ``RoleSpec`` is checked against the
    vocabulary, which is right: a role is a declaration, and declaring an intent to use
    ``get_recent_filings`` is not a lie.

    But the Director must not assign a question to a role on the strength of a tool
    nothing implements. That failure surfaces as a mysterious tool refusal in the middle
    of a run rather than as an unassignable question at plan time — and a mid-run refusal
    looks like a coverage problem where a plan-time one is a coverage *fact*.

    Found by V3.6's playbooks: biotech's blocking ``pipeline_state`` needs
    ``get_recent_filings``, which two roles declare and nothing implements.

    Takes ``cfg`` because one registration is CONDITIONAL: the external tools exist only
    behind their feature flag (V3.12). Reading the process-global settings here instead
    would mean a run's own configuration could not decide its own tool surface — the
    pipeline threads ``cfg`` through every layer precisely so that a run is explicit
    about what it is allowed to do, and this function silently opted out of that.
    """
    try:
        from app.services.agent_tools.builtin import register_builtins
        from app.services.agent_tools.registry import ToolRegistry

        return frozenset(register_builtins(ToolRegistry(), cfg=cfg).names())
    except Exception:  # noqa: BLE001 - a planner must not fail on introspection
        from app.services.agent_tools.contracts import TOOL_NAMES

        return frozenset(TOOL_NAMES)


def _baseline_questions() -> list[PlannedQuestion]:
    return [
        PlannedQuestion(
            key=key,
            text=text,
            origin=ledger.ORIGIN_DIRECTOR,
            required_tools=tools,
            priority=2,
        )
        for key, text, tools in BASELINE_QUESTIONS
    ]


def _external_questions(available: "frozenset[str]") -> list[PlannedQuestion]:
    """The external question, when and only when something implements its tools."""
    return [
        PlannedQuestion(
            key=key,
            text=text,
            origin=ledger.ORIGIN_DIRECTOR,
            required_tools=tools,
            priority=3,
        )
        for key, text, tools in EXTERNAL_QUESTIONS
        if tools <= available
    ]


async def plan_research(
    *,
    subject: str,
    mode: str | ResearchMode = "standard",
    playbooks: "Sequence[PlaybookLike]" = (),
    prior_open_gaps: "Sequence[tuple[str, str]]" = (),
    refiner: PlanRefiner | None = None,
    extra_roles: "Sequence[str]" = (),
    cfg: "Any | None" = None,
) -> ResearchPlan:
    """Build a bounded plan. Never raises on a model failure.

    ``prior_open_gaps`` is ``(question_key, text)`` from a previous run — the Research
    Memory seam (V3.8). Passing it in rather than reading it here keeps the Director
    testable and keeps memory from becoming a hidden input.
    """
    resolved = mode if isinstance(mode, ResearchMode) else parse_mode(str(mode))
    limits = limits_for(resolved)
    plan = ResearchPlan(subject=subject, mode=resolved, limits=limits)

    questions: dict[str, PlannedQuestion] = {}

    # 1. Playbook questions first: they are the methodology, and they are the only
    #    source permitted to mark a question blocking.
    for playbook in playbooks:
        plan.playbook_versions[playbook.playbook_id] = playbook.version
        for question in playbook.mandatory_questions():
            questions.setdefault(
                question.key,
                PlannedQuestion(
                    key=question.key,
                    text=question.text,
                    origin=ledger.ORIGIN_PLAYBOOK,
                    required_tools=frozenset(question.required_tools),
                    priority=question.priority,
                    blocking=question.blocking,
                    required_evidence_classes=tuple(question.required_evidence_classes),
                    required_calculations=tuple(question.required_calculations),
                ),
            )

    # 2. Prior gaps: a gap the last run could not close is this run's question, and it
    #    carries its origin so "we are re-asking" is visible.
    for key, text in prior_open_gaps:
        questions.setdefault(
            key,
            PlannedQuestion(
                key=key,
                text=text,
                origin=ledger.ORIGIN_PRIOR_GAP,
                priority=2,
            ),
        )

    # 3. The baseline, last, so a specialised playbook's questions are never crowded
    #    out of the budget by generic ones.
    for question in _baseline_questions():
        questions.setdefault(question.key, question)

    # 3b. The external question, only when something implements its tools — which is to
    #     say only when the external feature flag is on. Same single condition the role
    #     seating uses below, and for the same reason: one switch, not two that can
    #     disagree about whether the platform is allowed to reach the open web.
    for question in _external_questions(implemented_tools(cfg)):
        questions.setdefault(question.key, question)

    ordered = sorted(
        questions.values(),
        # Blocking first, then priority, then a stable key order. A blocking question
        # dropped for capacity would be a methodology silently not applied.
        key=lambda q: (not q.blocking, q.priority, q.key),
    )

    # 4. An optional model refinement, bounded and unable to do harm.
    if refiner is not None:
        ordered, plan.refined_by_model = await _refine(refiner, subject, ordered, limits)

    cap = min(ABSOLUTE_MAX_QUESTIONS, max(1, limits.max_tasks * 3))
    if len(ordered) > cap:
        plan.dropped_for_capacity = [q.key for q in ordered[cap:]]
        ordered = ordered[:cap]
    plan.questions = ordered

    # 5. Assignment. Who cannot answer is a fact, not a judgement.
    wanted_roles = list(ALWAYS_PRESENT)
    for playbook in playbooks:
        for role_id in playbook.specialist_roles():
            if role_id not in wanted_roles and role_for(role_id) is not None:
                wanted_roles.append(role_id)
    for role_id in extra_roles:
        if role_id not in wanted_roles and role_for(role_id) is not None:
            wanted_roles.append(role_id)

    assignments: dict[str, PlannedTask] = {}
    available = implemented_tools(cfg)

    # V3.12. A role that reaches outside the platform joins the run when — and only
    # when — its tools are actually implemented, which is to say when the external
    # feature flag is on: `register_external_tools` is the one conditional registration
    # in the builtin set. Deriving presence from the tool surface rather than reading
    # the flag again means there is ONE condition, and a role cannot be seated with
    # nothing to call.
    for role_id in external_research_roles():
        role = role_for(role_id)
        if role is None or role_id in wanted_roles:
            continue
        if role.tools & EXTERNAL_TOOL_NAMES <= available:
            wanted_roles.append(role_id)
    for question in plan.questions:
        missing = set(question.required_tools) - available
        if missing:
            # A role DECLARES this tool and nothing implements it. Refused at plan time,
            # where it is a coverage fact, rather than mid-run where it is a mystery.
            plan.unassignable.append(
                (
                    question.key,
                    f"no implementation for {sorted(missing)}",
                )
            )
            continue
        # V3.12. A role that reaches OUTSIDE the platform may answer only a question
        # that actually needs to. Two live defects came from omitting this:
        #
        #  * the external role also holds `search_company_corpus`, so it competed for
        #    corpus questions and — winning the `_least_loaded` tie on role id — took
        #    `recent_disclosure` away from `business_analyst`, sending a question the
        #    corpus can answer to a paid vendor. Turning a flag on must not re-route
        #    work that has nothing to do with it.
        #  * a carry-forward gap question has EMPTY `required_tools`, so every role can
        #    "answer" it; assigned externally, the investigator's fallback runs the
        #    role's whole tool list and issues a vendor search for a question nobody
        #    asked to be searched. That is a bill arriving on the second run of a
        #    company and on no other.
        wants_external = bool(set(question.required_tools) & EXTERNAL_TOOL_NAMES)

        def _eligible(role: "RoleSpec") -> bool:
            return wants_external or not (role.tools & EXTERNAL_TOOL_NAMES)

        candidates = [
            role
            for role in roles_that_can_answer(question.required_tools)
            if role.role_id in wanted_roles and _eligible(role)
        ]
        if not candidates and question.required_tools:
            # Widen once to any declared role before giving up — a specialist the
            # playbook did not ask for is still a role the platform has. The external
            # restriction still applies: widening is about specialists, not about
            # spending.
            candidates = [
                role
                for role in roles_that_can_answer(question.required_tools)
                if _eligible(role)
            ]
        if not candidates:
            plan.unassignable.append(
                (
                    question.key,
                    (f"no role holds {sorted(question.required_tools) or 'the required tools'}"),
                )
            )
            continue
        chosen = _least_loaded(candidates, assignments)
        task = assignments.setdefault(
            chosen.role_id,
            PlannedTask(
                role_id=chosen.role_id,
                max_iterations=min(chosen.max_iterations, limits.max_rounds * 4),
                tool_budget=dict(chosen.tool_budget),
            ),
        )
        task.question_keys.append(question.key)

    tasks = list(assignments.values())
    if len(tasks) > limits.max_tasks:
        # Keep the tasks carrying the most questions; the rest become unassignable with
        # a reason, never silently dropped.
        tasks.sort(key=lambda t: (-len(t.question_keys), t.role_id))
        for task in tasks[limits.max_tasks :]:
            for key in task.question_keys:
                plan.unassignable.append((key, "task budget exhausted"))
        tasks = tasks[: limits.max_tasks]
    plan.tasks = sorted(tasks, key=lambda t: t.role_id)
    return plan


def _least_loaded(
    candidates: "Sequence[RoleSpec]", assignments: "dict[str, PlannedTask]"
) -> RoleSpec:
    """Spread questions across roles deterministically.

    Ties break on ``role_id`` so two runs of the same plan assign identically — a
    planner whose output depended on dict ordering would make every comparison between
    two runs a comparison of two plans.
    """
    return min(
        candidates,
        key=lambda role: (
            len(assignments[role.role_id].question_keys) if role.role_id in assignments else 0,
            role.role_id,
        ),
    )


async def _refine(
    refiner: PlanRefiner,
    subject: str,
    questions: "list[PlannedQuestion]",
    limits: ModeLimits,
) -> "tuple[list[PlannedQuestion], bool]":
    """Let a model reorder and add a little. It can never remove or block.

    A failure returns the plan unchanged. The deterministic path is the product; the
    model is an improvement to it, and an improvement that can take the system down is
    not one.
    """
    by_key = {q.key: q for q in questions}
    try:
        order, additions = await refiner.refine(
            subject=subject, questions=list(questions), max_new=3
        )
    except Exception:  # noqa: BLE001 - a model failure must not cost the plan
        return questions, False

    seen: list[PlannedQuestion] = []
    for key in order:
        question = by_key.pop(str(key), None)
        if question is not None:
            seen.append(question)
    # Anything the model omitted is appended, not dropped. A model cannot delete a
    # playbook's mandatory question by leaving it out of a list.
    seen.extend(by_key[key] for key in sorted(by_key))

    added = 0
    for question in additions:
        if added >= 3 or question.key in {q.key for q in seen}:
            continue
        seen.append(
            PlannedQuestion(
                key=question.key,
                text=question.text,
                # Attributed, always. "The model added this" has to be queryable.
                origin=ledger.ORIGIN_DIRECTOR,
                required_tools=frozenset(question.required_tools),
                priority=max(3, question.priority),
                # NEVER blocking. Only a playbook may stop a Council convening.
                blocking=False,
            )
        )
        added += 1
    seen.sort(key=lambda q: (not q.blocking, q.priority, q.key))
    return seen, True


async def persist_plan(session: Any, run: Any, plan: ResearchPlan) -> dict[str, int]:
    """Write the plan into the ledger, including what it could not assign.

    An unassignable question becomes a ``tool_unavailable`` gap rather than vanishing.
    That is the mechanism: a role with no declared tool for a question does not answer
    it badly, it raises a gap somebody can act on.
    """
    counts = {"questions": 0, "tasks": 0, "gaps": 0}
    for question in plan.questions:
        await ledger.add_question(
            session,
            run,
            question_key=question.key,
            text=question.text,
            origin=question.origin,
            priority=question.priority,
            blocking=question.blocking,
            required_evidence_classes=question.required_evidence_classes,
        )
        counts["questions"] += 1
    for task in plan.tasks:
        await ledger.add_task(
            session,
            run,
            role=task.role_id,
            question_keys=task.question_keys,
            round_index=task.round_index,
            max_iterations=task.max_iterations,
            tool_budget=task.tool_budget or None,
        )
        counts["tasks"] += 1
    for key, reason in plan.unassignable:
        await ledger.record_gap(
            session,
            run,
            gap_type=ledger.GAP_TOOL_UNAVAILABLE,
            description=f"No role could be assigned to {key!r}: {reason}.",
            question_key=key,
            why_it_matters=(
                "The question was planned and nothing can answer it, so any statement "
                "about it in this run would be unsupported."
            ),
            closable=False,
            blocks_council=any(q.blocking for q in plan.questions if q.key == key),
        )
        counts["gaps"] += 1
    for key in plan.dropped_for_capacity:
        await ledger.record_gap(
            session,
            run,
            gap_type=ledger.GAP_BUDGET_EXHAUSTED,
            description=f"Question {key!r} was dropped: the plan reached its cap.",
            question_key=key,
            why_it_matters=("The run is narrower than its methodology asks for, and says so."),
            closable=True,
        )
        counts["gaps"] += 1
    run.status = ledger.RUN_INVESTIGATING
    await session.flush()
    return counts


__all__ = [
    "ABSOLUTE_MAX_QUESTIONS",
    "BASELINE_QUESTIONS",
    "PlanRefiner",
    "PlannedQuestion",
    "PlannedTask",
    "PlaybookLike",
    "ResearchPlan",
    "persist_plan",
    "plan_research",
]
