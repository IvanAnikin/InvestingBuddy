"""V3.20 slice 11.5 — Asian grid plans: OCCTO (Japan) and CEA (India).

Rules under test:
* OCCTO: only tables 4-2/4-3/4-5/4-6 are projects (4-4/4-7 decommissioning are not read);
  the header is pinned BY NAME (footnote digits ignored); the filing company is written
  once per block and carried down; a header repeated inside a continued table is skipped;
  cells are NFKC-normalised with Japanese wraps joined and ※ marks removed; companies carry
  their official English names and area; 未定 has no year; no capex.
* CEA: columns found BY NAME with whitespace removed; a serial-numbered row is a project
  with its stated cost (Rs crore × 10⁷ INR), its elements follow it; the SPV is a second
  promoter and PGCIL carries its full name; anticipated completion wins, the original is
  kept.
* OCCTO is open and on; CEA needs permission to reproduce, so it is private use and OFF.
"""

from __future__ import annotations

from decimal import Decimal
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
from app.services.grid_plans import base, cea_transmission, occto_supply_plan
from app.services.grid_plans.store import SourceDisabled, ingest_file, parser_for
from app.services.sources.issuer_holdings.base import LayoutError
from app.services.web_research.source_policy import (
    USE_OPEN_LICENCE,
    USE_PRIVATE_USE_PERMITTED,
)


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


# --------------------------------------------------------------------------- OCCTO

LINE_HEADER = ["届出事業者", "名称32", "電圧", "こう長33,34", "回線数", "着工年月", "使用開始年月",
               "理由35"]
SUB_HEADER = ["届出事業者", "名称36", "電圧", "容量", "台数", "着工年月", "使用開始年月", "理由35"]
TEPCO = "東京電力パワー\nグリッド株式会社"
JPOWER = "電源開発送変電ﾈｯﾄﾜｰｸ株式会社"


def _occto_tables() -> list[tuple[str, list[list[object]]]]:
    return [
        ("2", [LINE_HEADER,
               [TEPCO, "新京葉線※1", "500kV", "40km", "2", "2024年4月", "2028年3月", "需要対策"],
               ["", "Ｆ支線", "275kV", "3.2km", "2", "2025年1月", "未定", "経年対策"]]),
        # The same table continued on the next page, with its header printed again.
        ("2", [LINE_HEADER,
               [JPOWER, "佐久間東幹線 (直流)", "DC-200kV", "120km", "2", "2026年1月", "2030年10月",
                "連系線増強"]]),
        ("3", [LINE_HEADER,
               ["北海道電力ネットワーク株式会社", "北本連系設備 (海底ケーブル)", "DC-250kV", "―", "1",
                "2027年4月", "2031年3月", "連系線増強"]]),
        ("4", [["届出事業者", "名称", "電圧", "こう長", "回線数", "廃止年月", "理由"],
               ["沖縄電力株式会社", "旧線", "132kV", "5km", "1", "2027年3月", "経年"]]),
        ("5", [SUB_HEADER,
               ["九州電力送配電株式会社", "新北九州変電所", "500/220kV", "1500MVA", "2",
                "2025年6月", "2029年6月", "需要対策"]]),
        # A continued table with no header keeps its columns and company.
        ("5", [["", "東変換所 (仮称)", "275kV", "900MW", "3", "2026年1月", "2030年3月", "連系"]]),
    ]


def test_occto_reads_project_tables_and_carries_the_company() -> None:
    recs = occto_supply_plan.records_from_tables(_occto_tables())
    names = [r["project_name"] for r in recs]
    assert names == ["新京葉線", "F支線", "佐久間東幹線 (直流)", "北本連系設備 (海底ケーブル)",
                     "新北九州変電所", "東変換所 (仮称)"]
    assert "旧線" not in names  # 4-4 decommissioning is not a project
    first, second, dc, hokkaido, sub, cont = recs
    assert first["promoters"] == ["東京電力パワーグリッド株式会社",
                                  "TEPCO Power Grid, Incorporated"]
    assert second["promoters"] == first["promoters"]  # forward-filled within the block
    assert first["region"] == "Japan: Tokyo" and first["status_class"] == "under_construction"
    assert first["voltage_kv"] == 500 and first["commissioning_year"] == 2028
    assert first["capacity_text"] == "こう長 40km (回線数 2)"
    assert second["commissioning_text"] == "未定" and second["commissioning_year"] is None
    assert dc["promoters"][1] == "J-Power Transmission Network Co., Ltd."
    assert dc["region"] == "Japan: inter-regional"
    assert dc["technology_tags"] == ["hvdc"]  # tagged on the name, not the reason
    assert hokkaido["status_class"] == "planned" and hokkaido["capacity_text"] is None
    assert {"hvdc", "cable", "interconnector"} <= set(hokkaido["technology_tags"])
    assert sub["category"].startswith("主要変電所") and "substation" in sub["technology_tags"]
    assert cont["promoters"][0] == "九州電力送配電株式会社"
    assert "converter_station" in cont["technology_tags"]
    assert all(r.get("capex_amount") is None for r in recs)
    assert len({r["external_id"] for r in recs}) == len(recs)


def test_occto_external_id_is_stable_across_reads() -> None:
    a = occto_supply_plan.records_from_tables(_occto_tables())
    b = occto_supply_plan.records_from_tables(_occto_tables())
    assert [r["external_id"] for r in a] == [r["external_id"] for r in b]
    assert a[0]["external_id"].startswith("t4-2-")


@pytest.mark.parametrize(("tables", "reason"), [
    ([("2", [["", "", "", ""], ["x", "y", "z", "w"]])], "missing_header"),
    ([("2", [SUB_HEADER])], "table_header_mismatch"),
    ([("9", [LINE_HEADER])], "unexpected_table"),
])
def test_occto_fails_closed_on_drift(tables, reason: str) -> None:  # noqa: ANN001
    with pytest.raises(LayoutError, match=reason):
        occto_supply_plan.records_from_tables(tables)


def test_occto_parse_refuses_non_pdf_and_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(LayoutError, match="not_a_pdf"):
        occto_supply_plan.parse(b"<html></html>")
    monkeypatch.setattr(occto_supply_plan, "tables_from_pdf", lambda body: [])
    with pytest.raises(LayoutError, match="missing_tables"):
        occto_supply_plan.parse(b"%PDF-1.7")
    monkeypatch.setattr(occto_supply_plan, "tables_from_pdf",
                        lambda body: [("2", [LINE_HEADER])])
    with pytest.raises(LayoutError, match="no_projects"):
        occto_supply_plan.parse(b"%PDF-1.7")


def test_occto_cell_normalisation() -> None:
    assert occto_supply_plan.company_names("中部電力パワー\nグリッド株式会社") == (
        ["中部電力パワーグリッド株式会社", "Chubu Electric Power Grid Co., Inc."], "Chubu")
    assert occto_supply_plan.company_names("新規送電株式会社") == (["新規送電株式会社"], None)
    assert occto_supply_plan.tags("Ｆ支線 架空") == ["overhead_line"]


# --------------------------------------------------------------------------- CEA

CEA_HEADER = ["S. No .", "Name of the Transmission\nProject & Scope", "Executin g\nAgency",
              "Length\n(CKM)", "MVA", "Cost\n(Rs. Cr)", "Date of\nTransfer of SPV",
              "Completion\nTarget -\nOriginal", "Completion\nTarget -\nAnticipated",
              "Total Locs.\n(no.)", "TF-CB\n(Nos)", "TE-CB\n(Nos)", "Stringing-CB\n(ckm)",
              "Civil works-\nCB (%)", "Eqpt.\nReceived-\nCB (%)", "Eqpt.\nErection -CB (%)",
              "Remarks /\nConstraints &\nassistance required."]


def _row(**kw: str) -> list[str]:
    cols = {"sno": 0, "name": 1, "agency": 2, "ckm": 3, "mva": 4, "cost": 5, "spv": 6,
            "orig": 7, "ant": 8, "remarks": 16}
    out = [""] * 17
    for k, v in kw.items():
        out[cols[k]] = v
    return out


def _cea_rows() -> list[list[str]]:
    return [
        ["Monthly progress report", "", ""],  # a title row above the header
        CEA_HEADER,
        _row(sno="1", name="Transmission System for evacuation of 7 GW RE Power from Khavda "
             "(SPV: POWERGRID Vataman\nTransmission Ltd)", agency="PGCIL", cost="4,231",
             spv="Dec-23", orig="Dec-25", ant="Mar - 27"),
        _row(name="Establishment of 765 kV switching station near Vataman", agency="PGCIL",
             orig="Dec-25", ant="Mar - 27", remarks="Land acquired"),
        _row(name="Halvad – Vataman 765 kV D/c line", agency="PGCIL", ckm="254",
             orig="Dec-25", ant="Mar - 27"),
        # the next page: no header, rows continue
        _row(sno="2", name="Augmentation at Karur PS: 2x500 MVA, 400/230 kV ICTs",
             agency="Adani", cost="1,48,000.5", orig="Nov-26", ant="Nov-26"),
        _row(name="2x500 MVA, 400/230 kV ICT (3rd & 4th)", mva="1000", ant="Nov-26"),
        _row(sno="3", name="Scheme with no cost stated", agency="Sterlite", cost="-",
             ant="Jun-28"),
    ]


def test_cea_projects_carry_cost_and_their_elements() -> None:
    recs = cea_transmission.records_from_rows(_cea_rows(), route="TBCB")
    kinds = [(r["record_kind"], r["external_id"], r["parent_external_id"]) for r in recs]
    assert kinds == [("project", "tbcb-1", None), ("investment", "tbcb-1-e1", "tbcb-1"),
                     ("investment", "tbcb-1-e2", "tbcb-1"), ("project", "tbcb-2", None),
                     ("investment", "tbcb-2-e1", "tbcb-2"), ("project", "tbcb-3", None)]
    p1, e1, e2, p2, e3, p3 = recs
    assert p1["capex_amount"] == Decimal(4231) * 10**7 and p1["capex_currency"] == "INR"
    assert p1["capex_period"] == "project cost as stated: Rs 4,231 crore"
    assert p1["promoters"] == ["PGCIL", "Power Grid Corporation of India Limited",
                               "POWERGRID Vataman Transmission Ltd"]
    assert p1["commissioning_text"] == "Mar-27 (anticipated; original Dec-25)"
    assert p1["commissioning_year"] == 2027 and p1["status_class"] == "under_construction"
    assert p1["region"] == "India" and "SPV transferred Dec-23" in p1["description"]
    assert e1.get("capex_amount") is None and e1["description"] == "remarks: Land acquired"
    assert e1["voltage_kv"] == 765 and "substation" in e1["technology_tags"]
    assert e2["capacity_text"] == "254 ckm" and e2["promoters"] == p1["promoters"]
    assert p2["capex_amount"] == Decimal("148000.5") * 10**7
    assert "transformer" in p2["technology_tags"]
    assert e3["capacity_text"] == "1000 MVA" and e3["promoters"] == ["Adani"]  # inherited
    assert p3["capex_amount"] is None and p3["capex_currency"] is None
    assert p3["commissioning_year"] == 2028


def test_cea_rows_before_the_header_are_ignored() -> None:
    assert cea_transmission.records_from_rows(_cea_rows()[:1], route="RTM") == []
    assert cea_transmission.header_columns(CEA_HEADER) is not None
    assert cea_transmission.header_columns(CEA_HEADER[:5]) is None


def test_cea_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(LayoutError, match="not_a_pdf"):
        cea_transmission.parse_tbcb(b"PK\x03\x04")
    renamed = [c.replace("Executin g", "Implementing") for c in CEA_HEADER]
    monkeypatch.setattr(cea_transmission, "rows_from_pdf", lambda body: [renamed])
    with pytest.raises(LayoutError, match="missing_header"):
        cea_transmission.parse_rtm(b"%PDF-1.7")
    monkeypatch.setattr(cea_transmission, "rows_from_pdf", lambda body: [CEA_HEADER])
    with pytest.raises(LayoutError, match="no_projects"):
        cea_transmission.parse_rtm(b"%PDF-1.7")


# --------------------------------------------------------------------------- sources

def test_sources_terms_and_defaults() -> None:
    cfg = Settings()
    occto = base.SOURCES["occto_supply_plan"]
    assert occto.use_constraint == USE_OPEN_LICENCE and cfg.v3_grid_plans_occto_enabled
    assert "edited" in occto.attribution and occto.download_url.endswith(".pdf")
    for key in ("cea_tbcb_uc", "cea_rtm_uc"):
        src = base.SOURCES[key]
        assert src.use_constraint == USE_PRIVATE_USE_PERMITTED
        assert src.flag == "v3_grid_plans_cea_enabled" and "prior permission" in src.licence
        assert src.download_url.startswith("https://cea.nic.in/")
    assert cfg.v3_grid_plans_cea_enabled is False
    assert parser_for("occto_supply_plan") is occto_supply_plan.parse
    assert parser_for("cea_tbcb_uc") is cea_transmission.parse_tbcb
    assert parser_for("cea_rtm_uc") is cea_transmission.parse_rtm


def test_registry_lists_both() -> None:
    from app.services.sources.registry import build_registry

    reg = build_registry(Settings())
    assert reg.get("occto_supply_plan").enabled
    assert not reg.get("cea_transmission_uc").enabled


async def test_ingest_with_attribution_and_the_cea_gate(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(occto_supply_plan, "tables_from_pdf", lambda body: _occto_tables())
    monkeypatch.setattr(cea_transmission, "rows_from_pdf", lambda body: _cea_rows())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        with pytest.raises(SourceDisabled):
            await ingest_file(session, key="cea_tbcb_uc", body=b"%PDF-1.7", cfg=Settings())
        on = SimpleNamespace(v3_grid_plans_occto_enabled=True, v3_grid_plans_cea_enabled=True)
        o = await ingest_file(session, key="occto_supply_plan", body=b"%PDF-1.7", cfg=on)
        c = await ingest_file(session, key="cea_tbcb_uc", body=b"%PDF-1.7", cfg=on)
        assert (o.status, o.rows_written, o.plan_edition) == ("complete", 6, "2026-03-30")
        assert (c.status, c.rows_written, c.plan_edition) == ("complete", 6, "2026-10")
        rows = (await session.execute(select(GridCapexPlan))).scalars().all()
        by_source = {r.source_key: r for r in rows}
        assert by_source["occto_supply_plan"].use_constraint == USE_OPEN_LICENCE
        assert by_source["cea_tbcb_uc"].use_constraint == USE_PRIVATE_USE_PERMITTED
        project = next(r for r in rows if r.external_id == "tbcb-1")
        assert project.capex_currency == "INR" and float(project.capex_amount) == 4.231e10
        assert all(r.attribution.startswith("Source:") for r in rows)
    await engine.dispose()


def test_power_grid_of_india_aliases() -> None:
    from app.services.grid_plans.triangulation import _matches

    assert _matches("POWERGRID", "PGCIL")
    assert _matches("Power Grid Corporation of India Ltd", "PGCIL")
    assert _matches("Adani Energy Solutions", "Adani")
    assert not _matches("Kansai Electric Power", "Kansai Transmission and Distribution, Inc.")
