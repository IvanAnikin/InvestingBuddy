"""V3.20 slice 5.2 — electricity layer classifier with cited evidence."""

from __future__ import annotations

import pathlib
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from app.services.electricity.classifier import (
    BASIS_SEGMENT_REVENUE,
    BASIS_TEXT_ANCHORS,
    STATUS_CLASSIFIED,
    STATUS_EXCLUDED,
    STATUS_NO_EVIDENCE,
    STATUS_NOT_ELECTRICITY,
    CitedText,
    SegmentRevenue,
    classify,
)
from app.services.pipeline.electricity_step import electricity_layers_section
from app.services.safety_terms import scan_value
from tests import test_v3_entity_relationships as _base

session = _base.session

POWL_LIKE = (
    "The Company designs and manufactures custom-engineered equipment for the "
    "distribution, control and monitoring of electrical energy, including "
    "medium-voltage switchgear, low-voltage switchgear, protective relays, and "
    "power distribution transformers for utilities, data centers and industrial users."
)


def _seg(name: str, value: int, period: str = "FY2025", **kw: Any) -> SegmentRevenue:
    return SegmentRevenue(name=name, value=Decimal(value), currency=kw.get("currency", "USD"),
                          period=period, scale="million", doc_date=kw.get("doc_date"),
                          source_url="https://www.sec.gov/x.htm", fact_id=kw.get("fid"))


def test_text_only_classification_needs_two_anchors_and_cites_them() -> None:
    out = classify([CitedText(POWL_LIKE, "company_profile", "https://powellind.com",
                              "2026-11-01")])
    assert out["status"] == STATUS_CLASSIFIED
    assert out["primary_layer"] == "distribution_grid_edge"
    assert out["primary_basis"] == BASIS_TEXT_ANCHORS
    dist = out["layers"][0]
    assert dist["text_anchor_count"] >= 2
    assert "mv_lv_switchgear" in dist["sub_segments"]
    ev = dist["evidence"][0]
    assert ev["matched"].lower() in ev["quote"].lower()
    assert ev["source_url"] == "https://powellind.com" and ev["as_of"] == "2026-11-01"
    # "protective relays" is one anchor of its layer — not enough on its own.
    assert "sensing_protection_digital" not in {x["layer"] for x in out["layers"]}
    assert out["revenue_by_layer"] is None
    assert "grid_distribution" in out["theme_tags"]


def test_segment_revenue_sets_primary_layer_and_shares() -> None:
    segs = [
        _seg("Transformers", 600),
        _seg("Switchgear", 300),
        _seg("Services", 100),
        _seg("United States (geographic)", 700),  # geographic never maps or counts
        _seg("Transformers", 500, period="FY2024"),  # older period ignored
        _seg("Transformers", 999, fid="dup"),  # second reading of the same segment
    ]
    out = classify([CitedText("Industrial equipment maker.", "company_profile")], segs)
    assert out["status"] == STATUS_CLASSIFIED and out["primary_basis"] == BASIS_SEGMENT_REVENUE
    rev = out["revenue_by_layer"]
    assert rev["axis"] == "business_segment" and rev["period"] == "FY2025"
    # 600 + 300 + 100; the geographic row, the FY2024 row and the duplicate do not count.
    assert rev["total_revenue"] == 1000.0
    # Bare "Transformers" and "Switchgear" both read as distribution / grid edge.
    assert rev["shares"] == {"distribution_grid_edge": 0.9}
    assert out["primary_layer"] == "distribution_grid_edge"
    assert [r["layer"] for r in rev["segments"]] == ["distribution_grid_edge",
                                                     "distribution_grid_edge", None]
    for row in rev["segments"]:
        assert row["source_url"]


def test_named_segments_map_to_layers() -> None:
    segs = [_seg("Power Transformers", 600), _seg("Medium Voltage Switchgear", 300),
            _seg("Other", 100)]
    out = classify([], segs)
    assert out["primary_layer"] == "transmission"
    assert out["revenue_by_layer"]["shares"] == {"transmission": 0.6,
                                                 "distribution_grid_edge": 0.3}
    assert out["revenue_by_layer"]["mapped_share"] == 0.9
    assert out["layers"][0]["segments"] == ["Power Transformers"]
    assert out["layers"][0]["revenue_share"] == 0.6


def test_exclusion_holds_without_a_disclosed_power_share() -> None:
    text = ("A gold mining company that also supplies copper wire and magnet wire to "
            "motor makers.")
    out = classify([CitedText(text, "company_profile")])
    assert out["status"] == STATUS_EXCLUDED
    assert out["exclusion"]["rule"] == "general_mining"
    assert out["exclusion"]["override_passed"] is False
    assert out["layers"] == [] and out["primary_layer"] is None
    assert out["candidate_layers"][0]["layer"] == "upstream_electrical_materials"


def test_exclusion_overridden_by_dominant_power_revenue() -> None:
    text = "A coal mining group with a large power generation business."
    segs = [_seg("Power Generation", 700), _seg("Coal Mining", 300)]
    out = classify([CitedText(text, "company_profile")], segs)
    assert out["exclusion"]["rule"] == "coal"
    assert out["exclusion"]["power_share"] == 0.7
    assert out["exclusion"]["override_passed"] is True
    assert out["status"] == STATUS_CLASSIFIED and out["primary_layer"] == "generation_fuel"


def test_not_electricity_and_no_evidence() -> None:
    assert classify([CitedText("We sell handbags.", "company_profile")])["status"] == \
        STATUS_NOT_ELECTRICITY
    assert classify([])["status"] == STATUS_NO_EVIDENCE


async def test_step_reads_description_and_segment_facts(session) -> None:  # noqa: ANN001
    from app.models.company import Company
    from app.models.extracted_document import ExtractedDocument, ExtractedFact

    company = Company(id=uuid.uuid4(), ticker="EXG", exchange="NASDAQ", name="Example Grid",
                      description=POWL_LIKE + " Our upside is a price target.",
                      website="https://example.com")
    session.add(company)
    await session.flush()
    doc = ExtractedDocument(
        id=uuid.uuid4(), content_hash=uuid.uuid4().hex * 2,
        canonical_url="https://www.sec.gov/Archives/x/10k.htm", provider="sec_edgar",
        source_type="annual_report", source_tier="T1_primary_filing", mime_type="text/html",
        extraction_method="inline_xbrl", status="extracted", doc_date=date(2025, 11, 20),
        retrieved_at=datetime.now(timezone.utc), excerpts_json=[], company_id=company.id)
    session.add(doc)
    await session.flush()
    for name, value, active in (("Power Transformers", 600, True),
                                ("Medium Voltage Switchgear", 400, True),
                                ("Stale Segment", 9000, False)):
        session.add(ExtractedFact(
            id=uuid.uuid4(), extracted_document_id=doc.id, label="revenue",
            value_numeric=value, currency="USD", scale="million", period="FY2025",
            extraction_method="inline_xbrl", confidence=0.9, validation_status="validated",
            needs_human_review=False, is_active=active, scope_type="segment",
            scope_name=name, scope_key=f"segment:{name.casefold()}"))
    await session.flush()

    off = await electricity_layers_section(
        session, company, cfg=SimpleNamespace(v3_electricity_classifier_enabled=False))
    assert off is None
    block = await electricity_layers_section(
        session, company, cfg=SimpleNamespace(v3_electricity_classifier_enabled=True))
    assert block is not None and block["status"] == STATUS_CLASSIFIED
    assert block["primary_layer"] == "transmission"
    assert block["revenue_by_layer"]["total_revenue"] == 1000.0
    assert block["revenue_by_layer"]["segments"][0]["source_url"].startswith("https://www.sec")
    assert scan_value(block) == []  # issuer prose neutralised


def test_report_attaches_block_and_web_reader_pins_keys() -> None:
    import inspect

    from app.services.pipeline import professional_research as pr

    assert "electricity_layers" in inspect.signature(pr.ReportInputs).parameters
    reader = (pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "components"
              / "research" / "electricityLayers.ts").read_text()
    for key in ("status", "primary_layer", "primary_basis", "layers", "candidate_layers",
                "revenue_by_layer", "exclusion", "taxonomy_version", "display_name",
                "sub_segments", "revenue_share", "evidence", "quote", "source_url",
                "shares", "mapped_share", "period", "currency", "axis", "rule",
                "override_passed", "power_share", "note"):
        assert f"raw.{key}" in reader or f"r.{key}" in reader, key
