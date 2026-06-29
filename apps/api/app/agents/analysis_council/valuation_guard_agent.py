"""
ValuationGuardAgent — Phase 9 Analysis Council.

Prevents premature valuation conclusions.
Identifies which valuation inputs are missing.
Determines whether valuation work is allowed with current evidence.

Rules enforced:
  - If key fundamentals are missing, valuation_readiness = "not_ready".
  - No fair value output.
  - No target price output.
  - No upside/downside percentage output.
  - No valuation multiple conclusion unless sourced from T1/T2.
  - Always returns a result — never raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Valuation inputs required for each method
_DCF_REQUIRED = [
    "financials.free_cash_flow",
    "financials.revenue",
    "financials.ebitda",
    "financials.net_income",
    "financials.total_debt",
    "financials.cash_and_equivalents",
]

_RELATIVE_REQUIRED = [
    "financials.ebitda",
    "financials.earnings_per_share",
    "financials.revenue",
    "price_history.latest_close",
]

_YIELD_REQUIRED = [
    "financials.dividend_yield",
    "price_history.latest_close",
    "financials.earnings_per_share",
]

# Fields disqualifying any valuation if absent
_IDENTITY_BLOCKERS = [
    "identity.legal_name",
    "identity.ticker",
]

# Disallowed phrases in any output
_FORBIDDEN_VALUATION_PHRASES = {
    "price target",
    "target price",
    "fair value",
    "upside of",
    "downside of",
    "intrinsic value",
    "undervalued",
    "overvalued",
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "REJECT",
}


@dataclass
class ValuationGuardOutput:
    """Structured output from the ValuationGuardAgent."""

    valuation_readiness: str           # "not_ready" | "partial" | "ready"
    available_valuation_inputs: list[str]
    missing_valuation_inputs: list[str]
    valuation_blockers: list[str]
    allowed_next_steps: list[str]
    disallowed_outputs: list[str]
    warnings: list[str] = field(default_factory=list)


def run_valuation_guard_agent(
    company_snapshot: dict,
    financial_data_summary: dict,
    source_quality_summary: dict,
) -> ValuationGuardOutput:
    """
    Guard against premature valuation conclusions.

    Checks available financial data against minimum requirements for
    each valuation method and blocks valuation outputs unless conditions met.

    Returns:
        ValuationGuardOutput — always returns, never raises.
    """
    warnings: list[str] = []
    valuation_blockers: list[str] = []
    allowed_next_steps: list[str] = []
    available_valuation_inputs: list[str] = []
    missing_valuation_inputs: list[str] = []

    is_mock = company_snapshot.get("is_mock", True)
    identity = company_snapshot.get("company_identity", {})
    profile = company_snapshot.get("profile", {})
    price_summary = company_snapshot.get("price_history_summary", {})
    provider_meta = company_snapshot.get("provider_metadata", {})

    source_tier = provider_meta.get("source_tier", "T6_model_estimate")
    provider_name = provider_meta.get("provider_name", "unknown")
    overall_sq = source_quality_summary.get("overall_source_quality", "insufficient")

    # Available data from snapshot
    available_financial_data = set(
        financial_data_summary.get("available_financial_data", [])
    )
    missing_financial_data = set(
        financial_data_summary.get("missing_financial_data", [])
    )

    # ── Check identity prerequisites ──────────────────────────────────────
    for field_path in _IDENTITY_BLOCKERS:
        section, key = field_path.split(".", 1)
        obj = identity if section == "identity" else profile
        if not obj.get(key):
            valuation_blockers.append(
                f"Identity field '{field_path}' missing — "
                "cannot confirm which entity is being valued."
            )

    # ── Check DCF inputs ──────────────────────────────────────────────────
    dcf_available = [f for f in _DCF_REQUIRED if f in available_financial_data]
    dcf_missing = [
        f for f in _DCF_REQUIRED
        if f in missing_financial_data or f not in available_financial_data
    ]
    available_valuation_inputs.extend(dcf_available)
    missing_valuation_inputs.extend(dcf_missing)

    if dcf_missing:
        ellipsis = "..." if len(dcf_missing) > 3 else ""
        valuation_blockers.append(
            f"DCF valuation blocked: {len(dcf_missing)} of {len(_DCF_REQUIRED)} "
            f"required inputs missing ({', '.join(dcf_missing[:3])}{ellipsis})."
        )

    # ── Check relative valuation inputs ───────────────────────────────────
    rel_available = [f for f in _RELATIVE_REQUIRED if f in available_financial_data]
    rel_missing = [f for f in _RELATIVE_REQUIRED if f not in available_financial_data]

    for f in rel_available:
        if f not in available_valuation_inputs:
            available_valuation_inputs.append(f)
    for f in rel_missing:
        if f not in missing_valuation_inputs:
            missing_valuation_inputs.append(f)

    if price_summary.get("available"):
        available_valuation_inputs.append("price_history.latest_close (available)")
    else:
        valuation_blockers.append(
            "Relative valuation (P/E, EV/EBITDA) blocked: no price data available."
        )

    if rel_missing:
        valuation_blockers.append(
            f"Relative valuation blocked: {len(rel_missing)} inputs missing "
            f"({', '.join(rel_missing[:3])}{'...' if len(rel_missing) > 3 else ''})."
        )

    # ── Source tier check ─────────────────────────────────────────────────
    if source_tier in ("T6_model_estimate", "T5_api_aggregator"):
        valuation_blockers.append(
            f"Source tier is {source_tier} — valuation multiples from {provider_name} "
            "must not be used as primary valuation inputs. "
            "T1/T2 primary sources required for any valuation conclusion."
        )
        warnings.append(
            f"Source tier {source_tier}: all current data is aggregator/estimate quality. "
            "Valuation work requires primary filing data (T1/T2)."
        )

    if overall_sq in ("weak", "insufficient"):
        valuation_blockers.append(
            f"Source quality '{overall_sq}' — insufficient for valuation analysis. "
            "T1 primary filings (annual reports) required at minimum."
        )

    # ── Mock data absolute block ──────────────────────────────────────────
    if is_mock:
        valuation_blockers.append(
            "CRITICAL: Mock provider active — all data is synthetic. "
            "Valuation is completely blocked when using mock data."
        )
        warnings.append(
            "Mock provider active — valuation guard returns 'not_ready'. "
            "No valuation work permitted with synthetic data."
        )

    # ── Determine valuation_readiness ─────────────────────────────────────
    hard_blocks = [b for b in valuation_blockers if "CRITICAL" in b or "blocked" in b.lower()]
    if hard_blocks or is_mock:
        valuation_readiness = "not_ready"
    elif len(valuation_blockers) > 2:
        valuation_readiness = "not_ready"
    elif valuation_blockers:
        valuation_readiness = "partial"
    else:
        valuation_readiness = "ready"

    # ── Allowed next steps ────────────────────────────────────────────────
    if valuation_readiness == "not_ready":
        allowed_next_steps.extend([
            "Source T1 primary filings (annual report / 10-K) for revenue, EBITDA, FCF.",
            "Verify legal entity via GLEIF (LEI lookup) and confirm ISIN.",
            "Source price history from exchange data or a T2/T3 provider.",
            "Complete Research Team outputs — resolve all blocking gaps first.",
            "Upgrade source quality from T5/T6 to T1/T2 before any valuation work.",
        ])
    elif valuation_readiness == "partial":
        allowed_next_steps.extend([
            "Fill remaining missing valuation inputs from T1/T2 sources.",
            "Validate existing data points against primary filings before use.",
            "Proceed with qualitative business model and competitive position assessment.",
        ])
    else:
        allowed_next_steps.extend([
            "Proceed with DCF sensitivity analysis using sourced T1 data.",
            "Compute relative multiples (EV/EBITDA, P/E) against sourced peer data.",
            "Present valuation range with explicit assumptions — not point estimates.",
        ])

    # ── Disallowed outputs ────────────────────────────────────────────────
    disallowed_outputs = [
        "Fair value estimate or intrinsic value conclusion.",
        "Price target or target price.",
        "Upside or downside percentage to any price target.",
        "Valuation multiple conclusion (EV/EBITDA, P/E, P/B) without T1/T2 sourced earnings.",
        "DCF output without T1/T2 sourced free cash flow data.",
        "Undervalued or overvalued label.",
        "Any investment recommendation (BUY, SELL, HOLD, WATCH, REJECT).",
    ]

    # ── Deduplicate inputs ────────────────────────────────────────────────
    available_valuation_inputs = list(dict.fromkeys(available_valuation_inputs))
    missing_valuation_inputs = list(dict.fromkeys(missing_valuation_inputs))

    return ValuationGuardOutput(
        valuation_readiness=valuation_readiness,
        available_valuation_inputs=available_valuation_inputs,
        missing_valuation_inputs=missing_valuation_inputs,
        valuation_blockers=valuation_blockers,
        allowed_next_steps=allowed_next_steps,
        disallowed_outputs=disallowed_outputs,
        warnings=warnings,
    )


def valuation_guard_output_to_dict(output: ValuationGuardOutput) -> dict:
    """Serialize output to a plain dict suitable for JSON storage."""
    return {
        "valuation_readiness": output.valuation_readiness,
        "available_valuation_inputs": output.available_valuation_inputs,
        "missing_valuation_inputs": output.missing_valuation_inputs,
        "valuation_blockers": output.valuation_blockers,
        "allowed_next_steps": output.allowed_next_steps,
        "disallowed_outputs": output.disallowed_outputs,
        "warnings": output.warnings,
    }
