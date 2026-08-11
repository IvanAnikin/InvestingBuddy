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

Phase 19.4.1 — enrichment completeness consistency:
  The schema draft is built from the raw provider profile and never carries the
  Phase 19.4 enrichment (LEI/ISIN/sector from GLEIF/SEC, derived market cap / EV
  / P/E / 52-week range from free price + SEC data). Without accounting for that
  enrichment this agent would flag LEI, sector classification, market cap and
  enterprise value as blocking gaps even though the enriched snapshot already
  carries them. ``_enriched_present_fields`` derives, from the enriched company
  snapshot, which schema field entries are already satisfied so that a genuinely
  present field is never reported as a blocking/missing gap. Genuinely absent
  fields (ISIN, EBITDA, …) remain gaps. No data is fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.sources.company_evidence import regulator_connector_for

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

# Phase-32A(fix) — the generic, jurisdiction-agnostic wording for the
# domicile-verification task. For issuers whose exchange/country resolves to a
# known home regulator (see ``_jurisdiction_aware_snapshot_tasks`` below) this
# is swapped for a jurisdiction-appropriate equivalent. US issuers, and any
# issuer whose jurisdiction cannot be resolved, keep this wording unchanged —
# SEC EDGAR is genuinely the right venue for US issuers, and an unresolved
# jurisdiction must never guess at a regulator.
_GENERIC_DOMICILE_TASK = "Cross-check company name and domicile against SEC EDGAR or SEDAR+"

# Research tasks that unlock each incomplete phase
_PHASE_TASKS = {
    "snapshot": [
        "Verify legal entity via GLEIF (obtain LEI)",
        "Confirm ISIN from exchange listing or regulatory data",
        _GENERIC_DOMICILE_TASK,
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


def _first_present(source: dict, *keys: str) -> bool:
    """True when any of ``keys`` resolves to a non-None value in ``source``."""
    return any(source.get(k) is not None for k in keys)


# Phase 29B.3 — parsed primary-fact fields → the schema ``section.field`` they
# satisfy. Only fields that genuinely map onto a schema entry are listed; a
# high-confidence T1 primary-filing fact for one of these satisfies that entry.
_PRIMARY_FACT_SCHEMA_FIELDS: dict[str, str] = {
    "revenue": "snapshot_financials.revenue",
    "net_income": "snapshot_financials.net_income",
    "total_debt": "snapshot_financials.total_debt",
    "cash_and_equivalents": "snapshot_financials.cash",
}


def _primary_fact_present_fields(primary_facts: list[dict] | None) -> set[str]:
    """``section.field`` entries satisfied by real high-confidence T1 primary facts.

    Only a genuine fact counts — high confidence AND carrying its own real
    source_url (a structured datapoint parsed from an actual filing). Mock /
    aggregator-only data never produces such a fact, so it can never mark a field
    satisfied here. With no facts this returns an empty set and nothing changes.
    """
    present: set[str] = set()
    for fact in primary_facts or []:
        if not isinstance(fact, dict):
            continue
        if fact.get("confidence") != "high" or not fact.get("source_url"):
            continue
        field = fact.get("field")
        if not isinstance(field, str):
            continue
        entry = _PRIMARY_FACT_SCHEMA_FIELDS.get(field)
        if entry is not None:
            present.add(entry)
    return present


def _enriched_present_fields(
    company_snapshot: dict,
    primary_facts: list[dict] | None = None,
) -> set[str]:
    """
    Derive the ``section.field`` schema entries already satisfied by the enriched
    company snapshot (Phase 19.4 identity/profile + derived market metrics).

    This lets the completeness agent avoid reporting an enriched field as a
    blocking/missing gap when the snapshot already carries it. Only *present*
    values count — a None never satisfies a field, so genuinely-absent fields
    (ISIN, EBITDA, …) remain gaps and nothing is fabricated.
    """
    present: set[str] = set()
    if not company_snapshot:
        return present

    identity: dict[str, Any] = company_snapshot.get("company_identity") or {}
    profile: dict[str, Any] = company_snapshot.get("profile") or {}
    fundamentals: dict[str, Any] = company_snapshot.get("fundamentals_summary") or {}
    market_metrics: dict[str, Any] = company_snapshot.get("market_metrics_summary") or {}

    # ── identity section ──────────────────────────────────────────────────
    if identity.get("legal_name") is not None:
        present.add("identity.legal_name")
    if identity.get("ticker") is not None:
        present.add("identity.ticker")
    if identity.get("exchange") is not None:
        present.add("identity.exchange")
    if identity.get("country_domicile") is not None:
        present.add("identity.country_domicile")
    if identity.get("isin") is not None:
        present.add("identity.isin")
    if identity.get("lei") is not None:
        present.add("identity.lei")
    # Sector classification is satisfied by a present or mapped/inferred sector.
    if profile.get("sector") is not None:
        present.add("identity.sector_classification")

    # ── snapshot_financials section ───────────────────────────────────────
    # Values may arrive as SEC-normalized fundamentals or as Phase 19.4 derived
    # market metrics; either counts as present. EBITDA is never derived here and
    # therefore remains absent.
    if _first_present(fundamentals, "market_cap_usd_m", "market_cap_mln") or (
        market_metrics.get("market_cap_mln") is not None
    ):
        present.add("snapshot_financials.market_cap")
    if _first_present(fundamentals, "enterprise_value_usd_m", "enterprise_value_mln") or (
        market_metrics.get("enterprise_value_mln") is not None
    ):
        present.add("snapshot_financials.enterprise_value")
    if _first_present(fundamentals, "revenue_usd_m", "revenue_ttm_mln"):
        present.add("snapshot_financials.revenue")
    if _first_present(fundamentals, "ebitda_usd_m", "ebitda_mln"):
        present.add("snapshot_financials.ebitda")
    if fundamentals.get("net_income_usd_m") is not None:
        present.add("snapshot_financials.net_income")
    if _first_present(fundamentals, "total_debt_usd_m"):
        present.add("snapshot_financials.total_debt")
    if _first_present(fundamentals, "cash_and_equivalents_usd_m"):
        present.add("snapshot_financials.cash")

    # Phase 29B.3 — real high-confidence T1 primary-filing facts (revenue, …)
    # satisfy their schema field so a genuinely-sourced field is not reported as
    # a blocking/missing gap. Only genuine facts count (never mock/aggregator).
    present |= _primary_fact_present_fields(primary_facts)

    return present


# Phase-32A(fix) — human-readable display names for the regulator connector ids
# ``regulator_connector_for`` (company_evidence.py) can resolve. Kept minimal
# and scoped to exactly those ids; deliberately a local copy (not an import
# from the connector modules) to avoid coupling this deterministic, no-LLM
# agent to the connector layer's own display strings. No individual company
# name is ever hardcoded — only venue/regulator names.
_REGULATOR_DISPLAY_NAMES: dict[str, str] = {
    "uk_fca_nsm": "the UK FCA National Storage Mechanism (NSM)",
    "euronext_regulated_info": "Euronext Regulated Information / AMF filings",
    "deutsche_boerse": "the German regulated-information venue (Deutsche Börse / Bundesanzeiger)",
    "nordic_disclosures": "Nasdaq Nordic company disclosures",
    "six_swiss": "SIX Swiss Exchange regulatory disclosures",
}


def _jurisdiction_aware_snapshot_tasks(company_snapshot: dict) -> list[str]:
    """Return the ``snapshot`` phase task list with the generic domicile
    cross-check line swapped for a jurisdiction-appropriate one when the
    company's exchange/country resolves to a known home regulator.

    Uses the same ``regulator_connector_for`` resolver the connector layer
    already relies on (company_evidence.py) so the wording never drifts from
    the exchanges/countries the platform actually has a dedicated regulator
    connector for. A US issuer, or any issuer whose exchange/country does not
    resolve to a known regulator, keeps the generic "SEC EDGAR or SEDAR+"
    wording unchanged — an unresolved jurisdiction must never be guessed at.
    """
    tasks = list(_PHASE_TASKS["snapshot"])
    identity: dict[str, Any] = (company_snapshot or {}).get("company_identity") or {}
    exchange = identity.get("exchange")
    country = identity.get("country_domicile")
    if not exchange and not country:
        return tasks

    try:
        connector_id = regulator_connector_for(exchange, country)
    except Exception:
        # Never let a resolution failure block completeness reporting — fall
        # back to the safe, generic wording.
        connector_id = None

    display_name = _REGULATOR_DISPLAY_NAMES.get(connector_id) if connector_id else None
    if not display_name:
        return tasks

    return [
        f"Cross-check company name and domicile against {display_name}"
        if task == _GENERIC_DOMICILE_TASK
        else task
        for task in tasks
    ]


def run_research_completeness_agent(
    company_snapshot: dict,
    schema_draft: dict | None = None,
    schema_validation_errors: list[str] | None = None,
    primary_facts: list[dict] | None = None,
) -> ResearchCompletenessAgentOutput:
    """
    Compare snapshot and draft against the report schema; identify gaps.

    Args:
        company_snapshot: dict from build_company_snapshot().
        schema_draft: the partial schema draft dict (may be None).
        schema_validation_errors: error list from validate_real_asset_report().
        primary_facts: Phase 29B.3 — high-confidence T1 primary-filing facts
            surfaced by the council (each with its own source_url). When present,
            the schema fields they source (e.g. snapshot_financials.revenue) are
            no longer reported as gaps. Defaults to ``None`` (no change).

    Returns:
        ResearchCompletenessAgentOutput — always returns, never raises.
    """
    draft = schema_draft or {}
    errors = set(schema_validation_errors or [])

    # Phase 19.4.1: fields already satisfied by the enriched snapshot must not be
    # reported as blocking/missing gaps even though the schema draft (built from
    # the raw provider profile) does not carry them.
    # Phase 29B.3: genuine T1 primary-filing facts likewise satisfy their field.
    enriched_present = _enriched_present_fields(company_snapshot, primary_facts)

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
        sub_fields = meta["fields"]
        section_data = draft.get(section_key, {}) if in_draft else {}

        # A field counts as present when it is in the draft section OR the
        # enriched snapshot already carries it.
        absent = [
            f for f in sub_fields
            if f not in section_data
            and f"{section_key}.{f}" not in enriched_present
        ]

        if not absent:
            complete_sections.append(section_key)
            continue

        incomplete_sections.append(section_key)
        phases_needed.add(phase)
        # "Whole section absent" wording only applies when nothing (draft or
        # enrichment) satisfies any field of the section.
        section_fully_absent = not section_data and len(absent) == len(sub_fields)
        for f in absent:
            entry = f"{section_key}.{f}"
            if is_required:
                missing_required_fields.append(entry)
                if section_fully_absent:
                    blocking_gaps.append(
                        f"Required section absent: {section_key} (field: {entry})"
                    )
                else:
                    blocking_gaps.append(f"Required field missing: {entry}")
            else:
                if section_fully_absent:
                    non_blocking_gaps.append(f"Optional section absent: {section_key}")
                else:
                    non_blocking_gaps.append(f"Optional field absent: {entry}")
        # De-duplicate non_blocking_gaps for whole-section absence
        non_blocking_gaps = list(dict.fromkeys(non_blocking_gaps))

    # Collect next tasks from phases needed
    for phase in ("snapshot", "research", "financials", "analysis"):
        if phase not in phases_needed:
            continue
        if phase == "snapshot":
            next_tasks.extend(_jurisdiction_aware_snapshot_tasks(company_snapshot))
        else:
            next_tasks.extend(_PHASE_TASKS[phase])

    # Phase 19.4.1: drop identity-verification tasks the enriched snapshot has
    # already satisfied (e.g. do not ask to "obtain LEI" when LEI is present).
    if "identity.lei" in enriched_present:
        next_tasks = [t for t in next_tasks if "lei" not in t.lower()]
    if "identity.isin" in enriched_present:
        next_tasks = [t for t in next_tasks if "isin" not in t.lower()]

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
