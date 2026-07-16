"""
Phase 24.1 — Exchange-aware source registry.

Chooses relevant official / semi-official source *hints and query templates*
based on a company's exchange and country. This module never scrapes exchange
pages directly — it only generates search-query strings and candidate profile
URLs that downstream planning/discovery can use conservatively.

Design principles:
  - Generate hints, not scrapes. No fragile HTML parsing here.
  - Never crash on an unknown / missing exchange — fall back to a generic US
    profile (SEC EDGAR + company IR + reputable financial press).
  - Keep source tiers conservative. Exchange/listing-venue pages are NOT
    regulators; they map to ``T3_industry_specialist`` and are never promoted to
    T1/T2.
  - A small CURATED, VERIFIED issuer-source registry provides company-owned
    URLs for a handful of mega-cap issuers. This is maintained reference data
    (an allowlist), not model fabrication, and is documented as such.

Template placeholders (filled by the query planner):
  {ticker} {company} {legal_name} {sector} {industry}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.integrations.financial_data_provider import SourceTier

# Exchange/listing-venue pages are not regulators. Taxonomy has no dedicated
# exchange tier, so we treat them as industry-specialist sources.
EXCHANGE_SOURCE_TIER = SourceTier.T3_industry_specialist.value

# Recommendation / stock-prediction language that must never appear in a query.
FORBIDDEN_QUERY_PHRASES: tuple[str, ...] = (
    "best stock",
    "best stocks",
    "stock to buy",
    "stocks to buy",
    "should i buy",
    "buy signal",
    "sell signal",
    "price target",
    "target price",
    "fair value",
    "undervalued",
    "overvalued",
    "upside",
    "downside",
    "strong buy",
    "hot stock",
    "next big stock",
)

# Known low-quality / SEO / stock-prediction domains to avoid accepting as
# company or reputable-media sources. Substring match on the host.
LOW_QUALITY_DOMAIN_MARKERS: tuple[str, ...] = (
    "stockpredict",
    "stockforecast",
    "buyorsell",
    "pennystock",
    "stocktwits",
    "wallstreetzen",
    "stockinvest.us",
    "walletinvestor",
    "predict",
    "forecast-",
    "besttopstocks",
)

# Social-media hosts are not treated as company-owned primary sources.
SOCIAL_MEDIA_DOMAINS: tuple[str, ...] = (
    "facebook.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "instagram.com",
    "youtube.com",
    "reddit.com",
    "t.me",
    "tiktok.com",
)

# Reputable business/financial media → may be mapped to T4_quality_media when a
# resolved article's host matches. Aggregators without a resolved host stay T5.
TRUSTED_MEDIA_DOMAINS: dict[str, str] = {
    "reuters.com": SourceTier.T4_quality_media.value,
    "bloomberg.com": SourceTier.T4_quality_media.value,
    "wsj.com": SourceTier.T4_quality_media.value,
    "ft.com": SourceTier.T4_quality_media.value,
    "cnbc.com": SourceTier.T4_quality_media.value,
    "apnews.com": SourceTier.T4_quality_media.value,
    "nytimes.com": SourceTier.T4_quality_media.value,
    "marketwatch.com": SourceTier.T4_quality_media.value,
    "barrons.com": SourceTier.T4_quality_media.value,
    "forbes.com": SourceTier.T4_quality_media.value,
    "businesswire.com": SourceTier.T3_industry_specialist.value,
    "prnewswire.com": SourceTier.T3_industry_specialist.value,
    "globenewswire.com": SourceTier.T3_industry_specialist.value,
}


@dataclass
class ExchangeSourceProfile:
    """Exchange/country-scoped source hints and query templates."""

    exchange: str
    country: str
    official_profile_query_templates: list[str] = field(default_factory=list)
    news_query_templates: list[str] = field(default_factory=list)
    filing_query_templates: list[str] = field(default_factory=list)
    trusted_domains: list[str] = field(default_factory=list)
    exchange_profile_url_template: str | None = None


@dataclass
class CuratedIssuerSource:
    """A curated, verified company-owned source set for a well-known issuer."""

    ticker: str
    website: str
    investor_relations_url: str | None = None
    newsroom_url: str | None = None
    press_release_feed_url: str | None = None


# --------------------------------------------------------------------------- #
# Curated, verified issuer sources (allowlist — extend as needed).
# Maintained reference data, NOT model-derived. Only well-known, stable issuer
# URLs are listed here; discovery marks these as curated_verified_registry.
# --------------------------------------------------------------------------- #
KNOWN_ISSUER_SOURCES: dict[str, CuratedIssuerSource] = {
    "AAPL": CuratedIssuerSource(
        ticker="AAPL",
        website="https://www.apple.com",
        investor_relations_url="https://investor.apple.com",
        newsroom_url="https://www.apple.com/newsroom/",
        press_release_feed_url="https://www.apple.com/newsroom/rss/newsroom.rss",
    ),
    "MSFT": CuratedIssuerSource(
        ticker="MSFT",
        website="https://www.microsoft.com",
        investor_relations_url="https://www.microsoft.com/en-us/investor",
        newsroom_url="https://news.microsoft.com/",
        press_release_feed_url="https://news.microsoft.com/feed/",
    ),
    "NVDA": CuratedIssuerSource(
        ticker="NVDA",
        website="https://www.nvidia.com",
        investor_relations_url="https://investor.nvidia.com",
        newsroom_url="https://nvidianews.nvidia.com/",
        press_release_feed_url="https://nvidianews.nvidia.com/releases.xml",
    ),
    "GOOGL": CuratedIssuerSource(
        ticker="GOOGL",
        website="https://abc.xyz",
        investor_relations_url="https://abc.xyz/investor/",
        newsroom_url="https://blog.google/",
        press_release_feed_url="https://blog.google/rss/",
    ),
    "AMZN": CuratedIssuerSource(
        ticker="AMZN",
        website="https://www.aboutamazon.com",
        investor_relations_url="https://ir.aboutamazon.com",
        newsroom_url="https://www.aboutamazon.com/news",
        press_release_feed_url="https://press.aboutamazon.com/rss/pressrelease.aspx",
    ),
    "TSLA": CuratedIssuerSource(
        ticker="TSLA",
        website="https://www.tesla.com",
        investor_relations_url="https://ir.tesla.com",
        newsroom_url="https://www.tesla.com/blog",
        press_release_feed_url=None,
    ),
    "META": CuratedIssuerSource(
        ticker="META",
        website="https://about.meta.com",
        investor_relations_url="https://investor.atmeta.com",
        newsroom_url="https://about.fb.com/news/",
        press_release_feed_url="https://about.fb.com/news/feed/",
    ),
}


# --------------------------------------------------------------------------- #
# Exchange normalisation
# --------------------------------------------------------------------------- #

_NASDAQ_ALIASES = {"NASDAQ", "NAS", "XNAS", "NMS", "NASDAQGS", "NASDAQ-GS"}
_NYSE_ALIASES = {"NYSE", "XNYS", "NEW YORK STOCK EXCHANGE"}
_AMEX_ALIASES = {"AMEX", "NYSE AMERICAN", "NYSEAMERICAN", "XASE", "NYSE MKT"}
# Generic US bucket (e.g. exchange reported simply as "US").
_US_GENERIC_ALIASES = {"US", "USA", "UNITED STATES", "USOTC", "OTC", ""}


def normalize_exchange(exchange: str | None) -> str:
    """Normalise a raw exchange string to a canonical key."""
    raw = (exchange or "").strip().upper()
    if raw in _NASDAQ_ALIASES:
        return "NASDAQ"
    if raw in _NYSE_ALIASES:
        return "NYSE"
    if raw in _AMEX_ALIASES:
        return "AMEX"
    if raw in _US_GENERIC_ALIASES:
        return "US"
    return raw or "US"


def _nasdaq_profile() -> ExchangeSourceProfile:
    return ExchangeSourceProfile(
        exchange="NASDAQ",
        country="US",
        official_profile_query_templates=[
            "{ticker} Nasdaq company profile",
            '"{legal_name}" Nasdaq market activity',
        ],
        news_query_templates=[
            "{ticker} Nasdaq news",
            '"{company}" Nasdaq recent news',
        ],
        filing_query_templates=[
            "{ticker} SEC 8-K filing",
            '"{legal_name}" SEC EDGAR filing',
        ],
        trusted_domains=["nasdaq.com", "sec.gov"],
        exchange_profile_url_template=(
            "https://www.nasdaq.com/market-activity/stocks/{ticker_lower}"
        ),
    )


def _nyse_profile() -> ExchangeSourceProfile:
    return ExchangeSourceProfile(
        exchange="NYSE",
        country="US",
        official_profile_query_templates=[
            "{ticker} NYSE company profile",
            '"{legal_name}" NYSE listings',
        ],
        news_query_templates=[
            "{ticker} NYSE news",
            '"{company}" NYSE recent news',
        ],
        filing_query_templates=[
            "{ticker} SEC 8-K filing",
            '"{legal_name}" SEC EDGAR filing',
        ],
        trusted_domains=["nyse.com", "sec.gov"],
        exchange_profile_url_template="https://www.nyse.com/quote/{ticker}",
    )


def _amex_profile() -> ExchangeSourceProfile:
    p = _nyse_profile()
    p.exchange = "AMEX"
    p.official_profile_query_templates = [
        "{ticker} NYSE American company profile",
        '"{legal_name}" NYSE American listing',
    ]
    p.news_query_templates = ["{ticker} NYSE American news", '"{company}" recent news']
    return p


def _generic_us_profile() -> ExchangeSourceProfile:
    return ExchangeSourceProfile(
        exchange="US",
        country="US",
        official_profile_query_templates=['"{legal_name}" company profile'],
        news_query_templates=[
            '"{company}" recent news',
            "{ticker} stock news",
        ],
        filing_query_templates=[
            "{ticker} SEC 8-K filing",
            '"{legal_name}" SEC EDGAR filing',
        ],
        trusted_domains=["sec.gov"],
        exchange_profile_url_template=None,
    )


def _generic_profile(exchange: str, country: str | None) -> ExchangeSourceProfile:
    return ExchangeSourceProfile(
        exchange=exchange,
        country=country or "",
        official_profile_query_templates=['"{legal_name}" company profile'],
        news_query_templates=['"{company}" recent news', "{ticker} stock news"],
        filing_query_templates=['"{legal_name}" regulatory filing'],
        trusted_domains=[],
        exchange_profile_url_template=None,
    )


def get_exchange_profile(
    exchange: str | None, country: str | None = None
) -> ExchangeSourceProfile:
    """Return the source profile for an exchange, never raising on unknowns."""
    key = normalize_exchange(exchange)
    if key == "NASDAQ":
        return _nasdaq_profile()
    if key == "NYSE":
        return _nyse_profile()
    if key == "AMEX":
        return _amex_profile()
    if key == "US" or (country or "").upper() in ("US", "USA", "UNITED STATES"):
        return _generic_us_profile()
    return _generic_profile(key, country)


def get_curated_issuer_source(ticker: str | None) -> CuratedIssuerSource | None:
    """Return curated verified sources for a well-known issuer, if present."""
    if not ticker:
        return None
    return KNOWN_ISSUER_SOURCES.get(ticker.strip().upper())


# --------------------------------------------------------------------------- #
# Domain helpers
# --------------------------------------------------------------------------- #


def extract_domain(url: str | None) -> str:
    """Return the lower-case registrable-ish host of a URL (no ``www.``)."""
    if not url:
        return ""
    raw = url.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    host = (urlparse(raw).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_social_media_domain(domain: str) -> bool:
    d = domain.lower()
    return any(d == s or d.endswith("." + s) for s in SOCIAL_MEDIA_DOMAINS)


def is_low_quality_domain(domain: str) -> bool:
    d = domain.lower()
    return any(marker in d for marker in LOW_QUALITY_DOMAIN_MARKERS)


def resolve_media_tier(domain: str) -> str | None:
    """Return a trusted-media source tier for a host, else None (stays T5)."""
    d = domain.lower()
    for host, tier in TRUSTED_MEDIA_DOMAINS.items():
        if d == host or d.endswith("." + host):
            return tier
    return None


def query_has_forbidden_phrase(query: str) -> bool:
    """True if a query contains recommendation / prediction language."""
    low = query.lower()
    return any(phrase in low for phrase in FORBIDDEN_QUERY_PHRASES)
