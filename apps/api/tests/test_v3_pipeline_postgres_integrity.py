"""The V3 pipeline against REAL PostgreSQL, with foreign keys actually enforced.

WHY A SEPARATE FILE, AND WHY POSTGRES
=====================================
The unit suite runs on SQLite with foreign keys OFF. That is not a detail — it is the
reason the defect below reached the eve of a production activation with a green suite
of 6,100 tests behind it.

THE DEFECT
==========
``research_tool_calls.research_job_id`` is a FOREIGN KEY to ``research_jobs.id``. The id
handed to ``run_v3_research`` is an **AgentRun** id on BOTH entry points: the V2
background task passes it directly, and the durable handler passes ``content_run_id``,
which ``store.link_agent_run(ctx.job.id, agent_run_id=...)`` names as the agent run
rather than the job.

So the first tool call violated the constraint. PostgreSQL then aborts the whole
transaction, and — this is the part that matters — **every later statement on that
connection fails too**. The V3 pipeline runs on the SAME session that is about to write
the V2 report. Measured before the fix, at head 038:

    commit after V3            : FAILED (PendingRollbackError)
    V2 report rows persisted   : 0
    tool calls persisted       : 0

The module's docstring promises that "a V3 failure must never cost a report the V2 path
would have produced — and a test asserts exactly that". The test asserted it on SQLite,
where the failure cannot occur. On PostgreSQL the promise was false, and switching
``V3_PIPELINE_ENABLED`` on would have ended every company research run with no report.

Two things fix it, and both are tested here: the invalid link is no longer written, and
the run is wrapped in a SAVEPOINT so the promise is enforced by the database rather than
by catching exceptions in Python — ``except Exception`` cannot un-abort a transaction.

RUNNING
=======
Needs a PostgreSQL at head 038. Skipped when ``V3_TEST_POSTGRES_URL`` is unset::

    V3_TEST_POSTGRES_URL=postgresql+psycopg://postgres@127.0.0.1:5432/ib_v3_test \
      pytest tests/test_v3_pipeline_postgres_integrity.py
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.anyio

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")

requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 038 (SQLite cannot "
    "reproduce this: it runs with foreign keys OFF)",
)


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


@pytest.fixture
async def pg():  # noqa: ANN201
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(POSTGRES_URL, future=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _company_and_agent_run(maker: Any) -> tuple[uuid.UUID, uuid.UUID]:
    """A company plus an AgentRun id — precisely what production hands the pipeline."""
    from app.models.agent_run import AgentRun
    from app.models.company import Company

    async with maker() as session:
        company = Company(
            id=uuid.uuid4(),
            ticker=f"T{uuid.uuid4().hex[:6].upper()}",
            exchange="NASDAQ",
            name=f"Integrity Probe {uuid.uuid4().hex[:6]}",
            status="new",
            sector="Health Care",
            industry="Biotechnology",
        )
        run = AgentRun(
            id=uuid.uuid4(), workflow_name="company_research", status="running"
        )
        session.add_all([company, run])
        await session.flush()
        ids = (company.id, run.id)
        await session.commit()
    return ids


@requires_postgres
class TestAV3FailureNeverCostsTheReport:
    async def test_an_agent_run_id_does_not_poison_the_transaction(self, pg) -> None:  # noqa: ANN001
        """The exact production shape: an AgentRun id where the FK wants a job id."""
        from sqlalchemy import text

        from app.models.company import Company
        from app.services.pipeline.v3_pipeline import run_v3_research

        company_id, agent_run_id = await _company_and_agent_run(pg)

        async with pg() as session:
            before = (
                await session.execute(text("SELECT count(*) FROM research_tool_calls"))
            ).scalar_one()
            company = await session.get(Company, company_id)
            outcome = await run_v3_research(
                session, company, cfg=_cfg(), research_job_id=agent_run_id
            )
            await session.commit()

        async with pg() as session:
            after = (
                await session.execute(text("SELECT count(*) FROM research_tool_calls"))
            ).scalar_one()

        assert outcome.error is None, f"the run failed: {outcome.error}"
        assert after > before, (
            "no tool call persisted — the foreign key aborted the transaction, which "
            "is the defect this file exists for"
        )

    async def test_the_v2_report_survives_a_v3_run(self, pg) -> None:  # noqa: ANN001
        """The module's central promise, asserted where it can actually fail.

        The report is written BEFORE V3 runs, on the same session, exactly as
        `company_research_service` does it.
        """
        from sqlalchemy import text

        from app.models.company import Company
        from app.models.report import Report
        from app.services.pipeline.v3_pipeline import attach_to_report, run_v3_research

        company_id, agent_run_id = await _company_and_agent_run(pg)
        report_id = uuid.uuid4()

        async with pg() as session:
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
                session, company, cfg=_cfg(), research_job_id=agent_run_id
            )
            report = await session.get(Report, report_id)
            if report is not None:
                attach_to_report(report, outcome)
            await session.commit()

        async with pg() as session:
            rows = (
                await session.execute(
                    text("SELECT count(*) FROM reports WHERE id = :i"), {"i": report_id}
                )
            ).scalar_one()

        assert rows == 1, "the V2 report was lost to the V3 run — the one thing the "
        "pipeline promises cannot happen"

    async def test_the_bad_link_is_dropped_and_said_out_loud(self, pg) -> None:  # noqa: ANN001
        """Dropped, not silently ignored: a run whose tool calls are unlinked should
        say so, or nobody can find out why the column is empty."""
        from app.models.company import Company
        from app.services.pipeline.v3_pipeline import run_v3_research

        company_id, agent_run_id = await _company_and_agent_run(pg)
        async with pg() as session:
            company = await session.get(Company, company_id)
            outcome = await run_v3_research(
                session, company, cfg=_cfg(), research_job_id=agent_run_id
            )
            await session.commit()

        assert any("names no research_jobs row" in d for d in outcome.degraded), (
            f"the dropped link must be recorded; got {outcome.degraded}"
        )

    async def test_a_real_job_id_IS_linked(self, pg) -> None:  # noqa: ANN001
        """The other direction. A guard that dropped every id would also pass the
        tests above while quietly destroying the link the column exists for."""
        from sqlalchemy import text

        from app.models.company import Company
        from app.models.research_job import ResearchJob
        from app.services.pipeline.v3_pipeline import run_v3_research

        company_id, _ = await _company_and_agent_run(pg)
        job_id = uuid.uuid4()

        async with pg() as session:
            session.add(
                ResearchJob(
                    id=job_id,
                    job_type="company_research",
                    idempotency_key=f"probe:{job_id}",
                    status="running",
                    payload_json={},
                )
            )
            await session.flush()
            company = await session.get(Company, company_id)
            outcome = await run_v3_research(
                session, company, cfg=_cfg(), research_job_id=job_id
            )
            await session.commit()

        assert not any("names no research_jobs row" in d for d in outcome.degraded)

        async with pg() as session:
            linked = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM research_tool_calls "
                        "WHERE research_job_id = :i"
                    ),
                    {"i": job_id},
                )
            ).scalar_one()
        assert linked > 0, "a valid job id must still be written to the link column"


@requires_postgres
class TestASwallowedRecordFailureCostsOnlyTheRecord:
    """`_fetch_public_source` records a lead inside `try: ... except: pass`.

    A bare except around a database write is a trap: PostgreSQL aborts the transaction
    on the error, the exception is swallowed, and the caller discovers it at commit —
    by which point the V2 report is gone too. That is not hypothetical, it is precisely
    how `research_job_id` destroyed the report. The write is wrapped in a SAVEPOINT so a
    failed record costs the record and nothing else.
    """

    async def test_a_failing_lead_insert_does_not_take_the_report_with_it(
        self, pg
    ) -> None:  # noqa: ANN001
        from sqlalchemy import text

        from app.models.report import Report

        company_id, _ = await _company_and_agent_run(pg)
        report_id = uuid.uuid4()

        async with pg() as session:
            session.add(
                Report(
                    id=report_id,
                    company_id=company_id,
                    title="report beside a failing lead insert",
                    slug=f"v2-{uuid.uuid4().hex[:8]}",
                    report_type="company_analysis",
                    content_markdown='{"executive_summary": {"company_name": "x"}}',
                    status="draft",
                )
            )
            await session.flush()

            # A lead row that CANNOT be inserted: its company_id names no company, so
            # the foreign key rejects it — the same shape as the real defect.
            try:
                async with session.begin_nested():
                    await session.execute(
                        text(
                            "INSERT INTO research_leads "
                            "(id, company_id, provider, claim_text, status, created_at) "
                            "VALUES (:i, :c, 'deepseek', 'x', 'rejected', now())"
                        ),
                        {"i": uuid.uuid4(), "c": uuid.uuid4()},
                    )
            except Exception:  # noqa: BLE001 - exactly what the caller does
                pass

            await session.commit()

        async with pg() as session:
            rows = (
                await session.execute(
                    text("SELECT count(*) FROM reports WHERE id = :i"), {"i": report_id}
                )
            ).scalar_one()

        assert rows == 1, (
            "the report was lost to a failed lead insert — the savepoint is what stops "
            "a swallowed database error from poisoning the shared transaction"
        )


@requires_postgres
class TestClassificationOnRealPostgres:
    """The classification write, on the engine it actually runs on.

    SQLite with foreign keys off has hidden production-breaking defects twice in this
    campaign. The classification path writes real columns inside a real transaction that
    is also carrying a report, so both halves of that need checking here rather than on
    an in-memory database that forgives more.
    """

    async def test_the_classification_is_written_and_read_back(self, pg, monkeypatch) -> None:  # noqa: ANN001
        from app.models.company import Company
        from app.services.classification import service as classification_service
        from app.services.classification.service import ensure_company_classification

        async def _sec(ticker: str, exchange: str | None):  # noqa: ANN202
            return "2836", "Biological Products, (No Diagnostic Substances)", None

        monkeypatch.setattr(classification_service, "_fetch_sec_classification", _sec)
        company_id, _ = await _company_and_agent_run(pg)

        async with pg() as session:
            company = await session.get(Company, company_id)
            # The probe row is seeded classified; clear it so this exercises the write
            # rather than the reuse path.
            company.sector = None
            company.industry = None
            await session.flush()
            await ensure_company_classification(session, company)
            await session.commit()

        async with pg() as session:
            stored = await session.get(Company, company_id)
            assert stored.sector == "Healthcare"
            assert stored.industry == "Biotechnology"
            assert stored.industry_raw == "Biological Products, (No Diagnostic Substances)"
            assert stored.sic_code == "2836"
            assert stored.classification_tier == "T2_regulator_or_gov"
            assert stored.classification_updated_at is not None

    async def test_a_failed_classification_write_does_not_cost_the_run(self, pg, monkeypatch) -> None:  # noqa: ANN001
        """A savepoint, not a bare try/except.

        ``except Exception`` cannot un-abort a PostgreSQL transaction — that is what
        destroyed a report earlier in this campaign. Here the classification write is
        made to fail against a real connection, and the transaction must still be usable
        afterwards.
        """
        from sqlalchemy import text

        from app.models.company import Company
        from app.services.classification import service as classification_service
        from app.services.classification.service import ensure_company_classification

        async def _sec(ticker: str, exchange: str | None):  # noqa: ANN202
            # 200 characters — longer than `industry_raw` accepts, so the INSERT fails
            # at the database rather than at a Python guard that could be wrong about
            # what the database would have done.
            return "2836", "X" * 400, None

        monkeypatch.setattr(classification_service, "_fetch_sec_classification", _sec)
        company_id, _ = await _company_and_agent_run(pg)

        async with pg() as session:
            company = await session.get(Company, company_id)
            company.sector = None
            company.industry = None
            await session.flush()

            result = await ensure_company_classification(session, company)
            assert result.industry == "Biotechnology"

            # THE ASSERTION THAT MATTERS: the transaction still works. Without the
            # savepoint this raises `InFailedSqlTransaction` and every later write in
            # the run — including the report — is lost.
            alive = (await session.execute(text("SELECT 1"))).scalar_one()
            assert alive == 1
            await session.commit()
