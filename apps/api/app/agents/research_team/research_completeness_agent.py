"""
ResearchCompletenessAgent — Phase 8 Research Team.

Compares the current report draft against the real-asset equity report schema
and identifies which sections are complete, incomplete, or missing entirely.
Produces a structured list of next research tasks.

Schema-driven and deterministic — no LLM calls.
Does not fake missing sections.
Does not reduce schema strictness.
schema_valid=false is acceptable at this phase (many sections require LLM agents).
No investment recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass

# Top-level sections defined in report_schema.json
# Grouped by whether they are available from the Phase 8 snapshot or require
# deeper research (filings, LLM analysis, peer data, etc.)
_SCHEMA_SECTIONS = {
    "report_meta": {
        "required": True,
        "description": "Report metadata: schema_version, report_id, generated_at, "
                       "candidate_emerged_from, core_target_profile, theme_tags, conviction",
        "phase": "snapshot",
        "fields": ["schema_version", "report_id", "generated_at",
                   "candidate_emerged_from", "core_target_profile", "theme_tags", "conviction"],
    },
    "identity": {
        "required": True,
        "description": "Company identity datapoints: legal_name, ticker, exchange, "
                       "country_domicile, isin, lei, sector_classification",
        "phase": "snapshot",
        "fields": ["legal_name", "ticker", "exchange", "country_domicile",
                   "isin", "lei", "sector_classification"],
    },
    "discovery_profile": {
        "required": False,
        "description": "How this candidate was found: entry_path, supply_chain_distance, "
                       "coverage_metrics, event_trigger",
        "phase": "research",
        "fields": ["entry_path", "supply_chain_distance_from_obvious",
                   "coverage_metrics", "event_trigger"],
    },
    "snapshot_financials": {
        "required": True,
        "description": "Financial snapshot: market_cap, enterprise_value, revenue, "
                       "ebitda, net_income, total_debt, cash",
        "phase": "financials",
        "fields": ["market_cap", "enterprise_value", "revenue",
                   "ebitda", "net_income", "total_debt", "cash"],
    },
    "financials_deep": {
        "required": False,
        "description": "Detailed financial analysis: revenue_growth, margins, "
                       "capex, fcf, working_capital, balance_sheet_strength",
        "phase": "financials",
        "fields": ["revenue_growth", "gross_margin", "ebitda_margin",
                   "capex_intensity", "free_cash_flow", "net_debt_ebitda"],
    },
    "business_quality": {
        "required": False,
        "description": "Business quality assessment: moat, customer_concentration, "
                       "pricing_power, contract_backlog",
        "phase": "analysis",
        "fields": ["moat_assessment", "customer_concentration",
                   "pricing_power", "contract_backlog"],
    },
    "industry_context": {
        "required": False,
        "description": "Industry and market context: addressable_market, "
                       "competitive_dynamics, regulatory_environment",
        "phase": "research",
        "fields": ["addressable_market", "competitive_dynamics",
                   "regulatory_environment", "supply_chain_position"],
    },
    "scoring": {
        "required": False,
        "description": "Scoring rubric: composite_score, pillar scores "
                       "(financial_strength, underresearched_edge, etc.)",
        "phase": "analysis",
        "fields": ["composite_score", "financial_strength_score",
                   "underresearched_edge_score", "catalyst_quality_score"],
    },
    "self_critique": {
        "required": True,
        "description": "Mandatory self-critique: strongest_bear_case, "
                       "weakest_links_in_thesis, data_quality_warnings, "
                       "confirmation_bias_check, uncited_claim_scan_passed",
        "phase": "analysis",
        "fields": ["strongest_bear_case", "weakest_links_in_thesis",
                   "data_quality_warnings", "confirmation_bias_check",
                   "uncited_claim_scan_passed"],
    },
}

# Research tasks that unlock each incomplete phase
_PHASE_TASKS = {
    "snapshot": [
        "Verify legal entity via GLEIF (obtain LEI)",
        "Confirm ISIN from exchange listing or regulatory data",
        "Cross-check company name and domicile against SEC EDGAR or SEDAR+",
    ],
    "research": [
        "Source discovery profile: document how this candidate was identified",
        "Map supply-chain position: distance from obvious thematic name",
        "Quantify analyst coverage: sell-side estimate count, news volume",
        "Identify event trigger: insider buy, permit grant, contract award",
        "Build industry context: addressable market, competitive dynamics",
    ],
    "financials": [
        "Source latest annual report (T1) for revenue, EBITDA, net income",
        "Obtain balance sheet data: total debt, cash, net debt",
        "Compute enterprise value from market cap + net debt",
        "Build financial deep-dive: growth rates, margins, FCF, capex",
        "Source peer group multiples for relative valuation context",
    ],
    "analysis": [
        "Run business quality assessment: moat, customer concentration, pricing power",
        "Complete scoring rubric: assign scores with rationale and key evidence",
        "Write mandatory self-critique: bear case, weakest links, bias check",
        "Run uncited claim scan — set uncited_claim_scan_passed=true only if clean",
    ],
}


@dataclass
class ResearchCompletenessAgentOutput:
    """Structured output from the ResearchCompletenessAgent."""

    complete_sections: list[str]
    incomplete_sections: list[str]
    missing_required_fields: list[str]
    next_research_tasks: list[str]
    blocking_gaps: list[str]
    non_blocking_gaps: list[str]


def run_research_completeness_agent(
    company_snapshot: dict,
    schema_draft: dict | None = None,
    schema_validation_errors: list[str] | None = None,
) -> ResearchCompletenessAgentOutput:
    """
    Compare snapshot and draft against the report schema; identify gaps.

    Args:
        company_snapshot: dict from build_company_snapshot().
        schema_draft: the partial schema draft dict (may be None).
        schema_validation_errors: error list from validate_real_asset_report().

    Returns:
        ResearchCompletenessAgentOutput — always returns, never raises.
    """
    draft = schema_draft or {}
    errors = set(schema_validation_errors or [])

    complete_sections: list[str] = []
    incomplete_sections: list[str] = []
    missing_required_fields: list[str] = []
    next_tasks: list[str] = []
    blocking_gaps: list[str] = []
    non_blocking_gaps: list[str] = []

    phases_needed: set[str] = set()

    for section_key, meta in _SCHEMA_SECTIONS.items():
        in_draft = section_key in draft
        is_required = meta["required"]
        phase = meta["phase"]

        if in_draft:
            # Section present — check sub-fields
            section_data = draft[section_key]
            sub_fields = meta["fields"]
            absent = [f for f in sub_fields if f not in section_data]

            if absent:
                incomplete_sections.append(section_key)
                phases_needed.add(phase)
                for f in absent:
                    entry = f"{section_key}.{f}"
                    if is_required:
                        missing_required_fields.append(entry)
                        blocking_gaps.append(
                            f"Required field missing: {entry}"
                        )
                    else:
                        non_blocking_gaps.append(
                            f"Optional field absent: {entry}"
                        )
            else:
                complete_sections.append(section_key)
        else:
            # Section not in draft at all
            incomplete_sections.append(section_key)
            phases_needed.add(phase)
            all_fields = meta["fields"]
            for f in all_fields:
                entry = f"{section_key}.{f}"
                if is_required:
                    missing_required_fields.append(entry)
                    blocking_gaps.append(
                        f"Required section absent: {section_key} (field: {entry})"
                    )
                else:
                    non_blocking_gaps.append(
                        f"Optional section absent: {section_key}"
                    )
            # De-duplicate non_blocking_gaps for whole-section absence
            non_blocking_gaps = list(dict.fromkeys(non_blocking_gaps))

    # Collect next tasks from phases needed
    for phase in ("snapshot", "research", "financials", "analysis"):
        if phase in phases_needed:
            next_tasks.extend(_PHASE_TASKS[phase])

    # Surface schema validation errors as blocking gaps if not already captured
    for err in errors:
        err_short = err[:200] if len(err) > 200 else err
        if not any(err_short in g for g in blocking_gaps):
            blocking_gaps.append(f"Schema validation error: {err_short}")

    # De-duplicate
    next_tasks = list(dict.fromkeys(next_tasks))
    blocking_gaps = list(dict.fromkeys(blocking_gaps))
    non_blocking_gaps = list(dict.fromkeys(non_blocking_gaps))

    return ResearchCompletenessAgentOutput(
        complete_sections=complete_sections,
        incomplete_sections=incomplete_sections,
        missing_required_fields=missing_required_fields,
        next_research_tasks=next_tasks,
        blocking_gaps=blocking_gaps,
        non_blocking_gaps=non_blocking_gaps,
    )


def research_completeness_output_to_dict(output: ResearchCompletenessAgentOutput) -> dict:
    """Serialize output to a plain dict suitable for JSON storage."""
    return {
        "complete_sections": output.complete_sections,
        "incomplete_sections": output.incomplete_sections,
        "missing_required_fields": output.missing_required_fields,
        "next_research_tasks": output.next_research_tasks,
        "blocking_gaps": output.blocking_gaps,
        "non_blocking_gaps": output.non_blocking_gaps,
    }
