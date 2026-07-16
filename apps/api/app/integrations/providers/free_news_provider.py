"""
Phase 24 — News provider implementations.

Phase 24 keeps a live news provider OPTIONAL and non-blocking. SEC recent
filings produce the first real catalyst signal; news is additive context.

Implementations:
  NullNewsProvider        — returned when no news provider is configured. Emits
                            no items; the discovery service records a
                            "news provider not configured" warning.
  StaticNewsProvider      — returns a fixed list (used for mocks/tests).
  EnvConfiguredNewsProvider — generic env-key JSON provider. Disabled unless
                            NEWS_API_KEY + NEWS_API_BASE_URL are set. Never
                            raises; any error yields an empty list.

No paid dependency is required. No live external call happens in CI (the null
provider is the default and env vars are absent).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.integrations.financial_data_provider import SourceTier
from app.integrations.providers.news_provider_base import NewsProvider
from app.schemas.catalyst import NewsItem


class NullNewsProvider(NewsProvider):
    """No-op provider used when no news source is configured."""

    @property
    def provider_name(self) -> str:
        return "null_news"

    async def search_company_news(
        self,
        ticker: str,
        company_name: str | None = None,
        lookback_days: int = 90,
        max_items: int = 20,
    ) -> list[NewsItem]:
        return []


class StaticNewsProvider(NewsProvider):
    """Returns a fixed list of NewsItems — for offline mocks and tests."""

    def __init__(self, items: list[NewsItem], name: str = "static_news") -> None:
        self._items = items
        self._name = name

    @property
    def provider_name(self) -> str:
        return self._name

    async def search_company_news(
        self,
        ticker: str,
        company_name: str | None = None,
        lookback_days: int = 90,
        max_items: int = 20,
    ) -> list[NewsItem]:
        return list(self._items[:max_items])


class EnvConfiguredNewsProvider(NewsProvider):
    """
    Generic env-key news provider.

    Reads a JSON search endpoint whose shape is configured out-of-band. Field
    mapping is best-effort against common keys (title/headline, url/link,
    publishedAt/date, source/publisher, description/summary). Returns [] when
    unconfigured or on any error — never blocks the workflow.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        name: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("NEWS_API_KEY")
        self._base_url = base_url or os.environ.get("NEWS_API_BASE_URL")
        self._name = name or os.environ.get("NEWS_PROVIDER_NAME", "env_news")

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self._base_url)

    async def search_company_news(
        self,
        ticker: str,
        company_name: str | None = None,
        lookback_days: int = 90,
        max_items: int = 20,
    ) -> list[NewsItem]:
        if not self.is_configured:
            return []
        try:
            params = {
                "q": company_name or ticker,
                "ticker": ticker,
                "apiKey": self._api_key,
                "pageSize": str(max_items),
            }
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(self._base_url, params=params)  # type: ignore[arg-type]
                resp.raise_for_status()
                payload = resp.json()
        except Exception:
            return []
        return self._parse_payload(payload, max_items)

    def _parse_payload(self, payload: Any, max_items: int) -> list[NewsItem]:
        articles = []
        if isinstance(payload, dict):
            for key in ("articles", "results", "data", "items", "news"):
                if isinstance(payload.get(key), list):
                    articles = payload[key]
                    break
        elif isinstance(payload, list):
            articles = payload

        items: list[NewsItem] = []
        for art in articles[:max_items]:
            if not isinstance(art, dict):
                continue
            headline = art.get("title") or art.get("headline") or art.get("name")
            if not headline:
                continue
            source = art.get("source")
            source_name = (
                source.get("name") if isinstance(source, dict)
                else (source or art.get("publisher"))
            )
            items.append(
                NewsItem(
                    headline=str(headline),
                    url=art.get("url") or art.get("link"),
                    published_at=art.get("publishedAt") or art.get("published_at")
                    or art.get("date"),
                    source_name=source_name,
                    summary=art.get("description") or art.get("summary"),
                    provider_name=self._name,
                    source_tier=SourceTier.T5_api_aggregator.value,
                )
            )
        return items


def get_news_provider(name: str | None = None) -> NewsProvider:
    """
    Resolve a news provider.

    Reads NEWS_PROVIDER_NAME from the environment when ``name`` is None. Any
    value other than a configured env provider yields the NullNewsProvider, so
    the default (no configuration) is safe and offline.
    """
    provider_name = name or os.environ.get("NEWS_PROVIDER_NAME")
    if provider_name and provider_name not in ("null", "none", ""):
        env_provider = EnvConfiguredNewsProvider(name=provider_name)
        if env_provider.is_configured:
            return env_provider
    return NullNewsProvider()
