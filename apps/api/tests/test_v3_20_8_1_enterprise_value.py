"""V3.20 slice 8.1 — enterprise value and multiples over dated, cited inputs."""

from __future__ import annotations

import pathlib
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.pipeline.enterprise_value_step import (
    enterprise_value_section,
    market_cap_from,
    pick_inputs,
)
from app.services.safety_terms import scan_value
from app.services.valuation.enterprise_value import absolute, enterprise_value_block
from tests import test_v3_entity_relationships as _base

session = _base.session

MC = {"amount": 1_500_000_000, "currency": "USD", "as_of": "2026-11-16",
      "source_url": "https://www.nasdaq.com/x", "method": "exchange_directory"}
BAL = {"period": "FY2025", "currency": "USD", "total_debt": Decimal("400000000"),
       "cash": Decimal("100000000"), "filed": "2026-02-20",
       "sources": [{"label": "total_debt", "source_url": "https://www.sec.gov/a"}]}
EBIT = {"period": "FY2025", "currency": "USD", "value": Decimal("120000000"),
        "filed": "2026-02-20", "source_url": "https://www.sec.gov/a"}


def _m(block: dict, key: str) -> list[dict]:
    return [m for m in block["multiples"] if m["key"] == key]


def test_ev_and_multiples() -> None:
    block = enterprise_value_block(
        market_cap=MC, balance=BAL, ebit=EBIT,
        capacity=[{"value": 12, "unit": "GVA per year", "source_url": "https://x/c"},
                  {"value": 5, "unit": "GVA per year"},  # same unit: one multiple only
                  {"value": 300, "unit": "km"}])
    assert block["net_debt"]["value"] == 300_000_000
    ev = block["enterprise_value"]
    assert ev["state"] == "computed" and ev["value"] == 1_800_000_000
    assert ev["balance_sheet_age_days"] == (date(2026, 11, 16) - date(2026, 2, 20)).days
    assert _m(block, "ev_to_ebit")[0]["value"] == 15.0
    per = _m(block, "ev_per_capacity_unit")
    assert [p["capacity_unit"] for p in per] == ["GVA per year", "km"]
    assert per[0]["value"] == 150_000_000 and per[0]["unit"] == "USD per GVA per year"
    assert _m(block, "ev_to_backlog")[0]["state"] == "not_available"
    assert block["own_history"]["state"] == "not_available"
    assert scan_value(block) == []


def test_refusals_never_produce_a_number() -> None:
    eur = enterprise_value_block(market_cap={**MC, "currency": "EUR"}, balance=BAL, ebit=EBIT)
    assert eur["enterprise_value"]["reason"] == "currency_mismatch"
    assert eur["enterprise_value"]["value"] is None
    assert _m(eur, "ev_to_ebit")[0]["state"] == "refused"
    stale = enterprise_value_block(market_cap=MC, balance={**BAL, "filed": "2024-01-10"})
    assert stale["enterprise_value"]["reason"] == "stale_balance_sheet"
    none = enterprise_value_block(market_cap=None, balance=BAL)
    assert none["enterprise_value"]["reason"] == "no_market_cap"
    no_bal = enterprise_value_block(market_cap=MC, balance=None)
    assert no_bal["net_debt"]["state"] == "not_available"
    assert no_bal["enterprise_value"]["reason"] == "no_net_debt"
    loss = enterprise_value_block(market_cap=MC, balance=BAL,
                                  ebit={**EBIT, "value": Decimal("-5")})
    assert _m(loss, "ev_to_ebit")[0]["state"] == "not_meaningful"
    assert _m(loss, "ev_to_ebit")[0]["value"] is None
    netcash = enterprise_value_block(market_cap=MC, balance={**BAL, "cash": Decimal("9e8")})
    assert netcash["net_debt"]["value"] == -500_000_000  # net cash is a real answer
    lb = enterprise_value_block(market_cap={**MC, "lower_bound": True}, balance=BAL)
    assert lb["enterprise_value"]["lower_bound"] is True


def test_scale_and_fact_selection() -> None:
    assert absolute("1.5", "million") == Decimal("1500000.0")
    assert absolute(10, None) == Decimal(10)
    assert absolute(10, "lakh") is None
    rows = [
        {"label": "total_debt", "value": 400, "scale": "million", "currency": "usd",
         "period": "FY2025", "filed": "2026-02-20", "source_url": "u1", "fact_id": "1"},
        {"label": "cash_and_equivalents", "value": 100, "scale": "million",
         "currency": "USD", "period": "FY2025", "filed": "2026-02-20", "source_url": "u2"},
        {"label": "total_debt", "value": 999, "scale": "million", "currency": "USD",
         "period": "FY2026"},  # newer, but no cash for FY2026
        {"label": "total_debt", "value": 1, "scale": "million", "currency": "USD",
         "period": "H1 2026"},  # interim: not used
        {"label": "operating_profit", "value": 120, "scale": "million", "currency": "USD",
         "period": "FY2025", "filed": "2026-02-20"},
        {"label": "operating_profit", "value": 130, "scale": "million", "currency": "USD",
         "period": "FY2026", "filed": "2027-02-20"},
    ]
    balance, ebit = pick_inputs(rows)
    assert balance is not None and balance["period"] == "FY2025"
    assert balance["total_debt"] == Decimal("400000000")
    assert ebit is not None and ebit["period"] == "FY2026"


def test_market_cap_prefers_native_currency() -> None:
    native = SimpleNamespace(native_amount=2e9, native_currency="eur", market_cap_usd=2.2e9,
                             as_of="2026-11-16", source_url=None, method="m",
                             is_lower_bound=False)
    assert market_cap_from(native)["currency"] == "EUR"
    usd = SimpleNamespace(native_amount=None, native_currency=None, market_cap_usd=1e9,
                          as_of="2026-11-16", source_url=None, method="m",
                          is_lower_bound=True)
    got = market_cap_from(usd)
    assert got["currency"] == "USD" and got["amount"] == 1e9 and got["lower_bound"] is True
    assert market_cap_from(None) is None


async def test_section_reads_group_facts(session) -> None:  # noqa: ANN001
    from app.models.company import Company
    from app.models.extracted_document import ExtractedDocument, ExtractedFact

    company = Company(id=uuid.uuid4(), ticker="EVX", exchange="NASDAQ", name="EV Co")
    session.add(company)
    await session.flush()
    assert await enterprise_value_section(session, company, cfg=SimpleNamespace()) is None
    cfg = SimpleNamespace(v3_enterprise_value_enabled=True)
    assert await enterprise_value_section(session, company, cfg=cfg) is None  # no input

    doc_cols = {c.name for c in ExtractedDocument.__table__.columns}
    doc_kwargs = {"id": uuid.uuid4(), "company_id": company.id,
                  "doc_date": date(2026, 2, 20),
                  "canonical_url": "https://www.sec.gov/Archives/x.htm"}
    for col in ExtractedDocument.__table__.columns:
        if col.name in doc_kwargs or col.nullable or col.default is not None \
                or col.server_default is not None or col.primary_key:
            continue
        doc_kwargs[col.name] = "x" * 8 if str(col.type).startswith(("VARCHAR", "TEXT")) \
            else 0
    doc = ExtractedDocument(**{k: v for k, v in doc_kwargs.items() if k in doc_cols})
    session.add(doc)
    await session.flush()
    for label, value, scope in (("total_debt", 400, "group"),
                                ("cash_and_equivalents", 100, "group"),
                                ("operating_profit", 120, "group"),
                                ("total_debt", 5, "segment")):
        session.add(ExtractedFact(
            id=uuid.uuid4(), extracted_document_id=doc.id, label=label,
            value_numeric=Decimal(value), currency="USD", scale="million", period="FY2025",
            scope_type=scope, extraction_method="inline_xbrl", confidence=0.99,
            validation_status="validated", is_active=True))
    await session.flush()
    obs = SimpleNamespace(native_amount=None, native_currency=None, market_cap_usd=1.5e9,
                          as_of="2026-11-16", source_url=None, method="m",
                          is_lower_bound=False)
    block = await enterprise_value_section(session, company, cfg=cfg, observation=obs)
    assert block is not None
    assert block["enterprise_value"]["value"] == 1_800_000_000
    assert _m(block, "ev_to_ebit")[0]["value"] == 15.0


def test_report_attaches_block_and_web_reader_pins_keys() -> None:
    import inspect

    from app.services.pipeline import professional_research as pr

    assert "enterprise_value" in inspect.signature(pr.ReportInputs).parameters
    reader = (pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "components"
              / "research" / "enterpriseValue.ts").read_text()
    for key in ("enterprise_value", "market_cap", "net_debt", "multiples", "own_history",
                "note", "state", "value", "currency", "as_of", "period", "detail", "reason",
                "source_url", "lower_bound", "total_debt", "cash_and_equivalents",
                "sources", "key", "unit", "capacity_unit"):
        assert any(f"{p}.{key}" in reader for p in ("raw", "r", "mc", "nd", "ev", "s")), key
