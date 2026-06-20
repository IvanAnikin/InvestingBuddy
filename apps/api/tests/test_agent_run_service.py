"""
Tests for agent_run_service persistence logic.

Uses MagicMock objects instead of real SQLAlchemy instances so that
attribute assignment works without a live database session.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.models.agent_run import AgentRun, AgentStep
from app.services import agent_run_service


def _make_run(run_id: uuid.UUID | None = None) -> MagicMock:
    """Create a MagicMock that behaves like an AgentRun for service method tests."""
    run = MagicMock(spec=AgentRun)
    run.id = run_id or uuid.uuid4()
    run.workflow_name = "company_analysis"
    run.workflow_version = "1.0.0"
    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    run.finished_at = None
    run.trigger_type = "manual"
    run.created_by_user_id = None
    run.total_tokens = None
    run.total_cost = None
    run.error_message = None
    return run


def _make_step(run_id: uuid.UUID) -> MagicMock:
    """Create a MagicMock that behaves like an AgentStep for service method tests."""
    step = MagicMock(spec=AgentStep)
    step.id = uuid.uuid4()
    step.agent_run_id = run_id
    step.agent_name = "WorkflowController"
    step.step_name = "initialize"
    step.status = "running"
    step.input_json = None
    step.output_json = None
    step.model_name = None
    step.tokens_used = None
    step.cost = None
    step.started_at = datetime.now(timezone.utc)
    step.finished_at = None
    step.error_message = None
    return step


# ---------------------------------------------------------------------------
# create_agent_run
# ---------------------------------------------------------------------------


async def test_create_agent_run_adds_and_commits(mock_db: AsyncMock) -> None:
    mock_db.refresh = AsyncMock()

    await agent_run_service.create_agent_run(mock_db, "company_analysis")

    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()
    mock_db.refresh.assert_called_once()

    added_obj = mock_db.add.call_args[0][0]
    assert isinstance(added_obj, AgentRun)
    assert added_obj.workflow_name == "company_analysis"
    assert added_obj.status == "running"
    assert added_obj.trigger_type == "manual"


async def test_create_agent_run_custom_trigger(mock_db: AsyncMock) -> None:
    mock_db.refresh = AsyncMock()

    await agent_run_service.create_agent_run(
        mock_db, "weekly_research", trigger_type="scheduled"
    )

    added_obj = mock_db.add.call_args[0][0]
    assert added_obj.trigger_type == "scheduled"
    assert added_obj.workflow_name == "weekly_research"


# ---------------------------------------------------------------------------
# complete_agent_run
# ---------------------------------------------------------------------------


async def test_complete_agent_run_sets_status(mock_db: AsyncMock) -> None:
    run = _make_run()
    mock_db.refresh = AsyncMock()

    await agent_run_service.complete_agent_run(mock_db, run, total_tokens=100)

    assert run.status == "completed"
    assert run.finished_at is not None
    assert run.total_tokens == 100
    mock_db.commit.assert_called_once()


async def test_complete_agent_run_without_tokens(mock_db: AsyncMock) -> None:
    run = _make_run()
    mock_db.refresh = AsyncMock()

    await agent_run_service.complete_agent_run(mock_db, run)

    assert run.status == "completed"
    assert run.total_tokens is None


# ---------------------------------------------------------------------------
# fail_agent_run
# ---------------------------------------------------------------------------


async def test_fail_agent_run_sets_error(mock_db: AsyncMock) -> None:
    run = _make_run()
    mock_db.refresh = AsyncMock()

    await agent_run_service.fail_agent_run(mock_db, run, "Something went wrong")

    assert run.status == "failed"
    assert run.error_message == "Something went wrong"
    assert run.finished_at is not None


# ---------------------------------------------------------------------------
# create_agent_step
# ---------------------------------------------------------------------------


async def test_create_agent_step_links_to_run(mock_db: AsyncMock) -> None:
    run = _make_run(uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
    mock_db.refresh = AsyncMock()

    await agent_run_service.create_agent_step(
        mock_db,
        run=run,
        agent_name="CompanyAnalyst",
        step_name="analyze",
        input_data={"ticker": "VOW3"},
    )

    added_obj = mock_db.add.call_args[0][0]
    assert isinstance(added_obj, AgentStep)
    assert added_obj.agent_run_id == run.id
    assert added_obj.agent_name == "CompanyAnalyst"
    assert added_obj.step_name == "analyze"
    assert added_obj.status == "running"
    assert added_obj.input_json == {"ticker": "VOW3"}


async def test_create_agent_step_without_input(mock_db: AsyncMock) -> None:
    run = _make_run()
    mock_db.refresh = AsyncMock()

    await agent_run_service.create_agent_step(
        mock_db, run=run, agent_name="Reporter", step_name="write"
    )

    added_obj = mock_db.add.call_args[0][0]
    assert added_obj.input_json is None


# ---------------------------------------------------------------------------
# complete_agent_step
# ---------------------------------------------------------------------------


async def test_complete_agent_step_sets_output(mock_db: AsyncMock) -> None:
    run = _make_run()
    step = _make_step(run.id)
    mock_db.refresh = AsyncMock()
    output = {"rating": "WATCH", "confidence_score": 0.5}

    await agent_run_service.complete_agent_step(
        mock_db, step, output_data=output, model_name="placeholder"
    )

    assert step.status == "completed"
    assert step.output_json == output
    assert step.model_name == "placeholder"
    assert step.finished_at is not None


# ---------------------------------------------------------------------------
# fail_agent_step
# ---------------------------------------------------------------------------


async def test_fail_agent_step_sets_error(mock_db: AsyncMock) -> None:
    run = _make_run()
    step = _make_step(run.id)
    mock_db.refresh = AsyncMock()

    await agent_run_service.fail_agent_step(mock_db, step, "Step exploded")

    assert step.status == "failed"
    assert step.error_message == "Step exploded"
    assert step.finished_at is not None
