"""
NewsCatalystProvider — abstract interface and free implementations.

Provides recent news and catalyst events for internal research candidate
qualification. All outputs are internal; none are published directly.

STRICT PROHIBITION:
  - No BUY/SELL/HOLD/WATCH signals from news events.
  - No price impact estimates or upside/downside percentages.
  - News is context only — human review is required before any publication.

Implementations:
  SecEdgar8KProvider  — free, no API key. U.S. companies only.
                        Fetches recent 8-K filing events from SEC EDGAR submissions.
  NullNewsCatalystProvider — returns an empty list with a no-news warning.
                             Used when no news provider is configured.

To add a paid provider (NewsData.io, GDELT, etc.):
  1. Subclass NewsCatalystProvider.
  2. Implement get_recent_events().
  3. Register in get_news_catalyst_provider().

Source tiers:
  SecEdgar8KProvider: T2_regulator_or_gov (8-K is a regulated filing)
  NullNewsCatalystProvider: not applicable
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

_EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_USER_AGENT = "InvestingBuddy-Research-Platform/1.0 (contact: research@investingbuddy.com)"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class CatalystEvent:
    """A single news or filing event relevant to a company."""
    event_date: str               # ISO date string (YYYY-MM-DD)
    event_type: str               # e.g. "8-K", "news_article", "earnings_release"
    title: str
    source: str                   # provider or publication name
    source_tier: str              # T1–T6 label
    source_url: str | None = None
    description: str | None = None
    filing_accession: str | None = None  # SEC accession number if applicable
    is_mock: bool = False


@dataclass
class NewsCatalystResult:
    """Output of a news/catalyst provider call."""
    ticker: str
    events: list[CatalystEvent] = field(default_factory=list)
    provider: str = "unknown"
    retrieved_at: str = ""
    warnings: list[str] = field(default_factory=list)
    is_mock: bool = False

    def __post_init__(self) -> None:
        if not self.retrieved_at:
            self.retrieved_at = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class NewsCatalystProvider(ABC):
    """Abstract base for news and catalyst event providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def get_recent_events(
        self,
        ticker: str,
        cik: str | None = None,
        max_events: int = 10,
    ) -> NewsCatalystResult: ...


# ---------------------------------------------------------------------------
# Null provider
# ---------------------------------------------------------------------------


class NullNewsCatalystProvider(NewsCatalystProvider):
    """
    Placeholder provider returned when no news source is configured.

    Returns an empty event list with a warning instead of raising an error.
    The workflow continues without news — partial data is acceptable.
    """

    @property
    def provider_name(self) -> str:
        return "null_news"

    async def get_recent_events(
        self,
        ticker: str,
        cik: str | None = None,
        max_events: int = 10,
    ) -> NewsCatalystResult:
        return NewsCatalystResult(
            ticker=ticker.upper(),
            events=[],
            provider=self.provider_name,
            warnings=[
                "No news provider configured. "
                "Set NEWS_CATALYST_PROVIDER=sec_8k or configure a news API key. "
                "News/catalyst context unavailable for this analysis."
            ],
        )


# ---------------------------------------------------------------------------
# SEC EDGAR 8-K provider
# ---------------------------------------------------------------------------


class SecEdgar8KProvider(NewsCatalystProvider):
    """
    Free news/catalyst provider using SEC EDGAR 8-K filings.

    Fetches recent filings from the company's submissions endpoint and
    returns 8-K filings as CatalystEvent objects.

    Source tier: T2_regulator_or_gov
    API key: None required.
    Rate limit: 10 requests/second (SEC Fair Access Policy).
    U.S. companies only (SEC-registered entities).

    8-K forms covered:
      - 8-K:       Current report (major corporate events)
      - 8-K/A:     Amendment to current report
    """

    @property
    def provider_name(self) -> str:
        return "sec_edgar_8k"

    async def get_recent_events(
        self,
        ticker: str,
        cik: str | None = None,
        max_events: int = 10,
    ) -> NewsCatalystResult:
        """
        Fetch recent 8-K filings from SEC EDGAR for a U.S. company.

        Args:
            ticker:     Company ticker symbol (for labelling).
            cik:        SEC CIK (required). If None, returns a no-cik warning.
            max_events: Maximum number of 8-K events to return.

        Returns:
            NewsCatalystResult with CatalystEvent list (may be empty with warnings).
        """
        if not cik:
            return NewsCatalystResult(
                ticker=ticker.upper(),
                events=[],
                provider=self.provider_name,
                warnings=[
                    f"SEC CIK not provided for {ticker}. "
                    "Cannot fetch 8-K filings without a CIK. "
                    "Add a CIK to the company record to enable SEC 8-K events."
                ],
            )

        padded_cik = str(int(cik)).zfill(10)
        url = _EDGAR_SUBMISSIONS_URL.format(cik=padded_cik)

        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT},
                timeout=15.0,
            ) as client:
                response = await client.get(url)
                if response.status_code == 404:
                    return NewsCatalystResult(
                        ticker=ticker.upper(),
                        events=[],
                        provider=self.provider_name,
                        warnings=[
                            f"SEC EDGAR submissions not found for CIK {padded_cik} "
                            f"(ticker: {ticker}). CIK may be incorrect."
                        ],
                    )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            return NewsCatalystResult(
                ticker=ticker.upper(),
                events=[],
                provider=self.provider_name,
                warnings=[
                    f"SEC EDGAR 8-K fetch failed for {ticker} (CIK {padded_cik}): {exc}. "
                    "Continuing without news/catalyst context."
                ],
            )

        events = _parse_8k_filings(data, ticker, padded_cik, max_events)

        if not events:
            return NewsCatalystResult(
                ticker=ticker.upper(),
                events=[],
                provider=self.provider_name,
                warnings=[
                    f"No recent 8-K filings found for {ticker} (CIK {padded_cik}). "
                    "The company may not have filed recently or data may be delayed."
                ],
            )

        return NewsCatalystResult(
            ticker=ticker.upper(),
            events=events,
            provider=self.provider_name,
        )


def _parse_8k_filings(
    submissions_data: dict[str, Any],
    ticker: str,
    padded_cik: str,
    max_events: int,
) -> list[CatalystEvent]:
    """
    Parse SEC EDGAR submissions JSON and extract recent 8-K filings.

    Pure function — no network calls. Suitable for offline unit testing.
    """
    recent_filings: dict[str, Any] = submissions_data.get("filings", {}).get("recent", {})

    forms: list[str] = recent_filings.get("form", [])
    dates: list[str] = recent_filings.get("filingDate", [])
    descriptions: list[str] = recent_filings.get("primaryDocument", [])
    accessions: list[str] = recent_filings.get("accessionNumber", [])
    report_dates: list[str] = recent_filings.get("reportDate", [])

    events: list[CatalystEvent] = []

    for i, form in enumerate(forms):
        if form not in ("8-K", "8-K/A"):
            continue

        filing_date = dates[i] if i < len(dates) else ""
        accession = accessions[i] if i < len(accessions) else ""
        primary_doc = descriptions[i] if i < len(descriptions) else ""
        report_date = report_dates[i] if i < len(report_dates) else filing_date

        # Build EDGAR viewer URL
        accession_clean = accession.replace("-", "")
        source_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{int(padded_cik)}/{accession_clean}/{primary_doc}"
            if accession_clean and primary_doc
            else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={padded_cik}&type=8-K"
        )

        events.append(
            CatalystEvent(
                event_date=report_date or filing_date,
                event_type=form,
                title=f"SEC {form} filing — {ticker.upper()} — {filing_date}",
                source="SEC EDGAR",
                source_tier="T2_regulator_or_gov",
                source_url=source_url,
                description=(
                    f"{form} current report filed {filing_date}. "
                    "Document: " + (primary_doc or "see filing index.")
                ),
                filing_accession=accession,
                is_mock=False,
            )
        )

        if len(events) >= max_events:
            break

    return events


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_news_catalyst_provider(name: str | None = None) -> NewsCatalystProvider:
    """
    Resolve a news/catalyst provider by name.

    Reads NEWS_CATALYST_PROVIDER from environment if name not supplied.
    Defaults to NullNewsCatalystProvider when env var is absent.

    Supported values:
      "sec_8k"  → SecEdgar8KProvider (free, no key, U.S. only)
      "null"    → NullNewsCatalystProvider (no events, returns warning)
    """
    provider_name = name or os.environ.get("NEWS_CATALYST_PROVIDER", "null")
    if provider_name == "sec_8k":
        return SecEdgar8KProvider()
    return NullNewsCatalystProvider()
