"""
Phase 28A.1 / 28B.3 — LLM report routing + legacy "Phase 9" cleanup.

The single-company "Run Full Analysis" flow used to link a discovery candidate
to a legacy deterministic "Phase 9 Analysis Council Draft" (``llm_used`` from the
workflow's own — disabled — LLM node, so "LLM: not used"), even with the Phase
28A council enabled. These tests verify the fix: the flow now routes through the
Phase 28A final-report generator, the candidate links to that FINAL report, the
generated report never says "Phase 9" / "[LLM: not used]", and legacy drafts stay
readable but are clearly marked ``legacy``.

All tests run OFFLINE with the deterministic FAKE provider — no network, no
credentials.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import Report
from app.schemas.final_report import FinalReportResponse
from app.services import market_discovery_service as mds
from app.services.final_report_generator import (
    FINAL_REPORT_VERSION,
    FinalReportGeneratorService,
)

# asyncio_mode = "auto" (see pyproject.toml) — async tests need no marker.


# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def enable_council(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "llm_council_enabled", True)
    monkeypatch.setattr(config.settings, "llm_provider_council", "fake")
    yield


def _aapl_snapshot() -> dict[str, Any]:
    return {
        "is_mock": False,
        "source_tier": "T2_regulator_or_gov",
        "company_identity": {
            "ticker": "AAPL",
            "legal_name": "Apple Inc.",
            "exchange": "NASDAQ",
            "country_domicile": "US",
        },
        "profile": {"sector": "Technology", "industry": "Consumer Electronics"},
        "fundamentals_summary": {
            "revenue_usd_m": 383285.0,
            "net_income_usd_m": 96995.0,
            "form_type": "10-K",
            "fiscal_year": 2023,
            "source_tier": "T2_regulator_or_gov",
            "data_quality": "A_verified",
        },
    }


def _final_state(**over: Any) -> dict[str, Any]:
    st: dict[str, Any] = {
        "status": "completed",
        "draft_report_id": str(uuid.uuid4()),
        "agent_run_id": str(uuid.uuid4()),
        "company_snapshot": _aapl_snapshot(),
        "catalyst_discovery": None,
    }
    st.update(over)
    return st


def _candidate(**over: Any) -> MagicMock:
    c = MagicMock()
    c.id = over.get("id", uuid.uuid4())
    c.ticker = over.get("ticker", "AAPL")
    c.exchange = over.get("exchange", "NASDAQ")
    c.company_name = over.get("company_name", "Apple Inc.")
    c.raw_signal_json = over.get("raw_signal_json", {"provider_name": "free_real"})
    c.analysis_report_id = None
    c.agent_run_id = None
    return c


def _company() -> MagicMock:
    return MagicMock(
        id=uuid.uuid4(),
        name="Apple Inc.",
        ticker="AAPL",
        exchange="NASDAQ",
        country="US",
        sector="Technology",
        industry="Consumer Electronics",
    )


def _captured_report(mock_db: AsyncMock) -> Report:
    assert mock_db.add.called, "expected a report to be saved"
    return mock_db.add.call_args[0][0]


async def _run(mock_db: AsyncMock, *, candidate: MagicMock, company: MagicMock, state: dict):
    async def fake_runner(db_, **kwargs):
        return state

    # Force clean empty citation/source inputs so the real generator runs
    # against the in-memory snapshot only (no mock-session query artefacts).
    with patch.object(
        mds, "_load_final_report_inputs", AsyncMock(return_value=(None, [], []))
    ), patch.object(
        mds, "get_candidate", AsyncMock(return_value=candidate)
    ), patch.object(
        mds, "ensure_company", AsyncMock(return_value=company)
    ):
        return await mds.run_candidate_analysis(
            mock_db, candidate.id, run_analysis=fake_runner
        )


# ---------------------------------------------------------------------------
# Routing — LLM council enabled
# ---------------------------------------------------------------------------


async def test_run_analysis_routes_to_llm_council(mock_db, enable_council) -> None:
    candidate = _candidate()
    company = _company()
    state = _final_state()
    result = await _run(mock_db, candidate=candidate, company=company, state=state)

    summary = result["report"]
    assert summary is not None
    assert summary.report_kind == "final"
    assert summary.llm_used is True
    assert summary.schema_valid is True
    assert summary.safety_valid is True
    assert summary.final_report_version == FINAL_REPORT_VERSION

    # Candidate links to the FINAL report, not the legacy deterministic draft.
    assert result["analysis_report_id"] != uuid.UUID(state["draft_report_id"])
    assert candidate.analysis_report_id == result["analysis_report_id"]
    assert str(result["legacy_draft_report_id"]) == state["draft_report_id"]
    assert result["warnings"] == []


async def test_generated_final_report_never_says_phase9(mock_db, enable_council) -> None:
    candidate = _candidate()
    result = await _run(
        mock_db, candidate=candidate, company=_company(), state=_final_state()
    )
    report = _captured_report(mock_db)

    assert "Phase 9" not in (report.title or "")
    assert "Phase 9" not in (report.content_markdown or "")
    assert "Analysis Council Draft" != (report.title or "")
    # The literal legacy "not used" tag must never appear when the council ran.
    assert "[LLM: not used]" not in (report.content_markdown or "")
    # Council-ran title is explicit about the LLM involvement.
    assert report.title.startswith("LLM Council Analysis Draft")
    assert report.final_report_version == FINAL_REPORT_VERSION
    assert result["report"].llm_used is True


# ---------------------------------------------------------------------------
# Routing — LLM council disabled (honest deterministic path, still not Phase 9)
# ---------------------------------------------------------------------------


async def test_run_analysis_disabled_says_llm_not_used(mock_db) -> None:
    candidate = _candidate()
    result = await _run(
        mock_db, candidate=candidate, company=_company(), state=_final_state()
    )
    summary = result["report"]
    assert summary.report_kind == "final"
    assert summary.llm_used is False

    report = _captured_report(mock_db)
    # Even with the council off, the output is a final report — never Phase 9.
    assert "Phase 9" not in (report.content_markdown or "")
    assert report.title.startswith("Internal Analysis Draft")
    assert report.final_report_version == FINAL_REPORT_VERSION


async def test_final_report_schema_and_safety_valid(mock_db, enable_council) -> None:
    result = await _run(
        mock_db, candidate=_candidate(), company=_company(), state=_final_state()
    )
    assert result["report"].schema_valid is True
    assert result["report"].safety_valid is True


# ---------------------------------------------------------------------------
# Fallback — final-report generation failure never fails the whole run
# ---------------------------------------------------------------------------


async def test_generation_failure_falls_back_to_legacy(mock_db) -> None:
    candidate = _candidate()
    legacy_id = str(uuid.uuid4())
    state = _final_state(draft_report_id=legacy_id)

    async def fake_runner(db_, **kwargs):
        return state

    async def boom(db_, **kwargs):
        raise RuntimeError("generation exploded")

    with patch.object(
        mds, "_load_final_report_inputs", AsyncMock(return_value=(None, [], []))
    ), patch.object(mds, "get_candidate", AsyncMock(return_value=candidate)), patch.object(
        mds, "ensure_company", AsyncMock(return_value=_company())
    ):
        result = await mds.run_candidate_analysis(
            mock_db,
            candidate.id,
            run_analysis=fake_runner,
            generate_final_report=boom,
        )

    assert "final_report_generation_failed" in result["warnings"]
    assert result["report"].report_kind == "legacy"
    assert str(result["analysis_report_id"]) == legacy_id
    assert candidate.analysis_report_id == uuid.UUID(legacy_id)


# ---------------------------------------------------------------------------
# ReportLinkSummary builder — legacy vs final classification
# ---------------------------------------------------------------------------


def _report_row(**over: Any) -> Report:
    base: dict[str, Any] = dict(
        id=uuid.uuid4(),
        title="Report",
        slug="slug",
        report_type="company_deep_dive",
        status="draft",
        content_markdown="# body",
        final_report_version=None,
        source_summary_json=None,
        schema_validation_json=None,
        safety_validation_json=None,
        created_at=datetime.now(timezone.utc),
    )
    base.update(over)
    return Report(**base)


def test_legacy_report_is_marked_legacy() -> None:
    legacy = _report_row(
        title="Apple Inc. — Analysis Council Draft [LIVE DATA]",
        content_markdown="# Apple — Phase 9 Analysis Council Draft\n**LLM:** [LLM: not used]",
        final_report_version=None,
    )
    summary = mds.report_link_summary_from_report(legacy)
    assert summary is not None
    assert summary.report_kind == "legacy"
    assert summary.llm_used is False
    # Legacy content is left untouched — it stays readable.
    assert "Phase 9" in (legacy.content_markdown or "")


def test_final_report_is_marked_final_with_council_metadata() -> None:
    final = _report_row(
        title="LLM Council Analysis Draft — AAPL — Apple Inc.",
        final_report_version=FINAL_REPORT_VERSION,
        source_summary_json={
            "llm_council": {
                "llm_used": True,
                "provider": "fake",
                "model": "fake-council",
                "council_version": "v1",
                "agents_completed": 8,
                "agents_failed": 0,
                "evidence_item_count": 5,
            }
        },
        schema_validation_json={"is_valid": True},
        safety_validation_json={"passed": True},
    )
    summary = mds.report_link_summary_from_report(final)
    assert summary is not None
    assert summary.report_kind == "final"
    assert summary.llm_used is True
    assert summary.llm_provider == "fake"
    assert summary.council_version == "v1"
    assert summary.agents_completed == 8
    assert summary.evidence_item_count == 5
    assert summary.schema_valid is True
    assert summary.safety_valid is True


def test_report_link_summary_none_for_missing_report() -> None:
    assert mds.report_link_summary_from_report(None) is None


# ---------------------------------------------------------------------------
# generate_from_workflow_state — direct entry point
# ---------------------------------------------------------------------------


async def test_generate_from_workflow_state_llm_used(mock_db, enable_council) -> None:
    resp = await FinalReportGeneratorService().generate_from_workflow_state(
        mock_db,
        state={"company_snapshot": _aapl_snapshot(), "catalyst_discovery": None},
        company_record={"name": "Apple Inc.", "ticker": "AAPL", "exchange": "NASDAQ"},
    )
    assert isinstance(resp, FinalReportResponse)
    assert resp.llm_used is True
    assert resp.human_review_required is True
    assert resp.publication_ready is False


# ---------------------------------------------------------------------------
# Safety — no publish route, no recommendation fields
# ---------------------------------------------------------------------------


def test_no_publish_route_added() -> None:
    from app.main import app

    for route in app.routes:
        path = getattr(route, "path", "") or ""
        assert "publish" not in path.lower(), path


def test_run_analysis_response_has_no_recommendation_fields() -> None:
    from app.schemas.market_discovery import (
        ReportLinkSummary,
        RunCandidateAnalysisResponse,
    )

    fields = " ".join(RunCandidateAnalysisResponse.model_fields).lower()
    fields += " " + " ".join(ReportLinkSummary.model_fields).lower()
    for term in ("recommendation", "rating", "target", "fair_value", "upside", "downside"):
        assert term not in fields
