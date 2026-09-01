"""
Investor Research Experience V2 — company-scoped report listing.

The user-facing surfaces have to answer one question exactly: *which report is
this company's CURRENT research report?* Until now the only way to ask was to
page the global report list and filter client side, which is a window — a
company whose report falls off page one silently looks like it has none, and a
discovery candidate then links to whatever stale draft it happened to carry.

These tests pin the read filter itself: it scopes by the ``reports.company_id``
FK (migration 012), it counts what it returns, its ordering is deterministic,
and omitting it leaves the unfiltered listing exactly as it was.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import report as _report_module  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.report import Report
from app.services.report_service import list_reports


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


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


_BASE = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

COMPANY_A = uuid.UUID("aaaaaaaa-0000-0000-0000-0000000000a1")
COMPANY_B = uuid.UUID("bbbbbbbb-0000-0000-0000-0000000000b1")


def _report(
    slug: str,
    *,
    company_id: uuid.UUID | None,
    minutes: int,
    final_version: str | None = "16.0.0",
) -> Report:
    return Report(
        id=uuid.uuid4(),
        title=f"Report {slug}",
        slug=slug,
        report_type="company_deep_dive",
        status="draft",
        company_id=company_id,
        final_report_version=final_version,
        created_at=_BASE + timedelta(minutes=minutes),
        updated_at=_BASE + timedelta(minutes=minutes),
    )


async def _seed(session) -> None:
    session.add_all(
        [
            _report("a-old", company_id=COMPANY_A, minutes=0),
            _report("a-draft", company_id=COMPANY_A, minutes=10, final_version=None),
            _report("a-current", company_id=COMPANY_A, minutes=20),
            _report("b-current", company_id=COMPANY_B, minutes=30),
            _report("orphan", company_id=None, minutes=40),
        ]
    )
    await session.commit()


async def test_company_scope_returns_only_that_company(session) -> None:
    await _seed(session)

    reports, total = await list_reports(session, company_id=COMPANY_A)

    assert total == 3
    assert {r.slug for r in reports} == {"a-old", "a-draft", "a-current"}


async def test_company_scope_total_counts_the_scope_not_the_table(session) -> None:
    """A scoped total that counted the whole table would make paging lie."""
    await _seed(session)

    _, total_a = await list_reports(session, company_id=COMPANY_A)
    _, total_b = await list_reports(session, company_id=COMPANY_B)
    _, total_all = await list_reports(session)

    assert (total_a, total_b, total_all) == (3, 1, 5)


async def test_company_scope_is_newest_first(session) -> None:
    """The caller resolves 'current' from this order, so it must be exact."""
    await _seed(session)

    reports, _ = await list_reports(session, company_id=COMPANY_A)

    assert [r.slug for r in reports] == ["a-current", "a-draft", "a-old"]


async def test_company_scope_never_returns_another_company(session) -> None:
    await _seed(session)

    reports, _ = await list_reports(session, company_id=COMPANY_B)

    assert [r.slug for r in reports] == ["b-current"]


async def test_report_with_no_company_is_unreachable_by_scope(session) -> None:
    """A report whose company link was cleared belongs to no company's page."""
    await _seed(session)

    for company in (COMPANY_A, COMPANY_B):
        reports, _ = await list_reports(session, company_id=company)
        assert "orphan" not in {r.slug for r in reports}


async def test_no_scope_keeps_the_global_listing(session) -> None:
    await _seed(session)

    reports, total = await list_reports(session)

    assert total == 5
    assert [r.slug for r in reports] == [
        "orphan",
        "b-current",
        "a-current",
        "a-draft",
        "a-old",
    ]


async def test_unknown_company_is_an_empty_page_not_an_error(session) -> None:
    await _seed(session)

    reports, total = await list_reports(session, company_id=uuid.uuid4())

    assert (reports, total) == ([], 0)


async def test_scope_composes_with_limit_and_offset(session) -> None:
    await _seed(session)

    page, total = await list_reports(
        session, company_id=COMPANY_A, limit=1, offset=1
    )

    assert total == 3
    assert [r.slug for r in page] == ["a-draft"]
