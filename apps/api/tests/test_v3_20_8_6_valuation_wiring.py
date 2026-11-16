"""V3.20 slice 8.6 wiring — the engine runs on this run's cited blocks (ADR-099).

The rules under test: every input is cited or a named assumption; the ERP is only an
owner-entered figure WITH its source; the range is set against a dated price; the ADR-099
term appears only at ``valuation.relative_position``; the verdict reads the block; with no
computed EV there is no block.
"""

from __future__ import annotations

import copy
import pathlib
from types import SimpleNamespace

from app.core.config import Settings
from app.services import research_verdict as rv
from app.services import safety_terms
from app.services.pipeline import valuation_step as step
from app.services.pipeline.v3_pipeline import _report_safety_hits
from app.services.valuation import engine_inputs as ei

ERP = '{"value": 0.045, "source_url": "https://example.org/erp", "as_of": "2026-11-01"}'

EV = {
    "market_cap": {"value": 1_000_000_000, "currency": "USD", "as_of": "2026-11-14",
                   "source_url": "https://exchange/cap"},
    "net_debt": {"state": "computed", "value": 200_000_000, "currency": "USD",
                 "period": "FY2025", "sources": [{"source_url": "https://sec/10k"}]},
    "enterprise_value": {"state": "computed", "value": 1_200_000_000, "currency": "USD"},
    "multiples": [{"key": "ev_to_ebit", "state": "computed", "value": 12.0,
                   "ebit": 100_000_000, "period": "FY2025", "source_url": "https://sec/10k"}],
}
PEERS = {"median_ev_to_ebit": 14.0, "median_basis": "3 size-comparable peers, one currency"}
DEPTH = {"dilution": {"years": [{"period_end": "2025-12-31", "shares": 50_000_000,
                                 "source_url": "https://sec/10k"}]}}
CASH = {"operating_cash_flow": 150_000_000, "capital_expenditure": -60_000_000,
        "currency": "USD", "period": "FY2025",
        "sources": [{"source_url": "https://sec/10k-cf"}]}
REVENUE = [{"value": 800_000_000, "period": "FY2023"}, {"value": 900_000_000, "period": "FY2024"},
           {"value": 1_000_000_000, "period": "FY2025"}]
RF = ei.eng.Cited(value=0.042, source_url=step.risk_free_url(), period="2026-11-13",
                  currency="USD")


def _inputs(**kw):  # noqa: ANN003, ANN202
    args = {"ev_block": EV, "peers_block": PEERS, "depth_block": DEPTH, "cash_flow": CASH,
            "revenue_history": REVENUE, "risk_free": RF, "erp": ei.equity_risk_premium(ERP),
            "market_cap_usd": 1e9}
    args.update(kw)
    return ei.build_inputs(**args)


def test_inputs_are_cited_or_named() -> None:
    inputs = _inputs()
    assert inputs is not None
    assert inputs.base_fcff.value == 90_000_000 and inputs.base_fcff.source_url
    assert inputs.net_debt.source_url == "https://sec/10k"
    assert inputs.diluted_shares.value == 50_000_000
    assert inputs.size_premium.value == 0.02  # under USD 2bn
    base = inputs.scenarios["base"]
    assert 0.11 < base.growth[0].value < 0.12 and "FY2023 to FY2025" in base.growth[0].rationale
    assert all(a.proposed_by == "engine_default" for a in base.growth)


def test_the_erp_needs_its_source_and_risk_free_is_usd_only() -> None:
    assert ei.equity_risk_premium("") is None
    assert ei.equity_risk_premium('{"value": 0.05}') is None  # no source
    assert ei.equity_risk_premium('{"value": 0.5, "source_url": "x"}') is None  # absurd
    assert ei.equity_risk_premium("not json") is None
    eur = copy.deepcopy(EV)
    eur["enterprise_value"]["currency"] = "EUR"
    assert _inputs(ev_block=eur).risk_free is None
    assert Settings().v3_equity_risk_premium_json == ""
    assert Settings().v3_valuation_engine_enabled is False


def test_the_block_sets_the_range_against_a_dated_price() -> None:
    block = ei.valuation_block(_inputs(), EV)
    assert block["valuation_range"]["state"] == "computed"
    assert set(block["valuation_range"]["methods"]) == {"fcff_dcf", "peer_multiple"}
    assert block["price_reference"]["per_share"] == 20.0
    assert block["price_reference"]["market_cap_as_of"] == "2026-11-14"
    assert block["relative_position"] in (ei.UNDERVALUED, ei.IN_LINE, ei.OVERVALUED)
    assert set(block["upside_downside_pct"]) == {"low", "mid", "high"}
    # The only ADR-099 term is at its allowed path.
    assert safety_terms.scan_value(block, path="valuation",
                                   allowances=safety_terms.V320_REPORT_ALLOWANCES) == []


def test_without_an_erp_the_range_fails_closed() -> None:
    block = ei.valuation_block(_inputs(erp=None), EV)
    assert block["valuation_range"]["state"] == "insufficient_evidence"
    assert block["relative_position"] is None and block["upside_downside_pct"] is None
    assert ei.valuation_block(_inputs(ev_block={}), {}) is None


def test_the_verdict_reads_the_block_and_the_gate_scans_it() -> None:
    block = ei.valuation_block(_inputs(), EV)
    report = {"sections": [{"key": "valuation_context", "findings": []}], "valuation": block}
    verdict = rv.verdict_block(report, valuation=block)
    assert "valuation_insufficient" not in {c["key"] for c in verdict["caps"]}
    pillar = next(p for p in verdict["pillars"] if p["key"] == "valuation_gap")
    assert pillar["state"] == rv.SCORED
    assert _report_safety_hits({**report, "verdict": verdict}) == []
    leaked = copy.deepcopy(report)
    leaked["valuation"]["note"] = "The stock looks undervalued."
    assert _report_safety_hits(leaked)


def test_dgs10_parsing_skips_blank_days() -> None:
    body = "observation_date,DGS10\n2026-11-12,4.18\n2026-11-13,.\n"
    cited = step.parse_dgs10(body, step.risk_free_url())
    assert cited.value == 0.0418 and cited.period == "2026-11-12"
    assert step.parse_dgs10("garbage", "u") is None


async def test_the_step_is_off_by_default_and_needs_an_ev() -> None:
    off = SimpleNamespace(v3_valuation_engine_enabled=False)
    assert await step.valuation_section(None, None, cfg=off, ev_block=EV, peers_block=None,
                                        depth_block=None, size_fit=None) is None
    on = SimpleNamespace(v3_valuation_engine_enabled=True)
    assert await step.valuation_section(None, None, cfg=on, ev_block={}, peers_block=None,
                                        depth_block=None, size_fit=None) is None


def test_the_web_reader_reads_the_block_keys() -> None:
    source = (pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "components"
              / "research" / "valuationRange.ts").read_text()
    for key in ("valuation_range", "per_share_low", "per_share_mid", "per_share_high",
                "methods", "price_reference", "per_share", "market_cap_as_of",
                "relative_position", "upside_downside_pct", "assumptions", "rationale",
                "cited", "source_url", "floors", "reason"):
        assert key in source, key


def test_the_record_keeps_the_range_and_the_review_reads_its_drift() -> None:
    import uuid
    from datetime import date

    from app.services import portfolio_review as review_mod
    from app.services.research_records import build_record

    block = ei.valuation_block(_inputs(), EV)
    company = SimpleNamespace(ticker="X", exchange="NYSE", sector=None, industry=None)
    values = build_record(report={"sections": [], "valuation": block}, company=company,
                          size_fit=None, as_of=date.today())
    kept = values["record_json"]["valuation"]
    assert set(kept) == {"block_version", "valuation_range", "price_reference",
                         "relative_position", "upside_downside_pct"}
    assert "valuation" not in build_record(report={"sections": []}, company=company,
                                           size_fit=None, as_of=date.today())["record_json"]

    def rec(as_of: date, position: str) -> SimpleNamespace:
        return SimpleNamespace(id=uuid.uuid4(), as_of=as_of, ev_to_ebit=None,
                               research_run_id=None, rubric_json=None,
                               record_json={"blocks": {},
                                            "valuation": {"relative_position": position}})

    weak, context = review_mod.weakening_items(
        rec(date(2026, 1, 1), "undervalued"), rec(date(2026, 11, 1), "overvalued"), [],
        company_id=uuid.uuid4())
    assert [w["kind"] for w in weak] == ["valuation_range_position"]
    assert "below" in weak[0]["summary"] and "above" in weak[0]["summary"]
    assert safety_terms.scan_value(weak) == []
    weak, context = review_mod.weakening_items(
        rec(date(2026, 1, 1), "overvalued"), rec(date(2026, 11, 1), "in_line"), [],
        company_id=uuid.uuid4())
    assert weak == [] and [c["kind"] for c in context] == ["valuation_range_position"]
