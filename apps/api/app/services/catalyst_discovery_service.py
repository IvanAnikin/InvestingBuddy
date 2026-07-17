"""
Phase 24 / 24.1 — Catalyst discovery orchestration service.

Phase 24 ran SEC recent filings + a company press-release feed + an optional
news provider. Phase 24.1 adds, on top of that backbone:

  1. Company source discovery (website / IR / newsroom / press-release feeds)
     via a curated verified issuer registry, identity enrichment and (optional)
     a configured search provider.
  2. Exchange-aware, recommendation-free news query planning.
  3. A configurable real news/search provider (optional, non-blocking).
  4. Deterministic relevance scoring that separates COMPANY-specific catalysts
     from INDUSTRY/sector context.
  5. Source-class-aware coverage status (filings_only → limited/adequate/strong).

Robustness contract (unchanged):
  - No provider failure crashes company analysis. Every provider is wrapped so a
    timeout / parse error / missing CIK becomes a warning, never an exception.
  - "No catalysts found" is a valid, explicit result.
  - All external free text is neutralised before it reaches any report artifact.
  - No live external call happens in tests (providers are injected/mocked and
    source discovery + news search default to offline/null).

Safety: nothing here produces a recommendation, price target, fair value or
upside/downside. Industry context is NEVER treated as direct company evidence.
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
    CatalystCategory,
    CatalystCoverageStatus,
    CatalystDirection,
    CatalystDiscoveryResult,
    CatalystEvent,
    NewsItem,
    NewsProviderStatus,
    PressReleaseStatus,
    make_catalyst_event_id,
    summarize_events,
)
from app.schemas.company_sources import CompanySourceDiscoveryResult
from app.services.catalyst_classifier import apply_classification
from app.services.company_source_discovery_service import discover_company_sources
from app.services.news_query_planner import NewsSearchPlan, build_news_search_plan
from app.services.news_relevance_scorer import apply_relevance

logger = logging.getLogger(__name__)


def _news_item_to_event(
    item: NewsItem,
    ticker: str,
    company_name: str | None,
    normalized_event_type: str,
    multi_source: bool,
) -> CatalystEvent:
    """Convert a normalised, company-specific NewsItem into a classified event."""
    event = CatalystEvent(
        id=make_catalyst_event_id(
            ticker, normalized_event_type, item.published_at, item.url or item.headline
        ),
        ticker=ticker.upper(),
        company_name=company_name,
        event_date=item.published_at,
        source_name=item.source_name or item.provider_name,
        source_url=item.url,
        media_url=item.media_url,
        source_url_quality=_source_url_quality(item),
        source_tier=item.source_tier,
        provider_name=item.provider_name,
        headline=item.headline,
        summary=item.summary,
        raw_event_type=normalized_event_type,
        normalized_event_type=normalized_event_type,
        related_document_url=item.url,
        is_company_specific=True,
        is_industry_context=False,
        relevance_score=item.relevance_score,
        relevance_level=item.relevance_level,
        raw_query=item.raw_query,
        query_type=item.query_type,
    )
    return apply_classification(event, multi_source=multi_source)


def _source_url_quality(item: NewsItem) -> str:
    """Classify a news item's evidence-link quality (Phase 24.1.2)."""
    if item.url:
        return "canonical_article"
    if item.media_url:
        return "rejected_media_only"
    return "missing"


def _industry_item_to_event(
    item: NewsItem,
    ticker: str,
    company_name: str | None,
) -> CatalystEvent:
    """
    Convert an industry/sector-context NewsItem into an INDUSTRY event.

    Industry context is never a company catalyst: the category is forced to
    ``macro_sector`` and the direction to neutral/mixed so a positive-sounding
    sector headline can never become a positive COMPANY catalyst.
    """
    event = CatalystEvent(
        id=make_catalyst_event_id(
            ticker, "industry_news", item.published_at, item.url or item.headline
        ),
        ticker=ticker.upper(),
        company_name=company_name,
        event_date=item.published_at,
        source_name=item.source_name or item.provider_name,
        source_url=item.url,
        media_url=item.media_url,
        source_url_quality=_source_url_quality(item),
        source_tier=item.source_tier,
        provider_name=item.provider_name,
        headline=item.headline,
        summary=item.summary,
        raw_event_type="industry_news",
        normalized_event_type="industry_news",
        related_document_url=item.url,
        is_company_specific=False,
        is_industry_context=True,
        relevance_score=item.relevance_score,
        relevance_level=item.relevance_level,
        raw_query=item.raw_query,
        query_type=item.query_type,
    )
    classified = apply_classification(event, multi_source=False)
    # Force industry framing — not a company-specific positive/negative signal.
    direction = classified.catalyst_direction
    if direction in (
        CatalystDirection.positive.value,
        CatalystDirection.negative.value,
    ):
        direction = CatalystDirection.neutral.value
    return classified.model_copy(
        update={
            "catalyst_category": CatalystCategory.macro_sector.value,
            "catalyst_direction": direction,
            "is_company_specific": False,
            "is_industry_context": True,
        }
    )


def _multi_source_title_keys(items: list[NewsItem]) -> set[str]:
    """Return title keys reported by >=2 distinct providers (multi-source)."""
    by_key: dict[str, set[str]] = {}
    for it in items:
        key = normalize_title(it.headline)
        if not key:
            continue
        by_key.setdefault(key, set()).add(it.provider_name)
    return {k for k, providers in by_key.items() if len(providers) >= 2}


async def _run_query_plan(
    provider: NewsProvider,
    queries: list[tuple[str, str]],
    *,
    lookback_days: int,
    max_per_query: int,
    total_cap: int,
) -> list[NewsItem]:
    """Run a set of (query, query_type) pairs against a provider. Never raises."""
    collected: list[NewsItem] = []
    for query, query_type in queries:
        if len(collected) >= total_cap:
            break
        try:
            items = await provider.search(
                query,
                lookback_days=lookback_days,
                max_items=max_per_query,
                query_type=query_type,
            )
        except Exception as exc:  # defensive — a bad query never breaks discovery
            logger.debug("news query failed (non-fatal): %s", exc)
            continue
        for it in items:
            # Preserve query provenance if the provider did not set it.
            if it.raw_query is None or it.query_type is None:
                it = it.model_copy(
                    update={
                        "raw_query": it.raw_query or query,
                        "query_type": it.query_type or query_type,
                    }
                )
            collected.append(it)
    return collected[: total_cap * 2]


async def discover_catalysts(
    *,
    ticker: str,
    exchange: str | None = None,
    company_name: str | None = None,
    cik: str | None = None,
    website: str | None = None,
    sector: str | None = None,
    industry: str | None = None,
    country: str | None = None,
    sec_website: str | None = None,
    gleif_website: str | None = None,
    lookback_days: int = 90,
    news_lookback_days: int | None = None,
    max_events: int = 20,
    max_news_events: int = 12,
    max_industry_events: int = 8,
    include_sec: bool = True,
    include_news: bool = True,
    include_press_releases: bool = True,
    include_industry: bool = True,
    include_source_discovery: bool = False,
    sec_provider: SecRecentFilingsProvider | None = None,
    news_provider: NewsProvider | None = None,
    press_release_provider: CompanyPressReleaseProvider | None = None,
    source_discovery: CompanySourceDiscoveryResult | None = None,
) -> CatalystDiscoveryResult:
    """Discover source-backed catalysts for a company. Never raises."""
    ticker_u = ticker.upper()
    # News/press/industry use their own lookback window (env NEWS_LOOKBACK_DAYS via
    # the workflow); SEC filings keep ``lookback_days``.
    news_lb = news_lookback_days or lookback_days
    warnings: list[str] = []
    missing_sources: list[str] = []
    attempted: list[str] = []
    successful: list[str] = []
    source_statuses: dict[str, str] = {}
    # Phase 24.1.1 — precise press-release feed status.
    pr_status = PressReleaseStatus.not_discovered.value
    pr_feed_url: str | None = None
    pr_items_seen = 0
    pr_items_used = 0

    n_provider = news_provider or get_news_provider()

    # ── Company source discovery (Phase 24.1) ─────────────────────────────
    discovered_feed_urls: list[str] = []
    if include_source_discovery and source_discovery is None:
        attempted.append("company_source_discovery")
        try:
            source_discovery = await discover_company_sources(
                ticker=ticker_u,
                company_name=company_name,
                exchange=exchange,
                country=country,
                sector=sector,
                industry=industry,
                website=website,
                sec_website=sec_website,
                gleif_website=gleif_website,
                search_provider=n_provider,
            )
        except Exception as exc:  # defensive
            warnings.append(f"Company source discovery failed (non-fatal): {exc}")
            source_discovery = None
    elif source_discovery is not None:
        attempted.append("company_source_discovery")

    if source_discovery is not None:
        warnings.extend(source_discovery.warnings)
        discovered_feed_urls = source_discovery.candidate_feed_urls()
        website = website or source_discovery.company_website
        if source_discovery.has_verified_company_source:
            successful.append("company_source_discovery")
            source_statuses["company_source_discovery"] = "verified"
        else:
            source_statuses["company_source_discovery"] = "none"

    # ── Search plan (Phase 24.1) ──────────────────────────────────────────
    plan: NewsSearchPlan = build_news_search_plan(
        ticker=ticker_u,
        company_name=company_name,
        exchange=exchange,
        country=country,
        sector=sector,
        industry=industry,
        lookback_days=lookback_days,
        source_discovery=source_discovery,
    )

    filing_events: list[CatalystEvent] = []
    press_items: list[NewsItem] = []
    news_items: list[NewsItem] = []
    industry_items: list[NewsItem] = []

    # ── SEC recent filings (T2) ──────────────────────────────────────────
    if include_sec:
        attempted.append("sec_filings")
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
            if filing_events:
                successful.append("sec_filings")
            else:
                missing_sources.append("sec_recent_filings")
        except Exception as exc:  # defensive
            warnings.append(f"SEC recent filings provider error (non-fatal): {exc}")
            missing_sources.append("sec_recent_filings")
    else:
        missing_sources.append("sec_recent_filings")

    # ── Company press releases (T1, company-owned primary source) ────────
    if include_press_releases:
        attempted.append("company_press_release")
        pr_provider = press_release_provider or CompanyPressReleaseProvider()
        try:
            try:
                pr_result = await pr_provider.get_press_releases(
                    ticker_u,
                    company_name=company_name,
                    website=website,
                    lookback_days=news_lb,
                    max_items=max_events,
                    feed_urls=discovered_feed_urls or None,
                )
            except TypeError:
                # Injected/mock providers without the Phase 24.1 feed_urls kwarg.
                pr_result = await pr_provider.get_press_releases(
                    ticker_u,
                    company_name=company_name,
                    website=website,
                    lookback_days=news_lb,
                    max_items=max_events,
                )
            press_items = pr_result.items
            warnings.extend(pr_result.warnings)
            # Phase 24.1.2 — flag press items with no canonical article URL (a
            # media/image URL is NEVER used as evidence).
            no_canonical = sum(1 for it in press_items if not it.url)
            if no_canonical:
                warnings.append(
                    f"{no_canonical} press-release item(s) had no canonical article "
                    "URL; the associated media/image URL was NOT used as source "
                    "evidence."
                )
            # Phase 24.1.1 — precise status (fall back for older mock providers).
            pr_status = getattr(
                pr_result, "status", PressReleaseStatus.not_discovered.value
            )
            pr_feed_url = getattr(pr_result, "feed_url", None)
            pr_items_seen = getattr(pr_result, "items_seen", len(press_items))
            pr_items_used = getattr(pr_result, "items_used", len(press_items))
            if pr_items_used and pr_status == PressReleaseStatus.not_discovered.value:
                # Mock provider returned items without a status — treat as usable.
                pr_status = PressReleaseStatus.feed_discovered_with_items.value
            if press_items:
                successful.append("company_press_release")
            elif pr_status == PressReleaseStatus.not_discovered.value:
                # Only a genuine "no company source" state is a missing source.
                missing_sources.append("company_press_release")
            # feed_discovered_unreadable / _no_recent_items are NOT "missing" — a
            # source WAS discovered; the precise status + warning carry the nuance.
        except Exception as exc:  # defensive
            warnings.append(f"Company press-release provider error (non-fatal): {exc}")
            pr_status = PressReleaseStatus.feed_discovered_unreadable.value
            missing_sources.append("company_press_release")
        source_statuses["company_press_release"] = pr_status
    else:
        missing_sources.append("company_press_release")
        source_statuses["company_press_release"] = "skipped"

    # ── Configured news / search provider (T5 aggregator or mapped T4) ────
    news_configured = getattr(n_provider, "provider_name", "") != "null_news"
    news_status = NewsProviderStatus.not_configured.value
    if include_news:
        attempted.append("news_provider")
        news_items = await _run_query_plan(
            n_provider,
            plan.all_company_queries(),
            lookback_days=news_lb,
            max_per_query=plan.max_results_per_query,
            total_cap=max_news_events,
        )
        if news_items:
            successful.append("news_provider")
            news_status = NewsProviderStatus.results.value
        elif not news_configured:
            # No provider configured → a genuine missing source.
            news_status = NewsProviderStatus.not_configured.value
            missing_sources.append("news_provider")
            warnings.append(
                "News provider not configured. Catalyst coverage relies on SEC "
                "filings and any company press-release source. Set "
                "NEWS_PROVIDER_NAME='gdelt' (no key) or a keyed provider "
                "(NEWS_API_KEY + NEWS_API_BASE_URL) to add news context."
            )
        else:
            # Configured but returned nothing relevant — NOT a missing source.
            news_status = NewsProviderStatus.no_results.value
            warnings.append(
                f"News provider '{getattr(n_provider, 'provider_name', 'unknown')}' "
                "is configured but returned no company results in the lookback "
                "window."
            )
    else:
        missing_sources.append("news_provider")
        news_status = NewsProviderStatus.not_configured.value
    source_statuses["news_provider"] = news_status

    # ── Industry / sector context news (Phase 24.1) ───────────────────────
    if include_industry and include_news:
        attempted.append("industry_news")
        industry_items = await _run_query_plan(
            n_provider,
            plan.all_industry_queries(),
            lookback_days=news_lb,
            max_per_query=plan.max_results_per_query,
            total_cap=max_industry_events,
        )
        if industry_items:
            successful.append("industry_news")
        source_statuses["industry_news"] = (
            "results" if industry_items else "no_results"
        )

    # ── Relevance scoring + routing ───────────────────────────────────────
    # Press items are the company's own words → always company-specific (T1).
    for i, it in enumerate(press_items):
        press_items[i] = it.model_copy(
            update={"is_company_specific": True, "is_industry_context": False}
        )

    scored_company: list[NewsItem] = []
    routed_industry: list[NewsItem] = []
    dropped = 0
    for it in news_items + industry_items:
        scored = apply_relevance(
            it,
            company_name=company_name,
            ticker=ticker_u,
            sector=sector,
            industry=industry,
            lookback_days=news_lb,
        )
        level = scored.relevance_level
        if scored.is_industry_context:
            routed_industry.append(scored)
        elif scored.is_company_specific and level in ("high", "medium"):
            scored_company.append(scored)
        else:
            dropped += 1
    if dropped:
        warnings.append(
            f"{dropped} news/search result(s) were dropped as low-relevance or "
            "off-company (model-derived relevance filter)."
        )

    # ── Dedup + multi-source detection across news + press releases ──────
    combined_company_items = press_items + scored_company
    multi_keys = _multi_source_title_keys(combined_company_items)
    deduped_company = dedupe_news_items(combined_company_items)
    deduped_industry = dedupe_news_items(routed_industry)[:max_industry_events]

    press_release_events: list[CatalystEvent] = []
    news_events: list[CatalystEvent] = []
    for item in deduped_company:
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

    industry_events: list[CatalystEvent] = [
        _industry_item_to_event(item, ticker_u, company_name)
        for item in deduped_industry
    ]

    # Phase 24.1.1 — feed had recent items but all were deduped/dropped.
    if (
        pr_status == PressReleaseStatus.feed_discovered_with_items.value
        and not press_release_events
    ):
        pr_status = PressReleaseStatus.feed_discovered_items_filtered.value
        source_statuses["company_press_release"] = pr_status
        warnings.append(
            "Company press-release feed was parsed, but no items passed the "
            "dedup/relevance filters as distinct company events."
        )

    # ── Aggregate + summarise ────────────────────────────────────────────
    all_events = filing_events + press_release_events + news_events
    all_events.sort(key=lambda e: (e.event_date or e.filing_date or ""), reverse=True)
    all_events = all_events[:max_events]
    industry_events.sort(
        key=lambda e: (e.event_date or ""), reverse=True
    )

    summary = summarize_events(
        all_events, lookback_days, industry_events=industry_events
    )

    source_summary: dict[str, int] = {}
    for ev in all_events:
        source_summary[ev.source_tier] = source_summary.get(ev.source_tier, 0) + 1

    coverage_quality = summary.catalyst_coverage_status
    if (
        not all_events
        and not industry_events
        and not include_sec
        and not include_news
        and not include_press_releases
    ):
        coverage_quality = CatalystCoverageStatus.provider_unavailable.value

    return CatalystDiscoveryResult(
        ticker=ticker_u,
        company_name=company_name,
        lookback_days=lookback_days,
        events=all_events,
        filing_events=filing_events,
        news_events=news_events,
        press_release_events=press_release_events,
        industry_events=industry_events,
        summary=summary,
        warnings=warnings,
        source_summary=source_summary,
        missing_sources=sorted(set(missing_sources)),
        coverage_quality=coverage_quality,
        human_review_required=True,
        company_sources=(
            source_discovery.to_report_dict() if source_discovery is not None else None
        ),
        source_classes_attempted=sorted(set(attempted)),
        source_classes_successful=sorted(set(successful)),
        company_press_release_status=pr_status,
        company_press_release_feed_url=pr_feed_url,
        company_press_release_items_seen=pr_items_seen,
        company_press_release_items_used=pr_items_used,
        news_provider_status=news_status,
        source_statuses=source_statuses,
    )
