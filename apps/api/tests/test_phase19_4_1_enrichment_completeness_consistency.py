"""
Phase 19.4.1 — Enrichment Completeness Consistency.

Regression guard for the inconsistencies found after the Phase 19.4 AAPL
free_real staging smoke test: enriched identity/profile/market-metric fields
(LEI, sector classification, derived market cap / EV / P/E / 52-week range,
shares outstanding) were still being reported as missing / blocking gaps and
still triggered "Obtain LEI"-style recommendations, even though the enriched
snapshot already carried them.

These tests assert that:
  * a *present* enriched field is never reported as a missing field, a blocking
    gap, or a source-quality/committee "obtain it" recommendation, and
  * a genuinely *absent* field (ISIN, EBITDA, beta, …) still is, and
  * provider=mock behaviour is unchanged, and
  * no BUY/SELL/HOLD/WATCH, price target, fair value or upside is produced, and
  * valuation readiness stays partial and human review stays required.

All tests are offline: they reuse the Phase 19.4 fixture builders (fixture JSON
+ synthetic price/DEI data). No live SEC / GLEIF / EODHD network calls.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.agents.analysis_council.bear_case_agent import (
    bear_case_output_to_dict,
    run_bear_case_agent,
)
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
from app.agents.research_team.research_completeness_agent import (
    research_completeness_output_to_dict,
    run_research_completeness_agent,
)
from app.agents.research_team.source_quality_agent import run_source_quality_agent
from app.integrations.financial_data_provider import (
    CompanyProfileData,
    DataQuality,
    ProviderResponseMetadata,
    ProviderStatus,
    SourceTier,
)
from app.workflows.snapshot_builder import build_company_snapshot, build_schema_draft

# Reuse the Phase 19.4 offline fixture builders (tests/ is a package).
from tests.test_phase19_4_identity_sector_market_metrics import (
    _build_prices,
    _phase19_4_snapshot,
    load_fixture,
)

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
_AAPL_LEI = "HWUPKR0MPOU8FGXBT394"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_profile() -> CompanyProfileData:
    """Base provider profile passed to build_schema_draft (mirrors the workflow)."""
    meta = ProviderResponseMetadata(
        provider_name="sec_edgar_fundamentals",
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
        meta=meta,
    )


def _completeness_for(snapshot: dict) -> dict:
    """Build the schema draft the workflow would build, then run completeness."""
    draft = build_schema_draft(
        report_id="rid-19-4-1",
        snapshot=snapshot,
        profile=_base_profile(),
        prices=_build_prices(),
        fundamentals=None,  # free_real never calls the paid /fundamentals endpoint
    )
    out = run_research_completeness_agent(
        company_snapshot=snapshot,
        schema_draft=draft,
        schema_validation_errors=None,
    )
    return research_completeness_output_to_dict(out)


def _run_council(snapshot: dict) -> dict:
    """Run the deterministic council chain that feeds the committee chair."""
    completeness = _completeness_for(snapshot)
    sq = run_source_quality_agent(company_snapshot=snapshot)
    sq_dict = {
        "overall_source_quality": sq.overall_source_quality,
        "recommended_source_upgrades": sq.recommended_source_upgrades,
        "missing_primary_sources": sq.missing_primary_sources,
        "aggregator_only_claims": sq.aggregator_only_claims,
        "warnings": sq.warnings,
    }
    fda = run_financial_data_agent(snapshot, source_ids=["s1"])
    fda_dict = financial_data_agent_output_to_dict(fda)
    vg = run_valuation_guard_agent(
        company_snapshot=snapshot,
        financial_data_summary=fda_dict,
        source_quality_summary=sq_dict,
    )
    vg_dict = valuation_guard_output_to_dict(vg)
    bear = run_bear_case_agent(
        company_snapshot=snapshot,
        financial_data_summary=fda_dict,
        source_quality_summary=sq_dict,
        research_completeness_summary=completeness,
        bull_case_summary={"positive_thesis_points": [], "confidence_level": "low"},
    )
    bear_dict = bear_case_output_to_dict(bear)
    chair = run_investment_committee_chair(
        company_snapshot=snapshot,
        bull_case_summary={"positive_thesis_points": [], "confidence_level": "low"},
        bear_case_summary=bear_dict,
        risk_summary={"risk_summary": "", "financial_risks": []},
        valuation_guard_summary=vg_dict,
        research_completeness_summary=completeness,
        source_quality_summary=sq_dict,
        upgraded_citation_validation={"status": "ok"},
        schema_valid=False,
    )
    return {
        "completeness": completeness,
        "source_quality": sq,
        "valuation_guard": vg,
        "committee": chair,
    }


def _mock_snapshot() -> dict:
    meta = ProviderResponseMetadata(
        provider_name="mock",
        source_tier=SourceTier.T6_model_estimate,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=True,
        status=ProviderStatus.ok,
    )
    prof = CompanyProfileData(
        ticker="MOCK",
        legal_name="Mock Co",
        meta=meta,
        data_quality=DataQuality.D_weak_or_stale,
    )
    return build_company_snapshot(profile=prof, prices=None, fundamentals=None)


# ===========================================================================
# 1–4: LEI consistency
# ===========================================================================


def test_1_lei_present_not_in_missing_fields():
    snap, _prof, _mm = _phase19_4_snapshot()
    assert snap["company_identity"]["lei"] == _AAPL_LEI
    assert "identity.lei" not in snap["missing_fields"]


def test_2_lei_present_not_a_blocking_gap():
    snap, _prof, _mm = _phase19_4_snapshot()
    completeness = _completeness_for(snap)
    assert not any("identity.lei" in g for g in completeness["blocking_gaps"])
    assert "identity.lei" not in completeness["missing_required_fields"]


def test_3_lei_present_source_quality_does_not_recommend_obtaining_lei():
    snap, _prof, _mm = _phase19_4_snapshot()
    out = run_source_quality_agent(company_snapshot=snap)
    joined = " ".join(out.recommended_source_upgrades).lower()
    assert "obtain lei" not in joined
    # And the LEI is not listed as a missing primary source either.
    assert not any("lei" in m.lower() for m in out.missing_primary_sources)


def test_4_lei_absent_keeps_existing_missing_behaviour():
    snap, _prof, _mm = _phase19_4_snapshot(with_gleif=False)
    assert not snap["company_identity"].get("lei")
    assert "identity.lei" in snap["missing_fields"]
    # Source Quality still recommends obtaining the LEI.
    out = run_source_quality_agent(company_snapshot=snap)
    assert any("obtain lei" in r.lower() for r in out.recommended_source_upgrades)
    # And completeness still lists it as a blocking gap.
    completeness = _completeness_for(snap)
    assert any("identity.lei" in g for g in completeness["blocking_gaps"])


# ===========================================================================
# 5–6: Sector consistency
# ===========================================================================


def test_5_sector_present_not_in_missing_fields():
    snap, _prof, _mm = _phase19_4_snapshot()
    assert snap["profile"]["sector"] is not None
    assert "profile.sector" not in snap["missing_fields"]


def test_6_sector_classification_present_not_a_blocking_gap():
    snap, _prof, _mm = _phase19_4_snapshot()
    completeness = _completeness_for(snap)
    assert not any(
        "sector_classification" in g for g in completeness["blocking_gaps"]
    )
    assert "identity.sector_classification" not in completeness["missing_required_fields"]


# ===========================================================================
# 7–11: Market-metric consistency
# ===========================================================================


def test_7_market_cap_derived_not_reported_missing():
    snap, _prof, _mm = _phase19_4_snapshot()
    assert snap["fundamentals_summary"].get("market_cap_usd_m") is not None
    assert "fundamentals.market_cap_mln" not in snap["missing_fields"]
    assert "financials.market_cap" not in snap["missing_fields"]
    completeness = _completeness_for(snap)
    assert not any(
        "snapshot_financials.market_cap" in g for g in completeness["blocking_gaps"]
    )


def test_8_enterprise_value_derived_not_reported_missing():
    snap, _prof, _mm = _phase19_4_snapshot()
    assert snap["fundamentals_summary"].get("enterprise_value_usd_m") is not None
    assert "fundamentals.enterprise_value_mln" not in snap["missing_fields"]
    completeness = _completeness_for(snap)
    assert not any(
        "snapshot_financials.enterprise_value" in g
        for g in completeness["blocking_gaps"]
    )


def test_9_52_week_high_low_not_reported_missing():
    snap, _prof, _mm = _phase19_4_snapshot()
    assert "fundamentals.52_week_high" not in snap["missing_fields"]
    assert "fundamentals.52_week_low" not in snap["missing_fields"]


def test_10_pe_derived_not_reported_missing():
    snap, _prof, _mm = _phase19_4_snapshot()
    assert snap["fundamentals_summary"].get("pe_ratio") is not None
    assert "fundamentals.pe_ratio" not in snap["missing_fields"]


def test_11_source_quality_upgrades_derived_metrics_without_claiming_absent():
    snap, _prof, _mm = _phase19_4_snapshot()
    out = run_source_quality_agent(company_snapshot=snap)
    joined = " ".join(out.recommended_source_upgrades).lower()
    assert "replace derived market metrics" in joined
    # Must never assert the metric is unavailable/absent when it is present.
    all_text = " ".join(
        out.recommended_source_upgrades
        + out.missing_primary_sources
        + out.aggregator_only_claims
        + out.warnings
    ).lower()
    for absent_word in ("unavailable", "not available", "absent", "missing"):
        assert f"market cap {absent_word}" not in all_text
        assert f"market capitalization {absent_word}" not in all_text


# ===========================================================================
# 12–13: Committee open questions
# ===========================================================================


def test_12_committee_open_questions_omit_lei_when_present():
    snap, _prof, _mm = _phase19_4_snapshot()
    res = _run_council(snap)
    chair = res["committee"]
    questions = " ".join(chair.primary_open_questions).lower()
    steps = " ".join(chair.research_next_steps).lower()
    assert "identity.lei" not in questions
    assert "obtain lei" not in questions
    assert "obtain lei" not in steps
    assert "gleif (obtain lei)" not in steps


def test_13_committee_open_questions_mention_isin_when_absent():
    snap, _prof, _mm = _phase19_4_snapshot()
    assert "identity.isin" in snap["missing_fields"]
    res = _run_council(snap)
    questions = " ".join(res["committee"].primary_open_questions).lower()
    assert "isin" in questions


# ===========================================================================
# 14–16: Safety invariants preserved
# ===========================================================================


def test_14_human_review_required_remains_true():
    snap, _prof, _mm = _phase19_4_snapshot()
    chair = _run_council(snap)["committee"]
    assert chair.human_review_required is True
    assert "Human review required: False" not in chair.committee_summary


def test_15_valuation_readiness_partial_no_conclusion():
    snap, _prof, _mm = _phase19_4_snapshot()
    vg = _run_council(snap)["valuation_guard"]
    assert vg.valuation_readiness == "partial"
    # A valuation conclusion is explicitly withheld / disallowed.
    assert vg.disallowed_outputs
    assert any("withheld" in b.lower() for b in vg.valuation_blockers)


def test_16_ebitda_ev_ebitda_beta_remain_missing():
    snap, _prof, _mm = _phase19_4_snapshot()
    fs = snap["fundamentals_summary"]
    assert fs.get("ebitda_usd_m") is None
    assert "fundamentals.ev_ebitda_x" in snap["missing_fields"]
    assert "fundamentals.beta" in snap["missing_fields"]
    # EBITDA is a genuinely-absent required field → still a blocking gap.
    completeness = _completeness_for(snap)
    assert any(
        "snapshot_financials.ebitda" in g for g in completeness["blocking_gaps"]
    )


# ===========================================================================
# 17–18: No forbidden output
# ===========================================================================


def test_17_no_buy_sell_hold_watch_generated():
    snap, _prof, _mm = _phase19_4_snapshot()
    res = _run_council(snap)
    chair = res["committee"]
    completeness = res["completeness"]
    text = " " + " ".join(
        [chair.committee_summary]
        + chair.primary_open_questions
        + chair.research_next_steps
        + completeness["blocking_gaps"]
        + completeness["next_research_tasks"]
        + res["source_quality"].recommended_source_upgrades
    ).lower() + " "
    for token in _RECOMMENDATION_TOKENS:
        assert token not in text, token


def test_18_no_price_target_fair_value_or_upside():
    snap, _prof, _mm = _phase19_4_snapshot()
    res = _run_council(snap)
    chair = res["committee"]
    text = " ".join(
        [chair.committee_summary]
        + chair.primary_open_questions
        + chair.research_next_steps
        + res["source_quality"].recommended_source_upgrades
    ).lower()
    for token in _VALUATION_TOKENS:
        assert token not in text, token


# ===========================================================================
# 19–20: Regression — genuinely-absent fields + mock behaviour unchanged
# ===========================================================================


def test_19_plain_fixture_absent_market_cap_still_missing():
    # No SEC DEI shares → market cap is not derived → must remain missing/blocking.
    snap, _prof, mm = _phase19_4_snapshot(
        facts=load_fixture("sec_companyfacts_aapl.json"),
        prices=_build_prices(),
    )
    assert mm["market_cap_mln"] is None
    assert "fundamentals.market_cap_mln" in snap["missing_fields"]
    completeness = _completeness_for(snap)
    assert any(
        "snapshot_financials.market_cap" in g for g in completeness["blocking_gaps"]
    )
    # LEI is still present here (GLEIF), so it must still be suppressed.
    assert not any("identity.lei" in g for g in completeness["blocking_gaps"])


def test_20_mock_provider_behaviour_unchanged():
    snap = _mock_snapshot()
    assert snap["is_mock"] is True
    assert "identity.lei" in snap["missing_fields"]
    assert "profile.sector" in snap["missing_fields"]
    # Completeness still flags LEI + sector classification for a mock snapshot.
    completeness = _completeness_for(snap)
    assert any("identity.lei" in g for g in completeness["blocking_gaps"])
    assert any("sector_classification" in g for g in completeness["blocking_gaps"])
    # Source Quality still recommends obtaining the LEI and does not add the
    # derived-metric upgrade (no derived market metrics on a mock snapshot).
    out = run_source_quality_agent(company_snapshot=snap)
    joined = " ".join(out.recommended_source_upgrades).lower()
    assert "obtain lei" in joined
    assert "replace derived market metrics" not in joined
