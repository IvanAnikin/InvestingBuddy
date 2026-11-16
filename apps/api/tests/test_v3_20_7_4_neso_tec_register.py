"""V3.20 slice 7.4 — NESO Transmission Entry Capacity (TEC) register.

Rules under test: the CSV is read BY COLUMN NAME and fails closed on drift; every register
row is a project whose promoter is the customer, with tags from the plant types and NESO's
status words on the shared vocabulary; each host transmission owner (NGET, SPT, SHET — not
OFTO) gets one summary of the capacity that is contracted but not built; the source is open
(NESO Open Data Licence, attribution on every row) and on by default; demand
triangulation reads it — a TO named by a filing is corroborated through its summary, a
developer only through a project past scoping.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models.grid_plan import GridCapexPlan
from app.services.grid_plans import base, neso_tec
from app.services.grid_plans.store import SourceDisabled, ingest_file
from app.services.grid_plans.triangulation import held_plans, triangulation_block
from app.services.sources.issuer_holdings.base import LayoutError
from app.services.web_research.source_policy import USE_OPEN_LICENCE


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


HEADER = ["Project Name", "Customer Name", "Connection Site", "Stage", "MW Connected",
          "MW Increase / Decrease", "Cumulative Total Capacity (MW)", "MW Effective From",
          "Project Status", "Agreement Type", "HOST TO", "Plant Type", "Project ID",
          "Project Number", "Gate"]
ROWS = [
    ["17 Acres and BESS", "AEUK BATTERY PROJECT II LTD", "17 ACRES BESS 275KV SUBSTATION", "",
     "0.0", "95.76", "95.76", "2032-06-30", "Scoping", "Direct Connection", "SHET",
     "Energy Storage System", "a0l1", "PRO-004392", "1"],
    ["Hornsea Four", "Orsted Hornsea Project Four Ltd", "Killingholme", "", "0", "2400",
     "2400", "2030-10-31", "Consents Approved", "Direct Connection", "NGET",
     "Wind Offshore", "a0l2", "PRO-000100", "2"],
    ["Seagreen", "Seagreen Wind Energy Ltd", "Tealing", "", "1075", "0", "1075",
     "2023-10-01", "Built", "Direct Connection", "SPT", "Wind Offshore", "a0l3",
     "PRO-000200", ""],
    ["Kincardine STATCOM", "SP Transmission", "Kincardine", "", "0", "150", "150",
     "2027-04-01", "Under Construction/Commissioning", "Direct Connection", "SPT",
     "Reactive Compensation", "a0l4", "PRO-000300", ""],
    ["Offshore link", "Some OFTO Ltd", "Sea", "", "0", "500", "500", "2031-01-01",
     "Awaiting Consents", "Direct Connection", "OFTO",
     "Interconnector;Wind Offshore", "a0l5", "PRO-000400", ""],
    ["Demand site", "Data Centre Co Ltd", "Slough", "", "0", "300", "300", "2029-03-31",
     "Awaiting Consents", "Embedded", "NGET",
     "Demand;Energy Storage System;PV Array (Photo Voltaic/solar)", "a0l6", "PRO-000500", ""],
]
ON = SimpleNamespace(v3_grid_plans_neso_enabled=True)


def _csv(header: list[str] | None = None, rows: list[list[str]] | None = None) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header or HEADER)
    w.writerows(ROWS if rows is None else rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def test_projects_carry_the_customer_tags_status_and_year() -> None:
    rows = {r["external_id"]: r for r in neso_tec.parse(_csv())}
    bess = rows["PRO-004392"]
    assert bess["record_kind"] == "project" and bess["promoters"] == ["AEUK BATTERY PROJECT II LTD"]
    assert bess["technology_tags"] == ["storage"] and bess["status_class"] == "under_consideration"
    assert bess["commissioning_year"] == 2032 and bess["capacity_mw"] == 95.76
    assert "SHET" in bess["region"]
    assert rows["PRO-000100"]["status_class"] == "planned"
    assert rows["PRO-000100"]["technology_tags"] == ["gen_wind_offshore", "offshore"]
    assert rows["PRO-000200"]["status_class"] == "commissioned"
    assert rows["PRO-000300"]["status_class"] == "under_construction"
    assert rows["PRO-000400"]["status_class"] == "permitting"
    assert rows["PRO-000500"]["technology_tags"] == ["demand_connection", "gen_pv_array",
                                                     "storage"]


def test_one_unbuilt_summary_per_host_owner_and_none_for_ofto() -> None:
    rows = {r["external_id"]: r for r in neso_tec.parse(_csv())
            if r["record_kind"] == "queue_summary"}
    assert set(rows) == {"host-nget", "host-shet", "host-spt"}
    nget = rows["host-nget"]
    assert nget["capacity_mw"] == 2700.0 and nget["promoters"][0] == (
        "National Grid Electricity Transmission")
    assert "2 projects not yet built" in nget["capacity_text"]
    # SPT: the built Seagreen row is not counted, the STATCOM is.
    assert rows["host-spt"]["capacity_mw"] == 150.0
    assert rows["host-spt"]["commissioning_text"] == "MW effective 2027–2027"


@pytest.mark.parametrize(("body", "reason"), [
    (_csv([c for c in HEADER if c != "HOST TO"]), "missing_header"),
    (b"\xff\xfe\x00bad", "unreadable_csv"),
    (_csv(rows=[]), "no_rows"),
])
def test_drift_fails_closed(body: bytes, reason: str) -> None:
    with pytest.raises(LayoutError) as exc:
        neso_tec.parse(body)
    assert str(exc.value).split(":")[0] == reason


def test_plant_tags() -> None:
    assert neso_tec.plant_tags("Pump Storage;Wind Onshore") == [
        "gen_wind_onshore", "pumped_hydro", "storage"]
    assert neso_tec.plant_tags("CCGT (Combined Cycle Gas Turbine)") == ["gen_ccgt"]
    assert neso_tec.plant_tags("") == []


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def test_ingest_is_open_attributed_and_gated(session) -> None:  # noqa: ANN001
    assert Settings().v3_grid_plans_neso_enabled is True
    run = await ingest_file(session, key="neso_tec", body=_csv(), cfg=ON)
    assert (run.status, run.rows_written, run.plan_edition) == ("complete", 9, "2026-11-13")
    rows = (await session.execute(select(GridCapexPlan))).scalars().all()
    assert {r.use_constraint for r in rows} == {USE_OPEN_LICENCE}
    assert all(r.attribution.startswith("Supported by National Energy SO Open Data")
               for r in rows)
    with pytest.raises(SourceDisabled):
        await ingest_file(session, key="neso_tec", body=_csv(),
                          cfg=SimpleNamespace(v3_grid_plans_neso_enabled=False))


async def test_triangulation_reads_the_register(session) -> None:  # noqa: ANN001
    await ingest_file(session, key="neso_tec", body=_csv(), cfg=ON)
    cfg = SimpleNamespace(v3_grid_plans_neso_enabled=True, v3_grid_plans_aemo_enabled=False,
                          v3_grid_plans_lbl_enabled=False, v3_grid_plans_entsoe_enabled=False)
    plans, read = await held_plans(session, cfg)
    assert read == ["neso_tec@2026-11-13"]
    rel = {"customers": {"state": "disclosed", "rows": [
        {"name": "National Grid Electricity Transmission plc"}, {"name": "SSEN Transmission"},
        {"name": "AEUK Battery Project II Limited"}]}}
    block = triangulation_block(
        relationships=rel, layers={"status": "classified", "primary_layer": "storage"},
        plans=plans, today=date(2026, 11, 16), sources_read=read)
    by = {c["name"]: c["status"] for c in block["customers"]}
    assert by == {"National Grid Electricity Transmission plc": "corroborated",
                  "SSEN Transmission": "corroborated",
                  # its only project is at "Scoping": in the plans, but not active yet
                  "AEUK Battery Project II Limited": "not_corroborated"}


def test_registry_and_licence() -> None:
    from app.services.sources.registry import build_registry

    entry = build_registry(Settings()).get("neso_tec")
    assert entry.enabled and entry.jurisdiction == "GB"
    assert "Supported by National Energy SO Open Data" in base.SOURCES["neso_tec"].licence
