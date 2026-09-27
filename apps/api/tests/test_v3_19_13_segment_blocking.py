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

import os
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
from app.services.sources.fact_scope import (
    is_period_label,
    names_a_business_segment,
    parse_scope,
    scope_from_columns,
)


@compiles(JSONB, "sqlite")
def _jsonb(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")


@pytest.fixture(params=["sqlite", "postgres"])
async def session(request):  # noqa: ANN001, ANN201
    """Both engines: SQLite with FKs off has hidden real outages before. On PostgreSQL
    everything is rolled back — the database is shared with the rest of the suite."""
    if request.param == "postgres":
        if not POSTGRES_URL:
            pytest.skip("set V3_TEST_POSTGRES_URL to a PostgreSQL at head")
        engine = create_async_engine(POSTGRES_URL, future=True)
        async with engine.connect() as conn:
            outer = await conn.begin()
            async with async_sessionmaker(bind=conn, expire_on_commit=False,
                                          join_transaction_mode="create_savepoint")() as s:
                s.info["engine"] = "postgres"
                yield s
            await outer.rollback()
        await engine.dispose()
        return
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        s.info["engine"] = "sqlite"
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
def test_a_period_label_does_not_name_a_segment(label):
    """It names no business area — but the FIGURE stays segment-scoped, so it can never
    fill a Group slot under the implicit-Group convention (V3.19.15 review)."""
    assert is_period_label(label)
    assert not names_a_business_segment("segment", label)
    assert parse_scope(label).scope_type == "segment"
    assert scope_from_columns("segment", label).scope_type == "segment"


@pytest.mark.parametrize("label", ["Jewellery Maisons", "Core", "Fuel with more",
                                   "Americas", "Watchmakers"])
def test_a_business_area_is_still_a_segment(label):
    assert parse_scope(label).scope_type == "segment"


def test_the_condition_survives_planning():
    [question] = [q for q in LUXURY.mandatory_questions() if q.key == "segment_discipline"]
    assert question.blocking and question.blocking_requires == "named_segments"


async def _setup(session, segment_names, *, extracted=True, company=True,  # noqa: ANN001, ANN202
                 active=True, linked_by_attempt=False):
    with_company = company
    company = Company(id=uuid.uuid4(), ticker=f"P{uuid.uuid4().hex[:6]}".upper(),
                      exchange="CO", name="Pandora A/S",
                      status="new")
    session.add(company)
    await session.flush()
    doc = ExtractedDocument(
        id=uuid.uuid4(), content_hash=uuid.uuid4().hex * 2,
        canonical_url="https://pandoragroup.com/ar.pdf", provider="company_ir",
        source_type="annual_report", source_tier="T1_primary_filing",
        mime_type="application/pdf", extraction_method="native_pdf", status="extracted",
        retrieved_at=datetime.now(timezone.utc), excerpts_json=[],
        # A REUSED document keeps its first extractor's company (here: none); this company
        # owns it only through its ingestion attempt.
        company_id=None if linked_by_attempt else company.id)
    session.add(doc)
    await session.flush()
    if linked_by_attempt:
        from app.models.document_ingestion_attempt import DocumentIngestionAttempt

        session.add(DocumentIngestionAttempt(
            id=uuid.uuid4(), company_id=company.id, canonical_url=doc.canonical_url,
            url_hash=uuid.uuid4().hex, source_type="company_ir_annual_report",
            source_tier="T1_primary_filing", doc_kind="annual_report",
            discovery_strategy="static_link", attempted_at=datetime.now(timezone.utc),
            status="extracted", mime_type="application/pdf", http_status_class="2xx",
            extraction_method="native_pdf", page_count=10, content_hash=doc.content_hash,
            fetch_ms=1, extraction_ms=1, total_ms=2, pinned=True))
        await session.flush()
    if extracted:
        # Proof the platform read the accounts: one active Group-scope fact.
        session.add(ExtractedFact(
            id=uuid.uuid4(), extracted_document_id=doc.id, label="revenue",
            value_numeric=31000, currency="DKK", scale="million", period="FY2024",
            extraction_method="native_pdf", confidence=0.9, validation_status="validated",
            needs_human_review=False, is_active=True, scope_type="group",
            scope_name=None, scope_key="group"))
    for name in segment_names:
        session.add(ExtractedFact(
            id=uuid.uuid4(), extracted_document_id=doc.id, label="revenue",
            value_numeric=32500, currency="DKK", scale="million", period="FY2025",
            extraction_method="native_pdf", confidence=0.9, validation_status="validated",
            needs_human_review=False, is_active=active, scope_type="segment",
            scope_name=name, scope_key=f"segment:{name.casefold()}"))
    run = await ledger.open_run(session, mode="standard",
                                company_id=company.id if with_company else None)
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
    # The question's own reason is kept when it had one; the release is logged.
    assert row.unresolved_reason in (None, ledger.UNRESOLVED_PRECONDITION_ABSENT,
                                     *ledger.UNRESOLVED_REASONS)
    assert row.acquisition_log_json[-1]["rung"] == "blocking_released"
    # The Council and the reader are TOLD, through a non-blocking gap.
    gaps = await ledger.open_gaps(session, run, limit=50)
    [gap] = [g for g in gaps if g.question_key == "segment_discipline"]
    assert "could not be applied" in gap.description and not gap.blocks_council
    assert after.council_may_convene


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


async def test_an_existing_unresolved_reason_is_not_overwritten(session):
    from sqlalchemy import select

    from app.models.ledger import ResearchQuestion

    run, questions = await _setup(session, [])
    row = (await session.execute(select(ResearchQuestion).where(
        ResearchQuestion.research_run_id == run.id,
        ResearchQuestion.question_key == "segment_discipline"))).scalar_one()
    row.unresolved_reason = ledger.UNRESOLVED_BUDGET_EXHAUSTED
    await session.flush()
    assert await _release_conditional_blocks(session, run, questions)
    assert row.unresolved_reason == ledger.UNRESOLVED_BUDGET_EXHAUSTED


@pytest.mark.parametrize("case", ["no_company", "nothing_extracted"])
async def test_without_positive_proof_the_block_holds(session, case):
    """Absence of extraction is not absence of segments."""
    run, questions = await _setup(session, [], extracted=case != "nothing_extracted",
                                  company=case != "no_company")
    assert await _release_conditional_blocks(session, run, questions) == []
    assert (await ledger.summarise(session, run)).questions_blocking_open == 1


async def test_a_superseded_segment_fact_does_not_hold_the_block(session):
    run, questions = await _setup(session, ["Jewellery Maisons"], active=False)
    assert await _release_conditional_blocks(session, run, questions) == [
        "segment_discipline"]


async def test_a_named_segment_in_the_corpus_holds_the_block(session):
    """The segment table may exist only as indexed text — the CFR case again."""
    from app.models.research_chunk import ResearchDocumentChunk

    if session.info.get("engine") == "postgres":
        pytest.skip("the chunk's parent document rows are not built here (SQLite, FKs off)")
    run, questions = await _setup(session, [])
    session.add(ResearchDocumentChunk(
        id=uuid.uuid4(), chunk_id=f"c:{uuid.uuid4().hex[:12]}",
        derivation_id=uuid.uuid4(), research_document_version_id=uuid.uuid4(),
        company_id=run.company_id, kind="table", ordinal=0,
        text="Jewellery Maisons sales 14,000", char_start=0, char_end=30,
        indexable=True, scope_type="segment", scope_name="Jewellery Maisons"))
    await session.flush()
    assert await _release_conditional_blocks(session, run, questions) == []


@pytest.mark.parametrize("scope_key", ["segment:jewellery maisons", "segment:Watchmakers"])
async def test_a_segment_finding_of_this_run_holds_the_block(session, scope_key):
    """Recorded exactly as the loop records it: scope KEY only, no scope_type."""
    run, questions = await _setup(session, [])
    await ledger.record_finding(
        session, run, statement="Maisons sales rose.", evidence_ids=["ev:c:1"],
        question_key="regional_mix", scope_key=scope_key)
    await session.flush()
    assert await _release_conditional_blocks(session, run, questions) == []


async def test_a_group_or_period_finding_does_not_hold_the_block(session):
    run, questions = await _setup(session, [])
    await ledger.record_finding(session, run, statement="Group revenue fell.",
                                evidence_ids=["ev:c:1"], question_key="revenue",
                                scope_key="group")
    await ledger.record_finding(session, run, statement="Revenue this year.",
                                evidence_ids=["ev:c:2"], question_key="revenue",
                                scope_key="segment:this year")
    await session.flush()
    assert await _release_conditional_blocks(session, run, questions) == [
        "segment_discipline"]


async def _question_row(session, run):  # noqa: ANN001, ANN202
    from sqlalchemy import select

    from app.models.ledger import ResearchQuestion

    return (await session.execute(select(ResearchQuestion).where(
        ResearchQuestion.research_run_id == run.id,
        ResearchQuestion.question_key == "segment_discipline"))).scalar_one()


async def test_a_document_owned_through_its_ingestion_attempt_counts(session):
    """V3.19.14 — read live: Pandora's block held though nothing named a segment. A
    reused document keeps its FIRST extractor's company_id; the platform's own
    provenance link is the ingestion attempt, and that is what is read now."""
    run, questions = await _setup(session, ["This year"], linked_by_attempt=True)
    assert await _release_conditional_blocks(session, run, questions) == [
        "segment_discipline"]


@pytest.mark.parametrize(("kwargs", "names", "reason"), [
    ({"extracted": False}, [], "no_extraction_proof"),
    ({"company": False}, [], "no_company"),
    ({}, ["Jewellery Maisons"], "segment_fact:Jewellery Maisons"),
])
async def test_a_held_block_records_why(session, kwargs, names, reason):
    """A refused run must be auditable from the report alone."""
    run, questions = await _setup(session, names, **kwargs)
    assert await _release_conditional_blocks(session, run, questions) == []
    row = await _question_row(session, run)
    assert row.acquisition_log_json[-1] == {
        "rung": "blocking_held", "condition": "named_segments", "reason": reason}


def test_no_company_matches_no_document():
    from sqlalchemy import false

    from app.services.sources.company_documents import company_documents_clause

    assert str(company_documents_clause(None)) == str(false())


@pytest.mark.parametrize("label", [
    # V3.19.15 — read live: this chunk "segment" held Pandora's block.
    "across our regions – we expect to grow our market share across all of them",
    "Our jewellery business grew strongly in every market we operate in this year",
    "where we see continued momentum in our core collections",
])
def test_running_text_does_not_name_a_segment(label):
    assert not names_a_business_segment("segment", label)
    assert parse_scope(label).scope_type == "segment"  # the figure's scope is unchanged


@pytest.mark.parametrize("label", [
    "Jewellery Maisons", "Specialist Watchmakers", "Other Businesses", "Americas",
    "Fuel with more", "Asia Pacific", "iPhone", "Rest of the world",
    "Specialist Watchmakers and Other Businesses",
])
def test_a_heading_is_still_a_segment(label):
    assert parse_scope(label).scope_type == "segment"
    assert names_a_business_segment("segment", label)


@pytest.mark.parametrize("label", [
    # The review's cases: long or lower-case headings that ARE segment reporting.
    "Revenue and operating profit by reportable segment for the year ended 31 March 2025",
    "Segment information: revenue by operating segment and geographical area",
    "revenue by region", "sales by business segment", "eBay Marketplaces segment",
    "e-commerce and wholesale", "iPad and Mac", "eBay and StubHub",
    # "US" is a country, not a pronoun.
    "US", "US & Canada", "US Retail", "North America (US)", "Our Brands", "Our Maisons",
    "Fashion & Leather Goods, Perfumes & Cosmetics, Watches & Jewelry, Selective Retailing",
])
def test_segment_reporting_headings_keep_the_block(label):
    assert parse_scope(label).scope_type == "segment"
    assert names_a_business_segment("segment", label)


def test_a_segment_figure_never_becomes_group_eligible():
    """The CFR failure: a segment figure under any heading stays segment-scoped."""
    from app.services.sources.fact_scope import GROUP_SCOPE

    for label in ("This year", "across our regions – we expect to grow",
                  "Revenue by reportable segment for the year ended 31 March 2025"):
        assert parse_scope(label) != GROUP_SCOPE
        assert scope_from_columns("segment", label).scope_type == "segment"


async def test_a_prose_chunk_scope_does_not_hold_the_block(session):
    from app.models.research_chunk import ResearchDocumentChunk

    if session.info.get("engine") == "postgres":
        pytest.skip("the chunk's parent document rows are not built here (SQLite, FKs off)")
    run, questions = await _setup(session, [])
    session.add(ResearchDocumentChunk(
        id=uuid.uuid4(), chunk_id=f"c:{uuid.uuid4().hex[:12]}",
        derivation_id=uuid.uuid4(), research_document_version_id=uuid.uuid4(),
        company_id=run.company_id, kind="text", ordinal=0,
        text="across our regions – we expect to grow our market share", char_start=0,
        char_end=55, indexable=True, scope_type="segment",
        scope_name="across our regions – we expect to grow our market share across"))
    await session.flush()
    assert await _release_conditional_blocks(session, run, questions) == [
        "segment_discipline"]
