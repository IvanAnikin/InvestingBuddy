"""
Bounded live fetchers for the read-only evidence-preview endpoint — Phase 29B.

These adapt the existing catalyst providers into the connector fetcher shape so
the admin evidence-preview endpoint can demonstrate *real* SEC / company-IR
evidence on demand. They are used ONLY by that endpoint and ONLY when
``source_connector_enabled`` is set; the deterministic report path never calls
them (it replays already-fetched data instead).

Safety properties:
  * No user-supplied URL is ever fetched. SEC uses the fixed ``data.sec.gov``
    host keyed by ticker/CIK; company IR uses the curated verified-issuer feed
    allowlist keyed by ticker. There is no open proxy and no SSRF surface.
  * Bounded by ``query.max_items``; providers never raise (they degrade to an
    empty result), and the connector wraps them in ``call_safe`` regardless.
  * Nothing here logs prompts, completions, or credentials.
"""

from __future__ import annotations

from typing import Any

from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.safe_web_fetcher import (
    ANNUAL_REPORT_KEYWORDS,
    SafeFetchResult,
    safe_fetch_page,
)


def _filing_dict(event: Any) -> dict[str, Any]:
    """Map a ``CatalystEvent`` (SEC filing) into a connector filing dict."""
    get = event.get if isinstance(event, dict) else lambda k, d=None: getattr(event, k, d)
    return {
        "form_type": get("form_type"),
        "title": get("headline") or get("form_type") or "SEC filing",
        "url": get("source_url") or get("related_document_url"),
        "filed_date": get("filing_date") or get("event_date"),
        "summary": get("summary") or get("headline"),
        "accession_number": get("accession_number"),
    }


def _press_dict(item: Any) -> dict[str, Any]:
    """Map a ``NewsItem`` (press release) into a connector press dict."""
    get = item.get if isinstance(item, dict) else lambda k, d=None: getattr(item, k, d)
    return {
        "headline": get("headline"),
        "url": get("url"),
        "published_at": get("published_at"),
        "summary": get("summary"),
        "source_name": get("source_name") or "Company IR / Newsroom",
        "media_url": get("media_url"),
    }


async def live_sec_filings_fetcher(
    company: CompanyContext, query: QueryContext
) -> list[dict[str, Any]]:
    """Fetch recent SEC filing metadata for a US issuer (bounded, never raises)."""
    if not company.ticker:
        return []
    from app.integrations.providers.sec_recent_filings_provider import (
        SecRecentFilingsProvider,
    )

    provider = SecRecentFilingsProvider()
    result = await provider.get_recent_events(
        ticker=company.ticker,
        cik=company.cik,
        company_name=company.company_name,
        lookback_days=query.lookback_days or 90,
        max_events=query.max_items,
        exchange=company.exchange,
    )
    return [_filing_dict(e) for e in result.events]


async def live_ir_press_fetcher(
    company: CompanyContext, query: QueryContext
) -> list[dict[str, Any]]:
    """Fetch issuer press releases from the curated verified-issuer feed only.

    Only a curated, verified issuer feed URL is used — never a URL supplied by
    the caller — so there is no arbitrary-URL fetch / SSRF surface.
    """
    if not company.ticker:
        return []
    from app.integrations.exchange_source_registry import get_curated_issuer_source
    from app.integrations.providers.company_press_release_provider import (
        CompanyPressReleaseProvider,
    )

    curated = get_curated_issuer_source(company.ticker)
    feed_urls = []
    website = None
    if curated:
        website = curated.website
        if curated.press_release_feed_url:
            feed_urls.append(curated.press_release_feed_url)
    if not feed_urls and not website:
        return []

    provider = CompanyPressReleaseProvider()
    result = await provider.get_press_releases(
        ticker=company.ticker,
        company_name=company.company_name,
        website=website,
        lookback_days=query.lookback_days or 90,
        max_items=query.max_items,
        feed_urls=feed_urls or None,
    )
    return [_press_dict(i) for i in result.items]


async def live_ir_page_fetcher(
    url: str,
    *,
    allowed_domains: tuple[str, ...],
    keywords: tuple[str, ...] = ANNUAL_REPORT_KEYWORDS,
    fallback_keywords: tuple[str, ...] = (),
) -> SafeFetchResult:
    """Bounded, SSRF-safe fetch of ONE allowlisted issuer page (preview path).

    The URL is never caller-supplied — it originates from the code-defined
    verified-issuer registry (or a link already extracted from an allowlisted
    page) and is re-checked against ``allowed_domains`` before the request. Never
    raises: every failure degrades to a ``SafeFetchResult`` with ``error`` set.
    """
    return await safe_fetch_page(
        url,
        allowed_domains=allowed_domains,
        keywords=keywords,
        fallback_keywords=fallback_keywords,
    )


__all__ = [
    "live_sec_filings_fetcher",
    "live_ir_press_fetcher",
    "live_ir_page_fetcher",
]
