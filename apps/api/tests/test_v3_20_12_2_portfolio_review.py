"""V3.20 slice 12.2 — the portfolio review: a cited thesis status per holding, never a trade."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.models.company import Company
from app.models.monitoring import MonitoringSignal
from app.models.research_record import CompanyResearchRecord
from app.services import portfolio_review as pr
from app.services.safety_terms import scan_value
from tests.test_v3_20_12_1_user_portfolios import BASE, ME, OTHER, client, maker  # noqa: F401

CID = uuid.uuid4()
SIZING = ("increase the position", "reduce exposure", "rebalance", "trim", "add to the",
          "position size", "target weight")


def _blocks(customers: list[str], eps: str | None = None) -> dict:
    rows = [{"counterparty_name": n, "source_url": f"https://x/{n}"} for n in customers]
    out = {"business_model.relationships": {
        "state": "ok", "customers": {"state": "disclosed", "rows": rows},
        "parent_and_group": {"state": "not_researched"}}}
    if eps:
        out["financial_capacity.earnings_track_record"] = {"state": "held", "quarters": [
            {"period": "Q4 FY2026", "period_end": "2026-12-31", "expectation": {
                "source": "Vendor", "as_of": "2026-10-01" if eps == "0.50" else "2026-11-10",
                "eps_avg": eps, "revenue_avg": "100"}}]}
    return {"blocks": out}


def _rec(as_of: date, blocks: dict, ev_to_ebit: float | None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), as_of=as_of, record_json=blocks,
                           ev_to_ebit=ev_to_ebit, research_run_id=None)


def test_status_thresholds() -> None:
    one = [{"kind": "relationship_change"}]
    three_one_kind = [{"kind": "relationship_change"}] * 3
    three_two_kinds = [{"kind": "relationship_change"}] * 2 + [{"kind": "multiple_drift"}]
    assert pr.status_for([], researched_since=True, observed_since=False) == pr.INTACT
    assert pr.status_for([], researched_since=False, observed_since=False) == pr.INSUFFICIENT
    assert pr.status_for(one, researched_since=True, observed_since=True) == pr.WEAKENED
    assert pr.status_for(three_one_kind, researched_since=True,
                         observed_since=True) == pr.WEAKENED
    assert pr.status_for(three_two_kinds, researched_since=True,
                         observed_since=True) == pr.BROKEN


def test_weakening_items_are_cited_and_context_is_not_counted() -> None:
    entry = _rec(date(2026, 1, 1), _blocks(["TenneT", "Amprion"], eps="0.50"), 15.0)
    current = _rec(date(2026, 11, 1), _blocks(["TenneT", "Elia"], eps="0.42"), 10.0)
    weak, context = pr.weakening_items(entry, current, [], company_id=CID)
    kinds = sorted(w["kind"] for w in weak)
    assert kinds == ["consensus_revision", "multiple_drift", "relationship_change"]
    lost = next(w for w in weak if w["kind"] == "relationship_change")
    assert lost["source_url"] == "https://x/Amprion"
    assert [c["kind"] for c in context] == ["relationship_change"]  # Elia added: context
    status = pr.status_for(weak, researched_since=True, observed_since=False)
    assert status == pr.BROKEN


async def test_the_review_endpoint_is_owner_scoped_cited_and_never_a_trade(
        client, maker) -> None:  # noqa: ANN001, F811
    company = Company(id=uuid.uuid4(), ticker="POWL", exchange="NASDAQ", name="Powell",
                      status="new")
    async with maker() as s:
        s.add(company)
        s.add_all([
            CompanyResearchRecord(id=uuid.uuid4(), company_id=company.id,
                                  as_of=date(2026, 1, 2), record_version="t", ev_to_ebit=15,
                                  record_json=_blocks(["TenneT", "Amprion"])),
            CompanyResearchRecord(id=uuid.uuid4(), company_id=company.id,
                                  as_of=date(2026, 11, 1), record_version="t", ev_to_ebit=14,
                                  record_json=_blocks(["TenneT"])),
            MonitoringSignal(id=uuid.uuid4(), company_id=company.id,
                             kind="competitor_capacity_announcement", signal_key="k1",
                             summary="PEER (transmission) disclosed capacity of 1200 MVA.",
                             detail_json={"source_url": "https://peer/c"}, status="open",
                             observed_at=datetime(2026, 10, 1, tzinfo=timezone.utc)),
        ])
        await s.commit()
    pid = (await client.post(BASE, json={"name": "Mine"}, headers=ME)).json()["id"]
    for ticker in ("POWL", "NOPE"):
        r = await client.post(f"{BASE}/{pid}/positions",
                              json={"ticker": ticker, "entry_date": "2026-03-01"}, headers=ME)
        assert r.status_code == 201

    assert (await client.get(f"{BASE}/{pid}/review", headers=OTHER)).status_code == 404
    r = await client.get(f"{BASE}/{pid}/review", headers=ME)
    assert r.status_code == 200, r.text
    review = r.json()
    by_ticker = {h["ticker"]: h for h in review["holdings"]}
    powl = by_ticker["POWL"]
    assert powl["thesis_status"] == pr.WEAKENED
    assert {w["kind"] for w in powl["weakening"]} == {"relationship_change",
                                                      "competitor_capacity_announcement"}
    assert all(w["source_url"] for w in powl["weakening"])
    assert powl["entry_record"]["basis"] == "at or before entry"
    assert powl["current_verdict"]["state"] == "not_available"
    assert by_ticker["NOPE"]["thesis_status"] == pr.INSUFFICIENT

    assert scan_value(review) == []
    text = json.dumps(review).lower()
    for phrase in SIZING:
        assert phrase not in text, phrase
    assert all(p.startswith(("Re-run", "Investigate", "Research"))
               for h in review["holdings"] for p in h["proposed_research"])


def test_the_web_review_client_reads_the_service_keys() -> None:
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "lib"
              / "portfolioReviewApi.ts").read_text()
    for key in ("rules_version", "holdings", "status_counts", "note", "position_id",
                "ticker", "exchange", "entry_date", "thesis_status", "reason",
                "entry_record", "current_record", "weakening", "context",
                "proposed_research", "kind", "summary", "source_url"):
        assert f"{key}" in source, key
