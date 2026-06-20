"""
Tests for the real-asset equity report schema validation utility.

All tests run offline — no Azure, EODHD, LLM, or database credentials required.
Loads:
  - packages/research-contracts/real_asset_equity/v1/report_schema.json
  - packages/research-contracts/real_asset_equity/v1/example_report_filled.json
"""

from __future__ import annotations

import json
from pathlib import Path

from app.services.report_validation_service import (
    ValidationResult,
    validate_real_asset_report,
)

# ---------------------------------------------------------------------------
# Helpers to load the contract files
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parents[3]
_CONTRACT_DIR = _REPO_ROOT / "packages" / "research-contracts" / "real_asset_equity" / "v1"
_EXAMPLE_PATH = _CONTRACT_DIR / "example_report_filled.json"


def _load_example() -> dict:
    with _EXAMPLE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _make_minimal_datapoint(value: object = "test", quality: str = "B_single_credible") -> dict:
    return {
        "value": value,
        "as_of": "2026-01-01",
        "source_tier": "T1_primary_filing",
        "source_name": "Test filing",
        "source_url": None,
        "data_quality": quality,
        "unit": None,
        "note": None,
    }


def _make_minimal_score_item(score: int = 3) -> dict:
    return {
        "score": score,
        "weight": 0.125,
        "rationale": "Justification referencing specific facts captured elsewhere in the report.",
        "key_evidence": [_make_minimal_datapoint()],
    }


def _make_minimal_valid_report() -> dict:
    """Build the smallest possible report that satisfies every required field."""
    dp = _make_minimal_datapoint

    return {
        "report_meta": {
            "schema_version": "1.0.0",
            "report_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "generated_at": "2026-06-01T10:00:00Z",
            "agent_pipeline_version": "test-1.0",
            "candidate_emerged_from": "Supply chain laddering from grid capex.",
            "core_target_profile": "Mispriced optionality on grid build-out at a sole-qualified transformer core facility.",
            "theme_tags": ["grid_transmission"],
            "conviction": "WATCHLIST",
        },
        "identity": {
            "legal_name": dp("Test Corp AB"),
            "ticker": dp("TSTC"),
            "exchange": dp("Nasdaq Stockholm"),
            "country_domicile": dp("Sweden"),
            "country_primary_operations": dp("Sweden"),
            "reporting_currency": dp("SEK"),
            "fiscal_year_end": dp("31 December"),
            "founded_year": dp(1985),
            "one_line_description": dp("Makes transformer cores."),
            "position_in_supply_chain": dp("processing-refining"),
        },
        "discovery_profile": {
            "entry_path": "supply_chain_laddering",
            "supply_chain_distance_from_obvious": 2,
            "coverage_metrics": {
                "sell_side_estimate_count": dp(1),
                "english_news_volume_12m": dp(5),
                "sector_tag_mismatch": dp(True),
                "institutional_ownership_pct": dp(12.0),
                "primary_disclosure_language": dp("Swedish"),
                "days_since_ipo_or_relisting": dp(None),
            },
            "event_trigger": None,
            "discovery_edge_summary": (
                "Reached via supply-chain laddering from grid capex; "
                "only one sell-side analyst; Swedish primary disclosure; "
                "sector mis-tagged as iron & steel."
            ),
        },
        "snapshot_financials": {
            "price": dp(42.5),
            "market_cap_usd_m": dp(320.0),
            "enterprise_value_usd_m": dp(380.0),
            "net_debt_usd_m": dp(60.0),
            "revenue_ttm_usd_m": dp(210.0),
            "ebitda_ttm_usd_m": dp(38.0),
            "fcf_ttm_usd_m": dp(18.0),
            "ev_ebitda_x": dp(10.0),
            "ev_revenue_x": dp(1.8),
            "fcf_yield_pct": dp(5.6),
            "net_debt_ebitda_x": dp(1.6),
            "shares_out_m": dp(75.0),
            "avg_daily_value_traded_usd_k": dp(180.0),
            "free_float_pct": dp(60.0),
            "is_under_2bn_usd": dp(True),
        },
        "thesis": {
            "one_paragraph_thesis": (
                "Test Corp AB is the sole Nordic-qualified manufacturer of grain-oriented "
                "electrical-steel transformer cores. The European grid build-out requires "
                "multi-year transformer procurement, creating a structural supply constraint "
                "at this chokepoint. The company trades at a 30% discount to replacement cost "
                "and has minimal analyst coverage due to an iron-and-steel sector mis-tag. "
                "The re-rating catalyst is contract wins from utility customers accelerating "
                "capex over the next 18-36 months, which should close the valuation gap."
            ),
            "why_underresearched": "Only one sell-side analyst; Swedish-language disclosures; mis-tagged as iron & steel.",
            "macro_geopolitical_tailwind": "EU grid investment mandated by REPowerEU and TEN-E; IEA expects 2x transmission capex by 2030.",
            "variant_perception": "Market prices this as a legacy steel company; we see it as a sole-source grid-critical component supplier.",
            "what_would_break_thesis": [
                "Customer base concentrates in one utility which cancels capex program.",
                "New GOES processing capacity enters the Nordic market.",
            ],
        },
        "business": {
            "revenue_segments": [
                {
                    "segment": "Transformer Cores",
                    "pct_of_revenue": dp(80.0),
                    "comment": "Primary product",
                }
            ],
            "geographic_revenue_split": [
                {"region": "Nordics", "pct_of_revenue": dp(70.0)},
                {"region": "Rest of Europe", "pct_of_revenue": dp(30.0)},
            ],
            "market_share": dp(25.0),
            "moat_assessment": "Sole qualified Nordic supplier of large transformer cores; 18-month qualification lead time for new entrants.",
            "customer_concentration": dp(42.0),
            "key_customers": ["Utility A", "OEM B"],
            "industry_trends": "Transformer demand growing 8-12% pa driven by EU grid investment and data-center buildout.",
        },
        "real_asset_block": {
            "asset_type": "manufacturing_capacity",
            "asset_quality_summary": "Two-site Swedish/Polish processing facility; machinery 60% depreciated but maintained to OEM spec.",
            "ppe_net_usd_m": dp(145.0),
            "ppe_pct_of_assets": dp(68.0),
            "goodwill_intangibles_pct_of_assets": dp(3.0),
            "estimated_replacement_value_usd_m": dp(510.0),
            "capex_cycle_stage": dp("sustaining"),
            "sustaining_capex_usd_m": dp(12.0),
            "growth_capex_usd_m": dp(8.0),
            "capacity_metric": dp(18000.0),
            "utilization_pct": dp(84.0),
            "mining_resource": None,
            "offtake_contract_coverage": None,
            "commodity_price_sensitivity": None,
        },
        "financials_deep": {
            "revenue_3y": [dp(170.0), dp(192.0), dp(210.0)],
            "ebitda_margin_3y": [dp(16.0), dp(17.5), dp(18.1)],
            "fcf_3y": [dp(10.0), dp(14.0), dp(18.0)],
            "roic_or_roce": dp(14.0),
            "balance_sheet_summary": "Net debt of USD 60m; leverage comfortable at 1.6x EBITDA.",
            "cashflow_quality_comment": "Strong cash conversion; minimal working-capital volatility.",
            "debt_structure": {
                "total_debt_usd_m": dp(82.0),
                "net_debt_ebitda_x": dp(1.6),
                "nearest_maturity": dp("2027-12-31"),
                "weighted_avg_cost_of_debt_pct": dp(4.2),
                "covenant_headroom_comment": "Net leverage covenant at 3.0x; current 1.6x gives ample headroom.",
                "liquidity_runway_comment": "EUR 45m RCF undrawn.",
            },
        },
        "valuation": {
            "primary_method": "EV_EBITDA_relative",
            "primary_method_justification": (
                "EV/EBITDA is the standard method for profitable industrial manufacturers "
                "with stable margins; DCF is secondary given limited capex visibility beyond 3 years."
            ),
            "fair_value_per_share": dp(62.0),
            "upside_downside_pct": dp(46.0),
            "bull_bear_base": {
                "bear_value": dp(35.0),
                "base_value": dp(62.0),
                "bull_value": dp(88.0),
                "skew_comment": "Downside floored by replacement-cost discount; upside driven by multiple re-rating.",
            },
            "key_valuation_assumptions": [
                "EBITDA margin stays above 17% on new contracts.",
                "EV/EBITDA re-rates from 10x to 14x peer median over 24 months.",
            ],
            "implied_vs_replacement_value": "EV at USD 380m is 75% of estimated USD 510m replacement cost — significant margin of safety.",
        },
        "peers": {
            "peer_construction_logic": "Nordic and Central European manufacturers of electrical-steel products and transformer components.",
            "peer_table": [
                {
                    "name": "Peer Alpha SA",
                    "ticker": "PALP.WA",
                    "market_cap_usd_m": dp(520.0),
                    "ev_ebitda_x": dp(13.5),
                    "key_differentiator_vs_target": "Larger, more diversified; less transformer-core focused.",
                },
                {
                    "name": "Peer Beta AS",
                    "ticker": "PBT.OL",
                    "market_cap_usd_m": dp(280.0),
                    "ev_ebitda_x": dp(11.0),
                    "key_differentiator_vs_target": "Smaller, distribution only; no processing.",
                },
            ],
            "relative_positioning_comment": "Target trades at 10x vs 12.2x peer median — discount not justified by quality given sole-source position.",
        },
        "governance": {
            "ownership_structure": "Founder family holds 35%; rest free float.",
            "key_holders": [
                {"holder": "Founder Family", "pct": dp(35.0), "type": "founder"},
                {"holder": "Institutional Fund A", "pct": dp(12.0), "type": "institutional"},
            ],
            "management_track_record": "CEO 12-year tenure; delivered consistent EBITDA improvement.",
            "insider_activity": dp("net_buying_0.8m_usd"),
            "executive_remuneration_comment": "Pay linked to EBITDA and ROCE targets; no excessive dilution.",
            "workforce_signals": "Stable headcount; no labour disputes.",
            "related_party_or_governance_flags": [],
        },
        "catalysts_risks": {
            "catalysts": [
                {
                    "catalyst": "Major utility contract win (H2 2026)",
                    "expected_window": "H2 2026",
                    "probability": "medium",
                    "impact": "high",
                }
            ],
            "risks": [
                {
                    "risk": "Customer concentration: top customer ~42% of revenue",
                    "type": "customer_concentration",
                    "severity": "high",
                    "mitigant": "Multi-year contract in place; renewal discussions underway.",
                },
                {
                    "risk": "Commodity input cost (electrical steel) rises sharply",
                    "type": "commodity_price",
                    "severity": "medium",
                    "mitigant": "Pass-through clauses in 60% of contracts.",
                },
            ],
            "tariff_trade_exposure": "Minimal direct tariff exposure; sells within EU; inputs sourced from EU/UK.",
            "acquisitions_divestments": "No active M&A; potential acquisition target for a European industrial conglomerate.",
        },
        "scoring": {
            "pillars": {
                "asset_quality": _make_minimal_score_item(4),
                "balance_sheet_resilience": _make_minimal_score_item(4),
                "valuation_gap": _make_minimal_score_item(4),
                "moat_competitive": _make_minimal_score_item(4),
                "macro_geo_tailwind": _make_minimal_score_item(4),
                "catalyst_proximity": _make_minimal_score_item(3),
                "management_governance": _make_minimal_score_item(4),
                "underresearched_edge": _make_minimal_score_item(4),
            },
            "composite_score": 3.875,
            "score_to_conviction_mapping": ">=3.5 & no unmitigated high-severity kill-flag => WATCHLIST/SHORTLIST.",
        },
        "verdict": {
            "recommendation": "WATCHLIST",
            "override_reason": None,
            "watchlist_triggers": [
                "Utility contract win announced.",
                "Price falls below USD 34 (>20% below bear case).",
            ],
            "missing_information": [
                "Latest 10-Q/quarterly report not yet available.",
                "Independent valuation of replacement cost.",
            ],
            "position_sizing_note": "ADV ~USD 180k; a USD 1m position would be ~5.5 days of volume — viable for small allocation.",
        },
        "self_critique": {
            "strongest_bear_case": (
                "Customer concentration is the primary kill risk: if the top utility (42% of revenue) "
                "delays or cancels its transformer capex programme, revenue drops sharply and the "
                "EBITDA margin thesis collapses. The company has limited pricing power against a "
                "large utility offtaker, and the sole-source position does not protect against "
                "demand destruction — only against a new entrant. The valuation would re-rate "
                "sharply downward in this scenario as the growth premium disappears."
            ),
            "weakest_links_in_thesis": [
                "Customer concentration (42%) is the single biggest risk.",
                "Replacement-cost estimate is agent-derived (T6, quality C).",
            ],
            "data_quality_warnings": [
                "estimated_replacement_value_usd_m is T6 quality C — agent model.",
            ],
            "uncited_claim_scan_passed": True,
        },
    }


# ---------------------------------------------------------------------------
# Test 1: example_report_filled.json validates against the schema
# ---------------------------------------------------------------------------


def test_example_report_validates() -> None:
    """The bundled fictional example must validate against report_schema.json."""
    example = _load_example()
    result = validate_real_asset_report(example)
    assert result.is_valid, "Example report failed validation:\n" + "\n".join(result.errors)


def test_example_report_returns_validation_result() -> None:
    example = _load_example()
    result = validate_real_asset_report(example)
    assert isinstance(result, ValidationResult)
    assert isinstance(result.is_valid, bool)
    assert isinstance(result.errors, list)
    assert isinstance(result.warnings, list)


# ---------------------------------------------------------------------------
# Test 2: minimal valid report passes
# ---------------------------------------------------------------------------


def test_minimal_valid_report_passes() -> None:
    report = _make_minimal_valid_report()
    result = validate_real_asset_report(report)
    assert result.is_valid, "Minimal valid report should pass:\n" + "\n".join(result.errors)
    assert result.errors == []


# ---------------------------------------------------------------------------
# Test 3: malformed report (missing required top-level section) fails
# ---------------------------------------------------------------------------


def test_missing_required_section_fails() -> None:
    report = _make_minimal_valid_report()
    del report["thesis"]  # required top-level field
    result = validate_real_asset_report(report)
    assert not result.is_valid
    assert any("thesis" in e for e in result.errors)


def test_missing_report_meta_fails() -> None:
    report = _make_minimal_valid_report()
    del report["report_meta"]
    result = validate_real_asset_report(report)
    assert not result.is_valid
    assert any("report_meta" in e for e in result.errors)


def test_wrong_conviction_value_fails() -> None:
    report = _make_minimal_valid_report()
    report["report_meta"]["conviction"] = "STRONG_BUY"  # not in enum
    result = validate_real_asset_report(report)
    assert not result.is_valid


def test_invalid_schema_version_fails() -> None:
    report = _make_minimal_valid_report()
    report["report_meta"]["schema_version"] = "2.0.0"  # const must be "1.0.0"
    result = validate_real_asset_report(report)
    assert not result.is_valid


def test_empty_theme_tags_fails() -> None:
    report = _make_minimal_valid_report()
    report["report_meta"]["theme_tags"] = []  # minItems: 1
    result = validate_real_asset_report(report)
    assert not result.is_valid


def test_short_thesis_paragraph_fails() -> None:
    report = _make_minimal_valid_report()
    report["thesis"]["one_paragraph_thesis"] = "Too short."  # minLength: 200
    result = validate_real_asset_report(report)
    assert not result.is_valid


def test_score_out_of_range_fails() -> None:
    report = _make_minimal_valid_report()
    report["scoring"]["pillars"]["asset_quality"]["score"] = 6  # maximum: 5
    result = validate_real_asset_report(report)
    assert not result.is_valid


def test_additional_top_level_property_fails() -> None:
    report = _make_minimal_valid_report()
    report["unsupported_field"] = "extra"  # additionalProperties: false
    result = validate_real_asset_report(report)
    assert not result.is_valid


# ---------------------------------------------------------------------------
# Test 4: bare (unsourced) financial number fails validation
# ---------------------------------------------------------------------------


def test_bare_number_in_snapshot_financials_fails() -> None:
    """A bare number where a datapoint is required must fail schema validation."""
    report = _make_minimal_valid_report()
    # Replace a required datapoint field with a raw number
    report["snapshot_financials"]["market_cap_usd_m"] = 320.0  # should be a datapoint
    result = validate_real_asset_report(report)
    assert not result.is_valid, "Bare number in snapshot_financials.market_cap_usd_m must fail"


def test_bare_string_in_identity_fails() -> None:
    """A bare string where a datapoint object is required must fail."""
    report = _make_minimal_valid_report()
    report["identity"]["ticker"] = "TSTC"  # should be a datapoint object
    result = validate_real_asset_report(report)
    assert not result.is_valid


def test_datapoint_missing_required_source_tier_fails() -> None:
    """A datapoint without source_tier must fail."""
    report = _make_minimal_valid_report()
    dp = report["snapshot_financials"]["market_cap_usd_m"]
    del dp["source_tier"]  # required field in datapoint
    result = validate_real_asset_report(report)
    assert not result.is_valid


def test_datapoint_missing_as_of_fails() -> None:
    """A datapoint without as_of must fail."""
    report = _make_minimal_valid_report()
    dp = report["snapshot_financials"]["price"]
    del dp["as_of"]
    result = validate_real_asset_report(report)
    assert not result.is_valid


def test_datapoint_with_invalid_quality_flag_fails() -> None:
    """data_quality must be one of the four allowed enum values."""
    report = _make_minimal_valid_report()
    report["snapshot_financials"]["price"]["data_quality"] = "E_invented"
    result = validate_real_asset_report(report)
    assert not result.is_valid


def test_datapoint_with_invalid_source_tier_fails() -> None:
    """source_tier must be one of the six allowed enum values."""
    report = _make_minimal_valid_report()
    report["snapshot_financials"]["price"]["source_tier"] = "T7_social_media"
    result = validate_real_asset_report(report)
    assert not result.is_valid


# ---------------------------------------------------------------------------
# Test 5: data-quality warnings surface D_weak_or_stale in critical sections
# ---------------------------------------------------------------------------


def test_d_quality_in_snapshot_financials_produces_warning() -> None:
    report = _make_minimal_valid_report()
    report["snapshot_financials"]["market_cap_usd_m"]["data_quality"] = "D_weak_or_stale"
    result = validate_real_asset_report(report)
    assert result.is_valid, "D quality alone should not fail structural validation"
    assert any("D_weak_or_stale" in w for w in result.warnings)


def test_d_quality_warning_includes_field_path() -> None:
    report = _make_minimal_valid_report()
    report["snapshot_financials"]["ev_ebitda_x"]["data_quality"] = "D_weak_or_stale"
    result = validate_real_asset_report(report)
    assert any("ev_ebitda_x" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Test 6: ValidationResult.to_dict()
# ---------------------------------------------------------------------------


def test_to_dict_shape() -> None:
    report = _make_minimal_valid_report()
    result = validate_real_asset_report(report)
    d = result.to_dict()
    assert set(d.keys()) == {"is_valid", "errors", "warnings"}
    assert isinstance(d["is_valid"], bool)
    assert isinstance(d["errors"], list)
    assert isinstance(d["warnings"], list)
