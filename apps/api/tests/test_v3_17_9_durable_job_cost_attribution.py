"""V3.17.9 — research spend is attributable to the job that caused it, or it is unknown.

THE DEFECT THIS FILE EXISTS FOR
===============================
``run_company_research_job`` holds the real durable id in ``ctx.job.id`` and passed
``content_run_id`` — an **AgentRun** id — into the only id parameter company research
had. Five tables carry a ``research_job_id`` foreign key to ``research_jobs.id``, and the
result was that every one of them was NULL in production:

    research_runs  research_leads  research_tool_calls
    calculation_records  research_run_consumption

Two separate causes, stacked. The id that *was* passed named no job row, so the V3.13.1
guard dropped it — correctly, because writing it aborts the transaction and takes the V2
report with it. And three of the five writers never took the argument at all, so even a
correct id would have reached two tables.

``ResearchDecision.cost_usd_total`` therefore had no producer, and the loop stopped at
``cost_unknown`` on a decision whose spend was not unpriced but **unattributed**. Those
are different problems and only one of them is fixed by configuring prices.

WHAT IS ACTUALLY BEING PROVEN HERE
==================================
Not "a column is populated". The claims are:

1. the DURABLE id reaches the pipeline unchanged, and an AgentRun id is never substituted;
2. the non-durable path stores NULL rather than a fake link;
3. the FK guard still fails closed, and a bad id still cannot cost the report;
4. attribution is by LINEAGE — two concurrent jobs for one company stay separable;
5. cost is DERIVED, so a replayed round closure cannot double it and a retry is counted
   once per attempt that was actually paid for;
6. unknown cost is ``None`` at every step and never 0.0.

EACH CLASS NAMES THE MUTATION IT CATCHES.

THE FOREIGN KEYS RUN ON REAL POSTGRESQL
=======================================
The unit suite runs on SQLite with foreign keys OFF — which is the reason the original
defect reached the eve of a production activation behind a green suite. Claim 3 is a
statement about constraint enforcement and transaction abort semantics, and a test that
never enforces a constraint proves nothing about either.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.services.escalation.controller import round_idempotency_key
from app.services.escalation.cost import (
    BASIS_NO_CONSUMPTION,
    BASIS_NO_JOBS,
    BASIS_PRICED,
    BASIS_UNPRICED,
    decision_job_key_prefix,
    jobs_for_decision,
    spend_for_decision,
)

pytestmark = pytest.mark.anyio

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 040 (SQLite runs with "
    "foreign keys OFF and cannot demonstrate a constraint)",
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


# --------------------------------------------------------------------------- #
# Fixtures that build the real rows
# --------------------------------------------------------------------------- #


async def _company(session, ticker: str | None = None):  # noqa: ANN001, ANN201
    from app.models.company import Company

    company = Company(
        id=uuid.uuid4(),
        ticker=ticker or f"T{uuid.uuid4().hex[:6].upper()}",
        exchange="US",
        name="Attribution Test Co",
        status="new",
    )
    session.add(company)
    await session.flush()
    return company.id


async def _decision(session, company_id, **over):  # noqa: ANN001, ANN003
    from app.services.escalation import store
    from app.services.escalation.evidence import snapshot_evidence

    return await store.create_decision(
        session,
        company_id=company_id,
        discovery_run_id=over.pop("run_id", uuid.uuid4()),
        discovery_candidate_id=None,
        source="discovery_council",
        decision="research_next",
        reason="attribution test",
        max_rounds=over.pop("max_rounds", 2),
        evidence_before=over.pop(
            "evidence_before", (await snapshot_evidence(session, company_id)).to_dict()
        ),
        **over,
    )


async def _job(session, *, company_id=None, key: str, status: str = "completed"):  # noqa: ANN001, ANN201
    from app.models.research_job import ResearchJob

    row = ResearchJob(
        id=uuid.uuid4(),
        job_type="company_research",
        idempotency_key=key,
        status=status,
        company_id=company_id,
        attempt=1,
        max_attempts=3,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _consumption(  # noqa: ANN201
    session,
    *,
    job_id,
    company_id=None,
    usd: float | None,
    model_calls: int = 3,
    model_tokens: int = 1000,
):
    from app.models.research_run_consumption import ResearchRunConsumption

    row = ResearchRunConsumption(
        id=uuid.uuid4(),
        run_type="company_research",
        research_job_id=job_id,
        company_id=company_id,
        model_calls=model_calls,
        model_tokens=model_tokens,
        elapsed_seconds=4.0,
        estimated_cost_usd=usd,
    )
    session.add(row)
    await session.flush()
    return row.id


# --------------------------------------------------------------------------- #
# 1. THE DURABLE ID REACHES THE PIPELINE UNCHANGED
#
# Catches: passing the AgentRun id; dropping the durable id entirely.
# --------------------------------------------------------------------------- #


class TestTheDurableIdIsWhatTravels:
    async def test_the_handler_passes_ctx_job_id_and_not_the_agent_run(
        self, monkeypatch
    ) -> None:  # noqa: ANN001
        """The producer→consumer seam, asserted at the exact joint that was broken.

        The handler holds two ids. It must hand the CONTENT id positionally and the
        DURABLE id on its own parameter — and a mutation that passes ``content_run_id``
        for both leaves the two equal, which is what this asserts against.
        """
        import app.services.company_research_service as svc
        import app.services.jobs.company_research_job as mod

        durable_job_id = uuid.uuid4()
        content_run_id = uuid.uuid4()
        seen: dict[str, Any] = {}

        async def fake_process(job_id, **kwargs):  # noqa: ANN001, ANN003
            seen["positional"] = job_id
            seen["durable"] = kwargs.get("durable_job_id")
            return {"analysis_report_id": uuid.uuid4(), "warnings": []}

        monkeypatch.setattr(svc, "process_company_research_by_id", fake_process)
        ctx = _Ctx(job_id=durable_job_id, agent_run_id=content_run_id)

        await mod.run_company_research_job(ctx)

        assert seen["durable"] == durable_job_id, (
            "the handler did not pass its own ctx.job.id — this is the defect"
        )
        assert seen["positional"] == content_run_id
        assert seen["durable"] != seen["positional"], (
            "the durable id and the AgentRun id are the same value, so one of them is "
            "being substituted for the other"
        )

    async def test_the_agent_run_id_is_never_accepted_as_a_job_id(
        self, session
    ) -> None:  # noqa: ANN001
        """The guard, at its own level. An AgentRun id names no ``research_jobs`` row."""
        from app.models.agent_run import AgentRun
        from app.services.jobs.lineage import resolve_durable_job_id

        run = AgentRun(
            id=uuid.uuid4(), workflow_name="company_research", status="running"
        )
        session.add(run)
        await session.flush()

        assert await resolve_durable_job_id(session, run.id) is None

    async def test_a_real_job_id_survives_the_guard(self, session) -> None:  # noqa: ANN001
        from app.services.jobs.lineage import resolve_durable_job_id

        job_id = await _job(session, key=f"company_research:{uuid.uuid4()}#1")

        assert await resolve_durable_job_id(session, job_id) == job_id

    async def test_the_guard_is_fail_closed_on_junk(self, session) -> None:  # noqa: ANN001
        """Never raises, never guesses. A run must not fail over an audit link."""
        from app.services.jobs.lineage import coerce_job_id, resolve_durable_job_id

        assert await resolve_durable_job_id(session, "not-a-uuid") is None
        assert await resolve_durable_job_id(session, None) is None
        assert await resolve_durable_job_id(session, uuid.uuid4()) is None
        assert coerce_job_id("not-a-uuid") is None


class _Ctx:
    """The worker's ``JobContext``, reduced to what the handler reads."""

    def __init__(self, *, job_id: uuid.UUID, agent_run_id: uuid.UUID) -> None:
        self.job = _JobView(job_id, agent_run_id)
        self.payload = {"company_id": str(uuid.uuid4())}
        self.stages: list[str] = []

    async def checkpoint(self, stage: str | None = None) -> None:
        if stage:
            self.stages.append(stage)


class _JobView:
    def __init__(self, job_id: uuid.UUID, agent_run_id: uuid.UUID) -> None:
        #: A STRING, exactly as `JobView` carries it — which is why the handler has to
        #: convert, and why a test that hands it a UUID would not exercise the real shape.
        self.id = str(job_id)
        self.agent_run_id = str(agent_run_id)


# --------------------------------------------------------------------------- #
# 2. THE NON-DURABLE PATH STAYS NULL
#
# Catches: inventing a link when there is no durable job.
# --------------------------------------------------------------------------- #


class TestTheV2PathIsHonestlyNull:
    async def test_no_durable_job_means_no_link_anywhere(self, monkeypatch) -> None:  # noqa: ANN001
        """The V2 background task passes no durable id, and nothing fabricates one.

        Asserted on BOTH consumers — the V3 pipeline and the consumption recorder —
        because a fallback added to either one would be a false statement about which
        job paid for the evidence.
        """
        import app.services.company_research_service as svc

        seen: dict[str, Any] = {}

        async def fake_v3(session, *, company, report_id, research_job_id=None, **_kw):  # noqa: ANN001
            seen["v3"] = research_job_id
            return None

        async def fake_record(session, *, research_job_id=None, **kw):  # noqa: ANN001, ANN003
            seen["consumption"] = research_job_id

        monkeypatch.setattr(svc, "_run_v3_pipeline", fake_v3)
        monkeypatch.setattr(
            "app.services.consumption_recorder.record_run", fake_record
        )

        await _drive_one_run(svc, monkeypatch, durable_job_id=None)

        assert seen["v3"] is None
        assert seen["consumption"] is None

    async def test_an_unresolvable_durable_id_is_dropped_not_written(
        self, monkeypatch
    ) -> None:  # noqa: ANN001
        """Fail closed at the service boundary, before the recorder can be handed it.

        The recorder writes OUTSIDE the V3 pipeline's SAVEPOINT, so an id validated only
        inside the pipeline would still reach a foreign key that can abort the session
        the V2 report is waiting to commit in.
        """
        import app.services.company_research_service as svc

        seen: dict[str, Any] = {}

        async def fake_v3(session, *, company, report_id, research_job_id=None, **_kw):  # noqa: ANN001
            seen["v3"] = research_job_id
            return None

        async def fake_record(session, *, research_job_id=None, **kw):  # noqa: ANN001, ANN003
            seen["consumption"] = research_job_id

        monkeypatch.setattr(svc, "_run_v3_pipeline", fake_v3)
        monkeypatch.setattr(
            "app.services.consumption_recorder.record_run", fake_record
        )

        # A well-formed UUID that names no job row — the shape an AgentRun id has.
        await _drive_one_run(svc, monkeypatch, durable_job_id=uuid.uuid4())

        assert seen["v3"] is None, "an id naming no research_jobs row was passed on"
        assert seen["consumption"] is None


async def _drive_one_run(svc, monkeypatch, *, durable_job_id):  # noqa: ANN001
    """One full ``process_company_research_by_id`` against an in-memory schema."""
    from app.models.company import Company

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    report_id = uuid.uuid4()
    async with maker() as s:
        company = Company(
            id=uuid.uuid4(),
            ticker="ATTR",
            exchange="US",
            name="Attribution Co",
            status="new",
        )
        s.add(company)
        await s.flush()
        envelope = await svc.create_job_record(s, company, provider_name="mock")
        job_id = uuid.UUID(str(envelope["job_id"]))

    async def run_analysis(db, **kwargs):  # noqa: ANN001, ANN003
        return {"status": "completed", "draft_report_id": None, "agent_run_id": None}

    async def generate_final_report(db, **kwargs):  # noqa: ANN001, ANN003
        class _Resp:
            report_id = None
            llm_used = False
            schema_valid = True
            safety_valid = True

        _Resp.report_id = report_id
        return _Resp()

    try:
        return await svc.process_company_research_by_id(
            job_id,
            durable_job_id=durable_job_id,
            session_factory=maker,
            run_analysis=run_analysis,
            generate_final_report=generate_final_report,
        )
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# 3. ATTRIBUTION IS BY LINEAGE, NEVER BY COMPANY
#
# Catches: attributing by company id or by "the latest job"; summing unrelated jobs.
# --------------------------------------------------------------------------- #


class TestAttributionIsByLineage:
    async def test_every_round_of_a_decision_is_found_not_just_the_last(
        self, session
    ) -> None:  # noqa: ANN001
        """``last_job_id`` names one round. A decision spanning two has two jobs.

        A mutation that attributes only ``last_job_id`` loses round 0's spend the moment
        round 1 is authorised — which is exactly when a cost cap needs it.
        """
        company_id = await _company(session)
        decision = await _decision(session, company_id)

        round0 = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
        )
        round1 = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 1) + "#1",
        )
        decision.last_job_id = round1
        await session.flush()

        found = await jobs_for_decision(session, decision)

        assert set(found) == {round0, round1}

    async def test_another_companys_job_is_never_attributed(self, session) -> None:  # noqa: ANN001
        company_id = await _company(session)
        other_id = await _company(session)
        decision = await _decision(session, company_id)
        other_decision = await _decision(session, other_id)

        mine = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
        )
        theirs = await _job(
            session,
            company_id=other_id,
            key=round_idempotency_key(other_decision.id, 0) + "#1",
        )
        await _consumption(session, job_id=mine, company_id=company_id, usd=0.10)
        await _consumption(session, job_id=theirs, company_id=other_id, usd=99.00)

        spend = await spend_for_decision(session, decision)

        assert spend.job_ids == (mine,)
        assert spend.cost_usd_total == pytest.approx(0.10)

    async def test_two_concurrent_jobs_for_one_company_stay_separable(
        self, session
    ) -> None:  # noqa: ANN001
        """THE CASE COMPANY-BASED ATTRIBUTION GETS WRONG.

        An operator's manual research and an escalation round, live at the same moment
        for the same company, share a ``company_id`` and nothing else. Attributing by
        company bills each of them for both.
        """
        company_id = await _company(session)
        decision = await _decision(session, company_id)

        escalation_job = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
            status="running",
        )
        manual_job = await _job(
            session,
            company_id=company_id,
            key=f"company_research:{company_id}#7",
            status="running",
        )
        await _consumption(
            session, job_id=escalation_job, company_id=company_id, usd=0.20
        )
        await _consumption(
            session, job_id=manual_job, company_id=company_id, usd=5.00
        )

        spend = await spend_for_decision(session, decision)

        assert spend.job_ids == (escalation_job,)
        assert spend.cost_usd_total == pytest.approx(0.20), (
            "the manual run's spend leaked into the decision — attribution is by "
            "company rather than by lineage"
        )

    def test_the_key_prefix_carries_the_decision_id(self) -> None:
        """The link is minted BY the decision, which is what makes it explicit."""
        decision_id = uuid.uuid4()
        prefix = decision_job_key_prefix(decision_id)

        assert str(decision_id) in prefix
        assert round_idempotency_key(decision_id, 0).startswith(prefix)
        assert round_idempotency_key(decision_id, 3).startswith(prefix)
        # No SQL LIKE metacharacter, or the prefix match would silently widen.
        assert "%" not in prefix and "_" not in prefix


# --------------------------------------------------------------------------- #
# 4. UNKNOWN IS NEVER ZERO, AND PARTIAL IS NEVER TOTAL
#
# Catches: coercing an unknown cost to 0.0; reporting a priced subtotal as the total.
# --------------------------------------------------------------------------- #


class TestUnknownIsNeverZero:
    async def test_a_decision_with_no_job_reports_unknown(self, session) -> None:  # noqa: ANN001
        company_id = await _company(session)
        decision = await _decision(session, company_id)

        spend = await spend_for_decision(session, decision)

        assert spend.cost_usd_total is None
        assert spend.cost_usd_total != 0.0
        assert spend.basis == BASIS_NO_JOBS

    async def test_jobs_without_consumption_rows_report_unknown(self, session) -> None:  # noqa: ANN001
        """The production state when ``V3_RUN_CONSUMPTION_ENABLED`` is off.

        "We did not measure" is not "it was free", and a 0.0 here would hand a cost cap
        an entire research round for nothing.
        """
        company_id = await _company(session)
        decision = await _decision(session, company_id)
        await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
        )

        spend = await spend_for_decision(session, decision)

        assert spend.job_ids != ()
        assert spend.cost_usd_total is None
        assert spend.basis == BASIS_NO_CONSUMPTION

    async def test_tool_call_units_show_consumption_with_the_recorder_off(
        self, session
    ) -> None:  # noqa: ANN001
        """THE PRODUCTION STATE: ``V3_RUN_CONSUMPTION_ENABLED`` is absent.

        No run-consumption row is ever written, so the money record is empty — but tool
        calls are written unconditionally and carry their units. That is what makes
        "lineage known, consumption measured, price unknown" a checkable statement
        rather than a claim, and it is why a NULL cost here says
        ``no_consumption_recorded`` and not ``unpriced_consumption``: those need
        different fixes.
        """
        from app.models.research_tool_call import ResearchToolCall

        company_id = await _company(session)
        decision = await _decision(session, company_id)
        job_id = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
        )
        session.add(
            ResearchToolCall(
                id=uuid.uuid4(),
                research_job_id=job_id,
                company_id=company_id,
                role="external_research",
                tool_name="search_web",
                outcome="ok",
                consumption_json={
                    "model_calls": 2,
                    "model_input_tokens": 27_000,
                    "model_output_tokens": 1_500,
                },
            )
        )
        # A tool that measured nothing. Absent is NOT zero, and neither is money.
        session.add(
            ResearchToolCall(
                id=uuid.uuid4(),
                research_job_id=job_id,
                company_id=company_id,
                role="analyst",
                tool_name="search_company_corpus",
                outcome="ok",
                consumption_json=None,
            )
        )
        await session.flush()

        spend = await spend_for_decision(session, decision)

        assert spend.basis == BASIS_NO_CONSUMPTION
        assert spend.cost_usd_total is None
        assert spend.tool_calls == 2
        assert spend.tool_call_model_calls == 2
        assert spend.tool_call_model_tokens == 28_500

    async def test_tool_call_units_are_never_added_to_the_total(
        self, session
    ) -> None:  # noqa: ANN001
        """They overlap the run record, so adding them would double the real spend."""
        from app.models.research_tool_call import ResearchToolCall

        company_id = await _company(session)
        decision = await _decision(session, company_id)
        job_id = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
        )
        session.add(
            ResearchToolCall(
                id=uuid.uuid4(),
                research_job_id=job_id,
                company_id=company_id,
                role="external_research",
                tool_name="search_web",
                outcome="ok",
                consumption_json={"model_calls": 2, "model_input_tokens": 27_000},
            )
        )
        await _consumption(session, job_id=job_id, company_id=company_id, usd=0.25)
        await session.flush()

        spend = await spend_for_decision(session, decision)

        assert spend.cost_usd_total == pytest.approx(0.25)
        assert spend.tool_call_model_tokens == 27_000

    async def test_unpriced_consumption_reports_unknown_not_a_subtotal(
        self, session
    ) -> None:  # noqa: ANN001
        """THE PRODUCTION STATE TODAY: measured, and unpriced.

        The measurement is reported — that is the whole point of separating "unlinked"
        from "unpriced" — but the total stays unknown, because a cap compared against a
        subtotal passes on spend it never saw.
        """
        company_id = await _company(session)
        decision = await _decision(session, company_id)
        job_id = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
        )
        await _consumption(
            session, job_id=job_id, company_id=company_id, usd=None, model_tokens=27_000
        )
        await _consumption(session, job_id=job_id, company_id=company_id, usd=0.75)

        spend = await spend_for_decision(session, decision)

        assert spend.basis == BASIS_UNPRICED
        assert spend.cost_usd_total is None
        assert spend.priced_subtotal_usd == pytest.approx(0.75)
        assert spend.unpriced_rows == 1 and spend.priced_rows == 1
        # LINEAGE KNOWN, CONSUMPTION KNOWN, PRICE UNKNOWN, COST NULL.
        assert spend.model_tokens == 28_000

    async def test_fully_priced_consumption_is_a_real_total(self, session) -> None:  # noqa: ANN001
        company_id = await _company(session)
        decision = await _decision(session, company_id)
        job_id = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
        )
        await _consumption(session, job_id=job_id, company_id=company_id, usd=0.25)
        await _consumption(session, job_id=job_id, company_id=company_id, usd=0.50)

        spend = await spend_for_decision(session, decision)

        assert spend.basis == BASIS_PRICED
        assert spend.cost_usd_total == pytest.approx(0.75)


# --------------------------------------------------------------------------- #
# 5. DERIVED, NEVER ACCUMULATED
#
# Catches: `decision.cost_usd_total += round_cost`, which doubles on a replayed closure.
# --------------------------------------------------------------------------- #


class TestRetriesAndReplaysDoNotDoubleCount:
    async def test_closing_the_same_round_twice_yields_the_same_total(
        self, session, monkeypatch
    ) -> None:  # noqa: ANN001
        """The terminal observer AND the startup sweep can both close one round.

        ``job_hook`` calls ``complete_round`` from a notification; ``reconcile_missed_
        rounds`` calls it again from the database when the notification was lost. Both
        firing is a NORMAL outcome, not a fault, so the total has to be recomputed rather
        than added to.
        """
        import app.core.config as config

        for name, value in (
            ("v3_research_escalation_enabled", True),
            ("v3_durable_jobs_enabled", True),
            ("v3_escalation_max_rounds", 2),
            ("v3_escalation_cost_cap_usd", 0.0),
        ):
            monkeypatch.setattr(config.settings, name, value, raising=False)

        from app.services.escalation.controller import complete_round

        company_id = await _company(session)
        decision = await _decision(session, company_id)
        job_id = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
        )
        decision.last_job_id = job_id
        await _consumption(session, job_id=job_id, company_id=company_id, usd=0.30)
        await session.flush()

        await complete_round(session, decision, job_completed=True)
        first = decision.cost_usd_total
        await complete_round(session, decision, job_completed=True)
        second = decision.cost_usd_total

        assert first == pytest.approx(0.30)
        assert second == pytest.approx(0.30), (
            "the total doubled on a replayed closure — spend is being accumulated "
            "rather than derived"
        )

    async def test_a_retry_is_counted_once_per_attempt_actually_paid_for(
        self, session
    ) -> None:  # noqa: ANN001
        """Two attempts of one job spent two budgets, and both are real.

        This is NOT double counting. A retried research run calls the providers again;
        recording one of the two would understate spend by exactly the amount a retry
        costs, which is the amount a cap exists to bound.
        """
        company_id = await _company(session)
        decision = await _decision(session, company_id)
        job_id = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
        )
        await _consumption(session, job_id=job_id, company_id=company_id, usd=0.30)
        await _consumption(session, job_id=job_id, company_id=company_id, usd=0.30)

        spend = await spend_for_decision(session, decision)

        assert spend.consumption_rows == 2
        assert spend.cost_usd_total == pytest.approx(0.60)

    async def test_a_second_generation_of_the_same_round_key_is_still_this_decision(
        self, session
    ) -> None:  # noqa: ANN001
        """``JobStore`` appends ``#N`` generations to a base key. All of them are ours."""
        company_id = await _company(session)
        decision = await _decision(session, company_id)
        gen1 = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
        )
        gen2 = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#2",
        )

        assert set(await jobs_for_decision(session, decision)) == {gen1, gen2}


# --------------------------------------------------------------------------- #
# 6. THE V3 HALF OF THE SPEND REACHES THE MONEY RECORD
#
# Catches: recording only the council's tokens, so the investigator and its tools — the
# larger half of a real run — are costed at zero.
# --------------------------------------------------------------------------- #


class TestTheV3SpendIsRecorded:
    def test_v3_units_are_folded_into_the_run_record(self) -> None:
        from app.services.company_research_service import _v3_consumption_units

        class _Outcome:
            consumption = {
                "model": {
                    "model_calls": 9,
                    "model_input_tokens": 27_000,
                    "model_output_tokens": 3_000,
                    "instrumented": [
                        "model_calls",
                        "model_input_tokens",
                        "model_output_tokens",
                    ],
                },
                "web_search_calls": 4,
                "url_fetch_calls": 6,
                "documents_fetched": 2,
                # Derived values that are NOT units. A loop over the summary's keys
                # would sum these as consumption.
                "cost_per_verified_useful_finding": None,
                "findings_total": 18,
            }

        units = _v3_consumption_units(_Outcome())

        assert units.model_calls == 9
        assert units.model_tokens == 30_000
        assert units.web_search_calls == 4
        assert units.url_fetch_calls == 6
        assert units.documents_downloaded == 2
        assert units.measured("web_search_calls")

    def test_wall_time_is_not_counted_twice(self) -> None:
        """The V2 record already spans the whole run, V3 included."""
        from app.services.company_research_service import _v3_consumption_units

        class _Outcome:
            consumption = {"model": {"model_calls": 1, "elapsed_seconds": 120.0}}

        assert _v3_consumption_units(_Outcome()).elapsed_seconds == 0.0

    def test_no_v3_run_contributes_nothing(self) -> None:
        from app.services.company_research_service import _v3_consumption_units

        assert _v3_consumption_units(None).model_calls == 0
        assert _v3_consumption_units(object()).model_calls == 0


# --------------------------------------------------------------------------- #
# 7. THE MONEY RECORD IS ONE TABLE
#
# Catches: summing `research_tool_calls` as well, which counts the investigator's tokens
# twice now that they are folded into the run row.
# --------------------------------------------------------------------------- #


class TestToolCallsProveLineageAndCarryNoMoney:
    async def test_tool_calls_are_counted_but_never_summed_as_cost(
        self, session
    ) -> None:  # noqa: ANN001
        from app.models.research_tool_call import ResearchToolCall

        company_id = await _company(session)
        decision = await _decision(session, company_id)
        job_id = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
        )
        for _ in range(3):
            session.add(
                ResearchToolCall(
                    id=uuid.uuid4(),
                    research_job_id=job_id,
                    company_id=company_id,
                    role="external_research",
                    tool_name="search_web",
                    outcome="ok",
                    item_count=5,
                    # A price that a future slice might start writing here. It must not
                    # become a second money source.
                    estimated_cost_usd=1.00,
                )
            )
        await _consumption(session, job_id=job_id, company_id=company_id, usd=0.25)
        await session.flush()

        spend = await spend_for_decision(session, decision)

        assert spend.tool_calls == 3, "the lineage proof is missing"
        assert spend.cost_usd_total == pytest.approx(0.25), (
            "tool-call costs were added to the run record's cost — the investigator's "
            "spend is in both, so this is a double count"
        )


# --------------------------------------------------------------------------- #
# 7b. THE LINEAGE IS READABLE WITHOUT DATABASE ACCESS
#
# This platform's PostgreSQL is not reachable from outside its virtual network — two
# acceptance sessions had a ledger read denied. So "was this job's work attributed to
# it?" has to be answerable over HTTP, or it cannot be answered at all in production.
# --------------------------------------------------------------------------- #


class TestJobLineageIsReadable:
    async def test_counts_only_rows_that_name_this_job(self, session) -> None:  # noqa: ANN001
        from app.models.ledger import ResearchRun
        from app.models.research_tool_call import ResearchToolCall
        from app.services.jobs.lineage import counts_for_job

        company_id = await _company(session)
        mine = await _job(session, company_id=company_id, key=f"a:{uuid.uuid4()}#1")
        theirs = await _job(session, company_id=company_id, key=f"b:{uuid.uuid4()}#1")

        session.add_all(
            [
                ResearchRun(
                    id=uuid.uuid4(),
                    company_id=company_id,
                    research_job_id=mine,
                    mode="standard",
                    status="complete",
                ),
                ResearchToolCall(
                    id=uuid.uuid4(),
                    research_job_id=mine,
                    company_id=company_id,
                    role="external_research",
                    tool_name="search_web",
                    outcome="ok",
                    consumption_json={"model_calls": 1, "model_input_tokens": 900},
                ),
                # Another job, same company. A count matched by company would fold it in.
                ResearchToolCall(
                    id=uuid.uuid4(),
                    research_job_id=theirs,
                    company_id=company_id,
                    role="external_research",
                    tool_name="search_web",
                    outcome="ok",
                ),
            ]
        )
        await _consumption(session, job_id=mine, company_id=company_id, usd=None)
        await session.flush()

        got = await counts_for_job(session, mine)

        assert got.exists is True
        assert got.attributed is True
        assert got.research_runs == 1
        assert got.tool_calls == 1, "another job's tool call was counted"
        assert got.tool_call_model_tokens == 900
        assert got.consumption_rows == 1
        assert got.unpriced_consumption_rows == 1
        # Unpriced stays unknown. Never 0.0.
        assert got.estimated_cost_usd is None
        assert got.basis == BASIS_UNPRICED

    async def test_the_basis_separates_the_two_nulls(self, session) -> None:  # noqa: ANN001
        """V3.17.9.1. `recorder off` and `nothing prices it` look identical in the column.

        Both report `estimated_cost_usd: null`. One is fixed by a setting and the other by
        a price book, so a reader who cannot tell them apart fixes the wrong one — which
        is exactly what happened when the recorder turned out to be off in production.
        """
        from app.services.jobs.lineage import counts_for_job

        company_id = await _company(session)

        recorder_off = await _job(
            session, company_id=company_id, key=f"f:{uuid.uuid4()}#1"
        )
        assert (await counts_for_job(session, recorder_off)).basis == (
            BASIS_NO_CONSUMPTION
        )

        measured = await _job(session, company_id=company_id, key=f"g:{uuid.uuid4()}#1")
        await _consumption(session, job_id=measured, company_id=company_id, usd=None)
        got = await counts_for_job(session, measured)
        assert got.basis == BASIS_UNPRICED
        assert got.estimated_cost_usd is None, "the two nulls are still the same null"

    async def test_a_job_that_exists_is_never_no_jobs(self, session) -> None:  # noqa: ANN001
        """`no_jobs` means a DECISION ordered no work. It cannot describe a job."""
        from app.services.jobs.lineage import counts_for_job

        company_id = await _company(session)
        job_id = await _job(session, company_id=company_id, key=f"h:{uuid.uuid4()}#1")

        assert (await counts_for_job(session, job_id)).basis != BASIS_NO_JOBS

    async def test_the_job_and_its_decision_agree_on_the_basis(self, session) -> None:  # noqa: ANN001
        """One vocabulary, one classification. Two copies of it would drift."""
        from app.services.jobs.lineage import counts_for_job

        company_id = await _company(session)
        decision = await _decision(session, company_id)
        job_id = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
        )
        await _consumption(session, job_id=job_id, company_id=company_id, usd=None)
        await session.flush()

        job_basis = (await counts_for_job(session, job_id)).basis
        decision_basis = (await spend_for_decision(session, decision)).basis

        assert job_basis == decision_basis == BASIS_UNPRICED

    async def test_an_unknown_job_is_absent_not_unattributed(self, session) -> None:  # noqa: ANN001
        """Two different facts, and collapsing them would hide the defect.

        "This job exists and nothing was attributed to it" is the bug. "No such durable
        job" is a V2 job id, or a typo. A reader must be able to tell them apart.
        """
        from app.services.jobs.lineage import counts_for_job

        got = await counts_for_job(session, uuid.uuid4())

        assert got.exists is False
        assert got.attributed is False

    async def test_a_fully_priced_job_reports_a_real_number(self, session) -> None:  # noqa: ANN001
        from app.services.jobs.lineage import counts_for_job

        company_id = await _company(session)
        job_id = await _job(session, company_id=company_id, key=f"c:{uuid.uuid4()}#1")
        await _consumption(session, job_id=job_id, company_id=company_id, usd=0.10)
        await _consumption(session, job_id=job_id, company_id=company_id, usd=0.15)

        got = await counts_for_job(session, job_id)

        assert got.estimated_cost_usd == pytest.approx(0.25)
        assert got.priced_consumption_rows == 2
        assert got.basis == BASIS_PRICED

    async def test_a_partly_priced_job_reports_no_number_at_all(self, session) -> None:  # noqa: ANN001
        """No subtotal. A cap compared against one passes on spend it never saw."""
        from app.services.jobs.lineage import counts_for_job

        company_id = await _company(session)
        job_id = await _job(session, company_id=company_id, key=f"d:{uuid.uuid4()}#1")
        await _consumption(session, job_id=job_id, company_id=company_id, usd=0.10)
        await _consumption(session, job_id=job_id, company_id=company_id, usd=None)

        got = await counts_for_job(session, job_id)

        assert got.estimated_cost_usd is None
        assert got.priced_consumption_rows == 1
        assert got.unpriced_consumption_rows == 1


class TestTheReadEndpointsProjectIt:
    async def test_the_lineage_route_refuses_to_invent_a_job(self, session) -> None:  # noqa: ANN001
        """404 for an id that is no durable job — never a row of zeros."""
        from fastapi import HTTPException

        from app.api.v1.company_research import get_company_research_job_lineage

        with pytest.raises(HTTPException) as caught:
            await get_company_research_job_lineage(uuid.uuid4(), db=session)

        assert caught.value.status_code == 404

    async def test_the_lineage_route_projects_the_counts(self, session) -> None:  # noqa: ANN001
        from app.api.v1.company_research import get_company_research_job_lineage
        from app.models.research_tool_call import ResearchToolCall

        company_id = await _company(session)
        job_id = await _job(session, company_id=company_id, key=f"e:{uuid.uuid4()}#1")
        session.add(
            ResearchToolCall(
                id=uuid.uuid4(),
                research_job_id=job_id,
                company_id=company_id,
                role="external_research",
                tool_name="search_web",
                outcome="ok",
                consumption_json={"model_calls": 1, "model_input_tokens": 1200},
            )
        )
        await session.flush()

        read = await get_company_research_job_lineage(job_id, db=session)

        assert read.job_id == job_id
        assert read.attributed is True
        assert read.tool_calls == 1
        assert read.tool_call_model_tokens == 1200
        assert read.estimated_cost_usd is None
        assert read.basis == BASIS_NO_CONSUMPTION
        assert "not investment advice" in read.disclaimer.lower()

    async def test_the_decision_detail_read_recomputes_spend(self, session) -> None:  # noqa: ANN001
        """A decision still running has no recorded spend, and is the one being watched."""
        from app.api.v1.research_decisions import _to_read

        company_id = await _company(session)
        decision = await _decision(session, company_id)
        job_id = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
        )
        decision.last_job_id = job_id
        await _consumption(session, job_id=job_id, company_id=company_id, usd=None)
        await session.flush()

        live = await _to_read(session, decision, live_spend=True)
        listed = await _to_read(session, decision)

        assert live.spend is not None
        assert live.spend.basis == BASIS_UNPRICED
        assert live.spend.job_ids == [job_id]
        assert live.cost_usd_total is None
        # A LIST read reports what the round recorded, and this round has not closed.
        assert listed.spend is None

    async def test_a_listed_decision_reports_the_recorded_spend(
        self, session, monkeypatch
    ) -> None:  # noqa: ANN001
        import app.core.config as config

        for name, value in (
            ("v3_research_escalation_enabled", True),
            ("v3_durable_jobs_enabled", True),
            ("v3_escalation_cost_cap_usd", 0.0),
        ):
            monkeypatch.setattr(config.settings, name, value, raising=False)

        from app.api.v1.research_decisions import _to_read
        from app.services.escalation.controller import complete_round

        company_id = await _company(session)
        decision = await _decision(session, company_id)
        job_id = await _job(
            session,
            company_id=company_id,
            key=round_idempotency_key(decision.id, 0) + "#1",
        )
        decision.last_job_id = job_id
        await _consumption(session, job_id=job_id, company_id=company_id, usd=None)
        await session.flush()
        await complete_round(session, decision, job_completed=True)

        listed = await _to_read(session, decision)

        assert listed.spend is not None
        assert listed.spend.basis == BASIS_UNPRICED
        assert listed.spend.cost_usd_total is None
        # The delta projection still works beside it — the spend key does not leak in.
        assert listed.improvement is not None


# --------------------------------------------------------------------------- #
# 8. REAL POSTGRESQL — the foreign keys, and what a bad one costs
# --------------------------------------------------------------------------- #


@pytest.fixture
async def pg():  # noqa: ANN201
    engine = create_async_engine(POSTGRES_URL, future=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _cfg(**over: Any):  # noqa: ANN202
    from app.core.config import Settings

    base: dict[str, Any] = {
        "v3_pipeline_enabled": True,
        "v3_agent_tools_enabled": True,
        "azure_openai_api_key": "",
        "azure_openai_endpoint": "",
        "deepseek_api_key": "",
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


async def _pg_company_and_job(maker):  # noqa: ANN001, ANN201
    from app.models.company import Company
    from app.models.research_job import ResearchJob

    async with maker() as session:
        company = Company(
            id=uuid.uuid4(),
            ticker=f"A{uuid.uuid4().hex[:6].upper()}",
            exchange="NASDAQ",
            name=f"Attribution Probe {uuid.uuid4().hex[:6]}",
            status="new",
            sector="Health Care",
            industry="Biotechnology",
        )
        job = ResearchJob(
            id=uuid.uuid4(),
            job_type="company_research",
            idempotency_key=f"company_research:{company.id}#1",
            status="running",
            company_id=company.id,
            attempt=1,
            max_attempts=3,
        )
        session.add_all([company, job])
        await session.flush()
        ids = (company.id, job.id)
        await session.commit()
    return ids


@requires_postgres
class TestForeignKeysOnRealPostgres:
    async def test_a_real_job_id_links_the_run_and_its_tool_calls(self, pg) -> None:  # noqa: ANN001
        """The positive case, which SQLite cannot demonstrate: the FK is satisfied.

        Both ``research_runs.research_job_id`` and ``research_tool_calls.research_job_id``
        must carry the SAME durable id. ``open_run`` took this argument since V3.5 and no
        caller passed it, so the run row was NULL even on a correctly-linked run.
        """
        from sqlalchemy import text

        from app.models.company import Company
        from app.services.pipeline.v3_pipeline import run_v3_research

        company_id, job_id = await _pg_company_and_job(pg)

        async with pg() as session:
            company = await session.get(Company, company_id)
            outcome = await run_v3_research(
                session, company, cfg=_cfg(), research_job_id=job_id
            )
            await session.commit()

        assert outcome.error is None, f"the run failed: {outcome.error}"

        async with pg() as session:
            run_link = (
                await session.execute(
                    text(
                        "SELECT research_job_id FROM research_runs WHERE id = :i"
                    ),
                    {"i": outcome.research_run_id},
                )
            ).scalar_one()
            tool_links = (
                await session.execute(
                    text(
                        "SELECT DISTINCT research_job_id FROM research_tool_calls "
                        "WHERE research_job_id = :j"
                    ),
                    {"j": job_id},
                )
            ).scalars().all()

        assert run_link == job_id, (
            "research_runs.research_job_id is not the durable job — open_run was not "
            "given the id"
        )
        assert list(tool_links) == [job_id]
        assert not any("not linked to a durable job row" in d for d in outcome.degraded)

    async def test_an_agent_run_id_still_fails_closed_and_keeps_the_report(
        self, pg
    ) -> None:  # noqa: ANN001
        """The guard must NOT have been weakened by making the good path work.

        An id naming no job row is still dropped, the run still says so, and the V2
        report written on the same session still commits.
        """
        from sqlalchemy import text

        from app.models.agent_run import AgentRun
        from app.models.company import Company
        from app.models.report import Report
        from app.services.pipeline.v3_pipeline import attach_to_report, run_v3_research

        company_id, _ = await _pg_company_and_job(pg)
        report_id = uuid.uuid4()

        async with pg() as session:
            run = AgentRun(
                id=uuid.uuid4(), workflow_name="company_research", status="running"
            )
            session.add(run)
            session.add(
                Report(
                    id=report_id,
                    company_id=company_id,
                    title="V2 report that must survive",
                    slug=f"v2-{uuid.uuid4().hex[:8]}",
                    report_type="company_analysis",
                    content_markdown='{"executive_summary": {"company_name": "x"}}',
                    status="draft",
                )
            )
            await session.flush()
            company = await session.get(Company, company_id)
            outcome = await run_v3_research(
                session, company, cfg=_cfg(), research_job_id=run.id
            )
            report = await session.get(Report, report_id)
            if report is not None:
                attach_to_report(report, outcome)
            await session.commit()

        async with pg() as session:
            survived = (
                await session.execute(
                    text("SELECT count(*) FROM reports WHERE id = :i"), {"i": report_id}
                )
            ).scalar_one()
            linked = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM research_tool_calls "
                        "WHERE research_job_id = :j"
                    ),
                    {"j": run.id},
                )
            ).scalar_one()

        assert survived == 1, "the V2 report was lost to a bad V3 link"
        assert linked == 0, "an AgentRun id was written into a research_jobs FK"
        assert any("not linked to a durable job row" in d for d in outcome.degraded), (
            "the run dropped the link silently — nobody can find out why the column "
            "is empty"
        )

    async def test_the_decision_sees_its_jobs_spend_and_nobody_elses(self, pg) -> None:  # noqa: ANN001
        """Attribution over a real database, with the FK actually enforced."""
        from app.models.research_job import ResearchJob
        from app.models.research_run_consumption import ResearchRunConsumption
        from app.services.escalation import store
        from app.services.escalation.evidence import snapshot_evidence

        company_id, other_job_id = await _pg_company_and_job(pg)

        async with pg() as session:
            decision = await store.create_decision(
                session,
                company_id=company_id,
                discovery_run_id=None,
                discovery_candidate_id=None,
                source="discovery_council",
                decision="research_next",
                reason="pg attribution test",
                max_rounds=2,
                evidence_before=(
                    await snapshot_evidence(session, company_id)
                ).to_dict(),
            )
            mine = ResearchJob(
                id=uuid.uuid4(),
                job_type="company_research",
                idempotency_key=round_idempotency_key(decision.id, 0) + "#1",
                status="completed",
                company_id=company_id,
                attempt=1,
                max_attempts=3,
            )
            session.add(mine)
            await session.flush()
            session.add_all(
                [
                    ResearchRunConsumption(
                        id=uuid.uuid4(),
                        run_type="company_research",
                        research_job_id=mine.id,
                        company_id=company_id,
                        estimated_cost_usd=0.40,
                    ),
                    # The same company, a different job. Company-based attribution would
                    # fold this in.
                    ResearchRunConsumption(
                        id=uuid.uuid4(),
                        run_type="company_research",
                        research_job_id=other_job_id,
                        company_id=company_id,
                        estimated_cost_usd=9.99,
                    ),
                ]
            )
            await session.commit()
            spend = await spend_for_decision(session, decision)

        assert spend.job_ids == (mine.id,)
        assert spend.cost_usd_total == pytest.approx(0.40)

    async def test_an_unpriced_row_on_real_postgres_keeps_the_total_null(
        self, pg
    ) -> None:  # noqa: ANN001
        """NULL survives the round trip as NULL, not as 0.0."""
        from app.models.research_job import ResearchJob
        from app.models.research_run_consumption import ResearchRunConsumption
        from app.services.escalation import store
        from app.services.escalation.evidence import snapshot_evidence

        company_id, _ = await _pg_company_and_job(pg)

        async with pg() as session:
            decision = await store.create_decision(
                session,
                company_id=company_id,
                discovery_run_id=None,
                discovery_candidate_id=None,
                source="discovery_council",
                decision="research_next",
                reason="pg unpriced test",
                max_rounds=2,
                evidence_before=(
                    await snapshot_evidence(session, company_id)
                ).to_dict(),
            )
            job = ResearchJob(
                id=uuid.uuid4(),
                job_type="company_research",
                idempotency_key=round_idempotency_key(decision.id, 0) + "#1",
                status="completed",
                company_id=company_id,
                attempt=1,
                max_attempts=3,
            )
            session.add(job)
            await session.flush()
            session.add(
                ResearchRunConsumption(
                    id=uuid.uuid4(),
                    run_type="company_research",
                    research_job_id=job.id,
                    company_id=company_id,
                    model_calls=11,
                    model_tokens=41_000,
                    estimated_cost_usd=None,
                )
            )
            await session.commit()
            spend = await spend_for_decision(session, decision)

        assert spend.basis == BASIS_UNPRICED
        assert spend.cost_usd_total is None
        assert spend.model_tokens == 41_000

    async def test_a_valid_link_survives_a_commit_on_every_lineage_table(
        self, pg
    ) -> None:  # noqa: ANN001
        """All five foreign keys accept the same durable id in one transaction.

        Each is a separate constraint, and the defect was that three of the five writers
        never passed the column at all — so a test covering only tool calls would still
        have been green.
        """
        from app.models.calculation import CalculationRecord
        from app.models.ledger import ResearchRun
        from app.models.research_lead import ResearchLeadRecord
        from app.models.research_run_consumption import ResearchRunConsumption
        from app.models.research_tool_call import ResearchToolCall

        company_id, job_id = await _pg_company_and_job(pg)

        async with pg() as session:
            session.add_all(
                [
                    ResearchRun(
                        id=uuid.uuid4(),
                        company_id=company_id,
                        research_job_id=job_id,
                        mode="standard",
                        status="complete",
                    ),
                    ResearchToolCall(
                        id=uuid.uuid4(),
                        research_job_id=job_id,
                        company_id=company_id,
                        role="external_research",
                        tool_name="search_web",
                        outcome="ok",
                    ),
                    ResearchLeadRecord(
                        id=uuid.uuid4(),
                        research_job_id=job_id,
                        company_id=company_id,
                        provider="test",
                        lead_key=f"lead-{uuid.uuid4().hex[:8]}",
                        slot_key=f"slot-{uuid.uuid4().hex[:8]}",
                        claim_text="a claim",
                        status="rejected",
                        rejection_reason="not_verified",
                    ),
                    CalculationRecord(
                        id=uuid.uuid4(),
                        company_id=company_id,
                        research_job_id=job_id,
                        definition_key="cash_runway_months",
                        definition_version=1,
                        status="refused",
                        refusal_reason="missing_input",
                    ),
                    ResearchRunConsumption(
                        id=uuid.uuid4(),
                        run_type="company_research",
                        research_job_id=job_id,
                        company_id=company_id,
                        estimated_cost_usd=None,
                    ),
                ]
            )
            await session.commit()

        async with pg() as session:
            counts = {}
            for table in (
                "research_runs",
                "research_tool_calls",
                "research_leads",
                "calculation_records",
                "research_run_consumption",
            ):
                counts[table] = (
                    await session.execute(
                        select(_literal_count(table)).where(
                            _literal_job(table) == job_id
                        )
                    )
                ).scalar_one()

        assert all(v == 1 for v in counts.values()), counts


def _literal_count(table: str):  # noqa: ANN202
    from sqlalchemy import func

    return func.count()


def _literal_job(table: str):  # noqa: ANN202
    from app.db.base import Base

    return Base.metadata.tables[table].c.research_job_id
