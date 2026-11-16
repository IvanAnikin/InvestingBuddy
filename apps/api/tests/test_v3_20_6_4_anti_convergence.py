"""V3.20 slice 6.4 — anti-convergence: discovery_seen store, novelty gate, diversity quota."""

from __future__ import annotations

import pathlib
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.discovery.novelty import (
    apply_diversity_quota,
    diversity_cell,
    issuer_key,
    layer_hint,
    load_seen,
    mark_researched,
    newest_event_date,
    novelty_decision,
    record_seen,
)
from tests import test_v3_entity_relationships as _base

session = _base.session

NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


def test_issuer_key_and_layer_hint() -> None:
    assert issuer_key("nasdaq", " powl ") == "NASDAQ:POWL"
    assert issuer_key(None, "X") is None and issuer_key("XNAS", "") is None
    assert layer_hint("Maker of power transformers and switchgear") is not None
    assert layer_hint("A fast-growing jeweller") is None
    assert diversity_cell(layer=None, driver="d", regime="US") is None
    assert diversity_cell(layer="transmission", driver=None, regime=None) == {
        "layer": "transmission", "driver": "none", "regime": "unknown"}


def test_quota_reorders_and_never_shrinks() -> None:
    cells = {
        "a": {"layer": "transmission", "driver": "none", "regime": "US"},
        "b": {"layer": "transmission", "driver": "none", "regime": "US"},
        "c": {"layer": "transmission", "driver": "none", "regime": "US"},
        "d": None,  # outside the vertical: never moved
        "e": {"layer": "transmission", "driver": "none", "regime": "Germany"},
    }
    ordered, moved = apply_diversity_quota(list("abcde"), cells.get, max_per_cell=2)
    assert ordered == ["a", "b", "d", "e", "c"] and moved == ["c"]
    assert sorted(ordered) == sorted("abcde")
    off, none_moved = apply_diversity_quota(list("abcde"), cells.get, max_per_cell=0)
    assert off == list("abcde") and none_moved == []


def _seen(days_ago: int | None, event: date | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        last_researched_at=None if days_ago is None else NOW - timedelta(days=days_ago),
        last_event_date=event)


def test_novelty_decision_states() -> None:
    assert novelty_decision(None, now=NOW, newest_event=None)["state"] == "new"
    assert novelty_decision(_seen(None), now=NOW, newest_event=None)["state"] == \
        "not_researched"
    assert novelty_decision(_seen(120), now=NOW, newest_event=None)["state"] == "not_recent"
    held = novelty_decision(_seen(10, date(2026, 10, 1)), now=NOW,
                            newest_event=date(2026, 10, 1))
    assert held["state"] == "held" and "90 days" in held["rule"]
    fresh = novelty_decision(_seen(10, date(2026, 10, 1)), now=NOW,
                             newest_event=date(2026, 11, 10))
    assert fresh["state"] == "new_event" and fresh["event_date"] == "2026-11-10"
    # No event known at research time: any held event re-admits.
    assert novelty_decision(_seen(10), now=NOW, newest_event=date(2026, 9, 1))["state"] \
        == "new_event"
    # A naive timestamp (SQLite) is read as UTC, not a crash.
    naive = SimpleNamespace(last_researched_at=(NOW - timedelta(days=5)).replace(tzinfo=None),
                            last_event_date=None)
    assert novelty_decision(naive, now=NOW, newest_event=None)["state"] == "held"


async def test_store_records_sightings_and_research(session) -> None:  # noqa: ANN001
    from app.models.company import Company

    company = Company(id=uuid.uuid4(), ticker="GRID", exchange="NASDAQ", name="Grid Co")
    session.add(company)
    await session.flush()
    cell = {"layer": "transmission", "driver": "none", "regime": "United States"}
    n = await record_seen(session, [
        {"key": "NASDAQ:GRID", "company_id": str(company.id), "shortlisted": True,
         "cell": cell},
        {"key": "XETRA:EXG", "shortlisted": False, "cell": None},
        {"key": None},
    ], run_id=uuid.uuid4(), now=NOW)
    assert n == 2
    await record_seen(session, [{"key": "NASDAQ:GRID", "shortlisted": False}], now=NOW)
    rows = await load_seen(session, ["NASDAQ:GRID", "XETRA:EXG"])
    grid = rows["NASDAQ:GRID"]
    assert (grid.times_seen, grid.times_shortlisted) == (2, 1)
    assert grid.company_id == company.id and grid.last_researched_at is None
    assert rows["XETRA:EXG"].times_seen == 1

    assert await mark_researched(session, company, now=NOW,
                                 newest_event=date(2026, 11, 1)) is True
    grid = (await load_seen(session, ["NASDAQ:GRID"]))["NASDAQ:GRID"]
    assert grid.last_researched_at is not None and grid.last_event_date == date(2026, 11, 1)

    # Research without discovery creates the row.
    other = Company(id=uuid.uuid4(), ticker="SOLO", exchange="NYSE", name="Solo")
    session.add(other)
    await session.flush()
    assert await mark_researched(session, other, now=NOW) is True
    solo = (await load_seen(session, ["NYSE:SOLO"]))["NYSE:SOLO"]
    assert solo.times_seen == 0 and solo.company_id == other.id
    assert await newest_event_date(session, None, today=NOW.date()) is None


async def test_stage_gate_holds_and_states_it(session) -> None:  # noqa: ANN001
    from app.services.discovery import pipeline as dp

    def record(ticker: str, why: str | None = None) -> SimpleNamespace:
        lead = SimpleNamespace(name=f"{ticker} Inc", why=why)
        identity = SimpleNamespace(exchange="NASDAQ", ticker=ticker, name=f"{ticker} Inc",
                                   lead=lead, listing_country="United States",
                                   company_id=None)
        return SimpleNamespace(identity=identity, registry_item=None, provenance={})

    await record_seen(session, [{"key": "NASDAQ:OLD", "shortlisted": True}], now=NOW)
    from app.models.company import Company

    old_co = Company(id=uuid.uuid4(), ticker="OLD", exchange="NASDAQ", name="Old")
    session.add(old_co)
    await session.flush()
    await mark_researched(session, old_co, now=datetime.now(timezone.utc))

    stage = dp.StageResult()
    cfg = SimpleNamespace(v3_discovery_novelty_gate_enabled=True,
                          v3_discovery_novelty_window_days=90)
    kept = await dp._novelty_gate(session, stage, [record("OLD"), record("NEW")], cfg,
                                  None)
    assert [r.identity.ticker for r in kept] == ["NEW"]
    assert [r.identity.ticker for r in stage.novelty_held] == ["OLD"]
    assert stage.novelty_decisions["NASDAQ:OLD"]["state"] == "held"
    assert stage.novelty_decisions["NASDAQ:NEW"]["state"] == "new"
    assert any("not shortlisted again" in w for w in stage.warnings)

    off = dp.StageResult()
    both = [record("OLD"), record("NEW")]
    assert await dp._novelty_gate(session, off, both, SimpleNamespace(), None) == both

    # Cell from the lead's own words; outside the vertical there is no cell.
    assert dp._record_cell(record("T", "maker of power transformers"))["layer"]
    assert dp._record_cell(record("J", "a jeweller")) is None


def test_stage_output_only_when_the_gate_or_quota_acted() -> None:
    from app.services.discovery import pipeline as dp

    assert "anti_convergence" not in dp.StageResult()._base_dict()


def test_migration_052_is_additive_and_follows_051() -> None:
    path = (pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / "052_add_discovery_seen.py")
    text = path.read_text()
    assert 'revision: str = "052"' in text and 'down_revision: str | None = "051"' in text
    assert "drop_table(\"discovery_seen\")" in text
    assert "op.drop_column" not in text and "op.alter_column" not in text
