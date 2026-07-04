"""
InvestmentCommitteeChair — Phase 9 Analysis Council.

Summarises the Analysis Council debate into an admin-only committee draft.
Compares bull case, bear case, risk view, valuation guard and research
completeness to produce a preliminary internal research workflow status.

Allowed internal statuses ONLY:
  - "research_incomplete"
  - "needs_primary_sources"
  - "ready_for_deeper_analysis"
  - "reject_due_to_data_quality"
  - "watchlist_candidate_for_review"

FORBIDDEN internal status values:
  - BUY, SELL, HOLD, WATCH, REJECT, SHORTLIST, SHORTLIST_HIGH
  - Any price target or fair value

IMPORTANT: "watchlist_candidate_for_review" is an internal research workflow
status only — not a public investment recommendation. It must never be
presented to end users as investment advice.

Always returns a result — never raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Allowed internal statuses
ALLOWED_INTERNAL_STATUSES = {
    "research_incomplete",
    "needs_primary_sources",
    "ready_for_deeper_analysis",
    "reject_due_to_data_quality",
    "watchlist_candidate_for_review",
}

# Absolutely forbidden in output
_FORBIDDEN_OUTPUTS = {
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "REJECT",
    "SHORTLIST",
    "SHORTLIST_HIGH",
    "price target",
    "target price",
    "fair value",
    "upside of",
    "downside of",
    "undervalued",
    "overvalued",
}

# Quality gate fields
_QUALITY_GATE_FIELDS = {
    "source_quality_ok": False,
    "citation_status_ok": False,
    "schema_valid": False,
    "valuation_ready": False,
    "research_complete": False,
}


@dataclass
class CommitteeChairOutput:
    """Structured output from the InvestmentCommitteeChair."""

    committee_summary: str
    # "bull_dominant" | "bear_dominant" | "balanced" | "insufficient_data"
    bull_bear_balance: str
    primary_open_questions: list[str]
    research_next_steps: list[str]
    quality_gate_status: dict       # {field: bool}
    provisional_internal_status: str   # one of ALLOWED_INTERNAL_STATUSES
    human_review_required: bool
    warnings: list[str] = field(default_factory=list)


def _check_forbidden_output(text: str) -> list[str]:
    """Return list of any forbidden words/phrases found."""
    found: list[str] = []
    upper = text.upper()
    lower = text.lower()
    for phrase in _FORBIDDEN_OUTPUTS:
        if phrase.upper() in upper or phrase.lower() in lower:
            found.append(f"Forbidden content in committee output: '{phrase}'")
    return found


def run_investment_committee_chair(
    company_snapshot: dict,
    bull_case_summary: dict,
    bear_case_summary: dict,
    risk_summary: dict,
    valuation_guard_summary: dict,
    research_completeness_summary: dict,
    source_quality_summary: dict,
    upgraded_citation_validation: dict | None = None,
    schema_valid: bool | None = None,
) -> CommitteeChairOutput:
    """
    Synthesise Analysis Council outputs into an admin-only committee draft.

    Determines provisional internal research status based on quality gates.
    Does NOT assign BUY/SELL/HOLD/WATCH or any public recommendation.

    Returns:
        CommitteeChairOutput — always returns, never raises.
    """
    warnings: list[str] = []
    primary_open_questions: list[str] = []
    research_next_steps: list[str] = []

    identity = company_snapshot.get("company_identity", {})
    provider_meta = company_snapshot.get("provider_metadata", {})
    is_mock = company_snapshot.get("is_mock", True)

    ticker = identity.get("ticker", "N/A")
    legal_name = identity.get("legal_name", "Unknown")
    source_tier = provider_meta.get("source_tier", "T6_model_estimate")

    # ── Assess bull/bear balance ──────────────────────────────────────────
    bull_confidence = bull_case_summary.get("confidence_level", "low")
    bear_confidence = bear_case_summary.get("confidence_level", "low")
    bull_points_count = len(bull_case_summary.get("positive_thesis_points", []))
    bear_points_count = len(bear_case_summary.get("negative_thesis_points", []))

    if bull_confidence == "low" and bear_confidence == "low":
        bull_bear_balance = "insufficient_data"
    elif bull_points_count > bear_points_count and bull_confidence != "low":
        bull_bear_balance = "bull_dominant"
    elif bear_points_count > bull_points_count and bear_confidence != "low":
        bull_bear_balance = "bear_dominant"
    else:
        bull_bear_balance = "balanced"

    # ── Build quality gate status ─────────────────────────────────────────
    overall_sq = source_quality_summary.get("overall_source_quality", "insufficient")
    citation_status = (upgraded_citation_validation or {}).get("status", "unknown")
    valuation_readiness = valuation_guard_summary.get("valuation_readiness", "not_ready")
    blocking_gaps = len(research_completeness_summary.get("blocking_gaps", []))

    quality_gate_status = {
        "source_quality_ok": overall_sq in ("strong", "adequate"),
        "citation_status_ok": citation_status == "ok",
        "schema_valid": bool(schema_valid),
        "valuation_ready": valuation_readiness in ("ready", "partial"),
        "research_complete": blocking_gaps == 0,
    }

    gates_passed = sum(1 for v in quality_gate_status.values() if v)
    gates_total = len(quality_gate_status)

    # ── Determine provisional internal status ─────────────────────────────
    if is_mock:
        provisional_internal_status = "research_incomplete"
        warnings.append(
            "Mock provider active — provisional status forced to 'research_incomplete'. "
            "Status is not meaningful with synthetic data."
        )
    elif overall_sq in ("weak", "insufficient") and source_tier in (
        "T6_model_estimate", "T5_api_aggregator"
    ):
        provisional_internal_status = "needs_primary_sources"
    elif not quality_gate_status["citation_status_ok"] and citation_status == "failed":
        provisional_internal_status = "reject_due_to_data_quality"
    elif blocking_gaps > 0 and not quality_gate_status["research_complete"]:
        provisional_internal_status = "research_incomplete"
    elif gates_passed >= 3 and bull_bear_balance != "insufficient_data":
        provisional_internal_status = "watchlist_candidate_for_review"
    else:
        provisional_internal_status = "ready_for_deeper_analysis"

    # Validate: must be one of the allowed statuses
    if provisional_internal_status not in ALLOWED_INTERNAL_STATUSES:
        warnings.append(
            f"SAFETY: computed status '{provisional_internal_status}' not in allowed list. "
            "Falling back to 'research_incomplete'."
        )
        provisional_internal_status = "research_incomplete"

    # ── Primary open questions ────────────────────────────────────────────
    key_unknowns = bear_case_summary.get("key_unknowns", [])
    primary_open_questions.extend(key_unknowns[:5])

    bull_missing = bull_case_summary.get("missing_evidence", [])
    for m in bull_missing[:3]:
        if m not in primary_open_questions:
            primary_open_questions.append(m)

    vg_missing = valuation_guard_summary.get("missing_valuation_inputs", [])
    if vg_missing:
        primary_open_questions.append(
            f"Valuation blocked: {len(vg_missing)} inputs missing "
            f"({', '.join(vg_missing[:3])}{'...' if len(vg_missing) > 3 else ''})."
        )

    if not primary_open_questions:
        primary_open_questions.append(
            "Primary open questions cannot be determined without primary source data."
        )

    # ── Research next steps ───────────────────────────────────────────────
    next_tasks = research_completeness_summary.get("next_research_tasks", [])
    research_next_steps.extend(next_tasks[:6])

    vg_allowed = valuation_guard_summary.get("allowed_next_steps", [])
    for step in vg_allowed[:3]:
        if step not in research_next_steps:
            research_next_steps.append(step)

    sq_upgrades = source_quality_summary.get("recommended_source_upgrades", [])
    for upgrade in sq_upgrades[:3]:
        if upgrade not in research_next_steps:
            research_next_steps.append(upgrade)

    if not research_next_steps:
        research_next_steps.append(
            "Source T1 primary filings before any further analysis."
        )

    # ── Determine if human review required ───────────────────────────────
    human_review_required = (
        provisional_internal_status in (
            "watchlist_candidate_for_review",
            "ready_for_deeper_analysis",
        )
        or citation_status == "failed"
        or overall_sq in ("weak", "insufficient")
        or is_mock
    )

    # ── Build committee summary ───────────────────────────────────────────
    committee_summary = (
        f"INTERNAL COMMITTEE DRAFT — {legal_name} ({ticker}). "
        f"Provisional status: '{provisional_internal_status}'. "
        f"Bull/bear balance: {bull_bear_balance}. "
        f"Quality gates passed: {gates_passed}/{gates_total}. "
        f"Source quality: {overall_sq}. "
        f"Citation status: {citation_status}. "
        f"Valuation readiness: {valuation_readiness}. "
        f"Blocking research gaps: {blocking_gaps}. "
        f"Human review required: {human_review_required}. "
        "This is not an investment recommendation. "
        "Admin review required before any further action."
    )

    if is_mock:
        committee_summary += " [MOCK DATA — all assessments are illustrative only]"

    # ── Safety check on all output text ──────────────────────────────────
    all_text = " ".join([
        committee_summary,
        bull_bear_balance,
        provisional_internal_status,
    ] + primary_open_questions + research_next_steps)

    safety_violations = _check_forbidden_output(all_text)
    if safety_violations:
        warnings.extend(safety_violations)
        # Downgrade status on any safety violation
        provisional_internal_status = "research_incomplete"
        warnings.append(
            "SAFETY: forbidden content detected in committee output. "
            "Status downgraded to 'research_incomplete'."
        )

    return CommitteeChairOutput(
        committee_summary=committee_summary,
        bull_bear_balance=bull_bear_balance,
        primary_open_questions=primary_open_questions,
        research_next_steps=research_next_steps,
        quality_gate_status=quality_gate_status,
        provisional_internal_status=provisional_internal_status,
        human_review_required=human_review_required,
        warnings=warnings,
    )


def committee_chair_output_to_dict(output: CommitteeChairOutput) -> dict:
    """Serialize output to a plain dict suitable for JSON storage."""
    return {
        "committee_summary": output.committee_summary,
        "bull_bear_balance": output.bull_bear_balance,
        "primary_open_questions": output.primary_open_questions,
        "research_next_steps": output.research_next_steps,
        "quality_gate_status": output.quality_gate_status,
        "provisional_internal_status": output.provisional_internal_status,
        "human_review_required": output.human_review_required,
        "warnings": output.warnings,
    }
