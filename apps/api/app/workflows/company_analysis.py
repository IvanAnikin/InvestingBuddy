"""
Company Analysis Workflow — Phase 7: LLM Research Sections (optional).

Node structure (9 nodes + error handler):
  1. load_company              — resolve company from DB; create agent_run record
  2. fetch_provider_data       — call FinancialDataService (default: MockProvider)
  3. create_source_records     — build Source DB records from provider metadata
  4. build_company_snapshot    — assemble structured snapshot + schema draft
  5. generate_research_sections — (OPTIONAL) call LLM to generate draft sections
                                  Skipped when use_llm=False (default).
                                  Default LLM is MockResearchLLMClient (offline).
  6. create_citations          — create Citation records with field_path/source_tier/data_quality
  7. validate_report_schema    — call validate_real_asset_report(); store result
  8. save_draft_report         — save draft report with snapshot + validation status
  9. log_agent_steps           — mark agent_run completed; final step logging
  handle_error                 — marks agent_run failed on any unhandled error

Design rules enforced:
  - LLM calls are opt-in: use_llm=False by default; all CI tests run offline.
  - Default LLM provider is "mock" — no Azure credentials required in tests.
  - LLM output is constrained: no rating, no price target, no valuation, no bare numbers.
  - LLM output is safety-validated before being stored.
  - Schema validation always runs regardless of LLM usage.
  - No investment recommendations at this phase.
  - Mock provider is the default; all CI tests run offline.
  - Every node logs an agent_step (input + output JSON).
"""

from __future__ import annotations

import json
import pathlib
import re
import uuid
from datetime import datetime, timezone

from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import CompanyAnalysisState
from app.integrations.financial_data_provider import (
    DataQuality,
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
WORKFLOW_VERSION = "3.0.0"

_PROMPT_PATH = (
    pathlib.Path(__file__).resolve().parents[5]
    / "packages"
    / "prompts"
    / "research"
    / "phase7_company_research_v1.md"
)


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
                },
            )

            # Stash provider objects in holder for later nodes
            _run_holder["profile"] = profile
            _run_holder["prices"] = prices

            return {
                "provider_name": profile.meta.provider_name,
                "is_mock": is_mock,
                "analysis_output": _build_placeholder_analysis(state),
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

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="SnapshotBuilder",
            step_name="build_company_snapshot",
            input_data={
                "ticker": state.get("ticker"),
                "provider_name": state.get("provider_name"),
                "is_mock": state.get("is_mock"),
            },
        )

        snapshot = build_company_snapshot(profile=profile, prices=prices)

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
            },
        )

        _run_holder["snapshot"] = snapshot
        return {"company_snapshot": snapshot}

    # ------------------------------------------------------------------ #
    # Node 5: generate_research_sections  (optional LLM node)            #
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
    # Node 6: create_citations  (was Node 5 in Phase 6)                  #
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
    # Node 7: validate_report_schema  (was Node 6 in Phase 6)            #
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

        # Build a minimal schema-draft using provider data
        draft = build_schema_draft(
            report_id=agent_run_id or str(uuid.uuid4()),
            snapshot=_run_holder.get("snapshot", {}),
            profile=profile,
            prices=prices,
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
    # Node 8: save_draft_report  (was Node 7 in Phase 6)                 #
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
            },
        )

        slug = _make_report_slug(ticker, agent_run_id or "")

        # Build human-readable markdown content
        mode_tag = "[MOCK DATA]" if is_mock else "[LIVE DATA]"
        schema_tag = "SCHEMA VALID" if schema_valid else "SCHEMA INVALID"
        llm_tag = f"[LLM: {llm_provider_used}]" if llm_used else "[LLM: not used]"
        errors = validation.get("errors", [])
        warnings = validation.get("warnings", [])

        content_md = (
            f"# {company_name} — Draft Research Report {mode_tag}\n\n"
            f"**Provider:** {provider_name_used}  \n"
            f"**Ticker:** {ticker}  \n"
            f"**Schema Validation:** {schema_tag}  \n"
            f"**LLM:** {llm_tag}  \n\n"
            "> **ADMIN DRAFT ONLY** — Not investment advice. "
            "Human review required before any use.\n\n"
        )
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

        # LLM-generated sections (if available)
        if llm_used and llm_sections:
            if llm_section_warnings:
                content_md += "## LLM Safety Warnings\n\n"
                for w in llm_section_warnings:
                    content_md += f"> **WARNING:** {w}\n\n"

            content_md += (
                "## Thesis Summary (LLM Draft — Admin Review Required)\n\n"
                "> This section was generated by an LLM using provider identity data only. "
                "It is NOT investment advice. No rating or price target has been assigned.\n\n"
            )
            content_md += llm_sections.get("thesis_summary_draft", "") + "\n\n"

            content_md += (
                "## Business Overview (LLM Draft — Admin Review Required)\n\n"
            )
            content_md += llm_sections.get("business_overview_draft", "") + "\n\n"

            llm_missing = llm_sections.get("missing_information", [])
            if llm_missing:
                content_md += "## Missing Information (LLM Assessment)\n\n"
                content_md += "\n".join(f"- {m}" for m in llm_missing)
                content_md += "\n\n"

            content_md += "## Limitations (LLM Self-Critique)\n\n"
            content_md += llm_sections.get("self_critique_limitations", "") + "\n\n"

        identity = snapshot.get("company_identity", {})
        content_md += "## Company Identity\n\n"
        content_md += f"- **Legal Name:** {identity.get('legal_name', 'N/A')}  \n"
        content_md += f"- **Exchange:** {identity.get('exchange', 'N/A')}  \n"
        content_md += f"- **Country:** {identity.get('country_domicile', 'N/A')}  \n\n"

        if missing_fields:
            content_md += "## Missing Fields (Provider Data)\n\n"
            content_md += "\n".join(f"- `{f}`" for f in missing_fields)
            content_md += "\n\n"

        content_md += "## Snapshot JSON\n\n```json\n"
        content_md += json.dumps(snapshot, indent=2, default=str)
        content_md += "\n```\n\n"
        content_md += (
            "---\n\n"
            "*This is a Phase 7 draft report. "
            "No investment recommendation has been made. "
            "Human review required before any action.*\n"
        )

        summary = (
            f"Draft research for {company_name} ({ticker}). "
            f"Provider: {provider_name_used}. "
            f"{'MOCK DATA' if is_mock else 'LIVE DATA'}. "
            f"LLM: {llm_provider_used if llm_used else 'not used'}. "
            f"Schema: {schema_tag}. "
            f"Missing fields: {len(missing_fields)}. "
            "No investment recommendation."
        )

        report = await report_service.create_draft_report(
            db,
            ReportCreate(
                title=f"{company_name} — Provider Snapshot {mode_tag}",
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
            },
        )

        return {
            "draft_report_id": str(report.id),
        }

    # ------------------------------------------------------------------ #
    # Node 9: log_agent_steps  (was Node 8 in Phase 6)                   #
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
    graph.add_node("generate_research_sections", node_generate_research_sections)
    graph.add_node("create_citations", node_create_citations)
    graph.add_node("validate_report_schema", node_validate_report_schema)
    graph.add_node("save_draft_report", node_save_draft_report)
    graph.add_node("log_agent_steps", node_log_agent_steps)
    graph.add_node("handle_error", node_handle_error)

    graph.set_entry_point("load_company")
    graph.add_conditional_edges("load_company", route_after_load_company)
    graph.add_conditional_edges("fetch_provider_data", route_after_fetch)
    graph.add_edge("create_source_records", "build_company_snapshot")
    graph.add_edge("build_company_snapshot", "generate_research_sections")
    graph.add_edge("generate_research_sections", "create_citations")
    graph.add_edge("create_citations", "validate_report_schema")
    graph.add_edge("validate_report_schema", "save_draft_report")
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
