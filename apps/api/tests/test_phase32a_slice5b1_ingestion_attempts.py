"""Phase 32A Slice 5B.1 — durable record of EVERY document ingestion attempt.

Exercises ``document_ingestion_attempt_service`` against a REAL in-memory SQLite
async database (``aiosqlite``) so real INSERT / UPDATE / SELECT are genuinely
executed and read back (the shared conftest uses a mock AsyncSession, which
cannot exercise a WHERE clause / FK). Mirrors the Slice-5A persistence tests,
including the dialect-scoped ``JSONB -> JSON`` compiler shim that lets
``Base.metadata.create_all`` build the Postgres-flavoured schema on SQLite.

The core regression under test: Slice 5A persisted NOTHING for a failed
ingestion attempt. A failure must now leave an honest, bounded, secret-free row.

No network / Azure. Nothing here touches auth, publishing, or app settings; the
gate flags are monkeypatched on / off per test and auto-restored.
"""

from __future__ import annotations

import uuid
from typing import Any

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
from app.services.document_ingestion_attempt_service import (
    IngestionAttemptRecord,
    http_status_class,
    load_attempt_summary,
    record_ingestion_attempts,
    sanitize_failure_code,
    url_hash_of,
)

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


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
# Seed + record builders
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


def _record(
    *,
    url: str = "https://investor.apple.com/annual/2023.pdf",
    status: str = "discovered",
    failure_code: str | None = None,
    **kwargs: Any,
) -> IngestionAttemptRecord:
    return IngestionAttemptRecord(
        canonical_url=url,
        source_type="company_ir_primary_document",
        source_tier="T1_primary_filing",
        status=status,
        failure_code=failure_code,
        **kwargs,
    )


async def _count(session) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(DocumentIngestionAttempt)
        )
    ).scalar_one()


async def _rows(session) -> list[DocumentIngestionAttempt]:
    return list(
        (await session.execute(select(DocumentIngestionAttempt))).scalars().all()
    )


class _ExplodingSession:
    """A session stand-in that fails if ANY attribute is touched.

    Proves the OFF path issues no query and adds no row before returning.
    """

    def __getattr__(self, name):  # noqa: ANN001, ANN204
        raise AssertionError(f"session.{name} used while a gate flag was OFF")


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------
class TestMigration014:
    def _load_migration(self) -> Any:
        import importlib.util
        import pathlib

        migration_path = (
            pathlib.Path(__file__).parent.parent
            / "alembic"
            / "versions"
            / "014_add_document_ingestion_attempts.py"
        )
        spec = importlib.util.spec_from_file_location("migration_014", migration_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module

    def test_migration_014_importable(self) -> None:
        module = self._load_migration()
        assert hasattr(module, "upgrade")
        assert hasattr(module, "downgrade")
        assert module.revision == "014"
        assert module.down_revision == "013"

    def test_migration_014_upgrade_callable(self) -> None:
        module = self._load_migration()
        assert callable(module.upgrade)
        assert callable(module.downgrade)

    def test_migration_014_columns_match_the_orm_model(self, monkeypatch) -> None:
        """The migration and the ORM model must be column-for-column identical.

        Migration ``014`` has never been applied anywhere, so a new column is
        added to it IN PLACE rather than as an ``015`` — which only stays safe
        while the two definitions cannot drift.
        """
        import sqlalchemy as sa

        from alembic import op

        module = self._load_migration()
        captured: dict[str, Any] = {}

        def _fake_create_table(name: str, *args: Any, **kw: Any) -> None:
            captured[name] = [a for a in args if isinstance(a, sa.Column)]

        monkeypatch.setattr(op, "create_table", _fake_create_table)
        monkeypatch.setattr(op, "create_index", lambda *a, **kw: None)
        module.upgrade()

        migration_columns = {c.name for c in captured["document_ingestion_attempts"]}
        orm_columns = set(DocumentIngestionAttempt.__table__.columns.keys())
        assert migration_columns == orm_columns
        # The Slice 5B.1 review addition specifically.
        assert "pinned" in migration_columns
        pinned = next(
            c for c in captured["document_ingestion_attempts"] if c.name == "pinned"
        )
        assert isinstance(pinned.type, sa.Boolean)
        assert pinned.nullable is True


# ---------------------------------------------------------------------------
# Gating — either flag OFF ⇒ no query, no row
# ---------------------------------------------------------------------------
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

    written = await record_ingestion_attempts(
        _ExplodingSession(),  # type: ignore[arg-type]
        company_id=uuid.uuid4(),
        agent_run_id=uuid.uuid4(),
        attempts=[_record()],
        cfg=app_settings,
    )

    # Zero rows — the session was never touched (else it would raise).
    assert written == 0


async def test_master_flag_on_but_persistence_off_writes_nothing(monkeypatch, session):
    monkeypatch.setattr(
        app_settings, "primary_document_ingestion_enabled", True, raising=False
    )
    monkeypatch.setattr(
        app_settings, "report_citation_persistence_enabled", False, raising=False
    )
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)

    written = await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[_record()],
        cfg=app_settings,
    )

    assert written == 0
    assert await _count(session) == 0


# ---------------------------------------------------------------------------
# Happy path + the core Slice-5B fix (failures ARE persisted)
# ---------------------------------------------------------------------------
async def test_discovered_attempt_is_written(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)

    written = await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[
            _record(
                status="discovered",
                doc_kind="annual_report",
                discovery_strategy="static_link",
            )
        ],
        cfg=flags_on,
    )

    assert written == 1
    assert await _count(session) == 1
    row = (await _rows(session))[0]
    assert row.status == "discovered"
    assert row.company_id == company.id
    assert row.agent_run_id == run.id
    assert row.canonical_url == "https://investor.apple.com/annual/2023.pdf"
    assert row.url_hash == url_hash_of("https://investor.apple.com/annual/2023.pdf")
    assert row.source_tier == "T1_primary_filing"
    assert row.doc_kind == "annual_report"
    assert row.discovery_strategy == "static_link"
    assert row.failure_code is None
    assert row.attempted_at is not None


async def test_failed_attempt_is_persisted(session, flags_on):
    """THE Slice-5B fix: Slice 5A persisted NOTHING for a failed attempt."""
    company = await _add_company(session, ticker="CFR", name="Richemont")
    run = await _add_run(session)

    written = await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[
            _record(
                url="https://internal.example.com/report.pdf",
                status="rejected_security",
                failure_code="blocked_private_ip",
                fetch_ms=12,
                total_ms=12,
            )
        ],
        cfg=flags_on,
    )

    assert written == 1
    assert await _count(session) == 1
    row = (await _rows(session))[0]
    assert row.status == "rejected_security"
    assert row.failure_code == "blocked_private_ip"
    assert row.company_id == company.id
    assert row.fetch_ms == 12
    assert row.total_ms == 12
    # A failure never invents extraction output.
    assert row.content_hash is None
    assert row.page_count is None
    assert row.extraction_method is None


async def test_every_status_in_the_closed_vocabulary_is_accepted(session, flags_on):
    company = await _add_company(session, ticker="MSFT", name="Microsoft")
    run = await _add_run(session)

    written = await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[
            _record(url=f"https://microsoft.com/{status}.pdf", status=status)
            for status in ALL_STATUSES
        ],
        cfg=flags_on,
    )

    assert written == len(ALL_STATUSES)
    assert await _count(session) == len(ALL_STATUSES)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
async def test_same_url_same_run_updates_in_place(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)
    url = "https://investor.apple.com/annual/2023.pdf"

    first = await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[_record(url=url, status="discovered")],
        cfg=flags_on,
    )
    row_id = (await _rows(session))[0].id

    second = await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[
            _record(
                url=url,
                status="extraction_failed",
                failure_code="scanned_no_text",
                page_count=180,
                extraction_method="native_pdf",
            )
        ],
        cfg=flags_on,
    )

    assert first == 1
    assert second == 1
    assert await _count(session) == 1  # UPDATED, not appended
    row = (await _rows(session))[0]
    assert row.id == row_id
    assert row.status == "extraction_failed"
    assert row.failure_code == "scanned_no_text"
    assert row.page_count == 180
    assert row.extraction_method == "native_pdf"


async def test_duplicate_url_within_one_batch_writes_one_row(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)
    url = "https://investor.apple.com/annual/2023.pdf"

    await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[
            _record(url=url, status="discovered"),
            _record(url=url, status="fetched"),
        ],
        cfg=flags_on,
    )

    assert await _count(session) == 1
    assert (await _rows(session))[0].status == "fetched"


async def test_null_company_and_run_still_update_one_row(session, flags_on):
    """PR-review nit 8: the pre-query — not the UNIQUE constraint — is the guarantee.

    PostgreSQL NULLs never collide inside a UNIQUE constraint, so a row with a
    NULL company_id AND a NULL agent_run_id is NOT protected by
    ``uq_document_ingestion_attempts_run_url``. The writer's pre-query must catch
    it instead, or an unattributed attempt would append a duplicate every run.
    """
    url = "https://investor.apple.com/annual/2023.pdf"

    first = await record_ingestion_attempts(
        session,
        company_id=None,
        agent_run_id=None,
        attempts=[_record(url=url, status="discovered")],
        cfg=flags_on,
    )
    row_id = (await _rows(session))[0].id

    second = await record_ingestion_attempts(
        session,
        company_id=None,
        agent_run_id=None,
        attempts=[_record(url=url, status="metadata_only", failure_code="scanned_no_text")],
        cfg=flags_on,
    )

    assert first == 1 and second == 1
    assert await _count(session) == 1, "NULL lineage appended a duplicate row"
    row = (await _rows(session))[0]
    assert row.id == row_id  # UPDATED in place
    assert row.company_id is None and row.agent_run_id is None
    assert row.status == "metadata_only"
    assert row.failure_code == "scanned_no_text"


async def test_pinned_is_persisted_including_an_honest_false(session, flags_on):
    """Blocker 3: the pinning outcome is stored, not silently dropped."""
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)

    await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[
            _record(url="https://investor.apple.com/a.pdf", status="extracted", pinned=True),
            _record(url="https://investor.apple.com/b.pdf", status="extracted", pinned=False),
            _record(url="https://investor.apple.com/c.pdf", status="discovered"),
        ],
        cfg=flags_on,
    )

    by_url = {r.canonical_url: r for r in await _rows(session)}
    assert by_url["https://investor.apple.com/a.pdf"].pinned is True
    # False is an honest "not pinned" and must survive as False, not become NULL.
    assert by_url["https://investor.apple.com/b.pdf"].pinned is False
    # No fetch attempted ⇒ unknown ⇒ NULL, never a claim either way.
    assert by_url["https://investor.apple.com/c.pdf"].pinned is None


async def test_pinned_is_updated_in_place_on_a_re_attempt(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)
    url = "https://investor.apple.com/annual/2023.pdf"

    await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[_record(url=url, status="discovered")],
        cfg=flags_on,
    )
    await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[_record(url=url, status="extracted", pinned=True)],
        cfg=flags_on,
    )
    assert await _count(session) == 1
    assert (await _rows(session))[0].pinned is True


async def test_a_non_boolean_pinned_value_is_stored_as_null(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=None,
        attempts=[_record(url="https://investor.apple.com/a.pdf", status="extracted", pinned="yes")],
        cfg=flags_on,
    )
    assert (await _rows(session))[0].pinned is None


async def test_same_url_different_run_creates_second_row(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run_a = await _add_run(session)
    run_b = await _add_run(session)
    url = "https://investor.apple.com/annual/2023.pdf"

    await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run_a.id,
        attempts=[_record(url=url)],
        cfg=flags_on,
    )
    await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run_b.id,
        attempts=[_record(url=url)],
        cfg=flags_on,
    )

    assert await _count(session) == 2
    rows = await _rows(session)
    assert {r.agent_run_id for r in rows} == {run_a.id, run_b.id}
    assert {r.url_hash for r in rows} == {url_hash_of(url)}


# ---------------------------------------------------------------------------
# Honest vocabularies — junk never reaches the DB
# ---------------------------------------------------------------------------
async def test_unknown_status_is_skipped(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)

    written = await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[
            _record(url="https://apple.com/a.pdf", status="totally_made_up"),
            _record(url="https://apple.com/b.pdf", status="discovered"),
        ],
        cfg=flags_on,
    )

    assert written == 1
    rows = await _rows(session)
    assert len(rows) == 1
    assert rows[0].status == "discovered"
    assert rows[0].canonical_url.endswith("/b.pdf")


async def test_raw_failure_text_never_reaches_the_database(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)
    raw = "ConnectionError: 10.0.0.5 refused"

    await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[
            _record(url="https://apple.com/a.pdf", status="timeout", failure_code=raw)
        ],
        cfg=flags_on,
    )

    row = (await _rows(session))[0]
    assert row.failure_code == "unknown"
    assert "10.0.0.5" not in (row.failure_code or "")
    assert "ConnectionError" not in (row.failure_code or "")


def test_sanitizers_have_exactly_one_implementation() -> None:
    """PR-review nit 13: the writer re-exports, it does not re-implement.

    Two copies of the sanitizer could drift, and a drifted copy is how raw
    provider text (or an exact status code) reaches the database.
    """
    from app.services.sources import ingestion_status as vocab

    assert sanitize_failure_code is vocab.sanitize_failure_code
    assert http_status_class is vocab.http_status_class


def test_sanitize_failure_code_allows_only_the_closed_vocabulary() -> None:
    for code in ALL_FAILURE_CODES:
        assert sanitize_failure_code(code) == code
    assert sanitize_failure_code(None) == "unknown"
    assert sanitize_failure_code("") == "unknown"
    assert sanitize_failure_code("ConnectionError: 10.0.0.5 refused") == "unknown"
    assert sanitize_failure_code("BLOCKED_HOST") == "unknown"  # case-sensitive


# ---------------------------------------------------------------------------
# URL canonicalization / secrets
# ---------------------------------------------------------------------------
async def test_signed_url_is_canonicalized_before_hash_and_storage(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)
    signed = "https://cdn.apple.com/annual/2023.pdf?token=SECRETVALUE&sig=abc123"

    await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[_record(url=signed)],
        cfg=flags_on,
    )

    row = (await _rows(session))[0]
    assert "token" not in row.canonical_url.lower()
    assert "secretvalue" not in row.canonical_url.lower()
    assert "sig=" not in row.canonical_url.lower()
    assert row.canonical_url == "https://cdn.apple.com/annual/2023.pdf"
    assert row.url_hash == url_hash_of(signed)


def test_different_tokens_hash_identically() -> None:
    base = "https://cdn.apple.com/annual/2023.pdf"
    assert url_hash_of(f"{base}?token=a") == url_hash_of(f"{base}?token=b")
    assert url_hash_of(f"{base}?token=a") == url_hash_of(base)
    assert url_hash_of(base) != url_hash_of("https://cdn.apple.com/annual/2022.pdf")
    assert len(url_hash_of(base)) == 64


async def test_token_variants_update_one_row(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)
    base = "https://cdn.apple.com/annual/2023.pdf"

    await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[_record(url=f"{base}?token=a")],
        cfg=flags_on,
    )
    await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[_record(url=f"{base}?token=b", status="fetched")],
        cfg=flags_on,
    )

    assert await _count(session) == 1
    assert (await _rows(session))[0].status == "fetched"


async def test_overlong_url_is_truncated_not_rejected(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)
    long_url = "https://investor.apple.com/" + ("a" * 2500) + ".pdf"

    written = await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[_record(url=long_url)],
        cfg=flags_on,
    )

    assert written == 1
    row = (await _rows(session))[0]
    assert len(row.canonical_url) == 2000
    assert row.canonical_url == long_url[:2000]
    # The hash is over the FULL canonical URL, not the truncated column value.
    assert row.url_hash == url_hash_of(long_url)


# ---------------------------------------------------------------------------
# http_status_class
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (200, "2xx"),
        (204, "2xx"),
        (299, "2xx"),
        (301, "3xx"),
        (399, "3xx"),
        (403, "4xx"),
        (404, "4xx"),
        (429, "4xx"),
        (500, "5xx"),
        (503, "5xx"),
        (599, "5xx"),
        (None, None),
        (0, None),
        (100, None),
        (199, None),
        (600, None),
        (-1, None),
    ],
)
def test_http_status_class_mapping(code, expected) -> None:
    assert http_status_class(code) == expected


async def test_http_status_class_is_stored_not_the_exact_code(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)

    await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[
            _record(
                status="unsupported",
                failure_code="http_client_error",
                http_status_class=http_status_class(403),
            )
        ],
        cfg=flags_on,
    )

    row = (await _rows(session))[0]
    assert row.http_status_class == "4xx"
    assert "403" not in (row.http_status_class or "")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
async def test_load_attempt_summary_counts_by_status(session, flags_on):
    company = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    run = await _add_run(session)

    await record_ingestion_attempts(
        session,
        company_id=company.id,
        agent_run_id=run.id,
        attempts=[
            _record(url="https://apple.com/1.pdf", status="discovered"),
            _record(url="https://apple.com/2.pdf", status="discovered"),
            _record(url="https://apple.com/3.pdf", status="extracted"),
            _record(
                url="https://apple.com/4.pdf",
                status="rejected_security",
                failure_code="blocked_host",
            ),
        ],
        cfg=flags_on,
    )

    summary = await load_attempt_summary(
        session, company_id=company.id, agent_run_id=run.id, cfg=flags_on
    )

    assert summary == {
        "discovered": 2,
        "extracted": 1,
        "rejected_security": 1,
        "total": 4,
    }


async def test_load_attempt_summary_is_company_scoped(session, flags_on):
    company_a = await _add_company(session, ticker="AAPL", name="Apple Inc.")
    company_b = await _add_company(session, ticker="MSFT", name="Microsoft")
    run = await _add_run(session)

    await record_ingestion_attempts(
        session,
        company_id=company_a.id,
        agent_run_id=run.id,
        attempts=[_record(url="https://apple.com/1.pdf", status="discovered")],
        cfg=flags_on,
    )
    await record_ingestion_attempts(
        session,
        company_id=company_b.id,
        agent_run_id=run.id,
        attempts=[
            _record(url="https://microsoft.com/1.pdf", status="extracted"),
            _record(url="https://microsoft.com/2.pdf", status="timeout"),
        ],
        cfg=flags_on,
    )

    assert await load_attempt_summary(
        session, company_id=company_a.id, cfg=flags_on
    ) == {"discovered": 1, "total": 1}
    assert await load_attempt_summary(
        session, company_id=company_b.id, cfg=flags_on
    ) == {"extracted": 1, "timeout": 1, "total": 2}


async def test_load_attempt_summary_returns_empty_when_gated_off(monkeypatch):
    monkeypatch.setattr(
        app_settings, "primary_document_ingestion_enabled", True, raising=False
    )
    monkeypatch.setattr(
        app_settings, "report_citation_persistence_enabled", False, raising=False
    )

    summary = await load_attempt_summary(
        _ExplodingSession(),  # type: ignore[arg-type]
        company_id=uuid.uuid4(),
        cfg=app_settings,
    )

    assert summary == {}


async def test_load_attempt_summary_without_company_issues_no_query(flags_on):
    summary = await load_attempt_summary(
        _ExplodingSession(),  # type: ignore[arg-type]
        company_id=None,
        cfg=flags_on,
    )

    assert summary == {}
