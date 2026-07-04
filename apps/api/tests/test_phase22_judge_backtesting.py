"""
Phase 22: Tests for the Judge + Backtesting Framework.

All tests are fully offline:
  - No network calls
  - No EODHD or Azure OpenAI keys required
  - No live database (mock AsyncSession)
  - MockHistoricalOutcomeProvider used throughout

Tests cover:
  - MockHistoricalOutcomeProvider returns deterministic prices
  - Outcome metrics calculation
  - Benchmark comparison calculation
  - Missing data handling
  - ResearchJudgeService evaluation
  - Judge score normalisation
  - Forbidden output term detection
  - Allowed judge statuses only
  - BacktestingService create/list/get
  - API endpoints (create run, list runs, get run, judge report, results, summary)
  - No public recommendation outputs in any response
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.integrations.historical_outcome_provider import (
    MockHistoricalOutcomeProvider,
    _mock_price,
    get_historical_outcome_provider,
)
from app.main import app
from app.models.backtest import BacktestResult, BacktestRun
from app.models.report import Report
from app.schemas.backtesting import (
    ALLOWED_JUDGE_STATUSES,
    FORBIDDEN_OUTPUT_TERMS,
    BacktestRunCreate,
    HistoricalOutcome,
    JudgeEvaluation,
)
from app.services.backtesting_service import BacktestingService
from app.services.research_judge_service import ResearchJudgeService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)
_RUN_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_RESULT_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_REPORT_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
_COMPANY_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _make_mock_run(
    run_id: uuid.UUID = _RUN_ID,
    name: str = "Test Run",
    status: str = "pending",
    horizon_days: int = 90,
) -> MagicMock:
    run = MagicMock(spec=BacktestRun)
    run.id = run_id
    run.name = name
    run.description = None
    run.status = status
    run.horizon_days = horizon_days
    run.benchmark_symbol = None
    run.provider_name = "mock"
    run.parameters_json = {}
    run.summary_json = None
    run.created_at = _NOW
    run.started_at = None
    run.completed_at = None
    run.error_message = None
    return run


def _make_mock_result(
    result_id: uuid.UUID = _RESULT_ID,
    run_id: uuid.UUID = _RUN_ID,
    report_id: uuid.UUID = _REPORT_ID,
    status: str = "pending",
) -> MagicMock:
    result = MagicMock(spec=BacktestResult)
    result.id = result_id
    result.backtest_run_id = run_id
    result.report_id = report_id
    result.company_id = None
    result.scorecard_id = None
    result.ticker = "VOW3"
    result.exchange = "XETRA"
    result.evaluation_start_date = date(2025, 1, 1)
    result.evaluation_end_date = date(2025, 4, 1)
    result.horizon_days = 90
    result.benchmark_symbol = None
    result.outcome_json = None
    result.judge_evaluation_json = None
    result.warnings_json = None
    result.missing_data_json = None
    result.status = status
    result.created_at = _NOW
    return result


def _make_mock_report(report_id: uuid.UUID = _REPORT_ID) -> MagicMock:
    report = MagicMock(spec=Report)
    report.id = report_id
    report.title = "Volkswagen AG — Draft Analysis"
    report.slug = "company-analysis-vow3"
    report.report_type = "company_deep_dive"
    report.status = "draft"
    report.summary = "Internal research note. Risk: high debt. Bull case: EV pivot."
    report.content_markdown = (
        "# VOW3 Analysis\n\n"
        "## Bull Case\nEV transition could drive upside.\n\n"
        "## Risk\nHigh debt load and regulatory headwinds.\n"
    )
    report.content_html = None
    report.created_by_agent_run_id = None
    report.published_at = None
    report.created_at = _NOW
    report.updated_at = _NOW
    report.review_status = "draft"
    report.reviewed_at = None
    report.reviewer_note = None
    report.review_decision_reason = None
    report.human_review_required = True
    report.approved_by = None
    report.rejected_by = None
    report.final_report_version = "16.0.0"
    report.safety_validation_json = {"passed": True}
    report.schema_validation_json = {"is_valid": True}
    report.source_summary_json = {"source_count": 6, "citation_count": 4}
    report.scorecard_id = None
    return report


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
async def client(mock_db: AsyncMock) -> AsyncClient:
    app.dependency_overrides[get_db] = lambda: mock_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def mock_run() -> MagicMock:
    return _make_mock_run()


@pytest.fixture
def mock_result() -> MagicMock:
    return _make_mock_result()


@pytest.fixture
def mock_report() -> MagicMock:
    return _make_mock_report()


# ---------------------------------------------------------------------------
# MockHistoricalOutcomeProvider tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_provider_returns_deterministic_prices() -> None:
    """Mock provider must return the same prices for the same inputs."""
    provider = MockHistoricalOutcomeProvider()
    start = date(2024, 1, 1)
    end = date(2024, 4, 1)

    outcome1 = await provider.get_outcome("VOW3", "XETRA", start, end)
    outcome2 = await provider.get_outcome("VOW3", "XETRA", start, end)

    assert outcome1.start_price == outcome2.start_price
    assert outcome1.end_price == outcome2.end_price
    assert outcome1.data_available is True
    assert outcome1.provider_name == "mock"


@pytest.mark.asyncio
async def test_mock_provider_computes_absolute_return() -> None:
    provider = MockHistoricalOutcomeProvider()
    start = date(2024, 1, 1)
    end = date(2024, 7, 1)

    outcome = await provider.get_outcome("VOW3", "XETRA", start, end)

    assert outcome.absolute_return is not None
    assert isinstance(outcome.absolute_return, float)


@pytest.mark.asyncio
async def test_mock_provider_benchmark_comparison() -> None:
    """Mock provider computes relative return when benchmark is provided."""
    provider = MockHistoricalOutcomeProvider()
    start = date(2024, 1, 1)
    end = date(2024, 4, 1)

    outcome = await provider.get_outcome("VOW3", "XETRA", start, end, benchmark_symbol="SPY")

    assert outcome.benchmark_return is not None
    assert outcome.relative_return is not None
    assert outcome.relative_return == pytest.approx(
        outcome.absolute_return - outcome.benchmark_return, abs=1e-6
    )


@pytest.mark.asyncio
async def test_mock_provider_missing_data_warning() -> None:
    """Mock provider always includes a mock-data warning."""
    provider = MockHistoricalOutcomeProvider()
    outcome = await provider.get_outcome("AAPL", "NASDAQ", date(2024, 1, 1), date(2024, 4, 1))
    assert any("MOCK" in w.upper() for w in outcome.warnings)


@pytest.mark.asyncio
async def test_mock_provider_unknown_ticker() -> None:
    """Unknown ticker falls back to default price — still returns data."""
    provider = MockHistoricalOutcomeProvider()
    outcome = await provider.get_outcome("ZZZZ_UNKNOWN", None, date(2024, 1, 1), date(2024, 4, 1))
    assert outcome.data_available is True
    assert outcome.start_price is not None


def test_get_historical_outcome_provider_mock() -> None:
    provider = get_historical_outcome_provider("mock")
    assert isinstance(provider, MockHistoricalOutcomeProvider)


def test_get_historical_outcome_provider_unknown_falls_back_to_mock() -> None:
    """Unknown provider name falls back to mock — CI safety."""
    provider = get_historical_outcome_provider("nonexistent_provider")
    assert isinstance(provider, MockHistoricalOutcomeProvider)


def test_mock_price_deterministic() -> None:
    """_mock_price must be deterministic."""
    assert _mock_price("VOW3", 0) == _mock_price("VOW3", 0)
    assert _mock_price("VOW3", 90) == _mock_price("VOW3", 90)
    assert _mock_price("VOW3", 90) != _mock_price("VOW3", 0)


# ---------------------------------------------------------------------------
# ResearchJudgeService tests
# ---------------------------------------------------------------------------


def test_judge_evaluates_good_report() -> None:
    """Well-sourced report with risk/bull sections scores above 0.5."""
    judge = ResearchJudgeService()
    report_data = {
        "title": "VOW3 Analysis",
        "summary": "Internal note.",
        "content_markdown": (
            "## Bull Case\nStrong EV pipeline.\n"
            "## Risk\nDebt headwinds.\n"
        ),
        "source_summary_json": {"source_count": 6, "citation_count": 5},
        "safety_validation_json": {"passed": True},
        "schema_validation_json": {"is_valid": True},
    }
    eval_ = judge.evaluate_report(
        report_id=_REPORT_ID,
        report_data=report_data,
    )
    assert eval_.judge_score > 0.5
    assert eval_.judge_status in ALLOWED_JUDGE_STATUSES
    assert eval_.safety_passed is True


def test_judge_evaluates_poor_report() -> None:
    """Report with no sources and no content scores below 0.3."""
    judge = ResearchJudgeService()
    report_data: dict[str, Any] = {
        "title": "",
        "summary": "",
        "content_markdown": "",
        "source_summary_json": {},
        "safety_validation_json": None,
        "schema_validation_json": None,
    }
    eval_ = judge.evaluate_report(report_id=None, report_data=report_data)
    assert eval_.judge_score < 0.3
    assert eval_.judge_status in ALLOWED_JUDGE_STATUSES


def test_judge_status_always_allowed() -> None:
    """All judge statuses must be in ALLOWED_JUDGE_STATUSES."""
    judge = ResearchJudgeService()
    for scenario in [
        {"source_summary_json": {}, "content_markdown": ""},
        {"source_summary_json": {"source_count": 6, "citation_count": 4},
         "content_markdown": "## Bull Case\nGrowth. ## Risk\nDebt."},
        {"schema_validation_json": {"is_valid": False, "errors": ["e1", "e2", "e3"]},
         "content_markdown": "## Risk\nHeadwinds."},
    ]:
        eval_ = judge.evaluate_report(report_id=None, report_data=scenario)
        assert eval_.judge_status in ALLOWED_JUDGE_STATUSES, (
            f"Unexpected status: {eval_.judge_status}"
        )


def test_judge_score_normalization() -> None:
    judge = ResearchJudgeService()
    assert judge.normalize_score(1.5) == 1.0
    assert judge.normalize_score(-0.1) == 0.0
    assert judge.normalize_score(0.75) == 0.75


def test_judge_forbidden_terms_absent_from_evaluation() -> None:
    """Judge output must not contain forbidden recommendation terms."""
    judge = ResearchJudgeService()
    eval_ = judge.evaluate_report(
        report_id=_REPORT_ID,
        report_data={
            "content_markdown": (
                "## Bull Case\nStrong EV pipeline.\n"
                "## Risk\nDebt headwinds.\n"
            ),
            "source_summary_json": {"source_count": 5, "citation_count": 3},
            "safety_validation_json": {"passed": True},
            "schema_validation_json": {"is_valid": True},
        },
    )
    notes_text = " ".join(eval_.calibration_notes + eval_.lessons_learned)
    # Allowed: standard disclaimer contains "No BUY/SELL/HOLD" — that's expected
    # Forbidden: ACTUAL recommendation statements
    for term in ["price target", "fair value", "upside of", "guaranteed return"]:
        assert term.lower() not in notes_text.lower(), (
            f"Forbidden term '{term}' found in judge notes."
        )


def test_judge_detects_forbidden_terms_in_content() -> None:
    """Judge should flag report content containing forbidden terms."""
    judge = ResearchJudgeService()
    eval_ = judge.evaluate_report(
        report_id=None,
        report_data={
            "title": "Test",
            "content_markdown": "We recommend a BUY with a price target of 150.",
            "source_summary_json": {},
        },
    )
    assert eval_.safety_passed is False
    assert len(eval_.forbidden_terms_found) > 0
    assert eval_.judge_status == "outcome_review_required"


def test_judge_scan_output_helper() -> None:
    """scan_output_for_forbidden_terms should detect known forbidden terms."""
    judge = ResearchJudgeService()
    found = judge.scan_output_for_forbidden_terms("We recommend a BUY with fair value of 100.")
    assert "BUY" in found
    assert "fair value" in found


def test_judge_outcome_alignment_with_mock_outcome() -> None:
    judge = ResearchJudgeService()
    outcome_data = {
        "data_available": True,
        "absolute_return": 0.05,
        "warnings": ["MOCK DATA"],
    }
    eval_ = judge.evaluate_report(
        report_id=_REPORT_ID,
        report_data={
            "content_markdown": "## Bull Case\nEV. ## Risk\nDebt.",
            "source_summary_json": {"source_count": 5, "citation_count": 3},
            "schema_validation_json": {"is_valid": True},
        },
        outcome_data=outcome_data,
    )
    assert eval_.outcome_alignment_score >= 0.0


def test_judge_outcome_alignment_no_data() -> None:
    """Outcome alignment should be 0.0 when no outcome data is provided."""
    judge = ResearchJudgeService()
    eval_ = judge.evaluate_report(
        report_id=None,
        report_data={"content_markdown": "## Risk\nDebt."},
        outcome_data=None,
    )
    assert eval_.outcome_alignment_score == 0.0


# ---------------------------------------------------------------------------
# BacktestingService unit tests (mock DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backtesting_service_create_run(mock_db: AsyncMock) -> None:
    """create_backtest_run should persist a BacktestRun and return it."""
    svc = BacktestingService()

    # Mock db.add / commit / refresh
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    async def _fake_refresh(obj: Any) -> None:
        pass

    mock_db.refresh.side_effect = _fake_refresh

    payload = BacktestRunCreate(name="My Test Run", horizon_days=90)

    added_objects: list[Any] = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    await svc.create_backtest_run(mock_db, payload)
    assert mock_db.add.called
    assert mock_db.commit.called


@pytest.mark.asyncio
async def test_backtesting_service_get_run_not_found(mock_db: AsyncMock) -> None:
    svc = BacktestingService()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    run = await svc.get_backtest_run(mock_db, _RUN_ID)
    assert run is None


@pytest.mark.asyncio
async def test_backtesting_service_list_runs_empty(mock_db: AsyncMock) -> None:
    svc = BacktestingService()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    runs = await svc.list_backtest_runs(mock_db)
    assert runs == []


@pytest.mark.asyncio
async def test_backtesting_service_add_report_run_not_found(mock_db: AsyncMock) -> None:
    svc = BacktestingService()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(ValueError, match="not found"):
        await svc.add_report_to_backtest(mock_db, _RUN_ID, _REPORT_ID)


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_backtest_run_endpoint(
    client: AsyncClient, mock_db: AsyncMock
) -> None:
    """POST /api/v1/backtesting/runs creates a run."""
    # Patch the service method
    run = _make_mock_run()
    with patch.object(BacktestingService, "create_backtest_run", new=AsyncMock(return_value=run)):
        response = await client.post(
            "/api/v1/backtesting/runs",
            json={"name": "Phase 22 Test Run", "horizon_days": 90},
        )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Run"
    assert "disclaimer" in data
    assert "NOT INVESTMENT ADVICE" in data["disclaimer"]


@pytest.mark.asyncio
async def test_list_backtest_runs_endpoint(
    client: AsyncClient, mock_db: AsyncMock
) -> None:
    """GET /api/v1/backtesting/runs returns a list."""
    run = _make_mock_run()
    with patch.object(
        BacktestingService, "list_backtest_runs", new=AsyncMock(return_value=[run])
    ):
        response = await client.get("/api/v1/backtesting/runs")
    assert response.status_code == 200
    data = response.json()
    assert "runs" in data
    assert data["total"] == 1
    assert "NOT INVESTMENT ADVICE" in data["disclaimer"]


@pytest.mark.asyncio
async def test_get_backtest_run_not_found(
    client: AsyncClient, mock_db: AsyncMock
) -> None:
    """GET /api/v1/backtesting/runs/{id} returns 404 if not found."""
    with patch.object(
        BacktestingService, "get_backtest_run", new=AsyncMock(return_value=None)
    ):
        response = await client.get(f"/api/v1/backtesting/runs/{_RUN_ID}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_evaluate_backtest_run_not_found(
    client: AsyncClient, mock_db: AsyncMock
) -> None:
    with patch.object(
        BacktestingService,
        "evaluate_backtest_run",
        new=AsyncMock(side_effect=ValueError("not found")),
    ):
        response = await client.post(f"/api/v1/backtesting/runs/{_RUN_ID}/evaluate")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_results_run_not_found(
    client: AsyncClient, mock_db: AsyncMock
) -> None:
    with patch.object(
        BacktestingService, "get_backtest_run", new=AsyncMock(return_value=None)
    ):
        response = await client.get(f"/api/v1/backtesting/runs/{_RUN_ID}/results")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_summary_not_found(
    client: AsyncClient, mock_db: AsyncMock
) -> None:
    with patch.object(
        BacktestingService,
        "summarize_backtest_run",
        new=AsyncMock(side_effect=ValueError("not found")),
    ):
        response = await client.get(f"/api/v1/backtesting/runs/{_RUN_ID}/summary")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_judge_report_not_found(
    client: AsyncClient, mock_db: AsyncMock
) -> None:
    """POST /api/v1/backtesting/reports/{id}/judge returns 404 if report missing."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    response = await client.post(f"/api/v1/backtesting/reports/{_REPORT_ID}/judge")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_judge_report_returns_evaluation(
    client: AsyncClient, mock_db: AsyncMock, mock_report: MagicMock
) -> None:
    """POST /api/v1/backtesting/reports/{id}/judge returns judge evaluation."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_report
    mock_db.execute = AsyncMock(return_value=mock_result)

    response = await client.post(f"/api/v1/backtesting/reports/{_REPORT_ID}/judge")
    assert response.status_code == 200
    data = response.json()
    assert "evaluation" in data
    eval_data = data["evaluation"]
    assert "judge_score" in eval_data
    assert "judge_status" in eval_data
    assert eval_data["judge_status"] in ALLOWED_JUDGE_STATUSES
    assert "disclaimer" in data
    # Disclaimer must state this is NOT investment advice
    assert "NOT INVESTMENT ADVICE" in data["disclaimer"]
    # Status must not be a public recommendation
    for forbidden_status in ("BUY", "SELL", "HOLD", "WATCH"):
        assert eval_data["judge_status"].upper() != forbidden_status


# ---------------------------------------------------------------------------
# Safety gate tests
# ---------------------------------------------------------------------------


def test_forbidden_terms_list_non_empty() -> None:
    """FORBIDDEN_OUTPUT_TERMS must be non-empty."""
    assert len(FORBIDDEN_OUTPUT_TERMS) > 0


def test_no_forbidden_terms_in_allowed_statuses() -> None:
    """Allowed judge statuses must not contain forbidden recommendation words."""
    forbidden_simple = {"buy", "sell", "hold", "watch"}
    for status in ALLOWED_JUDGE_STATUSES:
        assert status.lower() not in forbidden_simple, (
            f"Allowed status '{status}' is a forbidden recommendation term."
        )


def test_judge_evaluation_disclaimer_present() -> None:
    """JudgeEvaluation must always include a disclaimer."""
    eval_ = JudgeEvaluation(
        judge_score=0.5,
        evidence_quality_score=0.5,
        risk_coverage_score=0.5,
        data_completeness_score=0.5,
    )
    assert eval_.disclaimer
    assert "NOT INVESTMENT ADVICE" in eval_.disclaimer


def test_historical_outcome_disclaimer_present() -> None:
    """HistoricalOutcome must always include a disclaimer."""
    outcome = HistoricalOutcome(
        ticker="VOW3",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 4, 1),
        horizon_days=90,
    )
    assert outcome.disclaimer
    assert "Not investment advice" in outcome.disclaimer


# ---------------------------------------------------------------------------
# Migration chain sanity
# ---------------------------------------------------------------------------


def test_migration_009_imports_without_error() -> None:
    """Migration 009 must be importable without errors."""
    import importlib.util
    from pathlib import Path

    migration_path = (
        Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "009_add_backtesting_tables.py"
    )
    spec = importlib.util.spec_from_file_location("migration_009", migration_path)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert mod.revision == "009"
    assert mod.down_revision == "008"
