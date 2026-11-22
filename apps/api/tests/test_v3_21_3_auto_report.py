"""V3.21.3 — automatic report on the best eligible candidate of each thesis run (ADR-110).

The rules under test:
* a completed thesis run queues ONE durable research job for its best-ranked ELIGIBLE
  candidate (dynamic stage ``eligible``, hard size passed, not size-unverified, not
  novelty-held), linked to the candidate, at ``V3_AUTO_REPORT_MODE`` depth;
* the candidate's analysis-job envelope points at the job and reads its live state;
* the company lands on the team Shortlist with ``source=auto_report``;
* idempotent per run; at most ``V3_AUTO_REPORT_MAX_PER_DAY`` queued per UTC day; a fresh
  final report is LINKED instead; ticker runs, ``auto_report=false``, the flag off and
  durable jobs off never queue anything;
* an error while queuing is recorded as ``failed`` and the run stays completed.

Offline: the research job submit is a fake unless a test says otherwise.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core.config import Settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import research_job as _research_job  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models.company import Company
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.models.monitoring import Watchlist, WatchlistEntry
from app.models.report import Report
from app.models.research_job import ResearchJob
from app.schemas.market_discovery import DiscoveryRunRead, ThesisDiscoveryRunCreate
from app.services import market_discovery_service as mds
from app.services.discovery import auto_report as ar

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


def _cfg(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"v3_discovery_auto_report_enabled": True,
                            "v3_durable_jobs_enabled": True}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
async def factory(tmp_path):  # noqa: ANN001, ANN201
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/v3213.db",
                                 connect_args={"check_same_thread": False, "timeout": 30})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _wire(factory, monkeypatch):  # noqa: ANN001
    import app.db.session as session_mod

    monkeypatch.setattr(session_mod, "async_session_factory", factory)
    monkeypatch.setattr(mds, "async_session_factory", factory)


class FakeSubmit:
    def __init__(self, *, raises: Exception | None = None, created: bool = True) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raises = raises
        self.created = created

    async def __call__(self, company: Company, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        self.calls.append({"company_id": company.id, **kwargs})
        if self.raises is not None:
            raise self.raises
        return {"job_id": str(uuid.uuid4()), "status": "pending",
                "provider_name": kwargs.get("provider_name")}, self.created


# ── Builders ────────────────────────────────────────────────────────────── #


def _size(status: str = "pass") -> dict[str, Any]:
    return {"key": "size", "requested": ["under_2bn"], "hardness": "hard", "status": status}


def _v319(status: str = "eligible", *, size: str | None = "pass",
          company_id: uuid.UUID | None = None) -> dict[str, Any]:
    return {
        "identity": {"ticker": "X", "exchange": "NASDAQ",
                     "company_id": str(company_id) if company_id else None},
        "eligibility": {"status": status},
        "constraint_results": [_size(size)] if size else [],
    }


async def _company(s, ticker: str, exchange: str = "NASDAQ") -> Company:  # noqa: ANN001
    c = Company(id=uuid.uuid4(), ticker=ticker, exchange=exchange, name=f"{ticker} Inc",
                status="new")
    s.add(c)
    await s.flush()
    return c


async def _setup(factory, specs: list[tuple[str, dict[str, Any] | None]], *,  # noqa: ANN001
                 mode: str = "thesis", status: str = "completed",
                 config: dict[str, Any] | None = None,
                 dynamic: dict[str, Any] | None = None) -> uuid.UUID:
    """One run with one candidate (and company) per ``(ticker, v319)``, in rank order."""
    async with factory() as s:
        run = DiscoveryRun(
            id=uuid.uuid4(), status=status, mode=mode, provider_name="free_real",
            universe_source="thesis_generated", universe_count=len(specs),
            requested_tickers=[t for t, _ in specs], processed_count=len(specs),
            candidate_count=len(specs), error_count=0, lookback_days=90, warnings=[],
            config_json=config or {}, universe_json={"dynamic": dynamic or {}},
            safety_notes={}, human_review_required=True)
        s.add(run)
        for rank, (ticker, v319) in enumerate(specs, start=1):
            await _company(s, ticker)
            s.add(DiscoveryCandidate(
                id=uuid.uuid4(), discovery_run_id=run.id, ticker=ticker,
                exchange="NASDAQ", rank=rank,
                thesis_match_json={"v319": v319} if v319 is not None else {}))
        await s.commit()
        return run.id


async def _go(factory, run_id, *, cfg=None, submit=None, now=NOW):  # noqa: ANN001, ANN202
    async with factory() as s:
        run = await s.get(DiscoveryRun, run_id)
        cands = (await s.execute(sa.select(DiscoveryCandidate).where(
            DiscoveryCandidate.discovery_run_id == run_id))).scalars().all()
        return await ar.auto_report_for_run(s, run, cands, cfg=cfg or _cfg(),
                                            submit=submit, now=now)


async def _candidate(factory, run_id, ticker: str) -> DiscoveryCandidate:  # noqa: ANN001
    async with factory() as s:
        return (await s.execute(sa.select(DiscoveryCandidate).where(
            DiscoveryCandidate.discovery_run_id == run_id,
            DiscoveryCandidate.ticker == ticker))).scalar_one()


async def _stored(factory, run_id) -> dict[str, Any] | None:  # noqa: ANN001
    async with factory() as s:
        run = await s.get(DiscoveryRun, run_id)
        return run.auto_report_json


# ── Queued ──────────────────────────────────────────────────────────────── #


async def test_rank_one_eligible_is_queued_linked_and_shortlisted(factory) -> None:  # noqa: ANN001
    run_id = await _setup(factory, [("AAA", _v319()), ("BBB", _v319())])
    submit = FakeSubmit()
    record = await _go(factory, run_id, cfg=_cfg(v3_auto_report_mode="deep"), submit=submit)

    assert record is not None and record["state"] == ar.STATE_QUEUED
    assert record["ticker"] == "AAA" and record["job_id"]
    assert record["idempotency_key"] == f"auto_report:{run_id}"
    assert len(submit.calls) == 1
    cand = await _candidate(factory, run_id, "AAA")
    assert submit.calls[0]["discovery_candidate_id"] == cand.id
    assert submit.calls[0]["research_mode"] == "deep"
    assert (await _stored(factory, run_id))["state"] == "queued"
    # The candidate's existing analysis fields point at the durable job.
    env = mds.get_analysis_job_envelope(cand)
    assert env["status"] == "pending" and env["durable_job_id"] == record["job_id"]
    assert env["source"] == "auto_report"
    # Team Shortlist, source=auto_report.
    async with factory() as s:
        entries = (await s.execute(sa.select(WatchlistEntry, Watchlist).join(
            Watchlist, Watchlist.id == WatchlistEntry.watchlist_id))).all()
    assert [(w.name, e.source, e.company_id) for e, w in entries] == [
        ("Shortlist", "auto_report", uuid.UUID(record["company_id"]))]


async def test_default_mode_is_standard(factory) -> None:  # noqa: ANN001
    run_id = await _setup(factory, [("AAA", _v319())])
    submit = FakeSubmit()
    await _go(factory, run_id, submit=submit)
    assert submit.calls[0]["research_mode"] == "standard"


@pytest.mark.parametrize("first", [
    _v319(size="unknown"),                    # hard size not verified
    _v319("included_with_mismatch"),           # not dynamic-stage eligible
    _v319("eligible_unverified"),
    None,                                     # no dynamic stage at all
])
async def test_ineligible_rank_one_gives_way_to_next_eligible(factory, first) -> None:  # noqa: ANN001
    run_id = await _setup(factory, [("AAA", first), ("BBB", _v319())])
    record = await _go(factory, run_id, submit=FakeSubmit())
    assert record["state"] == "queued" and record["ticker"] == "BBB"


async def test_size_unverified_and_novelty_held_listings_are_skipped(factory) -> None:  # noqa: ANN001
    held = {"size_unverified": [{"identity": {"ticker": "AAA", "exchange": "NASDAQ"}}],
            "anti_convergence": {"novelty_held": [
                {"identity": {"ticker": "BBB", "exchange": "NASDAQ"}}]}}
    run_id = await _setup(factory, [("AAA", _v319()), ("BBB", _v319()), ("CCC", _v319())],
                          dynamic=held)
    record = await _go(factory, run_id, submit=FakeSubmit())
    assert record["ticker"] == "CCC"


async def test_no_eligible_candidate_is_recorded(factory) -> None:  # noqa: ANN001
    run_id = await _setup(factory, [("AAA", _v319(size="fail"))])
    submit = FakeSubmit()
    record = await _go(factory, run_id, submit=submit)
    assert record["state"] == "skipped" and record["reason"] == ar.REASON_NO_ELIGIBLE
    assert submit.calls == []


async def test_joining_an_in_flight_job_is_still_queued(factory) -> None:  # noqa: ANN001
    run_id = await _setup(factory, [("AAA", _v319())])
    record = await _go(factory, run_id, submit=FakeSubmit(created=False))
    assert record["state"] == "queued" and record["reason"] == ar.REASON_JOINED


# ── Bounds ──────────────────────────────────────────────────────────────── #


async def test_idempotent_on_rerun(factory) -> None:  # noqa: ANN001
    run_id = await _setup(factory, [("AAA", _v319())])
    submit = FakeSubmit()
    first = await _go(factory, run_id, submit=submit)
    second = await _go(factory, run_id, submit=submit)
    assert len(submit.calls) == 1
    assert second["job_id"] == first["job_id"]


async def test_daily_cap(factory) -> None:  # noqa: ANN001
    submit = FakeSubmit()
    cfg = _cfg(v3_auto_report_max_per_day=2)
    states = []
    for i in range(3):
        run_id = await _setup(factory, [(f"T{i}", _v319())])
        states.append((await _go(factory, run_id, cfg=cfg, submit=submit))["state"])
    assert states == ["queued", "queued", "skipped"]
    assert (await _stored(factory, run_id))["reason"] == ar.REASON_DAILY_CAP
    assert len(submit.calls) == 2
    # The next UTC day starts a fresh count.
    run_id = await _setup(factory, [("NEXT", _v319())])
    record = await _go(factory, run_id, cfg=cfg, submit=submit, now=NOW + timedelta(days=1))
    assert record["state"] == "queued"


async def test_fresh_report_is_linked_not_rerun(factory) -> None:  # noqa: ANN001
    run_id = await _setup(factory, [("AAA", _v319())])
    async with factory() as s:
        company = (await s.execute(sa.select(Company).where(Company.ticker == "AAA"))
                   ).scalar_one()
        report = Report(id=uuid.uuid4(), title="AAA", slug=f"aaa-{uuid.uuid4().hex[:6]}",
                        report_type="company_deep_dive", final_report_version="v3",
                        company_id=company.id, created_at=NOW - timedelta(days=3))
        s.add(report)
        await s.commit()
    submit = FakeSubmit()
    record = await _go(factory, run_id, submit=submit)
    assert record["state"] == "linked" and record["report_id"] == str(report.id)
    assert record["reason"] == ar.REASON_FRESH_REPORT
    assert submit.calls == []
    cand = await _candidate(factory, run_id, "AAA")
    assert cand.analysis_report_id == report.id


async def test_stale_report_is_not_linked(factory) -> None:  # noqa: ANN001
    run_id = await _setup(factory, [("AAA", _v319())])
    async with factory() as s:
        company = (await s.execute(sa.select(Company).where(Company.ticker == "AAA"))
                   ).scalar_one()
        s.add(Report(id=uuid.uuid4(), title="AAA", slug="aaa-old",
                     report_type="company_deep_dive", final_report_version="v3",
                     company_id=company.id, created_at=NOW - timedelta(days=30)))
        await s.commit()
    record = await _go(factory, run_id, submit=FakeSubmit())
    assert record["state"] == "queued"


async def test_ticker_run_is_not_auto_reported(factory) -> None:  # noqa: ANN001
    run_id = await _setup(factory, [("AAA", _v319())], mode="ticker")
    submit = FakeSubmit()
    assert await _go(factory, run_id, submit=submit) is None
    assert submit.calls == [] and await _stored(factory, run_id) is None


async def test_request_opt_out(factory) -> None:  # noqa: ANN001
    run_id = await _setup(factory, [("AAA", _v319())], config={"auto_report": False})
    submit = FakeSubmit()
    record = await _go(factory, run_id, submit=submit)
    assert record["state"] == "skipped" and record["reason"] == ar.REASON_OPTED_OUT
    assert submit.calls == []


async def test_flag_off_writes_nothing(factory) -> None:  # noqa: ANN001
    run_id = await _setup(factory, [("AAA", _v319())])
    submit = FakeSubmit()
    assert await _go(factory, run_id, cfg=_cfg(v3_discovery_auto_report_enabled=False),
                     submit=submit) is None
    assert submit.calls == [] and await _stored(factory, run_id) is None


async def test_durable_jobs_off_is_recorded_as_skipped(factory) -> None:  # noqa: ANN001
    run_id = await _setup(factory, [("AAA", _v319())])
    submit = FakeSubmit()
    record = await _go(factory, run_id, cfg=_cfg(v3_durable_jobs_enabled=False),
                       submit=submit)
    assert record["state"] == "skipped" and record["reason"] == ar.REASON_DURABLE_OFF
    assert submit.calls == []


async def test_failed_run_is_not_auto_reported(factory) -> None:  # noqa: ANN001
    run_id = await _setup(factory, [("AAA", _v319())], status="failed")
    submit = FakeSubmit()
    record = await _go(factory, run_id, submit=submit)
    assert record["reason"] == ar.REASON_RUN_NOT_COMPLETED and submit.calls == []


# ── Never fatal ─────────────────────────────────────────────────────────── #


async def test_submit_error_is_recorded_and_run_stays_completed(factory) -> None:  # noqa: ANN001
    run_id = await _setup(factory, [("AAA", _v319())])
    record = await _go(factory, run_id, submit=FakeSubmit(raises=RuntimeError("boom")))
    assert record["state"] == "failed" and record["reason"] == "submit_error:RuntimeError"
    async with factory() as s:
        run = await s.get(DiscoveryRun, run_id)
        assert run.status == "completed"
        assert "boom" not in str(run.auto_report_json)


async def test_shortlist_failure_keeps_the_queued_record(factory, monkeypatch) -> None:  # noqa: ANN001
    from app.services.monitoring import team_lists

    async def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("no lists")

    monkeypatch.setattr(team_lists, "add_to_team_list", broken)
    run_id = await _setup(factory, [("AAA", _v319())])
    record = await _go(factory, run_id, submit=FakeSubmit())
    assert record["state"] == "queued"
    assert record["note"] == "shortlist_error:RuntimeError"
    assert (await _stored(factory, run_id))["state"] == "queued"


async def test_process_run_hook_never_fails_the_run(factory, monkeypatch) -> None:  # noqa: ANN001
    """End to end through ``process_run``: the hook runs after the final commit."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "v3_discovery_auto_report_enabled", True)
    monkeypatch.setattr(settings, "v3_durable_jobs_enabled", True)
    monkeypatch.setattr(settings, "v3_dynamic_discovery_enabled", False)
    calls: list[str] = []

    async def exploding_submit(company: Company, **_k: Any) -> Any:
        calls.append(company.ticker)
        raise ConnectionError("db down")

    monkeypatch.setattr(ar, "_default_submit", exploding_submit)

    async def extract(db, *, ticker, exchange, provider_name, lookback_days, **_):  # noqa: ANN001, ANN003, ANN202
        from tests.test_v3_discovery_durable import _signal

        return mds.ExtractedSignal(
            ticker=ticker, exchange=exchange, provider_name=provider_name,
            signal=_signal(ticker), status="ok", error=None, analysis_report_id=None,
            agent_run_id=None, schema_valid=False, safety_valid=True)

    async with factory() as s:
        await _company(s, "AAA", "US")
        await s.commit()
        run = DiscoveryRun(
            id=uuid.uuid4(), status="pending", mode="thesis", provider_name="free_real",
            universe_source="thesis_generated", universe_count=1,
            requested_tickers=["AAA"], processed_count=0, candidate_count=0,
            error_count=0, lookback_days=90, warnings=[], config_json={},
            universe_json={"items": [{"ticker": "AAA", "exchange": "NASDAQ",
                                      "v319": _v319()}]},
            safety_notes={}, human_review_required=True)
        s.add(run)
        await s.commit()
        run = await mds.process_run(s, run, extractor=extract)
        assert run.status == "completed"
    assert calls == ["AAA"]
    stored = await _stored(factory, run.id)
    assert stored["state"] == "failed" and stored["reason"] == "submit_error:ConnectionError"


# ── The candidate envelope reads the durable job ────────────────────────── #


async def test_real_submit_enqueues_a_durable_job_and_overlays(factory, monkeypatch) -> None:  # noqa: ANN001
    from app.services.jobs import company_research_job

    run_id = await _setup(factory, [("AAA", _v319())])
    record = await _go(factory, run_id)  # the real company_research_job.submit
    assert record["state"] == "queued"
    async with factory() as s:
        job = (await s.execute(sa.select(ResearchJob))).scalar_one()
        assert str(job.id) == record["job_id"]
        assert job.idempotency_key.startswith("company_research:")
        cand = await s.get(DiscoveryCandidate, uuid.UUID(record["candidate_id"]))
        env = await mds.refresh_durable_analysis_job(s, cand)
        assert env["status"] == "pending"
        # A durable envelope is never declared abandoned by this process's boot time.
        assert mds.describe_analysis_job(env)["status"] == "pending"
        # A second manual "Run analysis" joins rather than starting another run.
        _env, scheduled = await mds.start_candidate_analysis(s, cand)
        assert scheduled is False

    report_id = uuid.uuid4()
    async with factory() as s:
        s.add(Report(id=report_id, title="AAA", slug="aaa-new",
                     report_type="company_deep_dive", final_report_version="v3"))
        await s.commit()

    async def done(_job_id: uuid.UUID, **_k: Any) -> dict[str, Any]:
        return {"job_id": record["job_id"], "status": "completed",
                "analysis_report_id": str(report_id), "warnings": [],
                "completed_at": NOW.isoformat()}

    monkeypatch.setattr(company_research_job, "get_envelope", done)
    async with factory() as s:
        cand = await s.get(DiscoveryCandidate, uuid.UUID(record["candidate_id"]))
        env = await mds.refresh_durable_analysis_job(s, cand)
        assert env["status"] == "completed"
        assert env["analysis_report_id"] == str(report_id)
    cand = await _candidate(factory, run_id, "AAA")
    assert cand.analysis_report_id == report_id
    assert mds.get_analysis_job_envelope(cand)["status"] == "completed"


async def test_durable_job_ending_without_a_report_reads_failed(factory, monkeypatch) -> None:  # noqa: ANN001
    from app.services.jobs import company_research_job

    run_id = await _setup(factory, [("AAA", _v319())])
    record = await _go(factory, run_id, submit=FakeSubmit())

    async def dead(_job_id: uuid.UUID, **_k: Any) -> dict[str, Any]:
        return {"status": "dead_letter", "warnings": []}

    monkeypatch.setattr(company_research_job, "get_envelope", dead)
    async with factory() as s:
        cand = await s.get(DiscoveryCandidate, uuid.UUID(record["candidate_id"]))
        env = await mds.refresh_durable_analysis_job(s, cand)
    assert env["status"] == "failed" and env["error"] == "dead_letter"


# ── Schema and migration ────────────────────────────────────────────────── #


def test_request_flag_and_response_field() -> None:
    assert ThesisDiscoveryRunCreate(thesis_text="US cybersecurity").auto_report is None
    assert ThesisDiscoveryRunCreate(thesis_text="US cyber", auto_report=False).auto_report \
        is False
    assert "auto_report_json" in DiscoveryRunRead.model_fields


def test_model_has_the_063_column() -> None:
    col = DiscoveryRun.__table__.c.auto_report_json
    assert col.nullable is True


def test_migration_063_shape() -> None:
    text = Path("alembic/versions/063_add_discovery_run_auto_report.py").read_text()
    assert 'revision: str = "063"' in text
    assert 'down_revision: str | None = "062"' in text
    assert 'op.add_column(\n        "discovery_runs"' in text
    assert 'op.drop_column("discovery_runs", "auto_report_json")' in text
