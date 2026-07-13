"""
Phase 19.3 — SEC Fundamentals Normalization and Report Completeness Upgrade.

Verifies that SEC EDGAR XBRL companyfacts are normalized into structured
financial metrics, injected into the free_real snapshot, and consumed by the
FinancialDataAgent and ValuationGuardAgent — improving report completeness and
valuation readiness WITHOUT producing any recommendation, price target, fair
value or upside.

All tests are offline: fixture JSON only. No live SEC or EODHD network calls.

Coverage (per Phase 19.3 spec):
   1. normalizer extracts latest annual revenue
   2. normalizer extracts net income
   3. normalizer extracts EPS basic/diluted
   4. normalizer extracts assets/liabilities/equity
   5. normalizer extracts operating cash flow
   6. normalizer derives free cash flow when capex exists
   7. normalizer derives margins safely
   8. normalizer derives YoY revenue growth when prior-year data exists
   9. missing concepts produce warnings, not crashes
  10. EBITDA is not fabricated when unavailable
  11. TTM fields are not mislabeled when only annual data exists
  12. free_real snapshot includes normalized SEC fundamentals
  13. report no longer says "No financial fundamentals sourced at this phase"
  14. missing_information count decreases for financial fields
  15. valuation readiness uses financials but still blocks conclusions
  16. no BUY/SELL/HOLD/WATCH generated
  17. no price target/fair value/upside generated
  18. human_review_required remains true (valuation guard disallows it)
  19. final report generation still works
  20. safety_valid remains true (no forbidden phrases anywhere)
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import datetime, timezone

import pytest

from app.agents.analysis_council.valuation_guard_agent import run_valuation_guard_agent
from app.agents.research_team.financial_data_agent import run_financial_data_agent
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
from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts
from app.integrations.sec_fundamentals_normalizer import normalize_company_facts
from app.workflows.snapshot_builder import (
    build_company_snapshot,
    enrich_snapshot_with_free_real,
)

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures"

_FORBIDDEN_TERMS = {
    "buy", "sell", "hold", "watch", "reject", "shortlist",
    "price target", "target price", "fair value", "intrinsic value",
    "upside", "downside", "undervalued", "overvalued",
}


def load_fixture(name: str) -> dict:
    with open(FIXTURE_DIR / name) as f:
        return json.load(f)


@pytest.fixture
def aapl_facts() -> dict:
    return load_fixture("sec_companyfacts_aapl.json")


@pytest.fixture
def normalized(aapl_facts):
    return normalize_company_facts(aapl_facts, "AAPL", "320193")


# ---------------------------------------------------------------------------
# Helpers to assemble a full free_real snapshot from the fixture
# ---------------------------------------------------------------------------


def _build_fundamentals(aapl_facts: dict) -> FundamentalsData:
    """Mirror SecEdgarFundamentalsProvider.get_fundamentals merge logic."""
    dps, _ = parse_company_facts(aapl_facts, "AAPL", "320193")
    norm = normalize_company_facts(aapl_facts, "AAPL", "320193")
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


def _build_prices() -> PriceHistoryData:
    meta = ProviderResponseMetadata(
        provider_name="stooq",
        source_tier=SourceTier.T5_api_aggregator,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
        note="stooq ok",
    )
    return PriceHistoryData(
        ticker="AAPL",
        exchange="NASDAQ",
        currency="USD",
        price_points=[
            PricePoint(date="2023-09-01", close=180.0),
            PricePoint(date="2023-09-30", close=190.0),
        ],
        data_quality=DataQuality.B_single_credible,
        meta=meta,
    )


def _enriched_snapshot(aapl_facts: dict) -> dict:
    fund = _build_fundamentals(aapl_facts)
    prices = _build_prices()
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
            ident, price_data=prices, fundamentals_data=fund, provider_stack="free_real"
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
    cs = build_company_snapshot(profile=prof, prices=prices, fundamentals=None)
    return enrich_snapshot_with_free_real(cs, fr.to_dict())


def _run_agents(snapshot: dict):
    fda = run_financial_data_agent(snapshot, source_ids=["s1"])
    vg = run_valuation_guard_agent(
        company_snapshot=snapshot,
        financial_data_summary={
            "available_financial_data": fda.available_financial_data,
            "missing_financial_data": fda.missing_financial_data,
        },
        source_quality_summary={"overall_source_quality": "moderate"},
    )
    return fda, vg


# ===========================================================================
# 1–11: Normalizer unit tests
# ===========================================================================


def test_1_extracts_latest_annual_revenue(normalized):
    # AAPL FY2023 revenue = 383,285,000,000 → 383,285.0 USD_m
    assert normalized.revenue == pytest.approx(383285.0)
    assert normalized.fiscal_year == 2023
    assert normalized.form_type == "10-K"


def test_2_extracts_net_income(normalized):
    assert normalized.net_income == pytest.approx(96995.0)


def test_3_extracts_eps_basic_and_diluted(normalized):
    assert normalized.eps_basic == pytest.approx(6.16)
    assert normalized.eps_diluted == pytest.approx(6.13)


def test_4_extracts_balance_sheet(normalized):
    assert normalized.total_assets == pytest.approx(352583.0)
    assert normalized.total_liabilities == pytest.approx(290437.0)
    assert normalized.shareholders_equity == pytest.approx(62146.0)


def test_5_extracts_operating_cash_flow(normalized):
    assert normalized.operating_cash_flow == pytest.approx(110543.0)


def test_6_derives_free_cash_flow_from_capex(normalized):
    # FCF = OCF - capex = 110543 - 10959 = 99584
    assert normalized.capital_expenditures == pytest.approx(10959.0)
    assert normalized.free_cash_flow == pytest.approx(99584.0)


def test_7_derives_margins_safely(normalized):
    assert normalized.gross_margin == pytest.approx(44.13, abs=0.05)
    assert normalized.operating_margin == pytest.approx(29.82, abs=0.05)
    assert normalized.net_margin == pytest.approx(25.31, abs=0.05)
    assert normalized.return_on_equity == pytest.approx(156.08, abs=0.1)
    assert normalized.debt_to_equity == pytest.approx(1.788, abs=0.01)


def test_8_derives_revenue_yoy_growth(normalized):
    # (383285 - 394328) / 394328 * 100 = -2.80%
    assert normalized.revenue_yoy_growth == pytest.approx(-2.80, abs=0.05)
    assert normalized.net_income_yoy_growth == pytest.approx(-2.81, abs=0.05)
    assert normalized.free_cash_flow_yoy_growth == pytest.approx(-10.64, abs=0.1)


def test_9_missing_concepts_warn_not_crash():
    empty = {"cik": 999999, "entityName": "Empty Co", "facts": {"us-gaap": {}}}
    result = normalize_company_facts(empty, "EMPTY", "999999")
    assert result.revenue is None
    assert result.has_any_fundamentals() is False
    assert len(result.warnings) > 0


def test_10_ebitda_not_fabricated(normalized):
    assert normalized.ebitda is None
    assert any("EBITDA" in w and "not fabricated" in w.lower() for w in normalized.warnings)
    # No datapoint should assert an EBITDA value
    assert not any(
        dp.field_name == "sec_edgar.ebitda" for dp in normalized.to_datapoints()
    )


def test_11_annual_data_not_mislabeled_ttm(normalized):
    assert normalized.period_basis == "annual"
    for dp in normalized.to_datapoints():
        note = (dp.note or "").lower()
        assert "ttm" not in note, f"annual datapoint mislabeled TTM: {dp.field_name}"


def test_11b_quarterly_fallback_warns():
    """When only 10-Q data exists, fall back with a warning and skip YoY."""
    facts = {
        "cik": 111,
        "entityName": "QOnly Inc.",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": 5000000000,
                                "fy": 2024,
                                "fp": "Q1",
                                "form": "10-Q",
                                "filed": "2024-04-30",
                            }
                        ]
                    }
                }
            }
        },
    }
    result = normalize_company_facts(facts, "QONLY", "111")
    assert result.period_basis == "quarterly"
    assert result.revenue == pytest.approx(5000.0)
    assert result.revenue_yoy_growth is None
    assert any("quarterly" in w.lower() for w in result.warnings)


# ===========================================================================
# 12–14: Snapshot & report-completeness integration
# ===========================================================================


def test_12_snapshot_includes_normalized_fundamentals(aapl_facts):
    snapshot = _enriched_snapshot(aapl_facts)
    fs = snapshot.get("fundamentals_summary")
    assert fs is not None
    assert fs["revenue_usd_m"] == pytest.approx(383285.0)
    assert fs["net_income_usd_m"] == pytest.approx(96995.0)
    assert fs["operating_cash_flow_usd_m"] == pytest.approx(110543.0)
    assert fs["free_cash_flow_usd_m"] == pytest.approx(99584.0)
    assert fs["total_debt_usd_m"] == pytest.approx(111093.0)
    assert fs["cash_and_equivalents_usd_m"] == pytest.approx(29965.0)
    assert fs["net_margin_pct"] == pytest.approx(25.31, abs=0.05)
    assert fs["fiscal_year"] == 2023
    assert fs["period_basis"] == "annual"
    # Honest absence
    assert fs["ebitda_usd_m"] is None
    assert fs["market_cap_usd_m"] is None


def test_13_report_no_longer_says_no_fundamentals(aapl_facts):
    snapshot = _enriched_snapshot(aapl_facts)
    fda = run_financial_data_agent(snapshot, source_ids=["s1"])
    assert "No financial fundamentals sourced at this phase" not in fda.financial_context_summary
    # It should mention SEC-sourced revenue and net income
    summary = fda.financial_context_summary
    assert "SEC EDGAR" in summary
    assert "Revenue" in summary
    assert "Net income" in summary


def test_14_missing_information_count_decreases(aapl_facts):
    """The financial-fundamentals missing count is materially reduced."""
    snapshot = _enriched_snapshot(aapl_facts)
    fda = run_financial_data_agent(snapshot, source_ids=["s1"])
    missing_fin = [m for m in fda.missing_financial_data if m.startswith("financials.")]
    available_fin = [a for a in fda.available_financial_data if a.startswith("financials.")]

    # Before Phase 19.3 ALL 18 categories were missing. Now several are sourced.
    assert "financials.revenue" in available_fin
    assert "financials.net_income" in available_fin
    assert "financials.free_cash_flow" in available_fin
    assert "financials.total_debt" in available_fin
    assert "financials.cash_and_equivalents" in available_fin
    assert len(available_fin) >= 8
    # Still less than the full expected set — completeness is partial, not faked.
    assert len(missing_fin) < len(available_fin) + len(missing_fin)
    # Market-based fields legitimately remain missing.
    assert "financials.market_cap" in missing_fin
    assert "financials.ebitda" in missing_fin


# ===========================================================================
# 15–20: Valuation readiness & safety
# ===========================================================================


def test_15_valuation_readiness_partial_but_blocks_conclusions(aapl_facts):
    snapshot = _enriched_snapshot(aapl_facts)
    _fda, vg = _run_agents(snapshot)
    # Readiness improves from not_ready to partial with SEC statement data.
    assert vg.valuation_readiness == "partial"
    # But conclusions remain blocked because market data / EBITDA missing.
    blockers_text = " ".join(vg.valuation_blockers).lower()
    assert "ebitda" in blockers_text
    assert "market cap" in blockers_text or "market capitalization" in blockers_text
    # Sourced financial inputs are now recognized as available.
    assert any("revenue" in x for x in vg.available_valuation_inputs)
    assert any("free_cash_flow" in x for x in vg.available_valuation_inputs)


def test_15b_mock_still_not_ready():
    """Guard must stay not_ready for mock/synthetic data (unchanged safety)."""
    snapshot = {
        "is_mock": True,
        "company_identity": {"ticker": "X", "legal_name": "X"},
        "profile": {},
        "price_history_summary": {"available": False},
        "provider_metadata": {"source_tier": "T6_model_estimate", "provider_name": "mock"},
    }
    vg = run_valuation_guard_agent(
        company_snapshot=snapshot,
        financial_data_summary={"available_financial_data": [], "missing_financial_data": []},
        source_quality_summary={"overall_source_quality": "insufficient"},
    )
    assert vg.valuation_readiness == "not_ready"


def test_16_no_recommendation_generated(aapl_facts):
    snapshot = _enriched_snapshot(aapl_facts)
    fda, _vg = _run_agents(snapshot)
    # The generated analysis text itself must not issue a recommendation.
    # (The guard's disallowed_outputs legitimately *names* these as forbidden.)
    for term in ("buy", "sell", "hold", " watch"):
        assert term not in fda.financial_context_summary.lower()


def test_17_no_price_target_or_fair_value(aapl_facts):
    snapshot = _enriched_snapshot(aapl_facts)
    fda, _vg = _run_agents(snapshot)
    text = fda.financial_context_summary.lower()
    for term in ("price target", "fair value", "intrinsic value", "upside", "downside"):
        assert term not in text


def test_18_human_review_required_via_disallowed_outputs(aapl_facts):
    snapshot = _enriched_snapshot(aapl_facts)
    _fda, vg = _run_agents(snapshot)
    disallowed = " ".join(vg.disallowed_outputs).lower()
    assert "price target" in disallowed
    assert "fair value" in disallowed or "intrinsic value" in disallowed
    assert "recommendation" in disallowed


def test_19_final_report_valuation_section_builds(aapl_facts):
    """The final report valuation-readiness section consumes the guard output."""
    from app.services.final_report_generator import _build_valuation_readiness

    snapshot = _enriched_snapshot(aapl_facts)
    _fda, vg = _run_agents(snapshot)
    from app.agents.analysis_council.valuation_guard_agent import (
        valuation_guard_output_to_dict,
    )

    section = _build_valuation_readiness(valuation_guard_output_to_dict(vg), None)
    assert section["type"] == "valuation_readiness"
    assert section["human_review_required"] is True
    assert section["readiness"]["value"] == "partial"
    assert "No valuation estimates" in section["disclaimer"]


def test_20_safety_no_forbidden_phrases_anywhere(aapl_facts):
    snapshot = _enriched_snapshot(aapl_facts)
    fda, vg = _run_agents(snapshot)
    # The analysis narrative and the fundamentals note must be clean.
    fs = snapshot["fundamentals_summary"]
    surfaces = [
        fda.financial_context_summary,
        fs.get("note", ""),
        " ".join(fda.warnings),
    ]
    for surface in surfaces:
        low = surface.lower()
        for term in _FORBIDDEN_TERMS:
            assert term not in low, f"forbidden term '{term}' in: {surface[:120]}"
