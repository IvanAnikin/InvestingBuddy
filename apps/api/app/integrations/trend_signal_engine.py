"""
TrendSignalEngine — internal-only momentum and price trend metrics.

Computes technical signals from PriceHistoryData for internal research
candidate classification. Outputs internal labels only.

STRICT PROHIBITION:
  This module must NEVER output:
    - BUY, SELL, HOLD, WATCH
    - Price targets, fair values, or upside percentages
    - Investment recommendations of any kind

Internal momentum labels:
  positive_momentum_candidate  — price trend is positive across multiple lookback periods
  neutral_momentum             — mixed or flat signals
  negative_momentum            — price trend is negative across multiple lookback periods
  insufficient_price_history   — fewer than 30 trading days available

All outputs are tagged as T6_model_estimate (computed from T5 price data).

Inputs:
  PriceHistoryData (price_points must be sorted chronologically, oldest first)

Outputs:
  TrendSignalResult dataclass:
    momentum_label    — one of the four internal labels above
    return_1m         — 1-month (≈21 trading day) price return, % or None
    return_3m         — 3-month (≈63 trading day) price return, % or None
    return_6m         — 6-month (≈126 trading day) price return, % or None
    pct_above_ma50    — % deviation from 50-day MA (positive = above) or None
    pct_above_ma200   — % deviation from 200-day MA (positive = above) or None
    relative_strength — simple RS vs benchmark if provided, or None
    data_warnings     — list of non-fatal data quality notes
    computed_at       — UTC ISO timestamp of computation
    source_tier       — always T6_model_estimate
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import mean

from app.integrations.financial_data_provider import PriceHistoryData

# ── Internal momentum label constants ────────────────────────────────────────

LABEL_POSITIVE = "positive_momentum_candidate"
LABEL_NEUTRAL = "neutral_momentum"
LABEL_NEGATIVE = "negative_momentum"
LABEL_INSUFFICIENT = "insufficient_price_history"

# Trading-day approximations for calendar periods
_DAYS_1M = 21
_DAYS_3M = 63
_DAYS_6M = 126
_DAYS_MA50 = 50
_DAYS_MA200 = 200

# Minimum price points required before computing any signal
_MIN_POINTS_FOR_SIGNALS = 30


@dataclass
class TrendSignalResult:
    momentum_label: str
    return_1m: float | None = None
    return_3m: float | None = None
    return_6m: float | None = None
    pct_above_ma50: float | None = None
    pct_above_ma200: float | None = None
    relative_strength: float | None = None
    data_warnings: list[str] = field(default_factory=list)
    computed_at: str = ""
    source_tier: str = "T6_model_estimate"

    def __post_init__(self) -> None:
        if not self.computed_at:
            self.computed_at = datetime.now(timezone.utc).isoformat()


def _period_return(closes: list[float], lookback: int) -> float | None:
    """Return the percentage change from `lookback` periods ago to today."""
    if len(closes) < lookback + 1:
        return None
    past = closes[-(lookback + 1)]
    current = closes[-1]
    if past == 0:
        return None
    return round((current / past - 1) * 100, 2)


def _simple_ma(closes: list[float], window: int) -> float | None:
    """Compute simple moving average over the last `window` values."""
    if len(closes) < window:
        return None
    return mean(closes[-window:])


def _pct_deviation(price: float, ma: float | None) -> float | None:
    """Percentage deviation of price above or below a moving average."""
    if ma is None or ma == 0:
        return None
    return round((price / ma - 1) * 100, 2)


def _classify_label(
    return_1m: float | None,
    return_3m: float | None,
    return_6m: float | None,
    pct_above_ma50: float | None,
    pct_above_ma200: float | None,
) -> str:
    """
    Assign an internal momentum label from available signals.

    Uses a simple majority vote across up to 5 signals:
      positive score: +1
      negative score: -1
      neutral: 0

    Returns:
      LABEL_POSITIVE if score > 0
      LABEL_NEGATIVE if score < 0
      LABEL_NEUTRAL otherwise (including ties)
    """
    score = 0
    signals_seen = 0

    for ret in (return_1m, return_3m, return_6m):
        if ret is not None:
            signals_seen += 1
            if ret > 2.0:
                score += 1
            elif ret < -2.0:
                score -= 1

    for dev in (pct_above_ma50, pct_above_ma200):
        if dev is not None:
            signals_seen += 1
            if dev > 0:
                score += 1
            elif dev < 0:
                score -= 1

    if signals_seen == 0:
        return LABEL_INSUFFICIENT

    if score > 0:
        return LABEL_POSITIVE
    if score < 0:
        return LABEL_NEGATIVE
    return LABEL_NEUTRAL


def compute_trend_signals(
    price_data: PriceHistoryData,
    benchmark_prices: PriceHistoryData | None = None,
) -> TrendSignalResult:
    """
    Compute internal trend signals from PriceHistoryData.

    Args:
        price_data:        OHLCV price history (sorted oldest→newest).
        benchmark_prices:  Optional benchmark (e.g. SPY) for relative strength.
                           If absent, relative_strength is None.

    Returns:
        TrendSignalResult with internal momentum label and metric breakdown.
        No BUY/SELL/HOLD/WATCH labels are ever emitted.
    """
    warnings: list[str] = []

    closes = [p.close for p in price_data.price_points if p.close is not None]

    if len(closes) < _MIN_POINTS_FOR_SIGNALS:
        return TrendSignalResult(
            momentum_label=LABEL_INSUFFICIENT,
            data_warnings=[
                f"Only {len(closes)} price points available; "
                f"minimum {_MIN_POINTS_FOR_SIGNALS} required for trend signals. "
                f"Ticker: {price_data.ticker}."
            ],
        )

    current_price = closes[-1]

    return_1m = _period_return(closes, _DAYS_1M)
    return_3m = _period_return(closes, _DAYS_3M)
    return_6m = _period_return(closes, _DAYS_6M)

    ma50 = _simple_ma(closes, _DAYS_MA50)
    ma200 = _simple_ma(closes, _DAYS_MA200)

    pct_above_ma50 = _pct_deviation(current_price, ma50)
    pct_above_ma200 = _pct_deviation(current_price, ma200)

    if return_1m is None:
        warnings.append(
            f"Fewer than {_DAYS_1M + 1} price points — 1M return not computable."
        )
    if return_3m is None:
        warnings.append(
            f"Fewer than {_DAYS_3M + 1} price points — 3M return not computable."
        )
    if return_6m is None:
        warnings.append(
            f"Fewer than {_DAYS_6M + 1} price points — 6M return not computable."
        )
    if pct_above_ma200 is None:
        warnings.append(
            f"Fewer than {_DAYS_MA200} price points — 200-day MA not computable."
        )

    # Relative strength: (asset return over common window) / (benchmark return)
    relative_strength: float | None = None
    if benchmark_prices is not None:
        bench_closes = [
            p.close for p in benchmark_prices.price_points if p.close is not None
        ]
        asset_rs_period = min(_DAYS_3M, len(closes) - 1, len(bench_closes) - 1)
        if asset_rs_period > 5:
            asset_ret = _period_return(closes, asset_rs_period)
            bench_ret = _period_return(bench_closes, asset_rs_period)
            if asset_ret is not None and bench_ret is not None and bench_ret != 0:
                relative_strength = round(asset_ret / bench_ret, 3)
        else:
            warnings.append("Insufficient benchmark history for relative strength.")

    label = _classify_label(
        return_1m, return_3m, return_6m, pct_above_ma50, pct_above_ma200
    )

    return TrendSignalResult(
        momentum_label=label,
        return_1m=return_1m,
        return_3m=return_3m,
        return_6m=return_6m,
        pct_above_ma50=pct_above_ma50,
        pct_above_ma200=pct_above_ma200,
        relative_strength=relative_strength,
        data_warnings=warnings,
    )
