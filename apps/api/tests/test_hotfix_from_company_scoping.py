"""Phase 32A hotfix — company-scoped ``from-company`` final-report selection.

Root cause pinned by these tests: ``FinalReportGeneratorService.generate_from_company``
used to select the GLOBALLY-newest completed report (no company predicate), so a
``from-company/CFR`` request could return an Apple analysis. The fix adds a real
``reports.company_id`` FK (migration 012) and scopes the source-report query to
``Report.company_id == company_id`` with a deterministic ``(created_at desc,
id desc)`` tie-break, failing clearly (ValueError -> 404) when the company has no
eligible report (no cross-company fallback).

These tests run OFFLINE against a REAL in-memory SQLite async database
(``aiosqlite``) so the new FK column and the company-scoped SELECT are genuinely
exercised (the shared test conftest uses a mock AsyncSession, which cannot
exercise a WHERE clause). A dialect-scoped ``JSONB -> JSON`` compiler shim lets
``Base.metadata.create_all`` build the Postgres-flavoured schema on SQLite; the
shim only affects the ``sqlite`` dialect and never touches production Postgres.
The LLM council is DISABLED by default (``llm_council_enabled=False``), so
generation is deterministic and makes no network / Azure calls.

Auth note: authentication/authorization for these admin routes is enforced at
the proxy/middleware layer (GitHub OAuth + HMAC session, Phase 23), which is NOT
mounted in the bare ASGI test app. This diff does not touch auth, so 401 for
unauthenticated callers is unchanged and is validated in staging by the
orchestrator. These tests assert only the selection/404 behaviour changed here.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

# --- Import every model module so Base.metadata is complete for create_all. ---
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import backtest as _backtest  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import financial_snapshot as _financial_snapshot  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import review_event as _review_event  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import screening as _screening  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.agent_run import AgentRun
from app.models.company import Company
from app.models.report import Report
from app.services.final_report_generator import FinalReportGeneratorService

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


# ---------------------------------------------------------------------------
# SQLite compile shim: render Postgres JSONB columns as JSON on SQLite so
# create_all works. Dialect-scoped to "sqlite" — no effect on Postgres.
# ---------------------------------------------------------------------------
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
async def api_client(session_factory):
    """HTTP client whose get_db dependency yields the real SQLite session."""
    from app.db.session import get_db
    from app.main import app

    async def _override_get_db():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed helpers (add + flush only — the caller controls commit)
# ---------------------------------------------------------------------------
async def _add_company(session, *, ticker: str, exchange: str, name: str) -> Company:
    company = Company(
        id=uuid.uuid4(),
        ticker=ticker,
        exchange=exchange,
        name=name,
        country="US",
        sector="Technology",
        industry="Consumer Electronics",
        status="new",
    )
    session.add(company)
    await session.flush()
    return company


async def _add_completed_run(session, *, status: str = "completed") -> AgentRun:
    run = AgentRun(
        id=uuid.uuid4(),
        workflow_name="company_analysis",
        workflow_version="1.0.0",
        status=status,
        started_at=_utcnow(),
        trigger_type="manual",
    )
    session.add(run)
    await session.flush()
    return run


async def _add_draft_report(
    session,
    *,
    company_id: uuid.UUID | None,
    agent_run_id: uuid.UUID | None,
    created_at: datetime,
    report_id: uuid.UUID | None = None,
    title: str = "Draft",
) -> Report:
    report = Report(
        id=report_id or uuid.uuid4(),
        title=title,
        slug=f"draft-{uuid.uuid4().hex[:12]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="draft",
        content_markdown="# Analysis Council Draft (no envelope)",
        created_by_agent_run_id=agent_run_id,
        company_id=company_id,
        human_review_required=True,
        created_at=created_at,
    )
    session.add(report)
    await session.flush()
    return report


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------
def _parse_report_content(report: Report) -> dict:
    """Parse the structured report_content JSON block out of a saved report."""
    md = report.content_markdown or ""
    blocks = re.findall(r"```json\s*(.*?)\s*```", md, re.DOTALL)
    assert blocks, "saved final report has no JSON block"
    return json.loads(blocks[-1])


async def _load_report(session, report_id: uuid.UUID) -> Report:
    return (
        await session.execute(select(Report).where(Report.id == report_id))
    ).scalar_one()


async def _generate_from_company(session, company_id: uuid.UUID):
    """Run generate_from_company and return (response, saved_final, content)."""
    svc = FinalReportGeneratorService()
    resp = await svc.generate_from_company(session, company_id)
    saved = await _load_report(session, resp.report_id)
    return resp, saved, _parse_report_content(saved)


def _selected_source_report_id(content: dict) -> uuid.UUID:
    """The source report the final report was generated FROM (persisted lineage)."""
    return uuid.UUID(content["workflow_status"]["report_id"])


def _selected_agent_run_id(content: dict) -> uuid.UUID:
    return uuid.UUID(content["workflow_status"]["agent_run_id"])


# ---------------------------------------------------------------------------
# 1. Each company selects its OWN report, not the globally-newest
# ---------------------------------------------------------------------------
async def test_from_company_selects_each_companys_own_report(session) -> None:
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    cfr = await _add_company(session, ticker="CFR", exchange="SW", name="Richemont SA")

    aapl_run = await _add_completed_run(session)
    cfr_run = await _add_completed_run(session)

    # CFR report is the GLOBALLY-newest completed report.
    aapl_report = await _add_draft_report(
        session,
        company_id=aapl.id,
        agent_run_id=aapl_run.id,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        title="AAPL draft",
    )
    cfr_report = await _add_draft_report(
        session,
        company_id=cfr.id,
        agent_run_id=cfr_run.id,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        title="CFR draft",
    )
    await session.commit()

    # from-company(AAPL) -> AAPL's OWN (older) report, NOT the newer CFR one.
    _, _, aapl_content = await _generate_from_company(session, aapl.id)
    assert _selected_source_report_id(aapl_content) == aapl_report.id
    assert _selected_agent_run_id(aapl_content) == aapl_run.id
    assert aapl_content["company_identity"]["legal_name"]["value"] == "Apple Inc."

    # from-company(CFR) -> CFR's own report.
    _, _, cfr_content = await _generate_from_company(session, cfr.id)
    assert _selected_source_report_id(cfr_content) == cfr_report.id
    assert _selected_agent_run_id(cfr_content) == cfr_run.id
    assert cfr_content["company_identity"]["legal_name"]["value"] == "Richemont SA"


# ---------------------------------------------------------------------------
# 2. Reverse creation order: when AAPL is globally-newest, from-company(CFR)
#    still returns CFR's OWN report.
# ---------------------------------------------------------------------------
async def test_from_company_reverse_order_no_cross_company_leak(session) -> None:
    cfr = await _add_company(session, ticker="CFR", exchange="SW", name="Richemont SA")
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")

    cfr_run = await _add_completed_run(session)
    aapl_run = await _add_completed_run(session)

    cfr_report = await _add_draft_report(
        session,
        company_id=cfr.id,
        agent_run_id=cfr_run.id,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        title="CFR draft",
    )
    # AAPL is now the GLOBALLY-newest completed report.
    aapl_report = await _add_draft_report(
        session,
        company_id=aapl.id,
        agent_run_id=aapl_run.id,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        title="AAPL draft",
    )
    await session.commit()

    _, _, cfr_content = await _generate_from_company(session, cfr.id)
    assert _selected_source_report_id(cfr_content) == cfr_report.id
    assert _selected_source_report_id(cfr_content) != aapl_report.id


# ---------------------------------------------------------------------------
# 3. Multiple eligible completed reports for ONE company -> deterministic newest
#    by (created_at desc, id desc).
# ---------------------------------------------------------------------------
async def test_from_company_picks_newest_by_created_at(session) -> None:
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    run = await _add_completed_run(session)

    await _add_draft_report(
        session,
        company_id=aapl.id,
        agent_run_id=run.id,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        title="old",
    )
    newest = await _add_draft_report(
        session,
        company_id=aapl.id,
        agent_run_id=run.id,
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        title="newest",
    )
    await session.commit()

    _, _, content = await _generate_from_company(session, aapl.id)
    assert _selected_source_report_id(content) == newest.id


async def test_from_company_tie_break_by_id_desc(session) -> None:
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    run = await _add_completed_run(session)

    same_ts = datetime(2026, 5, 1, tzinfo=timezone.utc)
    # Two ids with a known hex ordering (SQLite stores Uuid as a 32-char hex
    # string, so id.desc() picks the lexicographically greatest hex).
    id_low = uuid.UUID("00000000-0000-0000-0000-000000000001")
    id_high = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")

    await _add_draft_report(
        session,
        company_id=aapl.id,
        agent_run_id=run.id,
        created_at=same_ts,
        report_id=id_low,
        title="low id",
    )
    await _add_draft_report(
        session,
        company_id=aapl.id,
        agent_run_id=run.id,
        created_at=same_ts,
        report_id=id_high,
        title="high id",
    )
    await session.commit()

    _, _, content = await _generate_from_company(session, aapl.id)
    assert _selected_source_report_id(content) == id_high


# ---------------------------------------------------------------------------
# 4. Company with NO eligible report -> ValueError (service) + 404 (route).
# ---------------------------------------------------------------------------
async def test_from_company_no_report_raises_value_error(session) -> None:
    cfr = await _add_company(session, ticker="CFR", exchange="SW", name="Richemont SA")
    await session.commit()

    with pytest.raises(ValueError, match="No eligible completed analysis report"):
        await FinalReportGeneratorService().generate_from_company(session, cfr.id)


async def test_from_company_no_report_returns_404(api_client, session_factory) -> None:
    async with session_factory() as s:
        cfr = await _add_company(s, ticker="CFR", exchange="SW", name="Richemont SA")
        cfr_id = cfr.id
        await s.commit()

    resp = await api_client.post(f"/api/v1/final-reports/from-company/{cfr_id}")
    assert resp.status_code == 404
    assert "No eligible completed analysis report" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 5. Unknown company id -> ValueError -> 404 (preserve existing behaviour).
# ---------------------------------------------------------------------------
async def test_from_company_unknown_company_raises_value_error(session) -> None:
    with pytest.raises(ValueError, match="not found"):
        await FinalReportGeneratorService().generate_from_company(
            session, uuid.uuid4()
        )


async def test_from_company_unknown_company_returns_404(api_client) -> None:
    resp = await api_client.post(
        f"/api/v1/final-reports/from-company/{uuid.uuid4()}"
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 6. from-report is unchanged: it uses exactly the report_id passed, regardless
#    of company_id or which report is globally newest.
# ---------------------------------------------------------------------------
async def test_from_report_uses_exact_report_regardless_of_company(session) -> None:
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    cfr = await _add_company(session, ticker="CFR", exchange="SW", name="Richemont SA")
    aapl_run = await _add_completed_run(session)
    cfr_run = await _add_completed_run(session)

    aapl_report = await _add_draft_report(
        session,
        company_id=aapl.id,
        agent_run_id=aapl_run.id,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        title="AAPL draft",
    )
    # CFR report is globally newest — must NOT be selected by from-report(AAPL).
    await _add_draft_report(
        session,
        company_id=cfr.id,
        agent_run_id=cfr_run.id,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        title="CFR draft",
    )
    await session.commit()

    resp = await FinalReportGeneratorService().generate_from_report(
        session, aapl_report.id
    )
    saved = await _load_report(session, resp.report_id)
    content = _parse_report_content(saved)
    assert _selected_source_report_id(content) == aapl_report.id
    assert _selected_agent_run_id(content) == aapl_run.id


# ---------------------------------------------------------------------------
# 7. Lineage preserved: the generated final report records the correct source
#    report id and its AgentRun id for the requested company.
# ---------------------------------------------------------------------------
async def test_from_company_preserves_source_lineage(session) -> None:
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    run = await _add_completed_run(session)
    source_report = await _add_draft_report(
        session,
        company_id=aapl.id,
        agent_run_id=run.id,
        created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        title="AAPL draft",
    )
    await session.commit()

    _, _, content = await _generate_from_company(session, aapl.id)
    ws = content["workflow_status"]
    assert ws["report_id"] == str(source_report.id)
    assert ws["agent_run_id"] == str(run.id)


# ---------------------------------------------------------------------------
# 8. No cross-company fallback: with only an AAPL report present and CFR having
#    none, from-company(CFR) must 404 and never return AAPL's report.
# ---------------------------------------------------------------------------
async def test_from_company_no_cross_company_fallback(api_client, session_factory) -> None:
    async with session_factory() as s:
        aapl = await _add_company(s, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
        cfr = await _add_company(s, ticker="CFR", exchange="SW", name="Richemont SA")
        run = await _add_completed_run(s)
        await _add_draft_report(
            s,
            company_id=aapl.id,
            agent_run_id=run.id,
            created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            title="AAPL draft",
        )
        cfr_id = cfr.id
        await s.commit()

    resp = await api_client.post(f"/api/v1/final-reports/from-company/{cfr_id}")
    assert resp.status_code == 404
    assert "No eligible completed analysis report" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 9. Safety / publication invariants on a generated from-company report.
# ---------------------------------------------------------------------------
async def test_from_company_preserves_safety_and_publication_invariants(
    session,
) -> None:
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    run = await _add_completed_run(session)
    await _add_draft_report(
        session,
        company_id=aapl.id,
        agent_run_id=run.id,
        created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        title="AAPL draft",
    )
    await session.commit()

    resp, saved, _ = await _generate_from_company(session, aapl.id)

    # Invariants unchanged by this hotfix.
    assert resp.human_review_required is True
    assert resp.publication_ready is False
    assert resp.status == "draft"
    assert resp.review_status == "draft"
    assert resp.llm_used is False  # council disabled offline
    # The persisted draft honours the same invariants.
    assert saved.status == "draft"
    assert saved.review_status == "draft"
    assert saved.human_review_required is True
    assert saved.published_at is None


async def test_from_company_api_happy_path_returns_201_draft(
    api_client, session_factory
) -> None:
    async with session_factory() as s:
        aapl = await _add_company(s, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
        run = await _add_completed_run(s)
        await _add_draft_report(
            s,
            company_id=aapl.id,
            agent_run_id=run.id,
            created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            title="AAPL draft",
        )
        aapl_id = aapl.id
        await s.commit()

    resp = await api_client.post(f"/api/v1/final-reports/from-company/{aapl_id}")
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "draft"
    assert body["review_status"] == "draft"
    assert body["human_review_required"] is True
    assert body["publication_ready"] is False
