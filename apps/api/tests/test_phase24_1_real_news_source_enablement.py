"""
Phase 24.1 — Real news + company source enablement tests.

Covers company source discovery, exchange-aware query planning, the configurable
news/search provider abstraction, industry-context news, deterministic relevance
scoring, catalyst integration, richer report sections, source-tier discipline and
safety.

No live external call happens here: the null provider is the default, every
provider is injected/mocked, and source discovery runs offline against the
curated issuer registry / supplied metadata.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
from unittest.mock import patch

import pytest

from app.agents.research_team.catalyst_agent import run_catalyst_agent
from app.integrations.exchange_source_registry import (
    extract_domain,
    get_curated_issuer_source,
    get_exchange_profile,
    is_low_quality_domain,
    normalize_exchange,
    query_has_forbidden_phrase,
    resolve_media_tier,
)
from app.integrations.providers.company_press_release_provider import PressReleaseResult
from app.integrations.providers.free_news_provider import (
    EnvConfiguredNewsProvider,
    GdeltNewsProvider,
    NullNewsProvider,
    StaticNewsProvider,
    get_news_provider,
)
from app.integrations.providers.news_provider_base import dedupe_news_items
from app.integrations.providers.sec_recent_filings_provider import RecentFilingsResult
from app.schemas.catalyst import CatalystEvent, NewsItem
from app.services.catalyst_discovery_service import discover_catalysts
from app.services.company_source_discovery_service import (
    discover_company_sources,
    domain_matches_brand,
)
from app.services.news_query_planner import build_news_search_plan
from app.services.news_relevance_scorer import brand_tokens, score_news_relevance

TODAY = datetime.date(2026, 7, 16)
TODAY_ISO = TODAY.isoformat()
OLD_ISO = (TODAY - datetime.timedelta(days=200)).isoformat()

_FORBIDDEN = re.compile(
    r"(?i)\b(BUY|SELL|HOLD|WATCH)\b|price target|target price|fair value|"
    r"intrinsic value|upside|downside|under\s?valued|over\s?valued"
)


def _assert_no_forbidden(text: str) -> None:
    m = _FORBIDDEN.search(text or "")
    assert m is None, f"forbidden term leaked: {m.group(0)!r}"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeSec:
    def __init__(self, events):
        self._events = events

    async def get_recent_events(
        self, ticker, cik=None, company_name=None, lookback_days=90, max_events=20
    ):
        return RecentFilingsResult(ticker=ticker, cik="320193", events=self._events)


class _FakePress:
    def __init__(self, items):
        self._items = items

    async def get_press_releases(
        self,
        ticker,
        company_name=None,
        website=None,
        lookback_days=90,
        max_items=20,
        feed_urls=None,
    ):
        return PressReleaseResult(ticker=ticker, items=self._items, feed_url="x")


def _sec_event() -> CatalystEvent:
    return CatalystEvent(
        id="sec1",
        ticker="AAPL",
        headline="SEC 8-K filing — AAPL",
        source_tier="T2_regulator_or_gov",
        normalized_event_type="sec_filing",
        form_type="8-K",
        filing_date=TODAY_ISO,
        event_date=TODAY_ISO,
    )


def _press_item(headline="Apple Inc. announces new product line") -> NewsItem:
    return NewsItem(
        headline=headline,
        url="https://www.apple.com/newsroom/1",
        published_at=TODAY_ISO,
        source_tier="T1_primary_filing",
        provider_name="company_press_release",
    )


def _run(coro):
    return asyncio.run(coro)


# ===========================================================================
# 1–7  Company source discovery
# ===========================================================================


class TestCompanySourceDiscovery:
    def test_1_uses_existing_profile_website(self):
        res = _run(
            discover_company_sources(
                ticker="ZZZ",
                company_name="Zeta Widgets Inc.",
                website="https://zetawidgets.com",
            )
        )
        assert res.company_website == "https://zetawidgets.com"
        assert res.has_verified_company_source

    def test_2_discovers_ir_candidate_from_search(self):
        prov = StaticNewsProvider(
            [NewsItem(headline="IR", url="https://zetawidgets.com/investor-relations")]
        )
        res = _run(
            discover_company_sources(
                ticker="ZZZ",
                company_name="Zeta Widgets Inc.",
                search_provider=prov,
            )
        )
        types = {c.source_type for c in res.verified_sources}
        assert "investor_relations" in types

    def test_3_verifies_company_owned_domain_high_confidence(self):
        res = _run(
            discover_company_sources(
                ticker="ZZZ",
                company_name="Zeta Widgets Inc.",
                website="https://zetawidgets.com",
            )
        )
        assert res.confidence >= 0.8

    def test_4_rejects_unrelated_domains(self):
        prov = StaticNewsProvider(
            [
                NewsItem(headline="spam", url="https://randomseoblog.example/zeta"),
                NewsItem(headline="social", url="https://twitter.com/zeta"),
            ]
        )
        res = _run(
            discover_company_sources(
                ticker="ZZZ", company_name="Zeta Widgets Inc.", search_provider=prov
            )
        )
        verified_urls = {c.url for c in res.verified_sources}
        assert "https://twitter.com/zeta" not in verified_urls
        assert "https://randomseoblog.example/zeta" not in verified_urls

    def test_5_no_website_no_provider_warns(self):
        res = _run(
            discover_company_sources(ticker="ZZZ", company_name="Zeta Widgets Inc.")
        )
        assert not res.has_verified_company_source
        assert any("primary news source unavailable" in w.lower() for w in res.warnings)

    def test_6_aapl_curated_produces_company_source(self):
        res = _run(
            discover_company_sources(
                ticker="AAPL",
                company_name="Apple Inc.",
                exchange="NASDAQ",
                country="US",
            )
        )
        assert res.company_website
        assert res.press_release_feed_url
        assert res.has_verified_company_source
        assert res.candidate_feed_urls()

    def test_7_does_not_fabricate_low_confidence(self):
        prov = StaticNewsProvider(
            [NewsItem(headline="unrelated", url="https://randomseoblog.example/x")]
        )
        res = _run(
            discover_company_sources(
                ticker="ZZZ", company_name="Zeta Widgets Inc.", search_provider=prov
            )
        )
        assert res.company_website is None
        assert res.confidence == 0.0

    def test_domain_matches_brand_helper(self):
        assert domain_matches_brand("zetawidgets.com", brand_tokens("Zeta Widgets Inc."))
        assert not domain_matches_brand("example.org", brand_tokens("Zeta Widgets Inc."))


# ===========================================================================
# 8–11  Exchange registry / query planning
# ===========================================================================


class TestQueryPlanning:
    def test_8_nasdaq_plan_includes_expected_queries(self):
        plan = build_news_search_plan(
            ticker="AAPL",
            company_name="Apple Inc.",
            exchange="NASDAQ",
            country="US",
            sector="Information Technology",
            industry="Consumer Electronics",
        )
        blob = " ".join(
            plan.company_queries
            + plan.exchange_queries
            + plan.primary_source_queries
            + plan.regulatory_queries
        )
        assert "AAPL" in blob
        assert "Apple Inc." in blob
        assert "Nasdaq" in blob
        assert "investor relations" in blob.lower()

    def test_9_unknown_exchange_falls_back(self):
        plan = build_news_search_plan(
            ticker="XYZ", company_name="Example SA", exchange="XSWX", country="CH"
        )
        assert plan.company_queries
        # generic profile has no exchange news templates
        assert plan.total_query_count() >= 1

    def test_10_query_count_bounded(self):
        plan = build_news_search_plan(
            ticker="AAPL",
            company_name="Apple Inc.",
            exchange="NASDAQ",
            country="US",
            sector="Information Technology",
            industry="Consumer Electronics",
            max_total_queries=10,
        )
        assert plan.total_query_count() <= 10

    def test_11_no_forbidden_phrases(self):
        plan = build_news_search_plan(
            ticker="AAPL",
            company_name="Apple Inc.",
            exchange="NASDAQ",
            country="US",
            sector="Information Technology",
            industry="Consumer Electronics",
        )
        allq = (
            plan.company_queries
            + plan.industry_queries
            + plan.exchange_queries
            + plan.primary_source_queries
            + plan.regulatory_queries
        )
        assert allq
        assert not any(query_has_forbidden_phrase(q) for q in allq)

    def test_exchange_normalisation(self):
        assert normalize_exchange("NASDAQ") == "NASDAQ"
        assert normalize_exchange("US") == "US"
        assert get_exchange_profile("NASDAQ").exchange_profile_url_template
        assert get_curated_issuer_source("AAPL") is not None
        assert get_curated_issuer_source("ZZZ") is None


# ===========================================================================
# 12–17  News provider
# ===========================================================================


class TestNewsProvider:
    def test_12_missing_config_nonblocking(self, monkeypatch):
        monkeypatch.delenv("NEWS_PROVIDER_NAME", raising=False)
        monkeypatch.delenv("NEWS_API_KEY", raising=False)
        prov = get_news_provider()
        assert isinstance(prov, NullNewsProvider)
        assert _run(prov.search("anything")) == []

    def test_13_mock_provider_normalized_items(self):
        prov = StaticNewsProvider(
            [NewsItem(headline="Apple Inc. news", url="https://reuters.com/a")]
        )
        items = _run(prov.search("Apple Inc.", query_type="company"))
        assert items and items[0].headline == "Apple Inc. news"
        assert items[0].query_type == "company"

    def test_14_http_failure_returns_empty(self):
        prov = EnvConfiguredNewsProvider(
            api_key="k", base_url="https://example.test/search"
        )
        with patch(
            "app.integrations.providers.free_news_provider.httpx.AsyncClient",
            side_effect=RuntimeError("boom"),
        ):
            assert _run(prov.search("q")) == []

    def test_14b_malformed_payload_returns_empty(self):
        prov = EnvConfiguredNewsProvider(api_key="k", base_url="https://x")
        assert prov._parse_payload("not-json", 10, "q", "company") == []
        assert prov._parse_payload({"nope": 1}, 10, "q", "company") == []
        assert GdeltNewsProvider()._parse_payload({"x": 1}, 10, "q", "company") == []

    def test_15_dedupe_by_url_and_title(self):
        a = NewsItem(
            headline="Same story",
            url="https://x.example/a?utm_source=x",
            source_tier="T5_api_aggregator",
            provider_name="agg",
        )
        b = NewsItem(
            headline="Same story",
            url="https://x.example/a",
            source_tier="T4_quality_media",
            provider_name="media",
        )
        out = dedupe_news_items([a, b])
        assert len(out) == 1
        assert out[0].source_tier == "T4_quality_media"

    def test_16_aggregator_stays_t5(self):
        prov = EnvConfiguredNewsProvider(api_key="k", base_url="https://x")
        items = prov._parse_payload(
            {"articles": [{"title": "t", "url": "https://randomnews.example/a"}]},
            10,
            "q",
            "company",
        )
        assert items[0].source_tier == "T5_api_aggregator"

    def test_17_trusted_domain_mapped_to_t4(self):
        prov = EnvConfiguredNewsProvider(api_key="k", base_url="https://x")
        items = prov._parse_payload(
            {"articles": [{"title": "t", "url": "https://www.reuters.com/a"}]},
            10,
            "q",
            "company",
        )
        assert items[0].source_tier == "T4_quality_media"
        assert resolve_media_tier("reuters.com") == "T4_quality_media"
        assert resolve_media_tier("randomnews.example") is None


# ===========================================================================
# 18–20  Industry news
# ===========================================================================


class TestIndustryNews:
    def test_18_industry_query_generated(self):
        plan = build_news_search_plan(
            ticker="AAPL",
            company_name="Apple Inc.",
            sector="Information Technology",
            industry="Consumer Electronics",
        )
        assert plan.industry_queries
        assert any("Consumer Electronics" in q for q in plan.industry_queries)

    def test_19_industry_item_marked_context(self):
        item = NewsItem(
            headline="Consumer Electronics industry supply chain tariffs",
            url="https://bloomberg.com/b",
            published_at=TODAY_ISO,
            source_tier="T4_quality_media",
            query_type="industry",
        )
        sc = score_news_relevance(
            item,
            company_name="Apple Inc.",
            ticker="AAPL",
            sector="Information Technology",
            industry="Consumer Electronics",
            query_type="industry",
            now=TODAY,
        )
        assert sc.is_industry_context
        assert not sc.is_company_specific

    def test_20_industry_not_positive_company_catalyst(self):
        news = [
            NewsItem(
                headline="Consumer Electronics industry launches record expansion",
                url="https://bloomberg.com/b",
                published_at=TODAY_ISO,
                source_tier="T4_quality_media",
                query_type="industry",
            )
        ]
        res = _run(
            discover_catalysts(
                ticker="AAPL",
                company_name="Apple Inc.",
                sector="Information Technology",
                industry="Consumer Electronics",
                sec_provider=_FakeSec([]),
                press_release_provider=_FakePress([]),
                news_provider=StaticNewsProvider(news),
            )
        )
        assert res.industry_events
        ev = res.industry_events[0]
        assert ev.is_industry_context and not ev.is_company_specific
        assert ev.catalyst_category == "macro_sector"
        assert ev.catalyst_direction in ("neutral", "mixed")
        # It must not appear as a positive company catalyst.
        assert all(e.catalyst_direction != "positive" for e in res.events)


# ===========================================================================
# 21–25  Relevance scorer
# ===========================================================================


class TestRelevanceScorer:
    def _score(self, item, **kw):
        kw.setdefault("company_name", "Apple Inc.")
        kw.setdefault("ticker", "AAPL")
        kw.setdefault("now", TODAY)
        return score_news_relevance(item, **kw)

    def test_21_company_plus_ticker_high(self):
        it = NewsItem(
            headline="Apple Inc. (AAPL) reports record quarterly earnings",
            published_at=TODAY_ISO,
        )
        sc = self._score(it)
        assert sc.relevance_level == "high"
        assert sc.is_company_specific

    def test_22_sector_only_industry_context(self):
        it = NewsItem(
            headline="Consumer Electronics supply chain faces tariffs",
            published_at=TODAY_ISO,
            query_type="industry",
        )
        sc = self._score(
            it, sector="Information Technology", industry="Consumer Electronics"
        )
        assert sc.is_industry_context
        assert sc.relevance_level in ("medium", "high")

    def test_23_unrelated_apple_fruit_filtered(self):
        it = NewsItem(headline="Best apple pie recipe for autumn harvest")
        sc = self._score(it)
        assert sc.relevance_level == "irrelevant"

    def test_24_stock_spam_domain_filtered(self):
        it = NewsItem(
            headline="AAPL stock forecast will it soar",
            url="https://walletinvestor.com/aapl",
        )
        sc = self._score(it)
        assert sc.relevance_level == "irrelevant"
        assert is_low_quality_domain("walletinvestor.com")

    def test_25_recency_improves_score(self):
        recent = NewsItem(
            headline="Apple Inc. AAPL announces update", published_at=TODAY_ISO
        )
        old = NewsItem(
            headline="Apple Inc. AAPL announces update", published_at=OLD_ISO
        )
        s_recent = self._score(recent)
        s_old = self._score(old)
        assert s_recent.relevance_score > s_old.relevance_score


# ===========================================================================
# 26–37  Catalyst integration
# ===========================================================================


def _rich_result():
    news = [
        NewsItem(
            headline="Apple Inc. (AAPL) posts record quarterly revenue",
            url="https://reuters.com/a",
            published_at=TODAY_ISO,
            source_tier="T4_quality_media",
            provider_name="news",
            query_type="company",
        ),
        NewsItem(
            headline="Consumer Electronics industry supply chain tariffs",
            url="https://bloomberg.com/b",
            published_at=TODAY_ISO,
            source_tier="T4_quality_media",
            provider_name="news",
            query_type="industry",
        ),
    ]
    return _run(
        discover_catalysts(
            ticker="AAPL",
            company_name="Apple Inc.",
            exchange="NASDAQ",
            country="US",
            sector="Information Technology",
            industry="Consumer Electronics",
            sec_provider=_FakeSec([_sec_event()]),
            press_release_provider=_FakePress([_press_item()]),
            news_provider=StaticNewsProvider(news),
            include_source_discovery=True,
        )
    )


class TestCatalystIntegration:
    def test_26_news_becomes_company_event(self):
        res = _rich_result()
        assert any(
            e.normalized_event_type == "news_article" and e.is_company_specific
            for e in res.news_events
        )

    def test_27_press_release_is_t1_event(self):
        res = _rich_result()
        assert res.press_release_events
        assert res.press_release_events[0].source_tier == "T1_primary_filing"

    def test_28_industry_separate_from_company(self):
        res = _rich_result()
        assert res.industry_events
        assert all(not e.is_industry_context for e in res.events)

    def test_29_coverage_improves_from_filings_only(self):
        sec_only = _run(
            discover_catalysts(
                ticker="AAPL",
                company_name="Apple Inc.",
                sec_provider=_FakeSec([_sec_event()]),
                press_release_provider=_FakePress([]),
                news_provider=NullNewsProvider(),
            )
        )
        assert sec_only.coverage_quality == "filings_only"
        rich = _rich_result()
        assert rich.coverage_quality in ("limited", "adequate", "strong")

    def test_30_warnings_list_missing_providers_only(self):
        res = _rich_result()
        # press + news + sec all succeeded → none of them "missing"
        assert "company_press_release" not in res.missing_sources
        assert "news_provider" not in res.missing_sources
        assert "sec_recent_filings" not in res.missing_sources

    def test_31_source_quality_not_claim_news_missing_when_present(self):
        out = run_catalyst_agent(_rich_result())
        assert not any(
            "Configure a news/search provider" in r
            for r in out.source_quality_recommendations
        )

    def test_32_source_quality_recommends_primary_for_aggregator(self):
        agg_news = [
            NewsItem(
                headline="Apple Inc. AAPL rumor from aggregator",
                url="https://randomnews.example/a",
                published_at=TODAY_ISO,
                source_tier="T5_api_aggregator",
                query_type="company",
            )
        ]
        res = _run(
            discover_catalysts(
                ticker="AAPL",
                company_name="Apple Inc.",
                sec_provider=_FakeSec([_sec_event()]),
                press_release_provider=_FakePress([]),
                news_provider=StaticNewsProvider(agg_news),
            )
        )
        out = run_catalyst_agent(res)
        assert any("aggregator" in r.lower() for r in out.source_quality_recommendations)

    def test_33_risk_includes_news_quality_risk(self):
        agg_news = [
            NewsItem(
                headline="Apple Inc. AAPL rumor",
                url="https://randomnews.example/a",
                published_at=TODAY_ISO,
                source_tier="T5_api_aggregator",
                query_type="company",
            )
        ]
        res = _run(
            discover_catalysts(
                ticker="AAPL",
                company_name="Apple Inc.",
                sec_provider=_FakeSec([_sec_event()]),
                press_release_provider=_FakePress([]),
                news_provider=StaticNewsProvider(agg_news),
            )
        )
        out = run_catalyst_agent(res)
        assert any(
            "aggregator" in r.lower() or "source diversity" in r.lower()
            for r in out.risk_flags
        )

    def test_34_committee_has_open_questions(self):
        out = run_catalyst_agent(_rich_result())
        assert out.committee_open_questions
        assert any("coverage status" in q.lower() for q in out.committee_open_questions)

    def test_35_provider_failure_no_crash(self):
        class _BoomSec:
            async def get_recent_events(self, *a, **k):
                raise RuntimeError("sec down")

        class _BoomPress:
            async def get_press_releases(self, *a, **k):
                raise RuntimeError("press down")

        class _BoomNews(NullNewsProvider):
            async def search(self, *a, **k):
                raise RuntimeError("news down")

        res = _run(
            discover_catalysts(
                ticker="AAPL",
                company_name="Apple Inc.",
                sec_provider=_BoomSec(),
                press_release_provider=_BoomPress(),
                news_provider=_BoomNews(),
            )
        )
        assert res.human_review_required is True
        assert res.warnings

    def test_36_machine_readable_payload_has_news_and_industry(self):
        section = _build_news_catalyst_discovery(_rich_result().to_report_dict())
        assert "industry_context_events" in section
        assert "company_sources" in section
        assert section["available"] is True

    def test_37_sec_only_behavior_preserved(self):
        res = _run(
            discover_catalysts(
                ticker="AAPL",
                company_name="Apple Inc.",
                sec_provider=_FakeSec([_sec_event()]),
                include_news=False,
                include_press_releases=False,
                include_source_discovery=False,
            )
        )
        assert res.coverage_quality == "filings_only"
        assert res.filing_events


# ===========================================================================
# 38–45  Safety
# ===========================================================================

from app.services.final_report_generator import (  # noqa: E402
    _build_news_catalyst_discovery,
    run_safety_gate,
)


class TestSafety:
    def test_38_positive_news_no_recommendation(self):
        news = [
            NewsItem(
                headline="Apple Inc. AAPL launches record product with raised guidance",
                url="https://reuters.com/a",
                published_at=TODAY_ISO,
                source_tier="T4_quality_media",
                query_type="company",
            )
        ]
        res = _run(
            discover_catalysts(
                ticker="AAPL",
                company_name="Apple Inc.",
                sec_provider=_FakeSec([]),
                press_release_provider=_FakePress([]),
                news_provider=StaticNewsProvider(news),
            )
        )
        out = run_catalyst_agent(res)
        _assert_no_forbidden(out.markdown)
        _assert_no_forbidden(json.dumps(res.to_report_dict()))

    def test_39_negative_news_no_recommendation(self):
        news = [
            NewsItem(
                headline="Apple Inc. AAPL faces investigation and lawsuit",
                url="https://reuters.com/a",
                published_at=TODAY_ISO,
                source_tier="T4_quality_media",
                query_type="company",
            )
        ]
        res = _run(
            discover_catalysts(
                ticker="AAPL",
                company_name="Apple Inc.",
                sec_provider=_FakeSec([]),
                press_release_provider=_FakePress([]),
                news_provider=StaticNewsProvider(news),
            )
        )
        out = run_catalyst_agent(res)
        _assert_no_forbidden(out.markdown)

    def test_40_41_no_valuation_language(self):
        res = _rich_result()
        blob = json.dumps(res.to_report_dict()) + run_catalyst_agent(res).markdown
        _assert_no_forbidden(blob)

    def test_42_human_review_required(self):
        assert _rich_result().human_review_required is True

    def test_43_safety_gate_passes(self):
        section = _build_news_catalyst_discovery(_rich_result().to_report_dict())
        assert run_safety_gate({"news_catalyst_discovery": section}).passed is True

    def test_44_external_headline_sanitised(self):
        news = [
            NewsItem(
                headline="Analyst BUY Apple Inc. AAPL: upside and price target hiked; buyback",
                url="https://reuters.com/a",
                published_at=TODAY_ISO,
                source_tier="T4_quality_media",
                query_type="company",
            )
        ]
        res = _run(
            discover_catalysts(
                ticker="AAPL",
                company_name="Apple Inc.",
                sec_provider=_FakeSec([]),
                press_release_provider=_FakePress([]),
                news_provider=StaticNewsProvider(news),
            )
        )
        section = _build_news_catalyst_discovery(res.to_report_dict())
        _assert_no_forbidden(json.dumps(section))
        assert run_safety_gate({"news_catalyst_discovery": section}).passed is True

    def test_45_schema_false_final_report_ok(self):
        from app.services.final_report_generator import (
            _assemble_final_report_content,
        )

        content = _assemble_final_report_content(
            company_snapshot=None,
            company_record={"name": "Apple", "ticker": "AAPL"},
            candidate=None,
            scorecard=None,
            financial_data_summary=None,
            source_quality_summary=None,
            research_completeness_summary=None,
            upgraded_citation_validation=None,
            bull_case_summary=None,
            bear_case_summary=None,
            risk_summary=None,
            valuation_guard_summary=None,
            committee_chair_summary=None,
            fundamentals_data=None,
            fundamentals_available=None,
            source_tier=None,
            sources=[],
            citations=[],
            report=None,
            agent_run_id=None,
            schema_valid=False,
            human_review_required=True,
            catalyst_discovery=_rich_result().to_report_dict(),
        )
        assert "news_catalyst_discovery" in content
        assert run_safety_gate(content).passed is True


# ===========================================================================
# 46–48  Backward compatibility / no live calls
# ===========================================================================


class TestBackwardCompat:
    def test_46_static_provider_deterministic(self):
        prov = StaticNewsProvider([NewsItem(headline="x", url="https://a.example/1")])
        a = _run(prov.search("q"))
        b = _run(prov.search("q"))
        assert [i.headline for i in a] == [i.headline for i in b]

    def test_47_default_provider_is_null(self, monkeypatch):
        monkeypatch.delenv("NEWS_PROVIDER_NAME", raising=False)
        assert isinstance(get_news_provider(), NullNewsProvider)

    def test_48_no_source_discovery_no_curated_by_default(self):
        # Without opting into source discovery, no curated feed is injected.
        res = _run(
            discover_catalysts(
                ticker="AAPL",
                company_name="Apple Inc.",
                sec_provider=_FakeSec([_sec_event()]),
                press_release_provider=_FakePress([]),
                news_provider=NullNewsProvider(),
            )
        )
        assert res.company_sources is None
        assert res.coverage_quality == "filings_only"


@pytest.mark.parametrize("exchange", ["NASDAQ", "NYSE", "AMEX", "US", "XSWX", None])
def test_registry_never_crashes_on_exchange(exchange):
    profile = get_exchange_profile(exchange)
    assert profile.exchange
    plan = build_news_search_plan(ticker="T", company_name="T Co", exchange=exchange)
    assert plan.total_query_count() >= 1


def test_extract_domain_helper():
    assert extract_domain("https://www.apple.com/newsroom") == "apple.com"
    assert extract_domain(None) == ""
