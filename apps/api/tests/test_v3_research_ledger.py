"""V3.5 Slice 5.1 — the Research Ledger.

THE RULE THESE TESTS EXIST FOR
==============================
> **A finding without evidence ids or calculation ids is not a finding.**

Everything else in the ledger follows from taking that seriously: a stopped run names its
limit, a closed gap names the finding that closed it, a resolved disagreement says how.
Each of those is a sentence the schema refuses to store.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.ledger import ResearchFinding
from app.services.ledger import store as ledger


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


async def _run(session, **overrides):  # noqa: ANN001
    return await ledger.open_run(session, mode=overrides.pop("mode", "standard"), **overrides)


async def _finding(session, run, **overrides):  # noqa: ANN001
    base = {"statement": "Group revenue was 31,338m DKK.", "evidence_ids": ["ev:abc"]}
    base.update(overrides)
    return await ledger.record_finding(session, run, **base)


class TestFindingsNeedSupport:
    async def test_a_finding_with_neither_is_refused(self, session) -> None:
        run = await _run(session)
        with pytest.raises(ledger.UnsupportedFindingError, match="no state for one"):
            await ledger.record_finding(
                session, run, statement="Margins will expand."
            )

    async def test_one_evidence_id_is_enough(self, session) -> None:
        run = await _run(session)
        finding = await _finding(session, run)
        assert finding.evidence_count == 1
        assert finding.calculation_count == 0

    async def test_a_calculation_id_alone_is_enough(self, session) -> None:
        run = await _run(session)
        finding = await ledger.record_finding(
            session,
            run,
            statement="Operating margin was 24.1%.",
            calculation_ids=["calc:1"],
        )
        assert finding.calculation_count == 1

    async def test_blank_ids_do_not_count_as_support(self, session) -> None:
        """A list of empty strings is a list with nothing in it."""
        run = await _run(session)
        with pytest.raises(ledger.UnsupportedFindingError):
            await ledger.record_finding(
                session, run, statement="x", evidence_ids=["", "   "]
            )

    async def test_the_counts_are_derived_and_cannot_drift(self, session) -> None:
        run = await _run(session)
        finding = await _finding(
            session, run, evidence_ids=["ev:a", "ev:b", "ev:a "], calculation_ids=["c1"]
        )
        assert finding.evidence_count == len(finding.evidence_ids_json)
        assert finding.calculation_count == len(finding.calculation_ids_json)

    async def test_the_database_refuses_it_too(self, session) -> None:
        """The service refusal gives a caller a message; the database refusal survives
        a future writer that bypasses the service. The second is the load-bearing half.
        """
        run = await _run(session)
        session.add(
            ResearchFinding(
                id=uuid.uuid4(),
                research_run_id=run.id,
                statement="Margins will expand.",
                evidence_count=0,
                calculation_count=0,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async def test_a_confidence_outside_zero_to_one_is_refused(self, session) -> None:
        run = await _run(session)
        with pytest.raises(ValueError, match="probability"):
            await _finding(session, run, confidence=1.4)

    async def test_a_direction_outside_the_vocabulary_is_refused(self, session) -> None:
        run = await _run(session)
        with pytest.raises(ValueError, match="not a finding direction"):
            await _finding(session, run, direction="bullish")


class TestRunsNameTheirLimit:
    async def test_a_stopped_run_must_name_what_stopped_it(self, session) -> None:
        """"We stopped because the search budget ran out with three questions
        unanswered" is useful; "analysis complete" would be a lie."""
        run = await _run(session)
        with pytest.raises(ValueError, match="widen a budget"):
            await ledger.close_run(session, run, status=ledger.RUN_STOPPED)

    async def test_a_stopped_run_that_names_its_limit_is_stored(self, session) -> None:
        run = await _run(session)
        await ledger.close_run(
            session, run, status=ledger.RUN_STOPPED, stopped_by="max_web_searches"
        )
        await session.commit()
        assert run.stopped_by == "max_web_searches"
        assert run.completed_at is not None

    async def test_a_complete_run_needs_no_limit(self, session) -> None:
        run = await _run(session)
        await ledger.close_run(session, run, status=ledger.RUN_COMPLETE)
        assert run.stopped_by is None


class TestGaps:
    async def test_a_gap_type_invented_at_a_call_site_is_refused(self, session) -> None:
        run = await _run(session)
        with pytest.raises(ValueError, match="keep hitting"):
            await ledger.record_gap(
                session, run, gap_type="felt_thin", description="x"
            )

    async def test_closing_a_gap_names_the_finding_that_closed_it(
        self, session
    ) -> None:
        """"Closed" with nothing behind it is the gap disappearing rather than being
        answered."""
        run = await _run(session)
        gap = await ledger.record_gap(
            session,
            run,
            gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
            description="No cash-flow statement retrieved.",
        )
        finding = await _finding(session, run)
        await ledger.close_gap(session, gap, finding=finding)
        await session.commit()
        assert gap.status == ledger.GAP_CLOSED
        assert gap.closed_by_finding_id == finding.id

    async def test_an_unclosable_gap_can_be_accepted(self, session) -> None:
        """The honest outcome for a gap no source can close."""
        run = await _run(session)
        gap = await ledger.record_gap(
            session,
            run,
            gap_type=ledger.GAP_TRANSCRIPT_UNAVAILABLE,
            description="CFR published no FY2025 transcript.",
            closable=False,
        )
        await ledger.accept_gap(session, gap)
        await session.commit()
        assert gap.status == ledger.GAP_ACCEPTED

    async def test_open_gaps_can_exclude_the_unclosable_ones(self, session) -> None:
        """What stops a bounded loop burning a round on the one thing no source has."""
        run = await _run(session)
        await ledger.record_gap(
            session,
            run,
            gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
            description="closable",
            closable=True,
        )
        await ledger.record_gap(
            session,
            run,
            gap_type=ledger.GAP_TRANSCRIPT_UNAVAILABLE,
            description="not closable",
            closable=False,
        )
        await session.commit()
        assert len(await ledger.open_gaps(session, run)) == 2
        closable = await ledger.open_gaps(session, run, closable_only=True)
        assert [g.description for g in closable] == ["closable"]


class TestDisagreements:
    async def test_a_finding_cannot_disagree_with_itself(self, session) -> None:
        run = await _run(session)
        finding = await _finding(session, run)
        with pytest.raises(ValueError, match="with itself"):
            await ledger.record_disagreement(
                session, run, finding_a=finding, finding_b=finding, nature="value"
            )

    async def test_a_resolved_disagreement_must_say_how(self, session) -> None:
        """Otherwise "resolved" is the conflict being closed rather than answered,
        which is exactly the laundering the Chair rule forbids."""
        run = await _run(session)
        a = await _finding(session, run, statement="Revenue was 31,338m.")
        b = await _finding(session, run, statement="Revenue was 30,000m.")
        with pytest.raises(ValueError, match="laundering"):
            await ledger.record_disagreement(
                session,
                run,
                finding_a=a,
                finding_b=b,
                nature="value",
                resolution=ledger.RESOLVED,
            )

    async def test_an_unresolved_disagreement_is_a_valid_output(self, session) -> None:
        """Not a failure of the run — one of its more valuable outputs."""
        run = await _run(session)
        a = await _finding(session, run, statement="Revenue was 31,338m.")
        b = await _finding(session, run, statement="Revenue was 30,000m.")
        row = await ledger.record_disagreement(
            session, run, finding_a=a, finding_b=b, nature="value"
        )
        await session.commit()
        assert row.resolution == ledger.UNRESOLVED
        assert row.resolution_note is None


class TestTasks:
    async def test_a_task_with_no_iteration_cap_is_refused(self, session) -> None:
        run = await _run(session)
        with pytest.raises(ValueError, match="need never stop"):
            await ledger.add_task(
                session, run, role="financial_analyst", max_iterations=0
            )

    async def test_a_partial_task_must_name_what_stopped_it(self, session) -> None:
        """Reporting a thin answer as complete is what `partial` exists to prevent, and
        a partial with no reason is the same failure one step later."""
        run = await _run(session)
        task = await ledger.add_task(session, run, role="financial_analyst")
        with pytest.raises(ValueError, match="one step later"):
            await ledger.finish_task(session, task, status=ledger.TASK_PARTIAL)

    async def test_a_complete_task_needs_no_reason(self, session) -> None:
        run = await _run(session)
        task = await ledger.add_task(session, run, role="risk_analyst")
        await ledger.finish_task(session, task, status=ledger.TASK_COMPLETE)
        await session.commit()
        assert task.completed_at is not None


class TestSummary:
    async def test_a_blocking_question_stops_the_council_convening(
        self, session
    ) -> None:
        """The run reports insufficient evidence rather than analysing around the
        hole, which is the whole point of a `blocking` flag existing."""
        run = await _run(session)
        await ledger.add_question(
            session,
            run,
            question_key="cash_runway",
            text="How many quarters of runway remain?",
            origin=ledger.ORIGIN_PLAYBOOK,
            blocking=True,
        )
        await session.commit()
        summary = await ledger.summarise(session, run)
        assert summary.questions_blocking_open == 1
        assert summary.council_may_convene is False

    async def test_a_gap_that_blocks_the_council_does_the_same(self, session) -> None:
        run = await _run(session)
        await ledger.record_gap(
            session,
            run,
            gap_type=ledger.GAP_ENTITY_AMBIGUOUS,
            description="Two candidate issuers.",
            blocks_council=True,
        )
        await session.commit()
        assert (await ledger.summarise(session, run)).council_may_convene is False

    async def test_an_unblocked_run_may_convene(self, session) -> None:
        run = await _run(session)
        await ledger.add_question(
            session,
            run,
            question_key="q1",
            text="What is revenue?",
            origin=ledger.ORIGIN_DIRECTOR,
        )
        await _finding(session, run)
        await session.commit()
        summary = await ledger.summarise(session, run)
        assert summary.council_may_convene is True
        assert summary.findings_total == 1

    async def test_the_summary_serialises_every_population_it_counts(
        self, session
    ) -> None:
        run = await _run(session)
        payload = (await ledger.summarise(session, run)).to_dict()
        assert set(payload) >= {
            "questions",
            "findings",
            "gaps",
            "disagreements_unresolved",
            "council_may_convene",
            "stopped_by",
        }


class TestVocabularies:
    def test_every_vocabulary_is_closed(self) -> None:
        assert len(ledger.GAP_TYPES) == 10
        assert len(ledger.RUN_STATUSES) == 5
        assert len(ledger.TASK_STATUSES) == 6
        assert len(ledger.QUESTION_ORIGINS) == 4
        assert len(ledger.RESOLUTIONS) == 3

    async def test_an_unknown_question_origin_is_refused(self, session) -> None:
        run = await _run(session)
        with pytest.raises(ValueError, match="not a question origin"):
            await ledger.add_question(
                session, run, question_key="q", text="t", origin="hunch"
            )
