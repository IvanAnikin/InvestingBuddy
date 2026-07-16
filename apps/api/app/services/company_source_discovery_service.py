"""
Phase 24.1 — Company source discovery service.

Discovers a company's OWN authoritative sources (website, investor relations,
newsroom, press-release / RSS feeds) plus the exchange profile page, so the
catalyst subsystem can collect T1 (company-owned) press releases and validate
identity metadata.

Discovery order (strongest evidence first, never fabricating a domain):
  1. Curated verified issuer registry (maintained allowlist).
  2. Existing ``profile.website`` (from identity enrichment).
  3. SEC submissions website (when supplied).
  4. GLEIF website (when supplied and the legal name matches strongly).
  5. Exchange-aware profile URL (registry template) — a T3 hint, never T1.
  6. Optional configured search provider results whose domain confidently
     matches the company brand.

Safety / conservativeness:
  - No broad or recursive crawling; the press-release provider probes only a
    small set of feed paths off a verified website.
  - A source is only marked company-owned (verified) when domain confidence is
    high. Uncertain results are kept as unverified candidates.
  - Social-media and low-quality/prediction domains are rejected.
  - Never raises: any provider error becomes a warning.
"""

from __future__ import annotations

import logging

from app.integrations.exchange_source_registry import (
    ExchangeSourceProfile,
    extract_domain,
    get_curated_issuer_source,
    get_exchange_profile,
    is_low_quality_domain,
    is_social_media_domain,
)
from app.integrations.financial_data_provider import SourceTier
from app.integrations.providers.company_press_release_provider import (
    discover_feed_urls,
)
from app.integrations.providers.news_provider_base import NewsProvider
from app.schemas.catalyst import NewsItem
from app.schemas.company_sources import (
    CompanySourceDiscoveryResult,
    SourceCandidate,
    SourceType,
    VerificationMethod,
)
from app.services.news_relevance_scorer import brand_tokens

logger = logging.getLogger(__name__)

_T1 = SourceTier.T1_primary_filing.value
_T3 = SourceTier.T3_industry_specialist.value

# Path fragments that hint at IR / newsroom pages in a search result URL.
_IR_URL_HINTS = ("investor", "/ir", "investor-relations", "investors")
_NEWSROOM_URL_HINTS = ("newsroom", "/news", "press", "media", "press-release")


def domain_matches_brand(domain: str, tokens: list[str]) -> bool:
    """True if a host plausibly belongs to the company brand."""
    if not domain or not tokens:
        return False
    host = domain.replace("-", "")
    root = host.split(".")[0]
    for tok in tokens:
        t = tok.replace("-", "")
        if len(t) < 3:
            continue
        if t in root or root in t or t in host:
            return True
    return False


def _classify_search_result_type(url: str) -> str:
    low = url.lower()
    if any(h in low for h in _IR_URL_HINTS):
        return SourceType.investor_relations.value
    if any(h in low for h in _NEWSROOM_URL_HINTS):
        return SourceType.newsroom.value
    return SourceType.company_homepage.value


def _homepage_candidate(
    url: str, method: str, confidence: float, tokens: list[str]
) -> SourceCandidate:
    domain = extract_domain(url)
    return SourceCandidate(
        url=url,
        domain=domain,
        source_type=SourceType.company_homepage.value,
        source_tier=_T1,
        confidence=confidence,
        verification_method=method,
        verified=True,
    )


async def discover_company_sources(
    *,
    ticker: str,
    company_name: str | None = None,
    exchange: str | None = None,
    country: str | None = None,
    sector: str | None = None,
    industry: str | None = None,
    website: str | None = None,
    sec_website: str | None = None,
    gleif_website: str | None = None,
    search_provider: NewsProvider | None = None,
    max_search_results: int = 8,
) -> CompanySourceDiscoveryResult:
    """Discover company-owned + exchange sources. Never raises."""
    ticker_u = (ticker or "").upper()
    tokens = brand_tokens(company_name) or brand_tokens(ticker_u)
    profile: ExchangeSourceProfile = get_exchange_profile(exchange, country)

    result = CompanySourceDiscoveryResult(
        ticker=ticker_u,
        company_name=company_name,
        exchange=exchange,
        country=country,
    )
    verified: list[SourceCandidate] = []
    candidates: list[SourceCandidate] = []
    rejected: list[SourceCandidate] = []
    warnings: list[str] = []

    # 1. Curated verified issuer registry (highest confidence) -------------
    curated = get_curated_issuer_source(ticker_u)
    if curated:
        result.company_website = curated.website
        result.investor_relations_url = curated.investor_relations_url
        result.newsroom_url = curated.newsroom_url
        result.press_release_feed_url = curated.press_release_feed_url
        for url, stype in (
            (curated.website, SourceType.company_homepage.value),
            (curated.investor_relations_url, SourceType.investor_relations.value),
            (curated.newsroom_url, SourceType.newsroom.value),
            (curated.press_release_feed_url, SourceType.press_release_feed.value),
        ):
            if not url:
                continue
            verified.append(
                SourceCandidate(
                    url=url,
                    domain=extract_domain(url),
                    source_type=stype,
                    source_tier=_T1,
                    confidence=0.95,
                    verification_method=VerificationMethod.curated_verified_registry.value,
                    verified=True,
                )
            )

    # 2..4. Website from enrichment / SEC / GLEIF --------------------------
    if not result.company_website:
        website_sources = [
            (website, VerificationMethod.profile_website.value, 0.9),
            (sec_website, VerificationMethod.sec_submissions_website.value, 0.85),
            (gleif_website, VerificationMethod.gleif_website.value, 0.8),
        ]
        for site, method, conf in website_sources:
            if not site:
                continue
            domain = extract_domain(site)
            if is_social_media_domain(domain) or is_low_quality_domain(domain):
                rejected.append(
                    SourceCandidate(
                        url=site,
                        domain=domain,
                        source_type=SourceType.company_homepage.value,
                        verification_method=method,
                        warnings=["Rejected: social-media / low-quality domain."],
                    )
                )
                continue
            # GLEIF must clear a brand-name match to guard against wrong LEIs.
            if method == VerificationMethod.gleif_website.value and not domain_matches_brand(
                domain, tokens
            ):
                rejected.append(
                    SourceCandidate(
                        url=site,
                        domain=domain,
                        source_type=SourceType.company_homepage.value,
                        verification_method=method,
                        warnings=["Rejected: GLEIF website did not match company brand."],
                    )
                )
                continue
            cand = _homepage_candidate(site, method, conf, tokens)
            verified.append(cand)
            result.company_website = site
            break

    # Derive candidate feed URLs from a verified website (no network here).
    if result.company_website and not result.press_release_feed_url:
        feed_candidates = discover_feed_urls(result.company_website)
        result.rss_feed_urls = feed_candidates
        for fu in feed_candidates:
            candidates.append(
                SourceCandidate(
                    url=fu,
                    domain=extract_domain(fu),
                    source_type=SourceType.rss_feed.value,
                    source_tier=_T1,
                    confidence=0.4,
                    verification_method=VerificationMethod.domain_brand_match.value,
                    verified=False,
                    warnings=["Candidate feed path — verified only if it parses."],
                )
            )

    # 5. Exchange profile URL (T3 hint, never T1) --------------------------
    if profile.exchange_profile_url_template:
        try:
            exch_url = profile.exchange_profile_url_template.format(
                ticker=ticker_u, ticker_lower=ticker_u.lower()
            )
            result.exchange_profile_url = exch_url
            candidates.append(
                SourceCandidate(
                    url=exch_url,
                    domain=extract_domain(exch_url),
                    source_type=SourceType.exchange_profile.value,
                    source_tier=_T3,
                    confidence=0.5,
                    verification_method=VerificationMethod.exchange_registry_pattern.value,
                    verified=False,
                    warnings=["Exchange/listing venue page — not a regulator (T3)."],
                )
            )
        except (KeyError, IndexError):
            pass

    # 6. Optional search-provider IR / newsroom discovery ------------------
    if search_provider is not None:
        query = f'"{company_name or ticker_u}" investor relations newsroom press release'
        try:
            items: list[NewsItem] = await search_provider.search(
                query,
                lookback_days=3650,
                max_items=max_search_results,
                query_type="primary_source",
            )
        except Exception as exc:  # defensive
            items = []
            warnings.append(f"Source-discovery search failed (non-fatal): {exc}")
        for it in items:
            url = it.url
            if not url:
                continue
            domain = extract_domain(url)
            if is_social_media_domain(domain) or is_low_quality_domain(domain):
                rejected.append(
                    SourceCandidate(
                        url=url,
                        domain=domain,
                        source_type=SourceType.search_result.value,
                        verification_method=VerificationMethod.unverified_candidate.value,
                        warnings=["Rejected: social-media / low-quality domain."],
                    )
                )
                continue
            is_brand = domain_matches_brand(domain, tokens)
            stype = (
                _classify_search_result_type(url)
                if is_brand
                else SourceType.search_result.value
            )
            cand = SourceCandidate(
                url=url,
                domain=domain,
                source_type=stype,
                source_tier=_T1 if is_brand else SourceTier.T5_api_aggregator.value,
                confidence=0.75 if is_brand else 0.2,
                verification_method=(
                    VerificationMethod.domain_brand_match.value
                    if is_brand
                    else VerificationMethod.unverified_candidate.value
                ),
                verified=is_brand,
            )
            if is_brand:
                verified.append(cand)
                is_ir = stype == SourceType.investor_relations.value
                is_newsroom = stype == SourceType.newsroom.value
                if is_ir and not result.investor_relations_url:
                    result.investor_relations_url = url
                elif is_newsroom and not result.newsroom_url:
                    result.newsroom_url = url
                elif not result.company_website:
                    result.company_website = url
            else:
                candidates.append(cand)

    # Assemble + confidence ------------------------------------------------
    result.verified_sources = verified
    result.source_candidates = candidates
    result.rejected_sources = rejected
    result.warnings = warnings

    if not verified:
        result.warnings.append(
            "Company primary news source unavailable: no company-owned website / "
            "IR / newsroom source could be confidently discovered for this issuer. "
            "Press-release catalysts rely on a known company feed; SEC filings and "
            "any configured news provider still apply."
        )
        result.confidence = 0.0
    else:
        result.confidence = round(
            min(1.0, max(c.confidence for c in verified)), 2
        )

    return result
