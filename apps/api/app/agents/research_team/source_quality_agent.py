"""
SourceQualityAgent — Phase 8 Research Team.

Evaluates whether available sources are sufficient for draft research.
Classifies source strength using T1–T6 source tiers.
Warns when important facts rely only on T5 aggregator or T6 estimate data.

Tier classification rules enforced:
  - EODHD, Stooq, OpenBB → T5_api_aggregator (never promoted to primary)
  - GLEIF → T2_regulator_or_gov
  - SEC EDGAR → T1_primary_filing or T2_regulator_or_gov (per data type)
  - Mock provider → T6_model_estimate (always weak)
  - T5/T6 alone is insufficient for decision-critical claims.

Fully deterministic — no LLM calls.
No investment recommendation. No invented data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.canonical_evidence import (
    PRIMARY_FACT_FIELDS,
    resolve_fundamentals,
)
from app.services.sources.company_evidence import regulator_connector_for

# Phase-32A(fix) — human-readable display names for the regulator connector ids
# ``regulator_connector_for`` (company_evidence.py) can resolve. Deliberately a
# local copy (not an import from the connector modules) to avoid coupling this
# deterministic, no-LLM agent to the connector layer's own display strings —
# mirrors the identical map in ``research_completeness_agent.py``. No
# individual company name is ever hardcoded — only venue/regulator names.
_REGULATOR_DISPLAY_NAMES: dict[str, str] = {
    "uk_fca_nsm": "the UK FCA National Storage Mechanism (NSM)",
    "euronext_regulated_info": "Euronext Regulated Information / AMF filings",
    "deutsche_boerse": "the German regulated-information venue (Deutsche Börse / Bundesanzeiger)",
    "nordic_disclosures": "Nasdaq Nordic company disclosures",
    "six_swiss": "SIX Swiss Exchange regulatory disclosures",
}


def _jurisdiction_appropriate_regulator_line(company_snapshot: dict) -> str:
    """The "missing regulatory filings" line, swapped to the issuer's actual
    home regulator when resolvable — never guessed when unresolved (a US
    issuer, or any issuer whose exchange/country does not resolve to a known
    regulator, keeps the generic "SEC EDGAR / SEDAR+" wording unchanged)."""
    generic = "SEC EDGAR / SEDAR+ filings — T2_regulator_or_gov needed for regulatory data"
    identity: dict = (company_snapshot or {}).get("company_identity") or {}
    exchange = identity.get("exchange")
    country = identity.get("country_domicile")
    if not exchange and not country:
        return generic
    try:
        connector_id = regulator_connector_for(exchange, country)
    except Exception:
        connector_id = None
    display_name = _REGULATOR_DISPLAY_NAMES.get(connector_id) if connector_id else None
    if not display_name:
        return generic
    return f"{display_name} — T2_regulator_or_gov needed for regulatory data"

# Tier strength ordering (lower index = stronger)
_TIER_RANK = {
    "T1_primary_filing": 1,
    "T2_regulator_or_gov": 2,
    "T3_industry_specialist": 3,
    "T4_quality_media": 4,
    "T5_api_aggregator": 5,
    "T6_model_estimate": 6,
}

# Tiers considered acceptable as "strong" for identity/price claims
_STRONG_TIERS = {"T1_primary_filing", "T2_regulator_or_gov", "T3_industry_specialist"}
# Tiers considered weak for any decision-critical claim
_WEAK_TIERS = {"T5_api_aggregator", "T6_model_estimate"}

# Providers known to be T5 aggregators (never promote to primary)
_T5_PROVIDERS = {"eodhd", "stooq", "alpha_vantage", "openbb"}
# Providers known to be T2 regulatory sources
_T2_PROVIDERS = {"gleif", "sec_edgar", "sedar"}
# Mock providers → always T6
_T6_PROVIDERS = {"mock"}

# Fields considered "decision-critical" for investment analysis
_DECISION_CRITICAL_FIELDS = {
    "identity.legal_name",
    "identity.ticker",
    "identity.exchange",
    "identity.country_domicile",
    "financials.revenue",
    "financials.ebitda",
    "financials.market_cap",
    "financials.total_debt",
    "price_history.latest_close",
}


@dataclass
class SourceQualityAgentOutput:
    """Structured output from the SourceQualityAgent."""

    overall_source_quality: str  # "strong" | "adequate" | "weak" | "insufficient"
    strong_sources: list[str]
    weak_sources: list[str]
    missing_primary_sources: list[str]
    aggregator_only_claims: list[str]
    recommended_source_upgrades: list[str]
    warnings: list[str] = field(default_factory=list)


def _has_derived_market_metrics(company_snapshot: dict) -> bool:
    """
    True when the snapshot carries a Phase 19.4 derived market metric (market cap
    or enterprise value) as a present value. Used to recommend upgrading derived
    estimates to a primary source — without ever asserting they are unavailable.
    """
    mm = company_snapshot.get("market_metrics_summary") or {}
    if mm.get("market_cap_mln") is not None or mm.get("enterprise_value_mln") is not None:
        return True
    fs = company_snapshot.get("fundamentals_summary") or {}
    return (
        fs.get("market_cap_usd_m") is not None
        or fs.get("enterprise_value_usd_m") is not None
    )


def _classify_provider(provider_name: str, declared_tier: str | None) -> str:
    """
    Resolve the effective source tier for a provider.

    Provider name takes precedence over declared tier to prevent T5 providers
    being accidentally promoted by misconfigured metadata.
    """
    lower = provider_name.lower().strip()
    if lower in _T6_PROVIDERS:
        return "T6_model_estimate"
    if lower in _T5_PROVIDERS:
        return "T5_api_aggregator"
    if lower in _T2_PROVIDERS:
        return "T2_regulator_or_gov"
    # Fall back to declared tier if provider not in known lists
    return declared_tier or "T6_model_estimate"


def run_source_quality_agent(
    company_snapshot: dict,
    citation_source_tiers: list[str] | None = None,
    primary_facts: list[dict] | None = None,
) -> SourceQualityAgentOutput:
    """
    Evaluate source quality from the company snapshot and citation metadata.

    Args:
        company_snapshot: dict produced by build_company_snapshot().
        citation_source_tiers: list of source_tier strings from Citation records
                               linked to this report (optional).
        primary_facts: high-confidence issuer-document facts surfaced by the
                       council (optional). Only available AFTER document
                       ingestion, so this agent's workflow-time invocation
                       passes None and the final-report generator re-invokes it
                       with the real facts. Without it, a non-US issuer whose
                       financials come from its own annual report (no SEC XBRL)
                       keeps the "annual report required for financials" gap
                       even after that report has been read — observed on CFR
                       and MC.

    Returns:
        SourceQualityAgentOutput — always returns, never raises.
    """
    warnings: list[str] = []

    provider_meta = company_snapshot.get("provider_metadata", {})
    price_summary = company_snapshot.get("price_history_summary", {})
    missing_fields = set(company_snapshot.get("missing_fields", []))
    is_mock = company_snapshot.get("is_mock", True)

    provider_name = provider_meta.get("provider_name", "unknown")
    declared_tier = provider_meta.get("source_tier", "T6_model_estimate")
    effective_tier = _classify_provider(provider_name, declared_tier)

    # ── Classify sources ──────────────────────────────────────────────────
    strong_sources: list[str] = []
    weak_sources: list[str] = []

    if effective_tier in _STRONG_TIERS:
        strong_sources.append(
            f"{provider_name} ({effective_tier}): company identity and profile data"
        )
    else:
        weak_sources.append(
            f"{provider_name} ({effective_tier}): company identity and profile data"
        )

    if price_summary.get("available"):
        price_provider = price_summary.get("provider_name", provider_name)
        price_tier = _classify_provider(price_provider, effective_tier)
        if price_tier in _STRONG_TIERS:
            strong_sources.append(
                f"{price_provider} ({price_tier}): price history data"
            )
        else:
            weak_sources.append(
                f"{price_provider} ({price_tier}): price history data"
            )

    # Incorporate citation source tiers if provided
    if citation_source_tiers:
        tier_counts: dict[str, int] = {}
        for t in citation_source_tiers:
            tier_counts[t] = tier_counts.get(t, 0) + 1
        for tier, count in tier_counts.items():
            label = f"Citations: {count} records at {tier}"
            if tier in _STRONG_TIERS:
                strong_sources.append(label)
            else:
                weak_sources.append(label)

    # ── Missing primary sources ───────────────────────────────────────────
    missing_primary: list[str] = []
    if "identity.isin" in missing_fields:
        missing_primary.append(
            "ISIN — required for unique instrument identification; "
            "obtain from exchange listing or regulatory filing"
        )
    if "identity.lei" in missing_fields:
        missing_primary.append(
            "LEI — required for legal entity identification; "
            "obtain from GLEIF (T2_regulator_or_gov)"
        )
    # Product-readiness fix — these three lines used to be appended
    # UNCONDITIONALLY. For NVDA that produced "Annual report / 10-K / 40-F —
    # T1_primary_filing required for financials" and "SEC EDGAR / SEDAR+
    # filings — T2_regulator_or_gov needed for regulatory data" in a report
    # that had already sourced the full FY2026 10-K statement set from SEC
    # EDGAR XBRL. Those false gaps propagate into the bear case, the risk
    # agent's source-quality risks AND the LLM council's ``known_gaps`` — which
    # is a direct input to the committee chair's label. Claiming a gap that has
    # in fact been closed is what pushed an 8/8 council with real
    # regulator-backed financials to ``insufficient_data``.
    #
    # A gap is now asserted only when the canonical evidence inventory says it
    # is genuinely open. What remains open is stated precisely instead.
    fundamentals = resolve_fundamentals(
        company_snapshot, None, primary_facts, financial_fields=PRIMARY_FACT_FIELDS
    )
    if not fundamentals.available:
        missing_primary.append(
            "Annual report / 10-K / 40-F — T1_primary_filing required for financials"
        )
        missing_primary.append(_jurisdiction_appropriate_regulator_line(company_snapshot))
    elif fundamentals.is_regulator_structured:
        # Regulator-backed structured statements ARE present. The remaining,
        # real gap is the filing NARRATIVE (MD&A, segment discussion, notes),
        # which structured XBRL statement data does not carry.
        missing_primary.append(
            "Annual report / 10-K / 40-F NARRATIVE (MD&A, segment discussion, "
            "accounting notes) — structured "
            f"{fundamentals.source} statement facts are already sourced "
            f"({fundamentals.source_tier}); the filing's narrative text is not"
        )
    else:
        missing_primary.append(
            "Annual report / 10-K / 40-F — T1/T2 filing needed to validate the "
            f"aggregator fundamentals currently sourced from {fundamentals.source}"
        )
        missing_primary.append(_jurisdiction_appropriate_regulator_line(company_snapshot))
    missing_primary.append(
        "Earnings call transcript — T1 source for management commentary"
    )

    # ── Aggregator-only claims ────────────────────────────────────────────
    aggregator_only_claims: list[str] = []
    if effective_tier in _WEAK_TIERS:
        for critical_field in _DECISION_CRITICAL_FIELDS:
            # Only flag fields that are in the snapshot (not just missing)
            if critical_field not in missing_fields:
                aggregator_only_claims.append(
                    f"{critical_field}: sourced only from {effective_tier} ({provider_name})"
                )

    # ── Recommended upgrades ──────────────────────────────────────────────
    recommended_upgrades: list[str] = []
    if is_mock or effective_tier == "T6_model_estimate":
        recommended_upgrades.append(
            "Replace mock/T6 data with live provider: "
            "use Stooq (T5) for prices, GLEIF (T2) for LEI, "
            "SEC EDGAR (T2) for US filings"
        )
    if effective_tier in _WEAK_TIERS:
        recommended_upgrades.append(
            f"Upgrade {provider_name} ({effective_tier}) data with T1 primary filing "
            "(annual report, 10-K, prospectus) for financial fundamentals"
        )
    # Phase 19.4.1: only recommend obtaining the LEI when it is actually missing.
    # When enrichment has already sourced the LEI (GLEIF, T2) it is present in the
    # snapshot and pruned from missing_fields, so this recommendation is skipped.
    if "identity.lei" in missing_fields:
        recommended_upgrades.append(
            "Obtain LEI from GLEIF API to confirm legal entity identity"
        )
    recommended_upgrades.extend([
        "Source latest annual report (T1) for revenue, EBITDA, debt metrics",
        "Obtain sell-side coverage data from T3/T4 sources for peer comparison",
    ])
    # Phase 19.4.1: market cap / EV / P/E may be present only as DERIVED ESTIMATES
    # (T6, from free price + SEC data). Recommend upgrading them to a primary
    # source before publication — but never claim they are absent when present.
    if _has_derived_market_metrics(company_snapshot):
        recommended_upgrades.append(
            "Replace derived market metrics (market cap, enterprise value, P/E) — "
            "currently T6 estimates from free price (T5) and SEC (T2) data — with "
            "official or primary-sourced market data before publication"
        )

    # ── Overall quality assessment ────────────────────────────────────────
    if len(strong_sources) > 0 and len(weak_sources) == 0:
        overall = "strong"
    elif len(strong_sources) > 0 and len(weak_sources) > 0:
        overall = "adequate"
    elif len(weak_sources) > 0 and len(strong_sources) == 0:
        overall = "weak"
    else:
        overall = "insufficient"

    # ── Warnings ─────────────────────────────────────────────────────────
    if is_mock:
        warnings.append(
            "Mock provider active: all data is synthetic. "
            "No real financial claims can be supported."
        )
    if effective_tier in _WEAK_TIERS:
        warnings.append(
            f"All current data from {effective_tier} ({provider_name}) only. "
            "Decision-critical fields lack primary source confirmation."
        )
    if aggregator_only_claims:
        warnings.append(
            f"{len(aggregator_only_claims)} decision-critical field(s) rely only on "
            f"aggregator/estimate data ({effective_tier}). "
            "These must be verified against T1/T2 sources before any analysis is published."
        )
    if overall in ("weak", "insufficient"):
        warnings.append(
            f"Overall source quality is '{overall}'. "
            "Do not publish research based on this source package alone."
        )

    return SourceQualityAgentOutput(
        overall_source_quality=overall,
        strong_sources=strong_sources,
        weak_sources=weak_sources,
        missing_primary_sources=missing_primary,
        aggregator_only_claims=aggregator_only_claims,
        recommended_source_upgrades=recommended_upgrades,
        warnings=warnings,
    )


def source_quality_output_to_dict(output: SourceQualityAgentOutput) -> dict:
    """Serialize output to a plain dict suitable for JSON storage."""
    return {
        "overall_source_quality": output.overall_source_quality,
        "strong_sources": output.strong_sources,
        "weak_sources": output.weak_sources,
        "missing_primary_sources": output.missing_primary_sources,
        "aggregator_only_claims": output.aggregator_only_claims,
        "recommended_source_upgrades": output.recommended_source_upgrades,
        "warnings": output.warnings,
    }
