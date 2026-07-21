"""
Phase 24 — News + Catalyst Discovery.

All tests run OFFLINE — no network calls, no API keys required.

Coverage map (task section 17):
  Provider/data model      1–3
  SEC provider             4–11
  News / press provider    12–16
  Classifier               17–24
  Workflow / report        25–36
  Safety                   37–44
  Backward compatibility   45–47
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.research_team.catalyst_agent import run_catalyst_agent
from app.integrations.financial_data_service import (
    FinancialDataService as _RealFinancialDataService,
)
from app.integrations.providers.company_press_release_provider import (
    PressReleaseResult,
    discover_feed_urls,
    parse_feed,
)
from app.integrations.providers.free_news_provider import (
    NullNewsProvider,
    StaticNewsProvider,
    get_news_provider,
)
from app.integrations.providers.news_provider_base import dedupe_news_items
from app.integrations.providers.sec_recent_filings_provider import (
    RecentFilingsResult,
    SecRecentFilingsProvider,
    parse_recent_filings,
)
from app.schemas.catalyst import (
    CatalystCoverageStatus,
    CatalystDirection,
    CatalystDiscoveryResult,
    CatalystEvent,
    CatalystStrength,
    EvidenceStrength,
    NewsItem,
    make_catalyst_event_id,
    neutralize_forbidden_terms,
    summarize_events,
)
from app.services.catalyst_classifier import apply_classification, classify_catalyst
from app.services.catalyst_discovery_service import discover_catalysts

_FORBIDDEN_TERMS = [
    "BUY", "SELL", "HOLD", "WATCH",
    "PRICE TARGET", "TARGET PRICE", "FAIR VALUE", "INTRINSIC VALUE",
    "UPSIDE", "DOWNSIDE", "UNDERVALUED", "OVERVALUED", "GUARANTEED RETURN",
]


def _assert_no_forbidden(text: str) -> None:
    upper = text.upper()
    for term in _FORBIDDEN_TERMS:
        assert term not in upper, f"Forbidden term '{term}' present in output"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _submissions(forms_items):
    """Build a minimal SEC submissions payload from (form, filingDate, items) tuples."""
    n = len(forms_items)
    return {
        "filings": {
            "recent": {
                "form": [f[0] for f in forms_items],
                "filingDate": [f[1] for f in forms_items],
                "reportDate": [f[1] for f in forms_items],
                "accessionNumber": [f"0000320193-26-{i:06d}" for i in range(n)],
                "primaryDocument": [f"doc-{i}.htm" for i in range(n)],
                "items": [f[2] for f in forms_items],
                "primaryDocDescription": [f[0] for f in forms_items],
            }
        }
    }


_RECENT = "2026-07-01"


def _sec_events(company_name="Apple Inc."):
    data = _submissions(
        [
            ("8-K", _RECENT, "2.02,9.01"),
            ("8-K", _RECENT, "5.02"),
            ("10-K", "2026-06-15", ""),
        ]
    )
    return parse_recent_filings(
        data, "AAPL", "320193", company_name=company_name, lookback_days=120
    )


def _prepared_result(events=None, coverage=None, warnings=None):
    events = events if events is not None else _sec_events()
    filing = [e for e in events if e.source_tier == "T2_regulator_or_gov"]
    summary = summarize_events(events, 90)
    return CatalystDiscoveryResult(
        ticker="AAPL",
        company_name="Apple Inc.",
        events=events,
        filing_events=filing,
        summary=summary,
        warnings=warnings or [],
        coverage_quality=coverage or summary.catalyst_coverage_status,
        source_summary={
            e.source_tier: sum(1 for x in events if x.source_tier == e.source_tier)
            for e in events
        },
    )


# ===========================================================================
# 1–3  Provider / data model
# ===========================================================================


class TestDataModel:
    def test_1_catalyst_event_accepts_sec_filing(self):
        ev = _sec_events()[0]
        assert ev.form_type == "8-K"
        assert ev.accession_number
        assert ev.source_tier == "T2_regulator_or_gov"
        assert ev.model_label_tier == "T6_model_estimate"

    def test_2_discovery_result_summarizes_counts(self):
        res = _prepared_result()
        assert res.summary.total_events == len(res.events)
        assert res.summary.total_events == 3

    def test_3_source_tiers_preserved(self):
        sec = _sec_events()[0]
        assert sec.source_tier == "T2_regulator_or_gov"
        assert sec.model_label_tier == "T6_model_estimate"  # label always T6
        item = NewsItem(headline="x", provider_name="agg")
        assert item.source_tier == "T5_api_aggregator"
        pr = parse_feed(
            "<?xml version='1.0'?><rss><channel><item><title>x</title></item></channel></rss>",
            "news",
        )
        assert pr[0].source_tier == "T1_primary_filing"  # company-owned primary


# ===========================================================================
# 4–11  SEC provider
# ===========================================================================


class TestSecProvider:
    def test_4_creates_8k_event(self):
        events = _sec_events()
        eightk = [e for e in events if e.form_type == "8-K"]
        assert len(eightk) == 2
        assert eightk[0].normalized_event_type == "sec_filing"

    def test_5_missing_cik_no_crash(self):
        provider = SecRecentFilingsProvider()
        with patch.object(provider, "_resolve_cik", new=AsyncMock(return_value=None)):
            res = asyncio.run(provider.get_recent_events("ZZZZ"))
        assert res.events == []
        assert any("CIK" in w for w in res.warnings)

    def test_6_sec_failure_warns(self):
        import httpx

        provider = SecRecentFilingsProvider()
        with patch.object(
            provider, "_fetch_submissions",
            new=AsyncMock(side_effect=httpx.ConnectError("boom")),
        ):
            res = asyncio.run(provider.get_recent_events("AAPL", cik="320193"))
        assert res.events == []
        assert any("fetch failed" in w.lower() for w in res.warnings)

    def test_7_accession_urls_safe(self):
        ev = _sec_events()[0]
        assert ev.source_url.startswith("https://www.sec.gov/Archives/edgar/data/")
        assert "000032019326" in ev.source_url.replace("-", "")

    def test_8_item_numbers_parsed(self):
        events = _sec_events()
        earnings_8k = [e for e in events if e.item_numbers and "2.02" in e.item_numbers]
        assert earnings_8k
        assert "9.01" in earnings_8k[0].item_numbers

    def test_9_routine_10k_neutral_filing_event(self):
        events = _sec_events()
        tenk = [e for e in events if e.form_type == "10-K"][0]
        assert tenk.catalyst_category == "filing_event"
        assert tenk.catalyst_direction == "neutral"

    def test_10_item_202_earnings_neutral(self):
        events = _sec_events()
        e202 = [e for e in events if "2.02" in e.item_numbers][0]
        assert e202.catalyst_category == "earnings"
        assert e202.catalyst_direction in ("neutral", "mixed")

    def test_11_negative_items_classified_risk(self):
        for item, _ in [("2.06", None), ("3.01", None), ("4.01", None)]:
            c = classify_catalyst(
                headline="8-K", summary="", source_tier="T2_regulator_or_gov",
                form_type="8-K", item_numbers=[item],
            )
            assert c.catalyst_direction == "negative"
            assert c.catalyst_category == "risk_event"


# ===========================================================================
# 12–16  News / press provider
# ===========================================================================


class TestNewsPressProviders:
    def test_12_news_abstraction_returns_items(self):
        prov = StaticNewsProvider(
            [NewsItem(headline="Co launches product", url="https://n/1", provider_name="s")]
        )
        items = asyncio.run(prov.search_company_news("AAPL"))
        assert len(items) == 1
        assert items[0].source_tier == "T5_api_aggregator"

    def test_13_missing_news_config_nonblocking(self):
        provider = get_news_provider()  # no env config
        assert isinstance(provider, NullNewsProvider)
        items = asyncio.run(provider.search_company_news("AAPL"))
        assert items == []

    def test_14_press_no_website_graceful(self):
        res = asyncio.run(
            discover_catalysts(
                ticker="AAPL",
                sec_provider=_FakeSec([]),
                news_provider=NullNewsProvider(),
                # no press provider injected + no website → real provider returns warning
            )
        )
        assert any("primary news source unavailable" in w.lower() for w in res.warnings)

    def test_15_mocked_rss_creates_t1_events(self):
        rss = (
            "<?xml version='1.0'?><rss><channel>"
            "<item><title>Apple announces new partnership</title>"
            "<link>https://apple.com/news/1</link><pubDate>2026-07-01</pubDate></item>"
            "</channel></rss>"
        )
        items = parse_feed(rss, "Apple newsroom")
        assert items[0].source_tier == "T1_primary_filing"
        assert discover_feed_urls("https://apple.com")  # discovery builds candidates

    def test_16_duplicate_news_deduped(self):
        a = NewsItem(headline="Same story", url="https://x.com/a?utm_source=x",
                     source_tier="T5_api_aggregator", provider_name="agg")
        b = NewsItem(headline="Same story", url="https://x.com/a",
                     source_tier="T4_quality_media", provider_name="media")
        out = dedupe_news_items([a, b])
        assert len(out) == 1
        assert out[0].source_tier == "T4_quality_media"  # stronger tier kept


class _FakeSec:
    def __init__(self, events):
        self._events = events

    async def get_recent_events(self, ticker, cik=None, company_name=None,
                                lookback_days=90, max_events=20, exchange=None):
        return RecentFilingsResult(ticker=ticker, cik="320193", events=self._events)


class _FakePress:
    def __init__(self, items):
        self._items = items

    async def get_press_releases(self, ticker, company_name=None, website=None,
                                 lookback_days=90, max_items=20):
        return PressReleaseResult(ticker=ticker, items=self._items, feed_url="x")


# ===========================================================================
# 17–24  Classifier
# ===========================================================================


class TestClassifier:
    def test_17_product_launch(self):
        c = classify_catalyst(headline="Company launches new product line",
                              summary="", source_tier="T1_primary_filing")
        assert c.catalyst_category == "product"

    def test_18_partnership_contract(self):
        c = classify_catalyst(headline="Company announces strategic partnership",
                              summary="", source_tier="T5_api_aggregator")
        assert c.catalyst_category in ("partnership", "contract")

    def test_19_lawsuit_negative(self):
        c = classify_catalyst(headline="Company hit with class action lawsuit",
                              summary="", source_tier="T5_api_aggregator")
        assert c.catalyst_category == "litigation"
        assert c.catalyst_direction == "negative"

    def test_20_earnings(self):
        c = classify_catalyst(headline="Company reports quarterly results",
                              summary="revenue and net income", source_tier="T4_quality_media")
        assert c.catalyst_category == "earnings"

    def test_21_aggregator_lower_evidence(self):
        c = classify_catalyst(headline="rumor of deal", summary="",
                              source_tier="T5_api_aggregator")
        assert c.evidence_strength == "aggregator_only"

    def test_22_primary_regulator_stronger(self):
        reg = classify_catalyst(headline="merger agreement", summary="details" * 5,
                                source_tier="T2_regulator_or_gov", form_type="8-K",
                                item_numbers=["2.01"])
        agg = classify_catalyst(headline="merger rumor", summary="",
                                source_tier="T5_api_aggregator")
        rank = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
        assert rank[reg.catalyst_strength] > rank[agg.catalyst_strength]
        assert reg.evidence_strength == "regulator_confirmed"

    def test_23_confidence_bounded(self):
        for tier in ("T1_primary_filing", "T2_regulator_or_gov", "T5_api_aggregator"):
            c = classify_catalyst(headline="x", summary="", source_tier=tier)
            assert 0.0 <= c.confidence <= 1.0

    def test_24_ambiguous_neutral_unknown(self):
        c = classify_catalyst(headline="Company issues statement", summary="",
                              source_tier="T5_api_aggregator")
        assert c.catalyst_direction in ("neutral", "unknown")

    def test_multi_source_confirmed(self):
        c = classify_catalyst(headline="deal", summary="", source_tier="T4_quality_media",
                              multi_source=True)
        assert c.evidence_strength == "multi_source_confirmed"


# ===========================================================================
# 25–36  Workflow / report integration
# ===========================================================================


def _mock_service_factory(provider_name=None):
    # Always use the offline mock provider under the hood, regardless of the
    # requested (free_real) provider name, so the workflow runs without network.
    return _RealFinancialDataService(provider_name="mock")


def _build_db_mocks():
    run = MagicMock()
    run.id = "11111111-1111-1111-1111-111111111111"
    step = MagicMock()
    step.id = "22222222-2222-2222-2222-222222222222"
    company = MagicMock()
    company.id = "33333333-3333-3333-3333-333333333333"
    company.name = "Apple Inc."
    company.ticker = "AAPL"
    company.sector = "Technology"
    company.industry = "Consumer Electronics"
    company.description = "Consumer electronics"
    company.sec_cik = "320193"
    report = MagicMock()
    report.id = "44444444-4444-4444-4444-444444444444"
    report.slug = "company-analysis-aapl-abc"
    source = MagicMock()
    source.id = "55555555-5555-5555-5555-555555555555"
    citation = MagicMock()
    citation.id = "66666666-6666-6666-6666-666666666666"
    return {"run": run, "step": step, "company": company, "report": report,
            "source": source, "citation": citation}


def _run_free_real_workflow(discover_return=None, discover_side_effect=None):
    """Run the full workflow with provider=free_real, capturing the draft markdown."""
    from app.workflows.company_analysis import run_company_analysis

    mocks = _build_db_mocks()
    discover_mock = AsyncMock()
    if discover_side_effect is not None:
        discover_mock.side_effect = discover_side_effect
    else:
        discover_mock.return_value = (
            discover_return if discover_return is not None else _prepared_result()
        )

    captured: dict = {}

    async def _capture_create(db, report_create):
        captured["markdown"] = report_create.content_markdown
        captured["summary"] = report_create.summary
        return mocks["report"]

    with (
        patch("app.workflows.company_analysis.FinancialDataService", _mock_service_factory),
        patch("app.workflows.company_analysis._lookup_gleif_profile",
              new=AsyncMock(return_value=None)),
        patch("app.workflows.company_analysis.discover_catalysts", new=discover_mock),
        patch("app.services.agent_run_service.create_agent_run",
              new=AsyncMock(return_value=mocks["run"])),
        patch("app.services.agent_run_service.create_agent_step",
              new=AsyncMock(return_value=mocks["step"])),
        patch("app.services.agent_run_service.complete_agent_step", new=AsyncMock()),
        patch("app.services.agent_run_service.fail_agent_step", new=AsyncMock()),
        patch("app.services.agent_run_service.complete_agent_run", new=AsyncMock()),
        patch("app.services.agent_run_service.fail_agent_run", new=AsyncMock()),
        patch("app.services.company_service.get_company_by_ticker",
              new=AsyncMock(return_value=mocks["company"])),
        patch("app.services.source_service.get_or_create_source",
              new=AsyncMock(return_value=(mocks["source"], True))),
        patch("app.services.citation_service.create_citation",
              new=AsyncMock(return_value=mocks["citation"])),
        patch("app.services.citation_service.list_citations_for_agent_run",
              new=AsyncMock(return_value=[])),
        patch("app.services.report_service.create_draft_report", new=_capture_create),
    ):
        state = asyncio.run(
            run_company_analysis(
                db=MagicMock(),
                ticker="AAPL",
                exchange="US",
                provider_name="free_real",
            )
        )
    return state, captured.get("markdown", "")


class TestWorkflowReport:
    def test_25_free_real_attaches_catalyst_discovery(self):
        state, _ = _run_free_real_workflow()
        assert state.get("catalyst_discovery") is not None
        assert state.get("catalyst_coverage_status")

    def test_26_report_has_news_catalyst_section(self):
        _, md = _run_free_real_workflow()
        assert "## News & Catalyst Discovery" in md

    def test_27_report_has_recent_catalyst_events(self):
        _, md = _run_free_real_workflow()
        assert "## Recent Catalyst Events" in md

    def test_28_report_has_sec_filing_events(self):
        _, md = _run_free_real_workflow()
        assert "## SEC Filing Events" in md
        assert "8-K" in md

    def test_29_report_has_evidence_quality(self):
        _, md = _run_free_real_workflow()
        assert "## Catalyst Evidence Quality" in md

    def test_30_report_has_gaps_next_tasks(self):
        _, md = _run_free_real_workflow()
        assert "## Catalyst Gaps / Next Research Tasks" in md

    def test_31_source_quality_mentions_catalyst_upgrades(self):
        _, md = _run_free_real_workflow()
        assert "Catalyst source upgrades" in md

    def test_32_risk_includes_catalyst_data_quality(self):
        # limited coverage → catalyst data-quality risk should surface
        res = _prepared_result(events=[], coverage=CatalystCoverageStatus.none_found.value)
        _, md = _run_free_real_workflow(discover_return=res)
        assert "Catalyst Data-Quality Risks" in md

    def test_33_committee_has_catalyst_open_questions(self):
        _, md = _run_free_real_workflow()
        assert "Catalyst Open Questions" in md

    def test_34_provider_failure_no_crash(self):
        state, md = _run_free_real_workflow(discover_side_effect=RuntimeError("boom"))
        assert state.get("status") == "completed"
        assert state.get("draft_report_id")

    def test_35_no_catalysts_explicit_coverage(self):
        res = _prepared_result(events=[], coverage=CatalystCoverageStatus.none_found.value)
        _, md = _run_free_real_workflow(discover_return=res)
        assert "No recent catalysts found" in md

    def test_36_existing_phase19_fields_present(self):
        _, md = _run_free_real_workflow()
        assert "## Company Snapshot" in md
        assert "Human Review Required" in md

    def test_embedded_json_roundtrip_to_final_report(self):
        from app.services.final_report_generator import (
            _extract_workflow_state_from_report,
        )

        _, md = _run_free_real_workflow()
        report = MagicMock()
        report.content_markdown = md
        state = _extract_workflow_state_from_report(report)
        assert state.get("catalyst_discovery") is not None
        assert state["catalyst_discovery"]["events"]


# ===========================================================================
# 37–44  Safety
# ===========================================================================


class TestSafety:
    def test_37_38_39_no_forbidden_in_catalyst_output(self):
        # Scope the scan to catalyst-subsystem output. (The pre-existing council
        # disclaimer legitimately enumerates "BUY, SELL, HOLD, WATCH" as outputs
        # it does NOT produce; that is unrelated to Phase 24.)
        state, _ = _run_free_real_workflow()
        catalyst_md = (state.get("catalyst_agent") or {}).get("markdown", "")
        _assert_no_forbidden(catalyst_md)
        _assert_no_forbidden(json.dumps(state.get("catalyst_discovery") or {}))

    def test_40_human_review_required(self):
        state, _ = _run_free_real_workflow()
        assert state.get("human_review_required") is True
        res = _prepared_result()
        assert res.human_review_required is True

    def test_41_safety_valid_in_final_report(self):
        from app.services.final_report_generator import (
            _build_news_catalyst_discovery,
            run_safety_gate,
        )

        section = _build_news_catalyst_discovery(_prepared_result().to_report_dict())
        result = run_safety_gate({"news_catalyst_discovery": section})
        assert result.passed is True

    def test_42_positive_catalyst_no_recommendation(self):
        ev = CatalystEvent(
            id="x", ticker="AAPL", headline="Company launches record product",
            source_tier="T1_primary_filing",
        )
        ev = apply_classification(ev)
        assert ev.catalyst_direction == "positive"
        _assert_no_forbidden(json.dumps(ev.to_report_dict()))

    def test_43_negative_catalyst_no_recommendation(self):
        ev = CatalystEvent(
            id="x", ticker="AAPL", headline="Company faces delisting and bankruptcy",
            source_tier="T2_regulator_or_gov",
        )
        ev = apply_classification(ev)
        assert ev.catalyst_direction == "negative"
        _assert_no_forbidden(json.dumps(ev.to_report_dict()))

    def test_44_schema_may_be_false_final_report_ok(self):
        from app.services.final_report_generator import (
            _assemble_final_report_content,
            run_safety_gate,
        )

        content = _assemble_final_report_content(
            company_snapshot=None, company_record={"name": "Apple", "ticker": "AAPL"},
            candidate=None, scorecard=None, financial_data_summary=None,
            source_quality_summary=None, research_completeness_summary=None,
            upgraded_citation_validation=None, bull_case_summary=None,
            bear_case_summary=None, risk_summary=None, valuation_guard_summary=None,
            committee_chair_summary=None, fundamentals_data=None,
            fundamentals_available=None, source_tier=None, sources=[], citations=[],
            report=None, agent_run_id=None, schema_valid=False,
            human_review_required=True,
            catalyst_discovery=_prepared_result().to_report_dict(),
        )
        assert "news_catalyst_discovery" in content
        assert run_safety_gate(content).passed is True

    def test_hostile_headline_neutralised(self):
        raw = "Analyst issues BUY, sees upside; buyback and price target hiked"
        assert neutralize_forbidden_terms(raw)
        _assert_no_forbidden(neutralize_forbidden_terms(raw))


# ===========================================================================
# 45–47  Backward compatibility
# ===========================================================================


class TestBackwardCompatibility:
    def test_45_mock_provider_no_catalyst(self):
        # The node skips catalyst discovery for the mock provider (deterministic).
        from app.workflows.company_analysis import run_company_analysis

        mocks = _build_db_mocks()
        captured: dict = {}

        async def _capture_create(db, report_create):
            captured["markdown"] = report_create.content_markdown
            return mocks["report"]

        with (
            patch("app.workflows.company_analysis.discover_catalysts",
                  new=AsyncMock(side_effect=AssertionError("must not run for mock"))),
            patch("app.services.agent_run_service.create_agent_run",
                  new=AsyncMock(return_value=mocks["run"])),
            patch("app.services.agent_run_service.create_agent_step",
                  new=AsyncMock(return_value=mocks["step"])),
            patch("app.services.agent_run_service.complete_agent_step", new=AsyncMock()),
            patch("app.services.agent_run_service.fail_agent_step", new=AsyncMock()),
            patch("app.services.agent_run_service.complete_agent_run", new=AsyncMock()),
            patch("app.services.agent_run_service.fail_agent_run", new=AsyncMock()),
            patch("app.services.company_service.get_company_by_ticker",
                  new=AsyncMock(return_value=mocks["company"])),
            patch("app.services.source_service.get_or_create_source",
                  new=AsyncMock(return_value=(mocks["source"], True))),
            patch("app.services.citation_service.create_citation",
                  new=AsyncMock(return_value=mocks["citation"])),
            patch("app.services.citation_service.list_citations_for_agent_run",
                  new=AsyncMock(return_value=[])),
            patch("app.services.report_service.create_draft_report", new=_capture_create),
        ):
            state = asyncio.run(
                run_company_analysis(db=MagicMock(), ticker="AAPL", exchange="US",
                                     provider_name="mock")
            )
        assert state.get("status") == "completed"
        assert state.get("catalyst_discovery") is None
        assert "## News & Catalyst Discovery" not in captured.get("markdown", "")

    def test_46_ids_deterministic(self):
        a = make_catalyst_event_id("AAPL", "8-K", "2026-07-01", "acc-1")
        b = make_catalyst_event_id("AAPL", "8-K", "2026-07-01", "acc-1")
        assert a == b
        assert a != make_catalyst_event_id("AAPL", "8-K", "2026-07-01", "acc-2")

    def test_47_no_live_calls_null_provider_default(self):
        # Default news provider is the offline null provider (no env config).
        assert isinstance(get_news_provider(), NullNewsProvider)


@pytest.mark.parametrize(
    "direction,strength",
    [(d.value, s.value) for d in CatalystDirection for s in CatalystStrength],
)
def test_agent_markdown_safe_across_labels(direction, strength):
    ev = _sec_events()[0].model_copy(
        update={"catalyst_direction": direction, "catalyst_strength": strength}
    )
    res = _prepared_result(events=[ev])
    out = run_catalyst_agent(res)
    _assert_no_forbidden(out.markdown)
    assert out.coverage_status
    assert EvidenceStrength  # symbol referenced
