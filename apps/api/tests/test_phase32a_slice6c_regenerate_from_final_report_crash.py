"""Phase 32A Slice 6C — fix a real crash regenerating a final report draft.

Root cause: ``committee_chair_summary`` has two on-the-wire shapes that share
the same top-level key:

  * the RAW ``company_analysis`` workflow-state shape, where
    ``provisional_internal_status`` is a plain ``str``; and
  * the ALREADY-RENDERED final-report SECTION shape produced by
    ``_build_committee_chair_summary`` (embedded in a saved final report's
    ``content_markdown`` JSON block), where ``provisional_internal_status`` is
    a datapoint dict: ``{"value": <str>, "provenance": ..., "note": ...}``.

``generate_from_report`` re-parses whichever report is passed as the SOURCE
report via ``_extract_workflow_state_from_report`` — including an
ALREADY-FINAL report (e.g. regenerating a second time from the
final-report-generator's own prior output, which is exactly what "Generate
Internal Final Report Draft" does when clicked again on a completed-council
report). Reading ``provisional_internal_status`` off a RENDERED section then
yields a ``dict``, which reached ``status not in ALLOWED_INTERNAL_STATUSES``
(a set-membership check) unguarded in THREE places
(``_build_executive_summary``, ``_build_committee_chair_summary``,
``_generate_and_save``) and crashed with
``TypeError: unhashable type: 'dict'``.

``POST /final-reports/{id}/validate`` never hit this: it only re-parses the
draft for safety + schema validation and never re-derives
``provisional_internal_status`` from ``committee_chair_summary`` at all.

This test drives the exact ``generate_from_report`` regenerate-from-final path
end to end against a REAL in-memory SQLite async DB (the shared conftest's
mock ``AsyncSession`` cannot exercise a real WHERE clause / commit round-trip),
so the bug is caught if it ever regresses.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import settings as app_settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import backtest as _backtest  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import financial_snapshot as _financial_snapshot  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import review_event as _review_event  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import screening as _screening  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.agent_run import AgentRun
from app.models.company import Company
from app.models.report import Report
from app.services import final_report_generator as frg
from app.services.final_report_generator import (
    ALLOWED_INTERNAL_STATUSES,
    FinalReportGeneratorService,
    _build_committee_chair_summary,
    _build_executive_summary,
    _coerce_status_value,
)
from app.services.llm.schemas import CouncilResult
from app.workflows.company_analysis import build_analysis_state_envelope

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Real async SQLite fixtures (mirrors test_phase32a_slice3_*)
# ---------------------------------------------------------------------------
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
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest.fixture(autouse=True)
def _disable_council(monkeypatch):
    """No LLM / Azure / network in this test — council stays disabled."""

    async def _fake(*args, **kwargs):
        return CouncilResult.disabled()

    monkeypatch.setattr(frg, "maybe_run_council", _fake)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(
        app_settings, "report_citation_persistence_enabled", True, raising=False
    )
    return True


# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------
def _brby_state() -> dict[str, Any]:
    return {
        "is_mock": False,
        "company_snapshot": {
            "is_mock": False,
            "source_tier": "T2_regulator_or_gov",
            "retrieved_at": "2026-08-01T00:00:00Z",
            "company_identity": {
                "legal_name": "Burberry Group plc",
                "ticker": "BRBY",
                "exchange": "LSE",
                "country_domicile": "GB",
            },
            "profile": {"sector": "Consumer Cyclical", "reporting_currency": "GBP"},
            "price_history_summary": {"available": False},
        },
        "financial_data_summary": {
            "available_count": 1,
            "missing_count": 2,
            "available_fields": ["market_cap"],
            "missing_fields": ["revenue", "ebitda"],
            "warnings": [],
        },
        "source_quality_summary": {"overall_source_quality": "weak"},
        "research_completeness_summary": {"completeness_score": 0.3},
        "upgraded_citation_validation": {"status": "ok"},
        "bull_case_summary": {"positive_thesis_points": ["Brand strength."]},
        "bear_case_summary": {"negative_thesis_points": ["Weak demand."]},
        "risk_summary": {"business_risks": ["FX exposure."]},
        "valuation_guard_summary": {
            "valuation_ready": False,
            "blockers": ["No verified DCF inputs."],
        },
        # RAW workflow-state shape: provisional_internal_status is a plain str.
        "committee_chair_summary": {
            "provisional_internal_status": "needs_primary_sources",
            "committee_summary": "Insufficient primary evidence located.",
        },
        "fundamentals_data": None,
        "fundamentals_available": False,
        "schema_validation_result": {"is_valid": False, "errors": [], "warnings": []},
        "source_tier": "T2_regulator_or_gov",
        "catalyst_discovery": None,
    }


async def _seed_envelope_draft(session) -> tuple[Company, AgentRun, Report]:
    company = Company(
        id=uuid.uuid4(),
        ticker="BRBY",
        exchange="LSE",
        name="Burberry Group plc",
        country="GB",
        sector="Consumer Cyclical",
        industry="Apparel Retail",
        status="new",
    )
    session.add(company)
    await session.flush()

    run = AgentRun(
        id=uuid.uuid4(),
        workflow_name="company_analysis",
        workflow_version="1.0.0",
        status="completed",
        started_at=_utcnow(),
        trigger_type="manual",
    )
    session.add(run)
    await session.flush()

    envelope = build_analysis_state_envelope(_brby_state())
    markdown = "# Draft\n\n```json\n" + json.dumps(envelope, default=str) + "\n```\n"

    draft = Report(
        id=uuid.uuid4(),
        title="Analysis Council Draft — BRBY",
        slug=f"brby-draft-{uuid.uuid4().hex[:8]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="draft",
        content_markdown=markdown,
        created_by_agent_run_id=run.id,
        company_id=company.id,
        human_review_required=True,
        created_at=_utcnow(),
    )
    session.add(draft)
    await session.flush()
    await session.commit()
    return company, run, draft


# ---------------------------------------------------------------------------
# 1. Unit-level: the exact TypeError this bug produced, isolated
# ---------------------------------------------------------------------------
def test_rendered_committee_chair_summary_status_is_a_dict_not_a_str() -> None:
    """Pin the exact shape mismatch: a rendered section's status is a datapoint
    dict, not the plain string the raw workflow-state shape carries."""
    rendered = _build_committee_chair_summary(
        {"provisional_internal_status": "needs_primary_sources"}
    )
    status_field = rendered["provisional_internal_status"]
    assert isinstance(status_field, dict)
    assert status_field["value"] == "needs_primary_sources"


def test_coerce_status_value_unwraps_both_shapes() -> None:
    # Raw workflow-state shape.
    assert _coerce_status_value("needs_primary_sources") == "needs_primary_sources"
    # Rendered final-report-section (datapoint) shape.
    assert (
        _coerce_status_value({"value": "needs_primary_sources", "provenance": "x"})
        == "needs_primary_sources"
    )
    # Genuinely absent / malformed — never fabricated, never raises.
    assert _coerce_status_value(None) is None
    assert _coerce_status_value({"value": None}) is None
    assert _coerce_status_value({}) is None


def test_build_executive_summary_never_raises_on_dict_shaped_status() -> None:
    """Before the fix this raised ``TypeError: unhashable type: 'dict'`` at the
    ``status not in ALLOWED_INTERNAL_STATUSES`` set-membership check."""
    rendered_committee_chair_summary = _build_committee_chair_summary(
        {"provisional_internal_status": "needs_primary_sources"}
    )
    dict_shaped_status = rendered_committee_chair_summary["provisional_internal_status"]
    assert isinstance(dict_shaped_status, dict)  # sanity: reproduces the trigger shape

    result = _build_executive_summary(
        "Burberry Group plc",
        "BRBY",
        None,
        rendered_committee_chair_summary,
        dict_shaped_status,
    )
    assert result["internal_status"] == "needs_primary_sources"
    assert result["internal_status"] in ALLOWED_INTERNAL_STATUSES


# ---------------------------------------------------------------------------
# 2. End-to-end: generate_from_report on an ALREADY-FINAL report
# ---------------------------------------------------------------------------
async def test_generate_from_report_on_already_final_report_does_not_crash(
    session, flag_on
) -> None:
    """The real regression: clicking 'Generate Internal Final Report Draft' a
    SECOND time, pointed at the final-report-generator's own prior output for
    the same company (an "existing completed-council report"), must not crash.
    """
    _, _, draft = await _seed_envelope_draft(session)

    svc = FinalReportGeneratorService()

    # First generation: source is the RAW envelope draft. Always worked.
    first = await svc.generate_from_report(session, draft.id)
    assert first.internal_status == "needs_primary_sources"

    # Second generation: source is the FIRST FINAL REPORT itself — the exact
    # "existing completed-council report" scenario that crashed pre-fix.
    second = await svc.generate_from_report(session, first.report_id)

    # The full set of invariants the orchestrator asked us to pin.
    assert second.schema_valid is True
    assert second.safety_valid is True
    assert second.human_review_required is True
    assert second.publication_ready is False
    assert second.internal_status == "needs_primary_sources"
    assert second.internal_status in ALLOWED_INTERNAL_STATUSES

    # Lineage / company identity preserved across the regenerate.
    from sqlalchemy import select

    second_row = (
        await session.execute(select(Report).where(Report.id == second.report_id))
    ).scalar_one()
    assert second_row.company_id is not None
    assert second_row.company_id == (
        await session.execute(select(Report).where(Report.id == draft.id))
    ).scalar_one().company_id

    md_blocks = second_row.content_markdown or ""
    import re

    blocks = re.findall(r"```json\s*(.*?)\s*```", md_blocks, re.DOTALL)
    assert blocks, "saved final report has no JSON block"
    content = json.loads(blocks[-1])
    # The regenerated executive_summary carries the correctly-coerced status —
    # this is the exact section that crashed pre-fix.
    assert content["executive_summary"]["internal_status"] == "needs_primary_sources"
    # Citations/lineage: regenerating from a final report is still draft-only.
    assert second_row.status == "draft"
    assert second_row.review_status == "draft"


async def test_regenerate_executive_summary_section_on_already_final_report(
    session, flag_on
) -> None:
    """The same dict-shaped-status landmine is reachable via the single-section
    ``regenerate_report_section("executive_summary")`` path too (it builds the
    same ``_build_executive_summary`` call inline from re-parsed state)."""
    _, _, draft = await _seed_envelope_draft(session)
    svc = FinalReportGeneratorService()
    first = await svc.generate_from_report(session, draft.id)

    result = await svc.regenerate_report_section(
        session, first.report_id, "executive_summary"
    )
    # Before the fix, building the "executive_summary" section from the
    # re-parsed FINAL report's rendered committee_chair_summary crashed with
    # TypeError before this response could ever be constructed.
    assert result.section_name == "executive_summary"
    assert result.regenerated is True
    assert result.safety_valid is True
