"""Item 21 on a real PostgreSQL: the issuer statements view reads THIS company's facts.

An ``ExtractedDocument`` is shared by content hash, so its ``company_id`` is whoever
extracted it first. A company's facts come through ``company_documents_clause``: its own
documents, plus unowned ones it attempted — never a document another company owns.
Only active, validated statement facts count. Skipped without ``V3_TEST_POSTGRES_URL``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest

from app.models.company import Company
from app.models.document_ingestion_attempt import DocumentIngestionAttempt
from app.models.extracted_document import ExtractedDocument, ExtractedFact
from app.services.pipeline.issuer_financials import (
    STATE_ACQUIRED_NOT_EXTRACTED,
    STATE_FACTS_EXTRACTED,
    financial_statements_for,
    load_statement_facts,
)

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head"
)

NOW = datetime(2026, 9, 30, tzinfo=UTC)


@pytest.fixture
async def factory():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(POSTGRES_URL, future=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _company(db, name="Statements Test Issuer") -> Company:  # noqa: ANN001
    company = Company(id=uuid.uuid4(), ticker=f"S{uuid.uuid4().hex[:5]}".upper(),
                      exchange="AU", name=name, status="new")
    db.add(company)
    await db.flush()
    return company


async def _document(db, owner, *, doc_date=date(2025, 9, 30)) -> ExtractedDocument:  # noqa: ANN001
    doc = ExtractedDocument(
        id=uuid.uuid4(), content_hash=uuid.uuid4().hex * 2,
        canonical_url=f"https://official.example/{uuid.uuid4().hex[:8]}.pdf",
        provider="asx_announcements", source_type="asx_announcement",
        source_tier="T1_primary_filing", mime_type="application/pdf",
        title="Annual Report 2025", doc_date=doc_date,
        extraction_method="native_pdf", status="extracted",
        retrieved_at=datetime.now(UTC), excerpts_json=[],
        company_id=owner.id if owner is not None else None,
    )
    db.add(doc)
    await db.flush()
    return doc


def _fact(doc, label, value, period="2025", *, scope="group", status="validated",  # noqa: ANN001
          active=True, confidence=0.8) -> ExtractedFact:
    return ExtractedFact(
        id=uuid.uuid4(), extracted_document_id=doc.id, label=label,
        value_numeric=value, value_text=str(value), unit="currency_amount",
        currency="AUD", scale="thousand", period=period, extraction_method="native_pdf",
        confidence=confidence, validation_status=status, needs_human_review=True,
        is_active=active,
        scope_type=scope if scope in (None, "group") else "segment",
        scope_name=None if scope in (None, "group") else scope,
        scope_key=None if scope is None else (
            "group" if scope == "group" else f"segment:{scope.casefold()}"),
    )


async def test_the_company_reads_its_own_and_its_attempted_documents_only(factory):
    async with factory() as db:
        company = await _company(db)
        other = await _company(db, "Another Issuer")
        own = await _document(db, company)
        unowned = await _document(db, None, doc_date=date(2026, 3, 15))
        borrowed = await _document(db, other)
        db.add(DocumentIngestionAttempt(
            id=uuid.uuid4(), company_id=company.id, canonical_url=unowned.canonical_url,
            url_hash=uuid.uuid4().hex * 2, source_type="asx_announcement",
            source_tier="T1_primary_filing", status="extracted",
            content_hash=unowned.content_hash, attempted_at=datetime.now(UTC)))
        # Even an attempt on the other company's document borrows nothing.
        db.add(DocumentIngestionAttempt(
            id=uuid.uuid4(), company_id=company.id, canonical_url=borrowed.canonical_url,
            url_hash=uuid.uuid4().hex * 2, source_type="asx_announcement",
            source_tier="T1_primary_filing", status="extracted",
            content_hash=borrowed.content_hash, attempted_at=datetime.now(UTC)))
        db.add_all([
            _fact(own, "net_income", -3265),
            _fact(own, "cash_and_equivalents", 9470),
            _fact(own, "operating_cash_flow", -2980, active=False),
            _fact(own, "capital_expenditure", 450, status="excerpt_only"),
            _fact(own, "employees", 42),  # not a statement line
            _fact(unowned, "cash_and_equivalents", 7000, "H1 2026"),
            _fact(borrowed, "revenue", 99999),
        ])
        await db.flush()

        facts = await load_statement_facts(db, company.id)
        read = {(f["field"], f["period"]) for f in facts}
        assert read == {("net_income", "2025"), ("cash_and_equivalents", "2025"),
                        ("cash_and_equivalents", "H1 2026")}
        # Newest document first.
        assert facts[0]["period"] == "H1 2026"
        assert {f["confidence"] for f in facts} == {"high"}
        assert facts[0]["scope"] == "group"
        await db.rollback()


async def test_c_from_the_database_and_b_when_only_a_segment_was_extracted(factory):
    ready = {"source_id": "asx_announcements", "documents": [
        {"document_kind": "annual_report", "state": "ready", "headline": "Annual Report 2025",
         "filing_date": "2025-09-30", "source_url": "https://official.example/ar.pdf"}]}
    async with factory() as db:
        company = await _company(db)
        doc = await _document(db, company)
        db.add_all([_fact(doc, "net_income", -3265), _fact(doc, "cash_and_equivalents", 9470)])
        await db.flush()
        state = await financial_statements_for(
            db, company, core_disclosures=ready, core_filings={}, now=NOW)
        assert state["annual"]["state"] == STATE_FACTS_EXTRACTED
        assert state["reporting_periods"]["latest_annual"] == "FY2025"
        assert state["documents_read"] == 1
        await db.rollback()

    async with factory() as db:
        company = await _company(db)
        doc = await _document(db, company)
        db.add(_fact(doc, "net_income", -3265, scope="Project Company Pty Ltd"))
        await db.flush()
        state = await financial_statements_for(
            db, company, core_disclosures=ready, core_filings={}, now=NOW)
        assert state["annual"]["state"] == STATE_ACQUIRED_NOT_EXTRACTED
        assert state["slots"] == {}
        await db.rollback()
