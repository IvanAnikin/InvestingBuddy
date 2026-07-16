"""
Phase 24.1 — Deterministic news relevance scorer.

Scores a normalised ``NewsItem`` for how relevant it is to a specific company vs
its industry/sector context. Fully deterministic and offline (no LLM, no
network). The relevance score/level is a model-derived signal (T6) used only to
filter and route items — it is NEVER a recommendation.

Outputs per item:
  - relevance_score  : 0..1
  - relevance_level  : high | medium | low | irrelevant
  - is_company_specific / is_industry_context (mutually informative flags)
  - relevance_reason : short, controlled-vocabulary explanation

Filtering behaviour:
  - Brand words in a clearly non-company context (e.g. "apple pie recipe") are
    marked irrelevant.
  - Known low-quality / stock-prediction / SEO domains are marked irrelevant.
  - Aggregator-only items with no company or sector signal are marked
    irrelevant.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from pydantic import BaseModel, Field

from app.integrations.exchange_source_registry import (
    extract_domain,
    is_low_quality_domain,
    is_social_media_domain,
)
from app.integrations.financial_data_provider import SourceTier
from app.schemas.catalyst import NewsItem
from app.schemas.company_sources import RelevanceLevel

_T1 = SourceTier.T1_primary_filing.value
_T2 = SourceTier.T2_regulator_or_gov.value
_T3 = SourceTier.T3_industry_specialist.value
_T4 = SourceTier.T4_quality_media.value

# Words dropped when extracting brand tokens from a legal name.
_SUFFIX_WORDS = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "plc", "llc", "lp", "holdings", "holding", "group", "sa", "ag",
    "nv", "the", "and", "class", "companies", "worldwide", "international",
}

# Single-word brands that are also common English words — require a ticker or
# finance context before treating a name match as company-specific.
_AMBIGUOUS_BRAND_WORDS = {
    "apple", "amazon", "meta", "block", "square", "target", "gap", "shell",
    "visa", "mastercard", "oracle", "arm", "nvidia", "broadcom", "ford",
    "general", "united", "american", "national", "capital", "match", "carnival",
}

_FINANCE_KEYWORDS = (
    "earnings", "revenue", "guidance", "quarterly", "results", "dividend",
    "shares", "shareholder", "sec", "filing", "10-k", "10-q", "8-k", "ceo",
    "cfo", "forecast", "nasdaq", "nyse", "investor", "profit", "sales",
    "market cap", "acquisition", "merger", "stock", "analyst", "outlook",
    "product launch", "contract", "partnership", "regulator",
)

# Non-company (food/agriculture) context markers for ambiguous brand words.
_NON_COMPANY_CONTEXT_KEYWORDS = (
    "recipe", "orchard", "fruit", "pie", "cider", "harvest", "grocery",
    "snack", "juice", "farmer", "crop", "dessert", "smoothie", "cooking",
    "baking", "salad", "vinegar",
)

_CATALYST_KEYWORDS = (
    "launch", "unveil", "acquire", "acquisition", "merger", "contract",
    "lawsuit", "investigation", "guidance", "earnings", "partnership",
    "dividend", "recall", "approval", "regulatory", "resign", "appoint",
)


class NewsRelevanceScore(BaseModel):
    relevance_score: float = Field(ge=0.0, le=1.0)
    relevance_level: str
    relevance_reason: str
    is_company_specific: bool = False
    is_industry_context: bool = False


def brand_tokens(company_name: str | None) -> list[str]:
    """Extract significant lower-case brand tokens from a legal name."""
    if not company_name:
        return []
    cleaned = re.sub(r"[^a-z0-9\s]", " ", company_name.lower())
    tokens = [t for t in cleaned.split() if t and t not in _SUFFIX_WORDS]
    return [t for t in tokens if len(t) >= 2]


def _is_ambiguous_brand(tokens: list[str]) -> bool:
    return len(tokens) == 1 and tokens[0] in _AMBIGUOUS_BRAND_WORDS


def _word_in(text: str, token: str) -> bool:
    return re.search(r"\b" + re.escape(token) + r"\b", text) is not None


def _ticker_in_text(ticker: str, text: str) -> bool:
    if not ticker or len(ticker) < 2:
        return False
    return re.search(r"\b" + re.escape(ticker) + r"\b", text, re.IGNORECASE) is not None


def _phrase_tokens(phrase: str | None) -> list[str]:
    if not phrase:
        return []
    cleaned = re.sub(r"[^a-z0-9\s]", " ", phrase.lower())
    return [t for t in cleaned.split() if len(t) >= 4]


def _phrase_match(text: str, phrase: str | None) -> bool:
    toks = _phrase_tokens(phrase)
    return any(_word_in(text, t) for t in toks)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(k in text for k in keywords)


def _is_recent(published_at: str | None, lookback_days: int, now: date | None) -> bool | None:
    if not published_at:
        return None
    today = now or datetime.now(timezone.utc).date()
    try:
        d = datetime.fromisoformat(published_at.replace("Z", "+00:00")[:19]).date()
    except ValueError:
        try:
            d = date.fromisoformat(published_at[:10])
        except ValueError:
            return None
    age = (today - d).days
    return 0 <= age <= lookback_days


def _level(score: float) -> str:
    if score >= 0.6:
        return RelevanceLevel.high.value
    if score >= 0.4:
        return RelevanceLevel.medium.value
    if score >= 0.2:
        return RelevanceLevel.low.value
    return RelevanceLevel.irrelevant.value


def score_news_relevance(
    item: NewsItem,
    *,
    company_name: str | None,
    ticker: str | None,
    sector: str | None = None,
    industry: str | None = None,
    query_type: str | None = None,
    lookback_days: int = 90,
    now: date | None = None,
) -> NewsRelevanceScore:
    """Score one NewsItem's relevance to the company / its industry context."""
    title = item.headline or ""
    snippet = item.summary or ""
    original = f"{title} {snippet}"
    text = original.lower()
    domain = extract_domain(item.url)

    tokens = brand_tokens(company_name)
    ticker_u = (ticker or "").upper()
    ticker_match = _ticker_in_text(ticker_u, original)
    matched_tokens = [t for t in tokens if _word_in(text, t)]
    name_match = bool(matched_tokens)
    strong_name_match = len(matched_tokens) >= 2
    ambiguous = _is_ambiguous_brand(tokens)

    finance_context = _contains_any(text, _FINANCE_KEYWORDS)
    non_company_context = _contains_any(text, _NON_COMPANY_CONTEXT_KEYWORDS)
    catalyst_kw = _contains_any(text, _CATALYST_KEYWORDS)
    sector_match = _phrase_match(text, sector)
    industry_match = _phrase_match(text, industry)

    # Hard filters --------------------------------------------------------
    if is_low_quality_domain(domain):
        return NewsRelevanceScore(
            relevance_score=0.05,
            relevance_level=RelevanceLevel.irrelevant.value,
            relevance_reason="Low-quality / stock-prediction domain — filtered.",
            is_company_specific=False,
            is_industry_context=False,
        )
    if (
        name_match
        and non_company_context
        and not ticker_match
        and not finance_context
    ):
        return NewsRelevanceScore(
            relevance_score=0.0,
            relevance_level=RelevanceLevel.irrelevant.value,
            relevance_reason=(
                "Brand word appears in a non-company (food/agriculture) context."
            ),
            is_company_specific=False,
            is_industry_context=False,
        )

    company_specific = bool(ticker_match) or (
        name_match and (not ambiguous or finance_context or strong_name_match)
    )

    reasons: list[str] = []
    score = 0.0

    if ticker_match:
        score += 0.40
        reasons.append("ticker match")
    if name_match:
        score += 0.25 if ambiguous else 0.40
        reasons.append("company name match")

    tier = item.source_tier
    if tier in (_T1, _T2):
        score += 0.15
    elif tier in (_T3, _T4):
        score += 0.10

    recent = _is_recent(item.published_at, lookback_days, now)
    if recent is True:
        score += 0.10
        reasons.append("recent")
    elif recent is False:
        score -= 0.10
        reasons.append("outside lookback")

    if catalyst_kw:
        score += 0.05
        reasons.append("catalyst keyword")

    if is_social_media_domain(domain):
        score -= 0.10
        reasons.append("social-media source")

    # Industry / sector context routing --------------------------------
    is_industry_context = False
    if not company_specific and (sector_match or industry_match):
        is_industry_context = True
        score = max(score, 0.45)
        reasons.append("industry/sector context")
    elif not company_specific and query_type == "industry":
        # Industry query that surfaced nothing sector-specific → weak.
        reasons.append("industry query, no clear match")

    score = round(max(0.0, min(1.0, score)), 2)
    level = _level(score)
    reason = ", ".join(reasons) or "no strong company or sector signal"

    return NewsRelevanceScore(
        relevance_score=score,
        relevance_level=level,
        relevance_reason=reason,
        is_company_specific=company_specific and not is_industry_context,
        is_industry_context=is_industry_context,
    )


def apply_relevance(
    item: NewsItem,
    *,
    company_name: str | None,
    ticker: str | None,
    sector: str | None = None,
    industry: str | None = None,
    lookback_days: int = 90,
    now: date | None = None,
) -> NewsItem:
    """Return a copy of ``item`` with relevance fields populated."""
    score = score_news_relevance(
        item,
        company_name=company_name,
        ticker=ticker,
        sector=sector,
        industry=industry,
        query_type=item.query_type,
        lookback_days=lookback_days,
        now=now,
    )
    return item.model_copy(
        update={
            "relevance_score": score.relevance_score,
            "relevance_level": score.relevance_level,
            "is_company_specific": score.is_company_specific,
            "is_industry_context": score.is_industry_context,
        }
    )
