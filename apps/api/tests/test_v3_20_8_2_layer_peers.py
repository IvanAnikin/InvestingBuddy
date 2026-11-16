"""V3.20 slice 8.2 — peers by electricity layer from the comparable-set store."""

from __future__ import annotations

import pathlib
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.safety_terms import scan_value
from app.services.valuation.layer_peers import layer_peers_block, subject_inputs
from tests import test_v3_entity_relationships as _base

session = _base.session


def _rec(ticker: str, cap: float | None, ev_ebit: float | None, ccy: str = "USD"
         ) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), ticker=ticker, exchange="NASDAQ", as_of=date(2026, 11, 1),
        market_cap_usd=None if cap is None else Decimal(str(cap)),
        market_cap_as_of=date(2026, 11, 1),
        ev_to_ebit=None if ev_ebit is None else Decimal(str(ev_ebit)),
        ev_currency=ccy if ev_ebit is not None else None, obscurity_score=60)


def test_median_over_size_comparable_peers_only() -> None:
    records = [_rec("A", 1e9, 10), _rec("B", 2e9, 12), _rec("C", 3e9, 20),
               _rec("HUGE", 50e9, 40), _rec("NOEV", 1e9, None)]
    block = layer_peers_block(layer="transmission", subject_market_cap_usd=1e9,
                              subject_ev_to_ebit=14.0, records=records)
    assert block is not None and block["peer_count"] == 5
    huge = next(p for p in block["peers"] if p["ticker"] == "HUGE")
    assert huge["size_ratio"] == 50.0 and huge["comparable_size"] is False
    assert block["median_ev_to_ebit"] == 12.0  # A, B, C — not HUGE, not NOEV
    assert "3 size-comparable" in block["median_basis"]
    assert scan_value(block) == []


def test_median_withheld_with_reason() -> None:
    two = layer_peers_block(layer="t", subject_market_cap_usd=1e9, subject_ev_to_ebit=None,
                            records=[_rec("A", 1e9, 10), _rec("B", 1e9, 11)])
    assert two["median_ev_to_ebit"] is None and "at least 3" in two["median_reason"]
    mixed = layer_peers_block(layer="t", subject_market_cap_usd=1e9, subject_ev_to_ebit=None,
                              records=[_rec("A", 1e9, 10), _rec("B", 1e9, 11),
                                       _rec("C", 1e9, 12, "EUR")])
    assert mixed["median_ev_to_ebit"] is None and "currency" in mixed["median_reason"]
    nosize = layer_peers_block(layer="t", subject_market_cap_usd=None,
                               subject_ev_to_ebit=None, records=[_rec("A", 1e9, 10)])
    assert nosize["median_ev_to_ebit"] is None and nosize["peers"][0]["size_ratio"] is None
    assert layer_peers_block(layer="t", subject_market_cap_usd=1e9, subject_ev_to_ebit=None,
                             records=[]) is None


def test_subject_inputs() -> None:
    ev = {"multiples": [{"key": "ev_to_ebit", "state": "computed", "value": 15.0}]}
    assert subject_inputs(ev, {"market_cap_usd": 1.5e9}) == (1.5e9, 15.0)
    refused = {"multiples": [{"key": "ev_to_ebit", "state": "not_meaningful", "value": None}]}
    assert subject_inputs(refused, None) == (None, None)


async def test_pipeline_section_reads_the_store(session) -> None:  # noqa: ANN001
    from app.models.company import Company
    from app.services.pipeline.v3_pipeline import _layer_peers_section
    from app.services.research_records import write_record

    layers = {"status": "classified", "primary_layer": "transmission"}
    report = {"sections": [{"key": "business_model", "electricity_layers": layers}]}
    cfg = SimpleNamespace(v3_research_records_enabled=True, v3_layer_peers_enabled=True)
    subject = Company(id=uuid.uuid4(), ticker="SUBJ", exchange="NASDAQ", name="S")
    peer = Company(id=uuid.uuid4(), ticker="PEER", exchange="NYSE", name="P")
    session.add_all([subject, peer])
    await session.flush()
    for c in (subject, peer):
        await write_record(session, company=c, research_run_id=None, report=report,
                           size_fit=None, cfg=cfg)
    block = await _layer_peers_section(session, subject, cfg=cfg, layers_block=layers,
                                       ev_block=None, size_fit=None)
    assert block is not None and [p["ticker"] for p in block["peers"]] == ["PEER"]
    assert await _layer_peers_section(session, subject, cfg=SimpleNamespace(),
                                      layers_block=layers, ev_block=None,
                                      size_fit=None) is None
    assert await _layer_peers_section(session, subject, cfg=cfg,
                                      layers_block={"status": "not_electricity"},
                                      ev_block=None, size_fit=None) is None


def test_report_attaches_block_and_web_reader_pins_keys() -> None:
    import inspect

    from app.services.pipeline import professional_research as pr

    assert "layer_peers" in inspect.signature(pr.ReportInputs).parameters
    reader = (pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "components"
              / "research" / "layerPeers.ts").read_text()
    for key in ("layer", "peers", "subject_ev_to_ebit", "median_ev_to_ebit", "median_basis",
                "median_reason", "note", "ticker", "exchange", "as_of", "market_cap_usd",
                "market_cap_as_of", "ev_to_ebit", "ev_currency", "obscurity_score",
                "size_ratio", "comparable_size"):
        assert f"raw.{key}" in reader or f"r.{key}" in reader, key
