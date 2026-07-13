"""
Phase 19.3.1 — SEC Fundamentals Freshness + Review Consistency.

Fixes verified here (all offline — fixture / inline dicts only, no network):

  SEC freshness
    1.  normalizer selects the latest annual fiscal year across ALL alias
        concepts, even when a stale taxonomy tag exists (no FY2018 shadowing).
    2.  filed date breaks fiscal-year ties (amended/restated filing wins).
    3.  quarterly (10-Q) fallback only when no annual data, with a warning.
    4.  stale-data warning when the selected annual year is > 2 years old.

  Report wording consistency
    5.  with normalized SEC fundamentals the report never says
        "No financial fundamentals sourced".
    6.  bear case acknowledges partial fundamentals instead of claiming all
        fundamentals are missing.
    7.  risk / committee wording does not list revenue / net income / cash flow /
        debt as missing when they are present.

  Human-review consistency
    8.  committee markdown never contains "Human review required: False" when the
        canonical state requires review.
    9.  human_review_required stays true for schema_invalid / research_incomplete.

  Safety (unchanged guarantees)
    10. safety_valid stays true (no forbidden phrases in narratives).
    11. no BUY/SELL/HOLD/WATCH recommendation is generated.
    12. no price target / fair value / upside is generated.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import datetime, timezone

import pytest

from app.agents.analysis_council.bear_case_agent import (
    bear_case_output_to_dict,
    run_bear_case_agent,
)
from app.agents.analysis_council.bull_case_agent import (
    bull_case_output_to_dict,
    run_bull_case_agent,
)
from app.agents.analysis_council.investment_committee_chair import (
    run_investment_committee_chair,
)
from app.agents.analysis_council.risk_agent import (
    risk_agent_output_to_dict,
    run_risk_agent,
)
from app.agents.analysis_council.valuation_guard_agent import (
    run_valuation_guard_agent,
    valuation_guard_output_to_dict,
)
from app.agents.research_team.financial_data_agent import (
    financial_data_agent_output_to_dict,
    run_financial_data_agent,
)
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
# Inline fixtures for the SEC-selection tests
# ---------------------------------------------------------------------------


def _annual_entry(fy: int, val: int, filed: str, form: str = "10-K") -> dict:
    return {
        "end": f"{fy}-09-30",
        "val": val,
        "accn": f"acc-{fy}-{form}",
        "fy": fy,
        "fp": "FY",
        "form": form,
        "filed": filed,
    }


def _taxonomy_switch_facts() -> dict:
    """
    Mimics Apple: a stale ``Revenues`` tag that stops at FY2018 plus the current
    ``RevenueFromContractWithCustomerExcludingAssessedTax`` tag carrying FY2021+.
    First-alias-wins would return the stale FY2018 value.
    """
    return {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            _annual_entry(2016, 215639000000, "2016-10-26"),
                            _annual_entry(2017, 229234000000, "2017-11-03"),
                            _annual_entry(2018, 265595000000, "2018-11-05"),
                        ]
                    }
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            _annual_entry(2021, 365817000000, "2021-10-29"),
                            _annual_entry(2022, 394328000000, "2022-10-28"),
                            _annual_entry(2023, 383285000000, "2023-11-03"),
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            _annual_entry(2022, 99803000000, "2022-10-28"),
                            _annual_entry(2023, 96995000000, "2023-11-03"),
                        ]
                    }
                },
            }
        },
    }


# ===========================================================================
# 1–4: SEC freshness selection
# ===========================================================================


def test_1_latest_annual_across_alias_concepts_not_stale_fy2018():
    result = normalize_company_facts(_taxonomy_switch_facts(), "AAPL", "320193")
    # Must pick the fresh FY2023 tag, never the stale FY2018 ``Revenues`` tag.
    assert result.fiscal_year == 2023
    assert result.period_basis == "annual"
    assert result.revenue == pytest.approx(383285.0)
    assert result.revenue != pytest.approx(265595.0)  # FY2018 stale value
    # YoY uses the fresh concept's prior year (FY2022), not the stale tag.
    assert result.revenue_yoy_growth == pytest.approx(-2.80, abs=0.05)


def test_2_filed_date_breaks_fiscal_year_tie():
    """An amended/restated 10-K/A filed later supersedes the original 10-K."""
    facts = {
        "cik": 111,
        "entityName": "Restate Co",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            _annual_entry(2023, 383285000000, "2023-11-03", "10-K"),
                            _annual_entry(2023, 380000000000, "2024-02-01", "10-K/A"),
                        ]
                    }
                }
            }
        },
    }
    result = normalize_company_facts(facts, "RST", "111")
    assert result.fiscal_year == 2023
    assert result.revenue == pytest.approx(380000.0)  # later-filed restatement
    assert result.form_type == "10-K/A"


def test_3_quarterly_fallback_only_without_annual_and_warns():
    facts = {
        "cik": 222,
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
    result = normalize_company_facts(facts, "QONLY", "222")
    assert result.period_basis == "quarterly"
    assert result.revenue == pytest.approx(5000.0)
    assert result.revenue_yoy_growth is None
    assert any("quarterly" in w.lower() for w in result.warnings)


def test_4_stale_annual_year_emits_warning():
    current_year = datetime.now(timezone.utc).year
    stale_fy = current_year - 5
    facts = {
        "cik": 333,
        "entityName": "Stale Co",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            _annual_entry(stale_fy, 100000000000, f"{stale_fy}-11-01")
                        ]
                    }
                }
            }
        },
    }
    result = normalize_company_facts(facts, "STL", "333")
    assert result.fiscal_year == stale_fy
    assert any("stale" in w.lower() for w in result.warnings)


def test_4b_recent_annual_year_no_stale_warning():
    current_year = datetime.now(timezone.utc).year
    fresh_fy = current_year - 1
    facts = {
        "cik": 444,
        "entityName": "Fresh Co",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            _annual_entry(fresh_fy, 100000000000, f"{fresh_fy}-11-01")
                        ]
                    }
                }
            }
        },
    }
    result = normalize_company_facts(facts, "FRS", "444")
    assert result.fiscal_year == fresh_fy
    assert not any("stale" in w.lower() for w in result.warnings)


# ===========================================================================
# Full free_real snapshot + council helpers (AAPL fixture)
# ===========================================================================


def _build_fundamentals(aapl_facts: dict) -> FundamentalsData:
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


def _enriched_snapshot() -> dict:
    aapl_facts = load_fixture("sec_companyfacts_aapl.json")
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
    prof = _make_profile(fund)
    cs = build_company_snapshot(profile=prof, prices=prices, fundamentals=None)
    return enrich_snapshot_with_free_real(cs, fr.to_dict())


def _make_profile(fund: FundamentalsData) -> CompanyProfileData:
    return CompanyProfileData(
        ticker="AAPL",
        exchange="NASDAQ",
        legal_name="Apple Inc.",
        country_domicile="US",
        reporting_currency="USD",
        meta=fund.meta,
    )


def _council(snapshot: dict, *, blocking_gaps=None, schema_valid=True):
    """Run the research/analysis council against the enriched snapshot."""
    fda = run_financial_data_agent(snapshot, source_ids=["s1"])
    fda_dict = financial_data_agent_output_to_dict(fda)
    sq = {
        "overall_source_quality": "adequate",
        "aggregator_only_claims": [],
        "missing_primary_sources": [],
        "warnings": [],
    }
    rc = {
        "blocking_gaps": blocking_gaps or [],
        "next_research_tasks": ["Source T1 filings."],
        "complete_sections": ["identity", "price_history", "financials"],
        "missing_required_fields": [],
        "incomplete_sections": [],
    }
    citation = {"status": "ok", "weak_citation_warnings": [], "approved_fields": []}

    bull = run_bull_case_agent(snapshot, fda_dict, sq, rc)
    bear = run_bear_case_agent(
        snapshot, fda_dict, sq, rc, bull_case_output_to_dict(bull)
    )
    risk = run_risk_agent(snapshot, fda_dict, sq, rc, citation)
    vg = run_valuation_guard_agent(
        company_snapshot=snapshot,
        financial_data_summary=fda_dict,
        source_quality_summary=sq,
    )
    chair = run_investment_committee_chair(
        company_snapshot=snapshot,
        bull_case_summary=bull_case_output_to_dict(bull),
        bear_case_summary=bear_case_output_to_dict(bear),
        risk_summary=risk_agent_output_to_dict(risk),
        valuation_guard_summary=valuation_guard_output_to_dict(vg),
        research_completeness_summary=rc,
        source_quality_summary=sq,
        upgraded_citation_validation=citation,
        schema_valid=schema_valid,
    )
    return {"fda": fda, "bull": bull, "bear": bear, "risk": risk, "vg": vg, "chair": chair}


# ===========================================================================
# 5–7: Report wording consistency (partial SEC fundamentals present)
# ===========================================================================


def test_5_report_does_not_say_no_fundamentals_sourced():
    snapshot = _enriched_snapshot()
    fda = run_financial_data_agent(snapshot, source_ids=["s1"])
    assert (
        "No financial fundamentals sourced at this phase"
        not in fda.financial_context_summary
    )
    assert "SEC EDGAR" in fda.financial_context_summary
    assert "Revenue" in fda.financial_context_summary
    assert "Net income" in fda.financial_context_summary


def test_6_bear_case_acknowledges_partial_fundamentals():
    snapshot = _enriched_snapshot()
    out = _council(snapshot)
    bear = out["bear"]
    joined = " ".join(bear.negative_thesis_points)
    # Acknowledges partial completeness.
    assert "partial" in joined.lower()
    # Does NOT claim all core fundamentals are missing.
    assert "core financial fundamental categories are missing" not in joined
    # key_unknowns no longer says revenue/net income/cash flow/debt none sourced.
    unknowns = " ".join(bear.key_unknowns).lower()
    assert "none sourced at this phase" not in unknowns
    assert any("valuation inputs remain missing" in k.lower() for k in bear.key_unknowns)
    # But still names the genuinely missing market/valuation inputs.
    assert "ebitda" in unknowns or "market cap" in unknowns or "enterprise value" in unknowns


def test_7_risk_and_committee_do_not_mark_present_financials_missing():
    snapshot = _enriched_snapshot()
    out = _council(snapshot)
    risk_fin = " ".join(out["risk"].financial_risks)
    # Risk says data is partial, not all-missing.
    assert "partial" in risk_fin.lower()
    assert "All" not in risk_fin or "core financial categories missing" not in risk_fin
    # Committee open questions must not claim revenue/net income/cash flow/debt absent.
    questions = " ".join(out["chair"].primary_open_questions).lower()
    assert "none sourced at this phase" not in questions


# ===========================================================================
# 8–9: Human-review consistency
# ===========================================================================


def test_8_committee_markdown_not_false_when_review_required():
    """research_incomplete (blocking gap) must force human review + consistent text."""
    snapshot = _enriched_snapshot()
    out = _council(snapshot, blocking_gaps=["business_model_unassessed"], schema_valid=True)
    chair = out["chair"]
    assert chair.provisional_internal_status == "research_incomplete"
    assert chair.human_review_required is True
    assert "Human review required: False" not in chair.committee_summary
    assert "Human review required: True" in chair.committee_summary


def test_9_human_review_true_for_schema_invalid():
    snapshot = _enriched_snapshot()
    out = _council(snapshot, blocking_gaps=None, schema_valid=False)
    chair = out["chair"]
    assert chair.human_review_required is True
    assert "Human review required: False" not in chair.committee_summary


def test_9b_human_review_true_for_research_incomplete():
    snapshot = _enriched_snapshot()
    out = _council(snapshot, blocking_gaps=["gap_a", "gap_b"], schema_valid=True)
    assert out["chair"].human_review_required is True


# ===========================================================================
# 10–12: Safety guarantees preserved
# ===========================================================================


def _narrative_surfaces(out: dict) -> list[str]:
    return [
        out["fda"].financial_context_summary,
        " ".join(out["bull"].positive_thesis_points),
        " ".join(out["bear"].negative_thesis_points),
        " ".join(out["bear"].key_unknowns),
        out["risk"].risk_summary,
        " ".join(out["risk"].financial_risks),
        out["chair"].committee_summary,
        " ".join(out["chair"].primary_open_questions),
    ]


def test_10_safety_no_forbidden_phrases_in_narratives():
    snapshot = _enriched_snapshot()
    out = _council(snapshot)
    for surface in _narrative_surfaces(out):
        low = f" {surface.lower()} "
        for token in _VALUATION_TOKENS:
            assert token not in low, f"forbidden valuation phrase in: {surface[:120]}"


def test_11_no_recommendation_generated():
    snapshot = _enriched_snapshot()
    out = _council(snapshot)
    for surface in _narrative_surfaces(out):
        low = f" {surface.lower()} "
        for token in _RECOMMENDATION_TOKENS:
            assert token not in low, f"recommendation token {token!r} in: {surface[:120]}"


def test_12_no_price_target_or_fair_value_or_upside():
    snapshot = _enriched_snapshot()
    out = _council(snapshot)
    for surface in _narrative_surfaces(out):
        low = surface.lower()
        for token in ("price target", "fair value", "intrinsic value", "upside", "downside"):
            assert token not in low, f"forbidden term {token!r} in: {surface[:120]}"
