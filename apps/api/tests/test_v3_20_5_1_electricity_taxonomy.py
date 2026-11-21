"""V3.20 slice 5.1 — the electricity taxonomy as versioned configuration."""

from __future__ import annotations

import json
import pathlib

from app.services.electricity import taxonomy as tx
from app.services.final_report_generator import run_safety_gate
from app.services.real_asset_report_completer import build_schema_complete_report
from app.services.report_validation_service import validate_real_asset_report
from app.services.safety_terms import scan_value

SCHEMA = (pathlib.Path(__file__).resolve().parents[3] / "packages" / "research-contracts"
          / "real_asset_equity" / "v1" / "report_schema.json")


def _schema_theme_enum() -> set[str]:
    schema = json.loads(SCHEMA.read_text())
    meta = schema["properties"]["report_meta"] if "report_meta" in schema.get(
        "properties", {}) else schema["$defs"]["report_meta"]
    return set(meta["properties"]["theme_tags"]["items"]["enum"])


def test_nine_layers_with_unique_keys_and_schema_tags() -> None:
    assert tx.LAYER_KEYS == (
        "generation_fuel", "transmission", "distribution_grid_edge",
        "sensing_protection_digital", "storage", "power_electronics",
        "demand_side_decentralised", "utilities_grid_services",
        "upstream_electrical_materials",
    )
    enum = _schema_theme_enum()
    sub_keys: list[str] = []
    for lay in tx.LAYERS:
        assert lay.sub_segments and lay.theme_tags
        assert set(lay.theme_tags) <= enum, lay.key
        for sub in lay.sub_segments:
            assert sub.anchors, sub.key
            assert set(sub.theme_tags) <= enum, sub.key
            sub_keys.append(sub.key)
    assert len(sub_keys) == len(set(sub_keys))
    assert tx.UNCLASSIFIED_THEME_TAG in enum
    assert {r.key for r in tx.EXCLUSIONS} == {"general_mining", "oil_gas_upstream_refining",
                                              "coal"}


def test_anchors_are_whole_words_plural_aware_and_acronyms_case_sensitive() -> None:
    text = ("We manufacture power transformers, HVDC converter stations and "
            "medium voltage switchgear, and sell UPS systems.")
    hits = {(h.layer, h.sub_segment) for h in tx.match_layers(text)}
    assert ("transmission", "power_transformers") in hits
    assert ("transmission", "hvdc") in hits
    assert ("distribution_grid_edge", "mv_lv_switchgear") in hits  # space ≈ hyphen
    assert ("power_electronics", "ups_power_quality") in hits
    # "ups" in prose is not an uninterruptible power supply; "transformation" is not
    # a transformer; "inverters" inside another word is not matched.
    prose = "Market ups and downs drove a digital transformation of our reinverterless line."
    assert tx.match_layers(prose) == []
    hit = next(h for h in tx.match_layers(text) if h.sub_segment == "power_transformers")
    assert text[hit.start:hit.end] == hit.quote == "power transformers"


def test_exclusions_and_the_dominant_end_market_override() -> None:
    found = tx.match_exclusions("A gold mining company with a thermal coal mine.")
    assert [r.key for r, _ in found] == ["general_mining", "coal"]
    # A copper refiner is not an oil refiner.
    assert tx.match_exclusions("We operate a copper refinery and a rod mill.") == []
    assert tx.dominant_end_market_passes(0.5) is True
    assert tx.dominant_end_market_passes(0.49) is False
    assert tx.dominant_end_market_passes(None) is False  # not disclosed ≠ passes


def test_theme_tags_order_dedup_and_fallback() -> None:
    assert tx.theme_tags_for(["transmission", "distribution_grid_edge"],
                             ["hv_cables", "mv_lv_switchgear"]) == [
        "grid_transmission", "grid_distribution", "grid_switchgear_cables"]
    assert tx.theme_tags_for(["nope"]) == [tx.UNCLASSIFIED_THEME_TAG]
    assert tx.theme_tags_for([]) == [tx.UNCLASSIFIED_THEME_TAG]


def test_completer_uses_the_classification_and_stays_schema_and_safety_valid() -> None:
    admin = {"executive_summary": {"value": "Internal draft."},
             "electricity_classification": {
                 "primary_layer": "transmission",
                 "layers": ["distribution_grid_edge", "transmission", "not_a_layer"],
                 "sub_segments": ["power_transformers", "mv_lv_switchgear"]}}
    comp = build_schema_complete_report(admin, report_id="r-5-1")
    meta = comp.report["report_meta"]
    assert meta["theme_tags"] == ["grid_transmission", "grid_distribution",
                                  "grid_switchgear_cables"]
    assert "primary layer: transmission" in meta["candidate_emerged_from"]
    assert tx.TAXONOMY_VERSION in meta["candidate_emerged_from"]
    assert validate_real_asset_report(comp.report).is_valid is True
    assert run_safety_gate(comp.report).passed is True

    bare = build_schema_complete_report({"executive_summary": {"value": "x"}},
                                        report_id="r-5-1b")
    assert bare.report["report_meta"]["theme_tags"] == [tx.UNCLASSIFIED_THEME_TAG]
    assert "structural stand-ins" in bare.report["report_meta"]["candidate_emerged_from"]


def test_taxonomy_text_passes_the_safety_scan() -> None:
    assert scan_value(tx.describe()) == []
    for lay in tx.LAYERS:
        for sub in lay.sub_segments:
            assert scan_value(list(sub.anchors)) == [], sub.key
