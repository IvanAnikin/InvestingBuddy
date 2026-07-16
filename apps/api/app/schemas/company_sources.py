"""
Phase 24.1 — Company source discovery contracts.

Data model for discovering a company's OWN authoritative sources (website,
investor-relations, newsroom, press-release / RSS feeds) plus exchange profile
pages, and for planning bounded, recommendation-free news searches.

Source-tier discipline (unchanged from Phase 24):
  - A company-owned primary source uses ``T1_primary_filing`` (the enum has no
    dedicated ``T1_primary_company_source``) and is documented as
    "company-owned primary source".
  - SEC / regulator sources are ``T2_regulator_or_gov``.
  - Exchange/listing-venue profile pages are NOT regulators: they are mapped to
    ``T3_industry_specialist`` (the taxonomy has no dedicated exchange tier), and
    they are NEVER promoted to T1/T2.
  - Aggregator / web-search / news-API results stay ``T5_api_aggregator`` unless
    the resolved original publisher is explicitly a trusted T4/T3 domain.
  - Model-derived labels (relevance, classification) are ``T6_model_estimate``.

Nothing here produces a recommendation, price target, fair value, or
upside/downside. Discovery is source-backed and never fabricates a domain.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.integrations.financial_data_provider import SourceTier


class SourceType(str, Enum):
    company_homepage = "company_homepage"
    investor_relations = "investor_relations"
    newsroom = "newsroom"
    press_release_feed = "press_release_feed"
    rss_feed = "rss_feed"
    exchange_profile = "exchange_profile"
    regulator_profile = "regulator_profile"
    search_result = "search_result"


class RelevanceLevel(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    irrelevant = "irrelevant"


# Verification methods, from strongest to weakest evidence of company ownership.
class VerificationMethod(str, Enum):
    curated_verified_registry = "curated_verified_registry"
    profile_website = "profile_website"
    sec_submissions_website = "sec_submissions_website"
    gleif_website = "gleif_website"
    exchange_registry_pattern = "exchange_registry_pattern"
    domain_brand_match = "domain_brand_match"
    unverified_candidate = "unverified_candidate"


class SourceCandidate(BaseModel):
    """A discovered candidate source for a company (verified or not)."""

    url: str
    domain: str = ""
    source_type: str = SourceType.search_result.value
    source_tier: str = SourceTier.T5_api_aggregator.value
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    verification_method: str = VerificationMethod.unverified_candidate.value
    verified: bool = False
    warnings: list[str] = Field(default_factory=list)

    def to_report_dict(self) -> dict:
        return {
            "url": self.url,
            "domain": self.domain,
            "source_type": self.source_type,
            "source_tier": self.source_tier,
            "confidence": self.confidence,
            "verification_method": self.verification_method,
            "verified": self.verified,
            "warnings": list(self.warnings),
        }


class CompanySourceDiscoveryResult(BaseModel):
    """Complete output of the company source discovery subsystem."""

    ticker: str
    company_name: str | None = None
    exchange: str | None = None
    country: str | None = None

    company_website: str | None = None
    investor_relations_url: str | None = None
    newsroom_url: str | None = None
    press_release_feed_url: str | None = None
    rss_feed_urls: list[str] = Field(default_factory=list)
    exchange_profile_url: str | None = None

    source_candidates: list[SourceCandidate] = Field(default_factory=list)
    verified_sources: list[SourceCandidate] = Field(default_factory=list)
    rejected_sources: list[SourceCandidate] = Field(default_factory=list)

    warnings: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @property
    def has_verified_company_source(self) -> bool:
        return any(
            c.source_type
            in (
                SourceType.company_homepage.value,
                SourceType.investor_relations.value,
                SourceType.newsroom.value,
                SourceType.press_release_feed.value,
                SourceType.rss_feed.value,
            )
            for c in self.verified_sources
        )

    def candidate_feed_urls(self) -> list[str]:
        """Feed URLs worth probing for press releases (verified first)."""
        urls: list[str] = []
        if self.press_release_feed_url:
            urls.append(self.press_release_feed_url)
        urls.extend(self.rss_feed_urls)
        # Deduplicate, preserve order.
        seen: set[str] = set()
        out: list[str] = []
        for u in urls:
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def to_report_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "company_name": self.company_name,
            "exchange": self.exchange,
            "country": self.country,
            "company_website": self.company_website,
            "investor_relations_url": self.investor_relations_url,
            "newsroom_url": self.newsroom_url,
            "press_release_feed_url": self.press_release_feed_url,
            "rss_feed_urls": list(self.rss_feed_urls),
            "exchange_profile_url": self.exchange_profile_url,
            "confidence": self.confidence,
            "has_verified_company_source": self.has_verified_company_source,
            "verified_sources": [c.to_report_dict() for c in self.verified_sources],
            "source_candidates": [c.to_report_dict() for c in self.source_candidates],
            "rejected_sources": [c.to_report_dict() for c in self.rejected_sources],
            "warnings": list(self.warnings),
        }
