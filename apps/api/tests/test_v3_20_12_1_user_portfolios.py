"""V3.20 slice 12.1 — private, hand-entered portfolios (ADR-100).

The rules under test: every row belongs to one owner (another owner gets a 404, never a
403); the owner is stored as a hash, never the address; in staging the forwarded header
is honoured only after Basic Auth put it on ``request.state``; entered data is validated
and never becomes an order; no public table references the private ones.
"""

from __future__ import annotations

import pathlib
import uuid
from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.api.v1 import portfolios as api
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.company import Company
from app.models.user_portfolio import UserPortfolio, UserPortfolioPosition

ME = {"X-IB-Admin-Email": "Owner@Example.com"}
OTHER = {"X-IB-Admin-Email": "someone.else@example.com"}
BASE = "/api/v1/portfolios"


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


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


async def _portfolio(client: AsyncClient, name: str = "Grid names") -> str:
    r = await client.post(BASE, json={"name": name}, headers=ME)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_without_an_owner_nothing_is_readable_or_writable(client) -> None:  # noqa: ANN001
    assert (await client.get(BASE)).status_code == 403
    assert (await client.post(BASE, json={"name": "x"})).status_code == 403


async def test_the_owner_is_stored_as_a_hash_and_scopes_every_row(client, maker) -> None:  # noqa: ANN001
    pid = await _portfolio(client)
    async with maker() as s:
        row = (await s.execute(select(UserPortfolio))).scalar_one()
    assert row.owner_key == api.owner_key_for("owner@example.com")
    assert "@" not in row.owner_key and len(row.owner_key) == 64

    assert (await client.get(BASE, headers=OTHER)).json() == []
    for method, path, body in (("GET", f"{BASE}/{pid}", None),
                               ("POST", f"{BASE}/{pid}/positions",
                                {"ticker": "POWL", "entry_date": "2026-01-05"}),
                               ("DELETE", f"{BASE}/{pid}", None)):
        r = await client.request(method, path, json=body, headers=OTHER)
        assert r.status_code == 404, (method, r.status_code)
    listed = (await client.get(BASE, headers=ME)).json()
    assert [p["name"] for p in listed] == ["Grid names"]


async def test_positions_are_entered_validated_and_linked(client, maker) -> None:  # noqa: ANN001
    async with maker() as s:
        s.add(Company(id=uuid.uuid4(), ticker="POWL", exchange="NASDAQ", name="Powell",
                      status="new"))
        await s.commit()
    pid = await _portfolio(client)
    good = {"ticker": "powl", "exchange": "nasdaq", "entry_date": "2026-01-05",
            "entry_price": "180.5", "entry_currency": "usd", "weight_pct": "12.5"}
    r = await client.post(f"{BASE}/{pid}/positions", json=good, headers=ME)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ticker"] == "POWL" and body["entry_currency"] == "USD"
    assert body["company_id"] is not None
    assert (await client.post(f"{BASE}/{pid}/positions", json=good,
                              headers=ME)).status_code == 409
    unknown = await client.post(f"{BASE}/{pid}/positions",
                                json={"ticker": "ZZZZ", "entry_date": "2026-02-01"}, headers=ME)
    assert unknown.status_code == 201 and unknown.json()["company_id"] is None

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    for bad in ({**good, "entry_currency": None},  # a price needs its currency
                {**good, "entry_price": None},  # and a currency its price
                {**good, "entry_date": tomorrow},
                {**good, "weight_pct": "120"},
                {**good, "entry_price": "-1"},
                {**good, "ticker": "DROP TABLE"}):
        r = await client.post(f"{BASE}/{pid}/positions", json=bad, headers=ME)
        assert r.status_code == 422, bad

    detail = (await client.get(f"{BASE}/{pid}", headers=ME)).json()
    assert detail["position_count"] == 2 and detail["weight_pct_total"] == "12.500"
    position_id = detail["positions"][0]["id"]
    assert (await client.delete(f"{BASE}/{pid}/positions/{position_id}",
                                headers=ME)).status_code == 204
    assert (await client.delete(f"{BASE}/{pid}", headers=ME)).status_code == 204
    async with maker() as s:
        assert (await s.execute(select(UserPortfolioPosition))).scalars().all() == []


async def test_in_staging_the_header_alone_is_not_an_owner(client, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(api.settings, "app_env", "staging")
    assert (await client.get(BASE, headers=ME)).status_code == 403


def test_no_public_table_references_the_private_tables() -> None:
    private = {"user_portfolios", "user_portfolio_positions"}
    for table in Base.metadata.tables.values():
        if table.name in private:
            continue
        for fk in table.foreign_keys:
            assert fk.column.table.name not in private, table.name


def test_no_route_places_an_order_or_sizes_a_position() -> None:
    src = (pathlib.Path(api.__file__)).read_text().lower()
    for word in ("broker", "rebalance", "target_weight", "order_type", "quantity_to"):
        assert word not in src.replace("no route imports from a broker", ""), word


def test_migration_055_follows_054_and_is_additive() -> None:
    text = (pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / "055_add_user_portfolios.py").read_text()
    assert 'revision: str = "055"' in text and 'down_revision: str | None = "054"' in text
    assert 'drop_table("user_portfolio_positions")' in text
    assert "op.alter_column" not in text and "op.drop_column" not in text


def test_the_web_client_reads_the_schema_keys() -> None:
    from app.schemas.portfolio import PortfolioDetail, PositionCreate, PositionRead

    client = (pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "lib"
              / "portfolioApi.ts").read_text()
    for model in (PortfolioDetail, PositionRead, PositionCreate):
        for key in model.model_fields:
            assert f"{key}" in client, (model.__name__, key)
    assert "/api/admin/proxy/api/v1/portfolios" in client
