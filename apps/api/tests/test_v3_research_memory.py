"""V3.8 Slice 8.1 — research memory.

MEMORY IS THE PREVIOUS RESEARCH, NOT A COPY OF IT
=================================================
There is no snapshot table, and these tests are largely about the consequences of that
choice being the right one — plus the three rules the architecture states: new evidence
outranks memory, memory is never a citation source, and a gap is what carries forward.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.company import Company
from app.services.ledger import store as ledger
from app.services.memory.store import (
    MAX_RECALLED_FINDINGS,
    STALE_AFTER_DAYS,
    previous_run,
    recall,
)

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


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


async def _company(session) -> Company:  # noqa: ANN001
    company = Company(
        id=uuid.uuid4(), ticker="CFR", exchange="SW", name="Richemont", status="new"
    )
    session.add(company)
    await session.flush()
    return company


async def _completed_run(session, company, *, started: datetime, **overrides):  # noqa: ANN001
    run = await ledger.open_run(
        session, mode="standard", company_id=company.id, now=started, **overrides
    )
    return run


class TestFindingThePriorRun:
    async def test_no_prior_research_is_a_state_not_an_error(self, session) -> None:
        company = await _company(session)
        await session.commit()
        prior = await recall(session, company_id=company.id, now=NOW)
        assert prior.exists is False
        assert prior.findings == ()

    async def test_a_run_that_never_investigated_is_not_memory(self, session) -> None:
        """Returning it would make "there is no prior research" indistinguishable from
        "the last attempt did not start"."""
        company = await _company(session)
        await _completed_run(session, company, started=NOW - timedelta(days=30))
        await session.commit()
        assert await previous_run(session, company_id=company.id) is None

    async def test_the_most_recent_qualifying_run_wins(self, session) -> None:
        company = await _company(session)
        old = await _completed_run(session, company, started=NOW - timedelta(days=200))
        await ledger.close_run(session, old, status=ledger.RUN_COMPLETE)
        new = await _completed_run(session, company, started=NOW - timedelta(days=10))
        await ledger.close_run(session, new, status=ledger.RUN_COMPLETE)
        await session.commit()
        assert (await previous_run(session, company_id=company.id)).id == new.id

    async def test_a_stopped_run_still_counts_as_memory(self, session) -> None:
        """It stopped on a budget and still learned things. Discarding it would throw
        away the work the budget paid for."""
        company = await _company(session)
        run = await _completed_run(session, company, started=NOW - timedelta(days=5))
        await ledger.close_run(
            session, run, status=ledger.RUN_STOPPED, stopped_by="max_rounds"
        )
        await session.commit()
        prior = await recall(session, company_id=company.id, now=NOW)
        assert prior.exists
        assert prior.stopped_by == "max_rounds"

    async def test_before_lets_a_replay_see_only_what_existed_then(
        self, session
    ) -> None:
        company = await _company(session)
        old = await _completed_run(session, company, started=NOW - timedelta(days=200))
        await ledger.close_run(session, old, status=ledger.RUN_COMPLETE)
        new = await _completed_run(session, company, started=NOW - timedelta(days=1))
        await ledger.close_run(session, new, status=ledger.RUN_COMPLETE)
        await session.commit()
        seen = await previous_run(
            session, company_id=company.id, before=NOW - timedelta(days=100)
        )
        assert seen.id == old.id


class TestMemoryIsLabelled:
    async def test_a_recalled_finding_names_the_run_it_came_from(
        self, session
    ) -> None:
        """Never merged into a new run's findings: merging would make the provenance a
        matter of reading the statement text."""
        company = await _company(session)
        run = await _completed_run(session, company, started=NOW - timedelta(days=10))
        await ledger.record_finding(
            session, run, statement="Margins expanded.", evidence_ids=["ev:a"]
        )
        await ledger.close_run(session, run, status=ledger.RUN_COMPLETE)
        await session.commit()
        prior = await recall(session, company_id=company.id, now=NOW)
        assert prior.findings[0].research_run_id == run.id
        assert prior.findings[0].to_dict()["kind"] == "prior_research_memory"

    async def test_age_is_reported_and_old_findings_are_marked_stale(
        self, session
    ) -> None:
        """Age is a property a reader weighs, not a reason to hide something — but a run
        that treats a two-year-old conclusion as current has confused memory with
        evidence."""
        company = await _company(session)
        run = await _completed_run(session, company, started=NOW - timedelta(days=900))
        finding = await ledger.record_finding(
            session, run, statement="Old conclusion.", evidence_ids=["ev:a"]
        )
        finding.created_at = NOW - timedelta(days=STALE_AFTER_DAYS + 50)
        await ledger.close_run(session, run, status=ledger.RUN_COMPLETE)
        await session.commit()
        prior = await recall(session, company_id=company.id, now=NOW)
        assert prior.findings[0].is_stale is True
        assert prior.stale_findings

    async def test_a_withdrawn_finding_does_not_return_through_memory(
        self, session
    ) -> None:
        """One the Red Team retired must not come back a quarter later wearing the
        authority of memory. Memory is the path it would otherwise return by."""
        company = await _company(session)
        run = await _completed_run(session, company, started=NOW - timedelta(days=10))
        await ledger.record_finding(
            session, run, statement="kept", evidence_ids=["ev:a"]
        )
        await ledger.record_finding(
            session,
            run,
            statement="retired",
            evidence_ids=["ev:b"],
            verification_status="withdrawn",
        )
        await ledger.close_run(session, run, status=ledger.RUN_COMPLETE)
        await session.commit()
        prior = await recall(session, company_id=company.id, now=NOW)
        assert [f.statement for f in prior.findings] == ["kept"]


class TestMemoryIsNotACitationSource:
    async def test_evidence_that_no_longer_resolves_is_reported(self, session) -> None:
        """If the underlying evidence has gone, the memory goes with it — so `recall`
        says which ids no longer resolve rather than quietly returning a finding whose
        support is missing."""
        company = await _company(session)
        run = await _completed_run(session, company, started=NOW - timedelta(days=10))
        await ledger.record_finding(
            session, run, statement="A", evidence_ids=["ev:alive", "ev:gone"]
        )
        await ledger.close_run(session, run, status=ledger.RUN_COMPLETE)
        await session.commit()
        prior = await recall(
            session,
            company_id=company.id,
            resolvable_evidence_ids=["ev:alive"],
            now=NOW,
        )
        assert prior.stale_evidence_ids == ("ev:gone",)

    async def test_no_staleness_is_claimed_when_none_was_checked(
        self, session
    ) -> None:
        """"Does this chunk still exist" is a corpus question, and memory must not become
        a second implementation of it."""
        company = await _company(session)
        run = await _completed_run(session, company, started=NOW - timedelta(days=10))
        await ledger.record_finding(
            session, run, statement="A", evidence_ids=["ev:whatever"]
        )
        await ledger.close_run(session, run, status=ledger.RUN_COMPLETE)
        await session.commit()
        prior = await recall(session, company_id=company.id, now=NOW)
        assert prior.stale_evidence_ids == ()


class TestGapsCarryForward:
    async def test_an_open_gap_becomes_the_next_runs_question(self, session) -> None:
        """The single most useful thing memory does: the difference between a refresh
        that re-derives everything and one that starts where the last stopped."""
        company = await _company(session)
        run = await _completed_run(session, company, started=NOW - timedelta(days=10))
        await ledger.record_gap(
            session,
            run,
            gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
            description="The cash-flow statement was never retrieved.",
            question_key="cash_flow",
        )
        await ledger.close_run(session, run, status=ledger.RUN_COMPLETE)
        await session.commit()
        prior = await recall(session, company_id=company.id, now=NOW)
        assert prior.carry_forward_questions == (
            ("cash_flow", "The cash-flow statement was never retrieved."),
        )

    async def test_an_unclosable_gap_is_remembered_but_not_re_asked(
        self, session
    ) -> None:
        """A gap no source can close would be re-asked every run forever, which is a
        memory that has learned nothing."""
        company = await _company(session)
        run = await _completed_run(session, company, started=NOW - timedelta(days=10))
        gap = await ledger.record_gap(
            session,
            run,
            gap_type=ledger.GAP_TRANSCRIPT_UNAVAILABLE,
            description="The issuer publishes no transcript.",
            closable=False,
        )
        await ledger.accept_gap(session, gap)
        await ledger.close_run(session, run, status=ledger.RUN_COMPLETE)
        await session.commit()
        prior = await recall(session, company_id=company.id, now=NOW)
        assert len(prior.open_gaps) == 1
        assert prior.carry_forward_questions == ()

    async def test_a_closed_gap_is_not_remembered_as_open(self, session) -> None:
        company = await _company(session)
        run = await _completed_run(session, company, started=NOW - timedelta(days=10))
        gap = await ledger.record_gap(
            session,
            run,
            gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
            description="was missing",
        )
        finding = await ledger.record_finding(
            session, run, statement="found it", evidence_ids=["ev:a"]
        )
        await ledger.close_gap(session, gap, finding=finding)
        await ledger.close_run(session, run, status=ledger.RUN_COMPLETE)
        await session.commit()
        prior = await recall(session, company_id=company.id, now=NOW)
        assert prior.open_gaps == ()

    async def test_the_carry_forward_shape_is_what_the_director_consumes(
        self, session
    ) -> None:
        """5.2's `prior_open_gaps` takes `(question_key, text)` and stamps
        `origin=prior_gap`."""
        from app.services.director.planner import plan_research

        company = await _company(session)
        run = await _completed_run(session, company, started=NOW - timedelta(days=10))
        await ledger.record_gap(
            session,
            run,
            gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
            description="still missing",
            question_key="cash_flow",
        )
        await ledger.close_run(session, run, status=ledger.RUN_COMPLETE)
        await session.commit()
        prior = await recall(session, company_id=company.id, now=NOW)
        plan = await plan_research(
            subject="CFR", prior_open_gaps=prior.carry_forward_questions
        )
        question = next(q for q in plan.questions if q.key == "cash_flow")
        assert question.origin == ledger.ORIGIN_PRIOR_GAP


class TestBounds:
    async def test_recall_is_bounded_and_says_when_it_truncated(
        self, session
    ) -> None:
        """A new run handed nine hundred remembered statements is a run reading a corpus
        of its own past."""
        company = await _company(session)
        run = await _completed_run(session, company, started=NOW - timedelta(days=10))
        for i in range(MAX_RECALLED_FINDINGS + 3):
            await ledger.record_finding(
                session, run, statement=f"finding {i}", evidence_ids=["ev:a"]
            )
        await ledger.close_run(session, run, status=ledger.RUN_COMPLETE)
        await session.commit()
        prior = await recall(session, company_id=company.id, now=NOW)
        assert len(prior.findings) == MAX_RECALLED_FINDINGS
        assert prior.truncated["findings"] >= 1

    async def test_the_summary_serialises_what_a_reader_needs(self, session) -> None:
        company = await _company(session)
        run = await _completed_run(session, company, started=NOW - timedelta(days=10))
        await ledger.close_run(session, run, status=ledger.RUN_COMPLETE)
        await session.commit()
        payload = (await recall(session, company_id=company.id, now=NOW)).to_dict()
        assert set(payload) >= {
            "exists",
            "run_id",
            "stale_finding_count",
            "carry_forward_question_count",
            "unresolved_disagreement_count",
            "stale_evidence_ids",
        }


class TestNoSnapshotTable:
    def test_memory_reads_the_ledger_rather_than_a_copy_of_it(self) -> None:
        """A copy would be a second source of truth that drifts — and drift here is
        uniquely bad, because the copy is what a later run would trust while the
        original is what a citation resolves against."""
        from app.db.base import Base

        assert "research_memory" not in Base.metadata.tables
        assert "research_memory_snapshots" not in Base.metadata.tables
