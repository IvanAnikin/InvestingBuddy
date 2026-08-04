"""Phase 32A Slice 5 (3c-i) — persist deep primary-document artifacts.

Exercises ``persist_primary_document_artifacts`` against a REAL in-memory SQLite
async database (``aiosqlite``) so real INSERT / SELECT + the new ExtractedDocument
/ ExtractedFact rows are genuinely persisted and read back (the shared conftest
uses a mock AsyncSession, which cannot exercise a WHERE clause / FK). The same
dialect-scoped ``JSONB -> JSON`` compiler shim as the Slice-3 tests lets
``Base.metadata.create_all`` build the Postgres-flavoured schema on SQLite.

No network / Azure. Nothing here touches auth, publishing, or app settings; the
gate flags are monkeypatched on / off per test and auto-restored.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

# --- Import every model module so Base.metadata is complete for create_all. ---
from app.core.config import settings as app_settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.agent_run import AgentRun
from app.models.company import Company
from app.models.extracted_document import ExtractedDocument, ExtractedFact
from app.services.extracted_document_service import (
    PersistResult,
    persist_primary_document_artifacts,
)
from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
from app.services.sources.extracted_fact_validator import ValidatedFact
from app.services.sources.primary_document_extractor import PrimaryDocumentExtraction

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Real async SQLite fixtures
# ---------------------------------------------------------------------------
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
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest.fixture
def flags_on(monkeypatch):
    """Turn BOTH gate flags ON for one test (auto-restored)."""
    monkeypatch.setattr(
        app_settings, "primary_document_ingestion_enabled", True, raising=False
    )
    monkeypatch.setattr(
        app_settings, "report_citation_persistence_enabled", True, raising=False
    )
    return app_settings


# ---------------------------------------------------------------------------
# Seed + artifact builders
# ---------------------------------------------------------------------------
async def _add_company(session, *, ticker: str, name: str) -> Company:
    company = Company(
        id=uuid.uuid4(),
        ticker=ticker,
        exchange="NASDAQ",
        name=name,
        country="US",
        sector="Technology",
        industry="Consumer Electronics",
        status="new",
    )
    session.add(company)
    await session.flush()
    return company


async def _add_run(session) -> AgentRun:
    run = AgentRun(
        id=uuid.uuid4(),
        workflow_name="company_analysis",
        status="completed",
    )
    session.add(run)
    await session.flush()
    return run


def _fact(
    *,
    label: str,
    value_numeric: float | None,
    validation_status: str = "validated",
    period: str | None = "FY2023",
    unit: str | None = "USD",
) -> ValidatedFact:
    return ValidatedFact(
        label=label,
        value_numeric=value_numeric,
        value_text=str(value_numeric) if value_numeric is not None else None,
        unit=unit,
        currency="USD",
        scale="millions",
        period=period,
        page_number=12,
        table_location="page=12;table=2;row=4;col=1",
        extraction_method="native_pdf",
        confidence=0.91,
        validation_status=validation_status,
        needs_human_review=True,
    )


def _artifact(
    *,
    source_url: str,
    content_hash: str,
    status: str = "extracted",
    facts: list[ValidatedFact] | None = None,
    title: str = "Annual Report 2023",
    document_type: str | None = "annual_report",
) -> PrimaryDocumentArtifact:
    extraction = None
    if content_hash:
        extraction = PrimaryDocumentExtraction(
            content_hash=content_hash,
            mime_type="application/pdf",
            extraction_method="native_pdf",
            status=status,
            page_count=180,
        )
    return PrimaryDocumentArtifact(
        source_url=source_url,
        document_type=document_type,
        title=title,
        retrieved_at=_utcnow(),
        status=status,
        extraction=extraction,
        validated_facts=facts or [],
    )


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
async def test_extracted_artifact_persists_document_and_validated_facts(
    session, flags_on
):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)
    artifact = _artifact(
        source_url="https://investor.apple.com/annual/2023.pdf",
        content_hash="a" * 64,
        facts=[
            _fact(label="Revenue", value_numeric=383285.0),
            _fact(label="Net income", value_numeric=96995.0),
            _fact(
                label="Some prose",
                value_numeric=None,
                validation_status="excerpt_only",
            ),
            _fact(label="Bad number", value_numeric=1.0, validation_status="rejected"),
        ],
    )

    result = await persist_primary_document_artifacts(
        session,
        artifacts=[artifact],
        company_id=company.id,
        agent_run_id=run.id,
        cfg=flags_on,
    )

    assert result.documents_created == 1
    assert result.documents_reused == 0
    assert result.facts_created == 2  # ONLY the two validated facts
    assert result.facts_deduped == 0
    assert await _count(session, ExtractedDocument) == 1
    assert await _count(session, ExtractedFact) == 2

    doc = (await session.execute(select(ExtractedDocument))).scalar_one()
    assert doc.content_hash == "a" * 64
    assert doc.canonical_url == "https://investor.apple.com/annual/2023.pdf"
    assert doc.status == "extracted"
    assert doc.source_tier == "T1_primary_filing"
    assert doc.mime_type == "application/pdf"
    assert doc.company_id == company.id
    assert doc.agent_run_id == run.id
    assert doc.blob_path is None

    facts = (await session.execute(select(ExtractedFact))).scalars().all()
    assert {f.validation_status for f in facts} == {"validated"}
    assert all(f.needs_human_review for f in facts)
    assert all(f.extracted_document_id == doc.id for f in facts)
    assert {f.label for f in facts} == {"Revenue", "Net income"}


async def test_excerpt_only_and_rejected_never_become_facts(session, flags_on):
    company = await _add_company(session, ticker="MSFT", name="Microsoft")
    run = await _add_run(session)
    artifact = _artifact(
        source_url="https://microsoft.com/ar.pdf",
        content_hash="b" * 64,
        facts=[
            _fact(label="Prose", value_numeric=None, validation_status="excerpt_only"),
            _fact(label="Rejected", value_numeric=5.0, validation_status="rejected"),
        ],
    )

    result = await persist_primary_document_artifacts(
        session,
        artifacts=[artifact],
        company_id=company.id,
        agent_run_id=run.id,
        cfg=flags_on,
    )

    # The document is real (status extracted) but carries NO structured facts.
    assert result.documents_created == 1
    assert result.facts_created == 0
    assert await _count(session, ExtractedDocument) == 1
    assert await _count(session, ExtractedFact) == 0


async def test_metadata_only_and_failed_are_not_persisted(session, flags_on):
    company = await _add_company(session, ticker="CFR", name="Richemont")
    run = await _add_run(session)
    artifacts = [
        _artifact(
            source_url="https://richemont.com/ir",
            content_hash="c" * 64,
            status="metadata_only",
            facts=[_fact(label="Revenue", value_numeric=1.0)],
        ),
        _artifact(
            source_url="https://richemont.com/scanned.pdf",
            content_hash="d" * 64,
            status="extraction_failed",
            facts=[_fact(label="Revenue", value_numeric=2.0)],
        ),
    ]

    result = await persist_primary_document_artifacts(
        session,
        artifacts=artifacts,
        company_id=company.id,
        agent_run_id=run.id,
        cfg=flags_on,
    )

    assert result.documents_created == 0
    assert result.facts_created == 0
    assert result.skipped == 2
    assert await _count(session, ExtractedDocument) == 0
    assert await _count(session, ExtractedFact) == 0


async def test_rerun_same_artifact_is_idempotent(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)

    def _fresh() -> PrimaryDocumentArtifact:
        return _artifact(
            source_url="https://investor.apple.com/annual/2023.pdf",
            content_hash="e" * 64,
            facts=[
                _fact(label="Revenue", value_numeric=383285.0),
                _fact(label="Net income", value_numeric=96995.0),
            ],
        )

    first = await persist_primary_document_artifacts(
        session,
        artifacts=[_fresh()],
        company_id=company.id,
        agent_run_id=run.id,
        cfg=flags_on,
    )
    assert first.documents_created == 1
    assert first.facts_created == 2

    second = await persist_primary_document_artifacts(
        session,
        artifacts=[_fresh()],
        company_id=company.id,
        agent_run_id=run.id,
        cfg=flags_on,
    )
    assert second.documents_created == 0
    assert second.documents_reused == 1
    assert second.facts_created == 0
    assert second.facts_deduped == 2

    # Still exactly one document + two facts after the re-run.
    assert await _count(session, ExtractedDocument) == 1
    assert await _count(session, ExtractedFact) == 2


async def test_different_content_same_url_makes_two_documents(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)
    url = "https://investor.apple.com/annual/latest.pdf"

    await persist_primary_document_artifacts(
        session,
        artifacts=[_artifact(source_url=url, content_hash="1" * 64)],
        company_id=company.id,
        agent_run_id=run.id,
        cfg=flags_on,
    )
    result = await persist_primary_document_artifacts(
        session,
        artifacts=[_artifact(source_url=url, content_hash="2" * 64)],
        company_id=company.id,
        agent_run_id=run.id,
        cfg=flags_on,
    )

    assert result.documents_created == 1  # different content_hash ⇒ new row
    assert await _count(session, ExtractedDocument) == 2


async def test_signed_and_redirected_variants_map_to_one_document(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)
    same_hash = "f" * 64

    signed = _artifact(
        source_url="https://cdn.apple.com/annual/2023.pdf?token=SECRET&sig=abc123",
        content_hash=same_hash,
    )
    redirected = _artifact(
        source_url="https://investor.apple.com/annual/2023.pdf",
        content_hash=same_hash,
    )

    first = await persist_primary_document_artifacts(
        session,
        artifacts=[signed],
        company_id=company.id,
        agent_run_id=run.id,
        cfg=flags_on,
    )
    second = await persist_primary_document_artifacts(
        session,
        artifacts=[redirected],
        company_id=company.id,
        agent_run_id=run.id,
        cfg=flags_on,
    )

    assert first.documents_created == 1
    assert second.documents_created == 0
    assert second.documents_reused == 1
    assert await _count(session, ExtractedDocument) == 1

    # The stored canonical URL carries NO credential-bearing query residue.
    doc = (await session.execute(select(ExtractedDocument))).scalar_one()
    assert "token" not in doc.canonical_url.lower()
    assert "secret" not in doc.canonical_url.lower()
    assert "sig=" not in doc.canonical_url.lower()


async def test_no_cross_company_linkage(session, flags_on):
    company_a = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    company_b = await _add_company(session, ticker="MSFT", name="Microsoft")
    run_a = await _add_run(session)
    run_b = await _add_run(session)

    await persist_primary_document_artifacts(
        session,
        artifacts=[
            _artifact(source_url="https://apple.com/a.pdf", content_hash="a" * 64)
        ],
        company_id=company_a.id,
        agent_run_id=run_a.id,
        cfg=flags_on,
    )
    await persist_primary_document_artifacts(
        session,
        artifacts=[
            _artifact(source_url="https://microsoft.com/b.pdf", content_hash="b" * 64)
        ],
        company_id=company_b.id,
        agent_run_id=run_b.id,
        cfg=flags_on,
    )

    docs = (await session.execute(select(ExtractedDocument))).scalars().all()
    assert len(docs) == 2
    by_company = {d.company_id: d for d in docs}
    assert set(by_company) == {company_a.id, company_b.id}
    assert by_company[company_a.id].agent_run_id == run_a.id
    assert by_company[company_b.id].agent_run_id == run_b.id
    # Neither document is linked to the other company.
    assert by_company[company_a.id].company_id != company_b.id
    assert by_company[company_b.id].company_id != company_a.id


class _ExplodingSession:
    """A session stand-in that fails if ANY attribute is touched.

    Proves the OFF path issues no query and adds no row before returning.
    """

    def __getattr__(self, name):  # noqa: ANN001, ANN204
        raise AssertionError(f"session.{name} used while a gate flag was OFF")


@pytest.mark.parametrize(
    ("ingestion", "citation"),
    [(False, True), (True, False), (False, False)],
)
async def test_either_flag_off_writes_nothing_and_issues_no_query(
    monkeypatch, ingestion, citation
):
    monkeypatch.setattr(
        app_settings, "primary_document_ingestion_enabled", ingestion, raising=False
    )
    monkeypatch.setattr(
        app_settings, "report_citation_persistence_enabled", citation, raising=False
    )
    artifact = _artifact(
        source_url="https://apple.com/a.pdf",
        content_hash="a" * 64,
        facts=[_fact(label="Revenue", value_numeric=1.0)],
    )

    result = await persist_primary_document_artifacts(
        _ExplodingSession(),  # type: ignore[arg-type]
        artifacts=[artifact],
        company_id=uuid.uuid4(),
        agent_run_id=uuid.uuid4(),
        cfg=app_settings,
    )

    # Zero of everything — the session was never touched (else it would raise).
    assert result == PersistResult()
