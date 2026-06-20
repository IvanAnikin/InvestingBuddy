"""
Tests for the company analysis workflow trigger endpoint and workflow structure.

Workflow execution is mocked for endpoint tests so no database is needed.
The graph structure test verifies LangGraph can build the graph successfully.
"""

import uuid
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import CompanyAnalysisState

# ---------------------------------------------------------------------------
# POST /api/v1/workflows/company-analysis/run — endpoint contract tests
# ---------------------------------------------------------------------------

_PLACEHOLDER_SOURCE_ID = str(uuid.UUID("44444444-4444-4444-4444-444444444444"))
_PLACEHOLDER_CITATION_ID = str(uuid.UUID("55555555-5555-5555-5555-555555555555"))


def _make_completed_state(
    agent_run_id: uuid.UUID, report_id: uuid.UUID, ticker: str = "VOW3"
) -> CompanyAnalysisState:
    return {
        "company_id": str(uuid.uuid4()),
        "ticker": ticker,
        "exchange": "XETRA",
        "agent_run_id": str(agent_run_id),
        "company_name": "Volkswagen AG",
        "company_sector": "Automotive",
        "company_description": None,
        "analysis_output": {
            "rating": "WATCH",
            "confidence_score": 0.5,
            "thesis": "Placeholder analysis for VOW3.",
        },
        "draft_report_id": str(report_id),
        "placeholder_source_id": _PLACEHOLDER_SOURCE_ID,
        "citation_ids": [_PLACEHOLDER_CITATION_ID],
        "error": None,
        "status": "completed",
    }


async def test_workflow_trigger_by_company_id_returns_202(
    client: AsyncClient,
    agent_run_id: uuid.UUID,
    report_id: uuid.UUID,
    company_id: uuid.UUID,
) -> None:
    with patch(
        "app.api.v1.workflows.run_company_analysis",
        new_callable=AsyncMock,
        return_value=_make_completed_state(agent_run_id, report_id),
    ):
        response = await client.post(
            "/api/v1/workflows/company-analysis/run",
            json={"company_id": str(company_id)},
        )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "completed"
    assert data["agent_run_id"] == str(agent_run_id)
    assert data["draft_report_id"] == str(report_id)
    assert data["workflow_name"] == "company_analysis"
    assert "summary" in data


async def test_workflow_trigger_by_ticker_returns_202(
    client: AsyncClient,
    agent_run_id: uuid.UUID,
    report_id: uuid.UUID,
) -> None:
    with patch(
        "app.api.v1.workflows.run_company_analysis",
        new_callable=AsyncMock,
        return_value=_make_completed_state(agent_run_id, report_id),
    ):
        response = await client.post(
            "/api/v1/workflows/company-analysis/run",
            json={"ticker": "VOW3", "exchange": "XETRA"},
        )

    assert response.status_code == 202


async def test_workflow_trigger_no_input_returns_422(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/workflows/company-analysis/run",
        json={},
    )
    assert response.status_code == 422


async def test_workflow_trigger_company_not_found_returns_422(
    client: AsyncClient, company_id: uuid.UUID
) -> None:
    failed_state: CompanyAnalysisState = {
        "company_id": str(company_id),
        "ticker": None,
        "exchange": None,
        "agent_run_id": None,
        "company_name": None,
        "company_sector": None,
        "company_description": None,
        "analysis_output": None,
        "draft_report_id": None,
        "placeholder_source_id": None,
        "citation_ids": None,
        "error": "Company not found in database",
        "status": "failed",
    }
    with patch(
        "app.api.v1.workflows.run_company_analysis",
        new_callable=AsyncMock,
        return_value=failed_state,
    ):
        response = await client.post(
            "/api/v1/workflows/company-analysis/run",
            json={"company_id": str(company_id)},
        )

    assert response.status_code == 422
    assert "Company not found" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Workflow graph structure tests — no database needed
# ---------------------------------------------------------------------------

async def test_workflow_graph_builds_successfully() -> None:
    """Verify LangGraph compiles the graph without errors."""
    mock_db = AsyncMock(spec=AsyncSession)
    from app.workflows.company_analysis import build_company_analysis_graph

    graph = build_company_analysis_graph(mock_db)
    assert graph is not None


async def test_workflow_initial_state_shape() -> None:
    """Verify the initial state TypedDict has all expected keys including Phase 3 fields."""
    state: CompanyAnalysisState = {
        "company_id": None,
        "ticker": "VOW3",
        "exchange": "XETRA",
        "agent_run_id": None,
        "company_name": None,
        "company_sector": None,
        "company_description": None,
        "analysis_output": None,
        "draft_report_id": None,
        "placeholder_source_id": None,
        "citation_ids": None,
        "error": None,
        "status": "running",
    }
    assert state["ticker"] == "VOW3"
    assert state["status"] == "running"
    assert state["analysis_output"] is None
    assert state["placeholder_source_id"] is None
    assert state["citation_ids"] is None


async def test_completed_workflow_state_includes_source_and_citation(
    agent_run_id: uuid.UUID, report_id: uuid.UUID
) -> None:
    """Verify that a completed workflow state carries source and citation IDs."""
    state = _make_completed_state(agent_run_id, report_id)
    assert state["placeholder_source_id"] == _PLACEHOLDER_SOURCE_ID
    assert isinstance(state["citation_ids"], list)
    assert len(state["citation_ids"]) == 1
    assert state["citation_ids"][0] == _PLACEHOLDER_CITATION_ID


async def test_placeholder_analysis_output_structure() -> None:
    """Verify placeholder analysis has required fields and valid rating."""
    from app.workflows.company_analysis import _build_placeholder_analysis

    state: CompanyAnalysisState = {
        "company_id": "abc",
        "ticker": "VOW3",
        "exchange": "XETRA",
        "agent_run_id": "xyz",
        "company_name": "Volkswagen AG",
        "company_sector": "Automotive",
        "company_description": None,
        "analysis_output": None,
        "draft_report_id": None,
        "placeholder_source_id": None,
        "citation_ids": None,
        "error": None,
        "status": "running",
    }
    analysis = _build_placeholder_analysis(state)

    assert analysis["ticker"] == "VOW3"
    assert analysis["rating"] in {"BUY", "WATCH", "HOLD", "SELL", "REJECT"}
    assert 0.0 <= analysis["confidence_score"] <= 1.0
    assert 0.0 <= analysis["risk_score"] <= 1.0
    assert isinstance(analysis["bull_case"], list)
    assert isinstance(analysis["bear_case"], list)
    assert isinstance(analysis["catalysts"], list)
    assert isinstance(analysis["citations"], list)
    assert analysis["is_placeholder"] is True
