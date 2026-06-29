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
        "The company must already exist in the database.\n\n"
        "Phase 7: optionally runs an LLM research sections node after the provider snapshot. "
        "Set use_llm=true to enable; defaults to false (offline, no LLM credentials required). "
        "LLM output is constrained to draft sections only — no rating, no price target. "
        "Schema validation always runs regardless of LLM usage. "
        "Returns agent_run_id, draft_report_id, provider summary, schema validation result, "
        "and llm_used/llm_provider summary."
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
            provider_name=payload.provider_name,
            require_schema_valid=payload.require_schema_valid,
            use_llm=payload.use_llm,
            llm_provider=payload.llm_provider,
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
    validation_result = final_state.get("schema_validation_result") or {}
    snapshot = final_state.get("company_snapshot") or {}
    llm_used = final_state.get("llm_used", False)
    llm_provider_used = final_state.get("llm_provider")

    company_name = final_state.get("company_name", "company")
    provider_name_used = final_state.get("provider_name", "mock")
    schema_label = "valid" if final_state.get("schema_valid") else "invalid"
    llm_label = f"LLM: {llm_provider_used}" if llm_used else "LLM: not used"

    # Phase 8: Research Team outputs
    financial_data_summary = final_state.get("financial_data_summary")
    source_quality_summary = final_state.get("source_quality_summary")
    research_completeness_summary = final_state.get("research_completeness_summary")
    upgraded_citation_validation = final_state.get("upgraded_citation_validation")
    research_team_warnings = final_state.get("research_team_warnings") or []

    # Phase 9: Analysis Council outputs
    bull_case_summary = final_state.get("bull_case_summary")
    bear_case_summary = final_state.get("bear_case_summary")
    risk_summary = final_state.get("risk_summary")
    valuation_guard_summary = final_state.get("valuation_guard_summary")
    committee_chair_summary = final_state.get("committee_chair_summary")
    analysis_council_warnings = final_state.get("analysis_council_warnings") or []
    quality_gate_status = final_state.get("quality_gate_status")
    provisional_internal_status = final_state.get("provisional_internal_status")
    human_review_required = final_state.get("human_review_required")

    source_quality = (source_quality_summary or {}).get("overall_source_quality", "unknown")
    citation_v2_status = (upgraded_citation_validation or {}).get("status", "unknown")

    # Build compact summaries for API response (avoid large nested objects)
    fda_compact = None
    if financial_data_summary:
        fda = financial_data_summary
        fda_compact = {
            "available_count": len(fda.get("available_financial_data", [])),
            "missing_count": len(fda.get("missing_financial_data", [])),
            "source_tier_summary": fda.get("source_tier_summary", {}),
            "financial_context_summary": fda.get("financial_context_summary", ""),
            "warnings_count": len(fda.get("warnings", [])),
        }

    sq_compact = None
    if source_quality_summary:
        sq = source_quality_summary
        sq_compact = {
            "overall_source_quality": source_quality,
            "strong_sources_count": len(sq.get("strong_sources", [])),
            "weak_sources_count": len(sq.get("weak_sources", [])),
            "aggregator_only_claims_count": len(sq.get("aggregator_only_claims", [])),
            "warnings_count": len(sq.get("warnings", [])),
        }

    rc_compact = None
    if research_completeness_summary:
        rc = research_completeness_summary
        rc_compact = {
            "complete_sections": rc.get("complete_sections", []),
            "incomplete_sections_count": len(rc.get("incomplete_sections", [])),
            "missing_required_fields_count": len(rc.get("missing_required_fields", [])),
            "blocking_gaps_count": len(rc.get("blocking_gaps", [])),
            "next_research_tasks_count": len(rc.get("next_research_tasks", [])),
        }

    cv2_compact = None
    if upgraded_citation_validation:
        cv2 = upgraded_citation_validation
        cv2_compact = {
            "status": citation_v2_status,
            "approved_claims_count": len(cv2.get("approved_claims", [])),
            "missing_citations_count": len(cv2.get("missing_citations", [])),
            "weak_citation_warnings_count": len(cv2.get("weak_citation_warnings", [])),
            "unsupported_number_warnings_count": len(
                cv2.get("unsupported_number_warnings", [])
            ),
            "source_tier_warnings_count": len(cv2.get("source_tier_warnings", [])),
        }

    # Phase 9: Analysis Council compact summaries
    bc_compact = None
    if bull_case_summary:
        bc = bull_case_summary
        bc_compact = {
            "confidence_level": bc.get("confidence_level", "low"),
            "positive_thesis_points_count": len(bc.get("positive_thesis_points", [])),
            "potential_tailwinds_count": len(bc.get("potential_tailwinds", [])),
            "missing_evidence_count": len(bc.get("missing_evidence", [])),
            "warnings_count": len(bc.get("warnings", [])),
        }

    br_compact = None
    if bear_case_summary:
        br = bear_case_summary
        br_compact = {
            "confidence_level": br.get("confidence_level", "low"),
            "negative_thesis_points_count": len(br.get("negative_thesis_points", [])),
            "key_unknowns_count": len(br.get("key_unknowns", [])),
            "warnings_count": len(br.get("warnings", [])),
        }

    risk_compact = None
    if risk_summary:
        rs = risk_summary
        risk_compact = {
            "risk_summary": rs.get("risk_summary", ""),
            "business_risks_count": len(rs.get("business_risks", [])),
            "financial_risks_count": len(rs.get("financial_risks", [])),
            "market_risks_count": len(rs.get("market_risks", [])),
            "data_quality_risks_count": len(rs.get("data_quality_risks", [])),
            "source_quality_risks_count": len(rs.get("source_quality_risks", [])),
            "warnings_count": len(rs.get("warnings", [])),
        }

    vg_compact = None
    if valuation_guard_summary:
        vg = valuation_guard_summary
        vg_compact = {
            "valuation_readiness": vg.get("valuation_readiness", "not_ready"),
            "blockers_count": len(vg.get("valuation_blockers", [])),
            "available_inputs_count": len(vg.get("available_valuation_inputs", [])),
            "missing_inputs_count": len(vg.get("missing_valuation_inputs", [])),
            "warnings_count": len(vg.get("warnings", [])),
        }

    cc_compact = None
    if committee_chair_summary:
        cc = committee_chair_summary
        cc_compact = {
            "committee_summary": cc.get("committee_summary", ""),
            "bull_bear_balance": cc.get("bull_bear_balance", "insufficient_data"),
            "provisional_internal_status": cc.get(
                "provisional_internal_status", "research_incomplete"
            ),
            "human_review_required": cc.get("human_review_required", True),
            "open_questions_count": len(cc.get("primary_open_questions", [])),
            "research_next_steps_count": len(cc.get("research_next_steps", [])),
            "warnings_count": len(cc.get("warnings", [])),
        }

    internal_status = provisional_internal_status or (
        (committee_chair_summary or {}).get("provisional_internal_status", "research_incomplete")
    )
    human_review = human_review_required if human_review_required is not None else True

    summary = (
        f"Phase 9 Analysis Council draft for {company_name}. "
        f"Provider: {provider_name_used}. "
        f"Schema: {schema_label}. "
        f"Source quality: {source_quality}. "
        f"Internal status: {internal_status}. "
        f"Human review: {human_review}. "
        f"{llm_label}."
    )

    return WorkflowRunResponse(
        agent_run_id=uuid.UUID(agent_run_id_str) if agent_run_id_str else uuid.uuid4(),
        draft_report_id=uuid.UUID(draft_report_id_str) if draft_report_id_str else None,
        status=final_state.get("status", "completed"),
        summary=summary,
        workflow_name="company_analysis",
        company_name=final_state.get("company_name"),
        ticker=final_state.get("ticker"),
        provider_name=final_state.get("provider_name"),
        is_mock=final_state.get("is_mock"),
        schema_valid=final_state.get("schema_valid"),
        validation_errors=validation_result.get("errors", []),
        validation_warnings=validation_result.get("warnings", []),
        missing_fields=snapshot.get("missing_fields", []),
        llm_provider=llm_provider_used,
        llm_used=llm_used,
        # Phase 8: Research Team
        financial_data_summary=fda_compact,
        source_quality_summary=sq_compact,
        research_completeness_summary=rc_compact,
        citation_validation_summary=cv2_compact,
        research_team_warnings=research_team_warnings,
        # Phase 9: Analysis Council
        bull_case_summary=bc_compact,
        bear_case_summary=br_compact,
        risk_summary=risk_compact,
        valuation_guard_summary=vg_compact,
        committee_chair_summary=cc_compact,
        analysis_council_warnings=analysis_council_warnings,
        quality_gate_status=quality_gate_status,
        provisional_internal_status=internal_status,
        human_review_required=human_review,
    )
