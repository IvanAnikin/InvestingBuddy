"""
FinancialDataAgent — Phase 8 Research Team.

Converts the provider company snapshot into a structured financial research
summary. Identifies what financial data is available, what is missing, and
assigns quality assessments based on source tier.

Fully deterministic — no LLM calls required.
Can optionally accept an LLM client to enrich the `financial_context_summary`
narrative, but all structural fields are computed from provider data alone.

Constraints enforced:
  - No invented numbers.
  - No valuation conclusion.
  - No investment recommendation.
  - All warnings are non-fatal — agent always returns a result.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.canonical_evidence import resolve_price_provenance

# Source tiers considered "primary" for financial data purposes
_PRIMARY_TIERS = {"T1_primary_filing", "T2_regulator_or_gov"}
_AGGREGATOR_TIERS = {"T5_api_aggregator", "T6_model_estimate"}

# Financial data categories expected in a full company analysis
_EXPECTED_FINANCIAL_CATEGORIES = [
    "revenue",
    "ebitda",
    "ebit",
    "net_income",
    "total_assets",
    "total_debt",
    "cash_and_equivalents",
    "free_cash_flow",
    "market_cap",
    "enterprise_value",
    "ev_ebitda",
    "price_to_earnings",
    "dividend_yield",
    "earnings_per_share",
    "book_value_per_share",
    "return_on_equity",
    "debt_to_equity",
    "current_ratio",
]

# Maps a fundamentals_summary key → expected financial category.
# Covers both the SEC EDGAR normalized keys (Phase 19.3) and the EODHD keys
# (Phase 13). A category counts as available only when its value is not None.
_FUNDAMENTALS_TO_CATEGORY = {
    # SEC EDGAR normalized (Phase 19.3, T2_regulator_or_gov)
    "revenue_usd_m": "revenue",
    "operating_income_usd_m": "ebit",
    "net_income_usd_m": "net_income",
    "total_assets_usd_m": "total_assets",
    "total_debt_usd_m": "total_debt",
    "cash_and_equivalents_usd_m": "cash_and_equivalents",
    "free_cash_flow_usd_m": "free_cash_flow",
    "eps_basic": "earnings_per_share",
    "eps_diluted": "earnings_per_share",
    "return_on_equity_pct": "return_on_equity",
    "debt_to_equity": "debt_to_equity",
    # Phase 19.4 derived market metrics (T6_model_estimate from T5 price + T2 SEC).
    # The derived P/E is written to the shared ``pe_ratio`` key mapped below.
    "market_cap_usd_m": "market_cap",
    "enterprise_value_usd_m": "enterprise_value",
    # EODHD (Phase 13, T5_api_aggregator)
    "revenue_ttm_mln": "revenue",
    "ebitda_mln": "ebitda",
    "market_cap_mln": "market_cap",
    "enterprise_value_mln": "enterprise_value",
    "ev_ebitda_x": "ev_ebitda",
    "pe_ratio": "price_to_earnings",
    "return_on_equity_ttm": "return_on_equity",
}

# Identity / profile fields available at snapshot phase
_SNAPSHOT_FIELDS = [
    "identity.legal_name",
    "identity.ticker",
    "identity.exchange",
    "identity.country_domicile",
    "profile.sector",
    "profile.industry",
    "profile.reporting_currency",
    "profile.fiscal_year_end",
    "profile.description",
]

# Price fields available when price data exists
_PRICE_FIELDS = [
    "price_history.latest_close",
    "price_history.date_range",
    "price_history.data_points_count",
]


@dataclass
class FinancialDataAgentOutput:
    """Structured output from the FinancialDataAgent."""

    available_financial_data: list[str]
    missing_financial_data: list[str]
    data_quality_notes: list[str]
    source_tier_summary: dict  # e.g. {"T5_api_aggregator": 2, "T6_model_estimate": 0}
    financial_context_summary: str
    warnings: list[str] = field(default_factory=list)


def _fmt_m(value: float | None) -> str | None:
    """Format a USD-millions value, or None if absent."""
    if value is None:
        return None
    return f"{value:,.0f} USD_m"


def _summarize_fundamentals(fs: dict, legal_name: str) -> str:
    """
    Build a factual, internal narrative from normalized fundamentals.

    Uses only sourced values. Labels annual data as annual (never TTM) and is
    explicit about what remains unavailable. Produces no valuation conclusion,
    price target, fair value or recommendation.
    """
    basis = fs.get("period_basis", "annual")
    fy = fs.get("fiscal_year")
    form = fs.get("form_type")
    period_label = f"{basis} FY{fy}" if fy else basis
    if form:
        period_label += f" ({form})"

    parts: list[str] = [
        f"SEC EDGAR XBRL fundamentals were normalized for {legal_name} "
        f"for the latest {period_label}."
    ]

    rev = fs.get("revenue_usd_m")
    rev_g = fs.get("revenue_yoy_growth_pct")
    if rev is not None:
        seg = f"Revenue {_fmt_m(rev)}"
        if rev_g is not None:
            seg += f" ({rev_g:+.1f}% YoY)"
        parts.append(seg + ".")

    ni = fs.get("net_income_usd_m")
    nm = fs.get("net_margin_pct")
    if ni is not None:
        seg = f"Net income {_fmt_m(ni)}"
        if nm is not None:
            seg += f" (net margin {nm:.1f}%)"
        parts.append(seg + ".")

    opm = fs.get("operating_margin_pct")
    gm = fs.get("gross_margin_pct")
    if gm is not None or opm is not None:
        bits = []
        if gm is not None:
            bits.append(f"gross margin {gm:.1f}%")
        if opm is not None:
            bits.append(f"operating margin {opm:.1f}%")
        parts.append("Margins: " + ", ".join(bits) + ".")

    ocf = fs.get("operating_cash_flow_usd_m")
    fcf = fs.get("free_cash_flow_usd_m")
    if ocf is not None:
        seg = f"Operating cash flow {_fmt_m(ocf)}"
        if fcf is not None:
            seg += f"; free cash flow {_fmt_m(fcf)}"
        parts.append(seg + ".")

    ta = fs.get("total_assets_usd_m")
    tl = fs.get("total_liabilities_usd_m")
    eq = fs.get("shareholders_equity_usd_m")
    if any(v is not None for v in (ta, tl, eq)):
        bits = []
        if ta is not None:
            bits.append(f"assets {_fmt_m(ta)}")
        if tl is not None:
            bits.append(f"liabilities {_fmt_m(tl)}")
        if eq is not None:
            bits.append(f"equity {_fmt_m(eq)}")
        parts.append("Balance sheet: " + ", ".join(bits) + ".")

    td = fs.get("total_debt_usd_m")
    de = fs.get("debt_to_equity")
    if td is not None:
        seg = f"Total debt {_fmt_m(td)}"
        if de is not None:
            seg += f" (debt/equity {de:.2f}x)"
        parts.append(seg + ".")

    # ── Phase 19.4: derived market metrics (internal estimates) ───────────
    mktcap = fs.get("market_cap_usd_m")
    ev = fs.get("enterprise_value_usd_m")
    pe = fs.get("pe_ratio")
    shares = fs.get("shares_outstanding_mln")
    latest_close = fs.get("latest_close")
    derived_bits: list[str] = []
    if shares is not None:
        derived_bits.append(f"shares outstanding {shares:,.0f}M")
    if mktcap is not None:
        derived_bits.append(f"market cap ~{_fmt_m(mktcap)}")
    if ev is not None:
        derived_bits.append(f"enterprise value ~{_fmt_m(ev)}")
    if pe is not None:
        derived_bits.append(f"P/E ~{pe:.1f}x")
    if derived_bits:
        close_note = (
            f" (latest close {latest_close})" if latest_close is not None else ""
        )
        parts.append(
            "Derived market metrics for internal review"
            f"{close_note}: " + ", ".join(derived_bits) + ". "
            "These are DERIVED ESTIMATES (T6) from free price data (T5) and SEC "
            "fundamentals (T2), not official figures and not a valuation conclusion."
        )

    # ── Honest remaining gaps (annual vs TTM, EBITDA, beta) ──────────────
    remaining: list[str] = []
    if fs.get("ebitda_usd_m") is None:
        remaining.append("EBITDA and EV/EBITDA")
    if mktcap is None:
        remaining.append("market capitalization")
    remaining.append("beta and validated TTM figures")
    parts.append(
        "Market-based valuation remains incomplete: "
        + ", ".join(remaining)
        + " are not available from the free SEC/price sources at this phase. "
        "No valuation conclusion is produced at this phase."
    )
    return " ".join(parts)


def run_financial_data_agent(
    company_snapshot: dict,
    source_ids: list[str] | None = None,
) -> FinancialDataAgentOutput:
    """
    Analyse the company snapshot and produce a structured financial data summary.

    Args:
        company_snapshot: dict produced by build_company_snapshot().
        source_ids: list of Source record UUIDs created from provider data.

    Returns:
        FinancialDataAgentOutput — always returns, never raises.
    """
    warnings: list[str] = []
    data_quality_notes: list[str] = []

    identity = company_snapshot.get("company_identity", {})
    profile = company_snapshot.get("profile", {})
    price_summary = company_snapshot.get("price_history_summary", {})
    provider_meta = company_snapshot.get("provider_metadata", {})
    fundamentals_summary = company_snapshot.get("fundamentals_summary") or {}
    missing_fields = set(company_snapshot.get("missing_fields", []))
    is_mock = company_snapshot.get("is_mock", True)

    source_tier = provider_meta.get("source_tier", "T6_model_estimate")
    provider_name = provider_meta.get("provider_name", "unknown")

    # ── Determine available data ──────────────────────────────────────────
    available: list[str] = []
    missing: list[str] = []

    # Identity fields
    for field_path in _SNAPSHOT_FIELDS:
        section, key = field_path.split(".")
        obj = identity if section == "identity" else profile
        val = obj.get(key)
        if val is not None and field_path not in missing_fields:
            available.append(field_path)
        else:
            missing.append(field_path)

    # Price data
    if price_summary.get("available"):
        for fp in _PRICE_FIELDS:
            available.append(fp)
    else:
        for fp in _PRICE_FIELDS:
            missing.append(fp)

    # Financial fundamentals — Phase 19.3: recognize categories now sourced
    # from the fundamentals_summary (SEC EDGAR XBRL for free_real / EODHD for
    # the paid stack). A category is available only when its value is not None.
    available_categories: set[str] = set()
    for key, cat in _FUNDAMENTALS_TO_CATEGORY.items():
        if fundamentals_summary.get(key) is not None:
            available_categories.add(cat)

    for cat in _EXPECTED_FINANCIAL_CATEGORIES:
        path = f"financials.{cat}"
        if cat in available_categories:
            available.append(path)
        else:
            missing.append(path)

    # ── Source tier accounting ────────────────────────────────────────────
    source_tier_summary: dict[str, int] = {
        "T1_primary_filing": 0,
        "T2_regulator_or_gov": 0,
        "T3_industry_specialist": 0,
        "T4_quality_media": 0,
        "T5_api_aggregator": 0,
        "T6_model_estimate": 0,
    }
    if source_tier in source_tier_summary:
        source_tier_summary[source_tier] += len(source_ids) if source_ids else 1

    # ── Quality notes ─────────────────────────────────────────────────────
    if is_mock:
        data_quality_notes.append(
            "All data is MOCK — generated by MockFinancialDataProvider. "
            "Not real financial data. Not investment advice."
        )
        warnings.append(
            "Mock provider active: all values are synthetic demo data."
        )

    if source_tier in _AGGREGATOR_TIERS:
        data_quality_notes.append(
            f"Provider '{provider_name}' is classified as {source_tier}. "
            "Aggregator data should be cross-verified against primary filings (T1/T2) "
            "before use in any investment analysis."
        )
        warnings.append(
            f"Source tier {source_tier}: all identity and price data from {provider_name} "
            "is aggregator quality. Missing primary filing sources."
        )
    elif source_tier in _PRIMARY_TIERS:
        data_quality_notes.append(
            f"Provider '{provider_name}' is classified as {source_tier}. "
            "Identity data from a primary or regulatory source."
        )

    if price_summary.get("available"):
        # Name the PRICE FEED's own provider/tier — never the company-level one.
        price = resolve_price_provenance(company_snapshot)
        data_quality_notes.append(
            f"{price.evidence_sentence()} "
            f"Quality: {price.price_data_quality or 'unknown'}."
        )
    else:
        warnings.append("No price history available from provider.")

    missing_fundamentals_count = len(
        [m for m in missing if m.startswith("financials.")]
    )
    if missing_fundamentals_count > 0:
        warnings.append(
            f"{missing_fundamentals_count} financial fundamental categories missing. "
            "Filings, XBRL data or a fundamentals-capable provider required."
        )

    # ── Build summary narrative ───────────────────────────────────────────
    ticker = identity.get("ticker") or "N/A"
    legal_name = identity.get("legal_name") or "Unknown"
    country = identity.get("country_domicile") or "unknown country"
    sector = profile.get("sector") or "unknown sector"
    currency = profile.get("reporting_currency") or "unknown currency"

    header = (
        f"{legal_name} ({ticker}) — {sector}, {country}, reporting in {currency}. "
        f"Provider: {provider_name} ({source_tier}). "
        f"Available data points: {len(available)}. "
        f"Missing data categories: {len(missing)} "
        f"(including {missing_fundamentals_count} financial fundamental fields). "
    )

    if available_categories:
        financial_context_summary = header + _summarize_fundamentals(
            fundamentals_summary, legal_name
        )
    else:
        financial_context_summary = (
            header + "No financial fundamentals sourced at this phase — "
            "identity and price data only."
        )
    if is_mock:
        financial_context_summary += " [MOCK DATA — not real financial information]"

    return FinancialDataAgentOutput(
        available_financial_data=available,
        missing_financial_data=missing,
        data_quality_notes=data_quality_notes,
        source_tier_summary=source_tier_summary,
        financial_context_summary=financial_context_summary,
        warnings=warnings,
    )


def financial_data_agent_output_to_dict(output: FinancialDataAgentOutput) -> dict:
    """Serialize output to a plain dict suitable for JSON storage.

    Phase B: goes through the typed :class:`FinancialDataSummary` contract, so
    the canonical field names and the DERIVED counts are produced in exactly
    one place. Previously this emitted the agent's own spelling while every
    downstream reader asked for a different one and silently got ``0`` / ``[]``
    — which rendered "Fundamentals Available = Yes" beside "Available Count =
    0" in a report whose council was quoting real FY2026 SEC statement facts.
    Renaming a field on the agent output now fails at
    ``FinancialDataSummary.from_agent_output`` instead of degrading a report.
    """
    from app.schemas.evidence_state import FinancialDataSummary

    return FinancialDataSummary.from_agent_output(output).to_payload()
