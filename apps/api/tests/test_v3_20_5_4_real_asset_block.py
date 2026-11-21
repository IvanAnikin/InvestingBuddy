"""V3.20 slice 5.4 — the electricity real-asset block, from cited findings only."""

from __future__ import annotations

import pathlib
import uuid
from types import SimpleNamespace

from app.services.electricity.real_asset import (
    NOT_DISCLOSED,
    capacity_readings,
    certifications_in,
    real_asset_block,
)
from app.services.real_asset_report_completer import build_schema_complete_report
from app.services.report_validation_service import validate_real_asset_report
from app.services.research_fields import _CAPACITY_RE
from app.services.safety_terms import scan_value

CLASSIFIED = {"status": "classified", "primary_layer": "transmission"}


def _f(statement: str, **kw: object) -> dict[str, object]:
    return {"finding_id": kw.get("fid", str(uuid.uuid4())), "statement": statement,
            "evidence_ids": kw.get("evidence_ids", ["ev-1"]),
            "verification_status": kw.get("status", "verified"),
            "superseded_by": kw.get("superseded_by"),
            "source_published_at": kw.get("as_of", "2026-03-01")}


def test_natural_units_and_no_bare_distance() -> None:
    units = {(r["unit"], r["unit_family"]) for t in (
        "Annual capacity of 40 GVA of power transformers.",
        "1,200 km of high-voltage cable and 250,000 tonnes per year of copper rod.",
        "120 MVAr of shunt reactors; 2.5 GWh of storage; 300 MW of solar.",
        "4,000 units per year and 30 ktpa of electrical steel.",
    ) for r in capacity_readings(t)}
    assert units == {
        ("GVA", "apparent_power"), ("km", "length"), ("tonnes per year", "mass_per_year"),
        ("MVAr", "apparent_power"), ("GWh", "energy"), ("MW", "power"),
        ("units per year", "units_per_year"), ("ktpa", "mass_per_year"),
    }
    assert capacity_readings("The plant is located 50 km from Houston, built in 2019.") == []


def test_certificates_carry_body_standard_and_date_or_say_not_disclosed() -> None:
    [c] = certifications_in(
        "The 420 kV transformer passed the KEMA short-circuit test (IEC 60076-5) in "
        "March 2025.")
    assert (c["body"], c["standard"], c["date"]) == ("KEMA", "IEC 60076-5", "March 2025")
    [u] = certifications_in("The company is an approved vendor to several utilities.")
    assert u["date"] is None and u["date_state"] == NOT_DISCLOSED
    assert certifications_in("Revenue grew 12%.") == []


def test_block_reads_only_usable_findings_and_marks_gaps_not_disclosed() -> None:
    findings = [
        _f("The Waukesha plant has capacity of 15 GVA per year.", fid="cap"),
        _f("A new factory will add 10 GVA of capacity by 2027.", fid="exp"),
        _f("Capacity utilisation was 92% in 2025.", fid="util"),
        _f("Old capacity was 5 GVA.", superseded_by="cap"),
        _f("Capacity of 99 GVA.", status="withdrawn"),
        _f("Capacity of 77 GVA.", evidence_ids=[]),  # uncited
    ]
    block = real_asset_block(findings, CLASSIFIED)
    assert block is not None and block["asset_type"] == "transmission_distribution"
    assert [(r["value"], r["finding_id"]) for r in block["capacity"]["readings"]] == [
        (15.0, "cap")]
    assert [r["finding_id"] for r in block["expansions"]["readings"]] == ["exp"]
    assert block["utilisation"]["readings"][0]["value"] == 92.0
    assert block["certifications"]["state"] == NOT_DISCLOSED
    assert block["replacement_value"]["state"] == NOT_DISCLOSED
    assert block["capacity"]["readings"][0]["evidence_ids"] == ["ev-1"]
    assert scan_value(block) == []
    # Not in the vertical → no block at all.
    assert real_asset_block(findings, {"status": "excluded"}) is None
    assert real_asset_block(findings, None) is None


def test_generic_capacity_regex_now_reads_electrical_units() -> None:
    for text in ("40 gva of transformers", "1,200 km of high-voltage cable", "120 mvar"):
        assert _CAPACITY_RE.search(text), text
    assert not _CAPACITY_RE.search("located 50 km from houston")


def test_completer_asset_type_follows_the_primary_layer() -> None:
    admin = {"executive_summary": {"value": "x"},
             "electricity_classification": {"primary_layer": "upstream_electrical_materials",
                                            "layers": ["upstream_electrical_materials"]}}
    comp = build_schema_complete_report(admin, report_id="r-5-4")
    assert comp.report["real_asset_block"]["asset_type"] == "processing_refining"
    assert validate_real_asset_report(comp.report).is_valid is True
    bare = build_schema_complete_report({"executive_summary": {"value": "x"}}, report_id="b")
    assert bare.report["real_asset_block"]["asset_type"] == "mixed"


async def test_pipeline_section_reads_the_run_findings() -> None:
    from app.services.pipeline import v3_pipeline

    class _Result:
        def __init__(self, rows: list[object]) -> None:
            self._rows = rows

        def scalars(self) -> _Result:
            return self

        def all(self) -> list[object]:
            return self._rows

    class _Nested:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *exc: object) -> None:
            return None

    row = SimpleNamespace(id=uuid.uuid4(), statement="Capacity of 12 GVA per year.",
                          evidence_ids_json=["e1"], verification_status="verified",
                          superseded_by_finding_id=None, source_published_at=None)

    class _Session:
        def begin_nested(self) -> _Nested:
            return _Nested()

        async def execute(self, _stmt: object) -> _Result:
            return _Result([row])

    run = SimpleNamespace(id=uuid.uuid4())
    block = await v3_pipeline._real_asset_section(_Session(), run, CLASSIFIED)
    assert block is not None and block["capacity"]["readings"][0]["value"] == 12.0
    assert await v3_pipeline._real_asset_section(_Session(), run, None) is None


def test_report_attaches_block_and_web_reader_pins_keys() -> None:
    import inspect

    from app.services.pipeline import professional_research as pr

    assert "real_asset" in inspect.signature(pr.ReportInputs).parameters
    reader = (pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "components"
              / "research" / "realAsset.ts").read_text()
    for key in ("asset_type", "primary_layer", "capacity", "expansions", "utilisation",
                "certifications", "replacement_value", "note", "state", "readings",
                "value", "unit", "finding_id", "as_of", "quote", "items", "certification",
                "standard", "body", "date", "statements"):
        assert f"raw.{key}" in reader or f"r.{key}" in reader or f"certs.{key}" in reader \
            or f"repl.{key}" in reader, key
