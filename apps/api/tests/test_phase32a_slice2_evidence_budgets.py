"""
Phase 32A Slice 2 — category-aware evidence budgets + structured financial-fact
wiring into the LLM council evidence pack.

Covers:
  * AAPL golden pack: SEC/XBRL statement facts tier-split into correctly-tiered
    financial_fact items; derived metrics T6; price T5; market T6; news cannot
    crowd financials out (floor honored with 20+ news).
  * Provenance: SEC facts retain T1/T2, price T5, derived T6 (C_inferred),
    metadata references never become facts, absent metrics never fabricated.
  * Budget behavior: floors + caps enforced, low-tier-news strict cap, near-dup
    news removed, deterministic ordering, total-item / payload limits.
  * CFR fallback: metadata-only references stay honest, never financial_fact.
  * Backward-compat: flag OFF ⇒ dark path unchanged (single legacy SEC item, no
    relevance carried, no new item types); legacy state no-ops.

The flag defaults OFF; every ON case constructs a ``Settings`` with the flag set.
"""

from __future__ import annotations

from collections import Counter

from app.core.config import Settings
from app.services.llm.evidence_budget import (
    CATEGORY_FINANCIAL_FACT,
    CATEGORY_LOW_TIER_NEWS,
    CATEGORY_MATERIAL_NEWS,
    CATEGORY_PRICE_TREND_METRIC,
    CATEGORY_SOURCE_REFERENCE,
    apply_evidence_budget,
    evidence_category,
)
from app.services.llm.evidence_pack import build_evidence_pack
from app.services.llm.schemas import (
    TIER_T1_PRIMARY_FILING,
    TIER_T2_REGULATOR_OR_GOV,
    TIER_T5_API_AGGREGATOR,
    TIER_T6_MODEL_ESTIMATE,
    EvidenceItem,
    EvidencePack,
)

# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #

# Realistic AAPL annual (FY2024 10-K) SEC/XBRL fundamentals_summary as produced
# by snapshot_builder's SEC path. All statement values are ANNUAL (never TTM).
AAPL_FUNDAMENTALS = {
    "source_tier": "T2_regulator_or_gov",
    "provider": "sec_edgar",
    "num_datapoints": 24,
    # Income statement (T1 content / T2 transport)
    "revenue_usd_m": 391035,
    "gross_profit_usd_m": 180683,
    "operating_income_usd_m": 123216,
    "net_income_usd_m": 93736,
    "eps_basic": 6.11,
    "eps_diluted": 6.08,
    # Cash flow
    "operating_cash_flow_usd_m": 118254,
    "capital_expenditures_usd_m": 9447,
    "free_cash_flow_usd_m": 108807,  # derived
    # Balance sheet
    "total_assets_usd_m": 364980,
    "total_liabilities_usd_m": 308030,
    "shareholders_equity_usd_m": 56950,
    "cash_and_equivalents_usd_m": 29943,
    "short_term_debt_usd_m": 22511,
    "long_term_debt_usd_m": 85750,
    "total_debt_usd_m": 108261,  # derived aggregate
    "shares_outstanding_mln": 15116,
    # Derived (percentages / ratios) — model-computed
    "gross_margin_pct": 46.2,
    "operating_margin_pct": 31.5,
    "net_margin_pct": 24.0,
    "return_on_equity_pct": 164.6,
    "free_cash_flow_margin_pct": 27.8,
    "debt_to_equity": 1.9,
    "revenue_yoy_growth_pct": 2.0,
    "net_income_yoy_growth_pct": -3.4,
    "free_cash_flow_yoy_growth_pct": 9.3,
    # Not sourced from SEC statements — kept explicit None (never fabricated)
    "ebitda_usd_m": None,
    "market_cap_usd_m": None,
    "enterprise_value_usd_m": None,
    # Filing metadata
    "fiscal_year": 2024,
    "fiscal_period": "FY",
    "form_type": "10-K",
    "filed_date": "2024-11-01",
    "accession_number": "0000320193-24-000123",
    "period_basis": "annual",
    "data_quality": "B_single_credible",
    "note": "SEC EDGAR XBRL fundamentals normalized for latest fiscal periods.",
}

AAPL_MARKET_METRICS = {
    "latest_close": 232.8,
    "week52_high": 260.1,
    "week52_low": 164.08,
    "shares_outstanding_mln": 15116,
    "market_cap_mln": 3519000.0,
    "enterprise_value_mln": 3598000.0,
    "pe_ratio": 37.5,
    "currency": "USD",
    "source_tiers": {
        "latest_close": "T5_api_aggregator",
        "week52_high": "T5_api_aggregator",
        "week52_low": "T5_api_aggregator",
        "shares_outstanding_mln": "T2_regulator_or_gov",
        "market_cap_mln": "T6_model_estimate",
        "enterprise_value_mln": "T6_model_estimate",
        "pe_ratio": "T6_model_estimate",
    },
}


def _aapl_snapshot(**overrides):
    snap = {
        "company_identity": {
            "ticker": "AAPL",
            "legal_name": "Apple Inc.",
            "exchange": "NASDAQ",
            "country_domicile": "US",
        },
        "fundamentals_summary": dict(AAPL_FUNDAMENTALS),
        "market_metrics_summary": dict(AAPL_MARKET_METRICS),
        "is_mock": False,
        "trend_signal_summary": {
            "momentum_label": "steady_uptrend",
            "return_3m": 7.4,
            "pct_above_ma200": 12.1,
            "source_tier": "T6_model_estimate",
        },
    }
    snap.update(overrides)
    return snap


def _news_events(n, *, tier=TIER_T5_API_AGGREGATOR, level="low", prefix="AAPL story"):
    return [
        {
            "headline": f"{prefix} number {i}",
            "summary": f"body text {i}",
            "source_tier": tier,
            "source_url": f"https://news.example.com/{prefix}/{i}",
            "event_date": "2024-10-01",
            "relevance_level": level,
            "source_type": "catalyst_event",
        }
        for i in range(n)
    ]


def _catalyst(events, ticker="AAPL"):
    return {
        "ticker": ticker,
        "filing_events": [],
        "events": events,
        "industry_events": [],
    }


def _cfg_on(**overrides):
    base = dict(
        llm_council_evidence_budgets_enabled=True,
        source_connector_enabled=True,
        llm_council_evidence_max_items=20,
        llm_council_evidence_financial_floor=3,
        llm_council_evidence_price_trend_cap=3,
        llm_council_evidence_news_cap=8,
        llm_council_evidence_low_tier_news_cap=4,
    )
    base.update(overrides)
    return Settings(**base)


def _build(snapshot, catalyst, *, cfg, apply_budget=True, max_items=40, report=None):
    return build_evidence_pack(
        report_content=report or {"company_identity": {"ticker": "AAPL"}},
        company_snapshot=snapshot,
        catalyst_discovery=catalyst,
        max_items=max_items,
        apply_budget=apply_budget,
        budget_cfg=cfg,
    )


def _by_category(pack):
    return Counter(evidence_category(i) for i in pack.evidence_items)


def _items_of(pack, category):
    return [i for i in pack.evidence_items if evidence_category(i) == category]


# --------------------------------------------------------------------------- #
# 1. AAPL golden pack                                                         #
# --------------------------------------------------------------------------- #


def test_golden_pack_has_tier_split_financial_facts():
    cfg = _cfg_on()
    pack = _build(_aapl_snapshot(), _catalyst([]), cfg=cfg, apply_budget=False)
    facts = _items_of(pack, CATEGORY_FINANCIAL_FACT)
    titles = " | ".join(i.title or "" for i in facts)
    # Income, cash flow and balance sheet each become their own T1/T2 item.
    assert len(facts) == 3, titles
    assert "income statement" in titles
    assert "cash flow statement" in titles
    assert "balance sheet" in titles
    for it in facts:
        assert it.content_tier == TIER_T1_PRIMARY_FILING
        assert it.transport_tier == TIER_T2_REGULATOR_OR_GOV
        assert it.provider_transport == "SEC EDGAR / data.sec.gov"


def test_golden_pack_labels_annual_never_ttm():
    cfg = _cfg_on()
    pack = _build(_aapl_snapshot(), _catalyst([]), cfg=cfg, apply_budget=False)
    for it in _items_of(pack, CATEGORY_FINANCIAL_FACT):
        assert "FY2024 ANNUAL 10-K" in (it.title or "")
        for f in it.fields_supported:
            assert not f.endswith("_ttm")


def test_golden_pack_quarterly_fallback_labelled():
    cfg = _cfg_on()
    fs = dict(AAPL_FUNDAMENTALS, period_basis="quarterly", form_type="10-Q")
    snap = _aapl_snapshot(fundamentals_summary=fs)
    pack = _build(snap, _catalyst([]), cfg=cfg, apply_budget=False)
    facts = _items_of(pack, CATEGORY_FINANCIAL_FACT)
    assert facts
    for it in facts:
        assert "QUARTERLY 10-Q" in (it.title or "")


def test_news_does_not_crowd_out_financials():
    cfg = _cfg_on()
    # 24 aggregator news events + the AAPL financial facts.
    pack = _build(_aapl_snapshot(), _catalyst(_news_events(24)), cfg=cfg)
    facts = _items_of(pack, CATEGORY_FINANCIAL_FACT)
    assert len(facts) == 3  # floor honored despite the news flood
    low_news = _items_of(pack, CATEGORY_LOW_TIER_NEWS)
    assert len(low_news) <= cfg.llm_council_evidence_low_tier_news_cap


# --------------------------------------------------------------------------- #
# 2. Provenance                                                               #
# --------------------------------------------------------------------------- #


def test_derived_metrics_are_t6_never_t1_t2():
    cfg = _cfg_on()
    pack = _build(_aapl_snapshot(), _catalyst([]), cfg=cfg, apply_budget=False)
    derived = [
        i
        for i in pack.evidence_items
        if i.source_type == "derived_financial_metric"
    ]
    assert derived, "derived-metrics item expected"
    for it in derived:
        assert it.content_tier == TIER_T6_MODEL_ESTIMATE
        assert it.data_quality == "C_inferred"
        assert "DERIVED" in (it.title or "")
        assert evidence_category(it) == CATEGORY_PRICE_TREND_METRIC


def test_price_is_t5_and_market_is_t6():
    cfg = _cfg_on()
    pack = _build(_aapl_snapshot(), _catalyst([]), cfg=cfg, apply_budget=False)
    price = [i for i in pack.evidence_items if i.source_type == "price_metric"]
    market = [i for i in pack.evidence_items if i.source_type == "market_metric"]
    assert len(price) == 1 and len(market) == 1
    assert price[0].content_tier == TIER_T5_API_AGGREGATOR
    assert market[0].content_tier == TIER_T6_MODEL_ESTIMATE
    # market cap / EV / P/E only in the T6 item, never in the T5 price item.
    assert set(market[0].fields_supported) <= {"market_cap_mln", "enterprise_value_mln", "pe_ratio"}
    assert set(price[0].fields_supported) <= {"latest_close", "week52_high", "week52_low"}


def test_absent_metrics_never_fabricated():
    cfg = _cfg_on()
    # Only revenue present; every other statement field absent.
    fs = {
        "revenue_usd_m": 391035,
        "form_type": "10-K",
        "fiscal_year": 2024,
        "period_basis": "annual",
        "filed_date": "2024-11-01",
        "data_quality": "B_single_credible",
    }
    snap = _aapl_snapshot(fundamentals_summary=fs, market_metrics_summary={})
    pack = _build(snap, _catalyst([]), cfg=cfg, apply_budget=False)
    facts = _items_of(pack, CATEGORY_FINANCIAL_FACT)
    # Only the income-statement item (revenue) — no cash-flow / balance items.
    assert len(facts) == 1
    assert facts[0].fields_supported == ["revenue_usd_m"]
    # No price / market items when no price data exists.
    assert not any(i.source_type in {"price_metric", "market_metric"} for i in pack.evidence_items)


def test_trend_signal_surfaced_as_t6():
    cfg = _cfg_on()
    pack = _build(_aapl_snapshot(), _catalyst([]), cfg=cfg, apply_budget=False)
    trend = [i for i in pack.evidence_items if i.source_type == "trend_signal"]
    assert len(trend) == 1
    assert trend[0].content_tier == TIER_T6_MODEL_ESTIMATE
    assert evidence_category(trend[0]) == CATEGORY_PRICE_TREND_METRIC


# --------------------------------------------------------------------------- #
# 3. Budget behavior                                                          #
# --------------------------------------------------------------------------- #


def _item(id, tier, *, source_type="catalyst_event", title="t", excerpt="x",
          url=None, data_quality=None, fields=None, relevance_level=None):
    return EvidenceItem(
        id=id,
        source_tier=tier,
        source_type=source_type,
        content_tier=tier,
        transport_tier=tier,
        title=title,
        url=url,
        excerpt=excerpt,
        data_quality=data_quality,
        fields_supported=fields or ["catalyst"],
        relevance_level=relevance_level,
    )


def test_financial_floor_reserved_over_higher_tier_news():
    # 3 financial facts (added first) + a flood of T2 regulator events.
    cfg = _cfg_on(llm_council_evidence_max_items=6)
    facts = [
        _item(f"F{i}", TIER_T1_PRIMARY_FILING, source_type="sec_financial_statement",
              title=f"stmt {i}", fields=["revenue_usd_m"])
        for i in range(3)
    ]
    reg_events = [
        _item(f"R{i}", TIER_T2_REGULATOR_OR_GOV, title=f"8-K filing {i}")
        for i in range(20)
    ]
    pack = EvidencePack(evidence_items=facts + reg_events)
    out = apply_evidence_budget(pack, cfg=cfg)
    kept_facts = _items_of(out, CATEGORY_FINANCIAL_FACT)
    assert len(kept_facts) == 3
    assert len(out.evidence_items) == 6


def _fact_item(id, field, period, *, scope=None, value=1.0, tier=TIER_T1_PRIMARY_FILING):
    """A structured company-IR financial-fact item carrying a real
    ``primary_fact`` payload (dict form — the shape it has after
    ``add_framework_item``'s ``model_dump``) so the diversity/period-recency
    key can actually read ``field``/``period`` off it."""
    return EvidenceItem(
        id=id,
        source_tier=tier,
        source_type="company_ir_financial_fact",
        content_tier=tier,
        transport_tier=tier,
        title=f"{field} {period}",
        excerpt=f"{field} = {value} [{period}]",
        data_quality="B",
        fields_supported=[field],
        scope=scope,
        period=period,
        primary_fact={
            "field": field,
            "value": str(value),
            "numeric_value": value,
            "unit": "currency_amount",
            "currency": "EUR",
            "scale": "million",
            "period": period,
        },
    )


def test_current_period_financial_fact_survives_over_stale_comparison_period():
    """Phase 32A corrective (LVMH H1 2026) — the diversity key for a
    structured financial fact is (field, scope), NOT (field, scope, period),
    so a comparison-period and a current-period fact for the SAME field
    compete for one round-robin slot. A live MC/LVMH run showed the stale
    2025 ``total_equity`` figure reach Council evidence while the CURRENT
    2026 figure was silently dropped by a tight floor. With only one slot
    available for this field, the more recent period must win."""
    cfg = _cfg_on(llm_council_evidence_financial_floor=1)
    facts = [
        _fact_item("F1", "total_equity", "2025", value=66875.0),
        _fact_item("F2", "total_equity", "2026", value=69694.0),
    ]
    pack = EvidencePack(evidence_items=facts)
    out = apply_evidence_budget(pack, cfg=cfg, max_items=1)
    kept = _items_of(out, CATEGORY_FINANCIAL_FACT)
    assert len(kept) == 1
    assert kept[0].primary_fact["period"] == "2026"
    assert kept[0].primary_fact["numeric_value"] == 69694.0


def test_price_trend_cap_enforced():
    cfg = _cfg_on(llm_council_evidence_max_items=20, llm_council_evidence_price_trend_cap=2)
    metrics = [
        _item(f"M{i}", TIER_T6_MODEL_ESTIMATE, source_type="market_metric",
              title=f"metric {i}", fields=["pe_ratio"])
        for i in range(6)
    ]
    out = apply_evidence_budget(EvidencePack(evidence_items=metrics), cfg=cfg)
    assert len(_items_of(out, CATEGORY_PRICE_TREND_METRIC)) == 2


def test_low_tier_news_strict_cap():
    cfg = _cfg_on(llm_council_evidence_max_items=20,
                  llm_council_evidence_news_cap=8,
                  llm_council_evidence_low_tier_news_cap=3)
    news = [
        _item(f"N{i}", TIER_T5_API_AGGREGATOR, title=f"news {i}", relevance_level="low")
        for i in range(15)
    ]
    out = apply_evidence_budget(EvidencePack(evidence_items=news), cfg=cfg)
    assert len(_items_of(out, CATEGORY_LOW_TIER_NEWS)) == 3


def test_news_cap_aggregate_across_material_and_low_tier():
    cfg = _cfg_on(llm_council_evidence_max_items=30,
                  llm_council_evidence_news_cap=5,
                  llm_council_evidence_low_tier_news_cap=4)
    material = [
        _item(f"Q{i}", "T4_quality_media", title=f"quality {i}", relevance_level="high")
        for i in range(6)
    ]
    low = [
        _item(f"N{i}", TIER_T5_API_AGGREGATOR, title=f"agg {i}", relevance_level="low")
        for i in range(6)
    ]
    out = apply_evidence_budget(EvidencePack(evidence_items=material + low), cfg=cfg)
    news = _items_of(out, CATEGORY_MATERIAL_NEWS) + _items_of(out, CATEGORY_LOW_TIER_NEWS)
    assert len(news) == 5  # aggregate news_cap
    assert len(_items_of(out, CATEGORY_LOW_TIER_NEWS)) <= 4


def test_materiality_ranks_high_over_low_within_tier():
    cfg = _cfg_on(llm_council_evidence_max_items=2,
                  llm_council_evidence_low_tier_news_cap=2,
                  llm_council_evidence_news_cap=2)
    items = [
        _item("A", TIER_T5_API_AGGREGATOR, title="low story", relevance_level="low"),
        _item("B", TIER_T5_API_AGGREGATOR, title="high story", relevance_level="high"),
        _item("C", TIER_T5_API_AGGREGATOR, title="irrelevant story", relevance_level="irrelevant"),
    ]
    out = apply_evidence_budget(EvidencePack(evidence_items=items), cfg=cfg)
    kept = {i.title for i in out.evidence_items}
    assert "high story" in kept
    assert "irrelevant story" not in kept


def test_near_duplicate_news_removed():
    cfg = _cfg_on()
    events = [
        {"headline": "Apple reports record revenue!", "summary": "a",
         "source_tier": TIER_T5_API_AGGREGATOR, "source_url": "https://x.com/1",
         "relevance_level": "medium", "source_type": "catalyst_event"},
        {"headline": "apple reports record revenue", "summary": "b different body",
         "source_tier": TIER_T5_API_AGGREGATOR, "source_url": "https://y.com/2",
         "relevance_level": "medium", "source_type": "catalyst_event"},
    ]
    pack = _build({"company_identity": {"ticker": "AAPL"}}, _catalyst(events), cfg=cfg)
    lows = _items_of(pack, CATEGORY_LOW_TIER_NEWS)
    assert len(lows) == 1  # near-duplicate (title normalized) removed


def test_derivative_instrument_demoted_and_excluded():
    cfg = _cfg_on()
    events = _news_events(3, tier="T4_quality_media", level="high", prefix="material")
    events.append({
        "headline": "Direxion Daily AAPL Bull 2X Shares (AAPU) leveraged ETF surges",
        "summary": "leveraged single-stock inverse product",
        "source_tier": TIER_T5_API_AGGREGATOR,
        "source_url": "https://x.com/aapu",
        "relevance_level": "high",
        "source_type": "catalyst_event",
    })
    # Inspect raw (unbudgeted) build: the derivative item is demoted.
    raw = _build({"company_identity": {"ticker": "AAPL"}}, _catalyst(events), cfg=cfg,
                 apply_budget=False)
    deriv = [i for i in raw.evidence_items if "AAPU" in (i.title or "")]
    assert len(deriv) == 1
    assert deriv[0].relevance_level == "irrelevant"
    assert deriv[0].content_tier == TIER_T6_MODEL_ESTIMATE


def test_deterministic_ordering_same_input_same_ids():
    cfg = _cfg_on()
    snap = _aapl_snapshot()
    cat = _catalyst(_news_events(10))
    p1 = _build(snap, cat, cfg=cfg)
    p2 = _build(snap, cat, cfg=cfg)
    assert p1.model_dump_json() == p2.model_dump_json()
    assert [i.id for i in p1.evidence_items] == [
        f"E{i + 1}" for i in range(p1.item_count)
    ]


def test_total_item_and_char_limits():
    cfg = _cfg_on(llm_council_evidence_max_items=5,
                  llm_council_evidence_max_chars=1000,
                  llm_council_evidence_max_chars_per_item=100)
    facts = [
        _item(f"F{i}", TIER_T1_PRIMARY_FILING, source_type="sec_financial_statement",
              title=f"stmt {i}", excerpt="z" * 500, fields=["revenue_usd_m"])
        for i in range(30)
    ]
    out = apply_evidence_budget(EvidencePack(evidence_items=facts), cfg=cfg)
    assert len(out.evidence_items) <= 5
    for it in out.evidence_items:
        assert len(it.excerpt or "") <= 100


def test_no_financial_facts_still_builds_safe_pack():
    cfg = _cfg_on()
    snap = _aapl_snapshot(fundamentals_summary={}, market_metrics_summary={},
                          trend_signal_summary={})
    pack = _build(snap, _catalyst(_news_events(5)), cfg=cfg)
    assert not _items_of(pack, CATEGORY_FINANCIAL_FACT)
    assert pack.company.ticker == "AAPL"  # identity always present


def test_omitted_reason_mentions_floors_and_caps():
    cfg = _cfg_on()
    pack = _build(_aapl_snapshot(), _catalyst(_news_events(24)), cfg=cfg)
    assert pack.omitted_evidence_count > 0
    reason = pack.omitted_reason or ""
    assert "financial-fact" in reason
    assert "capped" in reason


# --------------------------------------------------------------------------- #
# 4. CFR fallback — metadata-only references never become facts               #
# --------------------------------------------------------------------------- #


def test_metadata_only_reference_never_financial_fact():
    # Even a financial-looking source_type is a reference when metadata-only.
    ref = _item("E1", TIER_T2_REGULATOR_OR_GOV, source_type="company_ir_financial_fact",
                data_quality="metadata_only", title="Richemont filing index",
                fields=["revenue"])
    assert evidence_category(ref) == CATEGORY_SOURCE_REFERENCE


def test_cfr_metadata_only_pack_has_no_financial_facts():
    cfg = _cfg_on()
    refs = [
        _item(f"E{i}", TIER_T2_REGULATOR_OR_GOV, source_type="six_swiss_reference",
              data_quality="metadata_only", title=f"CFR venue reference {i}",
              excerpt="metadata only", fields=[])
        for i in range(6)
    ]
    out = apply_evidence_budget(EvidencePack(evidence_items=refs), cfg=cfg)
    assert not _items_of(out, CATEGORY_FINANCIAL_FACT)
    for it in out.evidence_items:
        assert evidence_category(it) == CATEGORY_SOURCE_REFERENCE


# --------------------------------------------------------------------------- #
# 5. Backward-compat / dark path                                              #
# --------------------------------------------------------------------------- #


def _cfg_off():
    return Settings(llm_council_evidence_budgets_enabled=False, source_connector_enabled=True)


def test_flag_off_emits_single_legacy_sec_item():
    cfg = _cfg_off()
    pack = _build(_aapl_snapshot(), _catalyst([]), cfg=cfg, apply_budget=False)
    sec = [i for i in pack.evidence_items if i.source_type == "company_filing"]
    assert len(sec) == 1
    assert sec[0].content_tier == TIER_T1_PRIMARY_FILING
    # None of the Slice-2 item types appear on the dark path.
    types = {i.source_type for i in pack.evidence_items}
    assert types.isdisjoint({
        "sec_financial_statement", "derived_financial_metric",
        "market_metric", "price_metric", "trend_signal", "financial_data_summary",
    })


def test_flag_off_catalysts_carry_no_relevance():
    cfg = _cfg_off()
    pack = _build({"company_identity": {"ticker": "AAPL"}},
                  _catalyst(_news_events(5, level="high")), cfg=cfg, apply_budget=False)
    catalysts = [i for i in pack.evidence_items if i.source_type == "catalyst_event"]
    assert catalysts
    for it in catalysts:
        assert it.relevance_level is None


def test_flag_off_budgeter_uses_flat_path():
    # Flat path pins T1/T2 before T5/T6 with max_items=2 (Phase 29B.2 contract).
    items = [
        _item("E1", TIER_T6_MODEL_ESTIMATE, title="model", source_type="x", fields=[]),
        _item("E2", TIER_T5_API_AGGREGATOR, title="agg", source_type="x", fields=[]),
        _item("E3", TIER_T1_PRIMARY_FILING, title="filing", source_type="x", fields=[]),
        _item("E4", TIER_T2_REGULATOR_OR_GOV, title="reg", source_type="x", fields=[]),
    ]
    out = apply_evidence_budget(EvidencePack(evidence_items=items), max_items=2)
    assert {i.title for i in out.evidence_items} == {"filing", "reg"}


def test_legacy_state_without_new_fields_is_safe():
    cfg = _cfg_on()
    # Legacy snapshot: fundamentals_summary present but no market_metrics / trend.
    snap = {
        "company_identity": {"ticker": "AAPL", "legal_name": "Apple Inc."},
        "fundamentals_summary": {
            "revenue_usd_m": 391035,
            "net_income_usd_m": 93736,
            "form_type": "10-K",
            "fiscal_year": 2024,
            "period_basis": "annual",
            "data_quality": "B_single_credible",
        },
        "is_mock": False,
    }
    pack = _build(snap, None, cfg=cfg)
    assert _items_of(pack, CATEGORY_FINANCIAL_FACT)
    assert pack.company.ticker == "AAPL"


def test_slice1_identity_and_mock_flag_preserved():
    cfg = _cfg_on()
    # Absence of is_mock must NOT coerce the pack into mock treatment.
    snap = _aapl_snapshot()
    snap.pop("is_mock", None)
    pack = _build(snap, _catalyst(_news_events(3)), cfg=cfg)
    joined = " ".join(pack.do_not_infer)
    assert "mock/placeholder" not in joined  # unknown never flagged as mock
    assert pack.company.company_name == "Apple Inc."
