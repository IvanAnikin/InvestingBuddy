"""V3.20 slice 8.3 — precedent transactions.

Rules under test: the extractor states only what the sentence says (an EV basis only when
the sentence says "enterprise value"; a metric only when it states a multiple); a deal is
used only once an admin APPROVES it with layer and metric; rejected rows are kept; the
block takes approved deals in the layer within ten years and states a median only from
three or more on an EV basis; that median becomes a precedent EV/EBIT engine method; the
admin routes need a signed-in admin and are on the proxy's list; the web reads the keys.
"""

from __future__ import annotations

import pathlib
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.api.v1 import precedent_transactions_admin as api
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.precedent_transaction import PrecedentTransaction
from app.services.electricity.taxonomy import LAYER_KEYS
from app.services.safety_terms import scan_value
from app.services.valuation import precedents as pt

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT.parent / "web" / "src"
ME = {"X-IB-Admin-Email": "owner@example.com"}
BASE = "/api/v1/admin/precedent-transactions"
LAYER = LAYER_KEYS[0]
TODAY = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


# ---------------------------------------------------------------- the extractor


def test_extractor_reads_parties_amount_basis_and_a_stated_multiple() -> None:
    [deal] = pt.extract_deals(
        "Results were strong. Hubbell has agreed to acquire DMC Power for an enterprise "
        "value of USD 825 million, about 12.5x EBITDA. Other text follows.")
    assert (deal["acquirer_name"], deal["target_name"]) == ("Hubbell", "DMC Power")
    assert deal["currency"] == "USD" and deal["enterprise_value"] == Decimal("825e6")
    assert deal["value_basis"] == "enterprise_value"
    assert deal["metric_key"] == "ebitda" and deal["metric_value"] == Decimal("66000000")
    assert "derived" in deal["metric_period"]


def test_extractor_labels_consideration_and_derives_no_metric_from_it() -> None:
    [deal] = pt.extract_deals("Prysmian completed the acquisition of Encore Wire for "
                              "$4.2 billion in cash, or 10x EBITDA.")
    assert deal["value_basis"] == "consideration" and "metric_key" not in deal
    assert deal["enterprise_value"] == Decimal("4.2e9")


def test_extractor_skips_what_it_cannot_state() -> None:
    assert pt.extract_deals("Siemens acquired a stake in a company last year.") == []
    assert pt.extract_deals("Acme has acquired Acme for USD 5 million.") == []
    assert pt.extract_deals("") == []


# ---------------------------------------------------------------- the block


def _row(target: str, *, mult: float | None = 10.0, status: str = "approved",
         layer: str = LAYER, when: date = date(2024, 5, 1),
         basis: str = "enterprise_value", key: str = "ebit") -> SimpleNamespace:
    ev = Decimal("1000e6")
    metric = None if mult is None else ev / Decimal(str(mult))
    return SimpleNamespace(
        id=uuid.uuid4(), acquirer_name="Buyer", target_name=target, status=status,
        layer=layer, announced_date=when, currency="USD", enterprise_value=ev,
        value_basis=basis, metric_key=key if mult else None, metric_value=metric,
        metric_period="FY2023", multiple=None if metric is None else ev / metric,
        source_url="https://example.com/deal", source_title="Deal release",
        retrieved_at=NOW, entry_method="curated")


def test_block_uses_approved_deals_in_the_layer_within_ten_years() -> None:
    rows = [_row("A", mult=10), _row("B", mult=12), _row("C", mult=14),
            _row("PROPOSED", status="proposed"), _row("REJ", status="rejected"),
            _row("OTHER", layer="elsewhere"), _row("OLD", when=date(2015, 1, 1)),
            _row("CONS", mult=30, basis="consideration")]
    block = pt.precedent_block(rows, layer=LAYER, today=TODAY)
    assert block is not None
    assert {t["target"] for t in block["transactions"]} == {"A", "B", "C", "CONS"}
    assert block["medians"] == {"ev_to_ebit": {"median": 12.0, "count": 3}}
    assert "control premium" in block["note"]
    assert scan_value(block) == []


def test_no_median_under_three_and_no_block_without_layer_or_deals() -> None:
    block = pt.precedent_block([_row("A"), _row("B")], layer=LAYER, today=TODAY)
    assert block is not None and block["medians"] == {}
    assert pt.precedent_block([_row("A")], layer=None, today=TODAY) is None
    assert pt.precedent_block([_row("P", status="proposed")], layer=LAYER, today=TODAY) is None


EV_BLOCK = {
    "enterprise_value": {"state": "computed", "currency": "USD", "value": 2e9},
    "net_debt": {"state": "computed", "value": 2e8, "period": "FY2025",
                 "sources": ["https://example.com/10k"]},
    "market_cap": {"value": 1.8e9, "currency": "USD", "as_of": "2026-11-10"},
    "multiples": [{"key": "ev_to_ebit", "state": "computed", "value": 13.3, "ebit": 1.5e8,
                   "period": "FY2025", "source_url": "https://example.com/10k"}],
}


def test_the_median_becomes_a_precedent_engine_method() -> None:
    from app.services.valuation.engine_inputs import build_inputs

    block = pt.precedent_block([_row("A", mult=10), _row("B", mult=12), _row("C", mult=14)],
                               layer=LAYER, today=TODAY)
    pm = pt.precedent_peer_multiple(block, EV_BLOCK)
    assert pm is not None and pm.key == "precedent_ev_to_ebit"
    assert pm.subject_metric.value == 1.5e8 and pm.peer_median.value == 12.0
    kwargs = dict(ev_block=EV_BLOCK, peers_block=None, depth_block=None, cash_flow=None,
                  revenue_history=[], risk_free=None, erp=None, market_cap_usd=1.8e9)
    with_deals = build_inputs(**kwargs, precedents_block=block)
    assert with_deals is not None and [m.key for m in with_deals.peer_multiples] == [
        "precedent_ev_to_ebit"]
    without = build_inputs(**kwargs)
    assert without is not None and without.peer_multiples == ()
    two = pt.precedent_block([_row("A"), _row("B")], layer=LAYER, today=TODAY)
    assert pt.precedent_peer_multiple(two, EV_BLOCK) is None


# ---------------------------------------------------------------- the scan


class _FakeSession:
    def __init__(self, rows: list[tuple]) -> None:  # noqa: D107
        self.rows, self.added = rows, []

    async def execute(self, _q):  # noqa: ANN001, ANN202
        return SimpleNamespace(all=lambda: self.rows)

    async def scalar(self, _q):  # noqa: ANN001, ANN202
        return None

    def add(self, obj) -> None:  # noqa: ANN001
        self.added.append(obj)

    async def flush(self) -> None:
        return None


async def test_scan_proposes_dated_deals_and_never_approves() -> None:
    text = "Hubbell has agreed to acquire DMC Power for an enterprise value of USD 825 million."
    session = _FakeSession([
        (text, "https://example.com/a", "Release", date(2025, 3, 1), NOW),
        (text, "https://example.com/a", "Release", date(2025, 3, 1), NOW),  # duplicate
        (text, "https://example.com/b", "Undated", None, NOW),
    ])
    counts = await pt.scan_company_documents(session, uuid.uuid4(), now=NOW)
    assert counts == {"chunks_read": 3, "deals_found": 3, "proposed": 1, "skipped_undated": 1}
    [row] = session.added
    assert row.status == "proposed" and row.entry_method == "extracted"
    assert row.source_url == "https://example.com/a" and row.announced_date == date(2025, 3, 1)


# ---------------------------------------------------------------- the admin API


@pytest.fixture
async def maker():  # noqa: ANN201
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(maker):  # noqa: ANN001, ANN201
    async def _db():  # noqa: ANN202
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


DEAL = {"acquirer_name": "Buyer plc", "target_name": "Grid Co", "announced_date": "2025-06-02",
        "currency": "eur", "enterprise_value": "1200000000",
        "source_url": "https://example.com/release", "source_title": "Release"}


async def test_curate_approve_and_reject(client) -> None:  # noqa: ANN001
    assert (await client.get(BASE)).status_code == 403
    r = await client.post(BASE, json=DEAL, headers=ME)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["status"] == "proposed" and row["currency"] == "EUR"
    assert (await client.post(BASE, json=DEAL, headers=ME)).status_code == 409

    approve = f"{BASE}/{row['id']}/approve"
    missing = await client.post(approve, json={"layer": LAYER}, headers=ME)
    assert missing.status_code == 422 and "metric_key" in missing.text
    bad_layer = await client.post(approve, json={"layer": "nope"}, headers=ME)
    assert bad_layer.status_code == 422
    ok = await client.post(approve, json={"layer": LAYER, "metric_key": "ebit",
                                          "metric_value": "100000000",
                                          "metric_period": "FY2024"}, headers=ME)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "approved" and body["reviewed_by"] == "owner@example.com"
    assert Decimal(body["multiple"]) == Decimal("12")

    other = (await client.post(BASE, json={**DEAL, "target_name": "Other Co"},
                               headers=ME)).json()
    reject = f"{BASE}/{other['id']}/reject"
    assert (await client.post(reject, json={}, headers=ME)).status_code == 422
    gone = await client.post(reject, json={"note": "minority stake"}, headers=ME)
    assert gone.status_code == 200 and gone.json()["status"] == "rejected"
    kept = await client.get(f"{BASE}?status_filter=rejected", headers=ME)
    assert [x["target_name"] for x in kept.json()] == ["Other Co"]


async def test_create_validates_url_date_and_layer(client) -> None:  # noqa: ANN001
    for patch in ({"source_url": "http://example.com/x"}, {"announced_date": "2099-01-01"},
                  {"layer": "not_a_layer"}, {"enterprise_value": "0"}):
        assert (await client.post(BASE, json={**DEAL, **patch}, headers=ME)).status_code == 422


async def test_in_staging_the_header_alone_is_not_an_admin(client, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(api.settings, "app_env", "staging")
    assert (await client.get(BASE, headers=ME)).status_code == 403


async def test_pipeline_section_reads_approved_rows(maker) -> None:  # noqa: ANN001
    from app.services.pipeline.v3_pipeline import _precedent_transactions_section

    cfg = SimpleNamespace(v3_precedent_transactions_enabled=True)
    layers = {"status": "classified", "primary_layer": LAYER}
    async with maker() as session:
        for target, status in (("A", "approved"), ("B", "proposed")):
            session.add(PrecedentTransaction(
                id=uuid.uuid4(), acquirer_name="Buyer", target_name=target, layer=LAYER,
                announced_date=date(2025, 1, 1), currency="USD",
                enterprise_value=Decimal("1e9"), value_basis="enterprise_value",
                metric_key="ebit", metric_value=Decimal("1e8"),
                source_url="https://example.com/x", retrieved_at=NOW,
                entry_method="curated", status=status, created_at=NOW))
        await session.flush()
        block = await _precedent_transactions_section(session, cfg=cfg, layers_block=layers)
        assert block is not None and [t["target"] for t in block["transactions"]] == ["A"]
        assert await _precedent_transactions_section(
            session, cfg=SimpleNamespace(v3_precedent_transactions_enabled=False),
            layers_block=layers) is None
        assert await _precedent_transactions_section(
            session, cfg=cfg, layers_block={"status": "unclassified"}) is None


# ---------------------------------------------------------------- wiring and docs


def test_flag_default_migration_and_proxy() -> None:
    assert Settings().v3_precedent_transactions_enabled is True
    text = (ROOT / "alembic" / "versions" / "056_add_precedent_transactions.py").read_text()
    assert 'revision: str = "056"' in text and 'down_revision: str | None = "055"' in text
    assert 'drop_table("precedent_transactions")' in text
    assert "op.alter_column" not in text and "op.drop_column" not in text
    proxy = (WEB / "app" / "api" / "admin" / "proxy" / "[...path]" / "route.ts").read_text()
    assert '"/api/v1/admin/precedent-transactions"' in proxy


def test_the_web_reads_the_producer_and_schema_keys() -> None:
    from app.schemas.precedent import PrecedentApprove, PrecedentRead

    reader = (WEB / "components" / "research" / "precedentTransactions.ts").read_text()
    for key in ("layer", "max_age_years", "median_rule", "note", "medians", "median",
                "count", "transactions", "acquirer", "target", "announced_date",
                "currency", "enterprise_value", "value_basis", "metric_key", "metric_value",
                "metric_period", "multiple_key", "multiple", "source_url", "source_title",
                "retrieved_at", "entry_method"):
        assert key in reader, key
    assert "readPrecedentTransactionsBlock(r.precedent_transactions)" in (
        WEB / "components" / "research" / "v3Research.ts").read_text()
    client = (WEB / "lib" / "precedentsApi.ts").read_text()
    for model in (PrecedentRead, PrecedentApprove):
        for key in model.model_fields:
            assert key in client, (model.__name__, key)
