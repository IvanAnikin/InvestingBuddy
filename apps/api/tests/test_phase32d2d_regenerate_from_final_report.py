"""
Phase 32D2d — regenerating a final report must not silently lose its state.

WHY THIS FILE EXISTS
====================
``generate_from_report`` recovers workflow state by re-parsing the source
report's markdown JSON blocks. For a Phase-9 analysis-council draft those blocks
ARE the state envelope (``company_snapshot`` / ``financial_data_summary`` /
``bull_case_summary`` / …). For an ALREADY-FINAL report they are the RENDERED
SECTIONS (``financial_snapshot`` / ``bull_case`` / …) — different key names — so
the parse recovers NOTHING and every key comes back ``None``.

A final report is exactly what an admin is looking at when they press "Generate
Final Report" on the report detail page, so this was easy to hit. Live staging
report ``835cc67b-4889-4de5-8c2d-7d8ac80c5fc4`` is the result: it rendered

    "Bull case summary not available. Run company analysis workflow."
    "Company snapshot not available. Run company analysis workflow first."
    "Valuation guard summary not available."
    "Financial data summary not available from analysis workflow."
    available_count: 0

directly beside a company-identity section carrying a validated T1 fiscal year
and a financial snapshot carrying a validated T1 revenue figure — and beside a
data-availability summary that correctly said
``fundamentals_source: issuer_primary_document / T1_primary_filing``.

"Run company analysis workflow" is, in that state, a FALSE instruction: the
workflow had run. Its draft was simply unreachable from the report being
regenerated.

These tests run OFFLINE (mock AsyncSession, council patched).
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import Report
from app.services import final_report_generator as frg
from app.services.final_report_generator import (
    FINAL_REPORT_VERSION,
    FinalReportGeneratorService,
)
from app.services.llm.schemas import CouncilResult
from app.services.report_lineage import ResolvedReportLineage

AGENT_RUN_ID = uuid.uuid4()


def _snapshot() -> dict[str, Any]:
    return {
        "is_mock": False,
        "source_tier": "T5_api_aggregator",
        "company_identity": {
            "legal_name": "Lineage Test Issuer A/S",
            "ticker": "LINE",
            "exchange": "CO",
            "country_domicile": "Denmark",
        },
        "profile": {"sector": "Consumer Discretionary", "reporting_currency": "DKK"},
        "price_history_summary": {
            "available": True,
            "latest_close": 100.0,
            "currency": "DKK",
            "data_points_count": 250,
            "source_tier": "T5_api_aggregator",
            "provider_name": "price_only_aggregator",
        },
        "provider_metadata": {
            "provider_name": "price_only_aggregator",
            "source_tier": "T5_api_aggregator",
            "is_mock": False,
        },
        "missing_fields": ["identity.isin", "identity.lei"],
    }


def _workflow_state() -> dict[str, Any]:
    from app.agents.analysis_council.bull_case_agent import (
        bull_case_output_to_dict,
        run_bull_case_agent,
    )
    from app.agents.research_team.financial_data_agent import (
        financial_data_agent_output_to_dict,
        run_financial_data_agent,
    )
    from app.agents.research_team.source_quality_agent import (
        run_source_quality_agent,
        source_quality_output_to_dict,
    )

    snap = _snapshot()
    fds = financial_data_agent_output_to_dict(run_financial_data_agent(snap))
    sqs = source_quality_output_to_dict(run_source_quality_agent(snap))
    return {
        "company_snapshot": snap,
        "financial_data_summary": fds,
        "source_quality_summary": sqs,
        "research_completeness_summary": {"blocking_gaps": [], "next_research_tasks": []},
        "bull_case_summary": bull_case_output_to_dict(
            run_bull_case_agent(snap, fds, sqs, {})
        ),
        "schema_validation_result": {"is_valid": True, "errors": [], "warnings": []},
    }


def _draft_report() -> Report:
    """A Phase-9 analysis-council draft: markdown carrying the STATE envelope."""
    state = _workflow_state()
    return Report(
        id=uuid.uuid4(),
        title="Lineage Test Issuer A/S — Analysis Council Draft",
        slug=f"draft-{uuid.uuid4().hex[:8]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="draft",
        human_review_required=True,
        final_report_version=None,
        created_by_agent_run_id=AGENT_RUN_ID,
        content_markdown="# Draft\n\n```json\n"
        + json.dumps(state, default=str)
        + "\n```\n",
    )


def _final_report(sections: dict[str, Any] | None = None) -> Report:
    """A generated FINAL report: markdown carrying RENDERED SECTIONS."""
    rendered = sections or {
        "financial_snapshot": {"type": "financial_snapshot"},
        "bull_case": {"type": "bull_case", "available": True},
        "company_identity": {"type": "company_identity"},
    }
    return Report(
        id=uuid.uuid4(),
        title="LLM Council Analysis Draft — LINE",
        slug=f"final-{uuid.uuid4().hex[:8]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="draft",
        human_review_required=True,
        final_report_version=FINAL_REPORT_VERSION,
        created_by_agent_run_id=AGENT_RUN_ID,
        content_markdown="# FINAL\n\n```json\n"
        + json.dumps(rendered, default=str)
        + "\n```\n",
    )


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _saved_content(mock_db: AsyncMock) -> dict[str, Any]:
    saved: Report = mock_db.add.call_args_list[-1][0][0]
    blocks = re.findall(r"```json\s*(.*?)\s*```", saved.content_markdown or "", re.S)
    assert blocks
    return json.loads(blocks[-1])


async def _regenerate(
    mock_db: AsyncMock, source: Report, lineage_draft: Report | None
) -> dict[str, Any]:
    with (
        patch.object(frg, "_load_report_by_id", AsyncMock(return_value=source)),
        patch.object(
            frg, "_load_workflow_draft_for_run", AsyncMock(return_value=lineage_draft)
        ),
        patch.object(frg, "_load_scorecard_for_report", AsyncMock(return_value=None)),
        patch.object(frg, "_load_scorecard_by_id", AsyncMock(return_value=None)),
        patch.object(frg, "_load_citations_for_report", AsyncMock(return_value=[])),
        patch.object(frg, "_load_sources_for_citations", AsyncMock(return_value=[])),
        patch.object(
            frg,
            "_resolve_regeneration_lineage",
            AsyncMock(return_value=ResolvedReportLineage()),
        ),
        patch.object(
            frg, "maybe_run_council", AsyncMock(return_value=CouncilResult(llm_used=False))
        ),
        patch.object(frg, "load_reusable_documents", AsyncMock(return_value=None)),
    ):
        await FinalReportGeneratorService().generate_from_report(mock_db, source.id)
    return _saved_content(mock_db)


# ---------------------------------------------------------------------------
# 1. Control — the defect, if the recovery is removed
# ---------------------------------------------------------------------------


def test_a_final_reports_markdown_does_not_parse_as_workflow_state() -> None:
    """The mechanism, stated directly: the key names simply do not overlap."""
    parsed = frg._extract_workflow_state_from_report(_final_report())
    assert parsed["company_snapshot"] is None
    assert parsed["bull_case_summary"] is None
    assert parsed["financial_data_summary"] is None


def test_a_draft_reports_markdown_does_parse_as_workflow_state() -> None:
    parsed = frg._extract_workflow_state_from_report(_draft_report())
    assert parsed["company_snapshot"] is not None
    assert parsed["bull_case_summary"] is not None


# ---------------------------------------------------------------------------
# 2. Recovery by explicit lineage
# ---------------------------------------------------------------------------


async def test_state_is_recovered_from_the_lineage_draft(mock_db: AsyncMock) -> None:
    draft = _draft_report()
    content = await _regenerate(mock_db, _final_report(), draft)

    # The deterministic sections are populated, not "not available".
    assert content["bull_case"]["available"] is True
    assert content["company_identity"]["legal_name"]["value"] == (
        "Lineage Test Issuer A/S"
    )
    assert content["data_availability_summary"]["available_count"] > 0

    notice = content["regeneration_notice"]
    assert notice["workflow_state_recovered"] is True
    assert notice["recovered_from_report_id"] == str(draft.id)


async def test_recovery_uses_the_agent_run_lineage_only(mock_db: AsyncMock) -> None:
    """A final report with no lineage agent_run must not guess at a draft."""
    orphan = _final_report()
    orphan.created_by_agent_run_id = None
    loader = AsyncMock(return_value=_draft_report())
    with (
        patch.object(frg, "_load_report_by_id", AsyncMock(return_value=orphan)),
        patch.object(frg, "_load_workflow_draft_for_run", loader),
        patch.object(frg, "_load_scorecard_for_report", AsyncMock(return_value=None)),
        patch.object(frg, "_load_scorecard_by_id", AsyncMock(return_value=None)),
        patch.object(frg, "_load_citations_for_report", AsyncMock(return_value=[])),
        patch.object(frg, "_load_sources_for_citations", AsyncMock(return_value=[])),
        patch.object(
            frg,
            "_resolve_regeneration_lineage",
            AsyncMock(return_value=ResolvedReportLineage()),
        ),
        patch.object(
            frg, "maybe_run_council", AsyncMock(return_value=CouncilResult(llm_used=False))
        ),
        patch.object(frg, "load_reusable_documents", AsyncMock(return_value=None)),
    ):
        await FinalReportGeneratorService().generate_from_report(mock_db, orphan.id)
    loader.assert_not_awaited()


# ---------------------------------------------------------------------------
# 3. Honest degradation when recovery is impossible
# ---------------------------------------------------------------------------


async def test_unrecoverable_state_is_declared_not_silently_rendered(
    mock_db: AsyncMock,
) -> None:
    content = await _regenerate(mock_db, _final_report(), None)

    notice = content["regeneration_notice"]
    assert notice["workflow_state_recovered"] is False
    assert notice["human_review_required"] is True
    # The reader is told NOT to act on the misleading generic instruction.
    assert "do not" in notice["note"].lower()
    assert "NOT a workflow that was never run" in notice["note"]

    # The generic notes are still there (they are shared builders) — which is
    # exactly why the notice above has to be.
    assert content["bull_case"]["available"] is False


async def test_the_ordinary_path_adds_no_regeneration_notice(
    mock_db: AsyncMock,
) -> None:
    """Regenerating from a Phase-9 draft is the normal case and is unchanged."""
    content = await _regenerate(mock_db, _draft_report(), None)
    assert "regeneration_notice" not in content
    assert content["bull_case"]["available"] is True


# ---------------------------------------------------------------------------
# 4. The loader's own contract
# ---------------------------------------------------------------------------


async def test_loader_filters_to_the_deterministic_draft_of_that_run() -> None:
    """It must ask for final_report_version IS NULL, scoped to the agent run."""
    db = AsyncMock(spec=AsyncSession)
    result = AsyncMock()
    result.scalar_one_or_none = lambda: None
    db.execute = AsyncMock(return_value=result)
    await frg._load_workflow_draft_for_run(db, AGENT_RUN_ID)
    stmt = str(db.execute.call_args[0][0])
    assert "reports.final_report_version IS NULL" in stmt
    assert "reports.created_by_agent_run_id" in stmt
