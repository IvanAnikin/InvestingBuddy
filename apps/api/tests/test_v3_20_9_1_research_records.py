"""V3.20 slice 9.1 — the comparable-set record, read from the report's own blocks."""

from __future__ import annotations

import pathlib
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.research_records import build_record, comparable_set, write_record
from tests import test_v3_entity_relationships as _base

session = _base.session

LAYERS = {"status": "classified", "primary_layer": "transmission"}
EV = {"enterprise_value": {"state": "computed", "value": 1.8e9, "currency": "USD"},
      "multiples": [{"key": "ev_to_ebit", "state": "computed", "value": 15.0},
                    {"key": "ev_to_backlog", "state": "not_available", "value": None}]}


def _report(**blocks: dict) -> dict:
    sections = {"business_model": {}, "valuation_context": {}, "operations": {}}
    for dotted, block in blocks.items():
        section, key = dotted.split("__")
        sections.setdefault(section, {})[key] = block
    return {"version": "v-test",
            "sections": [{"key": k, **v} for k, v in sections.items()]}


COMPANY = SimpleNamespace(ticker="GRID", exchange="NASDAQ", sector="Industrials",
                          industry="Electrical Equipment")


def test_record_is_read_from_the_blocks() -> None:
    report = _report(business_model__electricity_layers=LAYERS,
                     valuation_context__enterprise_value=EV,
                     valuation_context__obscurity={"score": 72})
    size = {"market_cap_usd": 1.5e9, "market_cap_as_of": "2026-11-16"}
    rec = build_record(report=report, company=COMPANY, size_fit=size,
                       as_of=date(2026, 11, 16))
    assert rec["layer"] == "transmission"
    assert rec["enterprise_value"] == Decimal("1800000000.0") and rec["ev_currency"] == "USD"
    assert rec["ev_to_ebit"] == Decimal("15.0") and rec["obscurity_score"] == 72
    assert rec["market_cap_usd"] == Decimal("1500000000.0")
    assert rec["market_cap_as_of"] == date(2026, 11, 16)
    assert set(rec["record_json"]["blocks"]) == {
        "business_model.electricity_layers", "valuation_context.enterprise_value",
        "valuation_context.obscurity"}


def test_missing_or_refused_blocks_are_null_not_zero() -> None:
    refused = {"enterprise_value": {"state": "refused", "value": None,
                                    "reason": "currency_mismatch"}, "multiples": []}
    rec = build_record(
        report=_report(valuation_context__enterprise_value=refused,
                       business_model__electricity_layers={"status": "not_electricity"}),
        company=COMPANY, size_fit=None, as_of=date(2026, 11, 16))
    for col in ("layer", "enterprise_value", "ev_currency", "ev_to_ebit", "obscurity_score",
                "market_cap_usd", "market_cap_as_of"):
        assert rec[col] is None, col
    assert rec["record_json"]["size_fit"] is None


async def _company(session, ticker: str):  # noqa: ANN001, ANN202
    from app.models.company import Company

    c = Company(id=uuid.uuid4(), ticker=ticker, exchange="NASDAQ", name=ticker)
    session.add(c)
    await session.flush()
    return c


async def test_write_and_comparable_set(session) -> None:  # noqa: ANN001
    cfg = SimpleNamespace(v3_research_records_enabled=True)
    report = _report(business_model__electricity_layers=LAYERS,
                     valuation_context__enterprise_value=EV)
    a = await _company(session, "AAA")
    b = await _company(session, "BBB")
    c = await _company(session, "CCC")
    assert await write_record(session, company=a, research_run_id=None, report=report,
                              size_fit=None, cfg=SimpleNamespace()) is False
    assert await write_record(session, company=a, research_run_id=None, report=None,
                              size_fit=None, cfg=cfg) is False
    for company, day in ((a, date(2026, 10, 1)), (a, date(2026, 11, 1)),
                         (b, date(2026, 11, 2))):
        assert await write_record(session, company=company, research_run_id=None,
                                  report=report, size_fit=None, cfg=cfg, as_of=day)
    other = _report(business_model__electricity_layers={
        "status": "classified", "primary_layer": "storage"})
    await write_record(session, company=c, research_run_id=None, report=other,
                       size_fit=None, cfg=cfg, as_of=date(2026, 11, 3))

    peers = await comparable_set(session, "transmission")
    assert [(p.ticker, p.as_of) for p in peers] == [("BBB", date(2026, 11, 2)),
                                                     ("AAA", date(2026, 11, 1))]
    without_a = await comparable_set(session, "transmission", exclude_company_id=a.id)
    assert [p.ticker for p in without_a] == ["BBB"]


def test_migration_053_is_additive_and_follows_052() -> None:
    text = (pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / "053_add_company_research_records.py").read_text()
    assert 'revision: str = "053"' in text and 'down_revision: str | None = "052"' in text
    assert 'drop_table("company_research_records")' in text
    assert "op.drop_column" not in text and "op.alter_column" not in text
