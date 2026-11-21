"""V3.20 slice 8.5 — debt maturity ladder, dilution, contract balances from SEC XBRL."""

from __future__ import annotations

import json
import pathlib
import uuid
from types import SimpleNamespace

from app.services.pipeline.financial_depth_step import financial_depth_section
from app.services.safety_terms import scan_value
from app.services.sources.sec_financial_depth import (
    LADDER,
    contract_balances,
    debt_ladder,
    dilution_series,
    financial_depth_block,
)
from tests import test_v3_entity_relationships as _base

session = _base.session


def _instant(val: float, end: str, filed: str, accn: str = "0000000001-26-000001",
             form: str = "10-K", unit: str = "USD") -> dict:
    return {"units": {unit: [{"end": end, "val": val, "accn": accn, "form": form,
                              "filed": filed, "fy": 2025, "fp": "FY"}]}}


def _ladder_payloads(skip: str | None = None) -> dict:
    out = {}
    for i, (key, concept) in enumerate(LADDER):
        if key == skip:
            continue
        p = _instant(100.0 * (i + 1), "2025-12-31", "2026-02-20")
        # An older year-end in an older filing: never mixed into the newest ladder.
        p["units"]["USD"].append({"end": "2024-12-31", "val": 1, "accn": "0000000001-25-1",
                                  "form": "10-K", "filed": "2025-02-20"})
        out[concept] = p
    return out


def test_ladder_one_date_and_total_only_when_complete() -> None:
    full = debt_ladder(_ladder_payloads(), url_for=lambda a: f"https://www.sec.gov/{a}")
    assert full["as_of"] == "2025-12-31" and full["buckets_tagged"] == 6
    assert [b["value"] for b in full["buckets"]] == [100, 200, 300, 400, 500, 600]
    assert full["total"] == 2100 and full["currency"] == "USD"
    assert full["buckets"][0]["source_url"].startswith("https://www.sec.gov/")
    gap = debt_ladder(_ladder_payloads(skip="year_4"))
    year4 = next(b for b in gap["buckets"] if b["bucket"] == "year_4")
    assert year4["state"] == "not_disclosed" and year4["value"] is None
    assert gap["total"] is None and "all six" in gap["total_note"]
    assert debt_ladder({})["state"] == "not_disclosed"


def test_quarterly_instants_are_not_a_ladder() -> None:
    p = {LADDER[0][1]: _instant(5, "2026-06-30", "2026-08-01", form="10-Q")}
    assert debt_ladder(p)["state"] == "not_disclosed"


def _duration(val: float, start: str, end: str, filed: str, accn: str) -> dict:
    return {"start": start, "end": end, "val": val, "accn": accn, "form": "10-K",
            "filed": filed, "fy": 2025, "fp": "FY"}


def test_dilution_series_and_restatement_flag() -> None:
    payload = {"units": {"shares": [
        _duration(100, "2023-01-01", "2023-12-31", "2024-02-20", "a1"),
        _duration(105, "2024-01-01", "2024-12-31", "2025-02-20", "a2"),
        _duration(315, "2024-01-01", "2024-12-31", "2026-02-20", "a3"),  # after a 3:1 split
        _duration(330, "2025-01-01", "2025-12-31", "2026-02-20", "a3"),
        {**_duration(80, "2025-07-01", "2025-09-30", "2025-11-01", "q3"), "form": "10-Q"},
    ]}}
    out = dilution_series(payload)
    years = out["years"]
    assert [y["period_end"] for y in years] == ["2023-12-31", "2024-12-31", "2025-12-31"]
    assert years[1]["shares"] == 315 and years[1]["as_first_filed"] == 105
    assert years[1]["latest_filing_restated"] is True
    assert years[2]["change_pct"] == round(100 * (330 - 315) / 315, 2)
    assert dilution_series(None)["state"] == "not_disclosed"


def test_contract_balances_and_block() -> None:
    payloads = {"ContractWithCustomerAssetNet": _instant(50, "2025-12-31", "2026-02-20")}
    cb = contract_balances(payloads)
    assert cb["contract_assets"]["series"][0]["value"] == 50
    assert cb["contract_liabilities"]["state"] == "not_disclosed"
    block = financial_depth_block(payloads, retrieved_at="2026-11-16")
    assert block is not None and block["debt_maturity_ladder"]["state"] == "not_disclosed"
    assert scan_value(block) == []
    assert financial_depth_block({}) is None


async def test_section_fetches_by_cik(session) -> None:  # noqa: ANN001
    from app.models.company import Company
    from app.models.legal_entity import EntityIdentifier, LegalEntity

    entity = LegalEntity(id=uuid.uuid4(), entity_key="test:fd", legal_name="FD Inc",
                         normalized_name="fd inc")
    session.add(entity)
    await session.flush()
    session.add(EntityIdentifier(id=uuid.uuid4(), legal_entity_id=entity.id, scheme="cik",
                                 value="123456", value_normalized="123456", scope_key="*",
                                 source="test"))
    company = Company(id=uuid.uuid4(), ticker="FD", exchange="NASDAQ", name="FD",
                      legal_entity_id=entity.id)
    session.add(company)
    await session.flush()
    payloads = _ladder_payloads()
    seen: list[str] = []

    async def fetch(url: str, accept: str) -> tuple[int, bytes | None]:
        seen.append(url)
        concept = url.rsplit("/", 1)[-1].removesuffix(".json")
        if concept in payloads:
            return 200, json.dumps(payloads[concept]).encode()
        return 404, None

    assert await financial_depth_section(session, company, cfg=SimpleNamespace(),
                                         fetch=fetch) is None
    cfg = SimpleNamespace(v3_financial_depth_enabled=True)
    block = await financial_depth_section(session, company, cfg=cfg, fetch=fetch)
    assert block is not None and block["debt_maturity_ladder"]["total"] == 2100
    assert len(seen) == 9 and all("CIK0000123456" in u for u in seen)

    async def broken(url: str, accept: str) -> tuple[int, bytes | None]:
        return 503, None

    assert await financial_depth_section(session, company, cfg=cfg, fetch=broken) is None


def test_report_attaches_block_and_web_reader_pins_keys() -> None:
    import inspect

    from app.services.pipeline import professional_research as pr

    assert "financial_depth" in inspect.signature(pr.ReportInputs).parameters
    reader = (pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "components"
              / "research" / "financialDepth.ts").read_text()
    for key in ("debt_maturity_ladder", "dilution", "contract_assets", "contract_liabilities",
                "note", "as_of", "currency", "total", "total_note", "reason", "buckets",
                "bucket", "value", "filed", "source_url", "state", "years", "period_end",
                "shares", "change_pct", "latest_filing_restated", "series"):
        assert any(f"{p}.{key}" in reader for p in ("raw", "r", "ladder", "dil")), key
