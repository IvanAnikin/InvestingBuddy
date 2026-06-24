"""
BearCaseAgent — Phase 9 Analysis Council.

Identifies negative thesis elements, weaknesses, missing data and downside
risks. Explicitly challenges the bull case.

Constraints enforced:
  - No SELL / SHORT recommendation.
  - No price target or fair value.
  - No invented facts.
  - Always returns a result — never raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_FORBIDDEN_RECOMMENDATION_WORDS = {
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "REJECT",
    "SHORTLIST",
    "SHORTLIST_HIGH",
    "SHORT",
}
_FORBIDDEN_VALUATION_PHRASES = {
    "price target",
    "target price",
    "fair value",
    "upside of",
    "downside of",
    "undervalued",
    "overvalued",
}


@dataclass
class BearCaseOutput:
    """Structured output from the BearCaseAgent."""

    negative_thesis_points: list[str]
    potential_headwinds: list[str]
    key_unknowns: list[str]
    evidence_used: list[str]
    missing_evidence: list[str]
    confidence_level: str          # "low" | "medium" | "high"
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


def run_bear_case_agent(
    company_snapshot: dict,
    financial_data_summary: dict,
    source_quality_summary: dict,
    research_completeness_summary: dict,
    bull_case_summary: dict | None = None,
) -> BearCaseOutput:
    """
    Identify negative thesis elements from the research package.
    Explicitly challenges the bull case where available.

    Returns:
        BearCaseOutput — always returns, never raises.
    """
    warnings: list[str] = []
    negative_thesis_points: list[str] = []
    potential_headwinds: list[str] = []
    key_unknowns: list[str] = []
    evidence_used: list[str] = []
    missing_evidence: list[str] = []

    profile = company_snapshot.get("profile", {})
    price_summary = company_snapshot.get("price_history_summary", {})
    provider_meta = company_snapshot.get("provider_metadata", {})
    snapshot_missing = company_snapshot.get("missing_fields", [])
    is_mock = company_snapshot.get("is_mock", True)

    sector = profile.get("sector", "unknown sector")
    source_tier = provider_meta.get("source_tier", "T6_model_estimate")
    provider_name = provider_meta.get("provider_name", "unknown")

    # ── Data quality as bear case evidence ───────────────────────────────
    overall_sq = source_quality_summary.get("overall_source_quality", "insufficient")
    if overall_sq in ("weak", "insufficient"):
        negative_thesis_points.append(
            f"Source quality is '{overall_sq}' — the research package lacks the "
            "primary (T1/T2) sources needed to validate any investment thesis. "
            "Claims from T5/T6 sources are not sufficient for investment decisions."
        )
        evidence_used.append(f"Source quality assessment: {overall_sq}.")

    # ── Challenge the bull case ───────────────────────────────────────────
    if bull_case_summary:
        bull_confidence = bull_case_summary.get("confidence_level", "low")
        bull_assumptions = bull_case_summary.get("assumptions", [])
        bull_missing = bull_case_summary.get("missing_evidence", [])

        if bull_confidence == "low":
            negative_thesis_points.append(
                "Bull case confidence is 'low' — the positive thesis is not supported "
                "by sufficient primary evidence at this phase."
            )

        if bull_assumptions:
            for assumption in bull_assumptions:
                potential_headwinds.append(
                    f"Bull case assumption challenged: '{assumption}' — "
                    "this assumption has not been verified against primary data."
                )

        if bull_missing:
            missing_evidence.extend(bull_missing)

    # ── Missing financial fundamentals ────────────────────────────────────
    missing_financials = [
        f for f in financial_data_summary.get("missing_financial_data", [])
        if f.startswith("financials.")
    ]
    if missing_financials:
        negative_thesis_points.append(
            f"All {len(missing_financials)} core financial fundamental categories are missing "
            "(revenue, EBITDA, margins, debt, cash flow). "
            "Cannot assess financial health, profitability or balance sheet risk."
        )
        key_unknowns.append(
            "Financial fundamentals (revenue, EBITDA, net income, cash flow, debt levels) — "
            "none sourced at this phase."
        )

    # ── Provider quality risk ─────────────────────────────────────────────
    aggregator_only = source_quality_summary.get("aggregator_only_claims", [])
    if aggregator_only:
        negative_thesis_points.append(
            f"{len(aggregator_only)} claims rely solely on aggregator (T5/T6) sources. "
            "These claims may contain errors, stale data, or provider-specific distortions."
        )
        evidence_used.append(
            f"Aggregator-only claim count: {len(aggregator_only)} from {provider_name}."
        )

    # ── Missing identity fields ───────────────────────────────────────────
    critical_missing_identity = [
        f for f in snapshot_missing
        if f in ("identity.isin", "identity.lei", "identity.country_domicile")
    ]
    if critical_missing_identity:
        negative_thesis_points.append(
            f"Critical identity fields missing: {', '.join(critical_missing_identity)}. "
            "Cannot confirm company legal identity — regulatory or listing risk may apply."
        )
        key_unknowns.append(
            "Legal entity verification not complete: "
            f"{', '.join(critical_missing_identity)} absent."
        )

    # ── Research completeness gaps ────────────────────────────────────────
    blocking_gaps = research_completeness_summary.get("blocking_gaps", [])
    if blocking_gaps:
        negative_thesis_points.append(
            f"{len(blocking_gaps)} blocking research gaps: the research package is not "
            "yet complete enough to support a positive investment thesis."
        )
        for gap in blocking_gaps[:5]:
            key_unknowns.append(f"Blocking gap: {gap}")

    # ── No price history ──────────────────────────────────────────────────
    if not price_summary.get("available"):
        negative_thesis_points.append(
            "No price history available — cannot assess recent price behavior, "
            "liquidity, or momentum signals."
        )
        key_unknowns.append("Price history and liquidity profile — not sourced.")

    # ── Sector headwinds (structural) ─────────────────────────────────────
    _sector_headwinds = {
        "energy": [
            "Energy transition may disrupt incumbent fossil fuel exposure.",
            "Commodity price volatility creates earnings unpredictability.",
        ],
        "industrials": [
            "Cyclical demand sensitivity to global GDP growth slowdown.",
            "Input cost inflation (energy, raw materials) may compress margins.",
        ],
        "materials": [
            "Commodity price cycles create significant earnings volatility.",
            "Demand tied to construction and manufacturing cycles.",
        ],
        "technology": [
            "Rapid competitive disruption risk; product cycles may shorten.",
            "Regulatory scrutiny on large technology platforms.",
        ],
        "healthcare": [
            "Drug pricing regulatory risk in key markets.",
            "Clinical trial failure risk for development-stage companies.",
        ],
        "financials": [
            "Credit cycle risk and loan loss provisions.",
            "Interest rate sensitivity to margin compression.",
        ],
        "real estate": [
            "Interest rate sensitivity — rising rates compress real estate valuations.",
            "Vacancy risk in demand-driven sub-sectors.",
        ],
    }

    sector_lower = (sector or "").lower()
    for sector_key, headwinds in _sector_headwinds.items():
        if sector_key in sector_lower:
            for hw in headwinds:
                potential_headwinds.append(
                    f"Sector headwind (structural, requires company-level validation): {hw}"
                )
            break

    if not potential_headwinds:
        potential_headwinds.append(
            "No sector-specific headwinds identified at this phase — requires industry research."
        )

    # ── Mock data risk ────────────────────────────────────────────────────
    if is_mock:
        negative_thesis_points.append(
            "All data is synthetic (MockFinancialDataProvider) — "
            "the bear case cannot be meaningfully assessed with mock data."
        )
        warnings.append(
            "Mock provider active — bear case is based on synthetic demo data. "
            "All negative thesis points are illustrative only."
        )
        key_unknowns.append(
            "All real financial data — mock provider used; no actual data available."
        )

    # ── Missing evidence summary ──────────────────────────────────────────
    missing_required = research_completeness_summary.get("missing_required_fields", [])
    if missing_required:
        missing_evidence.append(
            f"{len(missing_required)} required report fields missing — "
            "bear case assessment is incomplete."
        )

    for miss in source_quality_summary.get("missing_primary_sources", [])[:5]:
        missing_evidence.append(f"Missing primary source: {miss}")

    # ── Confidence level ──────────────────────────────────────────────────
    n_negatives = len(negative_thesis_points)
    if is_mock:
        confidence_level = "low"
    elif source_tier in ("T6_model_estimate", "T5_api_aggregator"):
        confidence_level = "low"
    elif n_negatives >= 3:
        confidence_level = "high"
    elif n_negatives >= 1:
        confidence_level = "medium"
    else:
        confidence_level = "low"

    # ── Final safety check ────────────────────────────────────────────────
    all_text = " ".join(negative_thesis_points + potential_headwinds + evidence_used)
    safety_violations = _check_forbidden_content(all_text)
    if safety_violations:
        warnings.extend(safety_violations)

    return BearCaseOutput(
        negative_thesis_points=negative_thesis_points,
        potential_headwinds=potential_headwinds,
        key_unknowns=key_unknowns,
        evidence_used=evidence_used,
        missing_evidence=missing_evidence,
        confidence_level=confidence_level,
        warnings=warnings,
    )


def bear_case_output_to_dict(output: BearCaseOutput) -> dict:
    """Serialize output to a plain dict suitable for JSON storage."""
    return {
        "negative_thesis_points": output.negative_thesis_points,
        "potential_headwinds": output.potential_headwinds,
        "key_unknowns": output.key_unknowns,
        "evidence_used": output.evidence_used,
        "missing_evidence": output.missing_evidence,
        "confidence_level": output.confidence_level,
        "warnings": output.warnings,
    }
