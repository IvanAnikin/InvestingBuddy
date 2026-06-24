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


def run_risk_agent(
    company_snapshot: dict,
    financial_data_summary: dict,
    source_quality_summary: dict,
    research_completeness_summary: dict,
    upgraded_citation_validation: dict | None = None,
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
    price_summary = company_snapshot.get("price_history_summary", {})
    provider_meta = company_snapshot.get("provider_metadata", {})
    is_mock = company_snapshot.get("is_mock", True)

    ticker = identity.get("ticker", "N/A")
    legal_name = identity.get("legal_name", "Unknown")
    sector = profile.get("sector", "unknown sector")
    country = profile.get("country_domicile") or identity.get("country_domicile", "unknown")
    source_tier = provider_meta.get("source_tier", "T6_model_estimate")
    provider_name = provider_meta.get("provider_name", "unknown")

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
    missing_financials = [
        f for f in financial_data_summary.get("missing_financial_data", [])
        if f.startswith("financials.")
    ]
    if missing_financials:
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

    financial_risks.append(
        "Currency risk: reporting currency is "
        f"'{profile.get('reporting_currency', 'unknown')}'. "
        "FX exposure to investment base currency is unknown at this phase."
    )

    # ── Market risks ──────────────────────────────────────────────────────
    if price_summary.get("available"):
        pts = price_summary.get("data_points_count", 0)
        market_risks.append(
            f"Price volatility risk: price data available ({pts} data points "
            f"from {provider_name}). Volatility, beta, and correlation to "
            "broader market indices not yet computed."
        )
    else:
        market_risks.append(
            "UNKNOWN: No price history — market liquidity, volatility, and "
            "trading characteristics cannot be assessed."
        )

    market_risks.append(
        f"Market depth risk: Exchange is {identity.get('exchange', 'unknown')}. "
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
        warnings.append(
            f"Source tier {source_tier}: risk assessment based on aggregator data only. "
            "Primary filings (T1/T2) required for reliable risk assessment."
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
        f"Data quality: {'MOCK (synthetic)' if is_mock else source_tier}. "
        "Assessment is incomplete — primary filings (T1/T2) required before any "
        "investment decision. This is an internal draft only."
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
