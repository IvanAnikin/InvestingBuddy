import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.agent import WorkflowRunRequest, WorkflowRunResponse
from app.workflows.company_analysis import run_company_analysis

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post(
    "/company-analysis/run",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger company analysis workflow",
    description=(
        "Run the company analysis workflow for a given company. "
        "Supply either company_id or (ticker + exchange). "
        "The company must already exist in the database. "
        "Returns the agent run ID, draft report ID, and a summary of the result."
    ),
)
async def run_company_analysis_endpoint(
    payload: WorkflowRunRequest,
    db: AsyncSession = Depends(get_db),
) -> WorkflowRunResponse:
    if not payload.company_id and not payload.ticker:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either company_id or ticker (+ exchange).",
        )

    try:
        final_state = await run_company_analysis(
            db=db,
            company_id=str(payload.company_id) if payload.company_id else None,
            ticker=payload.ticker,
            exchange=payload.exchange,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Workflow execution failed: {exc}",
        ) from exc

    if final_state.get("status") == "failed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=final_state.get("error") or "Workflow failed — see agent_run logs.",
        )

    agent_run_id_str = final_state.get("agent_run_id")
    draft_report_id_str = final_state.get("draft_report_id")

    return WorkflowRunResponse(
        agent_run_id=uuid.UUID(agent_run_id_str) if agent_run_id_str else uuid.uuid4(),
        draft_report_id=uuid.UUID(draft_report_id_str) if draft_report_id_str else None,
        status=final_state.get("status", "completed"),
        summary=(
            final_state.get("analysis_output", {}).get("thesis", "Workflow completed.")
            if final_state.get("analysis_output")
            else "Workflow completed."
        ),
        workflow_name="company_analysis",
        company_name=final_state.get("company_name"),
        ticker=final_state.get("ticker"),
    )
