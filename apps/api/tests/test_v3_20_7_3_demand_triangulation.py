"""V3.20 slice 7.3 — demand-side triangulation.

Rules under test: only a company in the electricity stack gets the block; with no named
customer it says so; a named customer is CORROBORATED only by an active project in the
subject's technology (or a provider's active interconnection queue), NOT corroborated when
it appears in a plan with nothing relevant and active, and "not in held plans" otherwise —
which is never evidence against; names match through legal-form stripping, aliases and
whole-token containment, never a substring of a word; only enabled sources and the newest
edition are read; the backlog claim is referred to by label and capex is said to be absent;
the block passes the report gate; the web reads the keys.
"""

from __future__ import annotations

import pathlib
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models.grid_plan import GridCapexPlan
from app.services.grid_plans.triangulation import (
    held_plans,
    name_key,
    triangulation_block,
)
from app.services.pipeline.v3_pipeline import _report_safety_hits

WEB = pathlib.Path(__file__).resolve().parents[2] / "web" / "src"
TODAY = date(2026, 11, 16)
LAYERS = {"status": "classified", "primary_layer": "transmission"}


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


def _customers(*names: str) -> dict:
    return {"customers": {"state": "disclosed", "rows": [
        {"name": n, "source_url": "https://example.com/10k"} for n in names]}}


def _plan(name: str, promoters: list[str], *, tags: list[str], status: str = "planned",
          year: int | None = 2029, kind: str = "project", mw: float | None = None) -> dict:
    return {"source_key": "aemo_tai", "plan_edition": "2026-08", "record_kind": kind,
            "project_name": name, "promoters": promoters, "technology_tags": tags,
            "status_class": status, "commissioning_year": year,
            "commissioning_text": str(year), "capacity_text": None, "capacity_mw": mw,
            "source_url": "https://aemo.example/tai.xlsx", "attribution": "Source: AEMO."}


PLANS = [
    _plan("HumeLink", ["Transgrid"], tags=["substation", "transformer"], status="committed"),
    _plan("Old line", ["Powerlink"], tags=["overhead_line"], status="commissioned", year=2021),
    _plan("Pumped hydro", ["Powerlink"], tags=["pumped_hydro"]),
    _plan("Queue", ["AEP", "PJM"], tags=["gen_solar"], status="under_consideration",
          year=2029, kind="queue_summary", mw=800.0),
]


def test_corroborated_not_corroborated_and_not_in_plans() -> None:
    report = {"sections": [{"key": "growth_and_catalysts", "findings": [
        {"label": "F4", "statement": "Order backlog reached USD 1.2 billion"},
        {"label": "F5", "statement": "Revenue grew"}]}]}
    block = triangulation_block(
        relationships=_customers("TransGrid Pty Ltd", "Powerlink Queensland",
                                 "American Electric Power Company, Inc.", "Acme Retail"),
        layers=LAYERS, plans=PLANS, today=TODAY, report=report,
        sources_read=["aemo_tai@2026-08"])
    assert block is not None and block["state"] == "corroborated"
    by = {c["name"]: c for c in block["customers"]}
    assert by["TransGrid Pty Ltd"]["status"] == "corroborated"
    assert by["TransGrid Pty Ltd"]["projects"][0]["project_name"] == "HumeLink"
    # Powerlink: in the plans, but commissioned in the past / pumped hydro ≠ transmission.
    assert by["Powerlink Queensland"]["status"] == "not_corroborated"
    assert by["Powerlink Queensland"]["plan_records"] == 2
    # Alias: a filing's "American Electric Power" is the queue's "AEP".
    assert by["American Electric Power Company, Inc."]["status"] == "corroborated"
    assert by["Acme Retail"]["status"] == "not_in_held_plans"
    assert block["subject_claim_labels"] == ["F4"]
    assert "SIZE of a backlog claim is not checked" in block["capex_note"]
    assert block["counts"] == {"corroborated": 2, "not_corroborated": 1, "not_in_held_plans": 1}


def test_states_without_a_match_or_a_customer() -> None:
    only_retail = triangulation_block(relationships=_customers("Acme Retail"), layers=LAYERS,
                                      plans=PLANS, today=TODAY)
    assert only_retail["state"] == "no_grid_customer_matched"
    stale = triangulation_block(relationships=_customers("Powerlink"), layers=LAYERS,
                                plans=PLANS, today=TODAY)
    assert stale["state"] == "not_corroborated"
    none = triangulation_block(relationships={"customers": {
        "state": "not_disclosed", "rows": [], "note": "No customer is disclosed."}},
        layers=LAYERS, plans=PLANS, today=TODAY)
    assert none["state"] == "no_customer_named" and "disclosed" in none["reason"]
    off = triangulation_block(relationships=None, layers=LAYERS, plans=PLANS, today=TODAY)
    assert off["state"] == "no_customer_named" and "graph is off" in off["reason"]
    assert triangulation_block(relationships=_customers("Transgrid"),
                               layers={"status": "unclassified"}, plans=PLANS,
                               today=TODAY) is None


def test_storage_layer_reads_storage_and_queues() -> None:
    block = triangulation_block(relationships=_customers("Powerlink", "AEP"),
                                layers={"status": "classified", "primary_layer": "storage"},
                                plans=PLANS, today=TODAY)
    by = {c["name"]: c["status"] for c in block["customers"]}
    assert by == {"Powerlink": "corroborated", "AEP": "corroborated"}


def test_name_matching_is_whole_token_never_a_fragment() -> None:
    assert name_key("Red Eléctrica de España, S.A.U.") == "red electrica de espana"
    assert name_key("TransGrid (NSW) Pty Ltd") == "transgrid"
    block = triangulation_block(
        relationships=_customers("Grid"),  # "grid" must not match "Transgrid"
        layers=LAYERS, plans=PLANS, today=TODAY)
    assert block["customers"][0]["status"] == "not_in_held_plans"


def test_the_block_passes_the_report_gate() -> None:
    block = triangulation_block(relationships=_customers("Transgrid"), layers=LAYERS,
                                plans=PLANS, today=TODAY)
    report = {"sections": [{"key": "business_model", "demand_triangulation": block}]}
    assert _report_safety_hits(report) == []
    assert Settings().v3_demand_triangulation_enabled is True


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


def _row(key: str, edition: str, name: str) -> GridCapexPlan:
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    return GridCapexPlan(
        id=uuid.uuid4(), source_key=key, plan_name="p", plan_edition=edition,
        record_kind="project", external_id=name, project_name=name,
        promoters_json=["Transgrid"], technology_tags_json=["substation"],
        status_class="planned", commissioning_year=2029, source_url="https://x",
        retrieved_at=now, use_constraint="open_licence", attribution="Source: x.")


async def test_only_enabled_sources_and_the_newest_edition_are_read(session) -> None:  # noqa: ANN001
    session.add_all([_row("aemo_tai", "2026-06", "old"), _row("aemo_tai", "2026-08", "new"),
                     _row("entsoe_tyndp", "2026-02-03", "tyndp")])
    await session.flush()
    cfg = SimpleNamespace(v3_grid_plans_aemo_enabled=True, v3_grid_plans_lbl_enabled=True,
                          v3_grid_plans_entsoe_enabled=False)
    rows, read = await held_plans(session, cfg)
    assert [r["project_name"] for r in rows] == ["new"] and read == ["aemo_tai@2026-08"]
    cfg.v3_grid_plans_entsoe_enabled = True
    rows, read = await held_plans(session, cfg)
    assert {r["project_name"] for r in rows} == {"new", "tyndp"}


async def test_pipeline_reads_plans_only_for_a_classified_company(session) -> None:  # noqa: ANN001
    from app.services.pipeline.v3_pipeline import _demand_plans

    cfg = SimpleNamespace(v3_demand_triangulation_enabled=True,
                          v3_grid_plans_aemo_enabled=True, v3_grid_plans_lbl_enabled=True,
                          v3_grid_plans_entsoe_enabled=False)
    assert await _demand_plans(session, cfg=cfg, layers_block={"status": "unclassified"}) is None
    assert await _demand_plans(session, cfg=cfg, layers_block=LAYERS) == ([], [])
    cfg.v3_demand_triangulation_enabled = False
    assert await _demand_plans(session, cfg=cfg, layers_block=LAYERS) is None


def test_the_web_reads_the_producer_keys() -> None:
    reader = (WEB / "components" / "research" / "demandTriangulation.ts").read_text()
    for key in ("state", "reason", "layer", "technologies", "sources_read",
                "subject_claim_labels", "capex_note", "customers", "name",
                "customer_source_url", "status", "plan_records", "relevant_active",
                "matched_promoters", "projects", "project_name", "source_key",
                "plan_edition", "record_kind", "status_class", "commissioning_text",
                "technology_tags", "capacity_text", "source_url", "attribution"):
        assert key in reader, key
    assert "readDemandTriangulationBlock(r.demand_triangulation)" in (
        WEB / "components" / "research" / "v3Research.ts").read_text()
