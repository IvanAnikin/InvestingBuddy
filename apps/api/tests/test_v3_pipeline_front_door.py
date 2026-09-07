"""V3.10 Slice 10.3 — the V3 pipeline at the real company-research front door.

TWO PROPERTIES THAT DECIDE WHETHER THIS IS SAFE
===============================================
1. **With the flag off, nothing changes.** Byte-for-byte the V2 path.
2. **A V3 failure never costs a report the V2 path would have produced.** The V3
   contribution is additive research state attached to a report that already exists, so
   the worst case is a report without it.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models.company import Company
from app.models.report import Report
from app.services.pipeline.v3_pipeline import (
    SOURCE_SUMMARY_KEY,
    V3ResearchOutcome,
    attach_to_report,
    run_v3_research,
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


async def _company(session, **overrides) -> Company:  # noqa: ANN001
    base = {
        "id": uuid.uuid4(),
        "ticker": "MRNA",
        "exchange": "NASDAQ",
        "name": "Moderna, Inc.",
        "status": "new",
        "sector": "Health Care",
        "industry": "Biotechnology",
    }
    base.update(overrides)
    company = Company(**base)
    session.add(company)
    await session.flush()
    return company


def _cfg(**overrides) -> Settings:
    base: dict[str, Any] = {
        "v3_pipeline_enabled": True,
        "v3_agent_tools_enabled": True,
        # No model configured: the pipeline must still run and degrade honestly.
        "azure_openai_api_key": "",
        "azure_openai_endpoint": "",
        "deepseek_api_key": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestTheFlag:
    def test_the_pipeline_is_off_by_default(self) -> None:
        """With it off the company-research entry point behaves as it does on main."""
        assert Settings().v3_pipeline_enabled is False

    async def test_the_front_door_helper_does_nothing_when_the_flag_is_off(
        self, session
    ) -> None:
        from app.services import company_research_service as svc

        company = await _company(session)
        result = await svc._run_v3_pipeline(
            session, company=company, report_id=uuid.uuid4()
        )
        assert result is None

    async def test_the_helper_does_nothing_when_no_report_was_produced(
        self, session, monkeypatch
    ) -> None:
        """Research state hanging off no report is state nobody can reach."""
        from app.services import company_research_service as svc

        monkeypatch.setattr(svc, "settings", _cfg())
        company = await _company(session)
        assert (
            await svc._run_v3_pipeline(session, company=company, report_id=None) is None
        )


class TestItNeverCostsAReport:
    async def test_the_pipeline_never_raises(self, session) -> None:
        """Every exception is caught, recorded and returned."""
        company = await _company(session)

        class _Exploding:
            def to_dict(self):  # noqa: ANN201
                raise RuntimeError("boom")

            def client_for(self, slot):  # noqa: ANN001, ANN201
                raise RuntimeError("boom")

            def any_resolved(self):  # noqa: ANN201
                raise RuntimeError("boom")

        outcome = await run_v3_research(
            session, company, cfg=_cfg(), routing=_Exploding()
        )
        assert outcome.error is not None
        assert outcome.ran is False
        assert outcome.degraded

    async def test_a_pipeline_failure_leaves_the_report_untouched(
        self, session, monkeypatch
    ) -> None:
        from app.services import company_research_service as svc

        monkeypatch.setattr(svc, "settings", _cfg())
        company = await _company(session)
        report = Report(
            id=uuid.uuid4(),
            title="t",
            slug=f"s-{uuid.uuid4().hex[:8]}",
            report_type="company_deep_dive",
            status="draft",
            source_summary_json={"existing": "value"},
        )
        session.add(report)
        await session.flush()

        async def _boom(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
            raise RuntimeError("pipeline exploded")

        monkeypatch.setattr(
            "app.services.pipeline.v3_pipeline.run_v3_research", _boom
        )
        assert (
            await svc._run_v3_pipeline(session, company=company, report_id=report.id)
            is None
        )
        await session.refresh(report)
        assert report.source_summary_json == {"existing": "value"}


class TestAttachmentIsAdditive:
    def test_an_existing_source_summary_is_preserved(self) -> None:
        report = Report(
            id=uuid.uuid4(),
            title="t",
            slug="s",
            report_type="company_deep_dive",
            status="draft",
            source_summary_json={"documents": [1, 2], "citations": 3},
        )
        attach_to_report(report, V3ResearchOutcome())
        assert report.source_summary_json["documents"] == [1, 2]
        assert report.source_summary_json["citations"] == 3
        assert SOURCE_SUMMARY_KEY in report.source_summary_json

    def test_a_report_with_no_summary_gains_exactly_one_key(self) -> None:
        report = Report(
            id=uuid.uuid4(),
            title="t",
            slug="s",
            report_type="company_deep_dive",
            status="draft",
        )
        attach_to_report(report, V3ResearchOutcome())
        assert list(report.source_summary_json) == [SOURCE_SUMMARY_KEY]

    def test_the_payload_says_what_it_is_and_is_not(self) -> None:
        """A reader of the stored report must be able to tell that the narrative is the
        existing generator's and this is the research state beside it."""
        payload = V3ResearchOutcome().to_dict()
        assert "narrative" in payload["note"]
        assert "existing generator" in payload["note"]


class TestTheRunItself:
    async def test_it_runs_end_to_end_with_no_model_and_degrades_honestly(
        self, session
    ) -> None:
        company = await _company(session)
        outcome = await run_v3_research(session, company, cfg=_cfg())
        await session.commit()
        assert outcome.ran is True
        assert outcome.research_run_id is not None
        # A pipeline that narrowed silently is one nobody can widen.
        assert any("no model provider resolved" in d for d in outcome.degraded)
        assert outcome.chair["deterministic_fallback"] is True

    async def test_the_biotech_playbook_is_selected_for_moderna(self, session) -> None:
        company = await _company(session)
        outcome = await run_v3_research(session, company, cfg=_cfg())
        assert "biotech" in outcome.playbook_versions

    async def test_an_unclassifiable_company_records_why_no_playbook_applied(
        self, session
    ) -> None:
        company = await _company(session, sector="Utilities", industry=None)
        outcome = await run_v3_research(session, company, cfg=_cfg())
        assert any("no playbook applied" in d for d in outcome.degraded)

    async def test_a_ledger_run_is_persisted_and_reachable(self, session) -> None:
        company = await _company(session)
        outcome = await run_v3_research(session, company, cfg=_cfg())
        await session.commit()
        from app.models.ledger import ResearchRun

        run = await session.get(ResearchRun, outcome.research_run_id)
        assert run is not None
        assert run.company_id == company.id
        assert run.playbook_versions_json

    async def test_the_loop_names_the_limit_that_stopped_it(self, session) -> None:
        company = await _company(session)
        outcome = await run_v3_research(session, company, cfg=_cfg(), mode="quick")
        assert outcome.loop["stopped_by"]
        assert "fabricated_citations_discarded" in outcome.loop

    async def test_the_council_refuses_when_there_is_nothing_to_reason_over(
        self, session
    ) -> None:
        """No model means no findings, and a council with nothing evidence-linked to
        reason over would produce prose."""
        company = await _company(session)
        outcome = await run_v3_research(session, company, cfg=_cfg())
        assert outcome.council["convened"] is False
        assert outcome.council["refusal_reason"]

    async def test_a_second_run_produces_a_delta(self, session) -> None:
        """The regression this exists for: the first draft asked `previous_run` again at
        delta time, found THIS run — the most recent — and the guard against comparing a
        run with itself silently produced no delta at all. The feature looked implemented
        and returned nothing."""
        company = await _company(session)
        first = await run_v3_research(session, company, cfg=_cfg())
        await session.commit()
        second = await run_v3_research(session, company, cfg=_cfg())
        await session.commit()
        assert first.research_run_id != second.research_run_id
        assert second.delta is not None, "the delta was silently not computed"
        assert second.delta["from_run_id"] == str(first.research_run_id)
        assert second.delta["to_run_id"] == str(second.research_run_id)

    async def test_the_first_run_has_no_delta_and_that_is_not_a_failure(
        self, session
    ) -> None:
        """A first run has no thesis to be unchanged from."""
        company = await _company(session)
        outcome = await run_v3_research(session, company, cfg=_cfg())
        assert outcome.delta is None

    async def test_prior_gaps_carry_into_the_next_run(self, session) -> None:
        company = await _company(session)
        await run_v3_research(session, company, cfg=_cfg())
        await session.commit()
        from app.services.memory.store import recall

        prior = await recall(session, company_id=company.id)
        assert prior.exists
        # The first run produced gaps (no model wrote anything up), and closable ones
        # become the next run's questions.
        assert prior.open_gaps
