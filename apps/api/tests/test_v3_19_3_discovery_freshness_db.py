"""V3.19.3 — what a discovery council may be told about prior research (DB-backed).

MUTATION GUARD (stale report reuse): a legacy report's contents never reach the council
as research signals; stale V3 research arrives labelled as dated context.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import company as _company  # noqa: F401
from app.models import report as _report_module  # noqa: F401
from app.models.report import Report
from app.services.current_research_resolver import resolve_research_for_discovery


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(eng, expire_on_commit=False)() as s:
        yield s
    await eng.dispose()


COMPANY = uuid.UUID("cccccccc-0000-0000-0000-0000000000c1")
CFG = SimpleNamespace(v3_research_fresh_days=120)
CONTENT = {
    "financial_snapshot": {
        "reporting_periods": {"latest_annual": {"value": "FY2025"}},
        "revenue_usd_m_primary_filing": {"numeric_value": 3100, "scale": "million",
                                         "currency": "DKK", "period": "FY2025"},
    }
}


def _report(*, days_old: int, professional: bool) -> Report:
    now = datetime.now(timezone.utc)
    summary: dict = {"llm_council": {"agents_completed": 8}}
    if professional:
        summary["v3_research"] = {"professional_research": {"sections": [{"key": "x"}]},
                                  "research_engine_version": "v3.19"}
    return Report(
        id=uuid.uuid4(), title="r", slug=f"s-{uuid.uuid4().hex[:8]}",
        report_type="company_deep_dive", status="draft", review_status="draft",
        human_review_required=True, company_id=COMPANY,
        content_markdown="# r\n\n```json\n" + json.dumps(CONTENT) + "\n```",
        final_report_version="16.0.0", source_summary_json=summary,
        created_at=now - timedelta(days=days_old), updated_at=now - timedelta(days=days_old),
    )


async def test_legacy_report_is_history_only(session):
    session.add(_report(days_old=3, professional=False))
    await session.commit()
    out = await resolve_research_for_discovery(session, COMPANY, cfg=CFG)
    assert out["historical_research_exists"] is True
    assert out["research_freshness"]["status"] == "legacy"
    # Nothing the legacy report SAID reaches the council.
    assert "annual_figures" not in out and "current_research_report_id" not in out


async def test_current_v3_research_is_evidence(session):
    session.add(_report(days_old=10, professional=True))
    session.add(_report(days_old=1, professional=False))  # a newer legacy report
    await session.commit()
    out = await resolve_research_for_discovery(session, COMPANY, cfg=CFG)
    assert out["research_freshness"]["status"] == "v3_current"
    assert out.get("annual_figures")
    assert "dated_context" not in out


async def test_stale_v3_research_is_labelled_dated_context(session):
    session.add(_report(days_old=200, professional=True))
    await session.commit()
    out = await resolve_research_for_discovery(session, COMPANY, cfg=CFG)
    assert out["research_freshness"]["status"] == "v3_stale"
    assert out["dated_context"] is True and "STALE" in out["note"]


async def test_no_research_is_empty(session):
    assert await resolve_research_for_discovery(session, COMPANY, cfg=CFG) == {}
