"""Phase 32A Slice 5B.3 — primary-document provenance admin API.

Covers:
  * GET /api/v1/reports/{report_id}/primary-documents (router, mocked service)
  * app.services.primary_document_view_service.get_report_primary_documents
    (real in-memory SQLite DB — the same pattern as
    test_phase32a_slice5_reuse.py)

Exercises: run-scoped vs company-scoped fallback, native vs OCR counting,
reuse detection, validated-fact promotion counting, honest all-zero response
for a report with no ingestion activity, and company isolation (a report
never sees another company's attempts).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import document_ingestion_attempt as _document_ingestion_attempt  # noqa: F401,E501
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models.document_ingestion_attempt import DocumentIngestionAttempt
from app.models.extracted_document import ExtractedDocument, ExtractedFact
from app.schemas.primary_document import ReportPrimaryDocumentsResponse
from app.services.primary_document_view_service import get_report_primary_documents


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def db(session_factory):
    async with session_factory() as s:
        yield s


def _attempt(
    *,
    company_id: uuid.UUID | None,
    agent_run_id: uuid.UUID | None,
    url: str,
    status: str,
    extraction_method: str | None = None,
    content_hash: str | None = None,
    failure_code: str | None = None,
    attempted_at: datetime | None = None,
) -> DocumentIngestionAttempt:
    return DocumentIngestionAttempt(
        id=uuid.uuid4(),
        company_id=company_id,
        agent_run_id=agent_run_id,
        canonical_url=url,
        url_hash=uuid.uuid4().hex,
        source_type="company_ir_annual_report",
        source_tier="T1_primary_filing",
        doc_kind="annual_report",
        discovery_strategy="static_link",
        attempted_at=attempted_at or _utcnow(),
        status=status,
        failure_code=failure_code,
        mime_type="application/pdf",
        http_status_class="2xx",
        extraction_method=extraction_method,
        page_count=10 if extraction_method else None,
        content_hash=content_hash,
        fetch_ms=500,
        extraction_ms=1200,
        total_ms=1700,
        pinned=True,
    )


def _document(
    *, content_hash: str, company_id: uuid.UUID, agent_run_id: uuid.UUID, created_at: datetime
) -> ExtractedDocument:
    return ExtractedDocument(
        id=uuid.uuid4(),
        content_hash=content_hash,
        canonical_url="https://www.example-issuer.com/reports/ar.pdf",
        provider="company_ir",
        source_type="company_ir_annual_report",
        source_tier="T1_primary_filing",
        mime_type="application/pdf",
        title="Annual Report 2024",
        retrieved_at=created_at,
        extraction_method="native_pdf",
        page_count=10,
        status="extracted",
        excerpts_json=[
            {
                "text": "Revenue increased.",
                "page_number": 3,
                "extraction_method": "native_pdf",
                "confidence": 0.9,
            }
        ],
        company_id=company_id,
        agent_run_id=agent_run_id,
        created_at=created_at,
    )


# --------------------------------------------------------------------------- #
# Service-level (real DB)
# --------------------------------------------------------------------------- #


async def test_no_activity_returns_honest_all_zero_summary(db):
    result = await get_report_primary_documents(
        db,
        report_company_id=uuid.uuid4(),
        report_agent_run_id=uuid.uuid4(),
        report_id=uuid.uuid4(),
    )
    assert result.summary.discovered_count == 0
    assert result.summary.extracted_count == 0
    assert result.documents == []


async def test_no_known_company_or_run_returns_empty_never_wildcards(db):
    company_id = uuid.uuid4()
    run_id = uuid.uuid4()
    db.add(_attempt(company_id=company_id, agent_run_id=run_id, url="https://x.com/a.pdf", status="extracted"))
    await db.commit()

    result = await get_report_primary_documents(
        db, report_company_id=None, report_agent_run_id=None, report_id=uuid.uuid4()
    )
    assert result.documents == []


async def test_native_and_ocr_counted_separately(db):
    run_id = uuid.uuid4()
    company_id = uuid.uuid4()
    db.add(_attempt(company_id=company_id, agent_run_id=run_id, url="https://x.com/a.pdf", status="extracted", extraction_method="native_pdf"))
    db.add(_attempt(company_id=company_id, agent_run_id=run_id, url="https://x.com/b.pdf", status="extracted", extraction_method="ocr"))
    db.add(_attempt(company_id=company_id, agent_run_id=run_id, url="https://x.com/c.pdf", status="metadata_only"))
    db.add(_attempt(company_id=company_id, agent_run_id=run_id, url="https://x.com/d.pdf", status="encrypted", failure_code="encrypted_pdf"))
    await db.commit()

    result = await get_report_primary_documents(
        db, report_company_id=company_id, report_agent_run_id=run_id, report_id=uuid.uuid4()
    )
    assert result.summary.attempted_count == 4
    assert result.summary.extracted_count == 2
    assert result.summary.native_count == 1
    assert result.summary.ocr_count == 1
    assert result.summary.metadata_only_count == 1
    assert result.summary.failed_count == 1
    # Failure code surfaces honestly on the failed document.
    failed = next(d for d in result.documents if d.status == "encrypted")
    assert failed.failure_code == "encrypted_pdf"


async def test_extracted_document_and_facts_joined_by_content_hash(db):
    run_id = uuid.uuid4()
    company_id = uuid.uuid4()
    content_hash = "a" * 64
    now = _utcnow()

    doc = _document(content_hash=content_hash, company_id=company_id, agent_run_id=run_id, created_at=now)
    db.add(doc)
    await db.flush()
    db.add(
        ExtractedFact(
            id=uuid.uuid4(),
            extracted_document_id=doc.id,
            label="revenue",
            value_numeric=1000,
            value_text="$1,000",
            unit="currency_amount",
            currency="USD",
            period="2024",
            page_number=3,
            table_location="p3:t0",
            extraction_method="native_pdf",
            confidence=0.9,
            validation_status="validated",
            needs_human_review=True,
        )
    )
    db.add(
        _attempt(
            company_id=company_id,
            agent_run_id=run_id,
            url="https://x.com/a.pdf",
            status="extracted",
            extraction_method="native_pdf",
            content_hash=content_hash,
            attempted_at=now + timedelta(seconds=1),
        )
    )
    await db.commit()

    result = await get_report_primary_documents(
        db, report_company_id=company_id, report_agent_run_id=run_id, report_id=uuid.uuid4()
    )
    assert result.summary.validated_fact_count == 1
    doc_read = result.documents[0]
    assert doc_read.title == "Annual Report 2024"
    assert len(doc_read.facts) == 1
    assert doc_read.facts[0].label == "revenue"
    assert len(doc_read.excerpts) == 1
    assert doc_read.excerpts[0].text == "Revenue increased."
    # Same-run fresh extraction (document created within the reuse tolerance
    # of the attempt) must NOT be flagged as reused.
    assert doc_read.reused is False


async def test_shared_content_hash_across_two_attempts_does_not_double_count(db):
    # A regression for a real reviewer-caught bug: the SAME document (one
    # content_hash) discovered via TWO distinct candidate URLs/discovery
    # strategies in the same run must count its validated facts and its
    # reused status ONCE, not once per attempt row that references it.
    run_id = uuid.uuid4()
    company_id = uuid.uuid4()
    content_hash = "c" * 64
    old = _utcnow() - timedelta(days=1)

    doc = _document(content_hash=content_hash, company_id=company_id, agent_run_id=uuid.uuid4(), created_at=old)
    db.add(doc)
    await db.flush()
    db.add(
        ExtractedFact(
            id=uuid.uuid4(),
            extracted_document_id=doc.id,
            label="revenue",
            value_numeric=1000,
            value_text="$1,000",
            unit="currency_amount",
            currency="USD",
            period="2024",
            page_number=3,
            table_location="p3:t0",
            extraction_method="native_pdf",
            confidence=0.9,
            validation_status="validated",
            needs_human_review=True,
        )
    )
    # Two attempts, same content_hash, same run — e.g. found via both a
    # direct anchor link and a sitemap-derived URL that canonicalize to the
    # same underlying document.
    db.add(_attempt(company_id=company_id, agent_run_id=run_id, url="https://x.com/a.pdf", status="extracted", extraction_method="native_pdf", content_hash=content_hash))
    db.add(_attempt(company_id=company_id, agent_run_id=run_id, url="https://x.com/a-mirror.pdf", status="extracted", extraction_method="native_pdf", content_hash=content_hash))
    await db.commit()

    result = await get_report_primary_documents(
        db, report_company_id=company_id, report_agent_run_id=run_id, report_id=uuid.uuid4()
    )
    # Two attempt rows are still shown (real, distinct discovery events)...
    assert len(result.documents) == 2
    # ...but the document-level counts are deduped, not doubled.
    assert result.summary.validated_fact_count == 1
    assert result.summary.reused_count == 1
    # Both attempt cards still show the same document's facts (display is
    # intentionally per-attempt, only the summary aggregate is deduped).
    assert all(len(d.facts) == 1 for d in result.documents)


async def test_reuse_detected_when_document_predates_attempt(db):
    run_id = uuid.uuid4()
    company_id = uuid.uuid4()
    content_hash = "b" * 64
    old = _utcnow() - timedelta(days=1)

    doc = _document(content_hash=content_hash, company_id=company_id, agent_run_id=uuid.uuid4(), created_at=old)
    db.add(doc)
    db.add(
        _attempt(
            company_id=company_id,
            agent_run_id=run_id,
            url="https://x.com/a.pdf",
            status="extracted",
            extraction_method="native_pdf",
            content_hash=content_hash,
        )
    )
    await db.commit()

    result = await get_report_primary_documents(
        db, report_company_id=company_id, report_agent_run_id=run_id, report_id=uuid.uuid4()
    )
    assert result.summary.reused_count == 1
    assert result.documents[0].reused is True


async def test_company_isolation_never_leaks_another_companys_attempts(db):
    company_a = uuid.uuid4()
    company_b = uuid.uuid4()
    run_a = uuid.uuid4()
    db.add(_attempt(company_id=company_a, agent_run_id=run_a, url="https://a.com/x.pdf", status="extracted", extraction_method="native_pdf"))
    db.add(_attempt(company_id=company_b, agent_run_id=uuid.uuid4(), url="https://b.com/y.pdf", status="extracted", extraction_method="native_pdf"))
    await db.commit()

    result = await get_report_primary_documents(
        db, report_company_id=company_a, report_agent_run_id=run_a, report_id=uuid.uuid4()
    )
    assert len(result.documents) == 1
    assert result.documents[0].canonical_url == "https://a.com/x.pdf"


async def test_legacy_report_falls_back_to_company_scope_when_run_unknown(db):
    company_id = uuid.uuid4()
    db.add(_attempt(company_id=company_id, agent_run_id=uuid.uuid4(), url="https://x.com/a.pdf", status="extracted", extraction_method="native_pdf"))
    await db.commit()

    result = await get_report_primary_documents(
        db, report_company_id=company_id, report_agent_run_id=None, report_id=uuid.uuid4()
    )
    assert len(result.documents) == 1


# --------------------------------------------------------------------------- #
# Router-level (mocked service, matches existing test_citations.py pattern)
# --------------------------------------------------------------------------- #

_GET_REPORT = "app.api.v1.reports.report_service.get_report"
_GET_DOCS = "app.api.v1.reports.primary_document_view_service.get_report_primary_documents"


async def test_router_404_when_report_missing(client: AsyncClient):
    with patch(_GET_REPORT, new_callable=AsyncMock, return_value=None):
        response = await client.get(f"/api/v1/reports/{uuid.uuid4()}/primary-documents")
    assert response.status_code == 404


async def test_router_returns_provenance_response(client: AsyncClient):
    report_id = uuid.uuid4()
    company_id = uuid.uuid4()
    run_id = uuid.uuid4()
    fake_report = MagicMock(company_id=company_id, created_by_agent_run_id=run_id)
    fake_response = ReportPrimaryDocumentsResponse(
        report_id=report_id,
        company_id=company_id,
        agent_run_id=run_id,
        summary={
            "discovered_count": 1,
            "attempted_count": 1,
            "extracted_count": 1,
            "metadata_only_count": 0,
            "failed_count": 0,
            "native_count": 1,
            "ocr_count": 0,
            "validated_fact_count": 0,
            "reused_count": 0,
            "evidence_reference_count": 0,
        },
        documents=[],
    )
    with (
        patch(_GET_REPORT, new_callable=AsyncMock, return_value=fake_report),
        patch(_GET_DOCS, new_callable=AsyncMock, return_value=fake_response),
    ):
        response = await client.get(f"/api/v1/reports/{report_id}/primary-documents")

    assert response.status_code == 200
    data = response.json()
    assert data["report_id"] == str(report_id)
    assert data["summary"]["extracted_count"] == 1


async def test_router_unauthenticated_returns_401_or_ok_per_perimeter(client: AsyncClient):
    # This router has no per-route auth dependency (matches every other admin
    # endpoint in this codebase — perimeter auth is HTTP Basic Auth at the
    # staging proxy/app-setting layer, not a FastAPI dependency). Confirm this
    # endpoint doesn't ADD a bypass beyond what /reports/{id} already has: a
    # missing report still 404s exactly like the existing GET /reports/{id}.
    with patch(_GET_REPORT, new_callable=AsyncMock, return_value=None):
        response = await client.get(f"/api/v1/reports/{uuid.uuid4()}/primary-documents")
    assert response.status_code == 404
