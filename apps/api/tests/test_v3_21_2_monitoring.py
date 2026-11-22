"""V3.21.2 — periodic tasks, team lists, the monitoring pass and its API (ADR-109).

The rules under test:
* a periodic task runs once per UTC bucket: two sweeps (two restarts) on one day give ONE
  job, and the next day gives another;
* the handler stamps ``source_poll_state`` (``last_ok_at`` / ``last_error``);
* the pass writes signals for LISTED companies only, bounded, and never starts research;
* the two new kinds (``missing_information``, ``catalyst_window``) are read from the latest
  research record and are not re-raised once acknowledged;
* team lists are idempotent; every API route needs an admin identity and answers 404 for
  an unknown list, entry, company or signal.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core.config import Settings
from app.db.base import Base
from app.models.company import Company
from app.models.monitoring import MonitoringSignal, Watchlist, WatchlistEntry
from app.models.research_job import ResearchJob
from app.models.research_record import CompanyResearchRecord
from app.models.source_poll_state import SourcePollState
from app.services.jobs import worker as worker_mod
from app.services.jobs.job_store import JobStore
from app.services.monitoring import pass_job, team_lists
from app.services.monitoring.detector import (
    KIND_CATALYST_WINDOW,
    KIND_MISSING_INFORMATION,
    KIND_NEW_DOCUMENT,
    MAX_SIGNALS_PER_PASS,
)
from app.services.safety_terms import scan_value
from app.services.scheduling import periodic
from tests import test_v3_monitoring as _base

NOW = datetime(2026, 11, 22, 3, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
ME = {"X-IB-Admin-Email": "Admin@Example.com"}


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


def _cfg(**overrides) -> Settings:  # noqa: ANN003
    base = {"v3_monitoring_enabled": True, "v3_periodic_tasks_enabled": True,
            "v3_monitoring_pass_enabled": True}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
async def factory(tmp_path):  # noqa: ANN001, ANN201
    from sqlalchemy import event

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/v3212.db",
                                 connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_connection, _record):  # noqa: ANN001, ANN202
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _wire(factory, monkeypatch):  # noqa: ANN001
    import app.db.session as session_mod

    monkeypatch.setattr(session_mod, "async_session_factory", factory)


@pytest.fixture
def only_monitoring_task():  # noqa: ANN201
    saved = list(periodic.registered_tasks())
    periodic.clear_periodic_tasks()
    periodic.register_periodic_task(pass_job.MONITORING_PASS_TASK)
    yield
    periodic.clear_periodic_tasks()
    for t in saved:
        periodic.register_periodic_task(t)


async def _company(s, ticker: str = "PNDORA") -> Company:  # noqa: ANN001
    c = Company(id=uuid.uuid4(), ticker=ticker, exchange="NASDAQ", name=f"{ticker} Inc",
                status="new")
    s.add(c)
    await s.flush()
    return c


def _record(company: Company, *, as_of: date = date(2026, 11, 1), rubric=None,  # noqa: ANN001
            blocks=None) -> CompanyResearchRecord:  # noqa: ANN001
    return CompanyResearchRecord(
        id=uuid.uuid4(), company_id=company.id, as_of=as_of, record_version="t",
        ticker=company.ticker, exchange=company.exchange, rubric_json=rubric,
        record_json={"blocks": blocks or {}})


_RUBRIC = {"recommendation": "WATCHLIST", "meaning": "m",
           "caps": [{"key": "valuation_insufficient",
                     "reason": "no computed valuation range (ADR-099 fails closed)"},
                    {"key": "low_confidence", "reason": "evidence confidence 0.4 below 0.6"}],
           "watchlist_triggers": ["A computed valuation range from cited inputs"]}
_BLOCKS = {"financial_capacity.earnings_track_record": {"state": "held", "quarters": [
    {"period": "Q3 FY2026", "report_date": (TODAY + timedelta(days=12)).isoformat(),
     "revenue_source_url": "https://www.sec.gov/x"},
    {"period": "Q2 FY2026", "report_date": (TODAY - timedelta(days=80)).isoformat()},
    {"period": "Q4 FY2026", "report_date": (TODAY + timedelta(days=95)).isoformat()}]}}


# ── periodic registry ───────────────────────────────────────────────────────── #


class TestBuckets:
    def test_daily_weekly_and_six_hourly_labels(self) -> None:
        t = datetime(2026, 11, 22, 13, 5, tzinfo=timezone.utc)
        assert periodic.bucket_label(periodic.DAILY, t) == "2026-11-22"
        assert periodic.bucket_label(periodic.WEEKLY, t) == "2026-W47"
        assert periodic.bucket_label(periodic.SIX_HOURLY, t) == "2026-11-22T12"
        assert periodic.bucket_start(periodic.WEEKLY, t).date() == date(2026, 11, 16)

    def test_due_is_against_the_bucket_start(self) -> None:
        task = pass_job.MONITORING_PASS_TASK
        assert periodic.is_due(task, None, NOW)
        assert not periodic.is_due(task, NOW - timedelta(hours=2), NOW)
        assert periodic.is_due(task, NOW - timedelta(hours=4), NOW)  # yesterday 23:00

    def test_an_interval_without_a_bucket_is_refused(self) -> None:
        with pytest.raises(ValueError, match="calendar bucket"):
            periodic.register_periodic_task(periodic.PeriodicTask(
                key="x", interval=timedelta(hours=5), job_type="x"))

    def test_the_monitoring_pass_is_registered_daily_with_its_own_flag(self) -> None:
        import app.services.jobs.handlers  # noqa: F401

        task = {t.key: t for t in periodic.registered_tasks()}["monitoring_pass"]
        assert task.interval == periodic.DAILY and task.job_type == "monitoring_pass"
        assert task.flag_attr == "v3_monitoring_pass_enabled"
        assert "monitoring_pass" in worker_mod.registry
        # Structural, not the process-global hook list (other tests clear it): the sweep
        # is decorated with register_sweep_hook, and the worker's handler module imports it.
        import ast
        from pathlib import Path

        tree = ast.parse(Path("app/services/scheduling/periodic.py").read_text())
        sweep = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                     and n.name == "periodic_tasks_sweep")
        assert any(isinstance(d, ast.Name) and d.id == "register_sweep_hook"
                   for d in sweep.decorator_list)
        handlers = Path("app/services/jobs/handlers.py").read_text()
        assert "from app.services.scheduling import periodic" in handlers
        assert "from app.services.monitoring import pass_job" in handlers
        assert Settings().v3_periodic_tasks_enabled is True
        assert Settings().v3_monitoring_pass_enabled is True


class TestOncePerBucket:
    async def test_two_sweeps_on_one_day_enqueue_one_job(
        self, factory, only_monitoring_task  # noqa: ANN001
    ) -> None:
        store = JobStore(factory)
        first = await periodic.run_periodic_tasks(cfg=_cfg(), session_factory=factory,
                                                  store=store, now=NOW)
        second = await periodic.run_periodic_tasks(
            cfg=_cfg(), session_factory=factory, store=store, now=NOW + timedelta(hours=5))
        assert first == ["monitoring_pass:2026-11-22"] and second == []
        async with factory() as s:
            jobs = (await s.execute(select(ResearchJob))).scalars().all()
            state = await s.get(SourcePollState, "monitoring_pass")
        assert len(jobs) == 1 and jobs[0].job_type == "monitoring_pass"
        assert jobs[0].payload_json["periodic_task_key"] == "monitoring_pass"
        assert state is not None and state.last_started_at is not None

    async def test_a_restart_after_the_job_finished_still_gives_one_job(
        self, factory, only_monitoring_task  # noqa: ANN001
    ) -> None:
        """Even with the job ended (which would let the store start a new generation),
        the claimed bucket holds."""
        store = JobStore(factory)
        await periodic.run_periodic_tasks(cfg=_cfg(), session_factory=factory,
                                          store=store, now=NOW)
        async with factory() as s:
            job = (await s.execute(select(ResearchJob))).scalar_one()
            job.status = "completed"
            await s.commit()
        assert await periodic.run_periodic_tasks(
            cfg=_cfg(), session_factory=factory, store=store,
            now=NOW + timedelta(hours=1)) == []
        nxt = await periodic.run_periodic_tasks(
            cfg=_cfg(), session_factory=factory, store=store, now=NOW + timedelta(days=1))
        assert nxt == ["monitoring_pass:2026-11-23"]

    async def test_a_claim_lost_to_another_process_enqueues_nothing(
        self, factory, only_monitoring_task  # noqa: ANN001
    ) -> None:
        async with factory() as s:
            s.add(SourcePollState(task_key="monitoring_pass",
                                  last_started_at=NOW - timedelta(minutes=1)))
            await s.commit()
        assert await periodic.run_periodic_tasks(
            cfg=_cfg(), session_factory=factory, store=JobStore(factory), now=NOW) == []

    async def test_the_master_switch_and_the_task_flag_each_stop_it(
        self, factory, only_monitoring_task  # noqa: ANN001
    ) -> None:
        for cfg in (_cfg(v3_periodic_tasks_enabled=False),
                    _cfg(v3_monitoring_pass_enabled=False)):
            assert await periodic.run_periodic_tasks(
                cfg=cfg, session_factory=factory, store=JobStore(factory), now=NOW) == []
        async with factory() as s:
            assert (await s.execute(select(func.count()).select_from(ResearchJob))
                    ).scalar() == 0

    async def test_the_sweep_hook_is_inert_without_durable_jobs(
        self, factory, only_monitoring_task, monkeypatch  # noqa: ANN001
    ) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "v3_durable_jobs_enabled", False)
        await periodic.periodic_tasks_sweep()
        async with factory() as s:
            assert await s.get(SourcePollState, "monitoring_pass") is None

    async def test_a_failed_enqueue_releases_the_bucket(
        self, factory, only_monitoring_task  # noqa: ANN001
    ) -> None:
        class Boom(JobStore):
            async def enqueue(self, **kw):  # noqa: ANN003, ANN201
                raise RuntimeError("db down")

        assert await periodic.run_periodic_tasks(
            cfg=_cfg(), session_factory=factory, store=Boom(factory), now=NOW) == []
        async with factory() as s:
            state = await s.get(SourcePollState, "monitoring_pass")
        assert state is not None and state.last_started_at is None
        assert "RuntimeError" in (state.last_error or "")


class TestHandlerStampsState:
    async def test_the_worker_runs_the_pass_and_stamps_last_ok(
        self, factory, only_monitoring_task, monkeypatch  # noqa: ANN001
    ) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "v3_monitoring_enabled", True)
        store = JobStore(factory)
        await periodic.run_periodic_tasks(cfg=_cfg(), session_factory=factory,
                                          store=store, now=datetime.now(timezone.utc))
        w = worker_mod.ResearchWorker(store, job_types=["monitoring_pass"],
                                      lease_seconds=60, heartbeat_seconds=30)
        assert await w.run_once()
        async with factory() as s:
            job = (await s.execute(select(ResearchJob))).scalar_one()
            state = await s.get(SourcePollState, "monitoring_pass")
        assert job.status in {"completed", "completed_with_warnings"}
        assert state is not None and state.last_ok_at is not None
        assert state.last_error is None

    async def test_a_failing_handler_stamps_the_error_type(
        self, factory, monkeypatch  # noqa: ANN001
    ) -> None:
        async def _boom(*a, **kw):  # noqa: ANN002, ANN003, ANN202
            raise ValueError("bad")

        monkeypatch.setattr(pass_job, "run_monitoring_pass", _boom)
        store = JobStore(factory)
        await store.enqueue(job_type="monitoring_pass", idempotency_key="mp:t",
                            payload={"periodic_task_key": "monitoring_pass"})
        w = worker_mod.ResearchWorker(store, job_types=["monitoring_pass"],
                                      lease_seconds=60, heartbeat_seconds=30)
        assert await w.run_once()
        async with factory() as s:
            state = await s.get(SourcePollState, "monitoring_pass")
        assert state is not None and state.last_error == "ValueError"
        assert state.last_ok_at is None


# ── team lists ──────────────────────────────────────────────────────────────── #


class TestTeamLists:
    async def test_lists_and_entries_are_idempotent(self, factory) -> None:  # noqa: ANN001
        async with factory() as s:
            c = await _company(s)
            a = await team_lists.ensure_team_list(s, "Shortlist")
            b = await team_lists.ensure_team_list(s, "Shortlist")
            first = await team_lists.add_to_team_list(
                s, "Shortlist", c.id, source="auto_report", note="rank 1",
                added_by_hash="h" * 64)
            again = await team_lists.add_to_team_list(s, "Shortlist", c.id,
                                                      source="manual")
            await s.commit()
            assert a.id == b.id and a.owner is None
            assert first.created and not again.created
            assert again.entry.id == first.entry.id and again.entry.source == "auto_report"
            assert (await s.execute(select(func.count()).select_from(WatchlistEntry))
                    ).scalar() == 1

    async def test_unknown_list_or_source_is_refused(self, factory) -> None:  # noqa: ANN001
        async with factory() as s:
            with pytest.raises(ValueError):
                await team_lists.ensure_team_list(s, "Favourites")
            c = await _company(s)
            with pytest.raises(ValueError):
                await team_lists.add_to_team_list(s, "Watchlist", c.id, source="email")


# ── record-derived kinds (pure) ─────────────────────────────────────────────── #


class TestRecordSignals:
    def test_missing_information_from_the_verdict_caps(self) -> None:
        c = Company(id=uuid.uuid4(), ticker="X", name="X", status="new")
        out = pass_job.missing_information_signals(_record(c, rubric=_RUBRIC),
                                                   company_id=c.id)
        assert [s.relates_to_question_key for s in out] == ["valuation_insufficient",
                                                            "low_confidence"]
        assert all(s.kind == KIND_MISSING_INFORMATION for s in out)
        assert pass_job.missing_information_signals(_record(c), company_id=c.id) == []

    def test_catalyst_window_only_within_thirty_days(self) -> None:
        c = Company(id=uuid.uuid4(), ticker="X", name="X", status="new")
        out = pass_job.catalyst_window_signals(_record(c, blocks=_BLOCKS),
                                               company_id=c.id, today=TODAY)
        assert len(out) == 1 and out[0].kind == KIND_CATALYST_WINDOW
        assert out[0].detail["label"] == "Q3 FY2026"
        assert out[0].detail["days_ahead"] == 12
        assert out[0].detail["source_url"] == "https://www.sec.gov/x"

    def test_summaries_say_what_the_record_holds_not_what_it_means(self) -> None:
        c = Company(id=uuid.uuid4(), ticker="X", name="X", status="new")
        rec = _record(c, rubric=_RUBRIC, blocks=_BLOCKS)
        signals = [*pass_job.missing_information_signals(rec, company_id=c.id),
                   *pass_job.catalyst_window_signals(rec, company_id=c.id, today=TODAY)]
        assert scan_value([s.summary for s in signals]) == []
        assert scan_value([s.detail for s in signals]) == []


# ── the pass ────────────────────────────────────────────────────────────────── #


class TestThePass:
    async def test_listed_companies_only_and_a_new_filing_is_signalled(
        self, factory  # noqa: ANN001
    ) -> None:
        async with factory() as s:
            listed = await _company(s, "LIST")
            other = await _company(s, "OTHR")
            for c in (listed, other):
                await _base._prior_run(s, c)
                await _base._document(s, c, retrieved=_base.NOW - timedelta(days=1),
                                      content_hash=(c.ticker[0].lower() * 64))
            await team_lists.add_to_team_list(s, "Shortlist", listed.id, source="manual")
            await s.commit()
            result = await pass_job.run_monitoring_pass(s, cfg=_cfg(), now=_base.NOW)
            rows = (await s.execute(select(MonitoringSignal))).scalars().all()
        assert result.companies == 1 and result.persisted >= 1
        assert {r.company_id for r in rows} == {listed.id}
        assert KIND_NEW_DOCUMENT in {r.kind for r in rows}

    async def test_record_kinds_are_written_once_even_after_acknowledge(
        self, factory  # noqa: ANN001
    ) -> None:
        async with factory() as s:
            c = await _company(s)
            s.add(_record(c, rubric=_RUBRIC, blocks=_BLOCKS))
            await team_lists.add_to_team_list(s, "Watchlist", c.id, source="report")
            await s.commit()
            await pass_job.run_monitoring_pass(s, cfg=_cfg(), now=NOW)
            kinds = sorted(r.kind for r in (await s.execute(
                select(MonitoringSignal))).scalars().all())
            assert kinds == [KIND_CATALYST_WINDOW, KIND_MISSING_INFORMATION,
                             KIND_MISSING_INFORMATION]
            for row in (await s.execute(select(MonitoringSignal))).scalars().all():
                row.status, row.acknowledged_at = "acknowledged", NOW
            await s.commit()
            again = await pass_job.run_monitoring_pass(s, cfg=_cfg(),
                                                       now=NOW + timedelta(days=1))
            assert again.persisted == 0 and again.duplicates_suppressed >= 3

    async def test_flag_off_observes_nothing_and_says_so(self, factory) -> None:  # noqa: ANN001
        async with factory() as s:
            c = await _company(s)
            s.add(_record(c, rubric=_RUBRIC))
            await team_lists.add_to_team_list(s, "Shortlist", c.id, source="manual")
            await s.commit()
            result = await pass_job.run_monitoring_pass(
                s, cfg=_cfg(v3_monitoring_enabled=False), now=NOW)
            assert result.companies == 0 and "V3_MONITORING_ENABLED is off" in (
                result.reason or "")
            assert (await s.execute(select(func.count()).select_from(MonitoringSignal))
                    ).scalar() == 0

    async def test_an_inactive_list_is_not_monitored(self, factory) -> None:  # noqa: ANN001
        async with factory() as s:
            c = await _company(s)
            s.add(_record(c, rubric=_RUBRIC))
            w = await team_lists.ensure_team_list(s, "Watchlist")
            await team_lists.add_entry(s, w, c.id)
            w.is_active = False
            await s.commit()
            result = await pass_job.run_monitoring_pass(s, cfg=_cfg(), now=NOW)
            assert result.companies == 0 and result.reason

    async def test_bounds(self, factory, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(pass_job, "MAX_COMPANIES_PER_PASS", 2)
        async with factory() as s:
            for i in range(3):
                c = await _company(s, f"B{i}")
                await team_lists.add_to_team_list(s, "Shortlist", c.id, source="manual")
            many = _record(c, rubric={"caps": [{"key": f"cap_{i}", "reason": "r"}
                                               for i in range(40)]})
            await s.commit()
            result = await pass_job.run_monitoring_pass(s, cfg=_cfg(), now=NOW)
            assert result.truncated and result.companies == 2
            s.add(many)
            await s.commit()
            one = await pass_job.monitor_company(s, c.id, cfg=_cfg(), now=NOW)
            assert len(one.signals) == MAX_SIGNALS_PER_PASS
            assert one.persisted == MAX_SIGNALS_PER_PASS

    def test_the_pass_never_starts_research(self) -> None:
        """No research entry point is imported or enqueued by the pass."""
        import ast
        from pathlib import Path

        tree = ast.parse(Path("app/services/monitoring/pass_job.py").read_text())
        modules = {n.module for n in ast.walk(tree)
                   if isinstance(n, ast.ImportFrom) and n.module}
        assert not any("company_research" in m or "research_job" in m or "escalation" in m
                       or "pipeline" in m for m in modules), modules
        calls = {n.func.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert "enqueue" not in calls


# ── the API ─────────────────────────────────────────────────────────────────── #


@pytest.fixture
async def client(factory):  # noqa: ANN001, ANN201
    from app.db.session import get_db
    from app.main import app

    async def _db():  # noqa: ANN202
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class TestApi:
    async def test_every_route_needs_an_admin(self, client) -> None:  # noqa: ANN001
        wid = uuid.uuid4()
        for method, path, body in (
            ("GET", "/api/v1/watchlists", None),
            ("GET", f"/api/v1/watchlists/{wid}", None),
            ("POST", f"/api/v1/watchlists/{wid}/entries", {"ticker": "X"}),
            ("DELETE", f"/api/v1/watchlists/{wid}/entries/{wid}", None),
            ("GET", "/api/v1/monitoring/signals", None),
            ("POST", f"/api/v1/monitoring/signals/{wid}/acknowledge", None),
            ("POST", "/api/v1/monitoring/run", {"company_id": str(wid)}),
        ):
            r = await client.request(method, path, json=body)
            assert r.status_code == 403, (method, path, r.status_code)

    async def test_in_staging_the_header_alone_is_not_an_admin(
        self, client, monkeypatch  # noqa: ANN001
    ) -> None:
        from app.api.v1 import watchlists as api

        monkeypatch.setattr(api.settings, "app_env", "staging")
        assert (await client.get("/api/v1/watchlists", headers=ME)).status_code == 403

    async def test_lists_entries_signals_and_acknowledge(
        self, client, factory, monkeypatch  # noqa: ANN001
    ) -> None:
        from app.api.v1 import watchlists as api
        from app.core.config import settings

        monkeypatch.setattr(settings, "v3_monitoring_enabled", True)
        monkeypatch.setattr(api.settings, "v3_durable_jobs_enabled", False)
        async with factory() as s:
            c = await _company(s, "POWL")
            s.add(_record(c, rubric=_RUBRIC, blocks=_BLOCKS))
            await s.commit()
        lists = (await client.get("/api/v1/watchlists", headers=ME)).json()
        assert [w["name"] for w in lists] == ["Shortlist", "Watchlist"]
        again = (await client.get("/api/v1/watchlists", headers=ME)).json()
        assert [w["id"] for w in again] == [w["id"] for w in lists]
        sid = lists[0]["id"]

        r = await client.post(f"/api/v1/watchlists/{sid}/entries", headers=ME,
                              json={"ticker": "powl", "exchange": "nasdaq",
                                    "source": "discovery_candidate", "note": "from run"})
        assert r.status_code == 201 and r.json()["created"] is True
        dup = await client.post(f"/api/v1/watchlists/{sid}/entries", headers=ME,
                                json={"company_id": str(c.id)})
        assert dup.status_code == 200 and dup.json()["created"] is False
        assert (await client.post(f"/api/v1/watchlists/{sid}/entries", headers=ME,
                                  json={"ticker": "ZZZZ"})).status_code == 404
        assert (await client.post(f"/api/v1/watchlists/{sid}/entries", headers=ME,
                                  json={"ticker": "POWL", "source": "email"})
                ).status_code == 422

        run = await client.post("/api/v1/monitoring/run", headers=ME,
                                json={"watchlist_id": sid})
        assert run.status_code == 200 and run.json()["mode"] == "inline"
        assert run.json()["result"]["persisted"] == 3

        detail = (await client.get(f"/api/v1/watchlists/{sid}", headers=ME)).json()
        entry = detail["entries"][0]
        assert entry["company"]["ticker"] == "POWL" and entry["source"] == "discovery_candidate"
        assert entry["latest_record"]["verdict"] == "WATCHLIST"
        assert entry["added_by_hash"] == api.owner_key_for("admin@example.com")
        assert len(entry["open_signals"]) == 3 and all(
            x["material"] for x in entry["open_signals"])
        summary = (await client.get("/api/v1/watchlists", headers=ME)).json()[0]
        assert summary["entry_count"] == 1 and summary["open_signal_count"] == 3

        feed = (await client.get("/api/v1/monitoring/signals",
                                 params={"company_id": str(c.id)}, headers=ME)).json()
        assert len(feed) == 3
        ack = await client.post(f"/api/v1/monitoring/signals/{feed[0]['id']}/acknowledge",
                                headers=ME)
        assert ack.status_code == 200 and ack.json()["status"] == "acknowledged"
        assert ack.json()["acknowledged_at"] is not None
        assert len((await client.get("/api/v1/monitoring/signals", headers=ME)).json()) == 2
        acked = (await client.get("/api/v1/monitoring/signals",
                                  params={"status": "acknowledged"}, headers=ME)).json()
        assert [x["id"] for x in acked] == [feed[0]["id"]]
        assert (await client.get("/api/v1/monitoring/signals", params={"status": "x"},
                                 headers=ME)).status_code == 422

        eid = entry["id"]
        assert (await client.delete(f"/api/v1/watchlists/{sid}/entries/{eid}",
                                    headers=ME)).status_code == 204
        assert (await client.delete(f"/api/v1/watchlists/{sid}/entries/{eid}",
                                    headers=ME)).status_code == 404
        async with factory() as s:  # signals are history: removing the entry keeps them
            assert (await s.execute(select(func.count()).select_from(MonitoringSignal))
                    ).scalar() == 3

    async def test_404s(self, client) -> None:  # noqa: ANN001
        unknown = uuid.uuid4()
        assert (await client.get(f"/api/v1/watchlists/{unknown}", headers=ME)
                ).status_code == 404
        assert (await client.post(f"/api/v1/watchlists/{unknown}/entries", headers=ME,
                                  json={"ticker": "X"})).status_code == 404
        assert (await client.post(f"/api/v1/monitoring/signals/{unknown}/acknowledge",
                                  headers=ME)).status_code == 404
        assert (await client.post("/api/v1/monitoring/run", headers=ME,
                                  json={"company_id": str(unknown)})).status_code == 404
        assert (await client.post("/api/v1/monitoring/run", headers=ME,
                                  json={})).status_code == 422
        sid = (await client.get("/api/v1/watchlists", headers=ME)).json()[0]["id"]
        assert (await client.post(f"/api/v1/watchlists/{sid}/entries", headers=ME,
                                  json={"company_id": str(unknown)})).status_code == 404

    async def test_with_durable_jobs_a_manual_run_is_enqueued(
        self, client, factory, monkeypatch  # noqa: ANN001
    ) -> None:
        from app.api.v1 import watchlists as api

        monkeypatch.setattr(api.settings, "v3_durable_jobs_enabled", True)
        async with factory() as s:
            c = await _company(s)
            await s.commit()
        r = await client.post("/api/v1/monitoring/run", headers=ME,
                              json={"company_id": str(c.id)})
        assert r.status_code == 200 and r.json()["mode"] == "enqueued"
        r2 = await client.post("/api/v1/monitoring/run", headers=ME,
                               json={"company_id": str(c.id)})
        assert r2.json()["job_id"] == r.json()["job_id"] and r2.json()["created"] is False
        async with factory() as s:
            job = (await s.execute(select(ResearchJob))).scalar_one()
        assert job.job_type == "monitoring_pass"
        assert job.payload_json["company_id"] == str(c.id)
        assert "periodic_task_key" not in job.payload_json


def test_watchlist_model_has_the_062_columns() -> None:
    cols = set(WatchlistEntry.__table__.columns.keys())
    assert {"added_by_hash", "source"} <= cols
    assert Watchlist.__tablename__ == "watchlists"


def test_migration_062_shape() -> None:
    from pathlib import Path

    text = Path("alembic/versions/062_add_periodic_tasks_and_team_lists.py").read_text()
    assert 'revision: str = "062"' in text
    for needle in ("source_poll_state", "missing_information", "catalyst_window",
                   "added_by_hash", "ck_watchlist_entries_source", "def downgrade"):
        assert needle in text, needle
    assert "DELETE FROM" not in text.upper()
