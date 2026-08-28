"""
RiskAgent — Phase 9 Analysis Council.

Structures risks into categories relevant for medium-term investing.
Must include data/source-quality risks from Phase 8 Research Team outputs.
Must mark unknowns clearly.

Constraints enforced:
  - No SELL/SHORT recommendation.
  - No price target or fair value.
  - No invented facts.
  - Always returns a result — never raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.integrations.financial_data_provider import normalize_source_tier
from app.schemas.evidence_state import FinancialDataSummary
from app.services.canonical_evidence import resolve_price_provenance
from app.services.final_research_state import (
    FinancialEvidenceState,
    category_labels,
)


@dataclass
class RiskAgentOutput:
    """Structured output from the RiskAgent."""

    business_risks: list[str]
    financial_risks: list[str]
    market_risks: list[str]
    regulatory_geopolitical_risks: list[str]
    data_quality_risks: list[str]
    source_quality_risks: list[str]
    risk_summary: str
    warnings: list[str] = field(default_factory=list)


def _data_quality_label(
    is_mock: bool,
    source_tier: str,
    fin_ev: "FinancialEvidenceState | None",
) -> str:
    """One label that does not overstate OR understate the evidence.

    A single tier cannot describe a report whose identity is T6 and whose
    revenue is T1; stating only the identity tier is what made the risk summary
    read "Data quality: T6_model_estimate" beside a validated filing figure.
    """
    if is_mock:
        return "MOCK (synthetic)"
    if fin_ev is not None and fin_ev.available and fin_ev.best_tier != source_tier:
        return (
            f"identity/price {source_tier}, financial statement facts "
            f"{fin_ev.best_tier}"
        )
    return source_tier


def _incompleteness_clause(fin_ev: "FinancialEvidenceState | None") -> str:
    """Why the assessment is incomplete, in terms of what is ACTUALLY missing.

    Manual-QA corrective: this sentence was the fixed string "Assessment is
    incomplete — primary filings (T1/T2) required before any investment
    decision", printed unchanged on reports that had already ingested a T1
    primary filing and were presenting validated statement facts from it one
    section above. A reader has no way to tell that from a report with no
    primary evidence at all, and the two need completely different work.

    The warning is NOT softened — every branch still says the assessment is
    incomplete and still withholds an investment decision. Only the stated
    REASON changes, to the one that is true. The primary-backed wording reuses
    the same vocabulary as the warnings block above so the two cannot drift.
    """
    if fin_ev is None or not fin_ev.is_primary_backed:
        return (
            "Assessment is incomplete — primary filings (T1/T2) required before "
            "any investment decision."
        )
    remaining = category_labels(fin_ev.open_categories) if fin_ev.open_categories else ""
    if remaining:
        return (
            "Assessment is incomplete — the issuer's own primary filing is "
            f"ingested, but the remaining statement lines ({remaining}) and "
            "identity/regulatory confirmation are still required before any "
            "investment decision."
        )
    return (
        "Assessment is incomplete — the issuer's own primary filing is ingested; "
        "identity/regulatory confirmation is still required before any "
        "investment decision."
    )


def run_risk_agent(
    company_snapshot: dict,
    financial_data_summary: dict,
    source_quality_summary: dict,
    research_completeness_summary: dict,
    upgraded_citation_validation: dict | None = None,
    financial_evidence: "FinancialEvidenceState | None" = None,
) -> RiskAgentOutput:
    """
    Structure risks into categories for medium-term investment analysis.

    Data/source-quality risks from Research Team always included.
    All unknowns are marked explicitly.

    Returns:
        RiskAgentOutput — always returns, never raises.
    """
    warnings: list[str] = []
    business_risks: list[str] = []
    financial_risks: list[str] = []
    market_risks: list[str] = []
    regulatory_geopolitical_risks: list[str] = []
    data_quality_risks: list[str] = []
    source_quality_risks: list[str] = []

    identity = company_snapshot.get("company_identity", {})
    profile = company_snapshot.get("profile", {})
    provider_meta = company_snapshot.get("provider_metadata", {})
    is_mock = company_snapshot.get("is_mock", True)

    ticker = identity.get("ticker") or "N/A"
    legal_name = identity.get("legal_name") or "Unknown"
    sector = profile.get("sector") or "sector not sourced"
    country = (
        profile.get("country_domicile")
        or identity.get("country_domicile")
        or "country not sourced"
    )
    source_tier = (
        normalize_source_tier(provider_meta.get("source_tier")) or "T6_model_estimate"
    )
    fin_ev = financial_evidence

    # ── Business risks ────────────────────────────────────────────────────
    # From research completeness gaps
    blocking_gaps = research_completeness_summary.get("blocking_gaps", [])
    if blocking_gaps:
        business_risks.append(
            f"Research incomplete: {len(blocking_gaps)} blocking gaps in the research "
            "package. Business model, competitive position, and management quality "
            "have not been assessed."
        )

    # Sector-specific business risks
    _sector_business_risks = {
        "energy": [
            "Business model disruption risk from energy transition policy acceleration.",
            "Asset stranding risk for fossil fuel assets in transition scenarios.",
        ],
        "industrials": [
            "Demand cyclicality — industrial revenues typically highly correlated with GDP.",
            "Capacity utilisation and fixed cost leverage exposure.",
        ],
        "materials": [
            "Commodity price dependency — earnings highly sensitive to commodity cycles.",
            "Project execution risk for capital-intensive operations.",
        ],
        "technology": [
            "Product obsolescence risk — technology cycles may shorten.",
            "Key person dependency risk in high-growth technology companies.",
        ],
        "healthcare": [
            "Clinical development risk — pipeline assets may fail trials.",
            "Reimbursement and pricing pressure from payers.",
        ],
        "financials": [
            "Credit quality risk — loan book deterioration in economic downturns.",
            "Liability duration mismatch risk.",
        ],
        "real estate": [
            "Vacancy and rental income risk in downturns.",
            "Development execution and cost overrun risk.",
        ],
    }

    sector_lower = (sector or "").lower()
    for sector_key, risks in _sector_business_risks.items():
        if sector_key in sector_lower:
            business_risks.extend(risks)
            break

    if not [r for r in business_risks if "sector" not in r.lower()]:
        business_risks.append(
            f"UNKNOWN: Business-specific risks for {legal_name} in {sector} "
            "cannot be assessed without company filings and industry research (T1/T3 sources)."
        )

    # ── Financial risks ───────────────────────────────────────────────────
    # Phase B: normalise the payload ONCE at ingress via the typed contract;
    # below this line the code reads attributes, never string keys, so a
    # producer rename fails at the boundary instead of silently reading [].
    _fds = FinancialDataSummary.from_payload(financial_data_summary) or (
        FinancialDataSummary()
    )
    available_financials = [
        f for f in _fds.available_fields if f.startswith("financials.")
    ]
    missing_financials = [
        f for f in _fds.missing_fields if f.startswith("financials.")
    ]
    if missing_financials and available_financials:
        # Partial: statement fundamentals are sourced but valuation inputs are
        # not. Financial data is partial, NOT absent.
        missing_labels = [
            f.split(".", 1)[1].replace("_", " ") for f in missing_financials
        ]
        # Phase 32D2 — name what is actually sourced, at its actual tier.
        sourced_label = (
            category_labels(fin_ev.resolved_categories)
            if fin_ev is not None and fin_ev.resolved_categories
            else ", ".join(
                f.split(".", 1)[1].replace("_", " ") for f in available_financials[:5]
            )
        )
        tier_clause = (
            f" ({fin_ev.best_tier}, {fin_ev.best_source})"
            if fin_ev is not None and fin_ev.available
            else ""
        )
        financial_risks.append(
            f"Financial data is partial: {len(available_financials)} statement categories "
            f"({sourced_label}) are sourced{tier_clause}; "
            f"{len(missing_financials)} valuation inputs remain missing "
            f"({', '.join(missing_labels[:5])}{'...' if len(missing_labels) > 5 else ''}). "
            "Leverage and liquidity can be partially assessed; market-based valuation cannot."
        )
    elif missing_financials:
        financial_risks.append(
            f"UNKNOWN: All {len(missing_financials)} core financial categories missing "
            "(revenue, EBITDA, margins, debt, cash flow). "
            "Balance sheet, leverage, and liquidity risks cannot be assessed."
        )
    else:
        financial_risks.append(
            "Financial fundamentals available — "
            "leverage, liquidity, and profitability assessment possible."
        )

    # Phase 32D2e — ``.get(key, default)`` returns the default only when the key
    # is ABSENT; a key present with value None returns None, which f-strings
    # render as the Python literal. Live reports read "reporting currency is
    # 'None'" beside a T1 revenue figure quoted in DKK.
    reporting_currency = profile.get("reporting_currency") or "not sourced"
    financial_risks.append(
        f"Currency risk: reporting currency is '{reporting_currency}'. "
        "FX exposure to investment base currency is unknown at this phase."
    )

    # ── Market risks ──────────────────────────────────────────────────────
    price = resolve_price_provenance(company_snapshot)
    if price.available:
        market_risks.append(
            f"Price volatility risk: price data available "
            f"({price.data_points_count} data points from "
            f"{price.provider_label}, {price.source_tier}). Volatility, beta, "
            "and correlation to broader market indices not yet computed."
        )
    else:
        market_risks.append(
            "UNKNOWN: No price history — market liquidity, volatility, and "
            "trading characteristics cannot be assessed."
        )

    market_risks.append(
        f"Market depth risk: Exchange is {identity.get('exchange') or 'unknown'}. "
        "Liquidity and bid-ask spread data not sourced."
    )

    _region_market_risks = {
        "norway": "Norwegian small/mid-cap market may have limited liquidity.",
        "sweden": "Stockholm market subject to Nordic economic cycle exposure.",
        "germany": "DAX exposure and European macro cycle dependency.",
        "united kingdom": "UK market subject to GBP FX risk and post-Brexit trade dynamics.",
        "united states": "US equity market correlation and Fed rate sensitivity.",
    }
    country_lower = (country or "").lower()
    for region_key, risk in _region_market_risks.items():
        if region_key in country_lower:
            market_risks.append(f"Regional market risk: {risk}")
            break

    # ── Regulatory / geopolitical risks ──────────────────────────────────
    if not identity.get("lei"):
        regulatory_geopolitical_risks.append(
            "UNKNOWN: LEI (Legal Entity Identifier) not sourced — "
            "regulatory standing and compliance status cannot be verified via GLEIF."
        )

    if not identity.get("isin"):
        regulatory_geopolitical_risks.append(
            "UNKNOWN: ISIN not sourced — exchange listing and regulatory compliance "
            "status cannot be confirmed."
        )

    regulatory_geopolitical_risks.append(
        f"UNKNOWN: Regulatory environment in {country} not yet assessed. "
        "Sector-specific regulatory risks require T2/T3 research."
    )

    _geopolitical_region_risks = {
        "russia": "Geopolitical risk: sanctions exposure and supply chain disruption.",
        "china": "Geopolitical risk: US-China trade dynamics; regulatory intervention risk.",
        "middle east": "Geopolitical risk: regional conflict and energy market volatility.",
        "ukraine": "Geopolitical risk: conflict zone proximity and supply chain disruption.",
    }
    for region, risk in _geopolitical_region_risks.items():
        if region in country_lower:
            regulatory_geopolitical_risks.append(risk)

    # ── Data quality risks (from Phase 8 Research Team) ──────────────────
    fda_warnings = financial_data_summary.get("warnings", [])
    for w in fda_warnings:
        data_quality_risks.append(f"Financial data quality: {w}")

    citation_status = (upgraded_citation_validation or {}).get("status", "unknown")
    if citation_status in ("warnings", "failed"):
        data_quality_risks.append(
            f"Citation validation status: {citation_status} — "
            "some claims in the research package lack adequate citation coverage."
        )

    unsupported_numbers = (upgraded_citation_validation or {}).get(
        "unsupported_number_warnings", []
    )
    for w in unsupported_numbers:
        data_quality_risks.append(f"Unsupported number risk: {w}")

    if is_mock:
        data_quality_risks.append(
            "CRITICAL: Mock provider active — all financial data is synthetic demo data. "
            "No real financial data has been sourced. "
            "All risk assessments are illustrative only."
        )

    # ── Source quality risks (from Phase 8) ──────────────────────────────
    sq_warnings = source_quality_summary.get("warnings", [])
    for w in sq_warnings:
        source_quality_risks.append(f"Source quality: {w}")

    aggregator_only = source_quality_summary.get("aggregator_only_claims", [])
    if aggregator_only:
        source_quality_risks.append(
            f"{len(aggregator_only)} claims rely only on T5/T6 aggregator sources. "
            "These may contain stale, incomplete or inaccurate data."
        )

    missing_primary = source_quality_summary.get("missing_primary_sources", [])
    for mp in missing_primary[:5]:
        source_quality_risks.append(f"Missing primary source: {mp}")

    # ── Warnings ──────────────────────────────────────────────────────────
    if is_mock:
        warnings.append(
            "Mock provider active — risk assessment is illustrative. "
            "Replace with real data before use."
        )

    if source_tier in ("T6_model_estimate", "T5_api_aggregator"):
        if fin_ev is not None and fin_ev.is_primary_backed:
            warnings.append(
                f"Identity and price data are {source_tier}. Financial statement "
                f"facts ({category_labels(fin_ev.resolved_categories)}) are "
                f"{fin_ev.best_tier}; the statement lines not yet extracted "
                "still limit the financial risk assessment."
            )
        else:
            warnings.append(
                f"Source tier {source_tier}: risk assessment based on aggregator "
                "data only. Primary filings (T1/T2) required for reliable risk "
                "assessment."
            )

    # ── Risk summary ──────────────────────────────────────────────────────
    total_risks = (
        len(business_risks) + len(financial_risks) +
        len(market_risks) + len(regulatory_geopolitical_risks) +
        len(data_quality_risks) + len(source_quality_risks)
    )
    unknown_count = sum(
        1 for r in (
            business_risks + financial_risks + market_risks +
            regulatory_geopolitical_risks + data_quality_risks + source_quality_risks
        )
        if r.startswith("UNKNOWN:")
    )

    risk_summary = (
        f"Risk assessment for {legal_name} ({ticker}), {sector}, {country}. "
        f"Total risk flags: {total_risks} "
        f"({unknown_count} marked UNKNOWN due to missing data). "
        f"Data quality: {_data_quality_label(is_mock, source_tier, fin_ev)}. "
        f"{_incompleteness_clause(fin_ev)} This is an internal draft only."
    )

    return RiskAgentOutput(
        business_risks=business_risks,
        financial_risks=financial_risks,
        market_risks=market_risks,
        regulatory_geopolitical_risks=regulatory_geopolitical_risks,
        data_quality_risks=data_quality_risks,
        source_quality_risks=source_quality_risks,
        risk_summary=risk_summary,
        warnings=warnings,
    )


def risk_agent_output_to_dict(output: RiskAgentOutput) -> dict:
    """Serialize output to a plain dict suitable for JSON storage."""
    return {
        "business_risks": output.business_risks,
        "financial_risks": output.financial_risks,
        "market_risks": output.market_risks,
        "regulatory_geopolitical_risks": output.regulatory_geopolitical_risks,
        "data_quality_risks": output.data_quality_risks,
        "source_quality_risks": output.source_quality_risks,
        "risk_summary": output.risk_summary,
        "warnings": output.warnings,
    }
