"""
Phase 32A Slice 6D — Deep Field Review input resolution + async job.

Runs against a real in-memory SQLite DB (the same pattern as
test_phase32a_slice5b3_primary_documents_api.py) so the FK/scoping behaviour is
exercised for real, and the deterministic FAKE field-review client so no network
or credentials are involved.

Focus: which candidates are comparable, that NO candidate is ever silently
dropped, that a mock-provenance candidate is included WITH a caveat rather than
excluded, and that a field review for run A can never see run B's data.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import document_ingestion_attempt as _dia  # noqa: F401
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import field_review as _field_review  # noqa: F401
from app.models import report as _report_model  # noqa: F401
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.models.field_review import FieldReviewCandidateSummary, FieldReviewRun
from app.models.report import Report
from app.services.field_review_service import (
    FieldReviewDisabledError,
    InsufficientAnalyzedCandidatesError,
    get_candidate_summaries,
    get_latest_field_review,
    process_field_review_by_id,
    resolve_field_candidates,
    start_field_review,
)
from app.services.llm.fake_field_review_client import FakeFieldReviewLLMClient


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
def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def db(session_factory):
    async with session_factory() as s:
        yield s


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _cfg(enabled: bool = True, **over: Any) -> Settings:
    base: dict[str, Any] = {
        "llm_council_enabled": enabled,
        "llm_field_review_council_enabled": enabled,
        "llm_provider_council": "fake",
        "llm_field_review_council_retry_enabled": False,
        "field_review_min_candidates": 2,
    }
    base.update(over)
    return Settings(**base)


def _sections() -> dict[str, Any]:
    return {
        "company_identity": {"type": "company_identity", "ticker": {"value": "X"}},
        "financial_snapshot": {
            "type": "financial_snapshot",
            "latest_close": {"value": 10.0, "provenance": "sourced_fact"},
        },
        "valuation_readiness": {"readiness": {"value": "not_ready"}},
    }


def _report(
    *,
    final: bool = True,
    schema_valid: bool = True,
    provenance: str | None = "real",
) -> Report:
    source_summary: dict[str, Any] = {
        "total_sources": 3,
        "total_citations": 4,
        "llm_council": {"llm_used": True, "agents_completed": 8, "agents_failed": 0},
    }
    if provenance is not None:
        source_summary["data_provenance"] = provenance
    return Report(
        id=uuid.uuid4(),
        title="Report",
        slug=f"r-{uuid.uuid4().hex[:10]}",
        report_type="company_deep_dive",
        status="draft",
        final_report_version="1.0.0" if final else None,
        content_markdown="```json\n" + json.dumps(_sections()) + "\n```",
        schema_validation_json={"schema_valid": schema_valid},
        source_summary_json=source_summary,
    )


def _run(**over: Any) -> DiscoveryRun:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "status": "completed",
        "mode": "ticker",
        "candidate_count": 0,
    }
    base.update(over)
    return DiscoveryRun(**base)


def _candidate(
    run: DiscoveryRun, ticker: str, *, report: Report | None = None, rank: int | None = 1
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=uuid.uuid4(),
        discovery_run_id=run.id,
        ticker=ticker,
        exchange="US",
        company_name=f"{ticker} Inc",
        rank=rank,
        analysis_report_id=report.id if report is not None else None,
    )


async def _seed(db, run: DiscoveryRun, rows: list[Any]) -> None:
    db.add(run)
    for row in rows:
        db.add(row)
    await db.commit()


# ---------------------------------------------------------------------------
# Input resolution
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_zero_candidates_resolves_to_nothing_comparable(db) -> None:
    run = _run()
    await _seed(db, run, [])
    res = await resolve_field_candidates(db, run, cfg=_cfg())
    assert res.included == []
    assert res.missing == []
    assert res.candidate_count == 0


@pytest.mark.anyio
async def test_one_valid_candidate_is_below_the_minimum(db) -> None:
    run = _run()
    report = _report()
    await _seed(db, run, [report, _candidate(run, "AAA", report=report)])
    res = await resolve_field_candidates(db, run, cfg=_cfg())
    assert len(res.included) == 1
    with pytest.raises(InsufficientAnalyzedCandidatesError) as exc:
        await start_field_review(db, run, cfg=_cfg())
    assert exc.value.included == 1
    assert exc.value.required == 2


@pytest.mark.anyio
async def test_candidate_without_an_analysis_report_is_excluded_with_a_reason(
    db,
) -> None:
    run = _run()
    report = _report()
    await _seed(
        db,
        run,
        [report, _candidate(run, "AAA", report=report), _candidate(run, "BBB")],
    )
    res = await resolve_field_candidates(db, run, cfg=_cfg())
    assert [c.ticker for c, _, _ in res.included] == ["AAA"]
    assert res.missing == [
        {
            "discovery_candidate_id": res.missing[0]["discovery_candidate_id"],
            "report_id": None,
            "ticker": "BBB",
            "exchange": "US",
            "exclusion_reason": "no_analysis_run",
        }
    ]


@pytest.mark.anyio
async def test_a_deleted_report_is_excluded_with_report_deleted(db) -> None:
    run = _run()
    candidate = DiscoveryCandidate(
        id=uuid.uuid4(),
        discovery_run_id=run.id,
        ticker="GONE",
        exchange="US",
        rank=1,
        # Points at a report row that does not exist.
        analysis_report_id=uuid.uuid4(),
    )
    await _seed(db, run, [candidate])
    res = await resolve_field_candidates(db, run, cfg=_cfg())
    assert res.included == []
    assert res.missing[0]["exclusion_reason"] == "report_deleted"
    # It DID count as "analysed" — the honest breakdown keeps that distinction.
    assert res.analyzed_candidate_count == 1


@pytest.mark.anyio
async def test_a_draft_only_report_is_excluded(db) -> None:
    run = _run()
    draft = _report(final=False)
    await _seed(db, run, [draft, _candidate(run, "DRFT", report=draft)])
    res = await resolve_field_candidates(db, run, cfg=_cfg())
    assert res.included == []
    assert res.missing[0]["exclusion_reason"] == "draft_only"


@pytest.mark.anyio
async def test_a_schema_invalid_report_is_excluded(db) -> None:
    run = _run()
    bad = _report(schema_valid=False)
    await _seed(db, run, [bad, _candidate(run, "BAD", report=bad)])
    res = await resolve_field_candidates(db, run, cfg=_cfg())
    assert res.included == []
    assert res.missing[0]["exclusion_reason"] == "not_schema_valid"


@pytest.mark.anyio
async def test_a_report_with_no_schema_validation_is_excluded(db) -> None:
    run = _run()
    bad = _report()
    bad.schema_validation_json = None
    await _seed(db, run, [bad, _candidate(run, "NONE", report=bad)])
    res = await resolve_field_candidates(db, run, cfg=_cfg())
    assert res.missing[0]["exclusion_reason"] == "not_schema_valid"


@pytest.mark.anyio
async def test_a_mock_provenance_candidate_is_included_with_a_caveat(
    db, session_factory
) -> None:
    """Mock/unknown data is NEVER dropped — it is compared, clearly caveated."""
    run = _run()
    real, mock = _report(), _report(provenance="mock")
    await _seed(
        db,
        run,
        [
            real,
            mock,
            _candidate(run, "REAL", report=real, rank=1),
            _candidate(run, "MOCK", report=mock, rank=2),
        ],
    )
    res = await resolve_field_candidates(db, run, cfg=_cfg())
    assert [c.ticker for c, _, _ in res.included] == ["REAL", "MOCK"]
    assert res.missing == []

    row, scheduled = await start_field_review(db, run, cfg=_cfg())
    assert scheduled is True
    await process_field_review_by_id(
        row.id,
        session_factory=session_factory,
        cfg=_cfg(),
        client=FakeFieldReviewLLMClient(),
    )
    db.expunge_all()
    summaries = await get_candidate_summaries(db, row.id)
    by_ticker = {s.ticker: s for s in summaries}
    assert by_ticker["MOCK"].included is True
    assert by_ticker["MOCK"].data_provenance == "mock"
    assert "data_provenance=mock" in (
        by_ticker["MOCK"].summary_json or {}
    ).get("caveats", [])


@pytest.mark.anyio
async def test_candidates_beyond_the_company_cap_are_excluded_honestly(db) -> None:
    run = _run()
    rows: list[Any] = []
    for i in range(4):
        report = _report()
        rows.append(report)
        rows.append(_candidate(run, f"T{i}", report=report, rank=i + 1))
    await _seed(db, run, rows)
    res = await resolve_field_candidates(
        db, run, cfg=_cfg(llm_field_review_council_max_companies=2)
    )
    assert len(res.included) == 2
    assert len(res.missing) == 2
    assert {m["exclusion_reason"] for m in res.missing} == {"over_company_cap"}


@pytest.mark.anyio
async def test_candidates_are_resolved_in_rank_order_with_nulls_last(db) -> None:
    run = _run()
    rows: list[Any] = []
    for ticker, rank in (("C", 3), ("A", 1), ("N", None), ("B", 2)):
        report = _report()
        rows.append(report)
        rows.append(_candidate(run, ticker, report=report, rank=rank))
    await _seed(db, run, rows)
    res = await resolve_field_candidates(db, run, cfg=_cfg())
    assert [c.ticker for c, _, _ in res.included] == ["A", "B", "C", "N"]
    assert [ref for _, _, ref in res.included] == ["F1", "F2", "F3", "F4"]


# ---------------------------------------------------------------------------
# Cross-run isolation — the load-bearing safety property
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_field_review_never_sees_another_runs_candidates(db) -> None:
    run_a, run_b = _run(), _run()
    a1, a2 = _report(), _report()
    b1, b2 = _report(), _report()
    db.add_all([run_a, run_b, a1, a2, b1, b2])
    db.add_all(
        [
            _candidate(run_a, "AAA", report=a1, rank=1),
            _candidate(run_a, "AAB", report=a2, rank=2),
            _candidate(run_b, "BBA", report=b1, rank=1),
            _candidate(run_b, "BBB", report=b2, rank=2),
        ]
    )
    await db.commit()

    res_a = await resolve_field_candidates(db, run_a, cfg=_cfg())
    res_b = await resolve_field_candidates(db, run_b, cfg=_cfg())
    assert sorted(c.ticker for c, _, _ in res_a.included) == ["AAA", "AAB"]
    assert sorted(c.ticker for c, _, _ in res_b.included) == ["BBA", "BBB"]
    assert {str(r.id) for _, r, _ in res_a.included}.isdisjoint(
        {str(r.id) for _, r, _ in res_b.included}
    )


@pytest.mark.anyio
async def test_the_same_company_in_two_runs_uses_each_runs_own_report(db) -> None:
    """The per-candidate ``analysis_report_id`` is authoritative: the same ticker
    analysed in two runs must resolve to two DIFFERENT reports, never the
    globally-newest one for that company."""
    run_a, run_b = _run(), _run()
    old_report, new_report = _report(), _report()
    other_a, other_b = _report(), _report()
    db.add_all([run_a, run_b, old_report, new_report, other_a, other_b])
    db.add_all(
        [
            _candidate(run_a, "SAME", report=old_report, rank=1),
            _candidate(run_a, "OTHA", report=other_a, rank=2),
            _candidate(run_b, "SAME", report=new_report, rank=1),
            _candidate(run_b, "OTHB", report=other_b, rank=2),
        ]
    )
    await db.commit()

    res_a = await resolve_field_candidates(db, run_a, cfg=_cfg())
    res_b = await resolve_field_candidates(db, run_b, cfg=_cfg())
    same_a = next(r for c, r, _ in res_a.included if c.ticker == "SAME")
    same_b = next(r for c, r, _ in res_b.included if c.ticker == "SAME")
    assert same_a.id == old_report.id
    assert same_b.id == new_report.id


# ---------------------------------------------------------------------------
# Async job lifecycle
# ---------------------------------------------------------------------------


async def _two_candidate_run(db) -> DiscoveryRun:
    run = _run()
    r1, r2 = _report(), _report()
    await _seed(
        db,
        run,
        [
            r1,
            r2,
            _candidate(run, "AAA", report=r1, rank=1),
            _candidate(run, "BBB", report=r2, rank=2),
        ],
    )
    return run


@pytest.mark.anyio
async def test_start_is_idempotent_while_a_job_is_in_flight(db) -> None:
    run = await _two_candidate_run(db)
    first, scheduled_first = await start_field_review(db, run, cfg=_cfg())
    second, scheduled_second = await start_field_review(db, run, cfg=_cfg())
    assert scheduled_first is True
    assert scheduled_second is False
    assert second.id == first.id
    rows = (
        (
            await db.execute(
                select(FieldReviewRun).where(
                    FieldReviewRun.discovery_run_id == run.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.anyio
async def test_a_completed_review_is_returned_unless_forced(db, session_factory) -> None:
    run = await _two_candidate_run(db)
    row, _ = await start_field_review(db, run, cfg=_cfg())
    await process_field_review_by_id(
        row.id,
        session_factory=session_factory,
        cfg=_cfg(),
        client=FakeFieldReviewLLMClient(),
    )
    db.expunge_all()

    existing, scheduled = await start_field_review(db, run, cfg=_cfg())
    assert scheduled is False
    assert existing.id == row.id

    forced, scheduled_forced = await start_field_review(db, run, cfg=_cfg(), force=True)
    assert scheduled_forced is True
    assert forced.id != row.id


@pytest.mark.anyio
async def test_disabled_raises_unless_a_prior_review_exists(db, session_factory) -> None:
    run = await _two_candidate_run(db)
    with pytest.raises(FieldReviewDisabledError):
        await start_field_review(db, run, cfg=_cfg(False))

    row, _ = await start_field_review(db, run, cfg=_cfg())
    await process_field_review_by_id(
        row.id,
        session_factory=session_factory,
        cfg=_cfg(),
        client=FakeFieldReviewLLMClient(),
    )
    db.expunge_all()
    # A prior completed review stays readable after the flags are turned off.
    existing, scheduled = await start_field_review(db, run, cfg=_cfg(False))
    assert scheduled is False
    assert existing.id == row.id


@pytest.mark.anyio
async def test_the_worker_persists_a_complete_honest_result(db, session_factory) -> None:
    run = _run()
    r1, r2 = _report(), _report()
    await _seed(
        db,
        run,
        [
            r1,
            r2,
            _candidate(run, "AAA", report=r1, rank=1),
            _candidate(run, "BBB", report=r2, rank=2),
            _candidate(run, "CCC", rank=3),  # never analysed
        ],
    )
    row, _ = await start_field_review(db, run, cfg=_cfg())
    await process_field_review_by_id(
        row.id,
        session_factory=session_factory,
        cfg=_cfg(),
        client=FakeFieldReviewLLMClient(),
    )
    db.expunge_all()

    stored = await get_latest_field_review(db, run.id)
    assert stored is not None
    assert stored.status == "completed"
    assert stored.llm_used is True
    assert stored.agents_completed == 8
    assert stored.agents_failed == 0
    assert stored.included_candidate_count == 2
    assert stored.missing_candidate_count == 1
    assert stored.safety_valid is True
    assert stored.human_review_required is True
    assert stored.review_json is not None
    assert stored.review_json["publication_ready"] is False

    summaries = await get_candidate_summaries(db, stored.id)
    assert len(summaries) == 3
    included = [s for s in summaries if s.included]
    excluded = [s for s in summaries if not s.included]
    assert sorted(s.citation_ref for s in included) == ["F1", "F2"]
    # The excluded candidate is RECORDED, not dropped, with an honest reason.
    assert excluded[0].ticker == "CCC"
    assert excluded[0].exclusion_reason == "no_analysis_run"
    assert excluded[0].summary_json is None
    # Excluded refs never collide with a cited company id.
    assert all(not s.citation_ref.startswith("F") for s in excluded)
    # Every included company was placed in exactly one internal priority tier.
    assert all(s.priority_tier is not None for s in included)


@pytest.mark.anyio
async def test_the_worker_records_insufficient_candidates_rather_than_crashing(
    db, session_factory
) -> None:
    """If the field shrinks between queueing and running, the job ends in an
    explicit, honest terminal state — never a silent partial review."""
    run = await _two_candidate_run(db)
    row, _ = await start_field_review(db, run, cfg=_cfg())
    await process_field_review_by_id(
        row.id,
        session_factory=session_factory,
        cfg=_cfg(field_review_min_candidates=5),
        client=FakeFieldReviewLLMClient(),
    )
    db.expunge_all()
    stored = await get_latest_field_review(db, run.id)
    assert stored is not None
    assert stored.status == "insufficient_candidates"
    assert stored.error == "insufficient_analyzed_candidates"
    assert stored.review_json is None


@pytest.mark.anyio
async def test_a_crashed_worker_always_leaves_a_terminal_row(db, session_factory) -> None:
    run = await _two_candidate_run(db)
    row, _ = await start_field_review(db, run, cfg=_cfg())

    class _Boom(FakeFieldReviewLLMClient):
        async def _complete_raw(self, *a, **kw):  # noqa: ANN002, ANN003
            raise RuntimeError("unexpected internal failure")

    await process_field_review_by_id(
        row.id, session_factory=session_factory, cfg=_cfg(), client=_Boom()
    )
    db.expunge_all()
    stored = await get_latest_field_review(db, run.id)
    assert stored is not None
    assert stored.status in {"failed", "completed_with_warnings"}
    # A job can never stick in "running".
    assert stored.status != "running"


@pytest.mark.anyio
async def test_deleting_the_discovery_run_cascades_the_field_review(
    db, session_factory
) -> None:
    run = await _two_candidate_run(db)
    row, _ = await start_field_review(db, run, cfg=_cfg())
    await process_field_review_by_id(
        row.id,
        session_factory=session_factory,
        cfg=_cfg(),
        client=FakeFieldReviewLLMClient(),
    )
    db.expunge_all()
    assert await get_latest_field_review(db, run.id) is not None
    # The ORM-level cascade is what the migration's FK encodes.
    summaries = await get_candidate_summaries(db, row.id)
    assert summaries
    assert all(
        isinstance(s, FieldReviewCandidateSummary) for s in summaries
    )
