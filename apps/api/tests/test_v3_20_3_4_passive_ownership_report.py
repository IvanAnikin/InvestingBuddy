"""V3.20 slice 3.4 — the report's "Passive ownership" block and its web reader."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.models.passive_ownership import FundHoldingsIngest
from app.services import safety_terms
from app.services.pipeline import professional_research as pr
from app.services.pipeline.passive_ownership_report import (
    MAX_FUNDS,
    not_ingested_block,
    passive_ownership_block,
    theme_flags,
)
from app.services.pipeline.v3_pipeline import _passive_ownership_section
from tests import test_v3_entity_relationships as _base
from tests.test_v3_20_3_3_passive_ownership_view import (
    LEI,
    POWL_COVER,
    _company,
    _fetch,
    _row,
)
from tests.test_v3_entity_relationships import _cfg

session = _base.session


def _fund(name: str, shares: float, *, series: str, etf: Any = True) -> dict[str, Any]:
    return {"series_id": series, "name": name, "sponsor": "Example Trust", "unit": "shares",
            "is_etf": etf, "shares": shares, "value_usd": shares * 10.0, "pct_of_fund": 0.12,
            "report_date": "2026-06-30"}


def _view(**overrides: Any) -> dict[str, Any]:
    security = {
        "identifier_kind": "isin", "identifier": "US7391281067", "security_id": "s",
        "security_type": "ordinary_share", "counted_in_pct": True, "not_counted_reason": None,
        "fund_count": 3, "etf_count": 1, "etf_unknown_count": 1, "index_fund_count": 2,
        "shares_held": 600.0, "etf_shares_held": 300.0, "source_url":
        "https://www.sec.gov/files/dera/data/form-n-port-data-sets/2026q3_nport.zip",
        "top_funds": [
            _fund("Example Smart Grid Infrastructure ETF", 300.0, series="S1"),
            _fund("Example Small-Cap Index Fund", 200.0, series="S2", etf=None),
            _fund("Example Total Market Fund", 100.0, series="S3", etf=False),
        ],
        "previous": {"period": "2026Q2", "fund_count": 2, "shares_held": 500.0,
                     "change_shares": 100.0, "change_pct": 20.0, "change_fund_count": 1},
    }
    view = {
        "state": "ok", "period": "2026Q3", "previous_period": "2026Q2",
        "securities": [security,
                       {**security, "identifier_kind": "lei", "identifier": LEI,
                        "counted_in_pct": False, "not_counted_reason":
                        "matched_by_issuer_lei_only", "fund_count": 1, "previous": None}],
        "shares_held": 600.0, "etf_shares_held": 300.0,
        "pct_of_shares_outstanding": 1.65, "etf_pct_of_shares_outstanding": 0.82,
        "pct_label": "Held by US-registered funds and ETFs only (SEC Form N-PORT). UCITS and "
                     "other non-US funds are not included.",
        "pct_withheld_reason": None,
        "denominator": {"shares": 36432564.0, "as_of": "2026-08-03", "form": "10-Q",
                        "accession": "0000080420-26-000107", "filed": "2026-08-04",
                        "classes": 1, "source_url": "https://data.sec.gov/api/x.json",
                        "concept": "dei:EntityCommonStockSharesOutstanding"},
        "holdings_as_of": "2026-06-30",
        "concentration": {"top3_share_of_passive_pct": 100.0, "basis": "b"},
        "source": "SEC Form N-PORT data sets (US-registered funds)",
        "freshness_note": "N-PORT holdings are as of each fund's report date.",
    }
    return view | overrides


class TestBlock:
    def test_held(self) -> None:
        block = passive_ownership_block(_view())
        assert block is not None and block["state"] == "held"
        assert block["pct_of_shares_outstanding"] == 1.65
        assert block["denominator"]["as_of"] == "2026-08-03"
        assert "UCITS" in block["pct_label"]
        assert [f["name"] for f in block["funds"]][0] == "Example Smart Grid Infrastructure ETF"
        assert [f["is_etf"] for f in block["funds"]] == ["yes", "not_stated", "no"]
        assert block["funds"][0]["theme_flags"] == ["grid_and_electrification"]
        assert block["funds"][1]["theme_flags"] == ["small_cap"]
        assert block["themed_fund_count"] == 2
        assert block["fund_count"] == 3  # the LEI line is not added
        assert block["other_lines"][0]["reason"] == "matched_by_issuer_lei_only"
        assert block["change"] == {"previous_period": "2026Q2", "change_shares": 100.0,
                                   "change_pct": 20.0, "change_fund_count": 1}

    def test_withheld_pct_keeps_its_reason(self) -> None:
        block = passive_ownership_block(_view(pct_of_shares_outstanding=None, denominator=None,
                                              pct_withheld_reason="no_cover_page_share_count"))
        assert block is not None and block["pct_of_shares_outstanding"] is None
        assert block["pct_withheld_reason"] == "no_cover_page_share_count"

    def test_states(self) -> None:
        assert passive_ownership_block(None) is None
        assert passive_ownership_block({"state": "disabled"}) is None
        assert passive_ownership_block({"state": "entity_unresolved"})["state"] == \
            "not_researched"
        none = passive_ownership_block({"state": "not_held_or_not_ingested"})
        assert none["state"] == "none_found" and "not a statement" in none["note"]
        assert not_ingested_block()["reason"] == "no_data_set_ingested"

    def test_a_flagged_fund_name_drops_only_that_fund(self) -> None:
        view = _view()
        view["securities"][0]["top_funds"].append(
            _fund("Example STRONG BUY Fund", 50.0, series="S9"))
        block = passive_ownership_block(view)
        assert block["withheld_for_safety"] == 1
        assert all("BUY" not in f["name"] for f in block["funds"])

    def test_fund_list_is_capped(self) -> None:
        view = _view()
        view["securities"][0]["top_funds"] = [
            _fund(f"Fund {i}", float(100 - i), series=f"S{i}") for i in range(20)]
        block = passive_ownership_block(view)
        assert len(block["funds"]) == MAX_FUNDS and block["funds_omitted"] == 20 - MAX_FUNDS

    def test_theme_flags_are_name_matches(self) -> None:
        assert theme_flags("Global Uranium & Nuclear ETF") == ["nuclear"]
        assert theme_flags("Copper Miners ETF") == ["materials_for_electrification"]
        assert theme_flags("Total Stock Market Index") == []
        assert theme_flags(None) == []


class TestAssemble:
    def test_attached_to_valuation_context_only_when_given(self) -> None:
        subject = {"ticker": "POWL", "name": "Powell Industries"}
        plain = pr.assemble(pr.ReportInputs(subject=subject, questions=(), findings=()))
        valuation = next(s for s in plain["sections"] if s["key"] == "valuation_context")
        assert "passive_ownership" not in valuation
        report = pr.assemble(pr.ReportInputs(subject=subject, questions=(), findings=(),
                                             passive_ownership=passive_ownership_block(_view())))
        valuation = next(s for s in report["sections"] if s["key"] == "valuation_context")
        assert valuation["passive_ownership"]["state"] == "held"
        # The block's own wording never trips the report's language check.
        assert safety_terms.scan_value(valuation["passive_ownership"], path="po") == []
        assert safety_terms.scan_value(not_ingested_block(), path="po") == []


class TestPipelineSeam:
    async def test_off_not_ingested_and_held(self, session, monkeypatch) -> None:
        from app.integrations import sec_relationships

        monkeypatch.setattr(sec_relationships, "_default_fetch", _fetch(POWL_COVER))
        cfg = _cfg()
        company = await _company(session)
        assert await _passive_ownership_section(
            session, company, cfg=_cfg(v3_passive_ownership_enabled=False)) is None
        assert (await _passive_ownership_section(session, company, cfg=cfg))["reason"] == \
            "no_data_set_ingested"
        session.add_all([
            FundHoldingsIngest(id=uuid.uuid4(), source_kind="nport", period="2026Q3",
                               status="complete"),
            _row("2026Q3", "3643256.4"),
        ])
        await session.flush()
        block = await _passive_ownership_section(session, company, cfg=cfg)
        assert block is not None and block["state"] == "held"
        assert block["pct_of_shares_outstanding"] == 10.0
        assert block["denominator"]["as_of"] == "2026-08-03"

    async def test_a_failure_costs_only_the_block(self, session, monkeypatch) -> None:
        from app.services.passive_ownership import view as view_module

        async def boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("x")

        monkeypatch.setattr(view_module, "company_passive_ownership", boom)
        session.add(FundHoldingsIngest(id=uuid.uuid4(), source_kind="nport", period="2026Q3",
                                       status="complete"))
        await session.flush()
        company = SimpleNamespace(id=uuid.uuid4(), legal_entity_id=uuid.uuid4())
        assert await _passive_ownership_section(session, company, cfg=_cfg()) is None


class TestWebReaderKeys:
    """Every key ``apps/web/.../passiveOwnership.ts`` reads is one the producer writes."""

    def test_reader_keys_are_written(self) -> None:
        reader = (Path(__file__).resolve().parents[2] / "web" / "src" / "components"
                  / "research" / "passiveOwnership.ts").read_text()
        js_methods = {"filter", "trim", "map", "length", "replace"}
        read = set(re.findall(
            r"\b(?:r|raw|denominator|change|concentration)\.([a-z_]+)\b", reader)) - js_methods
        written: set[str] = set()

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                written.update(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(passive_ownership_block(_view()))
        walk(not_ingested_block())
        assert read and read <= written, sorted(read - written)
