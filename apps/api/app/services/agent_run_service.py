import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun, AgentStep


async def create_agent_run(
    db: AsyncSession,
    workflow_name: str,
    workflow_version: str = "1.0.0",
    trigger_type: str = "manual",
    created_by_user_id: uuid.UUID | None = None,
) -> AgentRun:
    run = AgentRun(
        workflow_name=workflow_name,
        workflow_version=workflow_version,
        trigger_type=trigger_type,
        created_by_user_id=created_by_user_id,
        status="running",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def complete_agent_run(
    db: AsyncSession,
    run: AgentRun,
    total_tokens: int | None = None,
    total_cost: float | None = None,
) -> AgentRun:
    run.status = "completed"
    run.finished_at = datetime.now(timezone.utc)
    run.total_tokens = total_tokens
    run.total_cost = total_cost
    await db.commit()
    await db.refresh(run)
    return run


async def fail_agent_run(
    db: AsyncSession, run: AgentRun, error_message: str
) -> AgentRun:
    run.status = "failed"
    run.finished_at = datetime.now(timezone.utc)
    run.error_message = error_message
    await db.commit()
    await db.refresh(run)
    return run


async def create_agent_step(
    db: AsyncSession,
    run: AgentRun,
    agent_name: str,
    step_name: str,
    input_data: dict | None = None,
) -> AgentStep:
    step = AgentStep(
        agent_run_id=run.id,
        agent_name=agent_name,
        step_name=step_name,
        status="running",
        input_json=input_data,
    )
    db.add(step)
    await db.commit()
    await db.refresh(step)
    return step


async def complete_agent_step(
    db: AsyncSession,
    step: AgentStep,
    output_data: dict | None = None,
    tokens_used: int | None = None,
    cost: float | None = None,
    model_name: str | None = None,
) -> AgentStep:
    step.status = "completed"
    step.finished_at = datetime.now(timezone.utc)
    step.output_json = output_data
    step.tokens_used = tokens_used
    step.cost = cost
    step.model_name = model_name
    await db.commit()
    await db.refresh(step)
    return step


async def fail_agent_step(
    db: AsyncSession, step: AgentStep, error_message: str
) -> AgentStep:
    step.status = "failed"
    step.finished_at = datetime.now(timezone.utc)
    step.error_message = error_message
    await db.commit()
    await db.refresh(step)
    return step
