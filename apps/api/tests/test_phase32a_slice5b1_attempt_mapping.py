"""Phase 32A Slice 5B.1 — artifact → durable ingestion-attempt mapping.

The core Slice 5B fix under test: Slice 5A persisted a row ONLY for a fully
``extracted`` document, so every FAILED ingestion attempt vanished — seven
issuers' worth of attempts left ``extracted_documents`` at 0/0 with nothing
explaining why. Every artifact must now map onto exactly one honest attempt
record, and a FAILED artifact must still leave a row.

Three layers are covered:
  1. ``ingestion_attempts`` mapping (pure, no DB) — status vocabulary, sanitized
     failure codes, telemetry passthrough;
  2. the composed write against a REAL in-memory SQLite async database, proving a
     failure genuinely lands a row (and that either gate flag OFF issues NO query);
  3. the final-report wiring, proving the generator records attempts alongside the
     extracted documents inside the SAME protective guard and lineage.

No network, no Azure, no live services. Gate flags are monkeypatched per test.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import settings as app_settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import document_ingestion_attempt as _attempt  # noqa: F401
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.agent_run import AgentRun
from app.models.company import Company
from app.models.document_ingestion_attempt import (
    ALL_FAILURE_CODES,
    ALL_STATUSES,
    DocumentIngestionAttempt,
)
from app.services.document_ingestion_attempt_service import record_ingestion_attempts
from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
from app.services.sources.ingestion_attempts import (
    SOURCE_TIER_PRIMARY_FILING,
    SOURCE_TYPE_COMPANY_IR,
    SOURCE_TYPE_SEC_FILING,
    artifact_to_attempt,
    artifacts_to_attempts,
    attempts_for_primary_documents,
)
from app.services.sources.ingestion_status import (
    ATTEMPT_ENCRYPTED,
    ATTEMPT_EXTRACTED,
    ATTEMPT_EXTRACTION_FAILED,
    ATTEMPT_MALFORMED,
    ATTEMPT_METADATA_ONLY,
    ATTEMPT_PASSWORD_PROTECTED,
    ATTEMPT_REJECTED_SECURITY,
    ATTEMPT_TIMEOUT,
    ATTEMPT_UNSUPPORTED,
    FAILURE_BLOCKED_PRIVATE_IP,
    FAILURE_ENCRYPTED_PDF,
    FAILURE_FETCH_TIMEOUT,
    FAILURE_MALFORMED_PDF,
    FAILURE_NOT_A_PDF,
    FAILURE_PASSWORD_PROTECTED_PDF,
    FAILURE_SCANNED_NO_TEXT,
    FAILURE_UNKNOWN,
)
from app.services.sources.primary_document_extractor import (
    METHOD_HTML,
    STATUS_EXTRACTED,
    STATUS_EXTRACTION_FAILED,
    STATUS_METADATA_ONLY,
    PrimaryDocumentExtraction,
)
from app.services.sources.sec_filing_documents import STRATEGY_SEC_ACCESSION

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _extraction(
    *,
    status: str = STATUS_EXTRACTED,
    failure_code: str | None = None,
    page_count: int | None = 12,
    content_hash: str = "a" * 64,
) -> PrimaryDocumentExtraction:
    return PrimaryDocumentExtraction(
        content_hash=content_hash,
        mime_type="text/html",
        extraction_method=METHOD_HTML,
        status=status,
        page_count=page_count,
        failure_code=failure_code,
    )


def _artifact(
    *,
    status: str = STATUS_EXTRACTION_FAILED,
    failure_code: str | None = None,
    url: str = "https://investor.example.test/ar-2024.pdf",
    **kwargs: Any,
) -> PrimaryDocumentArtifact:
    return PrimaryDocumentArtifact(
        source_url=url,
        status=status,
        failure_code=failure_code,
        **kwargs,
    )


def _map(artifact: PrimaryDocumentArtifact):
    return artifact_to_attempt(
        artifact,
        source_type=SOURCE_TYPE_COMPANY_IR,
        source_tier=SOURCE_TIER_PRIMARY_FILING,
    )


# ---------------------------------------------------------------------------
# 1. Status vocabulary — a failure says WHAT failed, not just "it failed"
# ---------------------------------------------------------------------------
class TestAttemptStatusMapping:
    def test_extracted_artifact_maps_to_extracted(self) -> None:
        record = _map(
            _artifact(status=STATUS_EXTRACTED, extraction=_extraction())
        )
        assert record.status == ATTEMPT_EXTRACTED
        # A success carries NO failure code (not an 'unknown' placeholder).
        assert record.failure_code is None

    def test_scanned_document_maps_to_metadata_only(self) -> None:
        record = _map(
            _artifact(
                status=STATUS_METADATA_ONLY,
                failure_code=FAILURE_SCANNED_NO_TEXT,
                extraction=_extraction(
                    status=STATUS_METADATA_ONLY,
                    failure_code=FAILURE_SCANNED_NO_TEXT,
                ),
            )
        )
        assert record.status == ATTEMPT_METADATA_ONLY
        assert record.failure_code == FAILURE_SCANNED_NO_TEXT

    def test_encrypted_pdf_maps_to_encrypted(self) -> None:
        record = _map(_artifact(failure_code=FAILURE_ENCRYPTED_PDF))
        assert record.status == ATTEMPT_ENCRYPTED
        assert record.failure_code == FAILURE_ENCRYPTED_PDF

    def test_password_protected_pdf_maps_to_password_protected(self) -> None:
        record = _map(_artifact(failure_code=FAILURE_PASSWORD_PROTECTED_PDF))
        assert record.status == ATTEMPT_PASSWORD_PROTECTED

    def test_malformed_pdf_maps_to_malformed(self) -> None:
        record = _map(_artifact(failure_code=FAILURE_MALFORMED_PDF))
        assert record.status == ATTEMPT_MALFORMED

    def test_blocked_private_ip_maps_to_rejected_security(self) -> None:
        record = _map(_artifact(failure_code=FAILURE_BLOCKED_PRIVATE_IP))
        assert record.status == ATTEMPT_REJECTED_SECURITY

    def test_fetch_timeout_maps_to_timeout(self) -> None:
        record = _map(_artifact(failure_code=FAILURE_FETCH_TIMEOUT))
        assert record.status == ATTEMPT_TIMEOUT

    def test_not_a_pdf_maps_to_unsupported(self) -> None:
        record = _map(
            _artifact(status=STATUS_METADATA_ONLY, failure_code=FAILURE_NOT_A_PDF)
        )
        assert record.status == ATTEMPT_UNSUPPORTED

    def test_absent_failure_code_maps_to_extraction_failed(self) -> None:
        record = _map(_artifact(failure_code=None))
        assert record.status == ATTEMPT_EXTRACTION_FAILED
        assert record.failure_code is None

    def test_unknown_failure_code_maps_to_extraction_failed(self) -> None:
        record = _map(_artifact(failure_code=FAILURE_UNKNOWN))
        assert record.status == ATTEMPT_EXTRACTION_FAILED
        assert record.failure_code == FAILURE_UNKNOWN

    def test_every_mapped_status_is_in_the_closed_vocabulary(self) -> None:
        for code in (*ALL_FAILURE_CODES, None, "not-a-code"):
            record = _map(_artifact(failure_code=code))
            assert record.status in ALL_STATUSES


# ---------------------------------------------------------------------------
# 2. Sanitization — raw provider / exception text can never survive
# ---------------------------------------------------------------------------
class TestFailureCodeSanitization:
    def test_raw_exception_string_becomes_unknown(self) -> None:
        raw = "ConnectionError: [Errno 111] connect to 10.0.0.5:443 refused"
        record = _map(_artifact(failure_code=raw))
        assert record.failure_code == FAILURE_UNKNOWN
        assert record.status == ATTEMPT_EXTRACTION_FAILED
        # The raw text is nowhere on the record.
        assert raw not in str(record)

    def test_url_bearing_failure_text_becomes_unknown(self) -> None:
        record = _map(
            _artifact(failure_code="blocked https://evil.test/x?api_token=SECRET")
        )
        assert record.failure_code == FAILURE_UNKNOWN
        assert "SECRET" not in str(record)


# ---------------------------------------------------------------------------
# 3. Telemetry passthrough — bounded, secret-free, honest about "unknown"
# ---------------------------------------------------------------------------
class TestTelemetryMapping:
    def test_provenance_and_timings_are_carried(self) -> None:
        artifact = _artifact(
            status=STATUS_EXTRACTED,
            document_type="pdf",
            doc_kind="annual_report",
            discovery_strategy="next_data",
            http_status_class="2xx",
            content_hash="b" * 64,
            fetch_ms=120,
            extraction_ms=880,
            extraction=_extraction(page_count=42),
        )
        record = _map(artifact)
        assert record.doc_kind == "annual_report"
        assert record.discovery_strategy == "next_data"
        assert record.http_status_class == "2xx"
        assert record.mime_type == "pdf"
        assert record.extraction_method == METHOD_HTML
        assert record.page_count == 42
        assert record.content_hash == "b" * 64
        assert record.fetch_ms == 120
        assert record.extraction_ms == 880
        assert record.total_ms == 1000

    def test_content_hash_falls_back_to_the_extraction(self) -> None:
        record = _map(
            _artifact(status=STATUS_EXTRACTED, extraction=_extraction(content_hash="c" * 64))
        )
        assert record.content_hash == "c" * 64

    def test_unknown_fields_stay_none_never_guessed(self) -> None:
        record = _map(_artifact())
        assert record.doc_kind is None
        assert record.discovery_strategy is None
        assert record.http_status_class is None
        assert record.page_count is None
        assert record.total_ms is None

    def test_batch_mapping_is_one_record_per_artifact(self) -> None:
        records = artifacts_to_attempts(
            [_artifact(url="https://a.test/1.pdf"), _artifact(url="https://a.test/2.pdf")],
            source_type=SOURCE_TYPE_COMPANY_IR,
            source_tier=SOURCE_TIER_PRIMARY_FILING,
        )
        assert len(records) == 2

    def test_empty_batch_maps_to_empty_list(self) -> None:
        assert artifacts_to_attempts(
            None, source_type="x", source_tier="y"
        ) == []
        assert attempts_for_primary_documents(None) == []

    def test_sec_artifacts_are_attributed_to_the_sec_source(self) -> None:
        records = attempts_for_primary_documents(
            [
                _artifact(url="https://investor.example.test/ar.pdf"),
                _artifact(
                    url="https://www.sec.gov/Archives/edgar/data/320193/x/aapl.htm",
                    discovery_strategy=STRATEGY_SEC_ACCESSION,
                ),
            ]
        )
        assert [r.source_type for r in records] == [
            SOURCE_TYPE_COMPANY_IR,
            SOURCE_TYPE_SEC_FILING,
        ]
        assert {r.source_tier for r in records} == {SOURCE_TIER_PRIMARY_FILING}


# ---------------------------------------------------------------------------
# 1b. Connector provenance — the artifact learns WHAT it was and HOW it was found
# ---------------------------------------------------------------------------
_IR_DOC_URL = "https://www.richemont.com/reports/annual-report-2024.pdf"
_IR_PAGE_BODY = (
    "<html><body><h1>Reports</h1>"
    f'<a href="{_IR_DOC_URL}">Annual Report 2024</a>'
    "</body></html>"
)


def _ir_page_fetcher(*, body: str | None):
    from app.services.sources.safe_web_fetcher import SafeFetchResult, SafeLink

    async def _fetch(url, *, allowed_domains, keywords, fallback_keywords=()):  # noqa: ANN001
        return SafeFetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            links=[
                SafeLink(url=_IR_DOC_URL, text="Annual Report 2024", is_document=True)
            ],
            body_html=body,
        )

    return _fetch


def _stub_deep_extractor():
    async def _extract(url, **kwargs: Any) -> PrimaryDocumentArtifact:  # noqa: ANN001
        return PrimaryDocumentArtifact(
            source_url=url,
            document_type="pdf",
            status=STATUS_EXTRACTED,
            extraction=_extraction(content_hash="d" * 64),
            fetch_ms=5,
            extraction_ms=7,
        )

    return _extract


class TestConnectorStampsProvenance:
    async def _run(self, *, body: str | None):
        from app.services.sources.connector_base import CompanyContext, QueryContext
        from app.services.sources.connectors.company_ir import CompanyIrConnector
        from app.services.sources.verified_issuer_sources import (
            get_verified_issuer_source,
        )

        conn = CompanyIrConnector(
            verified_source=get_verified_issuer_source("CFR", "SW"),
            page_fetcher=_ir_page_fetcher(body=body),
            primary_document_extractor=_stub_deep_extractor(),
        )
        await conn.fetch_filings(
            CompanyContext(ticker="CFR", exchange="SW"), QueryContext(max_items=5)
        )
        return conn.collected_primary_document_artifacts

    async def test_classified_candidate_carries_kind_and_strategy(self) -> None:
        artifacts = await self._run(body=_IR_PAGE_BODY)
        assert len(artifacts) == 1
        assert artifacts[0].doc_kind == "annual_report"
        assert artifacts[0].discovery_strategy == "anchors"

    async def test_content_hash_is_carried_from_the_extraction(self) -> None:
        artifacts = await self._run(body=_IR_PAGE_BODY)
        assert artifacts[0].content_hash == "d" * 64

    async def test_unclassified_candidate_stays_none_never_guessed(self) -> None:
        # No page body ⇒ the discovery layer never ran ⇒ nothing is known about
        # the candidate's kind. That is recorded as unknown, not invented.
        artifacts = await self._run(body=None)
        assert len(artifacts) == 1
        assert artifacts[0].doc_kind is None
        assert artifacts[0].discovery_strategy is None

    async def test_provenance_reaches_the_attempt_record(self) -> None:
        artifacts = await self._run(body=_IR_PAGE_BODY)
        record = attempts_for_primary_documents(artifacts)[0]
        assert record.doc_kind == "annual_report"
        assert record.discovery_strategy == "anchors"
        assert record.source_type == SOURCE_TYPE_COMPANY_IR
        assert record.status == ATTEMPT_EXTRACTED


# ---------------------------------------------------------------------------
# Real async SQLite fixtures (layer 2)
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
async def session(engine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s


@pytest.fixture
def flags_on(monkeypatch):
    monkeypatch.setattr(
        app_settings, "primary_document_ingestion_enabled", True, raising=False
    )
    monkeypatch.setattr(
        app_settings, "report_citation_persistence_enabled", True, raising=False
    )
    return app_settings


class _ExplodingSession:
    """Fails if ANY attribute is touched — proves the dark path issues no query."""

    def __getattr__(self, name):  # noqa: ANN001, ANN204
        raise AssertionError(f"session.{name} used while a gate flag was OFF")


async def _seed(session):
    company = Company(
        id=uuid.uuid4(),
        ticker="AAPL",
        exchange="NASDAQ",
        name="Apple Inc.",
        country="US",
        sector="Technology",
        industry="Consumer Electronics",
        status="new",
    )
    run = AgentRun(
        id=uuid.uuid4(), workflow_name="company_analysis", status="completed"
    )
    session.add_all([company, run])
    await session.flush()
    return company, run


async def _count(session) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(DocumentIngestionAttempt)
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# 2. The core Slice 5B fix — a FAILED attempt still leaves a row
# ---------------------------------------------------------------------------
class TestFailedAttemptIsPersisted:
    async def test_failed_only_run_still_writes_an_attempt_row(
        self, session, flags_on
    ) -> None:
        company, run = await _seed(session)
        # The exact Slice-5A blind spot: the ONLY artifact failed, so Slice 5A
        # wrote nothing at all.
        artifacts = [
            _artifact(
                url="https://www.richemont.com/media/annual-report.pdf",
                failure_code=FAILURE_ENCRYPTED_PDF,
                document_type="pdf",
                http_status_class="2xx",
                fetch_ms=430,
                doc_kind="annual_report",
                discovery_strategy="anchors",
            )
        ]

        written = await record_ingestion_attempts(
            session,
            company_id=company.id,
            agent_run_id=run.id,
            attempts=attempts_for_primary_documents(artifacts),
            cfg=flags_on,
        )

        assert written == 1
        assert await _count(session) == 1
        row = (
            await session.execute(select(DocumentIngestionAttempt))
        ).scalar_one()
        assert row.status == ATTEMPT_ENCRYPTED
        assert row.failure_code == FAILURE_ENCRYPTED_PDF
        assert row.company_id == company.id
        assert row.agent_run_id == run.id
        assert row.doc_kind == "annual_report"
        assert row.discovery_strategy == "anchors"
        assert row.http_status_class == "2xx"
        # No document body, no excerpt, no figure is stored on an attempt.
        assert row.content_hash is None

    async def test_mixed_run_records_success_and_failure_side_by_side(
        self, session, flags_on
    ) -> None:
        company, run = await _seed(session)
        artifacts = [
            _artifact(
                url="https://www.sec.gov/Archives/edgar/data/320193/x/aapl-10k.htm",
                status=STATUS_EXTRACTED,
                document_type="html",
                discovery_strategy=STRATEGY_SEC_ACCESSION,
                extraction=_extraction(),
            ),
            _artifact(
                url="https://investor.example.test/ar-2024.pdf",
                failure_code=FAILURE_FETCH_TIMEOUT,
            ),
        ]
        written = await record_ingestion_attempts(
            session,
            company_id=company.id,
            agent_run_id=run.id,
            attempts=attempts_for_primary_documents(artifacts),
            cfg=flags_on,
        )
        assert written == 2
        statuses = {
            r.status
            for r in (
                await session.execute(select(DocumentIngestionAttempt))
            ).scalars()
        }
        assert statuses == {ATTEMPT_EXTRACTED, ATTEMPT_TIMEOUT}

    async def test_master_flag_off_writes_nothing_and_issues_no_query(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            app_settings, "primary_document_ingestion_enabled", False, raising=False
        )
        monkeypatch.setattr(
            app_settings, "report_citation_persistence_enabled", True, raising=False
        )
        written = await record_ingestion_attempts(
            _ExplodingSession(),  # type: ignore[arg-type]
            company_id=uuid.uuid4(),
            agent_run_id=uuid.uuid4(),
            attempts=attempts_for_primary_documents(
                [_artifact(failure_code=FAILURE_ENCRYPTED_PDF)]
            ),
            cfg=app_settings,
        )
        assert written == 0


# ---------------------------------------------------------------------------
# 3. Final-report wiring — attempts are recorded alongside extracted documents
# ---------------------------------------------------------------------------
class _Nested:
    """Stand-in for ``db.begin_nested()`` (a real SAVEPOINT context manager)."""

    async def __aenter__(self) -> "_Nested":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.begin_nested = MagicMock(return_value=_Nested())
    return db


class _Council:
    llm_used = True

    def __init__(self, artifacts: list[PrimaryDocumentArtifact]) -> None:
        self.primary_document_artifacts = artifacts


async def _save_draft(db, council, *, company_id, agent_run_id):
    from app.services.final_report_generator import (
        _save_final_report_draft,
        run_safety_gate,
    )

    return await _save_final_report_draft(
        db,
        report_content={"executive_summary": {"value": "Internal research draft."}},
        safety_result=run_safety_gate({}),
        schema_validation={"is_valid": False, "errors": [], "warnings": []},
        source_summary={},
        scorecard_id=None,
        company_name="Richemont",
        ticker="CFR",
        source_report_id=None,
        company_id=company_id,
        created_by_agent_run_id=agent_run_id,
        council_result=council,
    )


class TestFinalReportWiring:
    async def test_failed_artifact_is_recorded_with_report_lineage(
        self, monkeypatch
    ) -> None:
        import app.services.final_report_generator as frg

        monkeypatch.setattr(
            frg.settings, "primary_document_ingestion_enabled", True, raising=False
        )
        monkeypatch.setattr(
            frg.settings, "report_citation_persistence_enabled", True, raising=False
        )
        recorded: dict[str, Any] = {}

        async def _fake_record(session, **kwargs):  # noqa: ANN001
            recorded.update(kwargs)
            return len(kwargs.get("attempts") or [])

        monkeypatch.setattr(frg, "record_ingestion_attempts", _fake_record)
        monkeypatch.setattr(
            frg, "persist_primary_document_artifacts", AsyncMock(return_value=MagicMock())
        )
        monkeypatch.setattr(frg, "_persist_council_evidence_citations", AsyncMock(return_value=(0, 0)))

        company_id, run_id = uuid.uuid4(), uuid.uuid4()
        council = _Council([_artifact(failure_code=FAILURE_ENCRYPTED_PDF)])
        await _save_draft(_mock_db(), council, company_id=company_id, agent_run_id=run_id)

        assert recorded["company_id"] == company_id
        assert recorded["agent_run_id"] == run_id
        attempts = recorded["attempts"]
        assert len(attempts) == 1
        assert attempts[0].status == ATTEMPT_ENCRYPTED

    async def test_master_flag_off_never_records_an_attempt(self, monkeypatch) -> None:
        import app.services.final_report_generator as frg

        monkeypatch.setattr(
            frg.settings, "primary_document_ingestion_enabled", False, raising=False
        )
        monkeypatch.setattr(
            frg.settings, "report_citation_persistence_enabled", True, raising=False
        )
        called = MagicMock()
        monkeypatch.setattr(frg, "record_ingestion_attempts", AsyncMock(side_effect=called))
        monkeypatch.setattr(frg, "_persist_council_evidence_citations", AsyncMock(return_value=(0, 0)))

        council = _Council([_artifact(failure_code=FAILURE_ENCRYPTED_PDF)])
        await _save_draft(
            _mock_db(), council, company_id=uuid.uuid4(), agent_run_id=uuid.uuid4()
        )
        called.assert_not_called()
