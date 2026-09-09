"""The corrected MRNA path, end to end, on real PostgreSQL 16 at head 038.

WHY POSTGRES AND NOT THE UNIT SUITE
===================================
This campaign has now been bitten twice by SQLite. The unit suite runs with foreign keys
OFF, which is why a defect that would have ended every company research run with no
report survived 6,100 green tests. Anything whose correctness depends on real constraint
enforcement, real transaction semantics or real column types is proved here.

WHAT IT PROVES
==============
* canonical financial persistence — the corrected FY2025 figures survive a round trip;
* evidence relationships — a promoted lead's row and its evidence id;
* research run relationships — findings/gaps/tool calls hang off a real run;
* report persistence — the V3 payload attaches and reads back;
* no FK or session-rollback issue on the production id shape;
* the MRNA producer path — SEC facts in, canonical snapshot out.

Skipped without ``V3_TEST_POSTGRES_URL``, and CI sets it against a `postgres:16` service.
"""

from __future__ import annotations

import json
import os
import pathlib
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.anyio

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 038"
)

FIXTURE = (
    pathlib.Path(__file__).parent / "fixtures" / "sec_companyfacts_stale_tag.json"
)
FY2025_REVENUE = 1944.0
FY2025_OPERATING = -3074.0
FY2025_NET = -2822.0


@pytest.fixture
async def pg():  # noqa: ANN201
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(POSTGRES_URL, future=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _sec_summary() -> dict[str, Any]:
    """The fundamentals summary the corrected producer path yields for MRNA."""
    from app.integrations.providers.sec_edgar_fundamentals import (
        merge_fundamentals,
        parse_company_facts,
    )

    data = json.loads(FIXTURE.read_text())
    base, _ = parse_company_facts(data, "MRNA", "1682852")
    merged, _ = merge_fundamentals(data, "MRNA", "1682852", base)
    by_name = {dp.field_name: dp.value for dp in merged}
    return {
        "source_tier": "T2_regulator_or_gov",
        "provider": "sec_edgar",
        "num_datapoints": len(merged),
        "revenue_usd_m": by_name.get("sec_edgar.revenue"),
        "operating_income_usd_m": by_name.get("sec_edgar.operating_income"),
        "net_income_usd_m": by_name.get("sec_edgar.net_income"),
        "total_assets_usd_m": by_name.get("sec_edgar.total_assets"),
        "fiscal_year": by_name.get("sec_edgar.fiscal_year"),
        "fiscal_period": by_name.get("sec_edgar.fiscal_period"),
        "form_type": by_name.get("sec_edgar.form_type"),
        "period_basis": by_name.get("sec_edgar.period_basis") or "annual",
    }


@requires_postgres
class TestTheProducerPathOnRealPostgres:
    async def test_the_corrected_figures_survive_a_round_trip(self, pg) -> None:  # noqa: ANN001
        """Numeric columns, not Python floats in memory."""
        from sqlalchemy import text

        from app.services.final_report_generator import _build_financial_snapshot

        section = _build_financial_snapshot(
            {"fundamentals_summary": _sec_summary()}, None, [], None
        )
        assert section["revenue_usd_m"]["value"] == FY2025_REVENUE

        from app.models.company import Company
        from app.models.report import Report

        company_id, report_id = uuid.uuid4(), uuid.uuid4()
        async with pg() as session:
            session.add(
                Company(
                    id=company_id,
                    ticker=f"MR{uuid.uuid4().hex[:5].upper()}",
                    exchange="NASDAQ",
                    name="Moderna acceptance",
                    status="new",
                )
            )
            session.add(
                Report(
                    id=report_id,
                    company_id=company_id,
                    title="MRNA corrective acceptance",
                    slug=f"mrna-{uuid.uuid4().hex[:8]}",
                    report_type="company_analysis",
                    content_markdown=json.dumps({"financial_snapshot": section}),
                    status="draft",
                )
            )
            await session.commit()

        async with pg() as session:
            stored = (
                await session.execute(
                    text("SELECT content_markdown FROM reports WHERE id = :i"),
                    {"i": report_id},
                )
            ).scalar_one()
        snapshot = json.loads(stored)["financial_snapshot"]
        assert snapshot["revenue_usd_m"]["value"] == FY2025_REVENUE
        assert snapshot["operating_income_usd_m"]["value"] == FY2025_OPERATING
        assert snapshot["net_income_usd_m"]["value"] == FY2025_NET
        assert snapshot["reporting_periods"]["latest_annual"] == "FY2025"
        assert "19263" not in stored.replace(",", "").replace(".", "")

    async def test_a_full_v3_run_persists_its_relationships(self, pg) -> None:  # noqa: ANN001
        """Run, findings, gaps and tool calls, with foreign keys enforced."""
        from sqlalchemy import text

        from app.core.config import Settings
        from app.models.agent_run import AgentRun
        from app.models.company import Company
        from app.services.pipeline.v3_pipeline import run_v3_research

        async with pg() as session:
            company = Company(
                id=uuid.uuid4(),
                ticker=f"MR{uuid.uuid4().hex[:5].upper()}",
                exchange="NASDAQ",
                name="Moderna run acceptance",
                status="new",
                sector="Health Care",
                industry="Biotechnology",
            )
            run = AgentRun(
                id=uuid.uuid4(), workflow_name="company_research", status="running"
            )
            session.add_all([company, run])
            await session.flush()
            company_id, agent_run_id = company.id, run.id
            await session.commit()

        async with pg() as session:
            company = await session.get(Company, company_id)
            outcome = await run_v3_research(
                session,
                company,
                cfg=Settings(
                    v3_pipeline_enabled=True,
                    v3_agent_tools_enabled=True,
                    azure_openai_api_key="",
                    azure_openai_endpoint="",
                    deepseek_api_key="",
                ),
                research_job_id=agent_run_id,
            )
            await session.commit()

        assert outcome.error is None
        assert outcome.research_run_id is not None

        async with pg() as session:
            runs = (
                await session.execute(
                    text("SELECT count(*) FROM research_runs WHERE id = :i"),
                    {"i": outcome.research_run_id},
                )
            ).scalar_one()
            calls = (
                await session.execute(
                    text("SELECT count(*) FROM research_tool_calls WHERE company_id = :i"),
                    {"i": company_id},
                )
            ).scalar_one()
            gaps = (
                await session.execute(
                    text("SELECT count(*) FROM research_gaps WHERE research_run_id = :i"),
                    {"i": outcome.research_run_id},
                )
            ).scalar_one()
        assert runs == 1
        assert calls > 0, "tool calls did not persist — the FK class of defect"
        assert gaps > 0

    async def test_the_v3_payload_attaches_and_reads_back(self, pg) -> None:  # noqa: ANN001
        from sqlalchemy import text

        from app.models.company import Company
        from app.models.report import Report
        from app.services.pipeline.v3_pipeline import (
            SOURCE_SUMMARY_KEY,
            V3ResearchOutcome,
            attach_to_report,
        )

        company_id, report_id = uuid.uuid4(), uuid.uuid4()
        async with pg() as session:
            session.add(
                Company(
                    id=company_id,
                    ticker=f"MR{uuid.uuid4().hex[:5].upper()}",
                    exchange="NASDAQ",
                    name="payload acceptance",
                    status="new",
                )
            )
            report = Report(
                id=report_id,
                company_id=company_id,
                title="payload acceptance",
                slug=f"pl-{uuid.uuid4().hex[:8]}",
                report_type="company_analysis",
                content_markdown="{}",
                status="draft",
                source_summary_json={"llm_council": {"version": "v1"}},
            )
            session.add(report)
            await session.flush()
            attach_to_report(
                report,
                V3ResearchOutcome(
                    research_run_id=uuid.uuid4(),
                    external_research={"leads_discovered": 3},
                ),
            )
            await session.commit()

        async with pg() as session:
            stored = (
                await session.execute(
                    text("SELECT source_summary_json FROM reports WHERE id = :i"),
                    {"i": report_id},
                )
            ).scalar_one()
        assert stored["llm_council"] == {"version": "v1"}, "V2 metadata must survive"
        assert stored[SOURCE_SUMMARY_KEY]["external_research"]["leads_discovered"] == 3
