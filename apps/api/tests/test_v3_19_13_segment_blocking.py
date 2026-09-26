"""V3.19.13 — read in production: the Pandora refresh (report bcc55315) made 32 findings
and published NONE, because the luxury playbook's blocking `segment_discipline` question
stayed open and the Council refused to convene.

Two defects:
1. Pandora's FY2025 revenue was stored as scoped to the segment "This year" — a table
   column header. A period label is not a business area: it is UNKNOWN scope.
2. Segment discipline guards the CFR failure (a segment figure reported as the Group's).
   That hazard exists only for a company whose evidence NAMES segments. Where none is
   named, the question is recorded `unanswerable` (`precondition_absent`) and no longer
   refuses every finding the run made. A company with named segments stays blocked.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.company import Company
from app.models.extracted_document import ExtractedDocument, ExtractedFact
from app.services.director.loop import _release_conditional_blocks
from app.services.director.planner import persist_plan, plan_research
from app.services.ledger import store as ledger
from app.services.playbooks.industries import LUXURY
from app.services.sources.fact_scope import is_period_label, parse_scope, scope_from_columns


@compiles(JSONB, "sqlite")
def _jsonb(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


class _Adapter:
    def __init__(self, book) -> None:  # noqa: ANN001
        self._book = book
        self.playbook_id = book.playbook_id
        self.version = book.version

    def mandatory_questions(self):  # noqa: ANN201
        return self._book.mandatory_questions()

    def specialist_roles(self):  # noqa: ANN201
        return self._book.specialist_role_ids()

    def completion_rules(self):  # noqa: ANN201
        return self._book.completion_rule_ids()


@pytest.mark.parametrize("label", ["This year", "Last year", "Prior year", "FY2025",
                                   "2025", "2025/26", "H1 2026", "Q3", "Full year"])
def test_a_period_label_is_not_a_segment(label):
    assert is_period_label(label)
    assert parse_scope(label).scope_type is None
    # A row stored before this fix degrades on READ — no backfill.
    assert scope_from_columns("segment", label).scope_type is None


@pytest.mark.parametrize("label", ["Jewellery Maisons", "Core", "Fuel with more",
                                   "Americas", "Watchmakers"])
def test_a_business_area_is_still_a_segment(label):
    assert parse_scope(label).scope_type == "segment"


def test_the_condition_survives_planning():
    [question] = [q for q in LUXURY.mandatory_questions() if q.key == "segment_discipline"]
    assert question.blocking and question.blocking_requires == "named_segments"


async def _setup(session, segment_names):  # noqa: ANN001, ANN202
    company = Company(id=uuid.uuid4(), ticker="PNDORA", exchange="CO", name="Pandora A/S",
                      status="new")
    session.add(company)
    await session.flush()
    doc = ExtractedDocument(
        id=uuid.uuid4(), content_hash=uuid.uuid4().hex * 2,
        canonical_url="https://pandoragroup.com/ar.pdf", provider="company_ir",
        source_type="annual_report", source_tier="T1_primary_filing",
        mime_type="application/pdf", extraction_method="native_pdf", status="extracted",
        retrieved_at=datetime.now(timezone.utc), excerpts_json=[], company_id=company.id)
    session.add(doc)
    await session.flush()
    for name in segment_names:
        session.add(ExtractedFact(
            id=uuid.uuid4(), extracted_document_id=doc.id, label="revenue",
            value_numeric=32500, currency="DKK", scale="million", period="FY2025",
            extraction_method="native_pdf", confidence=0.9, validation_status="validated",
            needs_human_review=False, is_active=True, scope_type="segment",
            scope_name=name, scope_key=f"segment:{name.casefold()}"))
    run = await ledger.open_run(session, mode="standard", company_id=company.id)
    plan = await plan_research(subject="PNDORA", playbooks=[_Adapter(LUXURY)])
    await persist_plan(session, run, plan)
    await session.flush()
    return run, {q.key: q for q in plan.questions}


async def test_no_named_segment_releases_the_block(session):
    # The live case: the ONLY "segment" was the period header "This year".
    run, questions = await _setup(session, ["This year"])
    before = await ledger.summarise(session, run)
    assert before.questions_blocking_open == 1
    released = await _release_conditional_blocks(session, run, questions)
    assert released == ["segment_discipline"]
    after = await ledger.summarise(session, run)
    assert after.questions_blocking_open == 0
    from sqlalchemy import select

    from app.models.ledger import ResearchQuestion

    row = (await session.execute(select(ResearchQuestion).where(
        ResearchQuestion.research_run_id == run.id,
        ResearchQuestion.question_key == "segment_discipline"))).scalar_one()
    assert row.resolution_status == ledger.QUESTION_UNANSWERABLE
    assert row.unresolved_reason == ledger.UNRESOLVED_PRECONDITION_ABSENT


async def test_a_company_with_named_segments_stays_blocked(session):
    """The CFR protection is untouched: named segments, unanswered question → refusal."""
    run, questions = await _setup(session, ["Jewellery Maisons", "Specialist Watchmakers"])
    assert await _release_conditional_blocks(session, run, questions) == []
    assert (await ledger.summarise(session, run)).questions_blocking_open == 1


async def test_an_unconditional_blocking_question_is_never_released(session):
    from dataclasses import replace

    run, questions = await _setup(session, [])
    questions["segment_discipline"] = replace(questions["segment_discipline"],
                                              blocking_requires=None)
    assert await _release_conditional_blocks(session, run, questions) == []


async def test_an_unknown_condition_holds(session):
    from dataclasses import replace

    run, questions = await _setup(session, [])
    questions["segment_discipline"] = replace(questions["segment_discipline"],
                                              blocking_requires="something_new")
    assert await _release_conditional_blocks(session, run, questions) == []
