"""
BullCaseAgent — Phase 9 Analysis Council.

Identifies positive thesis elements from the research package.
Uses only existing company snapshot, research team summaries,
and cited/provider-backed data.

Constraints enforced:
  - No BUY / SELL / HOLD / WATCH / REJECT.
  - No price target or fair value.
  - No valuation conclusion.
  - No invented facts.
  - Always returns a result — never raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Forbidden words in any output string
_FORBIDDEN_RECOMMENDATION_WORDS = {
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "REJECT",
    "SHORTLIST",
    "SHORTLIST_HIGH",
}
_FORBIDDEN_VALUATION_PHRASES = {
    "price target",
    "target price",
    "fair value",
    "upside of",
    "undervalued",
    "overvalued",
}


@dataclass
class BullCaseOutput:
    """Structured output from the BullCaseAgent."""

    positive_thesis_points: list[str]
    potential_tailwinds: list[str]
    evidence_used: list[str]
    assumptions: list[str]
    missing_evidence: list[str]
    confidence_level: str          # "low" | "medium" | "high" — based on data completeness
    warnings: list[str] = field(default_factory=list)


def _check_forbidden_content(text: str) -> list[str]:
    """Return list of forbidden words/phrases found in text."""
    found: list[str] = []
    upper = text.upper()
    for word in _FORBIDDEN_RECOMMENDATION_WORDS:
        if word in upper:
            found.append(f"Forbidden recommendation word detected: {word}")
    lower = text.lower()
    for phrase in _FORBIDDEN_VALUATION_PHRASES:
        if phrase in lower:
            found.append(f"Forbidden valuation phrase detected: '{phrase}'")
    return found


def run_bull_case_agent(
    company_snapshot: dict,
    financial_data_summary: dict,
    source_quality_summary: dict,
    research_completeness_summary: dict,
    llm_sections: dict | None = None,
) -> BullCaseOutput:
    """
    Identify positive thesis elements from research package.

    All inputs come from the snapshot and Research Team outputs already
    in the workflow state — no LLM calls at this stage.

    Returns:
        BullCaseOutput — always returns, never raises.
    """
    warnings: list[str] = []
    positive_thesis_points: list[str] = []
    potential_tailwinds: list[str] = []
    evidence_used: list[str] = []
    assumptions: list[str] = []
    missing_evidence: list[str] = []

    identity = company_snapshot.get("company_identity", {})
    profile = company_snapshot.get("profile", {})
    price_summary = company_snapshot.get("price_history_summary", {})
    provider_meta = company_snapshot.get("provider_metadata", {})
    is_mock = company_snapshot.get("is_mock", True)

    ticker = identity.get("ticker", "N/A")
    legal_name = identity.get("legal_name", "Unknown")
    sector = profile.get("sector", "unknown sector")
    country = profile.get("country_domicile") or identity.get("country_domicile", "unknown")
    currency = profile.get("reporting_currency", "unknown")
    source_tier = provider_meta.get("source_tier", "T6_model_estimate")
    provider_name = provider_meta.get("provider_name", "unknown")

    # ── Evidence from identity / profile ─────────────────────────────────
    if legal_name and legal_name != "Unknown":
        evidence_used.append(f"Legal entity identified: {legal_name} ({ticker})")

    if sector and sector != "unknown sector":
        positive_thesis_points.append(
            f"Company operates in {sector} sector in {country}. "
            "Sector-level tailwinds may be relevant pending further research."
        )
        assumptions.append(
            f"Sector ({sector}) may benefit from macro or structural tailwinds — "
            "this requires verification against industry data (T1/T2 sources)."
        )

    if currency:
        evidence_used.append(
            f"Reporting currency: {currency}. "
            f"Exchange: {identity.get('exchange', 'N/A')}."
        )

    # ── Price history as positive evidence ────────────────────────────────
    if price_summary.get("available"):
        data_points = price_summary.get("data_points_count", 0)
        latest_close = price_summary.get("latest_close")
        evidence_used.append(
            f"Price history available from {provider_name}: "
            f"{data_points} data points. "
            f"Latest close: {latest_close} {price_summary.get('currency', '')} "
            f"(source tier: {source_tier})."
        )
        positive_thesis_points.append(
            "Price data available — enables tracking of recent price movement. "
            "Price trend analysis requires cross-referencing with fundamentals (not yet sourced)."
        )
        assumptions.append(
            "Price trend direction (if positive) is treated as a potential signal only. "
            "It is not a valuation signal at this phase."
        )
    else:
        missing_evidence.append("Price history not available — limits momentum analysis.")

    # ── Source quality as evidence quality signal ─────────────────────────
    overall_sq = source_quality_summary.get("overall_source_quality", "insufficient")
    if overall_sq in ("strong", "adequate"):
        positive_thesis_points.append(
            f"Source quality assessed as '{overall_sq}' — "
            "available data provides a reasonable foundation for further research."
        )
        evidence_used.append(f"Source quality assessment: {overall_sq}.")
    else:
        missing_evidence.append(
            f"Source quality is '{overall_sq}' — "
            "bull case cannot be adequately supported until higher-tier sources are obtained."
        )
        warnings.append(
            f"Source quality is '{overall_sq}'. "
            "Bull case points are tentative and require T1/T2 source verification."
        )

    # ── Research completeness as a gap signal ─────────────────────────────
    complete_sections = research_completeness_summary.get("complete_sections", [])
    missing_required = research_completeness_summary.get("missing_required_fields", [])
    blocking_count = len(research_completeness_summary.get("blocking_gaps", []))

    if complete_sections:
        evidence_used.append(
            f"Research sections with available data: {', '.join(complete_sections)}."
        )

    if blocking_count > 0:
        missing_evidence.append(
            f"{blocking_count} blocking research gaps identified. "
            "Bull case cannot be fully assessed until these gaps are resolved."
        )

    if missing_required:
        missing_evidence.append(
            f"{len(missing_required)} required report fields missing — "
            "bull case relies on incomplete data."
        )
        assumptions.append(
            "Missing fields (e.g. financials, ISIN, LEI) assumed not to invalidate the "
            "thesis — this assumption requires verification once data is sourced."
        )

    # ── LLM thesis draft as supplementary evidence ───────────────────────
    if llm_sections:
        thesis_draft = llm_sections.get("thesis_summary_draft", "")
        if thesis_draft:
            # Check for forbidden content in LLM output
            forbidden = _check_forbidden_content(thesis_draft)
            if forbidden:
                warnings.extend(forbidden)
                warnings.append(
                    "LLM thesis draft contained forbidden content — "
                    "not incorporated into bull case."
                )
            else:
                positive_thesis_points.append(
                    f"LLM thesis draft (admin review required): {thesis_draft}"
                )
                evidence_used.append("LLM-generated thesis summary incorporated (mock/draft only).")
                assumptions.append(
                    "LLM thesis is a draft narrative based on identity data only — "
                    "not verified against primary filings."
                )

        business_overview = llm_sections.get("business_overview_draft", "")
        if business_overview:
            forbidden = _check_forbidden_content(business_overview)
            if not forbidden:
                potential_tailwinds.append(
                    f"Business overview (LLM draft): {business_overview}"
                )

    # ── Tailwinds based on sector (structural, not valuation) ─────────────
    _sector_tailwinds = {
        "energy": [
            "Energy transition theme may drive medium-term demand for sector participants.",
            "Regulatory support for clean energy in some jurisdictions.",
        ],
        "industrials": [
            "Infrastructure investment cycles may benefit industrial companies.",
            "Supply chain reshoring trends could expand addressable markets.",
        ],
        "materials": [
            "Commodity price cycles and demand from electrification themes.",
            "Critical materials demand linked to energy transition.",
        ],
        "technology": [
            "Secular digitalisation trend supports technology sector broadly.",
            "AI adoption cycle may expand total addressable market.",
        ],
        "healthcare": [
            "Aging demographics in developed markets support healthcare demand.",
            "Pipeline optionality in biopharmaceuticals if applicable.",
        ],
        "financials": [
            "Interest rate environment may support lending margins.",
            "Consolidation opportunities in fragmented sub-sectors.",
        ],
        "real estate": [
            "Real asset exposure provides potential inflation hedge characteristics.",
            "Yield characteristics relative to risk-free rate.",
        ],
    }

    sector_lower = sector.lower() if sector else ""
    for sector_key, tailwinds in _sector_tailwinds.items():
        if sector_key in sector_lower:
            for tw in tailwinds:
                potential_tailwinds.append(
                    f"Structural tailwind (sector-level, requires company-level validation): {tw}"
                )
            break

    if not potential_tailwinds:
        potential_tailwinds.append(
            "No sector-specific structural tailwinds identified at this phase. "
            "Requires industry research (T3 sources)."
        )
        missing_evidence.append("Sector-specific tailwind analysis requires T3 industry sources.")

    # ── Confidence level ──────────────────────────────────────────────────
    data_gaps = len(missing_evidence)
    if is_mock:
        confidence_level = "low"
        warnings.append(
            "Mock provider active — bull case is based on synthetic demo data. "
            "All positive thesis points require real data verification."
        )
    elif source_tier in ("T6_model_estimate", "T5_api_aggregator"):
        confidence_level = "low"
        warnings.append(
            f"Data source is {source_tier} — bull case confidence is low. "
            "T1/T2 primary sources required."
        )
    elif data_gaps >= 3:
        confidence_level = "low"
    elif data_gaps >= 1:
        confidence_level = "medium"
    else:
        confidence_level = "high"

    # ── Final safety check on all generated text ──────────────────────────
    all_text = " ".join(
        positive_thesis_points + potential_tailwinds + evidence_used + assumptions
    )
    safety_violations = _check_forbidden_content(all_text)
    if safety_violations:
        warnings.extend(safety_violations)

    return BullCaseOutput(
        positive_thesis_points=positive_thesis_points,
        potential_tailwinds=potential_tailwinds,
        evidence_used=evidence_used,
        assumptions=assumptions,
        missing_evidence=missing_evidence,
        confidence_level=confidence_level,
        warnings=warnings,
    )


def bull_case_output_to_dict(output: BullCaseOutput) -> dict:
    """Serialize output to a plain dict suitable for JSON storage."""
    return {
        "positive_thesis_points": output.positive_thesis_points,
        "potential_tailwinds": output.potential_tailwinds,
        "evidence_used": output.evidence_used,
        "assumptions": output.assumptions,
        "missing_evidence": output.missing_evidence,
        "confidence_level": output.confidence_level,
        "warnings": output.warnings,
    }
