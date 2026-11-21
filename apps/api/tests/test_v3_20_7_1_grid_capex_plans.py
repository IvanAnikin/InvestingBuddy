"""V3.20 slice 7.1 — the grid capex plan store and the ENTSO-E TYNDP 2026 ingest.

Rules under test: tags, voltage, status and year are only what the text says; the parser
reads columns BY NAME on the pinned sheets and fails closed on drift; an investment
inherits its project's promoters and border region; the ENTSO-E source (private use only)
is refused while its flag is off — nothing parsed, nothing stored; a re-ingest of an
edition updates in place, a failed layout is recorded with no plan row; capex is an
amount with its currency or neither.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models.grid_plan import GridCapexPlan, GridPlanIngest
from app.services.grid_plans import base, entsoe_tyndp
from app.services.grid_plans.store import SourceDisabled, ingest_file
from app.services.sources.issuer_holdings.base import LayoutError
from app.services.sources.issuer_holdings.xlsx import sheet_names, xlsx_rows
from app.services.web_research.source_policy import USE_PRIVATE_USE_PERMITTED
from tests._xlsx_workbook import workbook

ROOT = pathlib.Path(__file__).resolve().parents[1]
ON = SimpleNamespace(v3_grid_plans_entsoe_enabled=True)
OFF = SimpleNamespace(v3_grid_plans_entsoe_enabled=False)


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


PROJECT_HEADER = ["", "Type", "Project ID", "Name", "Project promoters", "Description",
                  "New or upgrade", "Project category", "Cross-border or internal", "Status",
                  "Commissioning year", "Submission window"]


def _portfolio(*, drop_column: str | None = None, storage: bool = True) -> bytes:
    header = [c for c in PROJECT_HEADER if c != drop_column]

    def proj(*cells: object) -> list[object]:
        row = dict(zip(PROJECT_HEADER, ["", *cells], strict=False))
        return [row.get(c, "") for c in header]

    sheets: dict[str, list[list[object]]] = {
        "About": [["About the TYNDP 2026 project portfolio"]],
        "Transmission Projects": [
            ["", "List of transmission projects accepted in TYNDP 2026"], header,
            proj("Transmission", 16, "Biscay Gulf", "Red Eléctrica, Rte",
                 "394 km HVDC-VSC subsea cable with converter stations", "New",
                 "Offshore interconnector", "Cross-border", "Under Construction", 2028, 1),
            proj("Transmission", 1288, "SuedWestLink", "TransnetBW; 50Hertz",
                 "Extra-high voltage direct current transmission", "New", "Onshore",
                 "Internal project", "Under Consideration", 2037, 2),
        ],
        "Transmission Investments": [
            ["", "List of investments"],
            ["", "Project ID", "Investment ID", "Investment Name",
             "Description of the investment",
             "Commissioning month and year foreseen by the project promoter(s)", "Status"],
            ["", 16, 101, "Gatica substation", "New 400/220kV substation and transformers",
             "12-2027", "In permitting"],
        ],
        "Borders": [["", "List of cross-border impact"], ["", "TransmissionProject_Id", "Border"],
                    ["", 16, "Spain - France"]],
    }
    if storage:
        sheets["Storage Projects"] = [
            ["", "List of storage projects"], header,
            proj("Storage", 1004, "Estonian PHES", "Energiasalv Pakri OÜ",
                 "underground pumped-hydro energy storage", "New", "Hydro pumped storage",
                 "", "In Permitting", "2032(6GWh)/2038(15GWh)", 1)]
    return workbook(sheets)


# ---------------------------------------------------------------- vocabulary


def test_vocabulary_states_only_what_the_text_says() -> None:
    assert base.technology_tags("New 400kV OHL", "and a new substation") == [
        "overhead_line", "substation"]
    assert base.technology_tags("Phase shifting transformers (PST)") == ["transformer"]
    assert base.technology_tags("A new road") == []
    assert base.max_voltage_kv("New 400/220kV substation; 132 kV line") == 400
    assert base.max_voltage_kv("no voltage stated") is None
    assert base.commissioning_year("2032(6GWh)/2038(15GWh)") == 2032
    assert base.commissioning_year("12-2027") == 2027 and base.commissioning_year("") is None
    assert [base.status_class(s) for s in (
        "Under Consideration", "Planned But Not Yet Permitting", "In Permitting",
        "Under Construction", "Commissioned", "Completed", "Committed", "Withdrawn", "")] == [
        "under_consideration", "planned", "permitting", "under_construction",
        "commissioned", "commissioned", "committed", "withdrawn", "other"]
    assert base.split_promoters("Red Eléctrica, Rte") == ["Red Eléctrica", "Rte"]
    assert base.split_promoters("REN and Red Eléctrica") == ["REN", "Red Eléctrica"]
    assert base.split_promoters("Iberdrola, S.A., Elia") == ["Iberdrola, S.A.", "Elia"]
    assert base.split_promoters("Ørsted A/S; Energinet") == ["Ørsted A/S", "Energinet"]


def test_the_xlsx_reader_reads_a_named_sheet() -> None:
    body = _portfolio()
    assert "Borders" in sheet_names(body)
    assert xlsx_rows(body, "Borders")[2][1:] == ["16", "Spain - France"]
    with pytest.raises(LayoutError, match="missing_sheet"):
        xlsx_rows(body, "Nope")


# ---------------------------------------------------------------- the parser


def test_parser_reads_projects_storage_and_investments() -> None:
    rows = {r["external_id"]: r for r in entsoe_tyndp.parse(_portfolio())}
    assert set(rows) == {"transmission:16", "transmission:1288", "storage:1004",
                         "investment:101"}
    biscay = rows["transmission:16"]
    assert biscay["promoters"] == ["Red Eléctrica", "Rte"]
    assert biscay["region"] == "Spain - France"  # the Borders sheet, not the scope column
    assert {"hvdc", "cable", "converter_station", "offshore", "interconnector"} <= set(
        biscay["technology_tags"])
    assert biscay["status_class"] == "under_construction" and biscay["commissioning_year"] == 2028
    assert "capex_amount" not in biscay  # the portfolio states no capex
    assert rows["transmission:1288"]["region"] == "Internal project"
    inv = rows["investment:101"]
    assert inv["parent_external_id"] == "transmission:16"
    assert inv["promoters"] == ["Red Eléctrica", "Rte"] and inv["region"] == "Spain - France"
    assert inv["voltage_kv"] == 400 and "transformer" in inv["technology_tags"]
    assert inv["status_class"] == "permitting" and inv["commissioning_year"] == 2027
    assert rows["storage:1004"]["commissioning_year"] == 2032
    assert "pumped_hydro" in rows["storage:1004"]["technology_tags"]


@pytest.mark.parametrize("body,reason", [
    (_portfolio(storage=False), "missing_sheet"),
    (_portfolio(drop_column="Project promoters"), "missing_header"),
])
def test_parser_fails_closed_on_drift(body: bytes, reason: str) -> None:
    with pytest.raises(LayoutError, match=reason):
        entsoe_tyndp.parse(body)


# ---------------------------------------------------------------- the store


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def test_off_by_default_and_refused_before_anything_is_stored(session) -> None:  # noqa: ANN001
    assert Settings().v3_grid_plans_entsoe_enabled is False
    with pytest.raises(SourceDisabled, match="non-commercial"):
        await ingest_file(session, key="entsoe_tyndp", body=_portfolio(), cfg=OFF)
    assert await session.scalar(select(func.count()).select_from(GridPlanIngest)) == 0


async def test_ingest_upserts_an_edition_and_keeps_history(session) -> None:  # noqa: ANN001
    first = await ingest_file(session, key="entsoe_tyndp", body=_portfolio(), cfg=ON)
    assert first.status == "complete" and first.rows_written == 4
    assert first.plan_edition == "2026-02-03"  # from the pinned file name
    again = await ingest_file(session, key="entsoe_tyndp", body=_portfolio(), cfg=ON)
    assert again.rows_written == 4
    assert await session.scalar(select(func.count()).select_from(GridCapexPlan)) == 4
    await ingest_file(session, key="entsoe_tyndp", body=_portfolio(), cfg=ON,
                      edition="2026-12-01")
    assert await session.scalar(select(func.count()).select_from(GridCapexPlan)) == 8
    row = await session.scalar(select(GridCapexPlan).where(
        GridCapexPlan.external_id == "transmission:16",
        GridCapexPlan.plan_edition == "2026-02-03"))
    assert row.use_constraint == USE_PRIVATE_USE_PERMITTED
    assert row.attribution.startswith("Source: ENTSO-E")
    assert row.source_url.endswith("20260203_TYNDP2026_project_portfolio_final.xlsx")
    assert row.capex_amount is None and row.capex_currency is None
    assert row.ingest_id == again.id


async def test_a_drifted_layout_is_recorded_and_writes_nothing(session) -> None:  # noqa: ANN001
    failed = await ingest_file(session, key="entsoe_tyndp", body=_portfolio(storage=False),
                               cfg=ON)
    assert failed.status == "failed" and "missing_sheet" in failed.error
    assert failed.file_sha256 and failed.rows_written == 0
    assert await session.scalar(select(func.count()).select_from(GridCapexPlan)) == 0


# ---------------------------------------------------------------- registry and migration


def test_registry_labels_the_source_private_use() -> None:
    from app.services.sources.registry import assert_registry_safe, build_registry

    off = build_registry(Settings()).get("entsoe_tyndp")
    assert off is not None and off.enabled is False and "PRIVATE USE ONLY" in off.reliability_note
    on = build_registry(Settings(v3_grid_plans_entsoe_enabled=True))
    assert on.get("entsoe_tyndp").enabled is True
    assert_registry_safe(on)
    src = base.SOURCES["entsoe_tyndp"]
    assert src.use_constraint == USE_PRIVATE_USE_PERMITTED and "personal use" in src.licence


def test_migration_057_follows_056_and_is_additive() -> None:
    text = (ROOT / "alembic" / "versions" / "057_add_grid_capex_plans.py").read_text()
    assert 'revision: str = "057"' in text and 'down_revision: str | None = "056"' in text
    assert 'drop_table("grid_capex_plans")' in text and 'drop_table("grid_plan_ingests")' in text
    assert "op.alter_column" not in text and "op.drop_column" not in text
    assert "ck_grid_capex_plans_capex_pair" in text


def test_together_with_separates_promoters() -> None:
    """Real ENTSO-E row: "Statnett together with Energinet, TenneT Germany, ..."."""
    from app.services.grid_plans.base import split_promoters

    assert split_promoters("Statnett together with Energinet, TenneT Germany") == [
        "Statnett", "Energinet", "TenneT Germany"]
