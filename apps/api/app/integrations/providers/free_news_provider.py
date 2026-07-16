"""
Phase 24 / 24.1 — News provider implementations.

A live news provider is OPTIONAL and non-blocking. SEC recent filings and any
company press-release feed provide the primary catalyst signal; configured news
search is additive context.

Implementations:
  NullNewsProvider          — default when nothing is configured. Emits no items.
  StaticNewsProvider        — returns a fixed list (mocks/tests).
  EnvConfiguredNewsProvider — generic env-key JSON search adapter. Disabled
                              unless NEWS_API_KEY + NEWS_API_BASE_URL are set.
  ConfigurableWebNewsProvider — alias of the generic env adapter (clearer name).
  GdeltNewsProvider         — no-key public GDELT 2.1 DOC API adapter. Optional,
                              bounded, mocked in CI; results are T5 aggregator.

Contract for every provider:
  - ``search(query)`` and ``search_company_news(...)`` never raise; any error
    (missing config, HTTP failure, rate limit, malformed body) yields [].
  - No paid dependency is required. No live external call happens in CI (the
    null provider is the default and env vars are absent).

Environment variables:
  NEWS_PROVIDER_NAME     provider selector: "gdelt" | any configured name | none
  NEWS_API_KEY           secret for env-key providers (never committed)
  NEWS_API_BASE_URL      search endpoint for the generic adapter
  NEWS_SEARCH_ENDPOINT   optional explicit search path (defaults to base url)
  NEWS_MAX_RESULTS       result cap (default 10)
  NEWS_LOOKBACK_DAYS     default lookback window (default 90)
  NEWS_TIMEOUT_SECONDS   per-request timeout (default 8)
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.integrations.exchange_source_registry import (
    extract_domain,
    resolve_media_tier,
)
from app.integrations.financial_data_provider import SourceTier
from app.integrations.providers.news_provider_base import NewsProvider
from app.schemas.catalyst import NewsItem


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


class NullNewsProvider(NewsProvider):
    """No-op provider used when no news source is configured."""

    @property
    def provider_name(self) -> str:
        return "null_news"

    async def search(
        self,
        query: str,
        *,
        lookback_days: int = 90,
        max_items: int = 20,
        query_type: str = "company",
    ) -> list[NewsItem]:
        return []

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

    async def search(
        self,
        query: str,
        *,
        lookback_days: int = 90,
        max_items: int = 20,
        query_type: str = "company",
    ) -> list[NewsItem]:
        return [
            it.model_copy(
                update={
                    "raw_query": it.raw_query or query,
                    "query_type": it.query_type or query_type,
                    "provider_name": it.provider_name or self._name,
                }
            )
            for it in self._items[:max_items]
        ]

    async def search_company_news(
        self,
        ticker: str,
        company_name: str | None = None,
        lookback_days: int = 90,
        max_items: int = 20,
    ) -> list[NewsItem]:
        return await self.search(
            f'"{company_name or ticker}" {ticker}'.strip(),
            lookback_days=lookback_days,
            max_items=max_items,
            query_type="company",
        )


class EnvConfiguredNewsProvider(NewsProvider):
    """
    Generic env-key news / web-search provider.

    Reads a JSON search endpoint whose shape is configured out-of-band. Field
    mapping is best-effort against common keys (title/headline, url/link,
    publishedAt/date, source/publisher, description/summary). Returns [] when
    unconfigured or on any error — never blocks the workflow.

    Aggregator results are ``T5_api_aggregator`` unless the resolved publisher
    host is a trusted media domain (mapped to T4/T3 by the registry).
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        name: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("NEWS_API_KEY")
        self._base_url = (
            base_url
            or os.environ.get("NEWS_SEARCH_ENDPOINT")
            or os.environ.get("NEWS_API_BASE_URL")
        )
        self._name = name or os.environ.get("NEWS_PROVIDER_NAME", "env_news")
        self._max_results = _env_int("NEWS_MAX_RESULTS", 10)
        self._timeout = _env_float("NEWS_TIMEOUT_SECONDS", 8.0)

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self._base_url)

    async def search(
        self,
        query: str,
        *,
        lookback_days: int = 90,
        max_items: int = 20,
        query_type: str = "company",
    ) -> list[NewsItem]:
        if not self.is_configured:
            return []
        cap = min(max_items, self._max_results)
        try:
            params = {
                "q": query,
                "apiKey": self._api_key,
                "pageSize": str(cap),
            }
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(self._base_url, params=params)  # type: ignore[arg-type]
                resp.raise_for_status()
                payload = resp.json()
        except Exception:
            return []
        return self._parse_payload(payload, cap, query, query_type)

    async def search_company_news(
        self,
        ticker: str,
        company_name: str | None = None,
        lookback_days: int = 90,
        max_items: int = 20,
    ) -> list[NewsItem]:
        return await self.search(
            f'"{company_name or ticker}" {ticker}'.strip(),
            lookback_days=lookback_days,
            max_items=max_items,
            query_type="company",
        )

    def _parse_payload(
        self, payload: Any, max_items: int, query: str, query_type: str
    ) -> list[NewsItem]:
        articles: list[Any] = []
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
                source.get("name")
                if isinstance(source, dict)
                else (source or art.get("publisher"))
            )
            url = art.get("url") or art.get("link")
            items.append(
                NewsItem(
                    headline=str(headline),
                    url=url,
                    published_at=art.get("publishedAt")
                    or art.get("published_at")
                    or art.get("date")
                    or art.get("seendate"),
                    source_name=source_name,
                    summary=art.get("description") or art.get("summary"),
                    provider_name=self._name,
                    source_tier=resolve_media_tier(extract_domain(url))
                    or SourceTier.T5_api_aggregator.value,
                    raw_query=query,
                    query_type=query_type,
                )
            )
        return items


# Clearer public alias for the generic configurable adapter.
ConfigurableWebNewsProvider = EnvConfiguredNewsProvider


class GdeltNewsProvider(NewsProvider):
    """
    No-key public GDELT 2.1 DOC API adapter.

    GDELT indexes global news and requires no API key, which makes it a safe
    default when a richer paid provider is not configured. Results are always
    ``T5_api_aggregator`` (an index, not the original publisher) unless the
    resolved article host is a trusted media domain. Bounded, short timeout,
    never raises. Mocked in CI — no live call happens there.
    """

    _BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url or os.environ.get("NEWS_API_BASE_URL") or self._BASE_URL
        self._max_results = _env_int("NEWS_MAX_RESULTS", 10)
        self._timeout = _env_float("NEWS_TIMEOUT_SECONDS", 8.0)

    @property
    def provider_name(self) -> str:
        return "gdelt"

    async def search(
        self,
        query: str,
        *,
        lookback_days: int = 90,
        max_items: int = 20,
        query_type: str = "company",
    ) -> list[NewsItem]:
        cap = min(max_items, self._max_results)
        params = {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(cap),
            "timespan": f"{max(1, lookback_days)}d",
            "sort": "DateDesc",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    self._base_url,
                    params=params,
                    headers={"User-Agent": "InvestingBuddy-Research-Platform/1.0"},
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception:
            return []
        return self._parse_payload(payload, cap, query, query_type)

    def _parse_payload(
        self, payload: Any, max_items: int, query: str, query_type: str
    ) -> list[NewsItem]:
        if not isinstance(payload, dict):
            return []
        articles = payload.get("articles")
        if not isinstance(articles, list):
            return []
        items: list[NewsItem] = []
        for art in articles[:max_items]:
            if not isinstance(art, dict):
                continue
            title = art.get("title")
            if not title:
                continue
            url = art.get("url")
            items.append(
                NewsItem(
                    headline=str(title),
                    url=url,
                    published_at=art.get("seendate"),
                    source_name=art.get("domain"),
                    summary=None,
                    provider_name="gdelt",
                    source_tier=resolve_media_tier(extract_domain(url))
                    or SourceTier.T5_api_aggregator.value,
                    raw_query=query,
                    query_type=query_type,
                )
            )
        return items


def get_news_provider(name: str | None = None) -> NewsProvider:
    """
    Resolve a news provider from configuration.

    Resolution order (reads NEWS_PROVIDER_NAME when ``name`` is None):
      - "gdelt"                → GdeltNewsProvider (no key required)
      - a configured env name  → EnvConfiguredNewsProvider (needs key + base url)
      - anything else / unset  → NullNewsProvider (safe, offline default)
    """
    provider_name = name or os.environ.get("NEWS_PROVIDER_NAME")
    if not provider_name or provider_name in ("null", "none", ""):
        return NullNewsProvider()
    if provider_name.lower() == "gdelt":
        return GdeltNewsProvider()
    env_provider = EnvConfiguredNewsProvider(name=provider_name)
    if env_provider.is_configured:
        return env_provider
    return NullNewsProvider()
