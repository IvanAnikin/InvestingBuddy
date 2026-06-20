"""
Shared test fixtures.

Database strategy: tests use a mock AsyncSession so no real database is needed.
The get_db FastAPI dependency is overridden with a factory returning the mock.
Service functions that would hit the DB are patched per-test.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.main import app
from app.models.agent_run import AgentRun
from app.models.company import Company
from app.models.report import Report
from app.models.source import Citation, Source

# ---------------------------------------------------------------------------
# DB mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


# ---------------------------------------------------------------------------
# HTTP client with DB override
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(mock_db: AsyncMock) -> AsyncGenerator[AsyncClient, None]:
    app.dependency_overrides[get_db] = lambda: mock_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# UUID fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def company_id() -> uuid.UUID:
    return uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def agent_run_id() -> uuid.UUID:
    return uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def report_id() -> uuid.UUID:
    return uuid.UUID("33333333-3333-3333-3333-333333333333")


# ---------------------------------------------------------------------------
# Model fixtures — use MagicMock so SQLAlchemy instrumentation is bypassed.
# Pydantic model_validate(obj, from_attributes=True) uses getattr() so it
# works correctly with MagicMock attributes set explicitly below.
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_company(company_id: uuid.UUID) -> MagicMock:
    now = datetime.now(timezone.utc)
    company = MagicMock(spec=Company)
    company.id = company_id
    company.ticker = "VOW3"
    company.exchange = "XETRA"
    company.name = "Volkswagen AG"
    company.country = "Germany"
    company.region = "Europe"
    company.sector = "Automotive"
    company.industry = "Auto Manufacturers"
    company.market_cap = 60_000_000_000.0
    company.currency = "EUR"
    company.website = "https://www.volkswagenag.com"
    company.description = "German automobile manufacturer."
    company.status = "new"
    company.created_at = now
    company.updated_at = now
    return company


@pytest.fixture
def sample_agent_run(agent_run_id: uuid.UUID) -> MagicMock:
    now = datetime.now(timezone.utc)
    run = MagicMock(spec=AgentRun)
    run.id = agent_run_id
    run.workflow_name = "company_analysis"
    run.workflow_version = "1.0.0"
    run.status = "completed"
    run.started_at = now
    run.finished_at = now
    run.trigger_type = "manual"
    run.created_by_user_id = None
    run.total_tokens = None
    run.total_cost = None
    run.error_message = None
    return run


@pytest.fixture
def source_id() -> uuid.UUID:
    return uuid.UUID("44444444-4444-4444-4444-444444444444")


@pytest.fixture
def citation_id() -> uuid.UUID:
    return uuid.UUID("55555555-5555-5555-5555-555555555555")


@pytest.fixture
def sample_report(report_id: uuid.UUID, agent_run_id: uuid.UUID) -> MagicMock:
    now = datetime.now(timezone.utc)
    report = MagicMock(spec=Report)
    report.id = report_id
    report.title = "Volkswagen AG — Draft Analysis"
    report.slug = "company-analysis-vow3-22222222"
    report.report_type = "company_deep_dive"
    report.period_start = None
    report.period_end = None
    report.status = "draft"
    report.summary = "Placeholder analysis."
    report.content_markdown = "# VOW3"
    report.content_html = None
    report.created_by_agent_run_id = agent_run_id
    report.published_at = None
    report.created_at = now
    report.updated_at = now
    return report


@pytest.fixture
def sample_source(source_id: uuid.UUID) -> MagicMock:
    now = datetime.now(timezone.utc)
    source = MagicMock(spec=Source)
    source.id = source_id
    source.source_type = "placeholder"
    source.title = "[PLACEHOLDER] Volkswagen AG — workflow-generated source"
    source.url = None
    source.publisher = "InvestingBuddy workflow (placeholder)"
    source.published_at = None
    source.retrieved_at = now
    source.credibility_score = 0.0
    source.content_hash = None
    source.blob_path = None
    source.created_at = now
    return source


@pytest.fixture
def sample_citation(
    citation_id: uuid.UUID,
    source_id: uuid.UUID,
    report_id: uuid.UUID,
    agent_run_id: uuid.UUID,
) -> MagicMock:
    now = datetime.now(timezone.utc)
    citation = MagicMock(spec=Citation)
    citation.id = citation_id
    citation.source_id = source_id
    citation.report_id = report_id
    citation.agent_run_id = agent_run_id
    citation.claim_text = "thesis"
    citation.source_quote = (
        "[PLACEHOLDER] This citation is auto-generated by the workflow skeleton."
    )
    citation.url = None
    citation.retrieved_at = None
    citation.created_at = now
    return citation
