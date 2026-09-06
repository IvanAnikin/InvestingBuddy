"""V3.5 Slice 5.2 — the Research Director.

The Director plans; it does not decide the investment view. These tests are mostly about
the ways a planner can quietly stop being bounded, and about the one mechanism that keeps
a confident model from filling a hole with prose: **a role with no declared tool for a
question does not answer it badly, it raises a gap.**
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.services.agent_tools.contracts import TOOL_NAMES
from app.services.director.planner import (
    ABSOLUTE_MAX_QUESTIONS,
    PlannedQuestion,
    persist_plan,
    plan_research,
)
from app.services.director.roles import (
    ALWAYS_PRESENT,
    ON_MISSING_RAISE_GAP,
    ROLES,
    RoleSpec,
    roles_that_can_answer,
)
from app.services.ledger import store as ledger
from app.services.research_mode import ResearchMode, limits_for


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@dataclass
class _Playbook:
    playbook_id: str = "luxury"
    version: int = 1
    questions: list[PlannedQuestion] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)

    def mandatory_questions(self):  # noqa: ANN201
        return self.questions

    def specialist_roles(self):  # noqa: ANN201
        return self.roles

    def completion_rules(self):  # noqa: ANN201
        return self.rules


@dataclass
class _Refiner:
    order: list[str] = field(default_factory=list)
    additions: list[PlannedQuestion] = field(default_factory=list)
    raises: Exception | None = None
    seen: list[str] = field(default_factory=list)

    async def refine(self, *, subject, questions, max_new):  # noqa: ANN001, ANN201
        self.seen = [q.key for q in questions]
        if self.raises is not None:
            raise self.raises
        return (self.order or self.seen), self.additions


# --------------------------------------------------------------------------- #
# Roles are data
# --------------------------------------------------------------------------- #


class TestRoles:
    def test_every_role_names_only_real_tools(self) -> None:
        for role in ROLES.values():
            assert role.tools <= TOOL_NAMES, role.role_id

    def test_a_role_with_no_tools_cannot_be_declared(self) -> None:
        with pytest.raises(ValueError, match="gap generator"):
            RoleSpec(
                role_id="x", display_name="x", focus="x", tools=frozenset()
            )

    def test_a_role_naming_an_unknown_tool_cannot_be_declared(self) -> None:
        """It would silently be unable to answer anything, and the failure would look
        like a research gap."""
        with pytest.raises(ValueError, match="look like a research gap"):
            RoleSpec(
                role_id="x",
                display_name="x",
                focus="x",
                tools=frozenset({"read_the_internet"}),
            )

    def test_assert_anyway_is_not_a_permitted_escalation(self) -> None:
        with pytest.raises(ValueError, match="exists to prevent"):
            RoleSpec(
                role_id="x",
                display_name="x",
                focus="x",
                tools=frozenset({"lookup_entity"}),
                on_missing_evidence="assert_anyway",
            )

    def test_every_role_raises_a_gap_on_missing_evidence(self) -> None:
        assert all(
            role.on_missing_evidence == ON_MISSING_RAISE_GAP for role in ROLES.values()
        )

    def test_who_can_answer_is_a_set_operation(self) -> None:
        assert [r.role_id for r in roles_that_can_answer({"get_transcripts"})] == [
            "management_analyst"
        ]
        # search_web has no implementation, so no role declares it.
        assert roles_that_can_answer({"search_web"}) == []

    def test_the_valuation_role_says_what_it_does_not_do(self) -> None:
        assert "price target" in ROLES["valuation_context_analyst"].focus


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


class TestPlanning:
    async def test_a_plan_with_no_playbook_still_asks_the_baseline(self) -> None:
        plan = await plan_research(subject="CFR")
        assert plan.questions
        assert plan.tasks
        assert {t.role_id for t in plan.tasks} <= set(ALWAYS_PRESENT) | set(ROLES)

    async def test_playbook_questions_come_first(self) -> None:
        """A specialised playbook's questions must not be crowded out of the budget by
        generic ones."""
        playbook = _Playbook(
            questions=[
                PlannedQuestion(
                    key="cash_runway",
                    text="How many quarters of runway remain?",
                    origin=ledger.ORIGIN_PLAYBOOK,
                    required_tools=frozenset({"get_calculated_metrics"}),
                    priority=1,
                    blocking=True,
                )
            ]
        )
        plan = await plan_research(subject="MRNA", playbooks=[playbook])
        assert plan.questions[0].key == "cash_runway"
        assert plan.playbook_versions == {"luxury": 1}

    async def test_a_question_no_role_can_answer_becomes_unassignable(self) -> None:
        """The mechanism. Not assigned anyway, and not answered badly."""
        playbook = _Playbook(
            questions=[
                PlannedQuestion(
                    key="web_scan",
                    text="What is the open web saying?",
                    origin=ledger.ORIGIN_PLAYBOOK,
                    required_tools=frozenset({"search_web"}),
                )
            ]
        )
        plan = await plan_research(subject="CFR", playbooks=[playbook])
        assert ("web_scan", "no role holds ['search_web']") in plan.unassignable
        assert "web_scan" not in {
            key for task in plan.tasks for key in task.question_keys
        }

    async def test_a_specialist_the_playbook_asks_for_is_instantiated(self) -> None:
        playbook = _Playbook(
            roles=["management_analyst"],
            questions=[
                PlannedQuestion(
                    key="guidance_language",
                    text="How has guidance language changed?",
                    origin=ledger.ORIGIN_PLAYBOOK,
                    required_tools=frozenset({"get_transcripts"}),
                )
            ],
        )
        plan = await plan_research(subject="CFR", playbooks=[playbook])
        assert "management_analyst" in {t.role_id for t in plan.tasks}

    async def test_prior_gaps_become_questions_that_say_where_they_came_from(
        self,
    ) -> None:
        plan = await plan_research(
            subject="CFR",
            prior_open_gaps=[("fy24_transcript", "Was an FY2024 transcript published?")],
        )
        question = next(q for q in plan.questions if q.key == "fy24_transcript")
        assert question.origin == ledger.ORIGIN_PRIOR_GAP

    async def test_the_plan_is_deterministic(self) -> None:
        """A planner whose output depended on dict ordering would make every comparison
        between two runs a comparison of two plans."""
        a = await plan_research(subject="CFR")
        b = await plan_research(subject="CFR")
        assert [q.key for q in a.questions] == [q.key for q in b.questions]
        assert [(t.role_id, t.question_keys) for t in a.tasks] == [
            (t.role_id, t.question_keys) for t in b.tasks
        ]

    async def test_a_deeper_mode_permits_more_tasks(self) -> None:
        quick = await plan_research(subject="CFR", mode="quick")
        deep = await plan_research(subject="CFR", mode="deep")
        assert quick.limits.max_tasks < deep.limits.max_tasks
        assert quick.limits.max_rounds < deep.limits.max_rounds

    async def test_the_question_cap_is_absolute(self) -> None:
        """A plan with 400 questions is not a plan."""
        playbook = _Playbook(
            questions=[
                PlannedQuestion(
                    key=f"q{i}",
                    text=f"question {i}",
                    origin=ledger.ORIGIN_PLAYBOOK,
                    required_tools=frozenset({"get_financial_facts"}),
                    priority=5,
                )
                for i in range(500)
            ]
        )
        plan = await plan_research(subject="CFR", mode="max", playbooks=[playbook])
        assert len(plan.questions) <= ABSOLUTE_MAX_QUESTIONS
        assert plan.dropped_for_capacity

    async def test_a_blocking_question_is_never_dropped_for_capacity(self) -> None:
        """A blocking question dropped for capacity is a methodology silently not
        applied."""
        playbook = _Playbook(
            questions=[
                PlannedQuestion(
                    key=f"filler{i}",
                    text="filler",
                    origin=ledger.ORIGIN_PLAYBOOK,
                    required_tools=frozenset({"get_financial_facts"}),
                    priority=1,
                )
                for i in range(200)
            ]
            + [
                PlannedQuestion(
                    key="must_answer",
                    text="the blocking one",
                    origin=ledger.ORIGIN_PLAYBOOK,
                    required_tools=frozenset({"get_financial_facts"}),
                    priority=5,
                    blocking=True,
                )
            ]
        )
        plan = await plan_research(subject="CFR", playbooks=[playbook])
        assert "must_answer" in {q.key for q in plan.questions}
        assert "must_answer" not in plan.dropped_for_capacity

    async def test_task_overflow_is_recorded_not_silently_dropped(self) -> None:
        plan = await plan_research(subject="CFR", mode="quick")
        assert len(plan.tasks) <= limits_for(ResearchMode.QUICK).max_tasks


# --------------------------------------------------------------------------- #
# The optional model
# --------------------------------------------------------------------------- #


class TestRefinement:
    async def test_a_model_failure_leaves_the_plan_intact(self) -> None:
        """The deterministic path is the product. An improvement that can take the
        system down is not one."""
        plain = await plan_research(subject="CFR")
        refined = await plan_research(
            subject="CFR", refiner=_Refiner(raises=RuntimeError("boom"))
        )
        assert [q.key for q in refined.questions] == [q.key for q in plain.questions]
        assert refined.refined_by_model is False

    async def test_a_model_cannot_delete_a_question_by_omitting_it(self) -> None:
        playbook = _Playbook(
            questions=[
                PlannedQuestion(
                    key="cash_runway",
                    text="runway?",
                    origin=ledger.ORIGIN_PLAYBOOK,
                    required_tools=frozenset({"get_calculated_metrics"}),
                    blocking=True,
                )
            ]
        )
        plan = await plan_research(
            subject="MRNA",
            playbooks=[playbook],
            refiner=_Refiner(order=["identity"]),
        )
        assert "cash_runway" in {q.key for q in plan.questions}

    async def test_a_model_addition_is_attributed_and_never_blocking(self) -> None:
        """Only a playbook may stop a Council convening. A model able to set one could
        stop every run."""
        refiner = _Refiner(
            additions=[
                PlannedQuestion(
                    key="model_idea",
                    text="something the model thought of",
                    origin=ledger.ORIGIN_PLAYBOOK,
                    required_tools=frozenset({"get_financial_facts"}),
                    blocking=True,
                )
            ]
        )
        plan = await plan_research(subject="CFR", refiner=refiner)
        added = next(q for q in plan.questions if q.key == "model_idea")
        assert added.origin == ledger.ORIGIN_DIRECTOR
        assert added.blocking is False
        assert plan.refined_by_model is True

    async def test_a_model_cannot_exceed_its_addition_budget(self) -> None:
        refiner = _Refiner(
            additions=[
                PlannedQuestion(
                    key=f"extra{i}",
                    text="x",
                    origin=ledger.ORIGIN_DIRECTOR,
                    required_tools=frozenset({"get_financial_facts"}),
                )
                for i in range(20)
            ]
        )
        plan = await plan_research(subject="CFR", refiner=refiner)
        assert len([q for q in plan.questions if q.key.startswith("extra")]) == 3


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


class TestPersistence:
    async def test_the_plan_lands_in_the_ledger(self, session) -> None:
        run = await ledger.open_run(session, mode="standard")
        plan = await plan_research(subject="CFR")
        counts = await persist_plan(session, run, plan)
        await session.commit()
        assert counts["questions"] == len(plan.questions)
        assert counts["tasks"] == len(plan.tasks)
        summary = await ledger.summarise(session, run)
        assert summary.questions_total == len(plan.questions)
        assert run.status == ledger.RUN_INVESTIGATING

    async def test_an_unassignable_question_becomes_a_gap(self, session) -> None:
        run = await ledger.open_run(session, mode="standard")
        playbook = _Playbook(
            questions=[
                PlannedQuestion(
                    key="web_scan",
                    text="What is the open web saying?",
                    origin=ledger.ORIGIN_PLAYBOOK,
                    required_tools=frozenset({"search_web"}),
                    blocking=True,
                )
            ]
        )
        plan = await plan_research(subject="CFR", playbooks=[playbook])
        await persist_plan(session, run, plan)
        await session.commit()
        gaps = await ledger.open_gaps(session, run)
        gap = next(g for g in gaps if g.question_key == "web_scan")
        assert gap.gap_type == ledger.GAP_TOOL_UNAVAILABLE
        assert gap.closable is False
        # A blocking question nothing can answer stops the Council convening.
        assert gap.blocks_council is True
        assert (await ledger.summarise(session, run)).council_may_convene is False

    async def test_a_dropped_question_becomes_a_closable_gap(self, session) -> None:
        run = await ledger.open_run(session, mode="quick")
        playbook = _Playbook(
            questions=[
                PlannedQuestion(
                    key=f"q{i}",
                    text="x",
                    origin=ledger.ORIGIN_PLAYBOOK,
                    required_tools=frozenset({"get_financial_facts"}),
                    priority=5,
                )
                for i in range(200)
            ]
        )
        plan = await plan_research(subject="CFR", mode="quick", playbooks=[playbook])
        await persist_plan(session, run, plan)
        await session.commit()
        gaps = await ledger.open_gaps(session, run, limit=500)
        budget_gaps = [g for g in gaps if g.gap_type == ledger.GAP_BUDGET_EXHAUSTED]
        assert budget_gaps
        assert all(g.closable for g in budget_gaps)
