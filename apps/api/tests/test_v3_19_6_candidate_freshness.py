"""V3.19.6 — the page's freshness badge: batched, body-free, professional over legacy."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.company import Company
from app.models.discovery import DiscoveryCandidate
from app.models.report import Report
from app.services import market_discovery_service as mds


@compiles(JSONB, "sqlite")
def _jsonb(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


def _report(company_id, *, days, professional):
    now = datetime.now(timezone.utc)
    summary = {"v3_research": {"professional_research": {"sections": [1]}}} if professional else {}
    return Report(id=uuid.uuid4(), title="r", slug=f"s-{uuid.uuid4().hex[:8]}",
                  report_type="company_deep_dive", status="draft", review_status="draft",
                  human_review_required=True, company_id=company_id, content_markdown="x",
                  final_report_version="16.0.0", source_summary_json=summary,
                  created_at=now - timedelta(days=days), updated_at=now - timedelta(days=days))


async def test_professional_research_wins_over_a_newer_legacy_report(session):
    a, b = uuid.uuid4(), uuid.uuid4()
    session.add_all([
        Company(id=a, ticker="KER", exchange="PA", name="Kering SA", status="new"),
        Company(id=b, ticker="MONC", exchange="MI", name="Moncler", status="new"),
        _report(a, days=20, professional=True), _report(a, days=1, professional=False),
        _report(b, days=5, professional=False),
    ])
    await session.commit()
    run_id = uuid.uuid4()
    candidates = [DiscoveryCandidate(id=uuid.uuid4(), discovery_run_id=run_id, ticker="KER",
                                     exchange="PA"),
                  DiscoveryCandidate(id=uuid.uuid4(), discovery_run_id=run_id, ticker="MONC",
                                     exchange="MI"),
                  DiscoveryCandidate(id=uuid.uuid4(), discovery_run_id=run_id, ticker="NEW",
                                     exchange="PA")]
    out = await mds.candidate_research_freshness(session, candidates)
    assert out[candidates[0].id]["status"] == "v3_current"
    assert out[candidates[1].id]["status"] == "legacy"
    assert candidates[2].id not in out
