"""V3.20 slice 9.4 — monitoring signals derived from the comparable-set records.

Each kind compares the newest research record with the previous one (or a same-layer
peer's two records, or held disclosures since the last run). The rules under test:
a first read is never a flood, an unresearched group is never "lost", the same event
cannot be raised twice through two paths, and a summary says what changed — never what
it means.
"""

from __future__ import annotations

import pathlib
import uuid
from datetime import date, datetime, timedelta, timezone

from app.models.company import Company
from app.models.monitoring import MonitoringSignal
from app.models.research_record import CompanyResearchRecord
from app.services.monitoring import research_signals as rs
from app.services.monitoring.detector import (
    KIND_CHANGE_EVENT,
    KIND_COMPETITOR_CAPACITY,
    KIND_CONSENSUS_REVISION,
    KIND_EARNINGS_RELEASED,
    KIND_PASSIVE_OWNERSHIP_CHANGE,
    KIND_RELATIONSHIP_CHANGE,
    SIGNAL_KINDS,
    detect_for_company,
    persist_signals,
)
from app.services.safety_terms import scan_value
from tests import test_v3_monitoring as _base

session = _base.session
CID = uuid.uuid4()
RID = uuid.uuid4()


def _q(period: str, end: str, *, as_of: str | None = None, eps: str | None = None) -> dict:
    return {"period": period, "period_end": end, "revenue": "100", "eps_diluted": "0.5",
            "expectation": None if as_of is None else {
                "source": "Vendor", "as_of": as_of, "eps_avg": eps, "revenue_avg": "98"}}


def _earn(*quarters: dict, state: str = "held") -> dict:
    return {"financial_capacity.earnings_track_record": {"state": state,
                                                         "quarters": list(quarters)}}


# ── earnings + consensus ───────────────────────────────────────────────────── #


def test_a_newer_quarter_is_released_and_a_first_read_is_not_a_flood() -> None:
    prev = _earn(_q("Q2 FY2026", "2026-06-30"))
    cur = _earn(_q("Q3 FY2026", "2026-09-30"), _q("Q2 FY2026", "2026-06-30"),
                _q("Q1 FY2025", "2025-03-31"))  # an older quarter newly held: not "released"
    out = rs.earnings_signals(prev, cur, company_id=CID, run_id=RID)
    assert [(s.kind, s.detail["period"]) for s in out] == [(KIND_EARNINGS_RELEASED,
                                                            "Q3 FY2026")]
    first = rs.earnings_signals(_earn(state="not_researched"), cur, company_id=CID,
                                run_id=RID)
    assert first == []


def test_a_revised_expectation_for_the_same_quarter_is_a_consensus_revision() -> None:
    prev = _earn(_q("Q4 FY2026", "2026-12-31", as_of="2026-10-01", eps="0.40"))
    cur = _earn(_q("Q4 FY2026", "2026-12-31", as_of="2026-11-10", eps="0.46"))
    out = rs.earnings_signals(prev, cur, company_id=CID, run_id=RID)
    assert [s.kind for s in out] == [KIND_CONSENSUS_REVISION]
    assert out[0].detail["before"]["eps_avg"] == "0.40"
    assert out[0].detail["after"]["eps_avg"] == "0.46"
    same_day = _earn(_q("Q4 FY2026", "2026-12-31", as_of="2026-10-01", eps="0.46"))
    assert rs.earnings_signals(prev, same_day, company_id=CID, run_id=RID) == []


# ── passive ownership ──────────────────────────────────────────────────────── #


def _passive(pct: float | None, period: str, state: str = "held") -> dict:
    return {"valuation_context.passive_ownership": {
        "state": state, "pct_of_shares_outstanding": pct, "period": period,
        "fund_count": 12, "source_urls": ["https://www.sec.gov/x"]}}


def test_passive_ownership_moves_past_the_threshold_only() -> None:
    out = rs.passive_signals(_passive(8.1, "2026Q2"), _passive(9.0, "2026Q3"),
                             company_id=CID, run_id=RID)
    assert [s.kind for s in out] == [KIND_PASSIVE_OWNERSHIP_CHANGE]
    assert out[0].detail["change_pp"] == 0.9
    assert rs.passive_signals(_passive(8.1, "2026Q2"), _passive(8.4, "2026Q3"),
                              company_id=CID, run_id=RID) == []
    assert rs.passive_signals(_passive(None, "2026Q2", state="not_researched"),
                              _passive(9.0, "2026Q3"), company_id=CID, run_id=RID) == []


# ── relationships ─────────────────────────────────────────────────────────── #


def _rel(customers: list[str], parents: list[str], *, c_state: str = "disclosed",
         p_state: str = "disclosed") -> dict:
    row = lambda n: {"counterparty_name": n, "source_url": f"https://x/{n}"}  # noqa: E731
    return {"business_model.relationships": {
        "state": "ok",
        "customers": {"state": c_state, "rows": [row(n) for n in customers]},
        "parent_and_group": {"state": p_state, "direct_parents": [row(n) for n in parents],
                             "ultimate_parents": []}}}


def test_customers_and_parents_added_or_no_longer_held() -> None:
    out = rs.relationship_signals(_rel(["TenneT", "Amprion"], ["Old Parent AG"]),
                                  _rel(["tennet", "50Hertz"], []),
                                  company_id=CID, run_id=RID)
    got = sorted((s.detail["role"], s.detail["change"], s.detail["counterparty_name"])
                 for s in out)
    assert got == [("customer", "added", "50Hertz"),
                   ("customer", "no_longer_held", "Amprion"),
                   ("parent", "no_longer_held", "Old Parent AG")]
    assert {s.kind for s in out} == {KIND_RELATIONSHIP_CHANGE}


def test_an_unresearched_group_is_unknown_not_lost() -> None:
    out = rs.relationship_signals(_rel(["TenneT"], ["P"]),
                                  _rel([], [], c_state="not_researched",
                                       p_state="not_researched"),
                                  company_id=CID, run_id=RID)
    assert out == []


# ── change events ─────────────────────────────────────────────────────────── #

OFFTAKE = {"trigger_type": "first_offtake", "label": "Offtake agreement",
           "date": "2026-11-01", "title": "Binding offtake agreement signed",
           "source_url": "https://issuer.example/offtake"}
CLUSTER = {"trigger_type": "insider_cluster_buy", "start_date": "2026-10-01",
           "end_date": "2026-10-20", "distinct_insiders": 3, "purchases": 4}


def _events(*events: dict, clusters: tuple = ()) -> dict:
    return {"growth_and_catalysts.change_events": {"events": list(events),
                                                   "insider_clusters": list(clusters)}}


def test_new_events_and_clusters_are_raised_once_through_either_path() -> None:
    out = rs.change_event_signals(_events(), _events(OFFTAKE, clusters=(CLUSTER,)),
                                  company_id=CID, run_id=RID)
    assert sorted(s.detail["trigger_type"] for s in out) == ["first_offtake",
                                                             "insider_cluster_buy"]
    assert rs.change_event_signals({}, _events(OFFTAKE), company_id=CID, run_id=RID) == []
    fresh = rs.fresh_event_signals(
        {"events": [OFFTAKE, {**OFFTAKE, "date": "2026-09-01", "source_url": "o"}]},
        cutoff=datetime(2026, 10, 15, tzinfo=timezone.utc), company_id=CID, run_id=RID)
    assert len(fresh) == 1
    # The same event through the held-disclosure path has the same key.
    record_path = next(s for s in out if s.detail["trigger_type"] == "first_offtake")
    assert fresh[0].signal_key == record_path.signal_key


# ── competitor capacity ───────────────────────────────────────────────────── #


def _capacity(*readings: tuple[float, str]) -> dict:
    return {"blocks": {"operations.real_asset": {"capacity": {"readings": [
        {"value": v, "unit": u, "as_of": "2026-11-01", "source_url": "https://p/c"}
        for v, u in readings]}}}}


def test_capacity_rises_compare_the_same_unit_only() -> None:
    rises = rs.capacity_rises(_capacity((1000, "MVA"), (5, "GWh")),
                              _capacity((1100, "MVA"), (5.1, "GWh"), (40, "km")))
    assert [(r["unit"], r["before"], r["after"]) for r in rises] == [("MVA", 1000.0, 1100.0)]


# ── the pass, on a database ───────────────────────────────────────────────── #


def _record(company, *, day: date, created: datetime, blocks: dict,
            layer: str = "transmission") -> CompanyResearchRecord:
    return CompanyResearchRecord(
        id=uuid.uuid4(), company_id=company.id, as_of=day, record_version="t",
        ticker=company.ticker, layer=layer, created_at=created,
        record_json={"blocks": blocks})


async def test_a_detection_pass_raises_each_kind_once(session, monkeypatch) -> None:  # noqa: ANN001
    company = await _base._company(session)
    await _base._prior_run(session, company)
    peer = Company(id=uuid.uuid4(), ticker="PEER", exchange="XETRA", name="Peer",
                   status="new")
    session.add(peer)
    t0 = _base.LAST_RUN - timedelta(days=60)
    session.add_all([
        _record(company, day=t0.date(), created=t0, blocks={
            **_earn(_q("Q2 FY2026", "2026-06-30", as_of="2026-05-01", eps="0.40")),
            **_passive(8.0, "2026Q1"), **_rel(["TenneT"], []), **_events()}),
        _record(company, day=_base.LAST_RUN.date(), created=_base.LAST_RUN, blocks={
            **_earn(_q("Q3 FY2026", "2026-09-30"),
                    _q("Q2 FY2026", "2026-06-30", as_of="2026-07-01", eps="0.45")),
            **_passive(9.0, "2026Q2"), **_rel(["TenneT", "Elia"], []),
            **_events(clusters=(CLUSTER,))}),
        _record(peer, day=t0.date(), created=t0,
                blocks=_capacity((1000, "MVA"))["blocks"]),
        _record(peer, day=_base.NOW.date(), created=_base.NOW - timedelta(days=1),
                blocks=_capacity((1200, "MVA"))["blocks"]),
    ])
    await session.flush()

    async def inventory(_session, _company_id):  # noqa: ANN001, ANN202
        return [{"title": "Binding offtake agreement signed with a European utility",
                 "published_at": (_base.NOW - timedelta(days=3)).date().isoformat(),
                 "canonical_url": "https://issuer.example/o"}]

    monkeypatch.setattr("app.services.pipeline.document_coverage.current_inventory",
                        inventory)
    cfg = _base._cfg()
    result = await detect_for_company(session, company_id=company.id, cfg=cfg,
                                      now=_base.NOW)
    kinds = sorted(s.kind for s in result.signals)
    assert kinds == sorted([KIND_EARNINGS_RELEASED, KIND_CONSENSUS_REVISION,
                            KIND_PASSIVE_OWNERSHIP_CHANGE, KIND_RELATIONSHIP_CHANGE,
                            KIND_CHANGE_EVENT, KIND_CHANGE_EVENT,
                            KIND_COMPETITOR_CAPACITY])
    assert scan_value([s.summary for s in result.signals]) == []
    await persist_signals(session, result, now=_base.NOW)
    stored = (await session.execute(
        MonitoringSignal.__table__.select())).all()
    assert len(stored) == 7

    again = await detect_for_company(session, company_id=company.id, cfg=cfg,
                                     now=_base.NOW)
    await persist_signals(session, again, now=_base.NOW)
    assert again.persisted == 0 and again.duplicates_suppressed == 7

    off = await detect_for_company(
        session, company_id=company.id,
        cfg=_base._cfg(v3_monitoring_research_signals_enabled=False), now=_base.NOW)
    assert not {s.kind for s in off.signals} & {
        KIND_EARNINGS_RELEASED, KIND_CHANGE_EVENT, KIND_COMPETITOR_CAPACITY}


def test_the_vocabulary_and_migrations_054_and_062_agree() -> None:
    versions = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
    text = (versions / "054_widen_monitoring_signal_kinds.py").read_text()
    later = (versions / "062_add_periodic_tasks_and_team_lists.py").read_text()
    assert 'revision: str = "054"' in text and 'down_revision: str | None = "053"' in text
    for kind in SIGNAL_KINDS:
        # V3.21.2 (migration 062) widened the CHECK with two more kinds.
        assert f'"{kind}"' in text or f'"{kind}"' in later, kind
    assert "DELETE FROM" not in text.upper()  # signals are history
    assert "DELETE FROM" not in later.upper()
