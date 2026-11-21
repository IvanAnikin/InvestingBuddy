"""V3.20 slice 6.1 — driver/bottleneck catalogue and supply-chain laddering."""

from __future__ import annotations

import uuid
from datetime import date

from app.services.discovery.laddering import distance, ladder, query_plan
from app.services.electricity import drivers as dv
from app.services.electricity import taxonomy as tx
from app.services.real_asset_report_completer import build_schema_complete_report
from app.services.report_validation_service import validate_real_asset_report
from app.services.safety_terms import scan_value
from tests import test_v3_entity_relationships as _base

session = _base.session


def test_catalogue_is_consistent_with_the_taxonomy() -> None:
    keys = [b.key for b in dv.BOTTLENECKS]
    assert len(keys) == len(set(keys)) == 9
    for b in dv.BOTTLENECKS:
        lay = tx.layer(b.layer)
        assert lay is not None, b.key
        assert b.sub_segment is None or lay.sub_segment(b.sub_segment) is not None, b.key
        assert b.upstream, b.key
    assert {d.key for d in dv.DRIVERS} == {
        "data_centre_load", "offshore_wind_connection", "transformer_replacement",
        "interconnection_reform", "capacity_market_tightness"}
    for d in dv.DRIVERS:
        assert d.bottlenecks and all(dv.bottleneck(k) for k in d.bottlenecks), d.key
    assert scan_value([b.why_short for b in dv.BOTTLENECKS]) == []


def test_query_plan_climbs_three_steps() -> None:
    plan = query_plan("offshore_wind_connection", bottleneck_keys=["hvdc_valves"])
    assert plan is not None and plan["catalogue_version"] == dv.CATALOGUE_VERSION
    by_step = {s["step"] for s in plan["steps"]}
    assert by_step == {1, 2, 3}
    targets = [(s["step"], s["target"]) for s in plan["steps"]]
    assert (1, "HVDC converter valves") in targets
    assert (2, "converter transformer") in targets
    assert (3, "grain-oriented electrical steel") in targets
    two = query_plan("offshore_wind_connection", max_steps=2)
    assert two is not None and max(s["step"] for s in two["steps"]) == 2
    assert query_plan("nope") is None


async def _entity(session, name: str):  # noqa: ANN001, ANN202
    from app.models.legal_entity import LegalEntity

    e = LegalEntity(id=uuid.uuid4(), entity_key=f"test:{name}", legal_name=name,
                    normalized_name=name.lower())
    session.add(e)
    await session.flush()
    return e


async def _supplies(session, supplier, customer, **kw) -> None:  # noqa: ANN001
    from app.models.legal_entity import EntityRelationship

    session.add(EntityRelationship(
        id=uuid.uuid4(), subject_entity_id=supplier.id, object_entity_id=customer.id,
        relationship_type="supplier_of", source=kw.get("source", "annual_report"),
        source_url=f"https://example.com/{supplier.legal_name}", confidence=0.9,
        evidence_ref=f"ev:{supplier.legal_name}", effective_to=kw.get("effective_to"),
        as_of=date(2026, 3, 1)))
    await session.flush()


async def test_ladder_walks_upstream_with_cited_paths(session) -> None:  # noqa: ANN001
    seed = await _entity(session, "Utility")
    oem = await _entity(session, "TransformerOEM")
    bushing = await _entity(session, "BushingMaker")
    resin = await _entity(session, "ResinSupplier")
    gone = await _entity(session, "FormerSupplier")
    await _supplies(session, oem, seed)
    await _supplies(session, bushing, oem)
    await _supplies(session, resin, bushing)
    await _supplies(session, seed, resin)  # a cycle back to the seed
    await _supplies(session, gone, seed, effective_to=date(2020, 1, 1))  # ended edge

    out = await ladder(session, seed.id, today=date(2026, 11, 16))
    got = {c["entity_id"]: c for c in out["candidates"]}
    assert set(got) == {str(oem.id), str(bushing.id), str(resin.id)}
    assert got[str(oem.id)]["hops"] == 1
    assert got[str(resin.id)]["hops"] == 3
    assert got[str(resin.id)]["supply_chain_distance_from_obvious"] == 3
    path = got[str(resin.id)]["path"]
    assert [p["supplier_entity_id"] for p in path] == [str(oem.id), str(bushing.id),
                                                        str(resin.id)]
    assert all(p["source_url"] and p["evidence_ref"] for p in path)
    assert out["truncated"] is False

    two = await ladder(session, seed.id, max_hops=2, today=date(2026, 11, 16))
    assert str(resin.id) not in {c["entity_id"] for c in two["candidates"]}


def test_distance_clamps_to_schema() -> None:
    assert (distance(None), distance(0), distance(3), distance(9)) == (0, 0, 3, 5)


def test_completer_takes_the_real_hop_count() -> None:
    admin = {"executive_summary": {"value": "x"},
             "supply_chain_laddering": {"hops": 2, "driver": "data_centre_load"}}
    comp = build_schema_complete_report(admin, report_id="r-6-1")
    profile = comp.report["discovery_profile"]
    assert profile["entry_path"] == "supply_chain_laddering"
    assert profile["supply_chain_distance_from_obvious"] == 2
    assert validate_real_asset_report(comp.report).is_valid is True
    bare = build_schema_complete_report({"executive_summary": {"value": "x"}}, report_id="b")
    assert bare.report["discovery_profile"]["entry_path"] == "other"
