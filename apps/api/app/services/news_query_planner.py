"""
Phase 24.1 — News search query planner.

Builds a bounded, deterministic ``NewsSearchPlan`` from company identity plus the
exchange-aware source registry. Queries are grouped by intent (company / industry
/ exchange / primary-source / regulatory) and are guaranteed free of any
recommendation or stock-prediction language.

Guarantees:
  - Uses the exact company legal name and ticker (not vague phrases).
  - Total query count is bounded (``max_total_queries``), so a company analysis
    makes a small, predictable number of provider calls.
  - No query contains a forbidden phrase ("best stock to buy", "price target",
    "buy signal", …). Queries that would are dropped by construction.
  - Results feed the news/search provider; they never produce a recommendation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.integrations.exchange_source_registry import (
    get_exchange_profile,
    query_has_forbidden_phrase,
)
from app.schemas.company_sources import CompanySourceDiscoveryResult


class NewsSearchPlan(BaseModel):
    ticker: str
    company_name: str | None = None
    exchange: str | None = None
    country: str | None = None
    sector: str | None = None
    industry: str | None = None
    lookback_days: int = 90
    max_results_per_query: int = 10

    company_queries: list[str] = Field(default_factory=list)
    industry_queries: list[str] = Field(default_factory=list)
    exchange_queries: list[str] = Field(default_factory=list)
    primary_source_queries: list[str] = Field(default_factory=list)
    regulatory_queries: list[str] = Field(default_factory=list)

    def all_company_queries(self) -> list[tuple[str, str]]:
        """(query, query_type) pairs whose results are company-specific candidates."""
        out: list[tuple[str, str]] = []
        out += [(q, "company") for q in self.company_queries]
        out += [(q, "primary_source") for q in self.primary_source_queries]
        out += [(q, "exchange") for q in self.exchange_queries]
        out += [(q, "regulatory") for q in self.regulatory_queries]
        return out

    def all_industry_queries(self) -> list[tuple[str, str]]:
        return [(q, "industry") for q in self.industry_queries]

    def total_query_count(self) -> int:
        return (
            len(self.company_queries)
            + len(self.industry_queries)
            + len(self.exchange_queries)
            + len(self.primary_source_queries)
            + len(self.regulatory_queries)
        )


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.strip().lower()
        if it.strip() and key not in seen:
            seen.add(key)
            out.append(it.strip())
    return out


def _safe(queries: list[str]) -> list[str]:
    """Drop any query containing a forbidden recommendation/prediction phrase."""
    return [q for q in queries if not query_has_forbidden_phrase(q)]


def build_news_search_plan(
    *,
    ticker: str,
    company_name: str | None = None,
    exchange: str | None = None,
    country: str | None = None,
    sector: str | None = None,
    industry: str | None = None,
    lookback_days: int = 90,
    max_results_per_query: int = 10,
    max_total_queries: int = 10,
    source_discovery: CompanySourceDiscoveryResult | None = None,
) -> NewsSearchPlan:
    """Build a bounded, recommendation-free news search plan."""
    ticker_u = (ticker or "").upper()
    name = company_name or ticker_u
    profile = get_exchange_profile(exchange, country)

    ctx = {
        "ticker": ticker_u,
        "ticker_lower": ticker_u.lower(),
        "company": name,
        "legal_name": name,
        "sector": sector or "",
        "industry": industry or "",
    }

    def _fill(templates: list[str]) -> list[str]:
        out: list[str] = []
        for t in templates:
            try:
                out.append(t.format(**ctx).strip())
            except (KeyError, IndexError):
                continue
        return out

    # Company-specific queries (exact name + ticker).
    company_queries = [
        f'"{name}" {ticker_u} latest news',
        f'"{name}" earnings guidance product regulatory management',
    ]

    # Primary-source (company-owned) discovery queries.
    primary_source_queries = [
        f'"{name}" investor relations',
        f'"{name}" newsroom press release',
        f"{ticker_u} press release",
    ]

    # Exchange-aware queries from the registry.
    exchange_queries = _fill(profile.news_query_templates)

    # Regulatory queries.
    regulatory_queries = _fill(profile.filing_query_templates)

    # Industry / sector context queries.
    industry_queries: list[str] = []
    if sector:
        industry_queries.append(f'"{sector}" sector recent news')
    if industry:
        industry_queries.append(f'"{industry}" industry recent news')
    if sector and industry:
        industry_queries.append(f'"{industry}" supply chain regulation trends')
    if not industry_queries:
        industry_queries.append(f"{ticker_u} industry peers recent news")

    # Sanitize (drop forbidden phrases), dedupe.
    company_queries = _dedupe_keep_order(_safe(company_queries))
    primary_source_queries = _dedupe_keep_order(_safe(primary_source_queries))
    exchange_queries = _dedupe_keep_order(_safe(exchange_queries))
    regulatory_queries = _dedupe_keep_order(_safe(regulatory_queries))
    industry_queries = _dedupe_keep_order(_safe(industry_queries))

    # Bound the total query count. Priority: company > primary > industry >
    # exchange > regulatory (SEC filings are already fetched by the SEC provider,
    # so regulatory *search* queries are lowest priority).
    budget = max(1, max_total_queries)
    company_queries = company_queries[: max(1, budget // 3)]
    remaining = budget - len(company_queries)
    primary_source_queries = primary_source_queries[: max(0, min(3, remaining))]
    remaining = budget - len(company_queries) - len(primary_source_queries)
    industry_queries = industry_queries[: max(0, min(3, remaining))]
    remaining -= len(industry_queries)
    exchange_queries = exchange_queries[: max(0, min(2, remaining))]
    remaining -= len(exchange_queries)
    regulatory_queries = regulatory_queries[: max(0, remaining)]

    return NewsSearchPlan(
        ticker=ticker_u,
        company_name=name,
        exchange=exchange,
        country=country,
        sector=sector,
        industry=industry,
        lookback_days=lookback_days,
        max_results_per_query=max_results_per_query,
        company_queries=company_queries,
        industry_queries=industry_queries,
        exchange_queries=exchange_queries,
        primary_source_queries=primary_source_queries,
        regulatory_queries=regulatory_queries,
    )
