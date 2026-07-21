"""
Phase 26 — Final Report Schema Completion / Publication-Readiness.

The Phase 16 admin draft (executive_summary / financial_snapshot / …) does not
match the strict ``report_schema.json`` shape, so generated reports were stuck at
``schema_valid=False``. Phase 26 adds a deterministic completion layer that maps
the draft into the strict shape, filling genuinely-absent fields with honest
``not_sourced`` stand-ins (never fabricated data). A free-provider report now
reaches ``schema_valid=True`` while remaining ``research_complete=False``,
``publication_ready=False``, and ``human_review_required=True``.

These tests assert:
  1.  an AAPL-style free_real draft completes to schema_valid=True
  2.  missing fields become structured stand-ins, not fabricated numbers
  3.  safety_valid stays True (no banned recommendation/valuation language)
  4.  human_review_required stays True
  5.  publication_ready stays False
  6.  the valuation section carries no price target / fair value / intrinsic
      value / upside / downside
  7.  no BUY/SELL/HOLD/WATCH recommendation label is produced (verdict = PASS)
  8.  missing peers → peers_not_sourced stand-in
  9.  missing governance → not_sourced stand-in
  10. self_critique is always present
  11. catalyst data stays model-derived and human-review-required
  12. the validator distinguishes schema_valid from research_complete
"""

from __future__ import annotations

import json
import re

import pytest

from app.services import safety_terms
from app.services.final_report_generator import (
    run_final_report_validation,
    run_safety_gate,
)
from app.services.real_asset_report_completer import build_schema_complete_report
from app.services.report_validation_service import validate_real_asset_report

# Recommendation/valuation language that must never be produced (mirrors the app
# safety gate substrings plus bare upside/downside/valued for defence in depth).
_BANNED = re.compile(
    r"(?i)\b(BUY|SELL|HOLD|WATCH)\b|price target|target price|fair value|"
    r"intrinsic value|upside|downside|under\s?valued|over\s?valued"
)


def _free_real_admin() -> dict:
    """An AAPL-style free_real admin draft: some sourced numbers, not mock."""
    return {
        "executive_summary": {"company_name": "Apple Inc.", "ticker": "AAPL"},
        "company_identity": {
            "legal_name": {"value": "Apple Inc."},
            "ticker": {"value": "AAPL"},
            "exchange": {"value": "NASDAQ"},
            "country_domicile": {"value": "USA"},
            "reporting_currency": {"value": "USD"},
            "source_tier": "T5_api_aggregator",
            "is_mock": False,
        },
        "financial_snapshot": {
            "source_tier": "T5_api_aggregator",
            "is_mock": False,
            "latest_close": {"value": 195.3, "currency": "USD", "as_of": "2026-07-10"},
            "market_cap_usd_m": {"value": 3010000.0},
            "revenue_ttm_usd_m": {"value": 383000.0},
            "ebitda_ttm_usd_m": {"value": None},  # genuinely absent
        },
        "risk_analysis": {
            "business_risks": {"value": ["Concentration in a flagship product line."]},
            "financial_risks": {"value": ["FX translation exposure."]},
        },
        "news_catalyst_discovery": {
            "recent_events": {
                "value": [
                    {
                        "catalyst_category": "guidance",
                        "catalyst_direction": "positive",
                        "event_date": "2026-06-01",
                    }
                ]
            },
            "sec_filing_events": {"value": []},
        },
        "missing_information": {
            "missing_financial_fields": ["EBITDA", "enterprise value"],
        },
    }


def _minimal_admin() -> dict:
    return {"executive_summary": {"value": "Internal draft."}}


# ---------------------------------------------------------------------------
# 1. schema_valid=True for a free_real draft
# ---------------------------------------------------------------------------


def test_free_real_draft_completes_to_schema_valid() -> None:
    comp = build_schema_complete_report(_free_real_admin(), report_id="r-1")
    result = validate_real_asset_report(comp.report)
    assert result.is_valid is True, result.errors
    # Every strict-schema top-level section is present.
    for section in (
        "report_meta",
        "identity",
        "discovery_profile",
        "snapshot_financials",
        "thesis",
        "business",
        "real_asset_block",
        "financials_deep",
        "valuation",
        "peers",
        "governance",
        "catalysts_risks",
        "scoring",
        "verdict",
        "self_critique",
    ):
        assert section in comp.report


# ---------------------------------------------------------------------------
# 2. missing fields become structured stand-ins, not fabricated data
# ---------------------------------------------------------------------------


def test_missing_fields_are_null_standins_not_fabricated() -> None:
    comp = build_schema_complete_report(_free_real_admin(), report_id="r-2")
    snap = comp.report["snapshot_financials"]

    # A sourced number is carried through (quality C, no data-quality warning).
    assert snap["market_cap_usd_m"]["value"] == 3010000.0
    assert snap["market_cap_usd_m"]["data_quality"] == "C_inferred"

    # Genuinely-absent numbers are NEVER fabricated — null + quality D stand-ins.
    for absent in ("enterprise_value_usd_m", "ev_ebitda_x", "ebitda_ttm_usd_m"):
        dp = snap[absent]
        assert dp["value"] is None
        assert dp["data_quality"] == "D_weak_or_stale"
        assert "not_sourced" in dp["source_name"]

    assert comp.placeholder_fields  # incompleteness is tracked, not hidden


def test_mock_numbers_are_not_presented_as_sourced() -> None:
    admin = _free_real_admin()
    admin["financial_snapshot"]["is_mock"] = True
    admin["company_identity"]["is_mock"] = True
    comp = build_schema_complete_report(admin, report_id="r-2b")
    # A mock market cap must not be carried through as if sourced.
    assert comp.report["snapshot_financials"]["market_cap_usd_m"]["value"] is None


# ---------------------------------------------------------------------------
# 3-5. safety_valid / human_review_required / publication_ready
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("admin", [_free_real_admin(), _minimal_admin()])
def test_completed_report_passes_safety_gate(admin: dict) -> None:
    comp = build_schema_complete_report(admin, report_id="r-3")
    safety = run_safety_gate(comp.report)
    assert safety.passed is True, safety.forbidden_terms_found


def test_validation_keeps_human_review_and_no_publication() -> None:
    validation = run_final_report_validation(
        _free_real_admin(), report_id="r-4", generated_at=None
    )
    assert validation.safety_valid is True
    assert validation.schema_valid is True
    assert validation.publication_ready is False
    assert validation.persisted_schema_json["human_review_required"] is True
    assert validation.persisted_schema_json["publication_ready"] is False


# ---------------------------------------------------------------------------
# 6. valuation section has no price target / fair value / upside / downside
# ---------------------------------------------------------------------------


def test_valuation_has_no_recommendation_or_valuation_conclusion() -> None:
    comp = build_schema_complete_report(_free_real_admin(), report_id="r-6")
    valuation = comp.report["valuation"]
    # The (schema-required) valuation-change datapoint is deliberately null.
    assert valuation["upside_downside_pct"]["value"] is None
    assert "fair_value_per_share" not in valuation  # never emitted
    # No banned valuation language anywhere in the section's string VALUES.
    blob = json.dumps({k: v for k, v in valuation.items() if k != "upside_downside_pct"})
    assert _BANNED.search(blob) is None, blob
    # And the required key itself is the only place upside/downside appears.
    assert "upside" not in json.dumps(valuation["upside_downside_pct"]).lower()


# ---------------------------------------------------------------------------
# 7. no BUY/SELL/HOLD/WATCH recommendation is produced
# ---------------------------------------------------------------------------


def test_no_recommendation_label_produced() -> None:
    comp = build_schema_complete_report(_free_real_admin(), report_id="r-7")
    assert comp.report["verdict"]["recommendation"] == "PASS"
    assert comp.report["report_meta"]["conviction"] == "PASS"

    # No forbidden language appears in any scanned string VALUE (keys such as
    # sell_side_estimate_count / watchlist_triggers are schema keys, never
    # scanned). Uses the shared scanner so this test cannot drift from the
    # gates it is standing in for.
    assert safety_terms.scan_value(comp.report) == []


# ---------------------------------------------------------------------------
# 8. peers missing data → peers_not_sourced stand-in
# ---------------------------------------------------------------------------


def test_peers_missing_creates_not_sourced_placeholder() -> None:
    comp = build_schema_complete_report(_free_real_admin(), report_id="r-8")
    peers = comp.report["peers"]
    assert "peers_not_sourced" in peers["peer_construction_logic"]
    # peer_table has minItems 2 — two clearly-marked, non-fabricated stand-in rows.
    assert len(peers["peer_table"]) == 2
    assert all(row["ticker"] == "NOT_SOURCED" for row in peers["peer_table"])


# ---------------------------------------------------------------------------
# 9. governance missing data → not_sourced stand-in
# ---------------------------------------------------------------------------


def test_governance_missing_creates_not_sourced_placeholder() -> None:
    comp = build_schema_complete_report(_free_real_admin(), report_id="r-9")
    gov = comp.report["governance"]
    assert "not_sourced" in gov["ownership_structure"]
    assert "not_sourced" in gov["management_track_record"]
    assert gov["insider_activity"]["value"] is None


# ---------------------------------------------------------------------------
# 10. self_critique is always present
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("admin", [_free_real_admin(), _minimal_admin(), {}])
def test_self_critique_always_present(admin: dict) -> None:
    comp = build_schema_complete_report(admin, report_id="r-10")
    critique = comp.report["self_critique"]
    assert len(critique["strongest_bear_case"]) >= 150
    assert critique["weakest_links_in_thesis"]
    assert "publication-ready" in critique["confirmation_bias_check"].lower() or \
        "publication" in critique["confirmation_bias_check"].lower()
    # data_quality_warnings auto-lists the not_sourced fields (honest incompleteness).
    assert critique["data_quality_warnings"]


# ---------------------------------------------------------------------------
# 11. catalyst data stays model-derived and human-review-required
# ---------------------------------------------------------------------------


def test_catalysts_are_model_derived_and_review_required() -> None:
    comp = build_schema_complete_report(_free_real_admin(), report_id="r-11")
    catalysts = comp.report["catalysts_risks"]["catalysts"]
    assert len(catalysts) >= 1
    assert any("MODEL-DERIVED" in c["catalyst"] for c in catalysts)
    for c in catalysts:
        assert c["probability"] in {"high", "medium", "low"}
        assert c["impact"] in {"high", "medium", "low"}
    # Human review stays required for the completed report.
    validation = run_final_report_validation(
        _free_real_admin(), report_id="r-11b", generated_at=None
    )
    assert validation.persisted_schema_json["human_review_required"] is True


def test_no_catalyst_data_still_yields_minimum_one_entry() -> None:
    comp = build_schema_complete_report(_minimal_admin(), report_id="r-11c")
    catalysts = comp.report["catalysts_risks"]["catalysts"]
    assert len(catalysts) == 1
    assert "not_available" in catalysts[0]["catalyst"]


# ---------------------------------------------------------------------------
# 12. validator distinguishes schema_valid from research_complete
# ---------------------------------------------------------------------------


def test_schema_valid_is_not_research_complete() -> None:
    comp = build_schema_complete_report(_free_real_admin(), report_id="r-12")
    assert validate_real_asset_report(comp.report).is_valid is True
    assert comp.research_complete is False  # structural != research completeness

    validation = run_final_report_validation(
        _free_real_admin(), report_id="r-12b", generated_at=None
    )
    assert validation.schema_valid is True
    assert validation.research_complete is False
    assert validation.persisted_schema_json["placeholder_field_count"] > 0
