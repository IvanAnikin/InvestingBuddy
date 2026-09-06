"""V3.8 Slice 8.2 — the ResearchDelta.

THE QUESTION IT ANSWERS
=======================
> **What changed since the previous analysis, and which prior conclusions should be
> revisited?**

The hard part is not computing the difference. It is refusing to report a difference that
is not one — a rephrasing, a narrower run, a question nobody asked — because each of those
would make a routine refresh look like a reversal.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.company import Company
from app.models.delta import ResearchDelta
from app.services.ledger import store as ledger
from app.services.memory.delta import (
    compute_delta,
    latest_delta,
    persist_delta,
    slot_of,
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


async def _run(session, company, *, started: datetime):  # noqa: ANN001
    return await ledger.open_run(
        session, mode="standard", company_id=company.id, now=started
    )


async def _finding(session, run, **overrides):  # noqa: ANN001
    base = {
        "statement": "Group revenue was 31,338m DKK.",
        "evidence_ids": ["ev:old"],
        "question_key": "revenue",
        "period_key": "2025",
        "scope_key": "group",
    }
    base.update(overrides)
    return await ledger.record_finding(session, run, **base)


class TestFirstRun:
    async def test_a_first_run_is_not_unchanged(self, session) -> None:
        """It has no thesis to be unchanged from, and saying "nothing changed" would be
        the most misleading possible summary of it."""
        company = await _company(session)
        run = await _run(session, company, started=NOW)
        await _finding(session, run)
        await session.commit()
        delta = await compute_delta(session, to_run=run, from_run=None)
        assert delta.is_first_run is True
        assert delta.unchanged_core_thesis is False
        assert delta.new_evidence_ids == ["ev:old"]

    async def test_a_first_run_delta_can_be_persisted(self, session) -> None:
        company = await _company(session)
        run = await _run(session, company, started=NOW)
        await _finding(session, run)
        await session.commit()
        row = await persist_delta(
            session, await compute_delta(session, to_run=run), now=NOW
        )
        await session.commit()
        assert row.from_run_id is None
        assert row.to_run_id == run.id

    async def test_a_delta_naming_no_target_run_is_refused(self, session) -> None:
        from app.services.memory.delta import DeltaResult

        with pytest.raises(ValueError, match="cannot name"):
            await persist_delta(session, DeltaResult())


class TestWhatCounts:
    async def test_new_evidence_is_what_the_old_run_did_not_cite(
        self, session
    ) -> None:
        company = await _company(session)
        old = await _run(session, company, started=NOW - timedelta(days=90))
        await _finding(session, old, evidence_ids=["ev:a"])
        new = await _run(session, company, started=NOW)
        await _finding(session, new, evidence_ids=["ev:a", "ev:b"])
        await session.commit()
        delta = await compute_delta(session, to_run=new, from_run=old)
        assert delta.new_evidence_ids == ["ev:b"]

    async def test_a_rephrasing_is_not_a_changed_fact(self, session) -> None:
        """Two runs phrasing one conclusion differently are not a change, and calling it
        one would make every re-run look like a restatement."""
        company = await _company(session)
        old = await _run(session, company, started=NOW - timedelta(days=90))
        await _finding(session, old, statement="Group revenue was 31,338m DKK.")
        new = await _run(session, company, started=NOW)
        await _finding(
            session,
            new,
            statement="Revenue for the Group came to 31,338m DKK.",
            evidence_ids=["ev:old"],
        )
        await session.commit()
        delta = await compute_delta(session, to_run=new, from_run=old)
        assert delta.changed_facts == []
        assert delta.invalidated_finding_ids == []

    async def test_a_different_number_on_new_evidence_is_a_changed_fact(
        self, session
    ) -> None:
        company = await _company(session)
        old = await _run(session, company, started=NOW - timedelta(days=90))
        prior = await _finding(
            session, old, statement="Group revenue was 30,000m DKK.", evidence_ids=["ev:a"]
        )
        new = await _run(session, company, started=NOW)
        await _finding(
            session,
            new,
            statement="Group revenue was 31,338m DKK.",
            evidence_ids=["ev:b"],
        )
        await session.commit()
        delta = await compute_delta(session, to_run=new, from_run=old)
        assert len(delta.changed_facts) == 1
        assert delta.invalidated_finding_ids == [str(prior.id)]
        assert delta.unchanged_core_thesis is False

    async def test_a_different_period_is_a_different_slot_not_a_change(
        self, session
    ) -> None:
        """FY2026's number is not a revision of FY2025's."""
        company = await _company(session)
        old = await _run(session, company, started=NOW - timedelta(days=400))
        await _finding(session, old, period_key="2025", statement="31,338m")
        new = await _run(session, company, started=NOW)
        await _finding(
            session, new, period_key="2026", statement="33,000m", evidence_ids=["ev:b"]
        )
        await session.commit()
        delta = await compute_delta(session, to_run=new, from_run=old)
        assert delta.changed_facts == []
        assert delta.invalidated_finding_ids == []

    async def test_a_segment_figure_does_not_invalidate_a_group_one(
        self, session
    ) -> None:
        """The CFR failure mode, as a delta rule."""
        company = await _company(session)
        old = await _run(session, company, started=NOW - timedelta(days=90))
        await _finding(session, old, scope_key="group", statement="21,400m")
        new = await _run(session, company, started=NOW)
        await _finding(
            session,
            new,
            scope_key="segment:specialist watchmakers",
            statement="107m",
            evidence_ids=["ev:b"],
        )
        await session.commit()
        delta = await compute_delta(session, to_run=new, from_run=old)
        assert delta.invalidated_finding_ids == []

    def test_the_slot_is_question_period_and_scope_not_the_text(self) -> None:
        from app.models.ledger import ResearchFinding

        a = ResearchFinding(
            statement="one wording", question_key="revenue", period_key="2025",
            scope_key="group", evidence_count=1, calculation_count=0,
        )
        b = ResearchFinding(
            statement="quite another", question_key="revenue", period_key="2025",
            scope_key="group", evidence_count=1, calculation_count=0,
        )
        assert slot_of(a) == slot_of(b)


class TestSilenceIsNotRefutation:
    async def test_a_question_the_new_run_never_asked_is_not_invalidated(
        self, session
    ) -> None:
        """A run that ran out of budget before reaching a question has not disproved last
        quarter's answer. Treating silence as refutation would make every truncated run
        look like a reversal."""
        company = await _company(session)
        old = await _run(session, company, started=NOW - timedelta(days=90))
        await _finding(session, old, question_key="leverage", statement="2.1x")
        new = await _run(session, company, started=NOW)
        await _finding(session, new, question_key="revenue", evidence_ids=["ev:b"])
        await session.commit()
        delta = await compute_delta(session, to_run=new, from_run=old)
        assert delta.invalidated_finding_ids == []
        assert delta.unchanged_core_thesis is True

    async def test_a_question_the_new_run_never_asked_is_not_resolved(
        self, session
    ) -> None:
        """Silence is not an answer, and the distinction stops a narrower run from
        reading as progress."""
        company = await _company(session)
        old = await _run(session, company, started=NOW - timedelta(days=90))
        await ledger.add_question(
            session, old, question_key="leverage", text="?", origin=ledger.ORIGIN_DIRECTOR
        )
        new = await _run(session, company, started=NOW)
        await session.commit()
        delta = await compute_delta(session, to_run=new, from_run=old)
        assert delta.resolved_question_keys == []

    async def test_a_previously_open_question_now_answered_is_resolved(
        self, session
    ) -> None:
        company = await _company(session)
        old = await _run(session, company, started=NOW - timedelta(days=90))
        await ledger.add_question(
            session, old, question_key="leverage", text="?", origin=ledger.ORIGIN_DIRECTOR
        )
        new = await _run(session, company, started=NOW)
        question = await ledger.add_question(
            session, new, question_key="leverage", text="?", origin=ledger.ORIGIN_PRIOR_GAP
        )
        question.resolution_status = ledger.QUESTION_ANSWERED
        await session.commit()
        delta = await compute_delta(session, to_run=new, from_run=old)
        assert delta.resolved_question_keys == ["leverage"]


class TestGaps:
    async def test_a_gap_raised_again_is_persistence_not_churn(self, session) -> None:
        """Counting it as both closed and new would report churn where there is
        persistence."""
        company = await _company(session)
        old = await _run(session, company, started=NOW - timedelta(days=90))
        await ledger.record_gap(
            session,
            old,
            gap_type=ledger.GAP_TRANSCRIPT_UNAVAILABLE,
            description="none published",
            question_key="transcript",
        )
        new = await _run(session, company, started=NOW)
        await ledger.record_gap(
            session,
            new,
            gap_type=ledger.GAP_TRANSCRIPT_UNAVAILABLE,
            description="still none published",
            question_key="transcript",
        )
        await session.commit()
        delta = await compute_delta(session, to_run=new, from_run=old)
        assert delta.new_gap_ids == []
        assert delta.closed_gap_ids == []

    async def test_a_gap_the_new_run_no_longer_has_is_closed(self, session) -> None:
        company = await _company(session)
        old = await _run(session, company, started=NOW - timedelta(days=90))
        await ledger.record_gap(
            session,
            old,
            gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
            description="cash flow missing",
            question_key="cash_flow",
        )
        new = await _run(session, company, started=NOW)
        await session.commit()
        delta = await compute_delta(session, to_run=new, from_run=old)
        assert len(delta.closed_gap_ids) == 1

    async def test_a_genuinely_new_gap_is_reported(self, session) -> None:
        company = await _company(session)
        old = await _run(session, company, started=NOW - timedelta(days=90))
        new = await _run(session, company, started=NOW)
        await ledger.record_gap(
            session,
            new,
            gap_type=ledger.GAP_CONFLICTING_SOURCES,
            description="two figures disagree",
            question_key="revenue",
        )
        await session.commit()
        delta = await compute_delta(session, to_run=new, from_run=old)
        assert len(delta.new_gap_ids) == 1


class TestPersistence:
    async def test_the_prior_finding_is_not_modified(self, session) -> None:
        """It was a correct conclusion from the evidence available then. Rewriting it
        would make the earlier run's record a lie in service of the later one."""
        company = await _company(session)
        old = await _run(session, company, started=NOW - timedelta(days=90))
        prior = await _finding(session, old, statement="30,000m", evidence_ids=["ev:a"])
        new = await _run(session, company, started=NOW)
        await _finding(session, new, statement="31,338m", evidence_ids=["ev:b"])
        await session.commit()
        delta = await compute_delta(session, to_run=new, from_run=old)
        await persist_delta(session, delta, now=NOW)
        await session.commit()
        await session.refresh(prior)
        assert prior.statement == "30,000m"
        assert prior.verification_status != "withdrawn"

    async def test_an_unchanged_thesis_with_invalidations_is_unstorable(
        self, session
    ) -> None:
        """A conclusion the new evidence undermines IS a change to the thesis."""
        company = await _company(session)
        run = await _run(session, company, started=NOW)
        await session.commit()
        session.add(
            ResearchDelta(
                id=uuid.uuid4(),
                company_id=company.id,
                to_run_id=run.id,
                unchanged_core_thesis=True,
                invalidated_finding_count=2,
                computed_at=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async def test_the_counts_match_the_lists(self, session) -> None:
        company = await _company(session)
        old = await _run(session, company, started=NOW - timedelta(days=90))
        await _finding(session, old, statement="30,000m", evidence_ids=["ev:a"])
        new = await _run(session, company, started=NOW)
        await _finding(session, new, statement="31,338m", evidence_ids=["ev:b"])
        await session.commit()
        row = await persist_delta(
            session, await compute_delta(session, to_run=new, from_run=old), now=NOW
        )
        await session.commit()
        assert row.invalidated_finding_count == len(row.invalidated_finding_ids_json)
        assert row.new_evidence_count == len(row.new_evidence_ids_json)

    async def test_the_latest_delta_is_retrievable_by_entity(self, session) -> None:
        company = await _company(session)
        run = await _run(session, company, started=NOW)
        await _finding(session, run)
        await session.commit()
        await persist_delta(session, await compute_delta(session, to_run=run), now=NOW)
        await session.commit()
        found = await latest_delta(session, company_id=company.id)
        assert found is not None
        assert found.to_run_id == run.id

    async def test_the_serialised_delta_answers_the_investors_question(
        self, session
    ) -> None:
        company = await _company(session)
        old = await _run(session, company, started=NOW - timedelta(days=90))
        await _finding(session, old, statement="30,000m", evidence_ids=["ev:a"])
        new = await _run(session, company, started=NOW)
        await _finding(session, new, statement="31,338m", evidence_ids=["ev:b"])
        await session.commit()
        payload = (await compute_delta(session, to_run=new, from_run=old)).to_dict()
        assert set(payload) >= {
            "new_evidence",
            "changed_facts",
            "resolved_questions",
            "new_gaps",
            "closed_gaps",
            "invalidated_findings",
            "unchanged_core_thesis",
        }
        assert payload["counts"]["invalidated_findings"] == 1
