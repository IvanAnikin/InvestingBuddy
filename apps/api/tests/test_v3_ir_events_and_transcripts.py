"""V3.4 Slice 4.8 — IR events, transcript availability, and the two tools.

WHAT THESE TESTS ARE ABOUT
==========================
One distinction, made everywhere: **"the issuer published no transcript" is not "the
issuer held no call", and neither is "we have not looked".** ADR-051 chose free public
sources and accepted uneven coverage; representing the absence precisely is what makes
that liveable rather than invisible.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.company import Company
from app.models.ir_event import IrEventMaterial
from app.services.agent_tools.ir_events import (
    GET_IR_EVENTS_SPEC,
    GET_TRANSCRIPTS_SPEC,
    validate_get_ir_events,
    validate_get_transcripts,
)
from app.services.ir_events import service as ir

T0 = datetime(2026, 5, 15, tzinfo=timezone.utc)


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


def _spec(**overrides) -> ir.EventSpec:
    base = {
        "issuer_key": "cfr:sw",
        "event_type": ir.EVENT_EARNINGS_CALL,
        "status": ir.STATUS_OCCURRED,
        "fiscal_period_key": "2025",
        "occurred_at": T0,
        "title": "FY2025 results call",
    }
    base.update(overrides)
    return ir.EventSpec(**base)


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


class TestEventIdentity:
    def test_two_discoveries_of_one_call_converge(self) -> None:
        """One from an IR calendar in January, one from a results release in May."""
        a = ir.event_key_for(
            issuer_key="cfr:sw",
            event_type=ir.EVENT_EARNINGS_CALL,
            fiscal_period_key="2025",
        )
        b = ir.event_key_for(
            issuer_key="cfr:sw",
            event_type=ir.EVENT_EARNINGS_CALL,
            fiscal_period_key="FY2025",
        )
        assert a == b

    def test_different_periods_are_different_events(self) -> None:
        assert ir.event_key_for(
            issuer_key="cfr:sw", event_type=ir.EVENT_EARNINGS_CALL,
            fiscal_period_key="2025",
        ) != ir.event_key_for(
            issuer_key="cfr:sw", event_type=ir.EVENT_EARNINGS_CALL,
            fiscal_period_key="2024",
        )

    def test_an_event_with_no_period_and_no_date_over_splits(self) -> None:
        """Splitting produces a duplicate a reader can merge; merging attributes one
        call's transcript to another call, and only one of those is recoverable."""
        a = ir.event_key_for(issuer_key="x", event_type=ir.EVENT_AGM)
        b = ir.event_key_for(issuer_key="x", event_type=ir.EVENT_AGM)
        assert a != b

    async def test_upserting_the_same_event_twice_produces_one_row(
        self, session
    ) -> None:
        company = await _company(session)
        first = await ir.upsert_event(session, _spec(), company_id=company.id)
        second = await ir.upsert_event(session, _spec(), company_id=company.id)
        assert first.id == second.id

    async def test_an_occurred_event_is_never_demoted(self, session) -> None:
        """A later calendar scrape that still calls it "upcoming" must not un-happen a
        call the platform already saw occur."""
        company = await _company(session)
        await ir.upsert_event(session, _spec(), company_id=company.id)
        again = await ir.upsert_event(
            session,
            _spec(status=ir.STATUS_ANNOUNCED, occurred_at=None),
            company_id=company.id,
        )
        assert again.status == ir.STATUS_OCCURRED

    async def test_an_announced_event_is_promoted_when_it_happens(
        self, session
    ) -> None:
        company = await _company(session)
        await ir.upsert_event(
            session,
            _spec(status=ir.STATUS_ANNOUNCED, occurred_at=None, scheduled_at=T0),
            company_id=company.id,
        )
        promoted = await ir.upsert_event(session, _spec(), company_id=company.id)
        assert promoted.status == ir.STATUS_OCCURRED
        assert promoted.occurred_at == T0

    def test_an_announced_event_cannot_claim_it_occurred(self) -> None:
        with pytest.raises(ValueError, match="asserting a past"):
            _spec(status=ir.STATUS_ANNOUNCED)

    def test_the_vocabularies_are_closed(self) -> None:
        with pytest.raises(ValueError, match="not an IR event type"):
            _spec(event_type="fireside_chat")


# --------------------------------------------------------------------------- #
# The absence
# --------------------------------------------------------------------------- #


class TestTranscriptState:
    async def test_no_row_means_nobody_looked(self, session) -> None:
        company = await _company(session)
        event = await ir.upsert_event(session, _spec(), company_id=company.id)
        state = await ir.transcript_state(session, event)
        assert state.state == "not_checked"
        # And that is a TASK, not a research gap.
        assert state.is_a_research_gap is False

    async def test_not_published_is_a_row_and_a_research_gap(self, session) -> None:
        """The state the table exists for. "CFR published no FY2025 transcript" is a
        finding about the issuer; an absent row is a statement about this platform."""
        company = await _company(session)
        event = await ir.upsert_event(session, _spec(), company_id=company.id)
        await ir.record_availability(
            session,
            event,
            material_type=ir.MATERIAL_TRANSCRIPT,
            availability=ir.AVAILABILITY_NOT_PUBLISHED,
            note="IR site lists a presentation and a webcast, no transcript.",
        )
        state = await ir.transcript_state(session, event)
        assert state.state == "not_published"
        assert state.is_a_research_gap is True
        assert state.checked_at is not None

    async def test_published_and_held_are_different_states(self, session) -> None:
        company = await _company(session)
        event = await ir.upsert_event(session, _spec(), company_id=company.id)
        await ir.record_availability(
            session,
            event,
            material_type=ir.MATERIAL_TRANSCRIPT,
            availability=ir.AVAILABILITY_AVAILABLE,
            url="https://richemont.example/fy25-transcript.pdf",
        )
        assert (await ir.transcript_state(session, event)).state == "published"

    async def test_paywalled_is_a_gap_and_is_never_scraped(self, session) -> None:
        company = await _company(session)
        event = await ir.upsert_event(session, _spec(), company_id=company.id)
        await ir.record_availability(
            session,
            event,
            material_type=ir.MATERIAL_TRANSCRIPT,
            availability=ir.AVAILABILITY_PAYWALLED,
            url="https://vendor.example/transcript",
        )
        state = await ir.transcript_state(session, event)
        assert state.state == "paywalled"
        assert state.is_a_research_gap is True

    async def test_an_available_material_must_say_where_it_is(self, session) -> None:
        company = await _company(session)
        event = await ir.upsert_event(session, _spec(), company_id=company.id)
        with pytest.raises(ValueError, match="say where it is"):
            await ir.record_availability(
                session,
                event,
                material_type=ir.MATERIAL_TRANSCRIPT,
                availability=ir.AVAILABILITY_AVAILABLE,
            )

    async def test_a_corpus_document_cannot_hang_off_an_absence(self, session) -> None:
        company = await _company(session)
        event = await ir.upsert_event(session, _spec(), company_id=company.id)
        with pytest.raises(ValueError, match="contradicting itself"):
            await ir.record_availability(
                session,
                event,
                material_type=ir.MATERIAL_TRANSCRIPT,
                availability=ir.AVAILABILITY_NOT_PUBLISHED,
                research_document_version_id=uuid.uuid4(),
            )

    async def test_the_database_refuses_the_same_contradiction(self, session) -> None:
        """Enforced in SQL as well as in Python: a future writer that bypassed the
        service must not be able to store it."""
        company = await _company(session)
        event = await ir.upsert_event(session, _spec(), company_id=company.id)
        session.add(
            IrEventMaterial(
                id=uuid.uuid4(),
                ir_event_id=event.id,
                material_type=ir.MATERIAL_TRANSCRIPT,
                availability=ir.AVAILABILITY_AVAILABLE,
                url=None,
                research_document_version_id=None,
                checked_at=T0,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async def test_re_checking_updates_rather_than_duplicating(self, session) -> None:
        company = await _company(session)
        event = await ir.upsert_event(session, _spec(), company_id=company.id)
        await ir.record_availability(
            session,
            event,
            material_type=ir.MATERIAL_TRANSCRIPT,
            availability=ir.AVAILABILITY_NOT_PUBLISHED,
        )
        await ir.record_availability(
            session,
            event,
            material_type=ir.MATERIAL_TRANSCRIPT,
            availability=ir.AVAILABILITY_AVAILABLE,
            url="https://richemont.example/t.pdf",
        )
        await session.commit()
        from sqlalchemy import select

        rows = (
            await session.execute(
                select(IrEventMaterial).where(
                    IrEventMaterial.ir_event_id == event.id
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].availability == ir.AVAILABILITY_AVAILABLE

    async def test_a_presentation_and_a_transcript_are_separate_materials(
        self, session
    ) -> None:
        """The exact European shape ADR-051 predicts: a presentation published, a
        transcript not."""
        company = await _company(session)
        event = await ir.upsert_event(session, _spec(), company_id=company.id)
        await ir.record_availability(
            session,
            event,
            material_type=ir.MATERIAL_PRESENTATION,
            availability=ir.AVAILABILITY_AVAILABLE,
            url="https://richemont.example/fy25-deck.pdf",
        )
        await ir.record_availability(
            session,
            event,
            material_type=ir.MATERIAL_TRANSCRIPT,
            availability=ir.AVAILABILITY_NOT_PUBLISHED,
        )
        assert (await ir.transcript_state(session, event)).state == "not_published"


# --------------------------------------------------------------------------- #
# The tools
# --------------------------------------------------------------------------- #


class _Context:
    def __init__(self, session) -> None:  # noqa: ANN001
        self.session = session


class TestTools:
    def test_both_tools_are_registered(self) -> None:
        from app.services.agent_tools.builtin import register_builtins
        from app.services.agent_tools.registry import ToolRegistry

        names = register_builtins(ToolRegistry()).names()
        assert {"get_ir_events", "get_transcripts"} <= set(names)

    def test_a_transcript_payload_is_labelled_untrusted(self) -> None:
        """Issuer-published speech quoted back into a prompt is content from outside
        the platform."""
        assert GET_TRANSCRIPTS_SPEC.may_contain_untrusted_content is True
        assert GET_IR_EVENTS_SPEC.may_contain_untrusted_content is False

    def test_an_unknown_event_type_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not IR event types"):
            validate_get_ir_events(
                {"company_id": str(uuid.uuid4()), "event_types": ["fireside_chat"]}
            )

    def test_get_transcripts_defaults_to_events_that_produce_speech(self) -> None:
        """Defaulting to every type would report "no transcript" for an AGM notice,
        which is not a finding."""
        validated = validate_get_transcripts({"company_id": str(uuid.uuid4())})
        assert ir.EVENT_EARNINGS_CALL in validated["event_types"]
        assert ir.EVENT_RESULTS_RELEASE not in validated["event_types"]

    async def test_an_empty_event_list_says_what_it_means(self, session) -> None:
        company = await _company(session)
        result = await GET_IR_EVENTS_SPEC.handler(
            _Context(session),
            validate_get_ir_events({"company_id": str(company.id)}),
        )
        assert result["items"] == []
        assert "coverage" in result["summary"]

    async def test_the_transcript_tool_reports_a_state_per_event(
        self, session
    ) -> None:
        company = await _company(session)
        held = await ir.upsert_event(session, _spec(), company_id=company.id)
        await ir.record_availability(
            session,
            held,
            material_type=ir.MATERIAL_TRANSCRIPT,
            availability=ir.AVAILABILITY_AVAILABLE,
            url="https://richemont.example/t.pdf",
        )
        missing = await ir.upsert_event(
            session,
            _spec(fiscal_period_key="2024", occurred_at=datetime(2025, 5, 15, tzinfo=timezone.utc)),
            company_id=company.id,
        )
        await ir.record_availability(
            session,
            missing,
            material_type=ir.MATERIAL_TRANSCRIPT,
            availability=ir.AVAILABILITY_NOT_PUBLISHED,
        )
        await session.commit()
        result = await GET_TRANSCRIPTS_SPEC.handler(
            _Context(session),
            validate_get_transcripts({"company_id": str(company.id)}),
        )
        states = {item["fiscal_period_key"]: item["transcript_state"] for item in result["items"]}
        assert states == {"2025": "published", "2024": "not_published"}
        assert len(result["research_gaps"]) == 1
        assert result["research_gaps"][0]["fiscal_period_key"] == "2024"

    async def test_not_checked_is_not_reported_as_a_gap(self, session) -> None:
        company = await _company(session)
        await ir.upsert_event(session, _spec(), company_id=company.id)
        await session.commit()
        result = await GET_TRANSCRIPTS_SPEC.handler(
            _Context(session),
            validate_get_transcripts({"company_id": str(company.id)}),
        )
        assert result["items"][0]["transcript_state"] == "not_checked"
        assert result["research_gaps"] == []

    async def test_the_period_filter_bounds_the_population_asked_for(
        self, session
    ) -> None:
        company = await _company(session)
        for year in range(2015, 2026):
            await ir.upsert_event(
                session,
                _spec(
                    fiscal_period_key=str(year),
                    occurred_at=datetime(year, 5, 15, tzinfo=timezone.utc),
                ),
                company_id=company.id,
            )
        await session.commit()
        events = await ir.events_for_company(
            session, company.id, period_keys=("2016",), limit=3
        )
        assert [e.fiscal_period_key for e in events] == ["2016"]
