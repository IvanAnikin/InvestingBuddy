"""
Phase 24 — News provider abstraction.

Defines the ``NewsProvider`` interface plus URL/title normalisation and
deduplication helpers shared by all news implementations. Concrete providers
(free/optional, env-key, or mock) live in ``free_news_provider.py``.

Source tiers:
  A generic news aggregator / search API is ``T5_api_aggregator`` by default.
  A provider may map a specific well-known publisher to ``T4_quality_media`` or
  ``T3_industry_specialist``, but aggregators must NEVER be upgraded to T1/T2 —
  only a company-owned source (press release) or a regulator (SEC) earns those.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from app.integrations.financial_data_provider import SourceTier
from app.schemas.catalyst import NewsItem

# Source-tier strength ranking (lower rank = stronger evidence).
_TIER_RANK: dict[str, int] = {
    SourceTier.T1_primary_filing.value: 1,
    SourceTier.T2_regulator_or_gov.value: 2,
    SourceTier.T3_industry_specialist.value: 3,
    SourceTier.T4_quality_media.value: 4,
    SourceTier.T5_api_aggregator.value: 5,
    SourceTier.T6_model_estimate.value: 6,
}

_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|mc_|ref|ref_src)", re.IGNORECASE)


class NewsProvider(ABC):
    """Abstract base for company news providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def search_company_news(
        self,
        ticker: str,
        company_name: str | None = None,
        lookback_days: int = 90,
        max_items: int = 20,
    ) -> list[NewsItem]:
        """Return normalised NewsItems (may be empty). Must never raise."""


def normalize_url(url: str | None) -> str:
    """Lower-case host, drop fragments and tracking query params, strip trailing /."""
    if not url:
        return ""
    raw = url.strip()
    raw = raw.split("#", 1)[0]
    if "?" in raw:
        base, query = raw.split("?", 1)
        kept = [
            p
            for p in query.split("&")
            if p and not _TRACKING_PARAMS.match(p.split("=", 1)[0])
        ]
        raw = base + ("?" + "&".join(kept) if kept else "")
    raw = raw.rstrip("/")
    # Normalise scheme + host casing without touching the path.
    m = re.match(r"^(https?://)([^/]+)(.*)$", raw, re.IGNORECASE)
    if m:
        return m.group(1).lower() + m.group(2).lower() + m.group(3)
    return raw.lower()


def normalize_title(title: str | None) -> str:
    """Collapse whitespace and punctuation for fuzzy title dedup."""
    if not title:
        return ""
    lowered = title.lower()
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def _tier_rank(tier: str) -> int:
    return _TIER_RANK.get(tier, 99)


def dedupe_news_items(items: list[NewsItem]) -> list[NewsItem]:
    """
    Collapse duplicate news items.

    Duplicates are detected by normalised URL, then by normalised title + date.
    When duplicates collide, the item with the strongest source tier is kept
    (never upgrading an aggregator to a primary/regulator tier).
    """
    best: dict[str, NewsItem] = {}
    order: list[str] = []

    for item in items:
        url_key = normalize_url(item.url)
        title_key = normalize_title(item.headline)
        date_key = (item.published_at or "")[:10]
        key = url_key or f"{title_key}|{date_key}"
        if not key:
            continue

        existing = best.get(key)
        if existing is None:
            best[key] = item
            order.append(key)
        elif _tier_rank(item.source_tier) < _tier_rank(existing.source_tier):
            best[key] = item

    return [best[k] for k in order]
