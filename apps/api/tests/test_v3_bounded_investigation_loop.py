"""V3.5 Slice 5.3 — the bounded investigation loop.

WHAT MUST BE TRUE OF EVERY RUN
==============================
It terminates, it names the limit that stopped it, and it never calls a run complete
because the agents happened to return. "We stopped because the search budget ran out with
three questions unanswered" is useful; "analysis complete" would be a lie.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.services.director.loop import (
    LIMIT_STOP_REASONS,
    LOOP_STOP_REASONS,
    STOPPED_COMPLETE,
    STOPPED_MAX_ROUNDS,
    STOPPED_MAX_TASKS,
    STOPPED_MAX_TOOL_CALLS,
    STOPPED_MAX_WALL_SECONDS,
    STOPPED_NOTHING_LEFT,
    FindingDraft,
    GapDraft,
    LoopResult,
    TaskOutcome,
    run_investigation,
)
from app.services.director.planner import PlannedQuestion, ResearchPlan, plan_research
from app.services.ledger import store as ledger
from app.services.research_mode import ModeLimits, ResearchMode


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


def _limits(**overrides) -> ModeLimits:
    base = {
        "max_rounds": 2,
        "max_tasks": 8,
        "max_tool_calls": 100,
        "max_web_searches": 10,
        "max_provider_research_runs": 2,
        "max_documents": 10,
        "max_model_calls": 20,
        "max_model_tokens": 100_000,
        "max_wall_seconds": 600.0,
    }
    base.update(overrides)
    return ModeLimits(**base)


def _plan(*keys: str, blocking: set[str] | None = None) -> ResearchPlan:
    from app.services.director.planner import PlannedTask

    blocking = blocking or set()
    plan = ResearchPlan(subject="CFR", mode=ResearchMode.STANDARD, limits=_limits())
    plan.questions = [
        PlannedQuestion(
            key=key,
            text=f"question {key}",
            origin=ledger.ORIGIN_DIRECTOR,
            blocking=key in blocking,
        )
        for key in keys
    ]
    plan.tasks = [PlannedTask(role_id="financial_analyst", question_keys=list(keys))]
    return plan


@dataclass
class _Investigator:
    """A scriptable specialist. Real outcomes, no model and no network."""

    outcomes: list[TaskOutcome] = field(default_factory=list)
    default: TaskOutcome | None = None
    raises_on_round: int | None = None
    calls: list[tuple[str, int, int]] = field(default_factory=list)

    async def investigate(
        self, *, role_id, questions, round_index, remaining_tool_calls
    ):  # noqa: ANN001, ANN201
        self.calls.append((role_id, round_index, remaining_tool_calls))
        if self.raises_on_round == round_index:
            raise RuntimeError("investigator exploded")
        if self.outcomes:
            return self.outcomes.pop(0)
        return self.default or TaskOutcome(
            answered_question_keys=tuple(q.key for q in questions), tool_calls=1
        )


async def _run(session):  # noqa: ANN001
    return await ledger.open_run(session, mode="standard")


class TestTermination:
    async def test_every_stop_reason_is_in_the_closed_vocabulary(self) -> None:
        with pytest.raises(ValueError, match="no 'other'"):
            LoopResult(stopped_by="it seemed done")
        assert LIMIT_STOP_REASONS < LOOP_STOP_REASONS

    async def test_a_run_that_answers_everything_completes(self, session) -> None:
        run = await _run(session)
        plan = _plan("q1", "q2")
        from app.services.director.planner import persist_plan

        await persist_plan(session, run, plan)
        result = await run_investigation(
            session,
            run,
            plan,
            investigator=_Investigator(),
            limits=_limits(),
        )
        assert result.stopped_by == STOPPED_COMPLETE
        assert result.stopped_by_a_limit is False
        assert result.is_complete_analysis is True

    async def test_the_round_budget_stops_the_loop_and_is_named(self, session) -> None:
        run = await _run(session)
        plan = _plan("q1")
        from app.services.director.planner import persist_plan

        await persist_plan(session, run, plan)
        # Never answers, and always opens a closable gap — so there is always more to do.
        never = _Investigator(
            default=TaskOutcome(
                gaps=[
                    GapDraft(
                        gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
                        description="still missing",
                        question_key="q1",
                    )
                ],
                tool_calls=1,
            )
        )
        result = await run_investigation(
            session, run, plan, investigator=never, limits=_limits(max_rounds=2)
        )
        assert result.stopped_by == STOPPED_MAX_ROUNDS
        assert result.stopped_by_a_limit is True
        assert len(result.rounds) == 2
        assert run.status == ledger.RUN_STOPPED
        assert run.stopped_by == STOPPED_MAX_ROUNDS

    async def test_the_task_budget_stops_the_loop_before_the_work(self, session) -> None:
        """Checked BEFORE the work it would authorise. A budget satisfied by noticing
        afterwards is a budget that was exceeded."""
        run = await _run(session)
        plan = _plan("q1")
        from app.services.director.planner import PlannedTask

        plan.tasks = [
            PlannedTask(role_id="financial_analyst", question_keys=["q1"]),
            PlannedTask(role_id="risk_analyst", question_keys=["q1"]),
            PlannedTask(role_id="business_analyst", question_keys=["q1"]),
        ]
        investigator = _Investigator()
        result = await run_investigation(
            session, run, plan, investigator=investigator, limits=_limits(max_tasks=2)
        )
        assert result.stopped_by == STOPPED_MAX_TASKS
        assert len(investigator.calls) == 2

    async def test_the_tool_call_budget_stops_the_loop(self, session) -> None:
        run = await _run(session)
        plan = _plan("q1")
        from app.services.director.planner import PlannedTask

        plan.tasks = [
            PlannedTask(role_id="financial_analyst", question_keys=["q1"]),
            PlannedTask(role_id="risk_analyst", question_keys=["q1"]),
        ]
        greedy = _Investigator(
            default=TaskOutcome(tool_calls=50, answered_question_keys=())
        )
        result = await run_investigation(
            session, run, plan, investigator=greedy, limits=_limits(max_tool_calls=40)
        )
        assert result.stopped_by == STOPPED_MAX_TOOL_CALLS

    async def test_the_wall_clock_stops_the_loop(self, session) -> None:
        run = await _run(session)
        plan = _plan("q1")
        from app.services.director.planner import PlannedTask

        plan.tasks = [
            PlannedTask(role_id="financial_analyst", question_keys=["q1"]),
            PlannedTask(role_id="risk_analyst", question_keys=["q1"]),
        ]
        # The clock is read at: start, round start, then once before each task. The
        # fourth read is the check before the SECOND task, and it is the one that has
        # to fire — a wall bound noticed after the work is a wall bound that was
        # exceeded.
        ticks = iter([0.0, 0.0, 0.0, 999.0])
        result = await run_investigation(
            session,
            run,
            plan,
            investigator=_Investigator(
                default=TaskOutcome(tool_calls=1, answered_question_keys=())
            ),
            limits=_limits(max_wall_seconds=10.0),
            now=lambda: next(ticks, 999.0),
        )
        assert result.stopped_by == STOPPED_MAX_WALL_SECONDS

    async def test_a_run_with_nothing_left_to_do_stops(self, session) -> None:
        run = await _run(session)
        plan = _plan("q1")
        from app.services.director.planner import persist_plan

        await persist_plan(session, run, plan)
        # Opens an UNCLOSABLE gap: another round would fail the same way.
        stuck = _Investigator(
            default=TaskOutcome(
                gaps=[
                    GapDraft(
                        gap_type=ledger.GAP_TRANSCRIPT_UNAVAILABLE,
                        description="the issuer publishes none",
                        question_key="q1",
                        closable=False,
                    )
                ],
                tool_calls=1,
            )
        )
        result = await run_investigation(
            session, run, plan, investigator=stuck, limits=_limits(max_rounds=5)
        )
        assert result.stopped_by == STOPPED_NOTHING_LEFT
        assert len(result.rounds) == 1
        # And it is ACCEPTED, not left open pretending somebody might close it.
        assert result.gaps_accepted == 1


class TestGapsBecomeWork:
    async def test_a_closable_gap_becomes_a_follow_up_task(self, session) -> None:
        """The whole point of the loop: V2 can report a gap and cannot close one."""
        run = await _run(session)
        plan = _plan("q1")
        from app.services.director.planner import persist_plan

        await persist_plan(session, run, plan)
        investigator = _Investigator(
            outcomes=[
                TaskOutcome(
                    gaps=[
                        GapDraft(
                            gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
                            description="the cash-flow statement was not retrieved",
                            question_key="q1",
                        )
                    ],
                    tool_calls=1,
                ),
                TaskOutcome(
                    findings=[
                        FindingDraft(
                            statement="Cash flow from operations was 6,900m DKK.",
                            evidence_ids=("ev:page138",),
                            question_key="q1",
                        )
                    ],
                    answered_question_keys=("q1",),
                    tool_calls=1,
                ),
            ]
        )
        result = await run_investigation(
            session, run, plan, investigator=investigator, limits=_limits(max_rounds=3)
        )
        assert len(result.rounds) == 2
        assert result.findings == 1
        # Round two was a follow-up for the same role, targeting the gap's question.
        assert investigator.calls[1][1] == 1

    async def test_an_unclosable_gap_does_not_buy_another_round(self, session) -> None:
        """It would consume a whole round to fail again."""
        run = await _run(session)
        plan = _plan("q1")
        investigator = _Investigator(
            default=TaskOutcome(
                gaps=[
                    GapDraft(
                        gap_type=ledger.GAP_TRANSCRIPT_UNAVAILABLE,
                        description="none published",
                        question_key="q1",
                        closable=False,
                    )
                ],
                tool_calls=1,
            )
        )
        await run_investigation(
            session, run, plan, investigator=investigator, limits=_limits(max_rounds=5)
        )
        assert len(investigator.calls) == 1


class TestFindingsNeedSupport:
    async def test_an_unsupported_statement_becomes_a_gap_not_a_finding(
        self, session
    ) -> None:
        """An investigator that returns a claim has produced a claim. The run records
        what was asserted and why it was not accepted."""
        run = await _run(session)
        plan = _plan("q1")
        investigator = _Investigator(
            default=TaskOutcome(
                findings=[FindingDraft(statement="Margins will expand.")],
                tool_calls=1,
            )
        )
        result = await run_investigation(
            session, run, plan, investigator=investigator, limits=_limits(max_rounds=1)
        )
        assert result.findings == 0
        gaps = await ledger.open_gaps(session, run)
        assert any("no evidence or calculation" in g.description for g in gaps)

    async def test_a_supported_finding_is_persisted(self, session) -> None:
        run = await _run(session)
        plan = _plan("q1")
        investigator = _Investigator(
            default=TaskOutcome(
                findings=[
                    FindingDraft(
                        statement="Group revenue was 31,338m DKK.",
                        evidence_ids=("ev:abc",),
                        question_key="q1",
                    )
                ],
                answered_question_keys=("q1",),
                tool_calls=1,
            )
        )
        result = await run_investigation(
            session, run, plan, investigator=investigator, limits=_limits()
        )
        assert result.findings == 1


class TestCompletionIsNotAgentsFinishing:
    async def test_a_run_with_an_open_blocking_question_is_not_complete(
        self, session
    ) -> None:
        """Every agent returned successfully. The blocking question is unanswered.
        Calling that a complete analysis is the lie this property prevents."""
        run = await _run(session)
        plan = _plan("q1", "must_answer", blocking={"must_answer"})
        from app.services.director.planner import persist_plan

        await persist_plan(session, run, plan)
        investigator = _Investigator(
            default=TaskOutcome(answered_question_keys=("q1",), tool_calls=1)
        )
        result = await run_investigation(
            session, run, plan, investigator=investigator, limits=_limits(max_rounds=1)
        )
        assert result.council_may_convene is False
        assert result.is_complete_analysis is False
        assert "must_answer" in result.blocking_open_question_keys

    async def test_an_unrecognised_completion_rule_never_declares_success(
        self, session
    ) -> None:
        """A rule the platform cannot evaluate must not declare a run complete by being
        ignored."""
        run = await _run(session)
        plan = _plan("q1")
        from app.services.director.planner import persist_plan

        await persist_plan(session, run, plan)
        result = await run_investigation(
            session,
            run,
            plan,
            investigator=_Investigator(),
            limits=_limits(max_rounds=1),
            completion_rules=["cash_runway_present_or_gap_explained"],
        )
        assert result.stopped_by != STOPPED_COMPLETE

    async def test_a_known_completion_rule_is_honoured(self, session) -> None:
        run = await _run(session)
        plan = _plan("q1", "must_answer", blocking={"must_answer"})
        from app.services.director.planner import persist_plan

        await persist_plan(session, run, plan)
        investigator = _Investigator(
            default=TaskOutcome(
                answered_question_keys=("q1", "must_answer"), tool_calls=1
            )
        )
        result = await run_investigation(
            session,
            run,
            plan,
            investigator=investigator,
            limits=_limits(max_rounds=3),
            completion_rules=["all_blocking_questions_answered"],
        )
        assert result.stopped_by == STOPPED_COMPLETE
        assert result.council_may_convene is True


class TestResilience:
    async def test_one_role_exploding_does_not_end_the_run(self, session) -> None:
        run = await _run(session)
        plan = _plan("q1")
        from app.services.director.planner import PlannedTask

        plan.tasks = [
            PlannedTask(role_id="financial_analyst", question_keys=["q1"]),
            PlannedTask(role_id="risk_analyst", question_keys=["q1"]),
        ]
        investigator = _Investigator(raises_on_round=0)
        result = await run_investigation(
            session, run, plan, investigator=investigator, limits=_limits(max_rounds=1)
        )
        # Both tasks were attempted; neither took the run with it.
        assert result.tasks_run == 2
        assert result.stopped_by in LOOP_STOP_REASONS

    async def test_a_partial_task_is_recorded_with_its_reason(self, session) -> None:
        run = await _run(session)
        plan = _plan("q1")
        investigator = _Investigator(
            default=TaskOutcome(
                stopped_by="tool_budget", detail="ran out mid-question", tool_calls=3
            )
        )
        await run_investigation(
            session, run, plan, investigator=investigator, limits=_limits(max_rounds=1)
        )
        await session.commit()
        from sqlalchemy import select

        from app.models.ledger import ResearchTask

        tasks = (
            await session.execute(
                select(ResearchTask).where(ResearchTask.research_run_id == run.id)
            )
        ).scalars().all()
        assert any(t.status == ledger.TASK_PARTIAL and t.stopped_by for t in tasks)

    async def test_the_result_serialises_what_was_still_open(self, session) -> None:
        run = await _run(session)
        plan = _plan("q1", "q2")
        from app.services.director.planner import persist_plan

        await persist_plan(session, run, plan)
        result = await run_investigation(
            session,
            run,
            plan,
            investigator=_Investigator(
                default=TaskOutcome(answered_question_keys=("q1",), tool_calls=1)
            ),
            limits=_limits(max_rounds=1),
        )
        payload = result.to_dict()
        assert "q2" in payload["open_question_keys"]
        assert payload["stopped_by"] in LOOP_STOP_REASONS
        assert set(payload) >= {"is_complete_analysis", "council_may_convene", "rounds"}


class TestEndToEnd:
    async def test_a_real_plan_runs_through_the_loop(self, session) -> None:
        """The Director's own plan, not a hand-built one."""
        run = await _run(session)
        plan = await plan_research(subject="CFR", mode="quick")
        from app.services.director.planner import persist_plan

        await persist_plan(session, run, plan)
        result = await run_investigation(
            session,
            run,
            plan,
            investigator=_Investigator(),
            limits=plan.limits,
        )
        assert result.tasks_run == len(plan.tasks)
        assert result.stopped_by in LOOP_STOP_REASONS
        summary = await ledger.summarise(session, run)
        assert summary.questions_open == 0
