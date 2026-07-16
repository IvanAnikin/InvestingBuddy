"""
Phase 24 — Catalyst discovery orchestration service.

Runs the SEC recent-filings provider, the company press-release provider and an
optional news provider; normalises, deduplicates, classifies, summarises and
returns a single ``CatalystDiscoveryResult``.

Robustness contract:
  - No provider failure crashes company analysis. Every provider is wrapped so a
    timeout / parse error / missing CIK becomes a warning, never an exception.
  - "No catalysts found" is a valid, explicit result (coverage_quality reflects
    it), not an error.
  - All external free text is neutralised before it reaches any report artifact
    (see ``CatalystEvent.to_report_dict`` / ``neutralize_forbidden_terms``).

Performance / safety:
  - Short provider timeouts, capped event count, no broad crawling.
  - No external calls happen in tests (providers are injected/mocked).
"""

from __future__ import annotations

import logging

from app.integrations.financial_data_provider import SourceTier
from app.integrations.providers.company_press_release_provider import (
    CompanyPressReleaseProvider,
)
from app.integrations.providers.free_news_provider import get_news_provider
from app.integrations.providers.news_provider_base import (
    NewsProvider,
    dedupe_news_items,
    normalize_title,
)
from app.integrations.providers.sec_recent_filings_provider import (
    SecRecentFilingsProvider,
)
from app.schemas.catalyst import (
    CatalystCoverageStatus,
    CatalystDiscoveryResult,
    CatalystEvent,
    NewsItem,
    make_catalyst_event_id,
    summarize_events,
)
from app.services.catalyst_classifier import apply_classification

logger = logging.getLogger(__name__)


def _news_item_to_event(
    item: NewsItem,
    ticker: str,
    company_name: str | None,
    normalized_event_type: str,
    multi_source: bool,
) -> CatalystEvent:
    """Convert a normalised NewsItem into a classified CatalystEvent."""
    event = CatalystEvent(
        id=make_catalyst_event_id(
            ticker, normalized_event_type, item.published_at, item.url or item.headline
        ),
        ticker=ticker.upper(),
        company_name=company_name,
        event_date=item.published_at,
        source_name=item.source_name or item.provider_name,
        source_url=item.url,
        source_tier=item.source_tier,
        provider_name=item.provider_name,
        headline=item.headline,
        summary=item.summary,
        raw_event_type=normalized_event_type,
        normalized_event_type=normalized_event_type,
        related_document_url=item.url,
    )
    return apply_classification(event, multi_source=multi_source)


def _multi_source_title_keys(items: list[NewsItem]) -> set[str]:
    """Return title keys reported by >=2 distinct providers (multi-source)."""
    by_key: dict[str, set[str]] = {}
    for it in items:
        key = normalize_title(it.headline)
        if not key:
            continue
        by_key.setdefault(key, set()).add(it.provider_name)
    return {k for k, providers in by_key.items() if len(providers) >= 2}


async def discover_catalysts(
    *,
    ticker: str,
    exchange: str | None = None,
    company_name: str | None = None,
    cik: str | None = None,
    website: str | None = None,
    lookback_days: int = 90,
    max_events: int = 20,
    include_sec: bool = True,
    include_news: bool = True,
    include_press_releases: bool = True,
    sec_provider: SecRecentFilingsProvider | None = None,
    news_provider: NewsProvider | None = None,
    press_release_provider: CompanyPressReleaseProvider | None = None,
) -> CatalystDiscoveryResult:
    """Discover source-backed catalysts for a company. Never raises."""
    ticker_u = ticker.upper()
    warnings: list[str] = []
    missing_sources: list[str] = []

    filing_events: list[CatalystEvent] = []
    press_items: list[NewsItem] = []
    news_items: list[NewsItem] = []

    # ── SEC recent filings (T2) ──────────────────────────────────────────
    if include_sec:
        provider = sec_provider or SecRecentFilingsProvider()
        try:
            sec_result = await provider.get_recent_events(
                ticker_u,
                cik=cik,
                company_name=company_name,
                lookback_days=lookback_days,
                max_events=max_events,
            )
            filing_events = sec_result.events
            warnings.extend(sec_result.warnings)
            if not filing_events:
                missing_sources.append("sec_recent_filings")
        except Exception as exc:  # defensive
            warnings.append(f"SEC recent filings provider error (non-fatal): {exc}")
            missing_sources.append("sec_recent_filings")
    else:
        missing_sources.append("sec_recent_filings")

    # ── Company press releases (T1, company-owned primary source) ────────
    if include_press_releases:
        pr_provider = press_release_provider or CompanyPressReleaseProvider()
        try:
            pr_result = await pr_provider.get_press_releases(
                ticker_u,
                company_name=company_name,
                website=website,
                lookback_days=lookback_days,
                max_items=max_events,
            )
            press_items = pr_result.items
            warnings.extend(pr_result.warnings)
            if not press_items:
                missing_sources.append("company_press_release")
        except Exception as exc:  # defensive
            warnings.append(f"Company press-release provider error (non-fatal): {exc}")
            missing_sources.append("company_press_release")
    else:
        missing_sources.append("company_press_release")

    # ── Optional news provider (T5 aggregator) ───────────────────────────
    if include_news:
        n_provider = news_provider or get_news_provider()
        try:
            news_items = await n_provider.search_company_news(
                ticker_u,
                company_name=company_name,
                lookback_days=lookback_days,
                max_items=max_events,
            )
        except Exception as exc:  # defensive
            warnings.append(f"News provider error (non-fatal): {exc}")
            news_items = []
        if not news_items:
            missing_sources.append("news_provider")
            warnings.append(
                "News provider not configured or returned no results. "
                "Catalyst coverage relies on SEC filings and any company "
                "press-release source. Set NEWS_PROVIDER_NAME + NEWS_API_KEY to "
                "add aggregator news context."
            )
    else:
        missing_sources.append("news_provider")

    # ── Dedup + multi-source detection across news + press releases ──────
    combined_items = press_items + news_items
    multi_keys = _multi_source_title_keys(combined_items)
    deduped = dedupe_news_items(combined_items)

    press_release_events: list[CatalystEvent] = []
    news_events: list[CatalystEvent] = []
    for item in deduped:
        multi = normalize_title(item.headline) in multi_keys
        is_press = item.source_tier == SourceTier.T1_primary_filing.value
        event = _news_item_to_event(
            item,
            ticker_u,
            company_name,
            normalized_event_type="press_release" if is_press else "news_article",
            multi_source=multi,
        )
        if is_press:
            press_release_events.append(event)
        else:
            news_events.append(event)

    # ── Aggregate + summarise ────────────────────────────────────────────
    all_events = filing_events + press_release_events + news_events
    # Stable ordering: newest event first (by event/filing date).
    all_events.sort(
        key=lambda e: (e.event_date or e.filing_date or ""), reverse=True
    )
    all_events = all_events[:max_events]

    summary = summarize_events(all_events, lookback_days)

    source_summary: dict[str, int] = {}
    for ev in all_events:
        source_summary[ev.source_tier] = source_summary.get(ev.source_tier, 0) + 1

    coverage_quality = summary.catalyst_coverage_status
    if not all_events and not include_sec and not include_news and not include_press_releases:
        coverage_quality = CatalystCoverageStatus.provider_unavailable.value

    return CatalystDiscoveryResult(
        ticker=ticker_u,
        company_name=company_name,
        lookback_days=lookback_days,
        events=all_events,
        filing_events=filing_events,
        news_events=news_events,
        press_release_events=press_release_events,
        summary=summary,
        warnings=warnings,
        source_summary=source_summary,
        missing_sources=sorted(set(missing_sources)),
        coverage_quality=coverage_quality,
        human_review_required=True,
    )
