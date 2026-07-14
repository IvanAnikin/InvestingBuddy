"""
market_metrics_enrichment — Phase 19.4.

Derives market-based metrics for the free_real report from data the platform
already has: free price history (Stooq / EODHD /eod, T5) and the normalized SEC
EDGAR fundamentals (T2). No paid EODHD /fundamentals call is made.

Derived metrics (only when the required inputs are present):
  * latest_close / latest_close_date            (from price history, T5)
  * 52-week high / low + dates                  (from price history, T5)
  * shares_outstanding_mln                      (from SEC DEI, T2)
  * market_cap_mln = latest_close × shares      (derived, T6 estimate)
  * enterprise_value_mln = mktcap + debt − cash (derived, T6 estimate)
  * pe_ratio = mktcap / net_income  OR  price / diluted EPS  (derived, T6)
  * profit_margin / operating_margin / ROE      (mapped from annual SEC data)

Design rules (Phase 19.4):
  - Pure function — no network calls. Fully unit-testable with fixtures.
  - Never fabricate. EBITDA, EV/EBITDA and beta are NOT derived here and remain
    missing (with a warning) unless a defensible source exists.
  - Market cap / EV / P/E are labelled DERIVED ESTIMATES (T6_model_estimate)
    with their cited inputs — they are internal review aids, not official
    figures and never a valuation conclusion.
  - Annual SEC margins are labelled annual — never mislabelled TTM.
  - No BUY/SELL/HOLD/WATCH, price target, fair value or upside is produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.integrations.financial_data_provider import PriceHistoryData, SourceTier

_T2 = SourceTier.T2_regulator_or_gov.value
_T5 = SourceTier.T5_api_aggregator.value
_T6 = SourceTier.T6_model_estimate.value


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


@dataclass
class MarketMetrics:
    """
    Derived market metrics for a single company (Phase 19.4).

    Any field may be None when its inputs were unavailable — recorded in
    ``warnings``, never fabricated. ``source_tiers`` maps each populated field
    to the tier of the source(s) it derives from.
    """

    ticker: str
    currency: str = "USD"

    latest_close: float | None = None
    latest_close_date: str | None = None
    week52_high: float | None = None
    week52_low: float | None = None
    week52_high_date: str | None = None
    week52_low_date: str | None = None

    shares_outstanding_mln: float | None = None
    market_cap_mln: float | None = None
    enterprise_value_mln: float | None = None
    pe_ratio: float | None = None
    pe_basis: str | None = None

    profit_margin_pct: float | None = None
    operating_margin_annual_pct: float | None = None
    return_on_equity_annual_pct: float | None = None

    source_tiers: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Snapshot ``missing_fields`` entries this enrichment now satisfies.
    resolved_missing_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "currency": self.currency,
            "latest_close": self.latest_close,
            "latest_close_date": self.latest_close_date,
            "week52_high": self.week52_high,
            "week52_low": self.week52_low,
            "week52_high_date": self.week52_high_date,
            "week52_low_date": self.week52_low_date,
            "shares_outstanding_mln": self.shares_outstanding_mln,
            "market_cap_mln": self.market_cap_mln,
            "enterprise_value_mln": self.enterprise_value_mln,
            "pe_ratio": self.pe_ratio,
            "pe_basis": self.pe_basis,
            "profit_margin_pct": self.profit_margin_pct,
            "operating_margin_annual_pct": self.operating_margin_annual_pct,
            "return_on_equity_annual_pct": self.return_on_equity_annual_pct,
            "source_tiers": dict(self.source_tiers),
            "warnings": list(self.warnings),
            "resolved_missing_fields": list(self.resolved_missing_fields),
            "note": (
                "Market cap, enterprise value and P/E are DERIVED ESTIMATES "
                "(T6_model_estimate) from free T5 price data and T2 SEC "
                "fundamentals — internal review aids only, not official figures "
                "and not a valuation conclusion. Margins are annual (not TTM). "
                "EBITDA, EV/EBITDA and beta are not derived and remain missing."
            ),
        }


def derive_market_metrics(
    ticker: str,
    fundamentals_summary: dict | None,
    price_history: PriceHistoryData | None,
    reporting_currency: str = "USD",
) -> MarketMetrics:
    """
    Derive market metrics from free price history + normalized SEC fundamentals.

    Args:
        ticker:              Ticker symbol (labelling only).
        fundamentals_summary: The SEC-normalized fundamentals_summary dict from
                              the free_real snapshot (keys like
                              ``net_income_usd_m``, ``total_debt_usd_m``,
                              ``cash_and_equivalents_usd_m``,
                              ``shares_outstanding_mln``, ``eps_diluted``,
                              ``net_margin_pct`` …). May be None.
        price_history:        Free price history (Stooq / EODHD). May be None.
        reporting_currency:   Reporting currency label.

    Returns:
        MarketMetrics — always returned, never raises.
    """
    fs = fundamentals_summary or {}
    out = MarketMetrics(ticker=ticker.upper(), currency=reporting_currency or "USD")

    # ── Price / 52-week range ────────────────────────────────────────────
    points = price_history.price_points if price_history else []
    dated = [(p.date, p.close) for p in points if p.close is not None and p.date]
    if dated:
        dated.sort(key=lambda dc: dc[0])
        out.latest_close = round(float(dated[-1][1]), 4)
        out.latest_close_date = dated[-1][0]
        out.source_tiers["latest_close"] = _T5

        latest_dt = _parse_date(out.latest_close_date)
        window = dated
        if latest_dt is not None:
            cutoff = latest_dt - timedelta(days=365)
            window = [
                (d, c) for (d, c) in dated
                if (_parse_date(d) or latest_dt) >= cutoff
            ] or dated

        hi = max(window, key=lambda dc: dc[1])
        lo = min(window, key=lambda dc: dc[1])
        out.week52_high = round(float(hi[1]), 4)
        out.week52_low = round(float(lo[1]), 4)
        out.week52_high_date = hi[0]
        out.week52_low_date = lo[0]
        out.source_tiers["week52_high"] = _T5
        out.source_tiers["week52_low"] = _T5
        out.resolved_missing_fields += ["fundamentals.52_week_high", "fundamentals.52_week_low"]

        first_dt = _parse_date(window[0][0])
        if latest_dt is not None and first_dt is not None and (latest_dt - first_dt).days < 300:
            out.warnings.append(
                "52-week high/low computed over a price window shorter than one "
                "year — treat the range as partial."
            )
    else:
        out.warnings.append(
            "No price history available — latest close, 52-week range and any "
            "price-derived market metrics remain missing (not fabricated)."
        )

    # ── Shares outstanding (SEC DEI, T2) ─────────────────────────────────
    shares = fs.get("shares_outstanding_mln")
    if shares is not None:
        out.shares_outstanding_mln = round(float(shares), 4)
        out.source_tiers["shares_outstanding_mln"] = _T2
        out.resolved_missing_fields.append("fundamentals.shares_outstanding_mln")
    else:
        out.warnings.append(
            "Shares outstanding unavailable from SEC DEI facts — market "
            "capitalization cannot be derived and is not fabricated."
        )

    # ── Market cap = latest_close × shares (derived, T6) ─────────────────
    if out.latest_close is not None and out.shares_outstanding_mln is not None:
        out.market_cap_mln = round(out.latest_close * out.shares_outstanding_mln, 2)
        out.source_tiers["market_cap_mln"] = _T6
        out.resolved_missing_fields.append("fundamentals.market_cap_mln")
    elif out.latest_close is None or out.shares_outstanding_mln is None:
        out.warnings.append(
            "Market capitalization not derived — requires both latest close "
            "(T5 price) and shares outstanding (T2 SEC DEI)."
        )

    # ── Enterprise value = market cap + total debt − cash (derived, T6) ──
    total_debt = fs.get("total_debt_usd_m")
    cash = fs.get("cash_and_equivalents_usd_m")
    if out.market_cap_mln is not None and total_debt is not None and cash is not None:
        out.enterprise_value_mln = round(
            out.market_cap_mln + float(total_debt) - float(cash), 2
        )
        out.source_tiers["enterprise_value_mln"] = _T6
        out.resolved_missing_fields.append("fundamentals.enterprise_value_mln")
    else:
        missing_bits = []
        if out.market_cap_mln is None:
            missing_bits.append("market cap")
        if total_debt is None:
            missing_bits.append("total debt")
        if cash is None:
            missing_bits.append("cash & equivalents")
        out.warnings.append(
            "Enterprise value not derived — missing input(s): "
            f"{', '.join(missing_bits)}. Not fabricated."
        )

    # ── P/E: prefer market cap / net income, else price / diluted EPS ────
    net_income = fs.get("net_income_usd_m")
    eps_diluted = fs.get("eps_diluted")
    eps_basic = fs.get("eps_basic")
    eps = eps_diluted if eps_diluted is not None else eps_basic
    if out.market_cap_mln is not None and net_income not in (None, 0) and float(net_income) > 0:
        out.pe_ratio = round(out.market_cap_mln / float(net_income), 2)
        out.pe_basis = "market_cap / net_income (annual)"
        out.source_tiers["pe_ratio"] = _T6
        out.resolved_missing_fields.append("fundamentals.pe_ratio")
    elif out.latest_close is not None and eps not in (None, 0) and float(eps) > 0:
        out.pe_ratio = round(out.latest_close / float(eps), 2)
        out.pe_basis = "latest_close / diluted EPS (annual)"
        out.source_tiers["pe_ratio"] = _T6
        out.resolved_missing_fields.append("fundamentals.pe_ratio")
    else:
        out.warnings.append(
            "P/E not derived — requires market cap + positive net income, or "
            "latest close + positive diluted EPS. Not fabricated."
        )

    # ── Margins: map from annual SEC data (never labelled TTM) ───────────
    net_margin = fs.get("net_margin_pct")
    if net_margin is not None:
        out.profit_margin_pct = round(float(net_margin), 2)
        out.source_tiers["profit_margin_pct"] = _T2
        out.resolved_missing_fields.append("fundamentals.profit_margin")
    op_margin = fs.get("operating_margin_pct")
    if op_margin is not None:
        out.operating_margin_annual_pct = round(float(op_margin), 2)
        out.source_tiers["operating_margin_annual_pct"] = _T2
    roe = fs.get("return_on_equity_pct")
    if roe is not None:
        out.return_on_equity_annual_pct = round(float(roe), 2)
        out.source_tiers["return_on_equity_annual_pct"] = _T2

    if op_margin is not None or roe is not None:
        out.warnings.append(
            "Operating margin and ROE are annual SEC figures — not TTM. They are "
            "not mapped into any *_ttm field to avoid a false TTM label."
        )

    # ── Honest absences ──────────────────────────────────────────────────
    out.warnings.append(
        "EBITDA, EV/EBITDA and beta are not derived from the free SEC/price "
        "sources at this phase and remain missing (not fabricated)."
    )

    return out
