"""
Phase 19.4 — Identity + Sector + Market-Metric Enrichment.

Verifies that the free_real snapshot is enriched with:
  * identity / sector / profile fields (sector inferred from SEC SIC, website
    from SEC submissions, LEI from GLEIF — never fabricated), and
  * derived market metrics (latest close, 52-week range, shares outstanding,
    market cap, enterprise value, P/E) computed only from free price history
    (T5) and normalized SEC fundamentals (T2),

WITHOUT producing any recommendation, price objective, fair-value estimate,
upside percentage, EBITDA, EV/EBITDA or beta fabrication.

All tests are offline: fixture JSON + synthetic price/DEI data only. No live
SEC / GLEIF / EODHD network calls.

Coverage (per Phase 19.4 spec, tests 1–24):
   1. shares outstanding extracted from SEC DEI fixture
   2. latest close read from price history
   3. 52-week high/low derived from price history
   4. market cap derived only when latest close AND shares available
   5. enterprise value derived only when market cap, debt AND cash available
   6. P/E derived only when safe inputs available
   7. EBITDA is not fabricated
   8. EV/EBITDA remains missing without EBITDA
   9. beta remains missing unless sourced
  10. market metrics tagged as derived/internal estimate (T6)
  11. SEC / GLEIF / profile source tiers are correct
  12. missing information count decreases for AAPL fixture
  13. valuation readiness uses new market metrics but still blocks conclusions
  14. report text includes market metric availability
  15. report text does not produce BUY/SELL/HOLD/WATCH
  16. report text does not produce price target/fair value/upside
  17. human_review_required remains true
  18. final report generation still works
  19. safety gate stays clean (no forbidden phrases)
  20. sector/LEI/ISIN unavailable → warnings, no crash
  21. shares unavailable → market cap not fabricated
  22. price history unavailable → 52-week range + market cap missing with warnings
  23. provider=mock behaviour unchanged (no enrichment applied)
  24. provider=free_real Phase 19.3 snapshot path still intact
"""

from __future__ import annotations

import asyncio
import copy
import json
import pathlib
from datetime import datetime, timezone

import pytest

from app.agents.analysis_council.investment_committee_chair import (
    run_investment_committee_chair,
)
from app.agents.analysis_council.valuation_guard_agent import (
    run_valuation_guard_agent,
    valuation_guard_output_to_dict,
)
from app.agents.research_team.financial_data_agent import (
    financial_data_agent_output_to_dict,
    run_financial_data_agent,
)
from app.integrations.company_profile_enrichment import enrich_company_profile
from app.integrations.financial_data_provider import (
    CompanyProfileData,
    DataQuality,
    FundamentalsData,
    PriceHistoryData,
    PricePoint,
    ProviderResponseMetadata,
    ProviderStatus,
    SourceTier,
)
from app.integrations.free_real_snapshot import (
    CompanyIdentity,
    compose_free_real_snapshot,
)
from app.integrations.market_metrics_enrichment import derive_market_metrics
from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
from app.integrations.sec_fundamentals_normalizer import normalize_company_facts
from app.workflows.snapshot_builder import (
    build_company_snapshot,
    enrich_snapshot_with_free_real,
    enrich_snapshot_with_market_metrics,
    enrich_snapshot_with_profile_enrichment,
)

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures"

_RECOMMENDATION_TOKENS = (" buy ", " sell ", " hold ", " watch ", " reject ")
_VALUATION_TOKENS = (
    "price target",
    "target price",
    "fair value",
    "intrinsic value",
    "upside",
    "downside",
    "undervalued",
    "overvalued",
)


def load_fixture(name: str) -> dict:
    with open(FIXTURE_DIR / name) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Fixtures: AAPL facts with/without DEI shares, and price history helpers
# ---------------------------------------------------------------------------


def _aapl_facts_with_dei() -> dict:
    """AAPL companyfacts augmented with dei.EntityCommonStockSharesOutstanding.

    The trimmed committed fixture omits DEI facts; live SEC companyfacts always
    carry cover-page shares outstanding. We inject a realistic FY2023 10-K value
    (≈15.55bn shares) so the shares→market cap path is exercised offline.
    """
    facts = copy.deepcopy(load_fixture("sec_companyfacts_aapl.json"))
    facts.setdefault("facts", {}).setdefault("dei", {})[
        "EntityCommonStockSharesOutstanding"
    ] = {
        "units": {
            "shares": [
                {
                    "end": "2023-09-30",
                    "val": 15550061000,
                    "accn": "acc-2023",
                    "fy": 2023,
                    "fp": "FY",
                    "form": "10-K",
                    "filed": "2023-11-03",
                }
            ]
        }
    }
    return facts


def _build_fundamentals(facts: dict) -> FundamentalsData:
    dps, _ = parse_company_facts(facts, "AAPL", "320193")
    norm = normalize_company_facts(facts, "AAPL", "320193")
    existing = {dp.field_name for dp in dps}
    for dp in norm.to_datapoints():
        if dp.field_name not in existing:
            dps.append(dp)
            existing.add(dp.field_name)
    meta = ProviderResponseMetadata(
        provider_name="sec_edgar_fundamentals",
        source_tier=SourceTier.T2_regulator_or_gov,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
    )
    return FundamentalsData(ticker="AAPL", exchange="NASDAQ", datapoints=dps, meta=meta)


def _build_prices(points: list[tuple[str, float]] | None = None) -> PriceHistoryData:
    if points is None:
        points = [
            ("2022-11-01", 150.0),
            ("2023-01-10", 145.0),   # 52-week low
            ("2023-06-15", 198.0),   # 52-week high
            ("2023-09-30", 190.0),   # latest close
        ]
    pts = points
    meta = ProviderResponseMetadata(
        provider_name="stooq",
        source_tier=SourceTier.T5_api_aggregator,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
    )
    return PriceHistoryData(
        ticker="AAPL",
        exchange="NASDAQ",
        currency="USD",
        price_points=[PricePoint(date=d, close=c) for d, c in pts],
        data_quality=DataQuality.B_single_credible,
        meta=meta,
    )


def _sec_profile() -> CompanyProfileData:
    meta = ProviderResponseMetadata(
        provider_name="sec_edgar",
        source_tier=SourceTier.T2_regulator_or_gov,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
    )
    return CompanyProfileData(
        ticker="AAPL",
        exchange="NASDAQ",
        legal_name="Apple Inc.",
        country_domicile="US",
        reporting_currency="USD",
        industry="Electronic Computers",
        website="https://www.apple.com",
        meta=meta,
        data_quality=DataQuality.A_verified,
    )


def _gleif_profile(legal_name: str = "APPLE INC", lei: str = "HWUPKR0MPOU8FGXBT394") -> CompanyProfileData:
    meta = ProviderResponseMetadata(
        provider_name="gleif",
        source_tier=SourceTier.T2_regulator_or_gov,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
    )
    return CompanyProfileData(
        ticker=lei,
        legal_name=legal_name,
        lei=lei,
        meta=meta,
        data_quality=DataQuality.A_verified,
    )


def _free_real_snapshot(facts: dict, prices: PriceHistoryData | None) -> dict:
    """Snapshot after Phase 19.2/19.3 enrichment (before Phase 19.4)."""
    fund = _build_fundamentals(facts)
    ident = CompanyIdentity(
        ticker="AAPL",
        legal_name="Apple Inc.",
        exchange="NASDAQ",
        country_domicile="US",
        sec_cik="320193",
        reporting_currency="USD",
    )
    fr = asyncio.run(
        compose_free_real_snapshot(
            ident,
            price_data=prices if (prices and prices.price_points) else None,
            fundamentals_data=fund,
            provider_stack="free_real",
        )
    )
    prof = CompanyProfileData(
        ticker="AAPL",
        exchange="NASDAQ",
        legal_name="Apple Inc.",
        country_domicile="US",
        reporting_currency="USD",
        meta=fund.meta,
    )
    # Mirror the real workflow: SEC fundamentals are passed to build_company_snapshot,
    # which seeds the EODHD-style `fundamentals.*` missing entries that Phase 19.4
    # then prunes as metrics are derived.
    cs = build_company_snapshot(profile=prof, prices=prices, fundamentals=fund)
    return enrich_snapshot_with_free_real(cs, fr.to_dict())


def _phase19_4_snapshot(
    facts: dict | None = None,
    prices: PriceHistoryData | None = None,
    with_gleif: bool = True,
) -> tuple[dict, dict, dict]:
    """Full Phase 19.4 enriched snapshot + (profile_enrichment, market_metrics)."""
    facts = facts if facts is not None else _aapl_facts_with_dei()
    prices = prices if prices is not None else _build_prices()
    snap = _free_real_snapshot(facts, prices)

    prof = enrich_company_profile(
        ticker="AAPL",
        legal_name="Apple Inc.",
        exchange="NASDAQ",
        country="US",
        cik="320193",
        sec_profile=_sec_profile(),
        gleif_profile=_gleif_profile() if with_gleif else None,
    )
    snap = enrich_snapshot_with_profile_enrichment(snap, prof.to_dict())

    mm = derive_market_metrics(
        ticker="AAPL",
        fundamentals_summary=snap.get("fundamentals_summary"),
        price_history=prices if (prices and prices.price_points) else None,
        reporting_currency="USD",
    )
    mm_dict = mm.to_dict()
    snap = enrich_snapshot_with_market_metrics(snap, mm_dict)
    return snap, prof.to_dict(), mm_dict


def _run_agents(snapshot: dict):
    fda = run_financial_data_agent(snapshot, source_ids=["s1"])
    vg = run_valuation_guard_agent(
        company_snapshot=snapshot,
        financial_data_summary=financial_data_agent_output_to_dict(fda),
        source_quality_summary={"overall_source_quality": "adequate"},
    )
    return fda, vg


# ===========================================================================
# 1–3: Price + shares extraction
# ===========================================================================


def test_1_shares_outstanding_extracted_from_sec_dei():
    norm = normalize_company_facts(_aapl_facts_with_dei(), "AAPL", "320193")
    assert norm.shares_outstanding == pytest.approx(15550.061, abs=0.01)
    # Plain fixture (no DEI) leaves it missing with a warning — never fabricated.
    plain = normalize_company_facts(load_fixture("sec_companyfacts_aapl.json"), "AAPL", "320193")
    assert plain.shares_outstanding is None


def test_2_latest_close_read_from_price_history():
    mm = derive_market_metrics("AAPL", {}, _build_prices(), "USD")
    assert mm.latest_close == pytest.approx(190.0)
    assert mm.latest_close_date == "2023-09-30"


def test_3_52_week_high_low_derived():
    mm = derive_market_metrics("AAPL", {}, _build_prices(), "USD")
    assert mm.week52_high == pytest.approx(198.0)
    assert mm.week52_low == pytest.approx(145.0)
    assert mm.week52_high_date == "2023-06-15"
    assert mm.week52_low_date == "2023-01-10"


# ===========================================================================
# 4–6: Derived market cap / EV / P/E
# ===========================================================================


def test_4_market_cap_derived_only_with_close_and_shares():
    snap, _prof, mm = _phase19_4_snapshot()
    # 190.0 × 15550.061 ≈ 2,954,511.6 USD_m
    assert mm["market_cap_mln"] == pytest.approx(2954511.59, rel=1e-4)
    # Without shares, market cap is not derived.
    mm_no_shares = derive_market_metrics("AAPL", {}, _build_prices(), "USD")
    assert mm_no_shares.market_cap_mln is None


def test_5_enterprise_value_derived_only_with_mktcap_debt_cash():
    _snap, _prof, mm = _phase19_4_snapshot()
    # EV = market_cap + total_debt(111093) - cash(29965)
    assert mm["enterprise_value_mln"] == pytest.approx(2954511.59 + 111093.0 - 29965.0, rel=1e-4)
    # No cash/debt → not derived.
    mm2 = derive_market_metrics(
        "AAPL",
        {"shares_outstanding_mln": 15550.061},
        _build_prices(),
        "USD",
    )
    assert mm2.market_cap_mln is not None
    assert mm2.enterprise_value_mln is None


def test_6_pe_derived_only_with_safe_inputs():
    _snap, _prof, mm = _phase19_4_snapshot()
    # market_cap / net_income = 2,954,511.6 / 96,995 ≈ 30.5x
    assert mm["pe_ratio"] == pytest.approx(2954511.59 / 96995.0, rel=1e-3)
    assert "net_income" in mm["pe_basis"]
    # Price + EPS fallback when no market cap.
    mm_eps = derive_market_metrics(
        "AAPL",
        {"eps_diluted": 6.13},
        _build_prices(),
        "USD",
    )
    assert mm_eps.pe_ratio == pytest.approx(190.0 / 6.13, rel=1e-3)
    assert "EPS" in mm_eps.pe_basis
    # Negative net income and no EPS → no P/E fabricated.
    mm_none = derive_market_metrics("AAPL", {"net_income_usd_m": -100.0}, _build_prices(), "USD")
    assert mm_none.pe_ratio is None


# ===========================================================================
# 7–9: Never fabricate EBITDA / EV-EBITDA / beta
# ===========================================================================


def test_7_ebitda_not_fabricated():
    snap, _prof, mm = _phase19_4_snapshot()
    fs = snap["fundamentals_summary"]
    assert fs.get("ebitda_usd_m") is None
    assert not any(k == "ebitda_mln" for k in fs)
    assert any("ebitda" in w.lower() for w in mm["warnings"])


def test_8_ev_ebitda_missing_without_ebitda():
    snap, _prof, _mm = _phase19_4_snapshot()
    fs = snap["fundamentals_summary"]
    assert fs.get("ev_ebitda_x") is None
    # EV/EBITDA is still an unresolved missing field.
    assert "fundamentals.ev_ebitda_x" in snap["missing_fields"]


def test_9_beta_missing_unless_sourced():
    snap, _prof, mm = _phase19_4_snapshot()
    fs = snap["fundamentals_summary"]
    assert fs.get("beta") is None
    assert "fundamentals.beta" in snap["missing_fields"]
    assert any("beta" in w.lower() for w in mm["warnings"])


# ===========================================================================
# 10–11: Source-tier tagging
# ===========================================================================


def test_10_market_metrics_tagged_derived_estimate():
    _snap, _prof, mm = _phase19_4_snapshot()
    tiers = mm["source_tiers"]
    assert tiers["market_cap_mln"] == "T6_model_estimate"
    assert tiers["enterprise_value_mln"] == "T6_model_estimate"
    assert tiers["pe_ratio"] == "T6_model_estimate"
    assert "DERIVED ESTIMATE" in mm["note"]


def test_11_source_tiers_correct_for_sec_gleif_profile():
    _snap, prof, mm = _phase19_4_snapshot()
    tiers_p = prof["source_tiers"]
    # Sector inferred from SEC SIC → T6; website from SEC → T2; LEI from GLEIF → T2.
    assert tiers_p["sector"] == "T6_model_estimate"
    assert prof["sector_is_inferred"] is True
    assert tiers_p["website"] == "T2_regulator_or_gov"
    assert tiers_p["lei"] == "T2_regulator_or_gov"
    # Shares from SEC DEI → T2; latest close from price → T5.
    assert mm["source_tiers"]["shares_outstanding_mln"] == "T2_regulator_or_gov"
    assert mm["source_tiers"]["latest_close"] == "T5_api_aggregator"


# ===========================================================================
# 12–13: Missing-info reduction + valuation readiness
# ===========================================================================


def test_12_missing_information_count_decreases():
    before = _free_real_snapshot(_aapl_facts_with_dei(), _build_prices())
    after, _prof, _mm = _phase19_4_snapshot()
    assert len(after["missing_fields"]) < len(before["missing_fields"])
    for resolved in (
        "fundamentals.market_cap_mln",
        "fundamentals.enterprise_value_mln",
        "fundamentals.pe_ratio",
        "fundamentals.52_week_high",
        "fundamentals.52_week_low",
        "fundamentals.shares_outstanding_mln",
        "profile.sector",
    ):
        assert resolved not in after["missing_fields"], resolved


def test_13_valuation_readiness_uses_metrics_but_blocks_conclusions():
    snap, _prof, _mm = _phase19_4_snapshot()
    _fda, vg = _run_agents(snap)
    assert vg.valuation_readiness == "partial"
    blockers = " ".join(vg.valuation_blockers).lower()
    # Recognises derived market cap but keeps blocking on EBITDA / validated inputs.
    assert "derived estimate" in blockers
    assert "ebitda" in blockers
    # market cap / EV now recognised as available financial categories.
    fda, _vg = _run_agents(snap)
    assert "financials.market_cap" in fda.available_financial_data
    assert "financials.enterprise_value" in fda.available_financial_data


# ===========================================================================
# 14–16: Report text — availability + no recommendation / no price target
# ===========================================================================


def test_14_report_text_includes_market_metric_availability():
    snap, _prof, _mm = _phase19_4_snapshot()
    fda = run_financial_data_agent(snap, source_ids=["s1"])
    text = fda.financial_context_summary.lower()
    assert "derived market metrics" in text
    assert "market cap" in text
    assert "derived estimate" in text


def test_15_report_text_no_recommendation():
    snap, _prof, _mm = _phase19_4_snapshot()
    fda = run_financial_data_agent(snap, source_ids=["s1"])
    low = f" {fda.financial_context_summary.lower()} "
    for token in _RECOMMENDATION_TOKENS:
        assert token not in low, token


def test_16_report_text_no_price_target_or_fair_value():
    snap, prof, mm = _phase19_4_snapshot()
    surfaces = [
        run_financial_data_agent(snap, source_ids=["s1"]).financial_context_summary,
        mm["note"],
        " ".join(mm["warnings"]),
        " ".join(prof["warnings"]),
        (snap["fundamentals_summary"].get("market_metrics_note") or ""),
    ]
    for surface in surfaces:
        low = surface.lower()
        for token in _VALUATION_TOKENS:
            assert token not in low, f"{token!r} in: {surface[:120]}"


# ===========================================================================
# 17–19: Human review, final report generation, safety gate
# ===========================================================================


def test_17_human_review_required_remains_true():
    snap, _prof, _mm = _phase19_4_snapshot()
    fda = run_financial_data_agent(snap, source_ids=["s1"])
    fda_dict = financial_data_agent_output_to_dict(fda)
    vg = run_valuation_guard_agent(
        company_snapshot=snap,
        financial_data_summary=fda_dict,
        source_quality_summary={"overall_source_quality": "adequate"},
    )
    chair = run_investment_committee_chair(
        company_snapshot=snap,
        bull_case_summary={"positive_thesis_points": [], "confidence_level": "low"},
        bear_case_summary={"negative_thesis_points": [], "confidence_level": "low"},
        risk_summary={"risk_summary": "", "financial_risks": []},
        valuation_guard_summary=valuation_guard_output_to_dict(vg),
        research_completeness_summary={"blocking_gaps": [], "complete_sections": []},
        source_quality_summary={"overall_source_quality": "adequate"},
        upgraded_citation_validation={"status": "ok"},
        schema_valid=False,
    )
    assert chair.human_review_required is True
    assert "Human review required: False" not in chair.committee_summary


def test_18_final_report_sections_build():
    from app.services.final_report_generator import (
        _build_financial_snapshot,
        _build_valuation_readiness,
    )

    snap, _prof, _mm = _phase19_4_snapshot()
    _fda, vg = _run_agents(snap)
    section = _build_valuation_readiness(valuation_guard_output_to_dict(vg), None)
    assert section["readiness"]["value"] == "partial"
    # Financial snapshot builder still works with the enriched snapshot.
    fin = _build_financial_snapshot(snap, None)
    assert fin["type"] == "financial_snapshot"


def test_19_safety_gate_clean():
    from app.services.final_report_generator import run_safety_gate

    snap, prof, mm = _phase19_4_snapshot()
    fda = run_financial_data_agent(snap, source_ids=["s1"])
    content = {
        "market_metrics": mm,
        "identity_profile": prof,
        "narrative": fda.financial_context_summary,
        "fundamentals_summary": snap.get("fundamentals_summary"),
    }
    result = run_safety_gate(content)
    assert result.passed is True, result.forbidden_terms_found


# ===========================================================================
# 20–22: Graceful degradation when data is unavailable
# ===========================================================================


def test_20_sector_lei_isin_unavailable_warns_no_crash():
    prof = enrich_company_profile(
        ticker="XYZ",
        legal_name="Unknown Co",
        sec_profile=None,
        gleif_profile=None,
    )
    assert prof.sector is None
    assert prof.lei is None
    assert prof.isin is None
    assert any("sector" in w.lower() for w in prof.warnings)
    assert any("lei" in w.lower() for w in prof.warnings)
    assert any("isin" in w.lower() for w in prof.warnings)


def test_21_shares_unavailable_market_cap_not_fabricated():
    # Plain fixture has no DEI shares → market cap must not be fabricated.
    snap, _prof, mm = _phase19_4_snapshot(
        facts=load_fixture("sec_companyfacts_aapl.json"),
        prices=_build_prices(),
    )
    assert mm["shares_outstanding_mln"] is None
    assert mm["market_cap_mln"] is None
    assert snap["fundamentals_summary"].get("market_cap_usd_m") is None
    assert "fundamentals.market_cap_mln" in snap["missing_fields"]


def test_22_no_price_history_range_and_market_cap_missing():
    snap, _prof, mm = _phase19_4_snapshot(prices=_build_prices([]))
    assert mm["latest_close"] is None
    assert mm["week52_high"] is None
    assert mm["market_cap_mln"] is None
    assert any("no price history" in w.lower() for w in mm["warnings"])
    assert "fundamentals.52_week_high" in snap["missing_fields"]


# ===========================================================================
# 23–24: Mock unchanged + Phase 19.3 path intact
# ===========================================================================


def test_23_mock_provider_behaviour_unchanged():
    # A mock snapshot never goes through the Phase 19.4 enrichment path in the
    # workflow (guarded on provider in ('free_real','eodhd_free_real')), and the
    # enrichment functions themselves are inert on a mock snapshot with no
    # derivable inputs.
    meta = ProviderResponseMetadata(
        provider_name="mock",
        source_tier=SourceTier.T6_model_estimate,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=True,
        status=ProviderStatus.ok,
    )
    prof = CompanyProfileData(
        ticker="MOCK", legal_name="Mock Co", meta=meta, data_quality=DataQuality.D_weak_or_stale
    )
    cs = build_company_snapshot(profile=prof, prices=None, fundamentals=None)
    assert cs["is_mock"] is True
    assert "market_metrics_summary" not in cs
    mm = derive_market_metrics("MOCK", None, None, "USD")
    assert mm.market_cap_mln is None
    assert mm.latest_close is None


def test_24_phase19_3_free_real_path_intact():
    # The pre-19.4 free_real snapshot still carries normalized SEC fundamentals
    # and honestly-absent market fields (regression guard for Phase 19.3).
    snap = _free_real_snapshot(_aapl_facts_with_dei(), _build_prices())
    fs = snap["fundamentals_summary"]
    assert fs["revenue_usd_m"] == pytest.approx(383285.0)
    assert fs["net_income_usd_m"] == pytest.approx(96995.0)
    assert fs["market_cap_usd_m"] is None  # not derived until Phase 19.4 applied
    assert fs["ebitda_usd_m"] is None
