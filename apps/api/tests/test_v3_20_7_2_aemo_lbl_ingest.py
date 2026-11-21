"""V3.20 slice 7.2 — AEMO Transmission Augmentation Information + Berkeley Lab Queued Up.

Rules under test: both parsers read pinned columns BY NAME and fail closed on drift;
AEMO's status words map onto the shared vocabulary and its in-service cells (date serial,
"Oct-26", "Stage 1: Aug-29 ...") give the first stated year; Queued Up counts ACTIVE
requests only, aggregated per (region, provider, utility) with MW, IA-executed MW and MW by
type; both sources are open (attribution carried on every row) and on by default.
"""

from __future__ import annotations

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
from app.services.grid_plans import aemo_tai, base, lbl_queued_up
from app.services.grid_plans.store import ingest_file
from app.services.sources.issuer_holdings.base import LayoutError
from app.services.web_research.source_policy import USE_OPEN_LICENCE
from tests._xlsx_workbook import workbook


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


AEMO_HEADER = ["Date of project update", "Region", "Project type", "Project name",
               "Proponent(s)", "Project status", "Project description",
               "Increased network capacity", "Preparatory activities completion date",
               "Funding approval", "Construction stage", "Early works completion date",
               "In service date", "Capacity release date", "Source"]


def _aemo(header: list[str] | None = None) -> bytes:
    h = header or AEMO_HEADER

    def row(**kw: object) -> list[object]:
        return [kw.get(c, "") for c in h]

    return workbook({
        "Information": [["Transmission Augmentation Information"]],
        "Transmission augmentations": [
            ["Transmission Augmentation Information"], h,
            row(**{"Region": "SA, NSW, VIC", "Project type": "New interconnector",
                   "Project name": "Project EnergyConnect - Stage 2",
                   "Proponent(s)": "ElectraNet, Transgrid, AusNet Services",
                   "Project status": "Committed",
                   "Project description": "Second 330 kV circuit; 500 kV transformers",
                   "Increased network capacity": "650 MW", "In service date": "Oct-26"}),
            row(**{"Region": "NSW", "Project type": "New transmission",
                   "Project name": "HumeLink", "Proponent(s)": "Transgrid",
                   "Project status": "Committed", "In service date": 46722}),
            row(**{"Region": "VIC", "Project type": "New transmission",
                   "Project name": "Western Victoria Reinforcement",
                   "Proponent(s)": "VicGrid", "Project status": "Actionable Victorian Project",
                   "In service date": "Stage 1: Aug-29\nStage 2: Nov-30"}),
            row(**{"Region": "TAS", "Project name": "North West Tasmania",
                   "Proponent(s)": "TasNetworks",
                   "Project status": "Future project with ongoing RIT-T"}),
        ],
    })


LBL_HEADER = ["q_id", "q_status", "q_date", "prop_date", "on_date", "wd_date", "ia_date",
              "IA_phase_raw", "IA_phase_clean", "county", "state", "fips_code", "poi_name",
              "region", "project_name", "utility", "entity", "developer", "cluster", "service",
              "project_type", "type_1", "type_2", "type_3", "type_clean", "mw_1", "mw_2",
              "mw_3", "q_year", "prop_year"]


def _lbl(header: list[str] | None = None) -> bytes:
    h = header or LBL_HEADER

    def row(**kw: object) -> list[object]:
        return [kw.get(c, "") for c in h]

    pjm_aep = {"region": "PJM", "entity": "PJM", "utility": "AEP", "state": "OH"}
    return workbook({
        "Introduction": [["Queued Up"]],
        "03. Complete Queue Data": [
            ["RETURN TO CONTENTS"], h,
            row(**pjm_aep, q_status="active", IA_phase_clean="IA Executed",
                type_clean="Solar+Battery", mw_1=200, mw_2=100, prop_year=2028),
            row(**{**pjm_aep, "state": "WV"}, q_status="active",
                IA_phase_clean="Facility Study", type_clean="Gas", mw_1=500, prop_year=2030),
            row(**pjm_aep, q_status="withdrawn", type_clean="Wind", mw_1=900, prop_year=2027),
            row(region="ERCOT", entity="ERCOT", q_status="active", type_clean="Battery",
                mw_1=150, prop_year=2027, state="TX"),
            row(region="West", entity="APS", utility="Arizona Public Service",
                q_status="operational", type_clean="Solar", mw_1=20),
        ],
    })


ON = SimpleNamespace(v3_grid_plans_aemo_enabled=True, v3_grid_plans_lbl_enabled=True)


def test_aemo_maps_status_and_years() -> None:
    rows = {r["project_name"]: r for r in aemo_tai.parse(_aemo())}
    pec = rows["Project EnergyConnect - Stage 2"]
    assert pec["promoters"] == ["ElectraNet", "Transgrid", "AusNet Services"]
    assert pec["status_class"] == "committed" and pec["commissioning_year"] == 2026
    assert pec["region"] == "Australia NEM: SA, NSW, VIC" and pec["voltage_kv"] == 500
    assert {"transformer", "interconnector"} <= set(pec["technology_tags"])
    assert pec["capacity_text"] == "650 MW" and "capex_amount" not in pec
    hume = rows["HumeLink"]
    assert hume["commissioning_text"] == "2027-12-01" and hume["commissioning_year"] == 2027
    assert rows["Western Victoria Reinforcement"]["status_class"] == "planned"
    assert rows["Western Victoria Reinforcement"]["commissioning_year"] == 2029
    assert rows["North West Tasmania"]["status_class"] == "under_consideration"
    assert hume["external_id"] == "nsw-humelink"


def test_queued_up_aggregates_active_requests_per_provider() -> None:
    rows = {r["external_id"]: r for r in lbl_queued_up.parse(_lbl())}
    assert set(rows) == {"pjm-pjm-aep", "ercot-ercot"}
    aep = rows["pjm-pjm-aep"]
    assert aep["record_kind"] == "queue_summary" and aep["promoters"] == ["AEP", "PJM"]
    assert aep["capacity_mw"] == 800.0  # 300 hybrid + 500 gas; the withdrawn 900 MW is not
    assert "2 active requests, 800 MW; 300 MW with an executed" in aep["capacity_text"]
    assert "Gas 500 MW" in aep["capacity_text"]
    assert aep["commissioning_text"] == "proposed online 2028–2030"
    assert aep["region"] == "US PJM: OH, WV"
    assert set(aep["technology_tags"]) == {"gen_gas", "gen_solar_battery"}
    assert rows["ercot-ercot"]["promoters"] == ["ERCOT"]


@pytest.mark.parametrize("parse,body,reason", [
    (aemo_tai.parse, _aemo([c for c in AEMO_HEADER if c != "Proponent(s)"]), "missing_header"),
    (lbl_queued_up.parse, _lbl([c for c in LBL_HEADER if c != "mw_1"]), "missing_header"),
    (aemo_tai.parse, workbook({"Other": [["x"]]}), "missing_sheet"),
])
def test_parsers_fail_closed_on_drift(parse, body: bytes, reason: str) -> None:  # noqa: ANN001
    with pytest.raises(LayoutError, match=reason):
        parse(body)


def test_year_and_serial_helpers() -> None:
    assert base.commissioning_year("47452") == 2029
    assert base.commissioning_year("Jul-28 (Transformer cooling completed July 2025)") == 2028
    assert base.commissioning_year("Indicative ... Sept-31") == 2031
    assert base.serial_to_iso("46204") == "2026-07-01" and base.serial_to_iso("Oct-26") == "Oct-26"


async def test_both_ingest_with_attribution_and_are_on_by_default() -> None:
    cfg = Settings()
    assert cfg.v3_grid_plans_aemo_enabled is True and cfg.v3_grid_plans_lbl_enabled is True
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        a = await ingest_file(session, key="aemo_tai", body=_aemo(), cfg=ON)
        q = await ingest_file(session, key="lbl_queued_up", body=_lbl(), cfg=ON)
        assert (a.status, a.rows_written, a.plan_edition) == ("complete", 4, "2026-08")
        assert (q.status, q.rows_written, q.plan_edition) == ("complete", 2, "2026-05")
        rows = (await session.execute(select(GridCapexPlan))).scalars().all()
        assert {r.use_constraint for r in rows} == {USE_OPEN_LICENCE}
        assert all(r.attribution.startswith("Source:") for r in rows)
        lbl = next(r for r in rows if r.source_key == "lbl_queued_up")
        assert "GridTracker" in lbl.attribution and float(lbl.capacity_mw) > 0
    await engine.dispose()


def test_registry_lists_both_as_open_sources() -> None:
    from app.services.sources.registry import build_registry

    reg = build_registry(Settings())
    assert reg.get("aemo_tai").enabled and reg.get("lbl_queued_up").enabled
    assert "CC BY 4.0" in reg.get("lbl_queued_up").reliability_note
    assert "CC BY 4.0" in base.SOURCES["lbl_queued_up"].licence
    assert "any purpose" in base.SOURCES["aemo_tai"].licence
