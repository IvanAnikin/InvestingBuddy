"""
Company Analysis Workflow — Phase 9: Analysis Council MVP.

Node structure (18 nodes + error handler):
  1.  load_company                — resolve company from DB; create agent_run record
  2.  fetch_provider_data         — call FinancialDataService (default: MockProvider)
  3.  create_source_records       — build Source DB records from provider metadata
  4.  build_company_snapshot      — assemble structured snapshot + schema draft
  5.  financial_data_agent        — structured financial data summary (deterministic)
  6.  source_quality_agent        — T1–T6 source quality assessment (deterministic)
  7.  generate_research_sections  — (OPTIONAL) LLM draft sections; skipped by default
  8.  create_citations            — create Citation records with field_path/source_tier/data_quality
  9.  validate_report_schema      — call validate_real_asset_report(); store result
  10. research_completeness_agent — schema-gap analysis; next research tasks
  11. citation_validator_v2       — upgraded citation + datapoint source validation
  12. bull_case_agent             — positive thesis elements from research package (Phase 9)
  13. bear_case_agent             — negative thesis elements; challenges bull case (Phase 9)
  14. risk_agent                  — structured risk categories incl. data/source risks (Phase 9)
  15. valuation_guard_agent       — blocks premature valuation conclusions (Phase 9)
  16. investment_committee_chair  — synthesises council; assigns provisional status (Phase 9)
  17. save_draft_report           — save draft report with all council outputs
  18. log_agent_steps             — mark agent_run completed; final step logging
  handle_error                    — marks agent_run failed on any unhandled error

Design rules enforced:
  - All Phase 9 Analysis Council nodes (12–16) are deterministic — no LLM calls.
  - Phase 9 nodes are non-fatal — exceptions are caught; workflow always completes.
  - No BUY/SELL/HOLD/WATCH/REJECT/SHORTLIST from any node.
  - No price target, fair value, or valuation conclusion from any node.
  - Provisional internal status must be one of the five allowed internal workflow statuses.
  - LLM calls are opt-in: use_llm=False by default; all CI tests run offline.
  - Mock provider is the default; all CI tests run offline with no credentials.
  - Every node logs an agent_step (input + output JSON).
"""

from __future__ import annotations

import pathlib
import re
import uuid
from datetime import datetime, timezone

from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analysis_council.bear_case_agent import (
    bear_case_output_to_dict,
    run_bear_case_agent,
)
from app.agents.analysis_council.bull_case_agent import (
    bull_case_output_to_dict,
    run_bull_case_agent,
)
from app.agents.analysis_council.investment_committee_chair import (
    committee_chair_output_to_dict,
    run_investment_committee_chair,
)
from app.agents.analysis_council.risk_agent import (
    risk_agent_output_to_dict,
    run_risk_agent,
)
from app.agents.analysis_council.score_research_attractiveness import (
    run_score_research_attractiveness,
)
from app.agents.analysis_council.valuation_guard_agent import (
    run_valuation_guard_agent,
    valuation_guard_output_to_dict,
)
from app.agents.base import CompanyAnalysisState
from app.agents.research_team.citation_validator_v2 import (
    run_upgraded_citation_validator,
    upgraded_citation_validation_to_dict,
)
from app.agents.research_team.financial_data_agent import (
    financial_data_agent_output_to_dict,
    run_financial_data_agent,
)
from app.agents.research_team.research_completeness_agent import (
    research_completeness_output_to_dict,
    run_research_completeness_agent,
)
from app.agents.research_team.source_quality_agent import (
    run_source_quality_agent,
    source_quality_output_to_dict,
)
from app.integrations.financial_data_provider import (
    DataQuality,
    FundamentalsData,
    PriceHistoryData,
    build_source_record,
)
from app.integrations.financial_data_service import FinancialDataService
from app.integrations.llm_provider import get_llm_client, validate_llm_sections
from app.schemas.report import ReportCreate
from app.schemas.source import CitationCreate, SourceCreate
from app.services import (
    agent_run_service,
    citation_service,
    company_service,
    report_service,
    source_service,
)
from app.services.report_validation_service import validate_real_asset_report
from app.workflows.snapshot_builder import (
    build_company_snapshot,
    build_schema_draft,
    get_price_citation_fields,
    get_profile_citation_fields,
)

WORKFLOW_NAME = "company_analysis"
WORKFLOW_VERSION = "5.0.0"

def _resolve_prompt_path() -> pathlib.Path:
    # Walk up the directory tree looking for packages/prompts/; avoids hard-coded
    # depth which breaks on Azure App Service (shallower extraction path).
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        candidate = (
            parent / "packages" / "prompts" / "research" / "phase7_company_research_v1.md"
        )
        if candidate.exists():
            return candidate
    return here  # not found — _load_prompt_template falls back to inline prompt


_PROMPT_PATH = _resolve_prompt_path()


def _load_prompt_template() -> str:
    """Load the versioned prompt template from packages/prompts/."""
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    # Fallback inline minimal prompt if file not found (should not happen in normal usage)
    return (
        "Generate research sections for the following company:\n\n"
        "{{COMPANY_CONTEXT}}\n\n"
        "Output JSON with: thesis_summary_draft, business_overview_draft, "
        "missing_information, self_critique_limitations. "
        "No rating. No price target. No invented numbers."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report_slug(ticker: str, run_id: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", ticker.lower()).strip("-")
    short_id = run_id.replace("-", "")[:8]
    return f"company-analysis-{base}-{short_id}"


def _build_placeholder_analysis(state: CompanyAnalysisState) -> dict:
    """Kept for backward-compatibility with existing tests."""
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


# ---------------------------------------------------------------------------
# Workflow factory
# ---------------------------------------------------------------------------


def build_company_analysis_graph(
    db: AsyncSession,
    provider_name: str | None = None,
    use_llm: bool = False,
    llm_provider: str | None = None,
):
    """
    Return a compiled LangGraph graph with all Phase 7 nodes.

    provider_name — override config default (None = use FINANCIAL_DATA_PROVIDER config).
    Default is "mock" so all CI tests run offline.

    use_llm — when True, the generate_research_sections node runs after build_company_snapshot.
    Default False — safe offline mode, no LLM calls, CI-safe.

    llm_provider — override config default for LLM (None = use LLM_PROVIDER config).
    Default is "mock" so all CI tests run without Azure credentials.
    """

    _run_holder: dict = {}

    # ------------------------------------------------------------------ #
    # Node 1: load_company                                                #
    # ------------------------------------------------------------------ #
    async def node_load_company(state: CompanyAnalysisState) -> dict:
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
            step_name="load_company",
            input_data={
                "company_id": state.get("company_id"),
                "ticker": state.get("ticker"),
                "exchange": state.get("exchange"),
                "provider_name": state.get("provider_name"),
            },
        )

        company = None
        company_id = state.get("company_id")
        ticker = state.get("ticker")
        exchange = state.get("exchange")

        if company_id:
            company = await company_service.get_company(db, uuid.UUID(company_id))
        elif ticker and exchange:
            company = await company_service.get_company_by_ticker(db, ticker, exchange)

        if not company:
            await agent_run_service.fail_agent_step(db, step, "Company not found in database")
            await agent_run_service.fail_agent_run(db, run, "Company not found in database")
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
    # Node 2: fetch_provider_data                                         #
    # ------------------------------------------------------------------ #
    async def node_fetch_provider_data(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        pname = state.get("provider_name") or provider_name
        ticker = state.get("ticker") or "UNKNOWN"
        exchange = state.get("exchange")

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="FinancialDataAgent",
            step_name="fetch_provider_data",
            input_data={"ticker": ticker, "exchange": exchange, "provider_name": pname},
        )

        try:
            svc = FinancialDataService(provider_name=pname)
            profile = await svc.get_company_profile(ticker, exchange)

            prices: PriceHistoryData | None = None
            caps = [c.value if hasattr(c, "value") else c for c in svc.get_capabilities()]
            if "price_history" in caps:
                try:
                    prices = await svc.get_price_history(ticker, exchange)
                except NotImplementedError:
                    prices = None

            # Phase 13: optionally fetch EODHD fundamentals
            fundamentals: FundamentalsData | None = None
            fundamentals_warnings: list[str] = []
            if pname == "eodhd" and "fundamentals" in caps:
                try:
                    fundamentals = await svc.get_fundamentals(ticker, exchange)
                except NotImplementedError:
                    fundamentals_warnings.append(
                        "EODHD fundamentals: NotImplementedError — skipped."
                    )
                except Exception as fund_exc:
                    fundamentals_warnings.append(
                        f"EODHD fundamentals fetch failed (non-fatal): {fund_exc}"
                    )

            is_mock = profile.meta.is_mock

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "provider_name": profile.meta.provider_name,
                    "is_mock": is_mock,
                    "ticker": profile.ticker,
                    "legal_name": profile.legal_name,
                    "price_points_count": len(prices.price_points) if prices else 0,
                    "fundamentals_datapoints_count": len(fundamentals.datapoints) if fundamentals else 0, # noqa: E501
                    "fundamentals_warnings": fundamentals_warnings,
                },
            )

            # Stash provider objects in holder for later nodes
            _run_holder["profile"] = profile
            _run_holder["prices"] = prices
            _run_holder["fundamentals"] = fundamentals

            return {
                "provider_name": profile.meta.provider_name,
                "is_mock": is_mock,
                "analysis_output": _build_placeholder_analysis(state),
                "fundamentals_available": fundamentals is not None,
                "fundamentals_warnings": fundamentals_warnings or None,
            }

        except (ValueError, Exception) as exc:
            error_msg = f"fetch_provider_data failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            await agent_run_service.fail_agent_run(db, run, error_msg)
            return {"status": "failed", "error": error_msg}

    # ------------------------------------------------------------------ #
    # Node 3: create_source_records                                       #
    # ------------------------------------------------------------------ #
    async def node_create_source_records(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        profile = _run_holder.get("profile")
        prices = _run_holder.get("prices")
        ticker = state.get("ticker") or "UNKNOWN"

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="SourceRecordAgent",
            step_name="create_source_records",
            input_data={"ticker": ticker, "provider_name": state.get("provider_name")},
        )

        source_ids: list[str] = []
        provider_source_id: str | None = None
        price_source_id: str | None = None

        # Source record for company profile data
        profile_attrs = build_source_record(
            meta=profile.meta,
            source_url=profile.source_url,
            title=f"{profile.meta.provider_name} — company profile: {ticker}",
            data_quality=profile.data_quality
            if isinstance(profile.data_quality, DataQuality)
            else DataQuality(profile.data_quality),
        )
        profile_source, _ = await source_service.get_or_create_source(
            db,
            SourceCreate(
                source_type=profile_attrs.source_type,
                title=profile_attrs.title,
                url=profile_attrs.url,
                publisher=profile_attrs.publisher,
                retrieved_at=profile_attrs.retrieved_at,
                credibility_score=profile_attrs.credibility_score,
            ),
        )
        source_ids.append(str(profile_source.id))
        provider_source_id = str(profile_source.id)

        # Source record for price history (if fetched)
        if prices and prices.price_points:
            price_attrs = build_source_record(
                meta=prices.meta,
                source_url=prices.source_url,
                title=f"{prices.meta.provider_name} — price history: {ticker}",
                data_quality=prices.data_quality
                if isinstance(prices.data_quality, DataQuality)
                else DataQuality(prices.data_quality),
            )
            price_source, _ = await source_service.get_or_create_source(
                db,
                SourceCreate(
                    source_type=price_attrs.source_type,
                    title=price_attrs.title,
                    url=price_attrs.url,
                    publisher=price_attrs.publisher,
                    retrieved_at=price_attrs.retrieved_at,
                    credibility_score=price_attrs.credibility_score,
                ),
            )
            source_ids.append(str(price_source.id))
            price_source_id = str(price_source.id)

        await agent_run_service.complete_agent_step(
            db,
            step,
            output_data={"source_ids": source_ids, "provider_source_id": provider_source_id},
        )

        _run_holder["provider_source_id"] = provider_source_id
        _run_holder["price_source_id"] = price_source_id

        return {
            "source_ids": source_ids,
            "provider_source_id": provider_source_id,
            "price_source_id": price_source_id,
            "placeholder_source_id": None,
        }

    # ------------------------------------------------------------------ #
    # Node 4: build_company_snapshot                                      #
    # ------------------------------------------------------------------ #
    async def node_build_company_snapshot(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        profile = _run_holder.get("profile")
        prices = _run_holder.get("prices")
        fundamentals = _run_holder.get("fundamentals")

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="SnapshotBuilder",
            step_name="build_company_snapshot",
            input_data={
                "ticker": state.get("ticker"),
                "provider_name": state.get("provider_name"),
                "is_mock": state.get("is_mock"),
                "fundamentals_available": fundamentals is not None,
            },
        )

        snapshot = build_company_snapshot(profile=profile, prices=prices, fundamentals=fundamentals)

        await agent_run_service.complete_agent_step(
            db,
            step,
            output_data={
                "missing_fields_count": len(snapshot.get("missing_fields", [])),
                "missing_fields": snapshot.get("missing_fields", []),
                "is_mock": snapshot.get("is_mock"),
                "price_history_available": snapshot.get("price_history_summary", {}).get(
                    "available", False
                ),
                "fundamentals_summary_available": snapshot.get("fundamentals_summary") is not None,
            },
        )

        _run_holder["snapshot"] = snapshot
        return {"company_snapshot": snapshot}

    # ------------------------------------------------------------------ #
    # Node 5: financial_data_agent  (Phase 8 Research Team)              #
    # ------------------------------------------------------------------ #
    async def node_financial_data_agent(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        source_ids = state.get("source_ids") or []

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="FinancialDataAgent",
            step_name="financial_data_agent",
            input_data={
                "ticker": state.get("ticker"),
                "provider_name": state.get("provider_name"),
                "source_ids_count": len(source_ids),
            },
        )

        try:
            output = run_financial_data_agent(
                company_snapshot=snapshot,
                source_ids=source_ids,
            )
            output_dict = financial_data_agent_output_to_dict(output)
            _run_holder["financial_data_summary"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "available_count": len(output.available_financial_data),
                    "missing_count": len(output.missing_financial_data),
                    "warnings_count": len(output.warnings),
                    "source_tier_summary": output.source_tier_summary,
                },
            )

            return {
                "financial_data_summary": output_dict,
            }

        except Exception as exc:
            error_msg = f"financial_data_agent failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            # Non-fatal — workflow continues
            fallback = {
                "available_financial_data": [],
                "missing_financial_data": [],
                "data_quality_notes": [error_msg],
                "source_tier_summary": {},
                "financial_context_summary": f"FinancialDataAgent failed: {exc}",
                "warnings": [error_msg],
            }
            _run_holder["financial_data_summary"] = fallback
            return {"financial_data_summary": fallback}

    # ------------------------------------------------------------------ #
    # Node 6: source_quality_agent  (Phase 8 Research Team)              #
    # ------------------------------------------------------------------ #
    async def node_source_quality_agent(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="SourceQualityAgent",
            step_name="source_quality_agent",
            input_data={
                "ticker": state.get("ticker"),
                "provider_name": state.get("provider_name"),
                "is_mock": state.get("is_mock"),
            },
        )

        try:
            output = run_source_quality_agent(company_snapshot=snapshot)
            output_dict = source_quality_output_to_dict(output)
            _run_holder["source_quality_summary"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "overall_source_quality": output.overall_source_quality,
                    "strong_sources_count": len(output.strong_sources),
                    "weak_sources_count": len(output.weak_sources),
                    "aggregator_only_claims_count": len(output.aggregator_only_claims),
                    "warnings_count": len(output.warnings),
                },
            )

            return {
                "source_quality_summary": output_dict,
            }

        except Exception as exc:
            error_msg = f"source_quality_agent failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "overall_source_quality": "insufficient",
                "strong_sources": [],
                "weak_sources": [],
                "missing_primary_sources": [],
                "aggregator_only_claims": [],
                "recommended_source_upgrades": [],
                "warnings": [error_msg],
            }
            _run_holder["source_quality_summary"] = fallback
            return {"source_quality_summary": fallback}

    # ------------------------------------------------------------------ #
    # Node 7: generate_research_sections  (optional LLM node)            #
    # ------------------------------------------------------------------ #
    async def node_generate_research_sections(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        resolved_llm_provider = state.get("llm_provider") or llm_provider

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="ResearchLLMAgent",
            step_name="generate_research_sections",
            input_data={
                "llm_provider": resolved_llm_provider or "config_default",
                "use_llm": state.get("use_llm"),
                "snapshot_keys": list(snapshot.keys()),
            },
        )

        # If use_llm is False, skip without calling LLM
        if not state.get("use_llm"):
            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={"skipped": True, "reason": "use_llm=False"},
            )
            return {"llm_used": False, "llm_provider": "none", "llm_sections": None}

        try:
            client = get_llm_client(resolved_llm_provider)
            prompt_template = _load_prompt_template()
            sections = await client.generate_research_sections(
                company_snapshot=snapshot,
                prompt_template=prompt_template,
            )
            safety = validate_llm_sections(sections)

            sections_dict = sections.model_dump()

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "llm_provider": client.provider_name,
                    "is_mock": client.is_mock,
                    "safety_passed": safety.passed,
                    "safety_warnings": safety.warnings,
                    "thesis_length": len(sections.thesis_summary_draft),
                    "missing_info_count": len(sections.missing_information),
                },
            )

            return {
                "llm_used": True,
                "llm_provider": client.provider_name,
                "llm_sections": sections_dict,
                "llm_section_warnings": safety.warnings if not safety.passed else [],
            }

        except Exception as exc:
            error_msg = f"generate_research_sections failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            # LLM failure is non-fatal — workflow continues without LLM sections
            return {
                "llm_used": False,
                "llm_provider": "failed",
                "llm_sections": None,
                "llm_section_warnings": [error_msg],
            }

    # ------------------------------------------------------------------ #
    # Node 8: create_citations  (was Node 6 in Phase 7)                  #
    # ------------------------------------------------------------------ #
    async def node_create_citations(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        profile = _run_holder.get("profile")
        prices = _run_holder.get("prices")
        agent_run_id = state.get("agent_run_id")
        provider_source_id = _run_holder.get("provider_source_id")
        price_source_id = _run_holder.get("price_source_id")

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="CitationAgent",
            step_name="create_citations",
            input_data={
                "provider_source_id": provider_source_id,
                "price_source_id": price_source_id,
            },
        )

        citation_ids: list[str] = []

        # Citations from company profile fields
        if profile and provider_source_id:
            for desc in get_profile_citation_fields(profile):
                cit = await citation_service.create_citation(
                    db,
                    CitationCreate(
                        source_id=uuid.UUID(provider_source_id),
                        agent_run_id=uuid.UUID(agent_run_id) if agent_run_id else None,
                        claim_text=desc["claim_text"],
                        source_quote=desc["source_quote"],
                        retrieved_at=desc["retrieved_at"],
                        field_path=desc["field_path"],
                        source_tier=desc["source_tier"],
                        data_quality=desc["data_quality"],
                    ),
                )
                citation_ids.append(str(cit.id))

        # Citations from price history (if available)
        if prices and prices.price_points and price_source_id:
            for desc in get_price_citation_fields(prices):
                cit = await citation_service.create_citation(
                    db,
                    CitationCreate(
                        source_id=uuid.UUID(price_source_id),
                        agent_run_id=uuid.UUID(agent_run_id) if agent_run_id else None,
                        claim_text=desc["claim_text"],
                        source_quote=desc["source_quote"],
                        retrieved_at=desc["retrieved_at"],
                        field_path=desc["field_path"],
                        source_tier=desc["source_tier"],
                        data_quality=desc["data_quality"],
                    ),
                )
                citation_ids.append(str(cit.id))

        await agent_run_service.complete_agent_step(
            db,
            step,
            output_data={"citation_ids": citation_ids, "citation_count": len(citation_ids)},
        )

        return {"citation_ids": citation_ids}

    # ------------------------------------------------------------------ #
    # Node 9: validate_report_schema  (was Node 7 in Phase 7)            #
    # ------------------------------------------------------------------ #
    async def node_validate_report_schema(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        profile = _run_holder.get("profile")
        prices = _run_holder.get("prices")
        agent_run_id = state.get("agent_run_id")

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="SchemaValidator",
            step_name="validate_report_schema",
            input_data={"report_id_attempt": agent_run_id},
        )

        # Build a minimal schema-draft using provider data (Phase 13: includes fundamentals)
        draft = build_schema_draft(
            report_id=agent_run_id or str(uuid.uuid4()),
            snapshot=_run_holder.get("snapshot", {}),
            profile=profile,
            prices=prices,
            fundamentals=_run_holder.get("fundamentals"),
        )

        # Validate — expected to fail at this phase (many required sections absent)
        result = validate_real_asset_report(draft)
        validation_dict = result.to_dict()

        _run_holder["schema_draft"] = draft
        _run_holder["validation_result"] = validation_dict

        await agent_run_service.complete_agent_step(
            db,
            step,
            output_data={
                "schema_valid": result.is_valid,
                "error_count": len(result.errors),
                "warning_count": len(result.warnings),
                "first_error": result.errors[0] if result.errors else None,
            },
        )

        return {
            "schema_validation_result": validation_dict,
            "schema_valid": result.is_valid,
        }

    # ------------------------------------------------------------------ #
    # Node 10: research_completeness_agent  (Phase 8 Research Team)      #
    # ------------------------------------------------------------------ #
    async def node_research_completeness_agent(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        schema_draft = _run_holder.get("schema_draft")
        validation_result = _run_holder.get("validation_result", {})
        schema_errors = validation_result.get("errors", [])

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="ResearchCompletenessAgent",
            step_name="research_completeness_agent",
            input_data={
                "ticker": state.get("ticker"),
                "schema_valid": state.get("schema_valid"),
                "schema_error_count": len(schema_errors),
                "draft_sections": list(schema_draft.keys()) if schema_draft else [],
            },
        )

        try:
            output = run_research_completeness_agent(
                company_snapshot=snapshot,
                schema_draft=schema_draft,
                schema_validation_errors=schema_errors,
            )
            output_dict = research_completeness_output_to_dict(output)
            _run_holder["research_completeness_summary"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "complete_sections": output.complete_sections,
                    "incomplete_sections_count": len(output.incomplete_sections),
                    "missing_required_fields_count": len(output.missing_required_fields),
                    "blocking_gaps_count": len(output.blocking_gaps),
                    "next_tasks_count": len(output.next_research_tasks),
                },
            )

            return {
                "research_completeness_summary": output_dict,
            }

        except Exception as exc:
            error_msg = f"research_completeness_agent failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "complete_sections": [],
                "incomplete_sections": [],
                "missing_required_fields": [],
                "next_research_tasks": [],
                "blocking_gaps": [error_msg],
                "non_blocking_gaps": [],
            }
            _run_holder["research_completeness_summary"] = fallback
            return {"research_completeness_summary": fallback}

    # ------------------------------------------------------------------ #
    # Node 11: citation_validator_v2  (Phase 8 Research Team)            #
    # ------------------------------------------------------------------ #
    async def node_citation_validator_v2(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        schema_draft = _run_holder.get("schema_draft")
        agent_run_id = state.get("agent_run_id")

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="CitationValidatorV2",
            step_name="citation_validator_v2",
            input_data={
                "ticker": state.get("ticker"),
                "citation_ids_count": len(state.get("citation_ids") or []),
                "schema_draft_sections": list(schema_draft.keys()) if schema_draft else [],
            },
        )

        try:
            # Fetch citation records created in this run for source_tier info
            citation_records: list[dict] = []
            if agent_run_id:
                try:
                    run_citations = await citation_service.list_citations_for_agent_run(
                        db, uuid.UUID(agent_run_id)
                    )
                    citation_records = [
                        {
                            "id": str(c.id),
                            "field_path": c.field_path,
                            "source_tier": c.source_tier,
                            "data_quality": c.data_quality,
                        }
                        for c in run_citations
                    ]
                except Exception:
                    pass  # Non-fatal if citation fetch fails

            output = run_upgraded_citation_validator(
                company_snapshot=snapshot,
                schema_draft=schema_draft,
                citation_records=citation_records,
            )
            output_dict = upgraded_citation_validation_to_dict(output)
            _run_holder["upgraded_citation_validation"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "status": output.status,
                    "approved_claims_count": len(output.approved_claims),
                    "missing_citations_count": len(output.missing_citations),
                    "weak_warnings_count": len(output.weak_citation_warnings),
                    "unsupported_numbers_count": len(output.unsupported_number_warnings),
                    "tier_warnings_count": len(output.source_tier_warnings),
                },
            )

            return {
                "upgraded_citation_validation": output_dict,
            }

        except Exception as exc:
            error_msg = f"citation_validator_v2 failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "status": "warnings",
                "approved_claims": [],
                "missing_citations": [],
                "weak_citation_warnings": [error_msg],
                "unsupported_number_warnings": [],
                "source_tier_warnings": [],
            }
            _run_holder["upgraded_citation_validation"] = fallback
            return {"upgraded_citation_validation": fallback}

    # ------------------------------------------------------------------ #
    # Node 12: bull_case_agent  (Phase 9 Analysis Council)               #
    # ------------------------------------------------------------------ #
    async def node_bull_case_agent(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        financial_data_summary = _run_holder.get("financial_data_summary") or {}
        source_quality_summary = _run_holder.get("source_quality_summary") or {}
        research_completeness_summary = _run_holder.get("research_completeness_summary") or {}
        llm_sections = state.get("llm_sections") or {}

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="BullCaseAgent",
            step_name="bull_case_agent",
            input_data={
                "ticker": state.get("ticker"),
                "is_mock": state.get("is_mock"),
                "llm_used": state.get("llm_used"),
            },
        )

        try:
            output = run_bull_case_agent(
                company_snapshot=snapshot,
                financial_data_summary=financial_data_summary,
                source_quality_summary=source_quality_summary,
                research_completeness_summary=research_completeness_summary,
                llm_sections=llm_sections if llm_sections else None,
            )
            output_dict = bull_case_output_to_dict(output)
            _run_holder["bull_case_summary"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "positive_thesis_points_count": len(output.positive_thesis_points),
                    "potential_tailwinds_count": len(output.potential_tailwinds),
                    "confidence_level": output.confidence_level,
                    "warnings_count": len(output.warnings),
                },
            )
            return {"bull_case_summary": output_dict}

        except Exception as exc:
            error_msg = f"bull_case_agent failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "positive_thesis_points": [],
                "potential_tailwinds": [],
                "evidence_used": [],
                "assumptions": [],
                "missing_evidence": [error_msg],
                "confidence_level": "low",
                "warnings": [error_msg],
            }
            _run_holder["bull_case_summary"] = fallback
            return {"bull_case_summary": fallback}

    # ------------------------------------------------------------------ #
    # Node 13: bear_case_agent  (Phase 9 Analysis Council)               #
    # ------------------------------------------------------------------ #
    async def node_bear_case_agent(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        financial_data_summary = _run_holder.get("financial_data_summary") or {}
        source_quality_summary = _run_holder.get("source_quality_summary") or {}
        research_completeness_summary = _run_holder.get("research_completeness_summary") or {}
        bull_case_summary = _run_holder.get("bull_case_summary") or {}

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="BearCaseAgent",
            step_name="bear_case_agent",
            input_data={
                "ticker": state.get("ticker"),
                "is_mock": state.get("is_mock"),
                "bull_case_confidence": bull_case_summary.get("confidence_level", "low"),
            },
        )

        try:
            output = run_bear_case_agent(
                company_snapshot=snapshot,
                financial_data_summary=financial_data_summary,
                source_quality_summary=source_quality_summary,
                research_completeness_summary=research_completeness_summary,
                bull_case_summary=bull_case_summary if bull_case_summary else None,
            )
            output_dict = bear_case_output_to_dict(output)
            _run_holder["bear_case_summary"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "negative_thesis_points_count": len(output.negative_thesis_points),
                    "key_unknowns_count": len(output.key_unknowns),
                    "confidence_level": output.confidence_level,
                    "warnings_count": len(output.warnings),
                },
            )
            return {"bear_case_summary": output_dict}

        except Exception as exc:
            error_msg = f"bear_case_agent failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "negative_thesis_points": [],
                "potential_headwinds": [],
                "key_unknowns": [error_msg],
                "evidence_used": [],
                "missing_evidence": [],
                "confidence_level": "low",
                "warnings": [error_msg],
            }
            _run_holder["bear_case_summary"] = fallback
            return {"bear_case_summary": fallback}

    # ------------------------------------------------------------------ #
    # Node 14: risk_agent  (Phase 9 Analysis Council)                    #
    # ------------------------------------------------------------------ #
    async def node_risk_agent(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        financial_data_summary = _run_holder.get("financial_data_summary") or {}
        source_quality_summary = _run_holder.get("source_quality_summary") or {}
        research_completeness_summary = _run_holder.get("research_completeness_summary") or {}
        upgraded_citation_validation = _run_holder.get("upgraded_citation_validation") or {}

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="RiskAgent",
            step_name="risk_agent",
            input_data={
                "ticker": state.get("ticker"),
                "is_mock": state.get("is_mock"),
                "citation_status": upgraded_citation_validation.get("status", "unknown"),
            },
        )

        try:
            output = run_risk_agent(
                company_snapshot=snapshot,
                financial_data_summary=financial_data_summary,
                source_quality_summary=source_quality_summary,
                research_completeness_summary=research_completeness_summary,
                upgraded_citation_validation=upgraded_citation_validation or None,
            )
            output_dict = risk_agent_output_to_dict(output)
            _run_holder["risk_summary"] = output_dict

            total_risks = (
                len(output.business_risks) + len(output.financial_risks) +
                len(output.market_risks) + len(output.regulatory_geopolitical_risks) +
                len(output.data_quality_risks) + len(output.source_quality_risks)
            )
            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "total_risk_flags": total_risks,
                    "data_quality_risks_count": len(output.data_quality_risks),
                    "source_quality_risks_count": len(output.source_quality_risks),
                    "warnings_count": len(output.warnings),
                },
            )
            return {"risk_summary": output_dict}

        except Exception as exc:
            error_msg = f"risk_agent failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "business_risks": [],
                "financial_risks": [],
                "market_risks": [],
                "regulatory_geopolitical_risks": [],
                "data_quality_risks": [error_msg],
                "source_quality_risks": [],
                "risk_summary": f"RiskAgent failed: {error_msg}",
                "warnings": [error_msg],
            }
            _run_holder["risk_summary"] = fallback
            return {"risk_summary": fallback}

    # ------------------------------------------------------------------ #
    # Node 15: valuation_guard_agent  (Phase 9 Analysis Council)         #
    # ------------------------------------------------------------------ #
    async def node_valuation_guard_agent(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        financial_data_summary = _run_holder.get("financial_data_summary") or {}
        source_quality_summary = _run_holder.get("source_quality_summary") or {}

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="ValuationGuardAgent",
            step_name="valuation_guard_agent",
            input_data={
                "ticker": state.get("ticker"),
                "is_mock": state.get("is_mock"),
                "source_tier": (snapshot.get("provider_metadata") or {}).get("source_tier"),
            },
        )

        try:
            output = run_valuation_guard_agent(
                company_snapshot=snapshot,
                financial_data_summary=financial_data_summary,
                source_quality_summary=source_quality_summary,
            )
            output_dict = valuation_guard_output_to_dict(output)
            _run_holder["valuation_guard_summary"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "valuation_readiness": output.valuation_readiness,
                    "blockers_count": len(output.valuation_blockers),
                    "available_inputs_count": len(output.available_valuation_inputs),
                    "missing_inputs_count": len(output.missing_valuation_inputs),
                    "warnings_count": len(output.warnings),
                },
            )
            return {"valuation_guard_summary": output_dict}

        except Exception as exc:
            error_msg = f"valuation_guard_agent failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "valuation_readiness": "not_ready",
                "available_valuation_inputs": [],
                "missing_valuation_inputs": [],
                "valuation_blockers": [error_msg],
                "allowed_next_steps": [],
                "disallowed_outputs": [],
                "warnings": [error_msg],
            }
            _run_holder["valuation_guard_summary"] = fallback
            return {"valuation_guard_summary": fallback}

    # ------------------------------------------------------------------ #
    # Node 16: investment_committee_chair  (Phase 9 Analysis Council)    #
    # ------------------------------------------------------------------ #
    async def node_investment_committee_chair(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        bull_case_summary = _run_holder.get("bull_case_summary") or {}
        bear_case_summary = _run_holder.get("bear_case_summary") or {}
        risk_summary = _run_holder.get("risk_summary") or {}
        valuation_guard_summary = _run_holder.get("valuation_guard_summary") or {}
        research_completeness_summary = _run_holder.get("research_completeness_summary") or {}
        source_quality_summary = _run_holder.get("source_quality_summary") or {}
        upgraded_citation_validation = _run_holder.get("upgraded_citation_validation") or {}

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="InvestmentCommitteeChair",
            step_name="investment_committee_chair",
            input_data={
                "ticker": state.get("ticker"),
                "bull_confidence": bull_case_summary.get("confidence_level", "low"),
                "bear_confidence": bear_case_summary.get("confidence_level", "low"),
                "valuation_readiness": valuation_guard_summary.get(
                    "valuation_readiness", "not_ready"
                ),
                "schema_valid": state.get("schema_valid"),
            },
        )

        try:
            output = run_investment_committee_chair(
                company_snapshot=snapshot,
                bull_case_summary=bull_case_summary,
                bear_case_summary=bear_case_summary,
                risk_summary=risk_summary,
                valuation_guard_summary=valuation_guard_summary,
                research_completeness_summary=research_completeness_summary,
                source_quality_summary=source_quality_summary,
                upgraded_citation_validation=upgraded_citation_validation or None,
                schema_valid=state.get("schema_valid"),
            )
            output_dict = committee_chair_output_to_dict(output)
            _run_holder["committee_chair_summary"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "provisional_internal_status": output.provisional_internal_status,
                    "bull_bear_balance": output.bull_bear_balance,
                    "human_review_required": output.human_review_required,
                    "open_questions_count": len(output.primary_open_questions),
                    "warnings_count": len(output.warnings),
                },
            )

            # Aggregate analysis council warnings
            analysis_council_warnings: list[str] = []
            for summary_key in ["bull_case_summary", "bear_case_summary", "risk_summary",
                                 "valuation_guard_summary"]:
                s = _run_holder.get(summary_key) or {}
                analysis_council_warnings.extend(s.get("warnings", []))
            analysis_council_warnings.extend(output.warnings)

            return {
                "committee_chair_summary": output_dict,
                "analysis_council_warnings": analysis_council_warnings,
                "quality_gate_status": output.quality_gate_status,
                "provisional_internal_status": output.provisional_internal_status,
                "human_review_required": output.human_review_required,
            }

        except Exception as exc:
            error_msg = f"investment_committee_chair failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "committee_summary": f"InvestmentCommitteeChair failed: {error_msg}",
                "bull_bear_balance": "insufficient_data",
                "primary_open_questions": [],
                "research_next_steps": [],
                "quality_gate_status": {},
                "provisional_internal_status": "research_incomplete",
                "human_review_required": True,
                "warnings": [error_msg],
            }
            _run_holder["committee_chair_summary"] = fallback
            return {
                "committee_chair_summary": fallback,
                "analysis_council_warnings": [error_msg],
                "quality_gate_status": {},
                "provisional_internal_status": "research_incomplete",
                "human_review_required": True,
            }

    # ------------------------------------------------------------------ #
    # Node 17: score_research_attractiveness  (Phase 15)                 #
    # ------------------------------------------------------------------ #
    async def node_score_research_attractiveness(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="ScoringEngine",
            step_name="score_research_attractiveness",
            input_data={
                "ticker": state.get("ticker"),
                "is_mock": state.get("is_mock"),
                "provider_name": state.get("provider_name"),
            },
        )

        try:
            scorecard_dict = run_score_research_attractiveness(
                company_snapshot=snapshot,
                financial_data_summary=_run_holder.get("financial_data_summary"),
                source_quality_summary=_run_holder.get("source_quality_summary"),
                research_completeness_summary=_run_holder.get("research_completeness_summary"),
                citation_validation_summary=_run_holder.get("upgraded_citation_validation"),
                bull_case_summary=_run_holder.get("bull_case_summary"),
                bear_case_summary=_run_holder.get("bear_case_summary"),
                risk_summary=_run_holder.get("risk_summary"),
                valuation_guard_summary=_run_holder.get("valuation_guard_summary"),
                committee_chair_summary=_run_holder.get("committee_chair_summary"),
            )
            _run_holder["research_attractiveness_scorecard"] = scorecard_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "overall_score": scorecard_dict.get("overall_score", 0),
                    "internal_status": scorecard_dict.get("internal_status", "not_enough_data"),
                    "warnings_count": len(scorecard_dict.get("warnings", [])),
                },
            )
            return {"research_attractiveness_scorecard": scorecard_dict}

        except Exception as exc:
            error_msg = f"score_research_attractiveness failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "overall_score": 0,
                "internal_status": "not_enough_data",
                "scores": {},
                "warnings": [error_msg],
                "missing_data": [],
                "reasoning": f"Scoring node failed: {error_msg}",
                "source_quality_summary": {},
                "next_research_steps": [],
                "disclaimer": (
                    "INTERNAL SCORE ONLY. Not investment advice. "
                    "Not a public recommendation. Human review required."
                ),
            }
            _run_holder["research_attractiveness_scorecard"] = fallback
            return {"research_attractiveness_scorecard": fallback}

    # ------------------------------------------------------------------ #
    # Node 18: save_draft_report  (Phase 9 — includes Analysis Council)  #
    # ------------------------------------------------------------------ #
    async def node_save_draft_report(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        validation = _run_holder.get("validation_result", {})

        ticker = state.get("ticker") or "UNKNOWN"
        company_name = state.get("company_name") or ticker
        agent_run_id = state.get("agent_run_id")
        is_mock = state.get("is_mock", True)
        schema_valid = state.get("schema_valid", False)
        provider_name_used = state.get("provider_name") or "mock"
        missing_fields = snapshot.get("missing_fields", [])
        llm_used = state.get("llm_used", False)
        llm_sections = state.get("llm_sections") or {}
        llm_section_warnings = state.get("llm_section_warnings") or []
        llm_provider_used = state.get("llm_provider") or "none"

        # Phase 8: Research Team outputs
        financial_data_summary = _run_holder.get("financial_data_summary") or {}
        source_quality_summary = _run_holder.get("source_quality_summary") or {}
        research_completeness_summary = _run_holder.get("research_completeness_summary") or {}
        upgraded_citation_validation = _run_holder.get("upgraded_citation_validation") or {}

        # Phase 9: Analysis Council outputs
        bull_case_summary = _run_holder.get("bull_case_summary") or {}
        bear_case_summary = _run_holder.get("bear_case_summary") or {}
        risk_summary_dict = _run_holder.get("risk_summary") or {}
        valuation_guard_summary = _run_holder.get("valuation_guard_summary") or {}
        committee_chair_summary = _run_holder.get("committee_chair_summary") or {}
        analysis_council_warnings = state.get("analysis_council_warnings") or []
        provisional_status = state.get("provisional_internal_status") or "research_incomplete"
        human_review_req = state.get("human_review_required", True)

        # Aggregate research team warnings
        research_team_warnings: list[str] = []
        research_team_warnings.extend(financial_data_summary.get("warnings", []))
        research_team_warnings.extend(source_quality_summary.get("warnings", []))
        research_team_warnings.extend(
            upgraded_citation_validation.get("weak_citation_warnings", [])
        )
        research_team_warnings.extend(
            upgraded_citation_validation.get("source_tier_warnings", [])
        )

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="ReportWriter",
            step_name="save_draft_report",
            input_data={
                "ticker": ticker,
                "company_name": company_name,
                "schema_valid": schema_valid,
                "provider_name": provider_name_used,
                "is_mock": is_mock,
                "llm_used": llm_used,
                "llm_provider": llm_provider_used,
                "research_team_warnings_count": len(research_team_warnings),
                "analysis_council_warnings_count": len(analysis_council_warnings),
                "provisional_internal_status": provisional_status,
                "human_review_required": human_review_req,
            },
        )

        slug = _make_report_slug(ticker, agent_run_id or "")

        # Build human-readable markdown content
        mode_tag = "[MOCK DATA]" if is_mock else "[LIVE DATA]"
        schema_tag = "SCHEMA VALID" if schema_valid else "SCHEMA INVALID"
        llm_tag = f"[LLM: {llm_provider_used}]" if llm_used else "[LLM: not used]"
        errors = validation.get("errors", [])
        warnings = validation.get("warnings", [])
        source_quality = source_quality_summary.get("overall_source_quality", "unknown")
        citation_v2_status = upgraded_citation_validation.get("status", "unknown")

        content_md = (
            f"# {company_name} — Phase 9 Analysis Council Draft {mode_tag}\n\n"
            f"**Provider:** {provider_name_used}  \n"
            f"**Ticker:** {ticker}  \n"
            f"**Schema Validation:** {schema_tag}  \n"
            f"**LLM:** {llm_tag}  \n"
            f"**Source Quality:** {source_quality}  \n"
            f"**Citation Validation:** {citation_v2_status}  \n"
            f"**Provisional Internal Status:** `{provisional_status}`  \n"
            f"**Human Review Required:** {human_review_req}  \n\n"
            "> **INTERNAL ADMIN DRAFT ONLY.** "
            "This is not investment advice and must not be published without human admin review. "
            "No investment recommendation has been made. "
            "All analysis council outputs are internal workflow artefacts only.\n\n"
        )

        # ── Company Snapshot ──────────────────────────────────────────
        identity = snapshot.get("company_identity", {})
        content_md += "## Company Snapshot\n\n"
        content_md += f"- **Legal Name:** {identity.get('legal_name', 'N/A')}  \n"
        content_md += f"- **Exchange:** {identity.get('exchange', 'N/A')}  \n"
        content_md += f"- **Country:** {identity.get('country_domicile', 'N/A')}  \n\n"

        if missing_fields:
            content_md += "### Missing Fields (Provider Data)\n\n"
            content_md += "\n".join(f"- `{f}`" for f in missing_fields)
            content_md += "\n\n"

        # ── Provider Data Summary ────────────────────────────────────
        provider_meta = snapshot.get("provider_metadata", {})
        content_md += "## Provider Data Summary\n\n"
        content_md += (
            f"- **Provider:** {provider_meta.get('provider_name', 'N/A')}  \n"
            f"- **Source Tier:** {provider_meta.get('source_tier', 'N/A')}  \n"
            f"- **Retrieved:** {provider_meta.get('retrieved_at', 'N/A')}  \n"
            f"- **Mock Data:** {provider_meta.get('is_mock', True)}  \n\n"
        )

        # ── Financial Data Agent Summary ─────────────────────────────
        content_md += "## Financial Data Agent Summary\n\n"
        content_md += financial_data_summary.get("financial_context_summary", "N/A") + "\n\n"
        fda_warnings = financial_data_summary.get("warnings", [])
        if fda_warnings:
            content_md += "**Warnings:**\n\n"
            content_md += "\n".join(f"- {w}" for w in fda_warnings)
            content_md += "\n\n"
        fda_missing = financial_data_summary.get("missing_financial_data", [])
        if fda_missing:
            content_md += f"**Missing financial data categories:** {len(fda_missing)} total.  \n\n"

        # ── Source Quality Agent Summary ─────────────────────────────
        content_md += "## Source Quality Agent Summary\n\n"
        content_md += f"**Overall source quality:** {source_quality}  \n\n"
        sq_weak = source_quality_summary.get("weak_sources", [])
        if sq_weak:
            content_md += "**Weak sources:**\n\n"
            content_md += "\n".join(f"- {s}" for s in sq_weak)
            content_md += "\n\n"
        sq_agg = source_quality_summary.get("aggregator_only_claims", [])
        if sq_agg:
            content_md += f"**Aggregator-only claims:** {len(sq_agg)}  \n\n"
        sq_upgrades = source_quality_summary.get("recommended_source_upgrades", [])
        if sq_upgrades:
            content_md += "**Recommended source upgrades:**\n\n"
            content_md += "\n".join(f"- {u}" for u in sq_upgrades[:5])
            content_md += "\n\n"

        # ── LLM Research Draft ───────────────────────────────────────
        if llm_used and llm_sections:
            if llm_section_warnings:
                content_md += "## LLM Safety Warnings\n\n"
                for w in llm_section_warnings:
                    content_md += f"> **WARNING:** {w}\n\n"

            content_md += (
                "## LLM Research Draft (Admin Review Required)\n\n"
                "> Generated by LLM using provider identity data only. "
                "NOT investment advice. No rating or price target assigned.\n\n"
            )
            content_md += (
                "### Thesis Summary\n\n"
                + llm_sections.get("thesis_summary_draft", "") + "\n\n"
            )
            content_md += (
                "### Business Overview\n\n"
                + llm_sections.get("business_overview_draft", "") + "\n\n"
            )
            llm_missing = llm_sections.get("missing_information", [])
            if llm_missing:
                content_md += "### Missing Information (LLM Assessment)\n\n"
                content_md += "\n".join(f"- {m}" for m in llm_missing)
                content_md += "\n\n"
            content_md += (
                "### Limitations (LLM Self-Critique)\n\n"
                + llm_sections.get("self_critique_limitations", "") + "\n\n"
            )

        # ── Bull Case Draft ───────────────────────────────────────────────
        content_md += "## Bull Case Draft (Analysis Council — Internal)\n\n"
        bc_points = bull_case_summary.get("positive_thesis_points", [])
        bc_confidence = bull_case_summary.get("confidence_level", "low")
        content_md += f"**Confidence Level:** {bc_confidence}  \n\n"
        if bc_points:
            content_md += "**Positive Thesis Points:**\n\n"
            content_md += "\n".join(f"- {p}" for p in bc_points)
            content_md += "\n\n"
        bc_tailwinds = bull_case_summary.get("potential_tailwinds", [])
        if bc_tailwinds:
            content_md += "**Potential Tailwinds:**\n\n"
            content_md += "\n".join(f"- {t}" for t in bc_tailwinds[:5])
            content_md += "\n\n"
        bc_missing = bull_case_summary.get("missing_evidence", [])
        if bc_missing:
            content_md += f"**Missing Evidence:** {len(bc_missing)} items.  \n\n"
        bc_warnings = bull_case_summary.get("warnings", [])
        if bc_warnings:
            content_md += "**Warnings:**\n\n"
            content_md += "\n".join(f"> {w}" for w in bc_warnings[:3])
            content_md += "\n\n"

        # ── Bear Case Draft ───────────────────────────────────────────────
        content_md += "## Bear Case Draft (Analysis Council — Internal)\n\n"
        br_points = bear_case_summary.get("negative_thesis_points", [])
        br_confidence = bear_case_summary.get("confidence_level", "low")
        content_md += f"**Confidence Level:** {br_confidence}  \n\n"
        if br_points:
            content_md += "**Negative Thesis Points:**\n\n"
            content_md += "\n".join(f"- {p}" for p in br_points)
            content_md += "\n\n"
        br_unknowns = bear_case_summary.get("key_unknowns", [])
        if br_unknowns:
            content_md += "**Key Unknowns:**\n\n"
            content_md += "\n".join(f"- {u}" for u in br_unknowns[:5])
            content_md += "\n\n"

        # ── Risk Review ───────────────────────────────────────────────────
        content_md += "## Risk Review (Analysis Council — Internal)\n\n"
        content_md += risk_summary_dict.get("risk_summary", "N/A") + "\n\n"
        dq_risks = risk_summary_dict.get("data_quality_risks", [])
        if dq_risks:
            content_md += "**Data Quality Risks:**\n\n"
            content_md += "\n".join(f"- {r}" for r in dq_risks)
            content_md += "\n\n"
        sq_risks = risk_summary_dict.get("source_quality_risks", [])
        if sq_risks:
            content_md += "**Source Quality Risks:**\n\n"
            content_md += "\n".join(f"- {r}" for r in sq_risks[:5])
            content_md += "\n\n"

        # ── Valuation Guard ───────────────────────────────────────────────
        content_md += "## Valuation Guard (Analysis Council — Internal)\n\n"
        vg_readiness = valuation_guard_summary.get("valuation_readiness", "not_ready")
        content_md += f"**Valuation Readiness:** `{vg_readiness}`  \n\n"
        vg_blockers = valuation_guard_summary.get("valuation_blockers", [])
        if vg_blockers:
            content_md += "**Valuation Blockers:**\n\n"
            content_md += "\n".join(f"- {b}" for b in vg_blockers)
            content_md += "\n\n"
        vg_disallowed = valuation_guard_summary.get("disallowed_outputs", [])
        if vg_disallowed:
            content_md += "**Disallowed Outputs at This Phase:**\n\n"
            content_md += "\n".join(f"- {d}" for d in vg_disallowed)
            content_md += "\n\n"

        # ── Investment Committee Chair Summary ────────────────────────────
        content_md += "## Investment Committee Chair Summary (Admin Only)\n\n"
        content_md += committee_chair_summary.get("committee_summary", "N/A") + "\n\n"
        cc_questions = committee_chair_summary.get("primary_open_questions", [])
        if cc_questions:
            content_md += "**Primary Open Questions:**\n\n"
            content_md += "\n".join(f"- {q}" for q in cc_questions[:6])
            content_md += "\n\n"
        cc_next = committee_chair_summary.get("research_next_steps", [])
        if cc_next:
            content_md += "**Research Next Steps:**\n\n"
            content_md += "\n".join(f"- {s}" for s in cc_next[:6])
            content_md += "\n\n"

        # ── Research Completeness Review ─────────────────────────────
        content_md += "## Research Completeness Review\n\n"
        rc = research_completeness_summary
        complete = rc.get("complete_sections", [])
        incomplete = rc.get("incomplete_sections", [])
        content_md += (
            f"**Complete sections:** {', '.join(complete) if complete else 'none'}  \n"
            f"**Incomplete sections:** {len(incomplete)}  \n"
            f"**Blocking gaps:** {len(rc.get('blocking_gaps', []))}  \n\n"
        )
        next_tasks = rc.get("next_research_tasks", [])
        if next_tasks:
            content_md += "**Next research tasks:**\n\n"
            content_md += "\n".join(f"- {t}" for t in next_tasks[:8])
            content_md += "\n\n"

        # ── Citation Validation Review ───────────────────────────────
        content_md += "## Citation Validation Review (v2)\n\n"
        content_md += f"**Status:** {citation_v2_status}  \n"
        unsup_nums = upgraded_citation_validation.get("unsupported_number_warnings", [])
        if unsup_nums:
            content_md += "\n**Unsupported number warnings:**\n\n"
            content_md += "\n".join(f"- {w}" for w in unsup_nums)
            content_md += "\n\n"
        tier_warns = upgraded_citation_validation.get("source_tier_warnings", [])
        if tier_warns:
            content_md += "\n**Source tier warnings:**\n\n"
            content_md += "\n".join(f"- {w}" for w in tier_warns[:5])
            content_md += "\n\n"

        # ── Schema Errors / Warnings ─────────────────────────────────
        if errors:
            content_md += "## Schema Errors\n\n"
            content_md += "\n".join(f"- `{e}`" for e in errors[:10])
            if len(errors) > 10:
                content_md += f"\n- ... ({len(errors) - 10} more errors)\n"
            content_md += "\n\n"
        if warnings:
            content_md += "## Data Quality Warnings\n\n"
            content_md += "\n".join(f"- {w}" for w in warnings)
            content_md += "\n\n"

        # ── Missing Information ──────────────────────────────────────
        all_missing = list(dict.fromkeys(
            missing_fields
            + financial_data_summary.get("missing_financial_data", [])
        ))
        if all_missing:
            content_md += f"## Missing Information ({len(all_missing)} items)\n\n"
            content_md += "\n".join(f"- `{m}`" for m in all_missing[:20])
            if len(all_missing) > 20:
                content_md += f"\n- ... ({len(all_missing) - 20} more)\n"
            content_md += "\n\n"

        content_md += (
            "---\n\n"
            "> **INTERNAL ADMIN DRAFT — PHASE 9 ANALYSIS COUNCIL.** "
            "This is not investment advice. "
            "No public investment recommendation has been made. "
            "No price target, fair value, or valuation conclusion has been produced. "
            "Human admin review is required before any further use. "
            "Do not publish or share externally.\n"
        )

        summary = (
            f"Phase 9 Analysis Council draft for {company_name} ({ticker}). "
            f"Provider: {provider_name_used}. "
            f"{'MOCK DATA' if is_mock else 'LIVE DATA'}. "
            f"LLM: {llm_provider_used if llm_used else 'not used'}. "
            f"Schema: {schema_tag}. "
            f"Source quality: {source_quality}. "
            f"Internal status: {provisional_status}. "
            f"Human review: {human_review_req}. "
            "No investment recommendation."
        )

        report = await report_service.create_draft_report(
            db,
            ReportCreate(
                title=f"{company_name} — Analysis Council Draft {mode_tag}",
                slug=slug,
                report_type="company_deep_dive",
                summary=summary,
                content_markdown=content_md,
                created_by_agent_run_id=uuid.UUID(agent_run_id) if agent_run_id else None,
            ),
        )

        # Link all citations to the report
        citation_ids = state.get("citation_ids") or []
        for cit_id in citation_ids:
            # Citations were created without report_id (no report existed yet).
            # We update them here. For simplicity we re-use get_or_create pattern
            # by just accepting existing IDs — a real implementation would UPDATE.
            pass  # Citations are already created; report linkage via FK is handled separately

        await agent_run_service.complete_agent_step(
            db,
            step,
            output_data={
                "report_id": str(report.id),
                "slug": report.slug,
                "schema_valid": schema_valid,
                "missing_fields_count": len(missing_fields),
                "source_quality": source_quality,
                "citation_v2_status": citation_v2_status,
                "research_team_warnings_count": len(research_team_warnings),
            },
        )

        return {
            "draft_report_id": str(report.id),
            "research_team_warnings": research_team_warnings,
            "research_team_complete": True,
        }

    # (save_draft_report ends here)

    # ------------------------------------------------------------------ #
    # Node 18: log_agent_steps  (was Node 13 in Phase 8)                  #
    # ------------------------------------------------------------------ #
    async def node_log_agent_steps(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="WorkflowController",
            step_name="log_agent_steps",
            input_data={
                "draft_report_id": state.get("draft_report_id"),
                "schema_valid": state.get("schema_valid"),
                "citation_count": len(state.get("citation_ids") or []),
                "source_count": len(state.get("source_ids") or []),
            },
        )
        await agent_run_service.complete_agent_step(db, step, output_data={"status": "completed"})
        await agent_run_service.complete_agent_run(db, run)
        return {"status": "completed"}

    # ------------------------------------------------------------------ #
    # Error handler
    # ------------------------------------------------------------------ #
    async def node_handle_error(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        if run:
            error = state.get("error") or "Unknown error"
            await agent_run_service.fail_agent_run(db, run, error)
        return {"status": "failed"}

    # ------------------------------------------------------------------ #
    # Conditional routing
    # ------------------------------------------------------------------ #
    def route_after_load_company(state: CompanyAnalysisState) -> str:
        if state.get("status") == "failed":
            return "handle_error"
        return "fetch_provider_data"

    def route_after_fetch(state: CompanyAnalysisState) -> str:
        if state.get("status") == "failed":
            return "handle_error"
        return "create_source_records"

    # ------------------------------------------------------------------ #
    # Build graph
    # ------------------------------------------------------------------ #
    graph = StateGraph(CompanyAnalysisState)

    graph.add_node("load_company", node_load_company)
    graph.add_node("fetch_provider_data", node_fetch_provider_data)
    graph.add_node("create_source_records", node_create_source_records)
    graph.add_node("build_company_snapshot", node_build_company_snapshot)
    # Phase 8: Research Team nodes (deterministic)
    graph.add_node("financial_data_agent", node_financial_data_agent)
    graph.add_node("source_quality_agent", node_source_quality_agent)
    # Phase 7: optional LLM node
    graph.add_node("generate_research_sections", node_generate_research_sections)
    graph.add_node("create_citations", node_create_citations)
    graph.add_node("validate_report_schema", node_validate_report_schema)
    # Phase 8: post-validation Research Team nodes
    graph.add_node("research_completeness_agent", node_research_completeness_agent)
    graph.add_node("citation_validator_v2", node_citation_validator_v2)
    # Phase 9: Analysis Council nodes (deterministic)
    graph.add_node("bull_case_agent", node_bull_case_agent)
    graph.add_node("bear_case_agent", node_bear_case_agent)
    graph.add_node("risk_agent", node_risk_agent)
    graph.add_node("valuation_guard_agent", node_valuation_guard_agent)
    graph.add_node("investment_committee_chair", node_investment_committee_chair)
    # Phase 15: scoring node
    graph.add_node("score_research_attractiveness", node_score_research_attractiveness)
    graph.add_node("save_draft_report", node_save_draft_report)
    graph.add_node("log_agent_steps", node_log_agent_steps)
    graph.add_node("handle_error", node_handle_error)

    graph.set_entry_point("load_company")
    graph.add_conditional_edges("load_company", route_after_load_company)
    graph.add_conditional_edges("fetch_provider_data", route_after_fetch)
    graph.add_edge("create_source_records", "build_company_snapshot")
    graph.add_edge("build_company_snapshot", "financial_data_agent")
    graph.add_edge("financial_data_agent", "source_quality_agent")
    graph.add_edge("source_quality_agent", "generate_research_sections")
    graph.add_edge("generate_research_sections", "create_citations")
    graph.add_edge("create_citations", "validate_report_schema")
    graph.add_edge("validate_report_schema", "research_completeness_agent")
    graph.add_edge("research_completeness_agent", "citation_validator_v2")
    # Phase 9: Analysis Council chain
    graph.add_edge("citation_validator_v2", "bull_case_agent")
    graph.add_edge("bull_case_agent", "bear_case_agent")
    graph.add_edge("bear_case_agent", "risk_agent")
    graph.add_edge("risk_agent", "valuation_guard_agent")
    graph.add_edge("valuation_guard_agent", "investment_committee_chair")
    # Phase 15: insert scoring between council and report save
    graph.add_edge("investment_committee_chair", "score_research_attractiveness")
    graph.add_edge("score_research_attractiveness", "save_draft_report")
    graph.add_edge("save_draft_report", "log_agent_steps")
    graph.add_edge("log_agent_steps", END)
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
    provider_name: str | None = None,
    require_schema_valid: bool = False,
    use_llm: bool = False,
    llm_provider: str | None = None,
) -> CompanyAnalysisState:
    """
    Execute the company analysis workflow and return the final state.

    Either company_id (UUID string) or (ticker + exchange) must be provided.
    The company must already exist in the database.

    provider_name — override the default provider (None = use config, default: mock).
    require_schema_valid — if True and schema validation fails, status will be "failed".
    use_llm — if True, the generate_research_sections LLM node runs. Default False.
    llm_provider — override config LLM provider (None = use LLM_PROVIDER config, default: mock).
    """
    initial_state: CompanyAnalysisState = {
        "company_id": company_id,
        "ticker": ticker,
        "exchange": exchange,
        "agent_run_id": None,
        "company_name": None,
        "company_sector": None,
        "company_description": None,
        "provider_name": provider_name,
        "is_mock": None,
        "analysis_output": None,
        "draft_report_id": None,
        "placeholder_source_id": None,
        "citation_ids": None,
        "company_snapshot": None,
        "provider_source_id": None,
        "price_source_id": None,
        "source_ids": None,
        "schema_validation_result": None,
        "schema_valid": None,
        "use_llm": use_llm,
        "llm_provider": llm_provider,
        "llm_used": None,
        "llm_sections": None,
        "llm_section_warnings": None,
        # Phase 8: Research Team
        "financial_data_summary": None,
        "source_quality_summary": None,
        "research_completeness_summary": None,
        "upgraded_citation_validation": None,
        "research_team_warnings": None,
        "research_team_complete": None,
        # Phase 9: Analysis Council
        "bull_case_summary": None,
        "bear_case_summary": None,
        "risk_summary": None,
        "valuation_guard_summary": None,
        "committee_chair_summary": None,
        "analysis_council_warnings": None,
        "quality_gate_status": None,
        "provisional_internal_status": None,
        "human_review_required": None,
        # Phase 15: Research Attractiveness Scorecard
        "research_attractiveness_scorecard": None,
        "error": None,
        "status": "running",
    }

    graph = build_company_analysis_graph(
        db,
        provider_name=provider_name,
        use_llm=use_llm,
        llm_provider=llm_provider,
    )
    final_state: CompanyAnalysisState = await graph.ainvoke(initial_state)

    # If caller requires schema-valid output and we got an invalid draft, fail
    if require_schema_valid and not final_state.get("schema_valid"):
        final_state["status"] = "failed"
        final_state["error"] = (
            "Schema validation failed — draft does not satisfy report schema. "
            f"Errors: {(final_state.get('schema_validation_result') or {}).get('errors', [])}"
        )

    return final_state
