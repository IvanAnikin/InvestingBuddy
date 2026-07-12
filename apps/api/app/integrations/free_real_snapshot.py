"""
FreeRealSnapshotComposer — combines free real data sources into a unified snapshot.

Orchestrates:
  1. Company identity — from DB record (CompanyIdentity) or SEC EDGAR profile
  2. SEC EDGAR fundamentals — T2_regulator_or_gov XBRL facts (U.S. companies only)
  3. Price history — EODHD /eod (free plan) or Stooq (free, no key)
  4. Trend signals — internal momentum labels from TrendSignalEngine
  5. Source metadata and warnings

Design rules:
  - is_mock is False when at least one real provider returned data.
  - Partial success is acceptable — one missing source adds a warning, not a failure.
  - No BUY/SELL/HOLD/WATCH labels are ever produced.
  - No price targets, fair values, or upside percentages.
  - All outputs are internal and require human review before publication.

Outputs:
  FreeRealSnapshot dataclass with all available fields populated and a
  warnings list for anything unavailable.

Supported provider combinations:
  provider_stack="free_real"
    → Stooq prices (no key) + SEC EDGAR fundamentals (no key)
  provider_stack="eodhd_price_only"
    → EODHD /eod prices (free key) + SEC EDGAR fundamentals (no key)
  provider_stack="sec_only"
    → SEC EDGAR fundamentals only (no price data)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    FundamentalsData,
    PriceHistoryData,
)
from app.integrations.trend_signal_engine import TrendSignalResult, compute_trend_signals

# ---------------------------------------------------------------------------
# Company identity stub (from DB or resolution)
# ---------------------------------------------------------------------------


@dataclass
class CompanyIdentity:
    """Minimal company identity sourced from the InvestingBuddy DB."""
    ticker: str
    legal_name: str
    exchange: str | None = None
    country_domicile: str | None = None
    sector: str | None = None
    industry: str | None = None
    sec_cik: str | None = None
    reporting_currency: str | None = None


# ---------------------------------------------------------------------------
# Snapshot output
# ---------------------------------------------------------------------------


@dataclass
class FreeRealSnapshot:
    """
    Unified snapshot assembled from free real data providers.

    is_mock=False when at least one real provider contributed data.
    All fields are optional — absent data is noted in warnings, not errors.
    """
    ticker: str
    legal_name: str

    # Identity
    exchange: str | None = None
    country_domicile: str | None = None
    sector: str | None = None
    industry: str | None = None
    sec_cik: str | None = None
    reporting_currency: str | None = None

    # Price data
    price_history: PriceHistoryData | None = None
    price_provider: str | None = None
    price_source_tier: str | None = None

    # Fundamentals
    fundamentals: FundamentalsData | None = None
    fundamentals_provider: str | None = None
    fundamentals_source_tier: str | None = None

    # Trend signals (internal only)
    trend_signals: TrendSignalResult | None = None

    # Composite state
    is_mock: bool = True  # set False when any real provider succeeds
    provider_stack: str = "unknown"
    composed_at: str = ""
    warnings: list[str] = field(default_factory=list)
    # Which sub-providers actually contributed data (e.g. ["sec_edgar_fundamentals", "stooq"])
    contributing_providers: list[str] = field(default_factory=list)

    # Raw profile from provider (enrichment)
    provider_profile: CompanyProfileData | None = None

    def __post_init__(self) -> None:
        if not self.composed_at:
            self.composed_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict suitable for JSON storage / agent state."""
        price_summary: dict | None = None
        if self.price_history:
            pts = self.price_history.price_points
            price_summary = {
                "num_points": len(pts),
                "latest_date": pts[-1].date if pts else None,
                "latest_close": pts[-1].close if pts else None,
                "earliest_date": pts[0].date if pts else None,
                "source_tier": self.price_source_tier,
                "provider": self.price_provider,
                "is_mock": self.price_history.meta.is_mock,
            }

        fund_summary: dict | None = None
        if self.fundamentals:
            fund_summary = {
                "num_datapoints": len(self.fundamentals.datapoints),
                "source_tier": self.fundamentals_source_tier,
                "provider": self.fundamentals_provider,
                "is_mock": self.fundamentals.meta.is_mock,
                "datapoints": [
                    {
                        "field_name": dp.field_name,
                        "value": dp.value,
                        "unit": dp.unit,
                        "as_of": dp.as_of,
                        "source_tier": dp.source_tier.value
                        if hasattr(dp.source_tier, "value")
                        else dp.source_tier,
                        "data_quality": dp.data_quality.value
                        if hasattr(dp.data_quality, "value")
                        else dp.data_quality,
                    }
                    for dp in self.fundamentals.datapoints
                ],
            }

        trend_summary: dict | None = None
        if self.trend_signals:
            ts = self.trend_signals
            trend_summary = {
                "momentum_label": ts.momentum_label,
                "return_1m": ts.return_1m,
                "return_3m": ts.return_3m,
                "return_6m": ts.return_6m,
                "pct_above_ma50": ts.pct_above_ma50,
                "pct_above_ma200": ts.pct_above_ma200,
                "relative_strength": ts.relative_strength,
                "source_tier": ts.source_tier,
                "data_warnings": ts.data_warnings,
            }

        return {
            "ticker": self.ticker,
            "legal_name": self.legal_name,
            "exchange": self.exchange,
            "country_domicile": self.country_domicile,
            "sector": self.sector,
            "industry": self.industry,
            "sec_cik": self.sec_cik,
            "reporting_currency": self.reporting_currency,
            "is_mock": self.is_mock,
            "provider_stack": self.provider_stack,
            "composed_at": self.composed_at,
            "contributing_providers": self.contributing_providers,
            "price_history": price_summary,
            "fundamentals": fund_summary,
            "trend_signals": trend_summary,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Price provider fallback warning surfacing (Phase 19.2.1)
# ---------------------------------------------------------------------------


def summarize_price_provider_warning(price_data: PriceHistoryData | None) -> str | None:
    """
    Surface a concise, factual provider warning from a price fetch's metadata note.

    FreeRealProvider records the Stooq→EODHD fallback reason in
    ``price_data.meta.note``. Before Phase 19.2.1 that reason stayed buried in the
    price metadata and never reached the report's provider-warnings section, so
    operators could not see when a price fallback had happened.

    This helper lifts that reason into a short internal warning suitable for the
    ``provider_warnings`` list. Wording is internal and factual — it does not
    overstate data reliability and contains no secrets or credentials.

    Returns None when the note carries no fallback / failure signal (e.g. Stooq
    succeeded directly), so normal successful fetches add no noise.
    """
    if price_data is None:
        return None
    note = (price_data.meta.note or "").strip()
    if not note:
        return None
    lowered = note.lower()

    # Both price providers failed — no usable price history at all.
    if "no usable price history" in lowered:
        return "No usable price history available; trend signals unavailable."

    # Stooq failed or returned nothing and the EODHD price-only fallback was used.
    stooq_failed = "stooq" in lowered and (
        "fallback" in lowered
        or "falling back" in lowered
        or "unavailable" in lowered
        or "0 price points" in lowered
    )
    if stooq_failed:
        if price_data.price_points:
            return "Stooq price provider unavailable; used EODHD price-only fallback."
        return (
            "Stooq price provider unavailable; EODHD price-only fallback returned no data."
        )
    return None


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


async def compose_free_real_snapshot(
    identity: CompanyIdentity,
    price_data: PriceHistoryData | None = None,
    fundamentals_data: FundamentalsData | None = None,
    benchmark_prices: PriceHistoryData | None = None,
    provider_stack: str = "free_real",
    extra_warnings: list[str] | None = None,
) -> FreeRealSnapshot:
    """
    Assemble a FreeRealSnapshot from pre-fetched provider data.

    This function is pure — no network calls. Callers (workflow nodes or
    API handlers) are responsible for fetching data from providers and
    passing it here.

    Args:
        identity:           Company identity from DB or resolver.
        price_data:         Optional PriceHistoryData from Stooq or EODHD /eod.
        fundamentals_data:  Optional FundamentalsData from SEC EDGAR XBRL.
        benchmark_prices:   Optional benchmark PriceHistoryData for relative strength.
        provider_stack:     Label for which stack was used (free_real, eodhd_price_only, …).
        extra_warnings:     Additional warnings from the caller (e.g. provider errors).

    Returns:
        FreeRealSnapshot with is_mock=False when at least one real source provided data.
    """
    warnings: list[str] = list(extra_warnings or [])
    has_real_data = False

    # ── Price data ──────────────────────────────────────────────────────────
    price_provider: str | None = None
    price_source_tier: str | None = None

    if price_data is not None:
        price_provider = price_data.meta.provider_name
        price_source_tier = (
            price_data.meta.source_tier.value
            if hasattr(price_data.meta.source_tier, "value")
            else str(price_data.meta.source_tier)
        )
        if not price_data.meta.is_mock:
            has_real_data = True
        if not price_data.price_points:
            warnings.append(
                f"Price provider '{price_provider}' returned 0 price points "
                f"for {identity.ticker}."
            )
        # Phase 19.2.1: surface the Stooq→EODHD fallback reason carried in meta.note
        # so it reaches the report's Provider Warnings section, not just price meta.
        price_fallback_warning = summarize_price_provider_warning(price_data)
        if price_fallback_warning:
            warnings.append(price_fallback_warning)
    else:
        warnings.append(
            f"No price data available for {identity.ticker}. "
            "Configure EODHD_API_KEY for /eod prices or use Stooq (no key required)."
        )

    # ── Fundamentals ────────────────────────────────────────────────────────
    fundamentals_provider: str | None = None
    fundamentals_source_tier: str | None = None

    if fundamentals_data is not None:
        fundamentals_provider = fundamentals_data.meta.provider_name
        fundamentals_source_tier = (
            fundamentals_data.meta.source_tier.value
            if hasattr(fundamentals_data.meta.source_tier, "value")
            else str(fundamentals_data.meta.source_tier)
        )
        if not fundamentals_data.meta.is_mock:
            has_real_data = True
        if not fundamentals_data.datapoints:
            warnings.append(
                f"Fundamentals provider '{fundamentals_provider}' returned 0 datapoints "
                f"for {identity.ticker}."
            )
    else:
        if identity.country_domicile in (None, "US"):
            if identity.sec_cik:
                warnings.append(
                    f"No SEC EDGAR fundamentals for {identity.ticker} "
                    f"(CIK {identity.sec_cik}) — fetch failed or was skipped."
                )
            else:
                warnings.append(
                    f"No SEC CIK available for {identity.ticker}. "
                    "SEC EDGAR fundamentals unavailable. "
                    "Add a CIK to the company record to enable XBRL fundamentals."
                )
        else:
            warnings.append(
                f"{identity.ticker} is not a U.S. company — "
                "SEC EDGAR fundamentals not applicable. "
                "Consider EODHD paid plan for international fundamentals."
            )

    # ── Trend signals ───────────────────────────────────────────────────────
    trend_signals: TrendSignalResult | None = None
    if price_data is not None and price_data.price_points:
        trend_signals = compute_trend_signals(price_data, benchmark_prices)
        if trend_signals.data_warnings:
            warnings.extend(trend_signals.data_warnings)
    else:
        warnings.append(
            f"Trend signals not computable for {identity.ticker} — no price data."
        )

    # ── Contributing providers ──────────────────────────────────────────────
    contributing: list[str] = []
    if fundamentals_data is not None and not fundamentals_data.meta.is_mock:
        p = fundamentals_data.meta.provider_name
        if p and p not in contributing:
            contributing.append(p)
    if price_data is not None and not price_data.meta.is_mock:
        p = price_data.meta.provider_name
        if p and p not in contributing:
            contributing.append(p)
    if trend_signals is not None:
        if "trend_signal_engine" not in contributing:
            contributing.append("trend_signal_engine")

    # De-duplicate warnings while preserving order — the price fallback reason can
    # arrive both from the caller (extra_warnings) and from meta.note surfacing.
    warnings = list(dict.fromkeys(warnings))

    return FreeRealSnapshot(
        ticker=identity.ticker,
        legal_name=identity.legal_name,
        exchange=identity.exchange,
        country_domicile=identity.country_domicile,
        sector=identity.sector,
        industry=identity.industry,
        sec_cik=identity.sec_cik,
        reporting_currency=identity.reporting_currency,
        price_history=price_data,
        price_provider=price_provider,
        price_source_tier=price_source_tier,
        fundamentals=fundamentals_data,
        fundamentals_provider=fundamentals_provider,
        fundamentals_source_tier=fundamentals_source_tier,
        trend_signals=trend_signals,
        is_mock=not has_real_data,
        provider_stack=provider_stack,
        warnings=warnings,
        contributing_providers=contributing,
    )
