"""Shared pieces of every web search adapter — open-web W1 (spec §8).

* :func:`normalise_result` turns one vendor result into a ``SearchResultItem`` with a
  canonical URL and a PSL registrable domain, or ``None`` when it is not a usable
  ``http(s)`` URL.
* :func:`apply_client_filters` enforces, on the returned results, every filter
  InvestingBuddy can check itself (domains, and the date window on the published hint),
  and records in ``filters_enforced_by`` who enforced each requested filter. A vendor
  that accepted a filter and ignored it (ADR-055) cannot leak results past this.
* :func:`govern_payload` is the adapter-side governance call (spec §8.3, rule G4).
* :class:`UnavailableSearchProvider` is what the factory returns when a provider is
  selected but cannot run (no key, unknown name): every search is ``executed=False``
  with zero network calls.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from app.services.corpus.policy import ACCESS_PUBLIC_WEB
from app.services.providers.contracts import (
    FILTER_BY_CLIENT,
    FILTER_BY_PROVIDER,
    FILTER_PROVIDER_BOOST,
    FILTER_UNSUPPORTED,
    RESULT_STORAGE_TRANSIENT,
    SEARCH_ERROR_CREDENTIAL,
    SEARCH_ERROR_GOVERNANCE,
    FilterEnforcement,
    SearchCapabilities,
    SearchExecution,
    SearchRequest,
    SearchResultItem,
)
from app.services.providers.governance import (
    CredentialInPayloadError,
    ProviderGovernance,
    ProviderNotPermittedError,
    assert_no_credentials,
    default_governance,
)
from app.services.sources.public_suffix import registrable_domain
from app.services.sources.redaction import canonicalize_source_url

#: Filters InvestingBuddy can verify on a returned result.
CLIENT_CHECKABLE: frozenset[str] = frozenset(
    {"include_domains", "exclude_domains", "date_range"}
)
_MAX_TEXT = 2000


def _bounded(text: Any, limit: int = _MAX_TEXT) -> str | None:
    if not isinstance(text, str):
        return None
    cleaned = " ".join(text.split())
    return cleaned[:limit] or None


def parse_published_hint(raw: Any) -> datetime | None:
    """A vendor date string as an aware UTC datetime, or ``None``. Never raises."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    try:  # RFC 2822, e.g. "Mon, 15 Sep 2026 10:00:00 GMT"
        from email.utils import parsedate_to_datetime

        parsed = parsedate_to_datetime(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


def normalise_result(
    rank: int,
    *,
    url: Any,
    title: Any = None,
    snippet: Any = None,
    published: Any = None,
    score: Any = None,
    language: Any = None,
    metadata: Mapping[str, Any] | None = None,
) -> SearchResultItem | None:
    """One vendor result → ``SearchResultItem``, or ``None`` if the URL is unusable."""
    if not isinstance(url, str):
        return None
    raw_url = url.strip()
    try:
        parts = urlsplit(raw_url)
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    canonical = canonicalize_source_url(raw_url) or raw_url
    host = parts.hostname.lower()
    domain = registrable_domain(host) or host
    try:
        provider_score = float(score) if score is not None else None
    except (TypeError, ValueError):
        provider_score = None
    return SearchResultItem(
        rank=rank,
        url=raw_url,
        canonical_url=canonical,
        domain=domain,
        title=_bounded(title, 500),
        snippet=_bounded(snippet),
        published_hint=parse_published_hint(published),
        language_hint=_bounded(language, 16),
        provider_score=provider_score,
        metadata=dict(metadata or {}),
    )


def _host_matches(host: str, domain: str) -> bool:
    d = domain.strip().lower().lstrip(".")
    return bool(d) and (host == d or host.endswith("." + d))


def _host_of(item: SearchResultItem) -> str:
    try:
        return (urlsplit(item.url).hostname or "").lower()
    except ValueError:
        return ""


def _to_datetime(day: date, *, end: bool) -> datetime:
    if end:
        return datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=timezone.utc)
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def enforcement_plan(
    request: SearchRequest, capabilities: SearchCapabilities
) -> dict[str, FilterEnforcement]:
    """Who will enforce each filter this request actually sets.

    Domain filters are always ``client`` — InvestingBuddy verifies them on every result
    whether or not the vendor was also asked. The date window starts as ``client`` and
    is downgraded by :func:`finalize_enforcement` when undated results had to be kept.
    ``country`` is a ranking boost at the vendor (``provider_boost``), never a filter.
    The rest are ``provider`` if the vendor accepts them, otherwise ``unsupported``.
    """
    requested: list[str] = []
    if request.include_domains:
        requested.append("include_domains")
    if request.exclude_domains:
        requested.append("exclude_domains")
    if request.date_from or request.date_to:
        requested.append("date_range")
    if request.country:
        requested.append("country")
    if request.language:
        requested.append("language")
    if request.topic != "general":
        requested.append("topic")
    plan: dict[str, FilterEnforcement] = {}
    for name in requested:
        if name in CLIENT_CHECKABLE:
            plan[name] = FILTER_BY_CLIENT
        elif name == "country" and name in capabilities.provider_filters:
            plan[name] = FILTER_PROVIDER_BOOST
        elif name in capabilities.provider_filters:
            plan[name] = FILTER_BY_PROVIDER
        else:
            plan[name] = FILTER_UNSUPPORTED
    return plan


def finalize_enforcement(
    plan: dict[str, FilterEnforcement],
    capabilities: SearchCapabilities,
    date_unchecked: int,
) -> dict[str, FilterEnforcement]:
    """Correct the date label once the results are known (review C4).

    ``client`` is only true when every kept result carried a date the client checked.
    Otherwise the window rests on the vendor (``provider``) if it accepts one, and is
    ``unsupported`` if it does not.
    """
    if "date_range" in plan and date_unchecked > 0:
        plan["date_range"] = (
            FILTER_BY_PROVIDER
            if "date_range" in capabilities.provider_filters
            else FILTER_UNSUPPORTED
        )
    return plan


def apply_client_filters(
    request: SearchRequest, items: list[SearchResultItem]
) -> tuple[list[SearchResultItem], int, int]:
    """Drop results that violate a checkable filter; re-rank the survivors from 1.

    The date window is checked against ``published_hint`` only when the vendor gave one.
    An undated result is kept: the hint is never authoritative, and dropping every
    undated page would silently remove most of the general web. How many were kept
    unchecked is returned so the label can say so.
    Returns ``(kept, removed_count, date_unchecked_count)``.
    """
    lo = _to_datetime(request.date_from, end=False) if request.date_from else None
    hi = _to_datetime(request.date_to, end=True) if request.date_to else None
    kept: list[SearchResultItem] = []
    removed = 0
    for item in items:
        host = _host_of(item)
        if request.include_domains and not any(
            _host_matches(host, d) for d in request.include_domains
        ):
            removed += 1
            continue
        if request.exclude_domains and any(
            _host_matches(host, d) for d in request.exclude_domains
        ):
            removed += 1
            continue
        hint = item.published_hint
        if hint is not None and ((lo and hint < lo) or (hi and hint > hi)):
            removed += 1
            continue
        kept.append(item)
    limit = max(0, int(request.max_results))
    kept = kept[:limit]
    date_requested = request.date_from is not None or request.date_to is not None
    unchecked = (
        sum(1 for it in kept if it.published_hint is None) if date_requested else 0
    )
    reranked = [
        SearchResultItem(
            rank=i,
            url=it.url,
            canonical_url=it.canonical_url,
            domain=it.domain,
            title=it.title,
            snippet=it.snippet,
            published_hint=it.published_hint,
            language_hint=it.language_hint,
            provider_score=it.provider_score,
            metadata=it.metadata,
        )
        for i, it in enumerate(kept, start=1)
    ]
    return reranked, removed, unchecked


def govern_payload(
    provider: str, payload_text: str, governance: ProviderGovernance | None = None
) -> str | None:
    """Rule G4 at the adapter: ``None`` if the payload may be sent, else an error code.

    ``payload_text`` is exactly what will go on the wire, minus the auth header.
    """
    gov = governance or default_governance()
    try:
        gov.assert_permitted(provider, ACCESS_PUBLIC_WEB)
    except ProviderNotPermittedError:
        return SEARCH_ERROR_GOVERNANCE
    try:
        assert_no_credentials(payload_text)
    except CredentialInPayloadError:
        return SEARCH_ERROR_CREDENTIAL
    return None


@dataclass
class UnavailableSearchProvider:
    """A selected provider that cannot run. Every call: not executed, zero network."""

    name: str
    error_code: str
    capabilities: SearchCapabilities = field(
        default_factory=lambda: SearchCapabilities(result_storage=RESULT_STORAGE_TRANSIENT)
    )
    is_configured: bool = False

    async def search(
        self, request: SearchRequest
    ) -> tuple[SearchExecution, list[SearchResultItem]]:
        return (
            SearchExecution.not_executed(
                self.name,
                self.error_code,
                filters_enforced_by=enforcement_plan(request, self.capabilities),
            ),
            [],
        )


__all__ = [
    "CLIENT_CHECKABLE",
    "finalize_enforcement",
    "UnavailableSearchProvider",
    "apply_client_filters",
    "enforcement_plan",
    "govern_payload",
    "normalise_result",
    "parse_published_hint",
]
