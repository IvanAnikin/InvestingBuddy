"""
Company Analysis Workflow — Phase 2 skeleton.

Uses LangGraph StateGraph with deterministic placeholder nodes.
No LLM calls are made in this phase; nodes produce structured placeholder output.

Every execution is persisted:
  - one agent_run record (lifecycle: running → completed/failed)
  - one agent_step per node

To wire real LLM calls, replace node bodies with LangChain chain invocations
and keep the persistence/state update logic unchanged.
"""

import re
import uuid
from datetime import datetime, timezone

from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import CompanyAnalysisState
from app.schemas.report import ReportCreate
from app.schemas.source import CitationCreate, SourceCreate
from app.services import (
    agent_run_service,
    citation_service,
    company_service,
    report_service,
    source_service,
)

WORKFLOW_NAME = "company_analysis"
WORKFLOW_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Placeholder analysis logic — deterministic, no LLM
# ---------------------------------------------------------------------------

def _build_placeholder_analysis(state: CompanyAnalysisState) -> dict:
    """Produce a deterministic analysis output for a company."""
    ticker = state.get("ticker") or "UNKNOWN"
    company_name = state.get("company_name") or ticker
    sector = state.get("company_sector") or "Unknown sector"

    return {
        "ticker": ticker,
        "company_name": company_name,
        "rating": "WATCH",
        "confidence_score": 0.50,
        "risk_score": 0.50,
        "investment_horizon_months": 24,
        "thesis": (
            f"{company_name} is being added to the research pipeline for "
            f"initial review. Sector: {sector}. "
            "Full LLM-powered analysis will run once Azure OpenAI is configured."
        ),
        "bull_case": [
            "Company has been identified as a candidate for further research.",
            "Sector exposure may align with macro tailwinds.",
        ],
        "bear_case": [
            "No financial data has been verified yet.",
            "Analysis is placeholder — do not use for investment decisions.",
        ],
        "catalysts": [
            "Completion of full research workflow.",
            "Analyst review and data sourcing.",
        ],
        "financial_metrics": {},
        "citations": [],
        "missing_information": [
            "Financial metrics not yet sourced.",
            "LLM analysis not yet run.",
            "Filings not yet reviewed.",
        ],
        "decision_explanation": (
            "WATCH rating assigned as default pending full analysis. "
            "This is a placeholder output — human review required before any action."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_placeholder": True,
    }


def _make_report_slug(ticker: str, run_id: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", ticker.lower()).strip("-")
    short_id = run_id.replace("-", "")[:8]
    return f"company-analysis-{base}-{short_id}"


# ---------------------------------------------------------------------------
# Workflow factory — returns a compiled graph bound to the given db session
# ---------------------------------------------------------------------------

def build_company_analysis_graph(db: AsyncSession):
    """
    Return a compiled LangGraph graph with nodes that close over `db`.

    Each node:
      1. Records an agent_step at start.
      2. Does its work.
      3. Marks the step completed (or failed).
      4. Returns state updates.
    """

    # We store the live AgentRun so nodes can reference it without string↔UUID round-trips.
    _run_holder: dict = {}

    # ------------------------------------------------------------------ #
    # Node 1: initialize                                                  #
    # ------------------------------------------------------------------ #
    async def node_initialize(state: CompanyAnalysisState) -> dict:
        run = await agent_run_service.create_agent_run(
            db,
            workflow_name=WORKFLOW_NAME,
            workflow_version=WORKFLOW_VERSION,
            trigger_type="manual",
        )
        _run_holder["run"] = run

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="WorkflowController",
            step_name="initialize",
            input_data={
                "company_id": state.get("company_id"),
                "ticker": state.get("ticker"),
                "exchange": state.get("exchange"),
            },
        )

        # Resolve company from DB (by ID or ticker)
        company = None
        company_id = state.get("company_id")
        ticker = state.get("ticker")
        exchange = state.get("exchange")

        if company_id:
            company = await company_service.get_company(db, uuid.UUID(company_id))
        elif ticker and exchange:
            company = await company_service.get_company_by_ticker(db, ticker, exchange)

        if not company:
            await agent_run_service.fail_agent_step(
                db, step, "Company not found in database"
            )
            await agent_run_service.fail_agent_run(
                db, run, "Company not found in database"
            )
            return {"status": "failed", "error": "Company not found in database"}

        await agent_run_service.complete_agent_step(
            db,
            step,
            output_data={"company_id": str(company.id), "company_name": company.name},
        )

        return {
            "agent_run_id": str(run.id),
            "company_id": str(company.id),
            "company_name": company.name,
            "company_sector": company.sector,
            "company_description": company.description,
            "ticker": company.ticker,
            "status": "running",
            "error": None,
        }

    # ------------------------------------------------------------------ #
    # Node 2: analyze_company                                             #
    # ------------------------------------------------------------------ #
    async def node_analyze_company(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="CompanyAnalyst",
            step_name="analyze_company",
            input_data={
                "ticker": state.get("ticker"),
                "company_name": state.get("company_name"),
                "sector": state.get("company_sector"),
            },
        )

        analysis = _build_placeholder_analysis(state)

        await agent_run_service.complete_agent_step(
            db,
            step,
            output_data=analysis,
            model_name="placeholder",
        )

        return {"analysis_output": analysis}

    # ------------------------------------------------------------------ #
    # Node 3: save_report                                                 #
    # ------------------------------------------------------------------ #
    async def node_save_report(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="ReportWriter",
            step_name="save_draft_report",
            input_data={"analysis_output": state.get("analysis_output")},
        )

        analysis = state.get("analysis_output") or {}
        ticker = state.get("ticker") or "UNKNOWN"
        company_name = state.get("company_name") or ticker
        agent_run_id = state.get("agent_run_id")
        slug = _make_report_slug(ticker, agent_run_id or "")

        summary = (
            f"Company analysis draft for {company_name}. "
            f"Rating: {analysis.get('rating', 'WATCH')}. "
            f"Confidence: {analysis.get('confidence_score', 0):.0%}. "
            "This is a placeholder report — full LLM analysis pending."
        )

        content_md = (
            f"# {company_name} — Draft Analysis\n\n"
            f"**Rating:** {analysis.get('rating', 'WATCH')}  \n"
            f"**Confidence:** {analysis.get('confidence_score', 0):.0%}  \n"
            f"**Risk Score:** {analysis.get('risk_score', 0):.0%}  \n\n"
            f"## Thesis\n\n{analysis.get('thesis', '')}\n\n"
            "## Bull Case\n\n"
            + "\n".join(f"- {b}" for b in analysis.get("bull_case", []))
            + "\n\n## Bear Case\n\n"
            + "\n".join(f"- {b}" for b in analysis.get("bear_case", []))
            + "\n\n## Catalysts\n\n"
            + "\n".join(f"- {c}" for c in analysis.get("catalysts", []))
            + "\n\n---\n\n*[PLACEHOLDER] Output is demo data. Human review required.*\n"
        )

        report = await report_service.create_draft_report(
            db,
            ReportCreate(
                title=f"{company_name} — Draft Analysis",
                slug=slug,
                report_type="company_deep_dive",
                summary=summary,
                content_markdown=content_md,
                created_by_agent_run_id=uuid.UUID(agent_run_id) if agent_run_id else None,
            ),
        )

        # --- Phase 3: create a placeholder Source and link a Citation ---
        placeholder_source, _ = await source_service.get_or_create_source(
            db,
            SourceCreate(
                source_type="placeholder",
                title=f"[PLACEHOLDER] {company_name} — workflow-generated source",
                url=None,
                publisher="InvestingBuddy workflow (placeholder)",
                credibility_score=0.0,
            ),
        )

        citation = await citation_service.create_citation(
            db,
            CitationCreate(
                source_id=placeholder_source.id,
                report_id=report.id,
                agent_run_id=uuid.UUID(agent_run_id) if agent_run_id else None,
                claim_text="thesis",
                source_quote=(
                    "[PLACEHOLDER] This citation is auto-generated by the workflow skeleton. "
                    "Replace with real source data in Phase 4+."
                ),
            ),
        )

        await agent_run_service.complete_agent_step(
            db,
            step,
            output_data={
                "report_id": str(report.id),
                "slug": report.slug,
                "placeholder_source_id": str(placeholder_source.id),
                "citation_ids": [str(citation.id)],
            },
        )

        return {
            "draft_report_id": str(report.id),
            "placeholder_source_id": str(placeholder_source.id),
            "citation_ids": [str(citation.id)],
        }

    # ------------------------------------------------------------------ #
    # Node 4: finalize                                                    #
    # ------------------------------------------------------------------ #
    async def node_finalize(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="WorkflowController",
            step_name="finalize",
            input_data={"draft_report_id": state.get("draft_report_id")},
        )
        await agent_run_service.complete_agent_step(db, step, output_data={"status": "completed"})
        await agent_run_service.complete_agent_run(db, run)
        return {"status": "completed"}

    # ------------------------------------------------------------------ #
    # Error handler — called on any unhandled exception                   #
    # ------------------------------------------------------------------ #
    async def node_handle_error(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        if run:
            error = state.get("error") or "Unknown error"
            await agent_run_service.fail_agent_run(db, run, error)
        return {"status": "failed"}

    # ------------------------------------------------------------------ #
    # Conditional routing after initialize                                #
    # ------------------------------------------------------------------ #
    def route_after_initialize(state: CompanyAnalysisState) -> str:
        if state.get("status") == "failed":
            return "handle_error"
        return "analyze_company"

    # ------------------------------------------------------------------ #
    # Build graph                                                         #
    # ------------------------------------------------------------------ #
    graph = StateGraph(CompanyAnalysisState)

    graph.add_node("initialize", node_initialize)
    graph.add_node("analyze_company", node_analyze_company)
    graph.add_node("save_report", node_save_report)
    graph.add_node("finalize", node_finalize)
    graph.add_node("handle_error", node_handle_error)

    graph.set_entry_point("initialize")
    graph.add_conditional_edges("initialize", route_after_initialize)
    graph.add_edge("analyze_company", "save_report")
    graph.add_edge("save_report", "finalize")
    graph.add_edge("finalize", END)
    graph.add_edge("handle_error", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_company_analysis(
    db: AsyncSession,
    company_id: str | None = None,
    ticker: str | None = None,
    exchange: str | None = None,
) -> CompanyAnalysisState:
    """
    Execute the company analysis workflow and return the final state.

    Either company_id (UUID string) or (ticker + exchange) must be provided.
    The company must already exist in the database.
    """
    initial_state: CompanyAnalysisState = {
        "company_id": company_id,
        "ticker": ticker,
        "exchange": exchange,
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

    graph = build_company_analysis_graph(db)
    final_state: CompanyAnalysisState = await graph.ainvoke(initial_state)
    return final_state
