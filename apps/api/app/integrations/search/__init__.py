"""Web search adapters — open-web W1 (spec §8.2).

:func:`web_search_provider_from_settings` is the only place a web search provider is
chosen, and it reads ``V3_WEB_SEARCH_PROVIDER``:

* ``none`` (default) → ``None``: web search is disabled.
* ``fake`` → :class:`~app.integrations.search.fake.FakeWebSearchProvider` over the
  bundled fixtures. Tests and local only.
* ``tavily`` → :class:`~app.integrations.search.tavily.TavilySearchProvider` when
  ``TAVILY_API_KEY`` is set, otherwise an unavailable provider (``no_key``) that makes
  zero network calls.
* anything else — including ``deepseek`` — → an unavailable provider
  (``unknown_provider``). DeepSeek's ``search()`` is kept for the benchmark only and is
  **not** a web search provider; an unknown value is never mapped to some other vendor.
"""

from __future__ import annotations

from typing import Any

from app.integrations.search.base import UnavailableSearchProvider
from app.services.providers.contracts import (
    SEARCH_ERROR_NO_KEY,
    SEARCH_ERROR_UNKNOWN_PROVIDER,
    SearchProvider,
)

PROVIDER_NONE = "none"
PROVIDER_FAKE = "fake"
PROVIDER_TAVILY = "tavily"
SELECTABLE_PROVIDERS: frozenset[str] = frozenset(
    {PROVIDER_NONE, PROVIDER_FAKE, PROVIDER_TAVILY}
)


def web_search_provider_from_settings(cfg: Any | None = None) -> SearchProvider | None:
    """The configured web search provider, ``None`` when disabled by selection."""
    if cfg is None:
        from app.core.config import settings as cfg  # noqa: PLW0127

    choice = str(getattr(cfg, "v3_web_search_provider", PROVIDER_NONE) or PROVIDER_NONE)
    choice = choice.strip().lower()
    if choice == PROVIDER_NONE:
        return None
    if choice == PROVIDER_FAKE:
        from app.integrations.search.fake import FakeWebSearchProvider

        return FakeWebSearchProvider.from_fixture_dir()
    if choice == PROVIDER_TAVILY:
        from app.integrations.search.tavily import TavilySearchProvider

        key = str(getattr(cfg, "tavily_api_key", "") or "").strip()
        if not key:
            return UnavailableSearchProvider(name=PROVIDER_TAVILY, error_code=SEARCH_ERROR_NO_KEY)
        return TavilySearchProvider(
            api_key=key,
            base_url=str(getattr(cfg, "tavily_base_url", "") or "https://api.tavily.com"),
        )
    return UnavailableSearchProvider(
        name=choice[:40] or "unknown", error_code=SEARCH_ERROR_UNKNOWN_PROVIDER
    )


__all__ = [
    "PROVIDER_FAKE",
    "PROVIDER_NONE",
    "PROVIDER_TAVILY",
    "SELECTABLE_PROVIDERS",
    "web_search_provider_from_settings",
]
