"""
CompanyIdentifierResolver — resolves user-supplied identifiers to canonical EODHD symbols.

Users may supply:
  - A stock ticker ("AAPL")
  - A company name ("Apple Inc")
  - A ticker + exchange ("AAPL", "NASDAQ")
  - An EODHD-format symbol ("AAPL.US")
  - A CIK (SEC EDGAR reference) or LEI (GLEIF reference) — future

The resolver searches EODHD (when configured) and returns a ResolvedIdentifier.
When the result is ambiguous (multiple plausible matches), warnings are populated
and is_ambiguous is set to True so callers can surface the issue rather than
silently choosing the wrong company.

If EODHD is not configured, the resolver performs a best-effort structural parse
of the input (ticker + exchange suffix detection) with low confidence.

This service must NOT silently choose an ambiguous company.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


@dataclass
class ResolvedIdentifier:
    """
    Result of a company identifier lookup.

    provider_symbol is the EODHD-format symbol (TICKER.EXCHANGE), e.g. "AAPL.US".
    When is_ambiguous=True, the caller should surface warnings and ask for
    clarification rather than proceeding with the first match.
    """

    canonical_ticker: str
    provider_symbol: str        # e.g. "AAPL.US", "VOW3.XETRA"
    exchange: str
    company_name: str
    country: str | None
    provider_confidence: float  # 0.0–1.0; high when ticker+exchange exact match
    warnings: list[str] = field(default_factory=list)
    is_ambiguous: bool = False
    source: str = "eodhd"


@dataclass
class ResolvedIdentifierList:
    """Returned when the query matched multiple candidates."""

    query: str
    candidates: list[ResolvedIdentifier]
    is_ambiguous: bool
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


_EODHD_SYMBOL_RE = re.compile(r"^([A-Z0-9\-\.]{1,20})\.([A-Z]{1,10})$")
_TICKER_ONLY_RE = re.compile(r"^[A-Z0-9\-\.]{1,20}$")

# Common exchange name → EODHD suffix mapping (mirrors eodhd_provider.py)
_EXCHANGE_TO_SUFFIX: dict[str, str] = {
    "NASDAQ": "US",
    "NYSE": "US",
    "AMEX": "US",
    "LSE": "LSE",
    "XETRA": "XETRA",
    "OSE": "OL",
    "STO": "ST",
    "TSX": "TO",
    "TSXV": "V",
    "ASX": "AU",
    "US": "US",
}


class CompanyIdentifierResolver:
    """
    Resolves company identifiers to canonical EODHD symbols.

    When EODHD is configured (EODHD_API_KEY present), uses the EODHD search
    endpoint. When not configured, performs structural pattern matching with
    low confidence so callers know live resolution was not possible.
    """

    def __init__(self) -> None:
        self._api_key: str = os.environ.get("EODHD_API_KEY", "")

    @property
    def eodhd_available(self) -> bool:
        return bool(self._api_key)

    async def resolve(
        self,
        query: str,
        exchange: str | None = None,
        provider: str = "eodhd",
    ) -> ResolvedIdentifierList:
        """
        Resolve a company query to one or more candidates.

        Args:
            query:    Ticker, EODHD symbol, or company name.
            exchange: Optional exchange hint (helps disambiguate tickers).
            provider: Provider to use for search (currently only "eodhd" supported).

        Returns:
            ResolvedIdentifierList with 0–N candidates and ambiguity flag.
            Callers must check is_ambiguous and surface warnings when True.
        """
        query = query.strip()
        warnings: list[str] = []

        if not self.eodhd_available:
            warnings.append(
                "EODHD_API_KEY is not configured — live resolution unavailable. "
                "Performing structural pattern matching only (low confidence)."
            )
            candidates = self._structural_resolve(query, exchange, warnings)
            return ResolvedIdentifierList(
                query=query,
                candidates=candidates,
                is_ambiguous=len(candidates) != 1,
                warnings=warnings,
            )

        # EODHD available — use search endpoint
        candidates = await self._eodhd_resolve(query, exchange, warnings)
        return ResolvedIdentifierList(
            query=query,
            candidates=candidates,
            is_ambiguous=len(candidates) > 1,
            warnings=warnings,
        )

    # ── Private helpers ──────────────────────────────────────────────────────

    def _structural_resolve(
        self,
        query: str,
        exchange: str | None,
        warnings: list[str],
    ) -> list[ResolvedIdentifier]:
        """
        Offline structural resolution — no network calls.

        Handles:
          - EODHD-format symbols ("AAPL.US")
          - Ticker-only uppercase strings ("AAPL")
          - Anything else → low-confidence name match (no real lookup)
        """
        upper = query.upper()

        # Already in EODHD format (TICKER.EXCHANGE)?
        m = _EODHD_SYMBOL_RE.match(upper)
        if m:
            ticker, suffix = m.group(1), m.group(2)
            return [
                ResolvedIdentifier(
                    canonical_ticker=ticker,
                    provider_symbol=upper,
                    exchange=suffix,
                    company_name=ticker,
                    country=None,
                    provider_confidence=0.6,
                    warnings=["No live lookup — EODHD_API_KEY not configured. Confidence is low."],
                    is_ambiguous=False,
                    source="structural_parse",
                )
            ]

        # Looks like a pure ticker?
        if _TICKER_ONLY_RE.match(upper):
            suffix = (
                _EXCHANGE_TO_SUFFIX.get(exchange.upper(), exchange.upper()) if exchange else "US"
            )
            symbol = f"{upper}.{suffix}"
            warnings.append(
                f"Assumed exchange suffix '{suffix}' — live lookup not performed. "
                "Verify the exchange is correct before proceeding."
            )
            return [
                ResolvedIdentifier(
                    canonical_ticker=upper,
                    provider_symbol=symbol,
                    exchange=suffix,
                    company_name=upper,
                    country=None,
                    provider_confidence=0.4,
                    warnings=["Structural parse only — low confidence. "
                              "Configure EODHD_API_KEY for live name search."],
                    is_ambiguous=False,
                    source="structural_parse",
                )
            ]

        # Free-text name — cannot resolve without a live API
        warnings.append(
            f"Cannot resolve '{query}' without EODHD_API_KEY"
            " — company name search requires live API."
        )
        return []

    async def _eodhd_resolve(
        self,
        query: str,
        exchange: str | None,
        warnings: list[str],
    ) -> list[ResolvedIdentifier]:
        """Resolve using EODHD /search endpoint."""
        # Avoid importing EodhdProvider at module level to keep this file importable
        # in tests that mock the provider.
        from app.integrations.providers.eodhd_provider import (
            EodhdNotFoundError,
            EodhdProviderError,
        )

        upper = query.upper()

        # Exact EODHD-format symbol — high confidence, skip search
        m = _EODHD_SYMBOL_RE.match(upper)
        if m:
            ticker, suffix = m.group(1), m.group(2)
            return [
                ResolvedIdentifier(
                    canonical_ticker=ticker,
                    provider_symbol=upper,
                    exchange=suffix,
                    company_name=ticker,
                    country=None,
                    provider_confidence=0.85,
                    warnings=["Resolved from symbol format — company name not looked up."],
                    is_ambiguous=False,
                    source="eodhd_symbol_parse",
                )
            ]

        # Use EODHD search
        from app.integrations.providers.eodhd_provider import EodhdProvider

        provider = EodhdProvider()
        try:
            results: list[dict[str, Any]] = await provider.search_symbol(query, limit=10)
        except EodhdNotFoundError:
            warnings.append(f"EODHD search returned no results for '{query}'.")
            return []
        except EodhdProviderError as exc:
            warnings.append(f"EODHD search error: {exc}")
            return []

        if not results:
            warnings.append(f"EODHD search returned no results for '{query}'.")
            return []

        candidates = []
        for row in results:
            code = str(row.get("Code", "")).strip()
            ex = str(row.get("Exchange", "")).strip()
            name = str(row.get("Name", "")).strip()
            country = str(row.get("Country", "")).strip() or None
            if not code or not ex:
                continue

            # If exchange hint provided, prefer exact match
            confidence = self._score_result(query, exchange, code, ex, name)
            symbol = f"{code}.{ex}"
            candidates.append(
                ResolvedIdentifier(
                    canonical_ticker=code,
                    provider_symbol=symbol,
                    exchange=ex,
                    company_name=name,
                    country=country,
                    provider_confidence=confidence,
                    is_ambiguous=False,
                    source="eodhd_search",
                )
            )

        # Sort by confidence descending
        candidates.sort(key=lambda c: c.provider_confidence, reverse=True)

        # Detect ambiguity: if top 2 candidates are within 0.1 confidence of each other
        if len(candidates) >= 2:
            top, second = candidates[0], candidates[1]
            if (top.provider_confidence - second.provider_confidence) < 0.1:
                warnings.append(
                    f"Ambiguous result: top candidates"
                    f" '{top.provider_symbol}' ({top.company_name})"
                    f" and '{second.provider_symbol}' ({second.company_name})"
                    " have similar confidence."
                    " Specify the exchange or EODHD symbol format"
                    " (e.g. AAPL.US) to disambiguate."
                )
                for c in candidates:
                    c.is_ambiguous = True

        return candidates

    @staticmethod
    def _score_result(
        query: str,
        exchange_hint: str | None,
        code: str,
        exchange: str,
        name: str,
    ) -> float:
        """Compute a confidence score for a search result given the query context."""
        score = 0.5

        q_upper = query.upper()
        code_upper = code.upper()
        name_upper = name.upper()

        # Exact ticker match
        if q_upper == code_upper:
            score += 0.35
        elif name_upper.startswith(q_upper):
            score += 0.20
        elif q_upper in name_upper:
            score += 0.10

        # Exchange hint match
        if exchange_hint:
            hint_upper = exchange_hint.upper()
            if hint_upper == exchange.upper():
                score += 0.15
            elif _EXCHANGE_TO_SUFFIX.get(hint_upper, hint_upper) == exchange.upper():
                score += 0.15
            else:
                score -= 0.10

        return min(max(score, 0.0), 1.0)
