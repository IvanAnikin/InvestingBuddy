"""
Phase 22: Historical Outcome Provider — offline abstraction for backtesting.

Provides historical price data for internal backtesting evaluation.

IMPORTANT CONSTRAINTS:
  - CI/tests use MockHistoricalOutcomeProvider only — no network calls.
  - No EODHD, Stooq, or other live API keys required in CI.
  - No investment recommendations are produced.
  - Historical data is used only to evaluate past research quality.
  - Absolute/relative returns are historical evaluation metrics, NOT forecasts.

Provider selection:
  Set BACKTEST_PROVIDER=mock (default) for offline/CI use.
  Live providers (eodhd, stooq) can be added later without breaking the
  interface — but must not be required in CI.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date

from app.schemas.backtesting import HistoricalOutcome

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class HistoricalOutcomeProvider(ABC):
    """Abstract interface for fetching historical price data for backtesting."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short identifier for this provider (e.g. 'mock', 'eodhd')."""

    @abstractmethod
    async def get_outcome(
        self,
        ticker: str,
        exchange: str | None,
        start_date: date,
        end_date: date,
        benchmark_symbol: str | None = None,
    ) -> HistoricalOutcome:
        """Fetch historical outcome for a ticker over a date range.

        Returns a HistoricalOutcome with available price data.
        Must never raise — return data_available=False with warnings on failure.
        """


# ---------------------------------------------------------------------------
# Mock provider — deterministic, no network, suitable for CI
# ---------------------------------------------------------------------------

# Fixed seed prices for well-known tickers used in tests
_MOCK_SEED_PRICES: dict[str, float] = {
    "VOW3": 102.50,
    "AAPL": 178.25,
    "MSFT": 415.10,
    "SPY": 450.00,
    "UNKNOWN": 50.00,
}
_MOCK_DEFAULT_PRICE = 100.0
_MOCK_ANNUAL_RETURN = 0.08  # 8% annual — deterministic for tests


def _mock_price(ticker: str, days_offset: int = 0) -> float:
    """Return a deterministic mock price for a ticker at a given day offset."""
    base = _MOCK_SEED_PRICES.get(ticker.upper(), _MOCK_DEFAULT_PRICE)
    # Apply a fixed daily drift — deterministic, no randomness
    daily_rate = _MOCK_ANNUAL_RETURN / 365
    return round(base * ((1 + daily_rate) ** days_offset), 4)


def _mock_volatility(ticker: str) -> float:
    """Return a simple deterministic volatility proxy."""
    # Derived from ticker name length for determinism in tests
    return round(0.10 + (len(ticker) % 5) * 0.02, 4)


class MockHistoricalOutcomeProvider(HistoricalOutcomeProvider):
    """Deterministic offline mock provider for CI and unit tests.

    Returns fixture-based price history from a fixed seed.
    No network calls. No API keys required.
    All returned values are clearly labelled as mock data.
    """

    @property
    def provider_name(self) -> str:
        return "mock"

    async def get_outcome(
        self,
        ticker: str,
        exchange: str | None,
        start_date: date,
        end_date: date,
        benchmark_symbol: str | None = None,
    ) -> HistoricalOutcome:
        horizon_days = (end_date - start_date).days
        start_price = _mock_price(ticker, 0)
        end_price = _mock_price(ticker, horizon_days)

        absolute_return = (end_price - start_price) / start_price if start_price else None

        benchmark_start = benchmark_end = benchmark_return = relative_return = None
        if benchmark_symbol:
            benchmark_start = _mock_price(benchmark_symbol, 0)
            benchmark_end = _mock_price(benchmark_symbol, horizon_days)
            if benchmark_start:
                benchmark_return = (benchmark_end - benchmark_start) / benchmark_start
            if absolute_return is not None and benchmark_return is not None:
                relative_return = absolute_return - benchmark_return

        return HistoricalOutcome(
            ticker=ticker,
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
            horizon_days=horizon_days,
            benchmark_symbol=benchmark_symbol,
            start_price=start_price,
            end_price=end_price,
            benchmark_start_price=benchmark_start,
            benchmark_end_price=benchmark_end,
            absolute_return=round(absolute_return, 6) if absolute_return is not None else None,
            benchmark_return=round(benchmark_return, 6) if benchmark_return is not None else None,
            relative_return=round(relative_return, 6) if relative_return is not None else None,
            volatility_proxy=_mock_volatility(ticker),
            max_drawdown_proxy=round(-abs(_mock_volatility(ticker)) * 0.5, 4),
            data_available=True,
            missing_data=[],
            warnings=["MOCK DATA — not real market data. For internal evaluation only."],
            provider_name=self.provider_name,
            source_tier="mock",
            data_quality="mock",
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_historical_outcome_provider(provider_name: str = "mock") -> HistoricalOutcomeProvider:
    """Return the appropriate provider by name.

    Defaults to MockHistoricalOutcomeProvider for CI safety.
    Live providers must be added explicitly and must never be required in CI.
    """
    if provider_name == "mock":
        return MockHistoricalOutcomeProvider()
    # Future: add "eodhd", "stooq" adapters here, guarded by env checks
    logger.warning(
        "Unknown BACKTEST_PROVIDER=%r — falling back to mock provider.", provider_name
    )
    return MockHistoricalOutcomeProvider()
